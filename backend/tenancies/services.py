"""
Tenancy service functions (ADR-004).

The witnessed path. Accepting an application creates a confirmed tenancy
directly — no claim, no confirmation window, no dispute surface, no queue entry.
This is ADR-004's primary control on dispute volume, and the ADR says explicitly
that it must not be "simplified" into one uniform path for tidiness.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import User

from .constants import (
    DISPUTE_TRANSITIONS,
    ApplicationStatus,
    ClaimStatus,
    ConfirmationSource,
    DisputeReason,
    DisputeTransition,
    EscalationReason,
    TenancyStatus,
)
from .models import Application, Tenancy, TenancyClaim, TerminationRequest


class ApplicationNotDecidableError(ValidationError):
    """This application cannot be accepted or rejected in its current state."""


def _assert_decidable(application: Application) -> None:
    if not application.is_open():
        raise ApplicationNotDecidableError(
            {
                "status": _("This application is already %(status)s.")
                % {"status": application.get_status_display().lower()}
            }
        )


@transaction.atomic
def accept_application(
    application: Application,
    *,
    decided_by: User,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    monthly_rent_kes=None,
    note: str = "",
) -> Tenancy:
    """Accept an application and create the confirmed tenancy it implies.

    **No claim is created, and none should be.** The platform holds the
    application, the acceptance, the actor and the timestamp; asking the
    landlord to confirm a second time what they have just accepted adds latency
    and a dispute surface for nothing (ADR-004 §1.1).

    Atomic, because an accepted application with no tenancy is a stay the
    platform witnessed and cannot vouch for — which is exactly the gap the
    tenancy record exists to close.
    """
    _assert_decidable(application)

    now = timezone.now()
    application.status = ApplicationStatus.ACCEPTED
    application.decided_by = decided_by
    application.decided_at = now
    application.decision_note = note
    application.save(
        update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"]
    )

    return Tenancy.all_objects.create(
        unit=application.unit,
        tenant=application.applicant,
        application=application,
        confirmation_source=ConfirmationSource.APPLICATION,
        confirmed_by=decided_by,
        confirmed_at=now,
        start_date=start_date or application.move_in_date,
        end_date=end_date,
        monthly_rent_kes=(
            monthly_rent_kes if monthly_rent_kes is not None else application.unit.rent_kes
        ),
        status=TenancyStatus.CONFIRMED,
    )


@transaction.atomic
def reject_application(
    application: Application, *, decided_by: User, note: str = ""
) -> Application:
    """Reject an application. Creates nothing."""
    _assert_decidable(application)

    application.status = ApplicationStatus.REJECTED
    application.decided_by = decided_by
    application.decided_at = timezone.now()
    application.decision_note = note
    application.save(
        update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"]
    )
    return application


@transaction.atomic
def withdraw_application(application: Application) -> Application:
    """Withdraw an application, by its applicant.

    No ``decided_by``: withdrawing is the applicant's own act, not a decision
    made about them, and the constraint only demands an author for a
    ``decided_at``.
    """
    _assert_decidable(application)

    application.status = ApplicationStatus.WITHDRAWN
    application.save(update_fields=["status", "updated_at"])
    return application


# ---------------------------------------------------------------------------
# The claimed path (ADR-004)
# ---------------------------------------------------------------------------


class VerificationRequiredError(ValidationError):
    """This university gates claiming on student verification."""


def _assert_verification_permits(user: User) -> None:
    """ADR-003 gating, answered in one place (`accounts.gating.can_perform`)."""
    from accounts.gating import GatedAction, GateReason, can_perform

    decision = can_perform(user, GatedAction.CLAIM_TENANCY)
    if decision.allowed:
        return

    if decision.reason is GateReason.REJECTED:
        message = _("Your student verification was not accepted.")
    else:
        message = _("Your university asks students to verify before claiming a stay.")

    raise VerificationRequiredError({"verification": message})


class ClaimRateLimitExceededError(ValidationError):
    """This user has raised too many claims recently."""


class OverlappingTenancyError(ValidationError):
    """This user already has a confirmed stay covering these dates."""


def _assert_claim_rate_limit(claimant: User, *, now: dt.datetime | None = None) -> None:
    """Cap claims per user per rolling 30 days.

    The tenant initiates claims (ADR-004), so the abuse surface moved to them.
    Refused with an explanation rather than silently dropped.
    """
    since = (now or timezone.now()) - dt.timedelta(days=30)
    recent = TenancyClaim.all_objects.filter(claimant=claimant, created_at__gte=since).count()

    if recent >= settings.MAX_CLAIMS_PER_USER_PER_MONTH:
        raise ClaimRateLimitExceededError(
            {
                "claimant": _(
                    "You have raised %(count)d claims in the last 30 days, which is "
                    "the limit. Contact support if you have more stays to record."
                )
                % {"count": recent}
            }
        )


@transaction.atomic
def create_claim(
    *,
    unit,
    claimant: User,
    start_date: dt.date,
    end_date: dt.date | None,
    monthly_rent_kes,
    is_retrospective: bool = False,
    now: dt.datetime | None = None,
) -> TenancyClaim:
    """Raise a claim for a stay the platform did not witness.

    Off-platform arrangements and pre-platform history only. An accepted
    application creates a confirmed tenancy directly and must never come
    through here (ADR-004 §1.1).
    """
    now = now or timezone.now()
    _assert_verification_permits(claimant)
    _assert_claim_rate_limit(claimant, now=now)

    return TenancyClaim.all_objects.create(
        unit=unit,
        claimant=claimant,
        start_date=start_date,
        end_date=end_date,
        monthly_rent_kes=monthly_rent_kes,
        is_retrospective=is_retrospective,
        confirmation_deadline=now + dt.timedelta(days=settings.TENANCY_CONFIRMATION_WINDOW_DAYS),
    )


@transaction.atomic
def confirm_claim(
    claim: TenancyClaim,
    *,
    source: str,
    confirmed_by: User | None = None,
    now: dt.datetime | None = None,
) -> Tenancy:
    """Turn a confirmed claim into a tenancy.

    The single place a claim becomes evidence. ``source`` says who or what
    confirmed it, and the model constraint enforces that an unattributed source
    carries no actor.
    """
    now = now or timezone.now()

    claim.status = ClaimStatus.CONFIRMED
    claim.resolved_at = now
    # Null for AUTO and DISPUTE_TIMEOUT: silence has no author.
    claim.resolved_by = confirmed_by
    claim.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])

    return Tenancy.all_objects.create(
        unit=claim.unit,
        tenant=claim.claimant,
        claim=claim,
        confirmation_source=source,
        confirmed_by=confirmed_by,
        confirmed_at=now,
        was_disputed=claim.was_disputed_at_any_point(),
        start_date=claim.start_date,
        end_date=claim.end_date,
        monthly_rent_kes=claim.monthly_rent_kes,
        status=TenancyStatus.CONFIRMED,
    )


# ---------------------------------------------------------------------------
# The dispute state machine (ADR-004 §2)
# ---------------------------------------------------------------------------


class UnroutableDisputeError(ValidationError):
    """This dispute reason has no path out of the queue."""


class DisputeNotOpenError(ValidationError):
    """This claim is not in a state where that dispute action applies."""


def _transition_for(reason: str) -> DisputeTransition:
    """Look up a dispute reason's permitted paths, refusing unknown reasons.

    A reason absent from ``DISPUTE_TRANSITIONS`` could be raised and never
    routed — it would sit in ``disputed`` for ever, which is the indefinite
    block the timeout exists to remove. Raising here makes that state
    unconstructable rather than merely untested.
    """
    try:
        return DISPUTE_TRANSITIONS[reason]
    except KeyError:
        raise UnroutableDisputeError(
            {
                "dispute_reason": _(
                    "%(reason)s has no entry in DISPUTE_TRANSITIONS, so a dispute "
                    "raised with it could never be routed out of the queue."
                )
                % {"reason": reason}
            }
        ) from None


@transaction.atomic
def escalate(claim: TenancyClaim, *, reason: str, now: dt.datetime | None = None) -> TenancyClaim:
    """Put a claim in front of an administrator.

    ``dispute_reason`` is left exactly as raised. ``escalation_reason`` says
    what the admin has to decide, which is a different question (ADR-004 §2a),
    and the database constraint — generated from ``DISPUTE_TRANSITIONS`` —
    refuses a pairing the table does not permit.
    """
    now = now or timezone.now()
    permitted = _transition_for(claim.dispute_reason).escalates_to

    if reason not in permitted:
        raise UnroutableDisputeError(
            {
                "escalation_reason": _("A %(dispute)s dispute cannot escalate as %(escalation)s.")
                % {"dispute": claim.dispute_reason, "escalation": reason}
            }
        )

    claim.status = ClaimStatus.ESCALATED
    claim.escalation_reason = reason
    claim.escalated_at = now
    claim.escalation_deadline = now + dt.timedelta(days=settings.DISPUTE_RESOLUTION_WINDOW_DAYS)
    claim.save(
        update_fields=[
            "status",
            "escalation_reason",
            "escalated_at",
            "escalation_deadline",
            "updated_at",
        ]
    )
    return claim


def _find_covering_tenancy(claim: TenancyClaim) -> Tenancy | None:
    """The predicate behind a ``duplicate`` dispute.

    Deliberately the same predicate the exclusion constraint enforces, and now
    expressed in exactly one place -- ``TenancyQuerySet.overlapping()`` -- so
    the two cannot drift.

    Status-independent, exactly like the constraint. When this filtered on
    ``status='active'`` and the constraint did too, a duplicate claim against an
    ended stay was reported as "not in fact a duplicate" and escalated to an
    administrator -- who would have found a plainly duplicate stay the system
    had just told them was not one.
    """
    return (
        Tenancy.all_objects.overlapping(claim.start_date, claim.end_date)
        .filter(unit=claim.unit, tenant=claim.claimant)
        .exclude(claim=claim)
        .first()
    )


@transaction.atomic
def effective_stay_days(tenancy: Tenancy, *, today: dt.date | None = None) -> int:
    """How long the stay has lasted so far.

    **Never counts past today.** A twelve-month lease signed on Monday has
    lasted three days by Thursday, not 365 -- and the earlier implementation
    said 365, because it used `end_date` whenever one was set and only fell
    back to today for open-ended stays.

    The consequence was not small. Review eligibility reads this, so every
    tenancy with an agreed end date became reviewable the day it started: the
    minimum-stay rule ADR-004 exists to enforce -- *a week in a room tells you
    about the viewing; the water going off every third Thursday takes a month
    to notice* -- was defeated for the majority of stays, which are exactly the
    ones with an agreed end. And because eligibility latches, the wrongly
    granted right was then permanent.

    It also silently disabled `termination_would_defeat_review`, whose first
    condition is "the stay has not yet earned eligibility". Nothing could ever
    be defeated, because everything was already earned.

    No test saw it. Fixtures are past stays, where `end_date` is behind today
    and the two readings agree, or open-ended ones, which took the fallback.
    The disagreement only appears for a *current* stay with an agreed end --
    the commonest tenancy in the product and the one no fixture had.

    This is the *live* figure and it goes down if `end_date` moves back, which
    is why eligibility is latched rather than read from here.
    """
    today = today or dt.date.today()
    end = tenancy.end_date or today
    # Whichever comes first. A stay cannot have lasted longer than it has been
    # running, whatever the lease says it will eventually be.
    return (min(end, today) - tenancy.start_date).days


def review_eligibility_date(tenancy: Tenancy, *, today: dt.date | None = None) -> dt.date | None:
    """The day this stay became long enough to review, **latched**.

    Returns ``None`` while the threshold has not been reached.

    Nothing writes a row on the day a threshold passes, so this stamps the
    first time anything observes it met -- with the date it was actually
    crossed, not with today. `start_date + REVIEW_MINIMUM_STAY_DAYS` is the
    honest fact; `now` would be an artefact of when somebody happened to look.

    **Once set it is never cleared and never moved.** That is the whole point:

    > A landlord who can move `end_date` can otherwise push a stay back under
    > the minimum and delete a review right that was already earned. It is
    > `correction_defeats_review` at a different door, and the same answer
    > applies -- eligibility, once earned, is not the counterparty's to revoke.
    """
    if tenancy.review_eligible_at is not None:
        return tenancy.review_eligible_at

    if effective_stay_days(tenancy, today=today) < settings.REVIEW_MINIMUM_STAY_DAYS:
        return None

    crossed_on = tenancy.start_date + dt.timedelta(days=settings.REVIEW_MINIMUM_STAY_DAYS)
    Tenancy.all_objects.filter(pk=tenancy.pk).update(review_eligible_at=crossed_on)
    tenancy.review_eligible_at = crossed_on
    return crossed_on


def termination_would_defeat_review(
    tenancy: Tenancy, proposed_end: dt.date, *, today: dt.date | None = None
) -> bool:
    """Whether this termination would NEWLY remove a review right.

    Two conditions, and both are required:

    - the stay has **not yet** earned eligibility (if it has, the latch
      protects it and nothing here can take it away); and
    - the proposed date would leave it under the minimum.

    A termination that shortens an already-eligible stay is not caught, and
    must not be: the right is already earned, so there is nothing to defend.
    """
    if review_eligibility_date(tenancy, today=today) is not None:
        return False

    return (proposed_end - tenancy.start_date).days < settings.REVIEW_MINIMUM_STAY_DAYS


class TerminationNotOpenError(ValidationError):
    """This termination is not in a state where that action applies."""


def _assert_terminable(tenancy: Tenancy, ended_on: dt.date, *, today: dt.date) -> None:
    if ended_on < tenancy.start_date:
        raise ValidationError({"ended_on": _("A stay cannot end before it started.")})

    if ended_on > today:
        # A date that has not happened yet is not a termination; it is an
        # agreement to end later, which is a lease amendment. This platform
        # records what happened, and accepting a future date here would mean
        # storing a fact that is not yet true and might never be.
        raise ValidationError(
            {
                "ended_on": _(
                    "An early termination records a move-out that has already "
                    "happened. A future date is a lease amendment, which this "
                    "platform does not record."
                )
            }
        )


@transaction.atomic
def request_early_termination(
    tenancy: Tenancy,
    *,
    initiated_by: User,
    ended_on: dt.date,
    reason: str,
    now: dt.datetime | None = None,
    today: dt.date | None = None,
) -> TerminationRequest:
    """Propose that a stay ended early. Either party may.

    The counterparty has ``settings.TENANCY_CONFIRMATION_WINDOW_DAYS`` to
    confirm or dispute, and silence auto-confirms -- the same shape as a claim,
    for the same reason: an indefinite wait would let either side veto a fact
    by ignoring it.

    **Unless it would newly defeat a review right**, in which case it escalates
    immediately and no amount of silence confirms it.
    """
    now = now or timezone.now()
    today = today or dt.date.today()

    if not reason:
        raise ValidationError(
            {"reason": _("An early termination must say why. The date alone is not a record.")}
        )
    _assert_terminable(tenancy, ended_on, today=today)

    request = TerminationRequest.all_objects.create(
        tenancy=tenancy,
        initiated_by=initiated_by,
        proposed_end_date=ended_on,
        reason=reason,
        confirmation_deadline=now + dt.timedelta(days=settings.TENANCY_CONFIRMATION_WINDOW_DAYS),
    )

    if termination_would_defeat_review(tenancy, ended_on, today=today):
        # Straight to an administrator. Not disputed -- nobody has disagreed
        # yet -- but it must never reach the auto-confirm sweep, because the
        # counterparty's silence would then delete their own review right.
        return escalate_termination(
            request,
            reason=EscalationReason.TERMINATION_DEFEATS_REVIEW,
            now=now,
        )

    return request


@transaction.atomic
def escalate_termination(
    request: TerminationRequest, *, reason: str, now: dt.datetime | None = None
) -> TerminationRequest:
    """Put a termination in front of an administrator.

    The permitted pairings come from ``DISPUTE_TRANSITIONS`` -- the same table
    the claim state machine reads -- so a termination cannot escalate as
    something a dates dispute could not.
    """
    now = now or timezone.now()
    permitted = DISPUTE_TRANSITIONS[DisputeReason.TERMINATION_DATE].escalates_to

    if reason not in permitted:
        raise UnroutableDisputeError(
            {
                "escalation_reason": _("A termination cannot escalate as %(escalation)s.")
                % {"escalation": reason}
            }
        )

    request.status = ClaimStatus.ESCALATED
    request.escalation_reason = reason
    request.escalated_at = now
    request.escalation_deadline = now + dt.timedelta(days=settings.DISPUTE_RESOLUTION_WINDOW_DAYS)
    request.save(
        update_fields=[
            "status",
            "escalation_reason",
            "escalated_at",
            "escalation_deadline",
            "updated_at",
        ]
    )
    return request


@transaction.atomic
def confirm_termination(request: TerminationRequest, *, now: dt.datetime | None = None) -> Tenancy:
    """Apply the termination to the tenancy.

    ``end_date`` moves to the actual date and stays authoritative for currency,
    so a stay that ended in March reads as past from March with no flag
    consulted and no job run.

    ``terminated_early`` and the reason are kept because "ended in March" and
    "ended early in March" are different facts about the same date.
    """
    now = now or timezone.now()

    if not request.is_open():
        raise TerminationNotOpenError(
            {"status": _("This termination is already %(status)s.") % {"status": request.status}}
        )

    tenancy = request.tenancy
    # Latch before moving the date. Once end_date shrinks the live computation
    # can no longer see that the threshold was met, so this is the last moment
    # the fact is observable.
    review_eligibility_date(tenancy)

    tenancy.end_date = request.proposed_end_date
    tenancy.terminated_early = True
    tenancy.termination_reason = request.reason
    tenancy.save(
        update_fields=[
            "end_date",
            "terminated_early",
            "termination_reason",
            "review_eligible_at",
            "updated_at",
        ]
    )

    request.status = ClaimStatus.CONFIRMED
    request.resolved_at = now
    request.save(update_fields=["status", "resolved_at", "updated_at"])

    return tenancy


@transaction.atomic
def dispute_termination(
    request: TerminationRequest,
    *,
    disputed_by: User,
    counter_end_date: dt.date | None = None,
    now: dt.datetime | None = None,
) -> TerminationRequest:
    """The counterparty disagrees about when the stay ended."""
    now = now or timezone.now()

    if request.status != ClaimStatus.PENDING:
        raise TerminationNotOpenError({"status": _("Only a pending termination can be disputed.")})

    request.status = ClaimStatus.DISPUTED
    request.dispute_reason = DisputeReason.TERMINATION_DATE
    request.disputed_by = disputed_by
    request.disputed_at = now
    request.counter_end_date = counter_end_date
    request.save(
        update_fields=[
            "status",
            "dispute_reason",
            "disputed_by",
            "disputed_at",
            "counter_end_date",
            "updated_at",
        ]
    )

    if counter_end_date is None:
        # No counter-proposal means there is nothing for the parties to settle
        # between them.
        return escalate_termination(request, reason=EscalationReason.COUNTER_UNRESOLVED, now=now)

    return request


@transaction.atomic
def accept_termination_counter(
    request: TerminationRequest,
    *,
    now: dt.datetime | None = None,
    today: dt.date | None = None,
) -> TerminationRequest | Tenancy:
    """The initiator accepts the counterparty's date.

    The review-defeating guard applies again, against the **counter** date. A
    termination laundered through a counter is still a termination.
    """
    now = now or timezone.now()
    today = today or dt.date.today()

    if request.counter_end_date is None:
        raise TerminationNotOpenError(
            {"counter_end_date": _("This termination has not been countered.")}
        )

    if termination_would_defeat_review(request.tenancy, request.counter_end_date, today=today):
        return escalate_termination(
            request, reason=EscalationReason.TERMINATION_DEFEATS_REVIEW, now=now
        )

    request.proposed_end_date = request.counter_end_date
    request.save(update_fields=["proposed_end_date", "updated_at"])
    return confirm_termination(request, now=now)


@transaction.atomic
def resolve_termination_escalation(
    request: TerminationRequest,
    *,
    resolved_by: User,
    uphold: bool,
    now: dt.datetime | None = None,
) -> TerminationRequest | Tenancy:
    """An administrator decides an escalated termination."""
    now = now or timezone.now()

    if request.status != ClaimStatus.ESCALATED:
        raise TerminationNotOpenError({"status": _("This termination is not escalated.")})

    if uphold:
        request.status = ClaimStatus.PENDING
        request.save(update_fields=["status", "updated_at"])
        return confirm_termination(request, now=now)

    request.status = ClaimStatus.WITHDRAWN
    request.resolved_at = now
    request.save(update_fields=["status", "resolved_at", "updated_at"])
    return request


@transaction.atomic
def raise_dispute(
    claim: TenancyClaim,
    *,
    reason: str,
    disputed_by: User,
    note: str = "",
    proposed_start_date: dt.date | None = None,
    proposed_end_date: dt.date | None = None,
    now: dt.datetime | None = None,
) -> TenancyClaim:
    """Dispute a claim, and route it by its type.

    Most disputes never reach an administrator, which is the whole reason they
    are typed. Where each one goes is read from ``DISPUTE_TRANSITIONS`` — this
    function branches on the table's flags, never on the reason itself.
    """
    now = now or timezone.now()

    if claim.status != ClaimStatus.PENDING:
        raise DisputeNotOpenError({"status": _("Only a pending claim can be disputed.")})

    transition = _transition_for(reason)

    if reason == DisputeReason.DATES_INCORRECT and proposed_start_date is None:
        raise ValidationError(
            {"proposed_start_date": _("A dates dispute must state the dates it proposes instead.")}
        )

    claim.status = ClaimStatus.DISPUTED
    claim.dispute_reason = reason
    claim.dispute_note = note
    claim.disputed_by = disputed_by
    claim.disputed_at = now
    claim.proposed_start_date = proposed_start_date
    claim.proposed_end_date = proposed_end_date
    claim.save(
        update_fields=[
            "status",
            "dispute_reason",
            "dispute_note",
            "disputed_by",
            "disputed_at",
            "proposed_start_date",
            "proposed_end_date",
            "updated_at",
        ]
    )

    if transition.auto_resolves:
        return _auto_resolve_duplicate(claim, now=now)

    if not transition.can_resolve_between_parties:
        return escalate(claim, reason=transition.escalates_to[0], now=now)

    return claim


@transaction.atomic
def _auto_resolve_duplicate(claim: TenancyClaim, *, now: dt.datetime) -> TenancyClaim:
    """Settle a ``duplicate`` dispute from data, or escalate if it does not hold.

    If a covering tenancy exists the claim really is a duplicate and closes
    with no admin involved. If none exists the claim is not in fact a
    duplicate, and somebody has to say so.
    """
    if _find_covering_tenancy(claim) is None:
        return escalate(claim, reason=EscalationReason.DUPLICATE_UNMATCHED, now=now)

    # No resolved_by: nobody decided this, a query did.
    claim.status = ClaimStatus.WITHDRAWN
    claim.resolved_at = now
    claim.save(update_fields=["status", "resolved_at", "updated_at"])
    return claim


def _would_defeat_the_review(start_date: dt.date, end_date: dt.date | None) -> bool:
    """Whether a correction puts the stay under the review minimum.

    ADR-004 §2b: the cheapest attack on this whole mechanism is a dispute with
    a plausible-looking correction that drops the stay below the threshold. An
    ongoing stay has no end date and cannot be shortened this way.
    """
    if end_date is None:
        return False
    return (end_date - start_date).days < settings.REVIEW_MINIMUM_STAY_DAYS


@transaction.atomic
def accept_correction(
    claim: TenancyClaim, *, now: dt.datetime | None = None
) -> TenancyClaim | Tenancy:
    """The tenant accepts the disputer's corrected dates.

    Normally this settles the dispute with no administrator involved: the claim
    confirms with the corrected dates.

    **Unless the correction would make the stay too short to review.** Then it
    escalates as ``correction_defeats_review`` even though the tenant agreed,
    because a tenant who misremembers by a week — or who simply wants the
    argument over — may not realise that what they just accepted also deletes
    their review. Their acceptance is recorded as evidence for the admin, who
    will usually find the correction honest. The landlord is not presumed to be
    lying; the point is that this correction has a side effect the parties
    cannot settle privately.
    """
    now = now or timezone.now()

    if claim.status != ClaimStatus.DISPUTED or claim.dispute_reason != (
        DisputeReason.DATES_INCORRECT
    ):
        raise DisputeNotOpenError({"status": _("There is no open date correction on this claim.")})

    if claim.proposed_start_date is None:  # pragma: no cover - the constraint forbids it
        raise DisputeNotOpenError(
            {"proposed_start_date": _("This dispute proposes no corrected dates.")}
        )

    claim.tenant_accepted_correction_at = now

    if _would_defeat_the_review(claim.proposed_start_date, claim.proposed_end_date):
        claim.save(update_fields=["tenant_accepted_correction_at", "updated_at"])
        return escalate(claim, reason=EscalationReason.CORRECTION_DEFEATS_REVIEW, now=now)

    claim.start_date = claim.proposed_start_date
    claim.end_date = claim.proposed_end_date
    claim.save(
        update_fields=[
            "start_date",
            "end_date",
            "tenant_accepted_correction_at",
            "updated_at",
        ]
    )
    return confirm_claim(
        claim, source=ConfirmationSource.LANDLORD, confirmed_by=claim.disputed_by, now=now
    )


@transaction.atomic
def counter_correction(
    claim: TenancyClaim,
    *,
    start_date: dt.date,
    end_date: dt.date | None,
    now: dt.datetime | None = None,
) -> TenancyClaim:
    """The tenant counters the correction, once.

    Once, because an unbounded exchange between two people who disagree is an
    indefinite block by another name.
    """
    if claim.status != ClaimStatus.DISPUTED or claim.dispute_reason != (
        DisputeReason.DATES_INCORRECT
    ):
        raise DisputeNotOpenError({"status": _("There is no open date correction on this claim.")})

    if claim.counter_start_date is not None:
        raise DisputeNotOpenError(
            {"counter_start_date": _("This claim has already been countered once.")}
        )

    claim.counter_start_date = start_date
    claim.counter_end_date = end_date
    claim.save(update_fields=["counter_start_date", "counter_end_date", "updated_at"])
    return claim


@transaction.atomic
def accept_counter(
    claim: TenancyClaim, *, now: dt.datetime | None = None
) -> TenancyClaim | Tenancy:
    """The disputer accepts the tenant's counter-dates.

    The same review-defeating guard applies. A correction laundered through a
    counter is still a correction.
    """
    now = now or timezone.now()

    if claim.counter_start_date is None:
        raise DisputeNotOpenError({"counter_start_date": _("This claim has not been countered.")})

    if _would_defeat_the_review(claim.counter_start_date, claim.counter_end_date):
        return escalate(claim, reason=EscalationReason.CORRECTION_DEFEATS_REVIEW, now=now)

    claim.start_date = claim.counter_start_date
    claim.end_date = claim.counter_end_date
    claim.save(update_fields=["start_date", "end_date", "updated_at"])
    return confirm_claim(
        claim, source=ConfirmationSource.LANDLORD, confirmed_by=claim.disputed_by, now=now
    )


@transaction.atomic
def reject_counter(claim: TenancyClaim, *, now: dt.datetime | None = None) -> TenancyClaim:
    """The disputer rejects the counter. Two parties, no agreement, admin."""
    if claim.counter_start_date is None:
        raise DisputeNotOpenError({"counter_start_date": _("This claim has not been countered.")})

    return escalate(claim, reason=EscalationReason.COUNTER_UNRESOLVED, now=now)


@transaction.atomic
def resolve_escalation(
    claim: TenancyClaim,
    *,
    resolved_by: User,
    uphold_claim: bool,
    now: dt.datetime | None = None,
) -> TenancyClaim | Tenancy:
    """An administrator decides an escalated claim."""
    now = now or timezone.now()

    if claim.status != ClaimStatus.ESCALATED:
        raise DisputeNotOpenError({"status": _("This claim is not escalated.")})

    if uphold_claim:
        return confirm_claim(
            claim, source=ConfirmationSource.ADMIN, confirmed_by=resolved_by, now=now
        )

    claim.status = ClaimStatus.WITHDRAWN
    claim.resolved_at = now
    claim.resolved_by = resolved_by
    claim.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])
    return claim

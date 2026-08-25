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
from django.db.models import Q
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
from .models import Application, Tenancy, TenancyClaim


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
        status=TenancyStatus.ACTIVE,
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
        status=TenancyStatus.ACTIVE,
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

    Deliberately the same predicate the exclusion constraint enforces: a
    confirmed stay for this unit and this claimant whose range overlaps the
    claimed one. A database query, not a judgement call.

    Status-independent, exactly like the constraint. When this filtered on
    ``status='active'`` and the constraint did too, a duplicate claim against
    an ended stay was reported as "not in fact a duplicate" and escalated to an
    administrator -- who would have found a plainly duplicate stay the system
    had just told them was not one.
    """
    overlapping = Q(start_date__lte=claim.end_date or dt.date.max) & (
        Q(end_date__isnull=True) | Q(end_date__gte=claim.start_date)
    )
    return (
        Tenancy.all_objects.filter(
            overlapping,
            unit=claim.unit,
            tenant=claim.claimant,
        )
        .exclude(claim=claim)
        .first()
    )


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

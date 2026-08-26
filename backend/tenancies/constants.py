"""Reference data for applications, claims and tenancies (ADR-004)."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import models
from django.utils.translation import gettext_lazy as _


class ApplicationStatus(models.TextChoices):
    SUBMITTED = "submitted", _("Submitted")
    UNDER_REVIEW = "under_review", _("Under review")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")
    WITHDRAWN = "withdrawn", _("Withdrawn")
    EXPIRED = "expired", _("Expired")


#: Statuses in which an application is still live, so only one may exist per
#: applicant per unit.
OPEN_APPLICATION_STATUSES = (ApplicationStatus.SUBMITTED, ApplicationStatus.UNDER_REVIEW)


class ConfirmationSource(models.TextChoices):
    """How a tenancy came to be confirmed (ADR-004).

    ``APPLICATION`` is the volume control: the platform witnessed the agreement,
    so no claim, no confirmation window and no dispute surface. Everything else
    arrived through a ``TenancyClaim``.
    """

    APPLICATION = "application", _("Accepted on-platform application")
    LANDLORD = "landlord", _("Confirmed by the landlord")
    CARETAKER = "caretaker", _("Confirmed by a caretaker")
    AUTO = "auto", _("Auto-confirmed: the confirmation window elapsed")
    ADMIN = "admin", _("Resolved by a platform administrator")
    DISPUTE_TIMEOUT = "dispute_timeout", _("Auto-resolved: the dispute window elapsed")


#: Sources with no human actor, so ``confirmed_by`` must be null.
UNATTRIBUTED_SOURCES = (ConfirmationSource.AUTO, ConfirmationSource.DISPUTE_TIMEOUT)


class TenancyStatus(models.TextChoices):
    """What a tenancy IS, never when it is (ADR-004).

    **No ``active`` and no ``ended``.** Both were here, and both were wrong in
    the same way: whether a stay is running is a function of ``start_date``,
    ``end_date`` and today, so a stored value needs a job to stay true and the
    data lies silently the moment the job stops. Currency is derived at query
    time -- ``Tenancy.objects.current()``, ``.past()``, ``.upcoming()``.

    The symptom that made it concrete: ``confirm_claim`` marked every stay
    ACTIVE regardless of its end date, so every seeded 2023 tenancy read as
    running, and nothing anywhere noticed.

    What remains is genuinely stateful -- it changes only because somebody
    changes it.
    """

    #: Every tenancy starts here. A Tenancy row exists only once a claim was
    #: confirmed or an application accepted, so there is no earlier state.
    CONFIRMED = "confirmed", _("Confirmed")

    #: Challenged after the fact. Distinct from a disputed CLAIM, which has no
    #: tenancy yet.
    DISPUTED = "disputed", _("Disputed after confirmation")

    #: Voided by the parties -- both agree the stay did not happen as recorded.
    WITHDRAWN = "withdrawn", _("Withdrawn by the parties")

    #: Voided by a platform administrator, e.g. found fraudulent.
    REJECTED = "rejected", _("Rejected by an administrator")


#: Statuses in which a tenancy is real evidence. Only these can be current,
#: past or upcoming -- a voided tenancy is not a stay that happened at some
#: other time, it is a stay that did not happen.
LIVE_TENANCY_STATUSES = (TenancyStatus.CONFIRMED, TenancyStatus.DISPUTED)

#: Statuses that void a tenancy.
VOID_TENANCY_STATUSES = (TenancyStatus.WITHDRAWN, TenancyStatus.REJECTED)


class ClaimStatus(models.TextChoices):
    """Where a TenancyClaim stands (ADR-004)."""

    PENDING = "pending", _("Awaiting confirmation")
    CONFIRMED = "confirmed", _("Confirmed")
    DISPUTED = "disputed", _("Disputed between the parties")
    ESCALATED = "escalated", _("Escalated to platform admins")
    WITHDRAWN = "withdrawn", _("Withdrawn by the claimant")
    EXPIRED = "expired", _("Expired")


#: Statuses in which a claim is still live, so only one may exist per claimant
#: per unit.
OPEN_CLAIM_STATUSES = (ClaimStatus.PENDING, ClaimStatus.DISPUTED, ClaimStatus.ESCALATED)

#: Statuses in which a dispute is live, and therefore must carry a typed reason
#: and an author.
DISPUTED_CLAIM_STATUSES = (ClaimStatus.DISPUTED, ClaimStatus.ESCALATED)

#: Statuses that end a claim, and therefore require a resolution timestamp.
TERMINAL_CLAIM_STATUSES = (
    ClaimStatus.CONFIRMED,
    ClaimStatus.WITHDRAWN,
    ClaimStatus.EXPIRED,
)


class DisputeReason(models.TextChoices):
    """Why a claim was disputed, as raised (ADR-004 §2).

    Free text in ``dispute_note`` is additional context, never a substitute: an
    untyped dispute cannot be routed, so it can only go to a human, and the
    whole point of typing them is that most never reach one.

    Never rewritten. This records what the disputer actually claimed; where the
    dispute ends up is ``EscalationReason``.
    """

    DATES_INCORRECT = "dates_incorrect", _("The stay happened; the dates are wrong")
    NEVER_TENANTED = "never_tenanted", _("This person never lived here")
    DUPLICATE = "duplicate", _("Already covered by an existing tenancy")


class EscalationReason(models.TextChoices):
    """What an administrator has to decide (ADR-004 §2a).

    Deliberately separate from :class:`DisputeReason`. An identity question and
    a date question need completely different evidence, so collapsing them
    makes the queue harder to work — which is the opposite of what typing
    disputes is for. The admin queue sorts and filters on this.
    """

    COUNTER_UNRESOLVED = "counter_unresolved", _("Which set of dates is right")
    CORRECTION_DEFEATS_REVIEW = (
        "correction_defeats_review",
        _("Whether a review-defeating correction is honest"),
    )
    IDENTITY_DISPUTED = "identity_disputed", _("Whether this person lived here at all")
    DUPLICATE_UNMATCHED = "duplicate_unmatched", _("Whether an existing tenancy covers this")


@dataclass(frozen=True)
class DisputeTransition:
    """The permitted onward paths for one dispute reason."""

    #: Every escalation reason this dispute may arrive at. A dispute reason
    #: with an empty tuple could be raised and never routed.
    escalates_to: tuple[str, ...]

    #: Whether the parties can settle it without an administrator.
    can_resolve_between_parties: bool

    #: Whether the platform can decide it from data alone.
    auto_resolves: bool = False


#: The single source of truth for dispute routing (ADR-004 §2c).
#:
#: ``dispute_reason`` and ``escalation_reason`` are two enums with a mapping
#: between them. Left implicit, that mapping lives in whichever function happens
#: to branch on it, and a new dispute reason with no escalation path becomes a
#: dispute that can be **raised and never routed** — sitting in ``disputed`` for
#: ever, which is exactly the indefinite block the timeout exists to remove.
#:
#: The state machine reads this table. Nothing else encodes a transition, the
#: database constraint is generated from it, and raising a dispute with a reason
#: absent from it fails at construction, so the unroutable state cannot be built.
DISPUTE_TRANSITIONS: dict[str, DisputeTransition] = {
    DisputeReason.DATES_INCORRECT: DisputeTransition(
        escalates_to=(
            EscalationReason.COUNTER_UNRESOLVED,
            EscalationReason.CORRECTION_DEFEATS_REVIEW,
        ),
        can_resolve_between_parties=True,
    ),
    DisputeReason.NEVER_TENANTED: DisputeTransition(
        escalates_to=(EscalationReason.IDENTITY_DISPUTED,),
        can_resolve_between_parties=False,
    ),
    DisputeReason.DUPLICATE: DisputeTransition(
        escalates_to=(EscalationReason.DUPLICATE_UNMATCHED,),
        can_resolve_between_parties=False,
        auto_resolves=True,
    ),
}

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
    ApplicationStatus,
    ClaimStatus,
    ConfirmationSource,
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
    claim.save(update_fields=["status", "resolved_at", "updated_at"])

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

"""
Tenancy service functions (ADR-004).

The witnessed path. Accepting an application creates a confirmed tenancy
directly — no claim, no confirmation window, no dispute surface, no queue entry.
This is ADR-004's primary control on dispute volume, and the ADR says explicitly
that it must not be "simplified" into one uniform path for tidiness.
"""

from __future__ import annotations

import datetime as dt

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import User

from .constants import ApplicationStatus, ConfirmationSource, TenancyStatus
from .models import Application, Tenancy


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

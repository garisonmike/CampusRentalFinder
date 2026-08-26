"""
Saved properties and inquiries: the write paths.

The rate limits here are not hardening for later. **An inquiry is an
unsolicited message to a stranger**, so a messaging feature without limits is a
spam feature with extra steps, and the abuse surface arrives with the feature
rather than after it.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.capabilities import CaretakerPermission
from accounts.models import User
from properties.constants import TRANSACTABLE_PROPERTY_STATUSES
from properties.models import Property, Unit

from .constants import InquiryStatus
from .models import Inquiry, SavedProperty


class InquiryRateLimitError(ValidationError):
    """Too many inquiries."""


class InquiryNotAnswerableError(ValidationError):
    """This inquiry cannot be answered by this user, or not any more."""


class PropertyNotContactableError(ValidationError):
    """This property is not accepting inquiries."""


# ---------------------------------------------------------------------------
# Saved properties
# ---------------------------------------------------------------------------


@transaction.atomic
def save_property(user: User, property_obj: Property, *, note: str = "") -> SavedProperty:
    """Bookmark a property, idempotently.

    Saving twice is a double tap on a phone, not an error worth surfacing. The
    unique constraint is what makes it safe to treat as idempotent.
    """
    saved, _created = SavedProperty.all_objects.update_or_create(
        user=user, property_saved=property_obj, defaults={"note": note}
    )
    return saved


@transaction.atomic
def unsave_property(user: User, property_obj: Property) -> int:
    """Remove a bookmark. Removing one that is not there is not an error."""
    deleted, _detail = SavedProperty.all_objects.filter(
        user=user, property_saved=property_obj
    ).delete()
    return deleted


# ---------------------------------------------------------------------------
# Inquiries
# ---------------------------------------------------------------------------


def _assert_within_rate_limit(sender: User, unit: Unit, *, now: dt.datetime | None = None) -> None:
    """Per user **and** per unit, independently.

    Per user alone lets one account hammer a single landlord; per unit alone
    lets one account paper every listing in a town. Both windows are needed and
    neither subsumes the other.
    """
    now = now or timezone.now()
    since = now - dt.timedelta(hours=settings.INQUIRY_RATE_WINDOW_HOURS)

    by_sender = Inquiry.all_objects.filter(sender=sender, created_at__gte=since).count()
    if by_sender >= settings.INQUIRY_MAX_PER_USER:
        raise InquiryRateLimitError(
            {
                "detail": _(
                    "You have sent %(count)d inquiries recently. Give the "
                    "landlords a chance to reply before sending more."
                )
                % {"count": by_sender}
            }
        )

    by_unit = Inquiry.all_objects.filter(sender=sender, unit=unit, created_at__gte=since).count()
    if by_unit >= settings.INQUIRY_MAX_PER_UNIT:
        raise InquiryRateLimitError(
            {"detail": _("You have already asked about this unit recently.")}
        )


def _assert_contactable(unit: Unit) -> None:
    """A dormant or unpublished property has nobody to answer.

    A dormant one in particular belongs to an erased landlord (ADR-008), so an
    inquiry there is a message into a void the platform knows about in advance.
    """
    if unit.property.status not in TRANSACTABLE_PROPERTY_STATUSES:
        raise PropertyNotContactableError({"unit": _("This listing is not accepting inquiries.")})


@transaction.atomic
def send_inquiry(
    *,
    unit: Unit,
    sender: User,
    message: str,
    preferred_move_in_date: dt.date | None = None,
    now: dt.datetime | None = None,
) -> Inquiry:
    """Ask about a unit.

    **Never gated on verification.** `send_inquiry` is in `NEVER_GATED`
    (ADR-003): a student asking a landlord a question is how they find out
    whether to apply at all, and gating it would make verification a
    precondition for using the platform rather than for transacting on it.
    """
    now = now or timezone.now()

    if not message.strip():
        raise ValidationError({"message": _("An inquiry needs a message.")})

    _assert_contactable(unit)
    _assert_within_rate_limit(sender, unit, now=now)

    return Inquiry.all_objects.create(
        unit=unit,
        sender=sender,
        message=message.strip(),
        preferred_move_in_date=preferred_move_in_date,
    )


def may_respond_to(user: User, inquiry: Inquiry) -> bool:
    """Whether ``user`` may answer this inquiry.

    The landlord always; a caretaker only with ``RESPOND_INQUIRIES`` on a live
    assignment for that property (ADR-003). Answered here rather than in a view
    so the admin and any future path go through the same predicate.
    """
    from accounts.models import CaretakerAssignment

    landlord_profile = getattr(user, "landlord_profile", None)
    if landlord_profile is not None and inquiry.unit.property.landlord_id == landlord_profile.pk:
        return True

    return any(
        assignment.has_permission(CaretakerPermission.RESPOND_INQUIRIES)
        for assignment in CaretakerAssignment.all_objects.filter(
            user=user, property=inquiry.unit.property_id
        )
    )


@transaction.atomic
def respond_to_inquiry(
    inquiry: Inquiry, *, responder: User, response: str, now: dt.datetime | None = None
) -> Inquiry:
    """Answer an inquiry.

    One response, and it closes the exchange. This is deliberately not a
    thread: a thread would be a messaging product, and a messaging product is
    where the conversation stops producing an `Application` (ADR-004 §1.1).
    """
    now = now or timezone.now()

    if not inquiry.is_open():
        raise InquiryNotAnswerableError(
            {"status": _("This inquiry is already %(status)s.") % {"status": inquiry.status}}
        )
    if not response.strip():
        raise InquiryNotAnswerableError({"response": _("A response needs a message.")})
    if not may_respond_to(responder, inquiry):
        raise InquiryNotAnswerableError(
            {"responder": _("Only the landlord or an assigned caretaker may respond.")}
        )

    inquiry.status = InquiryStatus.ANSWERED
    inquiry.response = response.strip()
    inquiry.responded_by = responder
    inquiry.responded_at = now
    inquiry.save(update_fields=["status", "response", "responded_by", "responded_at", "updated_at"])
    return inquiry


@transaction.atomic
def close_inquiry(inquiry: Inquiry) -> Inquiry:
    """Close without answering. Not a rejection: an inquiry is not an
    application, and there is nothing to reject."""
    inquiry.status = InquiryStatus.CLOSED
    inquiry.save(update_fields=["status", "updated_at"])
    return inquiry


def expire_stale_inquiries(now: dt.datetime | None = None) -> int:
    """Mark unanswered inquiries expired once the window passes.

    Recorded rather than left ``sent`` for ever, so **"the landlord never
    replied" becomes a fact the student can see** instead of a screen that
    looks identical to one still waiting.
    """
    now = now or timezone.now()
    cutoff = now - dt.timedelta(days=settings.INQUIRY_EXPIRY_DAYS)

    return Inquiry.all_objects.filter(status=InquiryStatus.SENT, created_at__lte=cutoff).update(
        status=InquiryStatus.EXPIRED, updated_at=now
    )

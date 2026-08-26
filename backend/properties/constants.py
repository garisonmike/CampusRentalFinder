"""Reference data for Kenyan rental properties."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class PropertyType(models.TextChoices):
    """What actually exists around a Kenyan campus.

    Replaces the draft's US typology — apartment, condo, townhouse, studio
    (docs/AUDIT.md §3). "Condo" and "townhouse" are meaningless here, and
    bedsitter, single room and hostel block were all missing.
    """

    BEDSITTER = "bedsitter", _("Bedsitter")
    SINGLE_ROOM = "single_room", _("Single Room")
    ONE_BEDROOM = "one_bedroom", _("One Bedroom")
    TWO_BEDROOM = "two_bedroom", _("Two Bedroom")
    THREE_BEDROOM = "three_bedroom", _("Three Bedroom")
    HOSTEL_BLOCK = "hostel_block", _("Hostel Block")
    SHARED_HOUSE = "shared_house", _("Shared House")
    MAISONETTE = "maisonette", _("Maisonette")
    OTHER = "other", _("Other")


class PropertyStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")
    SUSPENDED = "suspended", _("Suspended")
    ARCHIVED = "archived", _("Archived")

    #: The owner erased their account (ADR-008). Distinct from ARCHIVED,
    #: which is a decision the owner made about a listing they still hold.
    #: A dormant property has no owner who can act on it, ever again, so
    #: nothing may move it back out of this state.
    DORMANT = "dormant", _("Dormant — the owner's account was erased")


#: Statuses in which a property accepts new applications, claims and inquiries.
#: DORMANT is absent by construction: there is nobody left to answer them.
TRANSACTABLE_PROPERTY_STATUSES = (PropertyStatus.PUBLISHED,)


class FurnishingStatus(models.TextChoices):
    UNFURNISHED = "unfurnished", _("Unfurnished")
    SEMI_FURNISHED = "semi_furnished", _("Semi Furnished")
    FURNISHED = "furnished", _("Furnished")


class PhotoProcessingStatus(models.TextChoices):
    """Where variant generation has got to (ADR-007).

    The API serves the original until variants are ready, so a slow or failed
    job degrades quality rather than breaking the page.
    """

    PENDING = "pending", _("Pending")
    READY = "ready", _("Ready")
    FAILED = "failed", _("Failed")


#: Longest edge, in pixels, for each derived variant.
PHOTO_VARIANTS: dict[str, int] = {
    "thumb": 400,
    "medium": 1024,
    "large": 1920,
}

#: Per-file upload cap. R2 storage is cheap and egress is free, but neither is
#: zero, and an unbounded upload size is an unbounded bill (ADR-007).
MAX_PHOTO_BYTES = 5 * 1024 * 1024

#: Per-unit photo count.
MAX_PHOTOS_PER_UNIT = 12

#: Content types accepted on upload. Validated against the actual bytes, never
#: the client-supplied header.
ALLOWED_PHOTO_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

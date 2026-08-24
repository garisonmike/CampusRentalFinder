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


class FurnishingStatus(models.TextChoices):
    UNFURNISHED = "unfurnished", _("Unfurnished")
    SEMI_FURNISHED = "semi_furnished", _("Semi Furnished")
    FURNISHED = "furnished", _("Furnished")

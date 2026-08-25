"""
Rules the database cannot express for properties.

Everything that can be a constraint is one. What lands here spans tables or
depends on related rows, which a PostgreSQL check constraint cannot see.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .constants import PropertyStatus
from .models import Property


class PropertyNotPublishableError(ValidationError):
    """Publishing this property would produce a listing nobody can see."""


def assert_property_is_publishable(property_obj: Property) -> None:
    """Refuse to publish a property that no tenant could reach.

    Two rules, both of which span rows and so cannot be constraints:

    **Coordinates.** ``PropertyCampusDistance`` computes ``straight_line_km`` by
    haversine on save and the column is NOT NULL, so an unpinned property
    cannot join a campus at all. Since the join is what makes a property visible
    to a university (ADR-002), publishing without coordinates produces a listing
    the landlord can see and nobody else can — a silent failure that looks like
    low demand.

    **At least one campus join.** Same consequence, arrived at differently: a
    property with coordinates but no join rows is still invisible. No constraint
    can express "at least one related row".
    """
    missing: dict[str, Any] = {}

    if property_obj.latitude is None or property_obj.longitude is None:
        missing["latitude"] = _(
            "Set the property's coordinates before publishing. Without them it "
            "cannot be matched to a campus, so no student would ever see it."
        )

    if not property_obj.campus_distances.exists():
        missing["campus_distances"] = _(
            "Link the property to at least one campus before publishing. A "
            "property with no campus is invisible to every university."
        )

    if missing:
        raise PropertyNotPublishableError(missing)


def publish(property_obj: Property) -> Property:
    """Publish a property, or refuse with a named reason.

    The single write path. ``published_at`` is set here because the model
    constraint requires it and nothing else should be choosing that timestamp.
    """
    assert_property_is_publishable(property_obj)

    property_obj.status = PropertyStatus.PUBLISHED
    property_obj.published_at = property_obj.published_at or timezone.now()
    property_obj.save(update_fields=["status", "published_at", "updated_at"])
    return property_obj

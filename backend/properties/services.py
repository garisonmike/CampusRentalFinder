"""
Rules the database cannot express for properties.

Everything that can be a constraint is one. What lands here spans tables or
depends on related rows, which a PostgreSQL check constraint cannot see.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .constants import PropertyStatus, VacancyFreshness
from .models import Property, Unit


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


# ---------------------------------------------------------------------------
# Vacancy provenance (ADR-002)
# ---------------------------------------------------------------------------


def vacancy_age_days(unit: Unit, *, now: dt.datetime | None = None) -> int | None:
    """How long since the landlord last stated this unit's vacancy.

    ``None`` when they never have. Distinct from a large number: "nobody has
    ever said" and "somebody said, long ago" are different facts and the UI
    words them differently.
    """
    if unit.vacant_count_updated_at is None:
        return None
    return ((now or timezone.now()) - unit.vacant_count_updated_at).days


def vacancy_freshness(unit: Unit, *, now: dt.datetime | None = None) -> str:
    """Which band this unit's vacancy count falls into.

    **Computed here and only here.** The API sends both this band and the raw
    age, and the client renders the band rather than re-deriving it -- a
    threshold in the client and a threshold in settings is two places for one
    rule, and `docs/OPERATIONS.md` records five occasions where the wrong copy
    won.
    """
    age = vacancy_age_days(unit, now=now)

    if age is None:
        return VacancyFreshness.UNKNOWN
    if age <= settings.VACANCY_FRESH_DAYS:
        return VacancyFreshness.FRESH
    if age <= settings.VACANCY_STALE_DAYS:
        return VacancyFreshness.AGEING
    return VacancyFreshness.STALE


@transaction.atomic
def state_vacancy(
    unit: Unit, *, vacant_count: int, stated_by, now: dt.datetime | None = None
) -> Unit:
    """The single write path for ``vacant_count``.

    Every write stamps the timestamp and the author together, so the three can
    never disagree. A bare ``unit.vacant_count = n; unit.save()`` elsewhere
    would leave a fresh number wearing an old date -- worse than a stale
    number, because the staleness signal would say it is current.
    """
    if vacant_count > unit.total_count:
        raise ValidationError(
            {
                "vacant_count": _("This unit has %(total)d rooms, so %(vacant)d cannot be free.")
                % {"total": unit.total_count, "vacant": vacant_count}
            }
        )
    if vacant_count < 0:
        raise ValidationError({"vacant_count": _("A vacancy count cannot be negative.")})

    unit.vacant_count = vacant_count
    unit.vacant_count_updated_at = now or timezone.now()
    unit.vacant_count_updated_by = stated_by
    unit.save(
        update_fields=[
            "vacant_count",
            "vacant_count_updated_at",
            "vacant_count_updated_by",
            "updated_at",
        ]
    )
    return unit


def units_with_stale_vacancy(now: dt.datetime | None = None):
    """Active units on published properties whose count has aged out.

    Includes units that have **never** been stated: a listing that has never
    said how many rooms are free is at least as misleading as one that said so
    two months ago, and the prompt is the same either way.
    """
    now = now or timezone.now()
    cutoff = now - dt.timedelta(days=settings.VACANCY_STALE_DAYS)

    return Unit.all_objects.filter(
        is_active=True, property__status=PropertyStatus.PUBLISHED
    ).filter(Q(vacant_count_updated_at__isnull=True) | Q(vacant_count_updated_at__lt=cutoff))


# ---------------------------------------------------------------------------
# Occupancy cross-check (ADR-002)
# ---------------------------------------------------------------------------
#
# A derived count, computed FOR COMPARISON ONLY. It never writes to
# `vacant_count`, never appears as an availability figure, and is not an
# alternative source of truth -- it is a way of noticing that the stated number
# and the tenancy records contradict each other.
#
# The asymmetry below is the whole design. Derived ABOVE stated is impossible
# and therefore a real signal. Derived BELOW stated is the normal case for the
# entire seeding period, and for ever after wherever a landlord lets rooms
# off-platform -- alerting on it would train everyone to ignore the alert,
# which costs more than the alert was ever worth.


@dataclass(frozen=True)
class OccupancyComparison:
    """What the tenancy records say about one unit, beside what it claims."""

    unit_id: int
    total_count: int
    stated_vacant: int
    #: Confirmed tenancies currently running in this unit.
    derived_occupied: int

    @property
    def stated_occupied(self) -> int:
        return self.total_count - self.stated_vacant

    @property
    def is_contradiction(self) -> bool:
        """More people confirmed in residence than the unit has rooms.

        Not "more than are stated occupied" -- that is merely the landlord
        being behind. This is a physical impossibility, and physical
        impossibilities are worth someone's attention.
        """
        return self.derived_occupied > self.total_count

    @property
    def is_informative(self) -> bool:
        """Whether the cross-check can say anything at all about this unit.

        A unit with no confirmed current tenancies tells us nothing: the rooms
        may be empty, or fully let off-platform, and the derived figure cannot
        distinguish those. Reported separately so "no contradictions found"
        is never mistaken for "everything checks out".
        """
        return self.derived_occupied > 0


def compare_occupancy(units=None, *, today: dt.date | None = None) -> list[OccupancyComparison]:
    """Derived-versus-stated occupancy, for every unit.

    One grouped query rather than one per unit: this runs over the whole
    catalogue and a per-unit count would make it quadratic in listings.
    """
    from django.db.models import Count

    from tenancies.models import Tenancy

    queryset = units if units is not None else Unit.all_objects.filter(is_active=True)
    unit_ids = list(queryset.values_list("pk", flat=True))

    occupied = dict(
        Tenancy.all_objects.filter(unit_id__in=unit_ids)
        .current(today=today)
        .values("unit_id")
        .annotate(total=Count("pk"))
        .values_list("unit_id", "total")
    )

    return [
        OccupancyComparison(
            unit_id=unit.pk,
            total_count=unit.total_count,
            stated_vacant=unit.vacant_count,
            derived_occupied=occupied.get(unit.pk, 0),
        )
        for unit in queryset
    ]


def occupancy_contradictions(units=None, *, today: dt.date | None = None):
    """Units where confirmed current tenancies exceed stated capacity.

    The only direction worth flagging. It means one of three things and all of
    them are worth a person looking: the capacity is wrong, a tenancy that
    ended was never closed, or somebody is letting more rooms than they have.
    """
    return [row for row in compare_occupancy(units, today=today) if row.is_contradiction]


def cross_check_coverage(units=None, *, today: dt.date | None = None) -> dict[str, int]:
    """How much of the catalogue the cross-check can speak to at all.

    **Reported rather than assumed.** Early on this is close to zero: almost
    no unit has a confirmed on-platform tenancy, so "no contradictions found"
    means "nothing was checked", and reporting the first as though it were a
    clean bill of health is exactly the shape `docs/OPERATIONS.md` catalogues.
    """
    rows = compare_occupancy(units, today=today)
    informative = [row for row in rows if row.is_informative]

    return {
        "units": len(rows),
        "informative": len(informative),
        "contradictions": len([row for row in informative if row.is_contradiction]),
    }

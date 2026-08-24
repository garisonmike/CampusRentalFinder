"""
Declarative filtering for property search (ADR-006).

Replaces the draft's ninety lines of ``if data.get('x'): queryset = ...``
(docs/AUDIT.md §7 item 6), which also contained a bounding-box calculation that
divided by zero at the equator.

The primary query — "properties near my campus, cheapest first" — is an indexed
range scan on the precomputed ``straight_line_km``, not a geometry operation.
"""

from __future__ import annotations

from decimal import Decimal

import django_filters
from django.db.models import Min, Q, QuerySet

from .constants import PropertyType
from .distances import bounding_box, haversine_km
from .models import Property


class PropertyFilter(django_filters.FilterSet):
    """Filters for the property list.

    Every filter is declared. The point of a FilterSet over a hand-rolled chain
    is not brevity: it is that the parameters are introspectable, so
    drf-spectacular documents them and the frontend's generated types know
    about them.
    """

    # -- Price, on the cheapest unit --------------------------------------
    min_rent = django_filters.NumberFilter(method="filter_min_rent", label="Minimum rent (KES)")
    max_rent = django_filters.NumberFilter(method="filter_max_rent", label="Maximum rent (KES)")

    # -- Type and location -------------------------------------------------
    unit_type = django_filters.MultipleChoiceFilter(
        choices=PropertyType.choices,
        field_name="units__unit_type",
        distinct=True,
        label="Unit type",
    )
    county = django_filters.CharFilter(field_name="county", lookup_expr="iexact")
    town = django_filters.CharFilter(field_name="town", lookup_expr="icontains")
    estate = django_filters.CharFilter(field_name="estate", lookup_expr="icontains")

    # -- Campus proximity: the platform's primary query --------------------
    max_distance_km = django_filters.NumberFilter(
        method="filter_max_distance",
        label="Maximum straight-line distance to campus (km)",
        help_text="Direct distance, not walking distance.",
    )
    campus = django_filters.NumberFilter(
        field_name="campus_distances__campus_id", label="Campus id"
    )

    # -- Amenities ---------------------------------------------------------
    has_water_tank = django_filters.BooleanFilter()
    has_borehole = django_filters.BooleanFilter()
    has_backup_power = django_filters.BooleanFilter()
    has_security_guard = django_filters.BooleanFilter()
    has_wifi = django_filters.BooleanFilter()
    caretaker_on_site = django_filters.BooleanFilter()

    # -- Availability ------------------------------------------------------
    available_only = django_filters.BooleanFilter(
        method="filter_available_only", label="Only properties with a vacant unit"
    )
    furnished = django_filters.CharFilter(field_name="units__furnished", distinct=True)

    # -- Free text ---------------------------------------------------------
    q = django_filters.CharFilter(method="filter_search", label="Search")

    class Meta:
        model = Property
        fields: list[str] = []

    # -- Implementations ---------------------------------------------------

    def filter_min_rent(self, queryset: QuerySet, name: str, value: Decimal) -> QuerySet:
        return queryset.filter(units__rent_kes__gte=value, units__is_active=True).distinct()

    def filter_max_rent(self, queryset: QuerySet, name: str, value: Decimal) -> QuerySet:
        return queryset.filter(units__rent_kes__lte=value, units__is_active=True).distinct()

    def filter_max_distance(self, queryset: QuerySet, name: str, value: Decimal) -> QuerySet:
        """Within N km of a campus, by the precomputed straight-line distance.

        An indexed range scan on a stored number. No geometry, no PostGIS, and
        faster than a spatial index would be for this access pattern because
        the work was done at write time (ADR-006).
        """
        return queryset.filter(campus_distances__straight_line_km__lte=value).distinct()

    def filter_available_only(self, queryset: QuerySet, name: str, value: bool) -> QuerySet:
        if not value:
            return queryset
        return queryset.filter(units__vacant_count__gt=0, units__is_active=True).distinct()

    def filter_search(self, queryset: QuerySet, name: str, value: str) -> QuerySet:
        return queryset.filter(
            Q(name__icontains=value)
            | Q(description__icontains=value)
            | Q(estate__icontains=value)
            | Q(landmark__icontains=value)
            | Q(town__icontains=value)
        ).distinct()


def order_by_campus_distance(queryset: QuerySet) -> QuerySet:
    """Order by distance to the nearest matching campus.

    Annotated rather than ordered on the joined column directly: ``.distinct()``
    and ``ORDER BY`` on a joined column interact badly, and a property serving
    two campuses of the same university would otherwise appear twice
    (ADR-002).
    """
    return queryset.annotate(nearest_campus_km=Min("campus_distances__straight_line_km")).order_by(
        "nearest_campus_km", "-published_at"
    )


def within_radius(queryset: QuerySet, latitude: float, longitude: float, radius_km: float) -> list:
    """Properties within an exact radius of an arbitrary point.

    A bounding box narrows the candidates in SQL, then the exact distance is
    filtered in Python — the box's corners are √2 times the stated radius.

    Not a FilterSet filter: it returns a list rather than a queryset, so it
    cannot be paginated, and ADR-006 names arbitrary-origin radius search at
    scale as a trigger for moving to PostGIS. Kept here for the small cases.
    """
    min_lat, max_lat, min_lon, max_lon = bounding_box(latitude, longitude, radius_km)

    candidates = queryset.filter(
        latitude__gte=min_lat,
        latitude__lte=max_lat,
        longitude__gte=min_lon,
        longitude__lte=max_lon,
        latitude__isnull=False,
        longitude__isnull=False,
    )

    return [
        candidate
        for candidate in candidates
        if haversine_km(latitude, longitude, candidate.latitude, candidate.longitude) <= radius_km
    ]

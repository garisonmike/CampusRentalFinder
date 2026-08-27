"""
Property and unit read endpoints (ADR-001, ADR-002).

Public: browsing listings is the product, and gating it behind a login is not
a verification policy but an outage (ADR-003). Everything here is nonetheless
**tenant-scoped** — a student on the Kenyatta subdomain sees properties joined
to a Kenyatta campus and nothing else, and that join is what makes a listing
visible at all (ADR-002).

Every list endpoint is covered by a query-count assertion. A listing page that
issues one query per row still renders correctly in a test and falls over at
forty rows, which is precisely the size where nobody notices until it is live.
"""

from __future__ import annotations

from django.db.models import Min, Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny

from config.api.throttling import Scope
from config.api.views import SchemaSafeQuerysetMixin
from config.middleware import get_current_university

from .constants import PropertyStatus
from .filters import PropertyFilter, order_by_campus_distance
from .models import Property, Unit, UnitPhoto
from .serializers import (
    PropertyDetailSerializer,
    PropertySummarySerializer,
    UnitDetailSerializer,
)


def _require_tenant():
    """The resolved university, or a 404.

    A request with no tenant is a request to a host we do not serve. 404 rather
    than 400: the alternative advertises which subdomains exist.
    """
    university = get_current_university()
    if university is None:
        raise NotFound("No university is configured for this host.")
    return university


class PublishedPropertyMixin:
    """The one queryset definition every property endpoint starts from.

    Scoped to the resolved tenant and filtered to published listings, in one
    place, so a new endpoint cannot accidentally expose drafts -- which would
    leak a landlord's unfinished work and, worse, properties with no
    coordinates that were never meant to be findable.
    """

    def base_queryset(self):
        university = _require_tenant()
        return (
            Property.objects.for_tenant(university)
            .filter(status=PropertyStatus.PUBLISHED)
            .distinct()
        )


@extend_schema_view(
    get=extend_schema(
        summary="Search published properties",
        description=(
            "Properties joined to a campus of the university resolved from the "
            "request host (ADR-002). Only published listings appear.\n\n"
            "Ordering by `distance` annotates `nearest_campus_km`, which is a "
            "STRAIGHT-LINE figure -- label it as such. Walking figures live on "
            "the property detail and are legitimately null."
        ),
        parameters=[
            OpenApiParameter(
                name="ordering",
                description=(
                    "`distance` (nearest campus first), `rent` (cheapest "
                    "first), or `-published_at` (newest first, the default)."
                ),
                required=False,
                type=str,
                enum=["distance", "rent", "-published_at"],
            )
        ],
    )
)
class PropertyListView(SchemaSafeQuerysetMixin, PublishedPropertyMixin, ListAPIView):
    """Listing search."""

    serializer_class = PropertySummarySerializer
    permission_classes = [AllowAny]
    filterset_class = PropertyFilter
    throttle_scope = Scope.PUBLIC_READ
    schema_queryset = Property.all_objects
    # The FilterSet owns filtering. SearchFilter and OrderingFilter are dropped
    # here deliberately: leaving them on would give the API a second,
    # differently-spelled way to do the same thing, and `?search=` and `?q=`
    # behaving differently is a support ticket nobody can reproduce.
    filter_backends = [DjangoFilterBackend]

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        queryset = (
            self.base_queryset()
            .select_related("landlord__user")
            .prefetch_related(
                Prefetch(
                    "units",
                    queryset=Unit.all_objects.filter(is_active=True).order_by("rent_kes"),
                )
            )
            .annotate(cheapest_rent_kes=Min("units__rent_kes"))
            # Explicit, because `.distinct()` on the scoped queryset drops the
            # model's Meta ordering, and an unordered paginated queryset can
            # repeat a row on page 2 that already appeared on page 1.
            .order_by("-published_at", "-id")
        )

        if self.request.query_params.get("ordering") == "distance":
            return order_by_campus_distance(queryset)
        if self.request.query_params.get("ordering") == "rent":
            return queryset.order_by("cheapest_rent_kes", "-published_at")
        return queryset


@extend_schema_view(
    get=extend_schema(
        summary="One property, with its units and campus distances",
        description=(
            "`straight_line_km` is as the crow flies. `walking_minutes` and "
            "`walking_distance_km` come from a routing provider and are "
            "legitimately null -- render an em dash, never a zero and never "
            "the straight-line figure (ADR-002)."
        ),
    )
)
class PropertyDetailView(PublishedPropertyMixin, RetrieveAPIView):
    """One property."""

    serializer_class = PropertyDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"
    throttle_scope = Scope.PUBLIC_READ

    def get_queryset(self):
        return (
            self.base_queryset()
            .select_related("landlord__user")
            .prefetch_related(
                Prefetch(
                    "units",
                    queryset=Unit.all_objects.filter(is_active=True).order_by("rent_kes"),
                ),
                "campus_distances__campus",
                "campus_distances__university",
            )
        )


@extend_schema_view(
    get=extend_schema(
        summary="One unit, with its photos",
        description=(
            "`vacant_count` is authoritative for availability, not the absence "
            "of a tenancy: a Unit row may represent a POOL of identical rooms "
            "(forty bedsitters as one row), so several students hold the same "
            "unit concurrently and that is correct."
        ),
    )
)
class UnitDetailView(RetrieveAPIView):
    """One unit."""

    serializer_class = UnitDetailSerializer
    permission_classes = [AllowAny]
    throttle_scope = Scope.PUBLIC_READ

    def get_queryset(self):
        university = _require_tenant()
        return (
            Unit.objects.for_tenant(university)
            .filter(is_active=True, property__status=PropertyStatus.PUBLISHED)
            .select_related("property")
            .prefetch_related(
                Prefetch("photos", queryset=UnitPhoto.all_objects.order_by("sort_order"))
            )
            .distinct()
        )

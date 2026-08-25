"""
Background jobs (ADR-002, ADR-007).

Both jobs here fail silently if the worker stops, so the tests care most about
what happens when they *do not* succeed: an image whose variants never arrive
still renders, and a routing failure leaves a gap rather than a guess.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import override_settings
from django.utils import timezone

from properties.constants import PHOTO_VARIANTS, PhotoProcessingStatus
from properties.jobs import (
    _variant_key,
    generate_photo_variants,
    route_campus_distance,
    route_stale_distances,
)
from properties.models import PropertyCampusDistance, UnitPhoto
from properties.routing.base import RouteResult
from properties.routing.registry import get_route_provider

pytestmark = pytest.mark.django_db


def upload_image(key: str, *, size: tuple[int, int] = (2400, 1600)) -> None:
    """Put a real image in the public bucket at ``key``."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (34, 197, 94)).save(buffer, format="JPEG")
    storages["default"].save(key, ContentFile(buffer.getvalue()))


# ---------------------------------------------------------------------------
# Image variants
# ---------------------------------------------------------------------------


class TestPhotoVariants:
    def test_it_produces_every_variant_and_marks_the_photo_ready(self, unit_photo_factory):
        photo = unit_photo_factory()
        upload_image(photo.original_key)

        generate_photo_variants(photo.pk)
        photo.refresh_from_db()

        assert photo.processing_status == PhotoProcessingStatus.READY
        assert photo.thumb_key and photo.medium_key and photo.large_key
        for key in (photo.thumb_key, photo.medium_key, photo.large_key):
            assert storages["default"].exists(key)

    def test_variants_respect_the_longest_edge(self, unit_photo_factory):
        from PIL import Image

        photo = unit_photo_factory()
        upload_image(photo.original_key, size=(2400, 1600))

        generate_photo_variants(photo.pk)
        photo.refresh_from_db()

        with storages["default"].open(photo.thumb_key) as handle:
            thumb = Image.open(handle)
            thumb.load()

        assert max(thumb.size) <= PHOTO_VARIANTS["thumb"]

    def test_it_records_the_original_dimensions(self, unit_photo_factory):
        """Layout stability: the client needs them to reserve space."""
        photo = unit_photo_factory(width=None, height=None)
        upload_image(photo.original_key, size=(1200, 900))

        generate_photo_variants(photo.pk)
        photo.refresh_from_db()

        assert (photo.width, photo.height) == (1200, 900)

    def test_variants_are_webp(self, unit_photo_factory):
        photo = unit_photo_factory()
        upload_image(photo.original_key)

        generate_photo_variants(photo.pk)
        photo.refresh_from_db()

        assert photo.medium_key.endswith(".webp")

    def test_a_missing_original_marks_the_photo_failed_with_a_reason(self, unit_photo_factory):
        """The constraint requires a reason, so the job must supply one."""
        photo = unit_photo_factory(original_key="properties/units/nothing-here.jpg")

        generate_photo_variants(photo.pk)
        photo.refresh_from_db()

        assert photo.processing_status == PhotoProcessingStatus.FAILED
        assert photo.processing_error

    def test_a_failed_photo_still_renders_the_original(self, unit_photo_factory):
        """Degradation, not breakage (ADR-007)."""
        photo = unit_photo_factory(original_key="properties/units/nothing-here.jpg")

        generate_photo_variants(photo.pk)
        photo.refresh_from_db()

        assert photo.display_key("medium") == photo.original_key

    def test_it_tolerates_the_photo_being_deleted(self, unit_photo_factory):
        """Jobs must be idempotent and survive the row going away."""
        photo = unit_photo_factory()
        photo_id = photo.pk
        UnitPhoto.all_objects.filter(pk=photo_id).delete()

        generate_photo_variants(photo_id)  # must not raise

    def test_running_it_twice_is_idempotent(self, unit_photo_factory):
        """RQ retries, so a second run must not double anything."""
        photo = unit_photo_factory()
        upload_image(photo.original_key)

        generate_photo_variants(photo.pk)
        photo.refresh_from_db()
        first_keys = (photo.thumb_key, photo.medium_key, photo.large_key)

        generate_photo_variants(photo.pk)
        photo.refresh_from_db()

        assert (photo.thumb_key, photo.medium_key, photo.large_key) == first_keys

    def test_variant_keys_are_derived_deterministically(self):
        """So a regenerated variant replaces its predecessor rather than
        accumulating orphans in the bucket."""
        key = "properties/units/abc/original.jpg"

        assert _variant_key(key, "thumb") == "properties/units/abc/original.thumb.webp"
        assert _variant_key(key, "thumb") == _variant_key(key, "thumb")


# ---------------------------------------------------------------------------
# Campus routing
# ---------------------------------------------------------------------------


class StubProvider:
    name = "stub"

    def route(self, origin, destination):
        return RouteResult(distance_km=1.8, duration_minutes=23, provider=self.name)


class FailingProvider:
    name = "failing"

    def route(self, origin, destination):
        return None


STUB = "tests.test_jobs.StubProvider"
FAILING = "tests.test_jobs.FailingProvider"


@pytest.fixture
def joined(university, campus_factory, property_factory, campus_distance_factory):
    campus = campus_factory(university=university, latitude=-1.18, longitude=36.93)
    prop = property_factory(latitude=-1.19, longitude=36.94)
    return campus_distance_factory(property=prop, university=university, campus=campus)


class TestCampusRouting:
    def test_a_successful_route_fills_every_walking_field(self, joined):
        with override_settings(ROUTE_PROVIDER=STUB):
            route_campus_distance(joined.pk)

        joined.refresh_from_db()

        assert joined.walking_distance_km == Decimal("1.80")
        assert joined.walking_minutes == 23
        assert joined.routed_at is not None
        assert joined.route_provider == "stub"

    def test_a_failure_leaves_the_walking_fields_null(self, joined):
        """The rule ADR-002 exists to protect.

        No route, quota exhausted or service down all mean "we do not know",
        and the UI renders an em dash rather than a guess.
        """
        with override_settings(ROUTE_PROVIDER=FAILING):
            route_campus_distance(joined.pk)

        joined.refresh_from_db()

        assert joined.walking_minutes is None
        assert joined.walking_distance_km is None
        assert joined.routed_at is None

    def test_a_failure_never_falls_back_to_the_straight_line(self, joined):
        """The specific thing that must not happen.

        straight_line_km is present and would divide neatly by 5 km/h. The job
        must not be tempted.
        """
        assert joined.straight_line_km > 0

        with override_settings(ROUTE_PROVIDER=FAILING):
            route_campus_distance(joined.pk)

        joined.refresh_from_db()

        assert joined.walking_distance_km != joined.straight_line_km
        assert joined.walking_distance_km is None

    def test_a_failed_row_is_retried_by_the_sweep(self, joined):
        """routed_at stays null, so the sweep does not treat it as done."""
        with override_settings(ROUTE_PROVIDER=FAILING):
            route_campus_distance(joined.pk)

        joined.refresh_from_db()
        assert joined.routed_at is None

        with override_settings(ROUTE_PROVIDER=STUB):
            route_stale_distances()

        joined.refresh_from_db()
        assert joined.walking_minutes == 23

    def test_it_tolerates_the_row_being_deleted(self, joined):
        distance_id = joined.pk
        PropertyCampusDistance.all_objects.filter(pk=distance_id).delete()

        with override_settings(ROUTE_PROVIDER=STUB):
            route_campus_distance(distance_id)  # must not raise

    def test_the_sweep_takes_never_routed_rows_first(
        self, university, campus_factory, property_factory, campus_distance_factory
    ):
        campus = campus_factory(university=university)
        routed = campus_distance_factory(
            property=property_factory(), university=university, campus=campus
        )
        PropertyCampusDistance.all_objects.filter(pk=routed.pk).update(
            walking_minutes=10,
            walking_distance_km=Decimal("1.00"),
            routed_at=timezone.now(),
        )
        never = campus_distance_factory(
            property=property_factory(),
            university=university,
            campus=campus_factory(university=university, name="Second"),
        )

        with override_settings(ROUTE_PROVIDER=STUB):
            assert route_stale_distances(limit=1) == 1

        never.refresh_from_db()
        assert never.walking_minutes == 23


class TestProviderRegistry:
    def test_the_default_provider_routes_nothing(self):
        """An unconfigured deployment leaves gaps, not invented numbers."""
        provider = get_route_provider()

        assert provider.route((-1.19, 36.94), (-1.18, 36.93)) is None

    def test_the_provider_is_swappable_by_settings(self):
        """ADR-002: a settings change and one new class."""
        with override_settings(ROUTE_PROVIDER=STUB):
            assert get_route_provider().name == "stub"

    def test_openrouteservice_returns_none_without_a_key(self):
        from properties.routing.openrouteservice import OpenRouteServiceProvider

        provider = OpenRouteServiceProvider(api_key="")

        assert provider.route((-1.19, 36.94), (-1.18, 36.93)) is None

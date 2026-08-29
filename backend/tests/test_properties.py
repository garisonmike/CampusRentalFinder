"""
Properties, units and campus distances (ADR-002, ADR-006).

The three things this file is really guarding:

- vacancy is expressible and cannot exceed the unit count;
- a property is visible to a university only through the join, and only its own
  tenant sees it;
- the two distance figures mean different things and walking time is never
  invented.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from config.tenancy import TenantScopeError
from properties.constants import (
    ALLOWED_PHOTO_CONTENT_TYPES,
    MAX_PHOTO_BYTES,
    MAX_PHOTOS_PER_UNIT,
    PhotoProcessingStatus,
    PropertyStatus,
    PropertyType,
)
from properties.distances import bounding_box, haversine_km, straight_line_km
from properties.models import Property, PropertyCampusDistance, Unit, UnitPhoto
from properties.services import PropertyNotPublishableError, publish

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


class TestProperty:
    def test_the_kenyan_property_types_are_available(self):
        """The draft offered apartment, condo, townhouse (docs/AUDIT.md §3)."""
        values = set(PropertyType.values)

        assert {"bedsitter", "single_room", "one_bedroom", "two_bedroom", "hostel_block"} <= values
        assert "condo" not in values
        assert "townhouse" not in values

    def test_the_address_is_county_town_estate(self, property_factory):
        prop = property_factory()

        assert prop.county == "nairobi"
        assert prop.estate
        # No state, no ZIP.
        assert not hasattr(prop, "zip_code")
        assert not hasattr(prop, "state")

    def test_a_published_property_must_have_a_timestamp(self, property_factory):
        """The listing page orders by it, so a null would be unsortable."""
        with pytest.raises(IntegrityError), transaction.atomic():
            property_factory(status=PropertyStatus.PUBLISHED, published_at=None)

    def test_a_draft_property_needs_no_timestamp(self, draft_property_factory):
        assert draft_property_factory().published_at is None

    def test_coordinates_are_range_checked(self, property_factory):
        with pytest.raises(IntegrityError), transaction.atomic():
            property_factory(latitude=91.0)

    def test_slugs_are_unique(self, property_factory):
        first = property_factory()

        with pytest.raises(IntegrityError), transaction.atomic():
            property_factory(slug=first.slug)

    def test_register_view_refreshes_the_in_memory_value(self, property_factory):
        """FIXED behaviour, guarded.

        The draft assigned an F() expression to the instance and then
        serialised that same instance, so the rental detail endpoint raised
        TypeError for every visitor who was not the owner (docs/AUDIT.md §4.1).
        """
        prop = property_factory(view_count=0)

        prop.register_view()

        assert prop.view_count == 1
        assert isinstance(prop.view_count, int)
        # The value must survive being read again, i.e. it is a number and not
        # an unresolved database expression.
        assert prop.view_count + 1 == 2

    def test_a_landlord_cannot_be_deleted_out_from_under_a_property(self, property_factory):
        """PROTECT by default. Deleting an owner must not orphan listings."""
        prop = property_factory()

        with pytest.raises(IntegrityError), transaction.atomic():
            prop.landlord.delete()


# ---------------------------------------------------------------------------
# Unit and vacancy
# ---------------------------------------------------------------------------


class TestUnit:
    def test_a_unit_row_can_represent_a_pool(self, unit_factory):
        """Forty bedsitters in a block are one row, which is how vacancy works."""
        unit = unit_factory(label="Bedsitters", total_count=40, vacant_count=3)

        assert unit.total_count == 40
        assert unit.vacant_count == 3

    def test_vacancy_cannot_exceed_the_total(self, unit_factory):
        """A listing that claims more free units than it has is lying."""
        with pytest.raises(IntegrityError), transaction.atomic():
            unit_factory(total_count=5, vacant_count=6)

    def test_rent_must_be_positive(self, unit_factory):
        with pytest.raises(IntegrityError), transaction.atomic():
            unit_factory(rent_kes=Decimal("0.00"))

    def test_rent_is_a_decimal_in_kes(self, unit_factory):
        unit = unit_factory(rent_kes=Decimal("9500.00"))

        assert isinstance(unit.rent_kes, Decimal)
        assert unit.rent_kes == Decimal("9500.00")

    def test_a_shared_ablutions_unit_is_listable(self, unit_factory):
        """The draft required bathrooms >= 1, so a hostel block could not list."""
        unit = unit_factory(has_private_bathroom=False, bedrooms=0)

        assert unit.pk is not None

    def test_size_is_square_metres(self, unit_factory):
        unit = unit_factory(size_sqm=20)

        assert unit.size_sqm == 20
        assert not hasattr(unit, "square_footage")

    def test_minimum_stay_defaults_to_a_semester(self, unit_factory):
        """The draft defaulted to a 12-month lease. Students rent by semester."""
        assert unit_factory().min_stay_months == 4

    def test_labels_are_unique_within_a_property(self, unit_factory, property_factory):
        prop = property_factory()
        unit_factory(property=prop, label="B12")

        with pytest.raises(IntegrityError), transaction.atomic():
            unit_factory(property=prop, label="B12")

    def test_the_same_label_may_exist_at_another_property(self, unit_factory, property_factory):
        unit_factory(property=property_factory(), label="B12")
        unit_factory(property=property_factory(), label="B12")

        assert Unit.all_objects.filter(label="B12").count() == 2

    def test_is_available_is_a_method_not_a_property(self, unit_factory):
        """`Unit.property` shadows the builtin inside the class body.

        `@property` there resolves to a ForeignKey and raises at import, so
        anything on Unit that would naturally be a property has to be a method.
        """
        unit = unit_factory(vacant_count=1)

        assert callable(unit.is_available)
        assert unit.is_available() is True

    def test_a_fully_occupied_unit_is_not_available(self, unit_factory):
        assert unit_factory(total_count=4, vacant_count=0).is_available() is False

    def test_an_inactive_unit_is_not_available(self, unit_factory):
        assert unit_factory(vacant_count=1, is_active=False).is_available() is False


# ---------------------------------------------------------------------------
# Distance computation (ADR-002, ADR-006)
# ---------------------------------------------------------------------------


class TestHaversine:
    def test_zero_distance_between_identical_points(self):
        assert haversine_km(-1.286389, 36.817223, -1.286389, 36.817223) == pytest.approx(0.0)

    def test_a_known_distance(self):
        """Nairobi CBD to Kenyatta University main campus, roughly 22 km."""
        distance = haversine_km(-1.286389, 36.817223, -1.180278, 36.929722)

        assert 15 < distance < 25

    def test_it_is_symmetric(self):
        there = haversine_km(-1.28, 36.81, -1.18, 36.92)
        back = haversine_km(-1.18, 36.92, -1.28, 36.81)

        assert there == pytest.approx(back)

    def test_it_works_on_the_equator(self):
        """Kenya straddles it. Anything that fails here fails in production."""
        assert haversine_km(0.0, 36.8, 0.0, 36.9) > 0


class TestBoundingBox:
    def test_it_does_not_divide_by_zero_at_the_equator(self):
        """The draft's `abs(lat / 90)` term crashed here.

        It survived review because at any plausible test latitude the
        correction is nearly 1, so the answer looked right — and Kenya is the
        one market where the failing value is a normal coordinate.
        """
        min_lat, max_lat, min_lon, max_lon = bounding_box(0.0, 36.8, 5.0)

        assert min_lat < 0.0 < max_lat
        assert min_lon < 36.8 < max_lon

    def test_the_longitude_term_is_a_cosine(self):
        """At the equator the box is nearly square; at 60° it is twice as wide."""
        _, _, eq_min_lon, eq_max_lon = bounding_box(0.0, 36.8, 10.0)
        _, _, hi_min_lon, hi_max_lon = bounding_box(60.0, 36.8, 10.0)

        assert (hi_max_lon - hi_min_lon) == pytest.approx(2 * (eq_max_lon - eq_min_lon), rel=0.01)

    def test_it_encloses_the_radius(self):
        latitude, longitude, radius = -1.286389, 36.817223, 5.0
        _, max_lat, _, _ = bounding_box(latitude, longitude, radius)

        # A point exactly `radius` north must be inside the box.
        assert haversine_km(latitude, longitude, max_lat, longitude) == pytest.approx(
            radius, rel=0.01
        )

    def test_it_clamps_at_the_poles(self):
        _, max_lat, min_lon, max_lon = bounding_box(89.999, 0.0, 100.0)

        assert max_lat <= 90.0
        assert min_lon >= -180.0 and max_lon <= 180.0


# ---------------------------------------------------------------------------
# PropertyCampusDistance
# ---------------------------------------------------------------------------


class TestCampusDistance:
    def test_straight_line_is_computed_on_save(
        self, property_factory, campus_factory, campus_distance_factory, university
    ):
        campus = campus_factory(university=university, latitude=-1.180278, longitude=36.929722)
        prop = property_factory(latitude=-1.286389, longitude=36.817223)

        join = campus_distance_factory(property=prop, university=university, campus=campus)

        assert join.straight_line_km > 0
        assert join.straight_line_km == straight_line_km(
            prop.latitude, prop.longitude, campus.latitude, campus.longitude
        )

    def test_it_is_recomputed_when_coordinates_change(
        self, property_factory, campus_factory, campus_distance_factory, university
    ):
        """Correcting a property's coordinates fixes its distances in place."""
        campus = campus_factory(university=university)
        prop = property_factory(latitude=-1.286389, longitude=36.817223)
        join = campus_distance_factory(property=prop, university=university, campus=campus)
        original = join.straight_line_km

        prop.latitude = -1.5
        prop.save(update_fields=["latitude"])
        join.property.refresh_from_db()
        join.save()

        assert join.straight_line_km != original

    def test_walking_figures_start_null(self, campus_distance_factory, university, campus_factory):
        """They come only from a routing provider (ADR-002)."""
        join = campus_distance_factory(
            university=university, campus=campus_factory(university=university)
        )

        assert join.walking_minutes is None
        assert join.walking_distance_km is None
        assert join.routed_at is None

    def test_the_routed_fields_move_together(
        self, campus_distance_factory, university, campus_factory
    ):
        """A walking time with no provider and no timestamp is unaccountable."""
        join = campus_distance_factory(
            university=university, campus=campus_factory(university=university)
        )
        join.walking_minutes = 15

        with pytest.raises(IntegrityError), transaction.atomic():
            join.save()

    def test_a_complete_routing_result_is_accepted(
        self, campus_distance_factory, university, campus_factory
    ):
        join = campus_distance_factory(
            university=university, campus=campus_factory(university=university)
        )
        join.walking_minutes = 15
        join.walking_distance_km = Decimal("1.20")
        join.routed_at = timezone.now()
        join.route_provider = "openrouteservice"
        join.save()

        assert join.walking_minutes == 15

    def test_no_code_path_derives_walking_time_from_the_straight_line(self):
        """ADR-002 forbids it, so the module must offer no way to do it.

        Asserted against the module surface rather than a behaviour: the risk
        is a future helper that looks convenient.
        """
        import properties.distances as module

        names = [name for name in dir(module) if not name.startswith("_")]

        assert not any("walk" in name.lower() for name in names)
        assert not any("minute" in name.lower() for name in names)

    def test_one_row_per_property_and_campus(
        self, property_factory, campus_factory, campus_distance_factory, university
    ):
        prop = property_factory()
        campus = campus_factory(university=university)
        campus_distance_factory(property=prop, university=university, campus=campus)

        with pytest.raises(IntegrityError), transaction.atomic():
            campus_distance_factory(property=prop, university=university, campus=campus)

    def test_only_one_primary_campus_per_property(
        self, property_factory, campus_factory, campus_distance_factory, university
    ):
        prop = property_factory()
        campus_distance_factory(
            property=prop,
            university=university,
            campus=campus_factory(university=university),
            is_primary=True,
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            campus_distance_factory(
                property=prop,
                university=university,
                campus=campus_factory(university=university, name="Second"),
                is_primary=True,
            )


# ---------------------------------------------------------------------------
# Tenant scoping (ADR-001, ADR-002)
# ---------------------------------------------------------------------------


class TestPropertyScoping:
    def test_an_unqualified_property_query_raises(self, property_factory):
        property_factory()

        with pytest.raises(TenantScopeError):
            list(Property.objects.all())

    def test_a_property_is_visible_only_to_universities_it_joins(
        self,
        property_factory,
        campus_factory,
        campus_distance_factory,
        university,
        university_factory,
    ):
        other = university_factory()
        prop = property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )

        assert prop in Property.objects.for_tenant(university)
        assert prop not in Property.objects.for_tenant(other)

    def test_one_property_can_serve_two_universities(
        self,
        property_factory,
        campus_factory,
        campus_distance_factory,
        university,
        university_factory,
    ):
        """The whole reason ADR-002 exists.

        A hostel between two campuses is one row, so its vacancy count cannot
        disagree with itself.
        """
        other = university_factory()
        prop = property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        campus_distance_factory(
            property=prop, university=other, campus=campus_factory(university=other)
        )

        assert prop in Property.objects.for_tenant(university)
        assert prop in Property.objects.for_tenant(other)
        assert Property.all_objects.count() == 1

    def test_a_property_with_no_join_rows_is_invisible_to_everyone(
        self, property_factory, university
    ):
        """A listing the landlord created that nobody can see (ADR-002).

        No constraint can express "at least one related row", so the serializer
        enforces it — this test is what makes the consequence visible.
        """
        orphan = property_factory()

        assert orphan not in Property.objects.for_tenant(university)
        assert PropertyCampusDistance.all_objects.filter(property=orphan).count() == 0

    def test_units_scope_through_their_property(
        self,
        unit_factory,
        property_factory,
        campus_factory,
        campus_distance_factory,
        university,
        university_factory,
    ):
        prop = property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        unit = unit_factory(property=prop)

        assert unit in Unit.objects.for_tenant(university)
        assert unit not in Unit.objects.for_tenant(university_factory())

    def test_the_join_scopes_directly(
        self, campus_distance_factory, campus_factory, university, university_factory
    ):
        join = campus_distance_factory(
            university=university, campus=campus_factory(university=university)
        )

        assert join in PropertyCampusDistance.objects.for_tenant(university)
        assert join not in PropertyCampusDistance.objects.for_tenant(university_factory())


# ---------------------------------------------------------------------------
# UnitPhoto (ADR-007)
# ---------------------------------------------------------------------------


class TestUnitPhoto:
    def test_a_photo_stores_keys_not_files(self, unit_photo_factory):
        """Object storage, never local disk, in any environment (ADR-007)."""
        photo = unit_photo_factory()

        assert photo.original_key.startswith("properties/")
        assert not hasattr(photo, "image")

    def test_only_one_primary_photo_per_unit(self, unit_photo_factory, unit_factory):
        """A constraint, not a save() override.

        The draft enforced this in save(), which a bulk update walks straight
        past (docs/AUDIT.md §7).
        """
        unit = unit_factory()
        unit_photo_factory(unit=unit, is_primary=True)

        with pytest.raises(IntegrityError), transaction.atomic():
            unit_photo_factory(unit=unit, is_primary=True)

    def test_two_units_may_each_have_a_primary(self, unit_photo_factory, unit_factory):
        unit_photo_factory(unit=unit_factory(), is_primary=True)
        unit_photo_factory(unit=unit_factory(), is_primary=True)

        assert UnitPhoto.all_objects.filter(is_primary=True).count() == 2

    def test_a_new_photo_starts_pending(self, unit_photo_factory):
        assert unit_photo_factory().processing_status == PhotoProcessingStatus.PENDING

    def test_ready_requires_every_variant(self, unit_photo_factory):
        """Ready with a missing key would serve a broken image."""
        photo = unit_photo_factory()
        photo.processing_status = PhotoProcessingStatus.READY
        photo.thumb_key = "properties/units/x/thumb.webp"

        with pytest.raises(IntegrityError), transaction.atomic():
            photo.save()

    def test_a_complete_variant_set_is_accepted(self, ready_unit_photo_factory):
        photo = ready_unit_photo_factory()

        assert photo.processing_status == PhotoProcessingStatus.READY
        assert photo.thumb_key and photo.medium_key and photo.large_key

    def test_failure_requires_a_reason(self, unit_photo_factory):
        photo = unit_photo_factory()
        photo.processing_status = PhotoProcessingStatus.FAILED

        with pytest.raises(IntegrityError), transaction.atomic():
            photo.save()

    def test_a_pending_photo_still_renders_the_original(self, unit_photo_factory):
        """A slow job degrades quality; it does not break the page (ADR-007)."""
        photo = unit_photo_factory()

        assert photo.display_key("thumb") == photo.original_key
        assert photo.display_key("medium") == photo.original_key

    def test_a_ready_photo_renders_its_variant(self, ready_unit_photo_factory):
        photo = ready_unit_photo_factory()

        assert photo.display_key("thumb") == photo.thumb_key
        assert photo.display_key("large") == photo.large_key

    def test_the_per_unit_cap_is_enforceable(self, unit_photo_factory, unit_factory):
        """R2 egress is free but storage is not zero (ADR-007)."""
        unit = unit_factory()
        assert not UnitPhoto.unit_is_full(unit.pk)

        for index in range(MAX_PHOTOS_PER_UNIT):
            unit_photo_factory(unit=unit, sort_order=index)

        assert UnitPhoto.unit_is_full(unit.pk)

    def test_the_upload_limits_are_defined(self):
        assert MAX_PHOTO_BYTES == 5 * 1024 * 1024
        assert MAX_PHOTOS_PER_UNIT == 12
        assert {"image/jpeg", "image/png", "image/webp"} == ALLOWED_PHOTO_CONTENT_TYPES

    def test_photos_scope_through_the_unit(
        self,
        unit_photo_factory,
        unit_factory,
        property_factory,
        campus_factory,
        campus_distance_factory,
        university,
        university_factory,
    ):
        prop = property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        photo = unit_photo_factory(unit=unit_factory(property=prop))

        assert photo in UnitPhoto.objects.for_tenant(university)
        assert photo not in UnitPhoto.objects.for_tenant(university_factory())


class TestDistanceNeedsCoordinates:
    def test_a_property_without_coordinates_cannot_join_a_campus(
        self, property_factory, campus_factory, campus_distance_factory, university
    ):
        """straight_line_km is NOT NULL and ADR-002 says it is always present.

        There is no honest value for an unpinned property, so the join is
        refused with a message naming the cause — rather than the database
        reporting a null column nobody knew was being written.
        """
        unpinned = property_factory(latitude=None, longitude=None)

        with pytest.raises(ValidationError) as caught:
            campus_distance_factory(
                property=unpinned,
                university=university,
                campus=campus_factory(university=university),
            )

        assert "property" in caught.value.message_dict
        assert "coordinates" in str(caught.value)

    def test_which_makes_an_unpinned_property_invisible_to_every_tenant(
        self, property_factory, university
    ):
        """A consequence worth naming: no join means no tenant sees it.

        The listing exists and its landlord can see it; nobody else can. The
        serializer has to require coordinates before publication.
        """
        unpinned = property_factory(latitude=None, longitude=None)

        assert unpinned not in Property.objects.for_tenant(university)


class TestPublicationGate:
    """A published property that no tenant can reach is a silent failure.

    It looks like low demand rather than a bug, which is why the gate names the
    cause instead of letting the database report a null column later.
    """

    def test_an_unpinned_property_cannot_be_published(
        self, draft_property_factory, university, campus_factory
    ):
        unpinned = draft_property_factory(latitude=None, longitude=None)

        with pytest.raises(PropertyNotPublishableError) as caught:
            publish(unpinned)

        assert "latitude" in caught.value.message_dict
        assert "campus" in str(caught.value).lower()

    def test_a_property_with_no_campus_cannot_be_published(self, draft_property_factory):
        orphan = draft_property_factory()

        with pytest.raises(PropertyNotPublishableError) as caught:
            publish(orphan)

        assert "campus_distances" in caught.value.message_dict

    def test_a_pinned_and_joined_property_publishes(
        self, draft_property_factory, university, campus_factory, campus_distance_factory
    ):
        prop = draft_property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )

        publish(prop)
        prop.refresh_from_db()

        assert prop.status == PropertyStatus.PUBLISHED
        assert prop.published_at is not None

    def test_publishing_makes_it_visible_to_the_tenant(
        self, draft_property_factory, university, campus_factory, campus_distance_factory
    ):
        """The whole point of the gate: published means reachable."""
        prop = draft_property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        publish(prop)

        assert prop in Property.objects.for_tenant(university)

    def test_publishing_twice_keeps_the_original_timestamp(
        self, draft_property_factory, university, campus_factory, campus_distance_factory
    ):
        prop = draft_property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        first = publish(prop).published_at

        assert publish(prop).published_at == first


class TestAnUploadIsJudgedByItsBytes:
    """`add_photo` must sniff, not ask.

    `Content-Type` on a multipart part is written by the client exactly as the
    filename is. The earlier implementation read it and refused on that basis,
    with a comment claiming it checked the contents -- so a PDF announcing
    itself as `image/jpeg` was stored under a `.jpg` key in the **public**
    bucket and served to whoever opened the listing.

    Found by seeding a file whose extension lies and noticing it was refused
    only because the fixture had told the truth about it.
    """

    def test_a_pdf_claiming_to_be_a_jpeg_is_refused(self, unit_factory):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from properties.services import add_photo

        lying = SimpleUploadedFile(
            "room.jpg", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 512, content_type="image/jpeg"
        )

        with pytest.raises(ValidationError):
            add_photo(unit=unit_factory(), upload=lying)

    def test_a_real_jpeg_mislabelled_as_a_pdf_is_accepted(self, unit_factory):
        """The other direction, and it matters as much.

        A browser that guesses the type wrong, or a client that sends
        `application/octet-stream`, is uploading a perfectly good photo. The
        bytes decide both ways.
        """
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from properties.services import add_photo

        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
        mislabelled = SimpleUploadedFile(
            "photo.dat", buffer.getvalue(), content_type="application/octet-stream"
        )

        photo = add_photo(unit=unit_factory(), upload=mislabelled)

        assert photo.original_key.endswith(".jpg")

    def test_a_listing_photo_is_stored_without_its_gps(self, unit_factory):
        """The original lives in the **public** bucket and is served whenever
        the variants are not ready -- which is always, briefly, and for ever
        for a photo that failed to resize.

        Found by seeding a real phone photo and reading back what was stored.
        """
        import piexif
        from django.core.files.storage import storages
        from django.core.files.uploadedfile import SimpleUploadedFile

        from config.management.commands._seed_images import generate
        from properties.services import add_photo

        raw, content_type, name = generate("phone_4mb", seed=1)
        assert piexif.load(raw)["GPS"], "the fixture should carry GPS to begin with"

        photo = add_photo(
            unit=unit_factory(),
            upload=SimpleUploadedFile(name, raw, content_type=content_type),
        )

        stored = storages["default"].open(photo.original_key).read()
        assert not piexif.load(stored)["GPS"]

    def test_the_recorded_size_matches_what_was_stored(self, unit_factory):
        """Stripping changes the bytes, so recording the upload's size would
        describe a file that no longer exists."""
        from django.core.files.storage import storages
        from django.core.files.uploadedfile import SimpleUploadedFile

        from config.management.commands._seed_images import generate
        from properties.services import add_photo

        raw, content_type, name = generate("modest_200kb", seed=2)
        photo = add_photo(
            unit=unit_factory(),
            upload=SimpleUploadedFile(name, raw, content_type=content_type),
        )

        assert photo.byte_size == len(storages["default"].open(photo.original_key).read())

    def test_a_truncated_image_is_refused_with_a_message(self, unit_factory):
        """A valid header and no body. The sniff cannot catch it -- the header
        really is a PNG's -- so the decode is what notices.

        Before this it was stored and failed in the resize job: a photo the
        landlord thought they had uploaded, sitting `failed` in a queue they
        cannot see, with the broken original being served. Found by seeding a
        deliberately truncated file, which then crashed with an unhandled
        OSError -- a 500 where a sentence belongs.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        from config.management.commands._seed_images import generate
        from properties.services import add_photo

        raw, content_type, name = generate("truncated", seed=1)

        with pytest.raises(ValidationError, match="incomplete"):
            add_photo(
                unit=unit_factory(),
                upload=SimpleUploadedFile(name, raw, content_type=content_type),
            )

    def test_the_file_is_still_readable_after_sniffing(self, unit_factory):
        """The sniff reads the head and must put the cursor back.

        Otherwise the stored object is the file minus its first 32 bytes --
        which is still a file, still has a key, and is corrupt in a way only a
        viewer would notice.
        """
        import io

        from django.core.files.storage import storages
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from properties.services import add_photo

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), "white").save(buffer, format="PNG")
        original = buffer.getvalue()

        photo = add_photo(
            unit=unit_factory(),
            upload=SimpleUploadedFile("room.png", original, content_type="image/png"),
        )

        stored = storages["default"].open(photo.original_key).read()
        assert stored == original


class TestHostileUploadsOnBothPaths:
    """The same three files against the photo path and the document path.

    They fail differently and both paths must survive all three:

    - a **PDF renamed `.jpg`** with `image/jpeg` on the part -- the case that
      showed `add_photo` was reading the client's own header;
    - an **SVG with a script tag** -- text, so it has no magic signature the
      allowlist recognises, and an allowlist is what makes that a refusal
      rather than an oversight;
    - a **truncated file** with a valid header, which no header check can
      catch because the header is genuine.
    """

    PDF_AS_JPEG = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 512
    SVG_WITH_SCRIPT = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        b"<script>alert(document.cookie)</script></svg>"
    )

    @staticmethod
    def truncated_png() -> bytes:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
        whole = buffer.getvalue()
        return whole[: len(whole) // 3]

    # -- the photo path (public bucket) ------------------------------------

    def upload_photo(self, unit, data: bytes, name: str, content_type: str):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from properties.services import add_photo

        return add_photo(
            unit=unit, upload=SimpleUploadedFile(name, data, content_type=content_type)
        )

    def test_photo_path_refuses_a_pdf_named_jpg(self, unit_factory):
        with pytest.raises(ValidationError):
            self.upload_photo(unit_factory(), self.PDF_AS_JPEG, "room.jpg", "image/jpeg")

    def test_photo_path_refuses_an_svg(self, unit_factory):
        """It never reaches the public bucket, which is what matters: an SVG
        served from a host is a script running on that host."""
        with pytest.raises(ValidationError):
            self.upload_photo(unit_factory(), self.SVG_WITH_SCRIPT, "logo.svg", "image/svg+xml")

    def test_photo_path_refuses_an_svg_claiming_to_be_a_png(self, unit_factory):
        """The extension and the header both lying at once."""
        with pytest.raises(ValidationError):
            self.upload_photo(unit_factory(), self.SVG_WITH_SCRIPT, "logo.png", "image/png")

    def test_photo_path_refuses_a_truncated_file(self, unit_factory):
        with pytest.raises(ValidationError, match="incomplete"):
            self.upload_photo(unit_factory(), self.truncated_png(), "cut.png", "image/png")

    def test_no_stored_photo_key_can_end_in_svg(self, unit_factory):
        """The extension comes from the **sniffed** type, so an SVG cannot be
        stored under a name the bucket would serve as `image/svg+xml`.

        That is what stands between the public media bucket and stored XSS
        today -- not a bucket policy. Asserted here so it stays true.
        """
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from properties.services import add_photo

        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")

        photo = add_photo(
            unit=unit_factory(),
            upload=SimpleUploadedFile("trythis.svg", buffer.getvalue(), content_type="image/png"),
        )

        assert photo.original_key.endswith(".png")

    # -- the document path (private bucket) --------------------------------

    def test_document_path_refuses_a_pdf_that_is_not_one(self, student_profile):
        """A PDF header is in the allowlist, so this asserts the opposite
        direction: bytes that are not any allowed type."""
        from accounts.documents import DocumentTypeNotAllowedError, submit_verification_document

        with pytest.raises(DocumentTypeNotAllowedError):
            submit_verification_document(student_profile, b"just some text, honestly")

    def test_document_path_refuses_an_svg(self, student_profile):
        from accounts.documents import DocumentTypeNotAllowedError, submit_verification_document

        with pytest.raises(DocumentTypeNotAllowedError):
            submit_verification_document(student_profile, self.SVG_WITH_SCRIPT)

    def test_document_path_refuses_a_truncated_image(self, student_profile):
        """A genuine PNG header and no body. The sniff passes and the decode
        is what notices -- the strip re-encodes, so an undecodable file cannot
        reach storage."""
        from accounts.documents import submit_verification_document

        with pytest.raises(Exception) as refusal:
            submit_verification_document(student_profile, self.truncated_png())

        assert (
            "truncated" in str(refusal.value).lower() or "incomplete" in str(refusal.value).lower()
        )

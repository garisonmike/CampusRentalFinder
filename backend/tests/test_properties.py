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
from django.db import IntegrityError, transaction
from django.utils import timezone

from config.tenancy import TenantScopeError
from properties.constants import PropertyStatus, PropertyType
from properties.distances import bounding_box, haversine_km, straight_line_km
from properties.models import Property, PropertyCampusDistance, Unit

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

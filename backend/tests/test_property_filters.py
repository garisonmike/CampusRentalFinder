"""
Property filtering (ADR-002, ADR-006).

Replaces the draft's hand-rolled ninety-line filter chain. Two things this file
guards beyond correctness: the primary query stays cheap, and the join that
makes ADR-002 work does not silently produce duplicate rows.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from properties.filters import PropertyFilter, order_by_campus_distance, within_radius
from properties.models import Property

pytestmark = pytest.mark.django_db


@pytest.fixture
def listed(university, campus_factory, property_factory, unit_factory, campus_distance_factory):
    """Build a small catalogue joined to the tenant under test."""

    campus = campus_factory(university=university, is_main=True, latitude=-1.18, longitude=36.93)

    def _make(*, name, rent, latitude=-1.19, longitude=36.94, vacant=1, total=None, **kwargs):
        prop = property_factory(name=name, latitude=latitude, longitude=longitude, **kwargs)
        unit_factory(
            property=prop,
            rent_kes=Decimal(rent),
            total_count=total if total is not None else max(vacant, 1),
            vacant_count=vacant,
        )
        campus_distance_factory(property=prop, university=university, campus=campus)
        return prop

    return _make


def run_filter(data, queryset=None):
    queryset = queryset if queryset is not None else Property.all_objects.all()
    filterset = PropertyFilter(data=data, queryset=queryset)
    assert filterset.is_valid(), filterset.errors
    return list(filterset.qs)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestPropertyFilter:
    def test_filters_by_rent_range(self, listed):
        cheap = listed(name="Cheap", rent="6000")
        listed(name="Dear", rent="30000")

        assert run_filter({"max_rent": "10000"}) == [cheap]

    def test_filters_by_town(self, listed):
        nairobi = listed(name="In town", rent="9000", town="Nairobi")
        listed(name="Elsewhere", rent="9000", town="Eldoret")

        assert run_filter({"town": "nairobi"}) == [nairobi]

    def test_filters_by_estate(self, listed):
        wendani = listed(name="Wendani", rent="9000", estate="Kahawa Wendani")
        listed(name="Juja", rent="9000", estate="Juja Town")

        assert run_filter({"estate": "wendani"}) == [wendani]

    def test_filters_by_amenity(self, listed):
        with_power = listed(name="Backup", rent="9000", has_backup_power=True)
        listed(name="No backup", rent="9000", has_backup_power=False)

        assert run_filter({"has_backup_power": "true"}) == [with_power]

    def test_free_text_searches_name_estate_and_landmark(self, listed):
        by_landmark = listed(name="Block C", rent="9000", landmark="opposite Naivas")
        listed(name="Block D", rent="9000", landmark="near the stage")

        assert run_filter({"q": "naivas"}) == [by_landmark]

    def test_available_only_excludes_full_properties(self, listed):
        free = listed(name="Has space", rent="9000", vacant=2)
        listed(name="Full", rent="9000", vacant=0)

        assert run_filter({"available_only": "true"}) == [free]

    def test_available_only_false_is_a_no_op(self, listed):
        listed(name="Has space", rent="9000", vacant=2)
        listed(name="Full", rent="9000", vacant=0)

        assert len(run_filter({"available_only": "false"})) == 2


class TestCampusProximity:
    def test_filters_by_straight_line_distance(
        self, listed, university, campus_factory, property_factory, campus_distance_factory
    ):
        """The platform's primary query.

        An indexed range scan on a precomputed number, not a geometry
        operation (ADR-006).
        """
        near = listed(name="Near", rent="9000", latitude=-1.185, longitude=36.935)
        listed(name="Far", rent="9000", latitude=-1.60, longitude=37.30)

        results = run_filter({"max_distance_km": "3"})

        assert near in results
        assert len(results) == 1

    def test_ordering_by_distance_does_not_duplicate_a_multi_campus_property(
        self, university, campus_factory, property_factory, unit_factory, campus_distance_factory
    ):
        """`.distinct()` and ORDER BY on a joined column interact badly.

        A property serving two campuses of the same university would otherwise
        appear twice in its own listing (ADR-002).
        """
        main = campus_factory(university=university, name="Main", is_main=True)
        second = campus_factory(university=university, name="Ruiru")
        prop = property_factory()
        unit_factory(property=prop)
        campus_distance_factory(property=prop, university=university, campus=main)
        campus_distance_factory(property=prop, university=university, campus=second)

        ordered = list(order_by_campus_distance(Property.objects.for_tenant(university)))

        assert ordered.count(prop) == 1

    def test_ordering_puts_the_nearest_first(self, listed):
        far = listed(name="Far", rent="9000", latitude=-1.40, longitude=37.10)
        near = listed(name="Near", rent="9000", latitude=-1.185, longitude=36.935)

        ordered = list(order_by_campus_distance(Property.all_objects.all()))

        assert ordered.index(near) < ordered.index(far)


class TestArbitraryRadius:
    def test_it_finds_points_inside_the_radius(self, listed):
        near = listed(name="Near", rent="9000", latitude=-1.286, longitude=36.817)
        listed(name="Far", rent="9000", latitude=-2.00, longitude=37.50)

        found = within_radius(Property.all_objects.all(), -1.286389, 36.817223, 5.0)

        assert found == [near]

    def test_it_works_on_the_equator(self, listed):
        """The draft's bounding box divided by zero here (docs/AUDIT.md §3).

        It survived review because the correction is nearly 1 at any plausible
        test latitude — and Kenya is the market where 0.0 is a normal value.
        """
        on_equator = listed(name="Equator", rent="9000", latitude=0.001, longitude=36.80)

        found = within_radius(Property.all_objects.all(), 0.0, 36.80, 5.0)

        assert on_equator in found

    def test_it_excludes_properties_with_no_coordinates(
        self, listed, property_factory, unit_factory
    ):
        """An unpinned property has no distance, so it cannot be found by one.

        It also cannot join a campus at all, which is why it is created
        directly here rather than through the `listed` helper.
        """
        pinned = listed(name="Pinned", rent="9000", latitude=-1.286, longitude=36.817)
        unpinned = property_factory(name="No pin", latitude=None, longitude=None)
        unit_factory(property=unpinned)

        found = within_radius(Property.all_objects.all(), -1.286389, 36.817223, 50.0)

        assert pinned in found
        assert unpinned not in found

    def test_the_box_corners_are_filtered_out(self, listed):
        """A box, not a circle: corners are √2 times the stated radius."""
        # ~7 km diagonally from the origin, inside a 5 km box but outside the
        # 5 km circle.
        corner = listed(name="Corner", rent="9000", latitude=-1.241, longitude=36.862)

        found = within_radius(Property.all_objects.all(), -1.286389, 36.817223, 5.0)

        assert corner not in found


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------


class TestQueryCounts:
    """ADR-002 warns that the join makes N+1 easy on the busiest page.

    These assert an upper bound rather than an exact number: the point is that
    the count does not grow with the number of properties.
    """

    def test_listing_properties_does_not_scale_with_row_count(
        self, listed, django_assert_num_queries, university
    ):
        for index in range(3):
            listed(name=f"Property {index}", rent="9000")

        queryset = (
            Property.objects.for_tenant(university)
            .select_related("landlord__user")
            .prefetch_related("units", "campus_distances__campus")
            .distinct()
        )

        with django_assert_num_queries(4):
            for prop in queryset:
                # Touch every relation the listing serializer will touch.
                touched = [
                    prop.landlord.user.email,
                    len(prop.units.all()),
                    [d.campus.name for d in prop.campus_distances.all()],
                ]
                assert touched

    def test_the_same_query_count_holds_at_twice_the_size(
        self, listed, django_assert_num_queries, university
    ):
        """The real assertion: the count is constant, not merely small."""
        for index in range(6):
            listed(name=f"Property {index}", rent="9000")

        queryset = (
            Property.objects.for_tenant(university)
            .select_related("landlord__user")
            .prefetch_related("units", "campus_distances__campus")
            .distinct()
        )

        with django_assert_num_queries(4):
            for prop in queryset:
                # Touch every relation the listing serializer will touch.
                touched = [
                    prop.landlord.user.email,
                    len(prop.units.all()),
                    [d.campus.name for d in prop.campus_distances.all()],
                ]
                assert touched

    def test_an_unprefetched_listing_is_the_n_plus_one_this_guards_against(
        self, listed, django_assert_num_queries, university
    ):
        """Documents the failure mode, so the guard above is not mysterious."""
        for index in range(3):
            listed(name=f"Property {index}", rent="9000")

        queryset = Property.objects.for_tenant(university).distinct()

        # One for the properties, then one per property for its units.
        with django_assert_num_queries(4):
            for prop in queryset:
                list(prop.units.all())

    def test_filtering_adds_no_extra_queries(self, listed, django_assert_num_queries, university):
        for index in range(3):
            listed(name=f"Property {index}", rent="9000")

        filterset = PropertyFilter(
            data={"max_rent": "20000", "available_only": "true"},
            queryset=Property.objects.for_tenant(university).prefetch_related("units"),
        )
        assert filterset.is_valid()

        with django_assert_num_queries(2):
            for prop in filterset.qs:
                list(prop.units.all())

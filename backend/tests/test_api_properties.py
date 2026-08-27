"""
The public property and unit endpoints (ADR-001, ADR-002).

Two properties are load-bearing here and neither is visible from a response
body:

**Tenant scoping.** A property is visible because it is joined to a campus of
the requesting university. A property with no such join is not "far away" — it
does not exist for that tenant.

**Query counts.** A listing that issues one query per row renders correctly in
a test with three fixtures and falls over at forty, which is exactly the size
where nobody notices until it is live.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from properties.constants import PropertyStatus

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/properties/"


@pytest.fixture
def host(university):
    """Requests arrive on the tenant's subdomain, as they do in production."""
    return f"{university.subdomain}.example.co.ke"


@pytest.fixture
def listing(university, campus_factory, property_factory, unit_factory, campus_distance_factory):
    """One published property joined to the tenant, with two units."""

    def build(**kwargs):
        campus = kwargs.pop("campus", None) or campus_factory(university=university, is_main=True)
        prop = property_factory(status=PropertyStatus.PUBLISHED, **kwargs)
        campus_distance_factory(property=prop, university=university, campus=campus)
        unit_factory(property=prop, label="Bedsitter", rent_kes=7000)
        unit_factory(property=prop, label="One-bedroom", rent_kes=12000)
        return prop

    return build


def get(api_client, url, host, **params):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return api_client.get(url, params, HTTP_HOST=host)


# ---------------------------------------------------------------------------
# Reading is public
# ---------------------------------------------------------------------------


class TestListingsArePublic:
    def test_an_anonymous_visitor_can_search(self, api_client, listing, host):
        """Browsing listings is the product. Gating it behind a login is not a
        verification policy, it is an outage (ADR-003)."""
        listing()

        response = get(api_client, LIST_URL, host)

        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_the_envelope_is_the_standard_one(self, api_client, listing, host):
        listing()

        body = get(api_client, LIST_URL, host).json()

        assert set(body) >= {"count", "page", "page_size", "total_pages", "results"}

    def test_an_unpublished_property_is_invisible(
        self,
        api_client,
        host,
        university,
        campus_factory,
        draft_property_factory,
        campus_distance_factory,
        unit_factory,
    ):
        """A draft leaks a landlord's unfinished work, and drafts may have no
        coordinates -- they were never meant to be findable."""
        campus = campus_factory(university=university, is_main=True)
        prop = draft_property_factory()
        campus_distance_factory(property=prop, university=university, campus=campus)
        unit_factory(property=prop)

        assert get(api_client, LIST_URL, host).json()["count"] == 0

    def test_a_property_detail_is_public(self, api_client, listing, host):
        prop = listing()

        response = get(api_client, f"{LIST_URL}{prop.slug}/", host)

        assert response.status_code == 200
        assert response.json()["name"] == prop.name

    def test_a_unit_detail_is_public(self, api_client, listing, host):
        prop = listing()
        unit = prop.units.first()

        response = get(api_client, f"{LIST_URL}units/{unit.pk}/", host)

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tenant scoping
# ---------------------------------------------------------------------------


class TestTenantScoping:
    def test_another_universitys_property_is_not_listed(
        self,
        api_client,
        host,
        listing,
        university_factory,
        campus_factory,
        property_factory,
        campus_distance_factory,
        unit_factory,
    ):
        """Not "far away" -- it does not exist for this tenant (ADR-002)."""
        listing()

        other = university_factory()
        other_prop = property_factory(status=PropertyStatus.PUBLISHED)
        campus_distance_factory(
            property=other_prop,
            university=other,
            campus=campus_factory(university=other),
        )
        unit_factory(property=other_prop)

        results = get(api_client, LIST_URL, host).json()["results"]

        assert len(results) == 1
        assert other_prop.slug not in {row["slug"] for row in results}

    def test_another_universitys_property_404s_by_slug(
        self,
        api_client,
        host,
        university_factory,
        campus_factory,
        property_factory,
        campus_distance_factory,
    ):
        """Scoping the list is not enough if the detail route is guessable --
        and a slug is very guessable."""
        other = university_factory()
        other_prop = property_factory(status=PropertyStatus.PUBLISHED)
        campus_distance_factory(
            property=other_prop, university=other, campus=campus_factory(university=other)
        )

        response = get(api_client, f"{LIST_URL}{other_prop.slug}/", host)

        assert response.status_code == 404

    def test_a_property_with_no_campus_join_is_invisible(
        self, api_client, host, property_factory, unit_factory
    ):
        """The join is what makes a listing visible at all."""
        prop = property_factory(status=PropertyStatus.PUBLISHED)
        unit_factory(property=prop)

        assert get(api_client, LIST_URL, host).json()["count"] == 0

    def test_an_unknown_host_gets_a_404_not_a_400(self, api_client, listing):
        """A 400 naming the problem would advertise which subdomains exist."""
        listing()

        response = get(api_client, LIST_URL, "nosuchschool.example.co.ke")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------


class TestQueryCounts:
    """The assertion is that cost does not grow with the number of rows.

    Written as "N properties cost the same as one" rather than as a fixed
    number, because a fixed number is a test that fails on every unrelated
    optimisation and gets bumped without being read.
    """

    def test_the_list_does_not_scale_with_rows(
        self, api_client, listing, host, campus_factory, university
    ):
        campus = campus_factory(university=university, is_main=True)
        listing(campus=campus)

        with CaptureQueriesContext(connection) as captured:
            get(api_client, LIST_URL, host)
        one_row = len(captured.captured_queries)

        for _ in range(6):
            listing(campus=campus)

        with CaptureQueriesContext(connection) as captured:
            response = get(api_client, LIST_URL, host)
        seven_rows = len(captured.captured_queries)

        assert response.json()["count"] == 7
        assert seven_rows == one_row, (
            f"{one_row} queries for 1 property, {seven_rows} for 7 -- the list "
            f"is issuing per-row queries and will fall over at page size 20."
        )

    def test_ordering_by_distance_does_not_add_per_row_queries(
        self, api_client, listing, host, campus_factory, university
    ):
        campus = campus_factory(university=university, is_main=True)
        for _ in range(5):
            listing(campus=campus)

        with CaptureQueriesContext(connection) as baseline:
            get(api_client, LIST_URL, host)
        with CaptureQueriesContext(connection) as ordered:
            response = get(api_client, LIST_URL, host, ordering="distance")

        assert response.status_code == 200
        assert len(ordered.captured_queries) <= len(baseline.captured_queries) + 1

    def test_the_detail_does_not_scale_with_units(self, api_client, listing, host, unit_factory):
        prop = listing()

        with CaptureQueriesContext(connection) as captured:
            get(api_client, f"{LIST_URL}{prop.slug}/", host)
        two_units = len(captured.captured_queries)

        for index in range(6):
            unit_factory(property=prop, label=f"Extra {index}")

        with CaptureQueriesContext(connection) as captured:
            response = get(api_client, f"{LIST_URL}{prop.slug}/", host)

        assert len(response.json()["units"]) == 8
        assert len(captured.captured_queries) == two_units


# ---------------------------------------------------------------------------
# The contract notes
# ---------------------------------------------------------------------------


class TestDistancesAreLabelled:
    def test_the_straight_line_is_present_and_named(self, api_client, listing, host):
        prop = listing()

        distances = get(api_client, f"{LIST_URL}{prop.slug}/", host).json()["campus_distances"]

        assert distances
        assert "straight_line_km" in distances[0]

    def test_walking_minutes_is_null_when_unrouted(self, api_client, listing, host):
        """Null is the honest answer: no route, quota exhausted, or provider
        down. A fabricated walking time erodes exactly the trust the platform
        sells (ADR-002)."""
        prop = listing()

        distances = get(api_client, f"{LIST_URL}{prop.slug}/", host).json()["campus_distances"]

        assert distances[0]["walking_minutes"] is None
        assert distances[0]["walking_distance_km"] is None

    def test_the_straight_line_is_never_substituted_for_the_walk(self, api_client, listing, host):
        prop = listing()

        distance = get(api_client, f"{LIST_URL}{prop.slug}/", host).json()["campus_distances"][0]

        assert distance["straight_line_km"] is not None
        assert distance["walking_distance_km"] is None

    def test_a_live_listing_names_its_landlord(self, api_client, listing, host):
        prop = listing()

        body = get(api_client, f"{LIST_URL}{prop.slug}/", host).json()

        assert body["landlord_name"] == prop.landlord.user.get_full_name()


class TestAnErasedLandlordsListings:
    """ADR-008 §2.2: properties do not cascade, they go dormant.

    The records survive because other people's tenancies and reviews depend on
    them. What stops is *new* business: unlisted, unsearchable, no new
    applications or claims.
    """

    def test_the_listing_leaves_search(self, api_client, listing, host):
        from accounts.privacy import erase_landlord_data

        prop = listing()
        assert get(api_client, LIST_URL, host).json()["count"] == 1

        erase_landlord_data(prop.landlord.user)

        assert get(api_client, LIST_URL, host).json()["count"] == 0

    def test_the_detail_is_no_longer_public(self, api_client, listing, host):
        """Unsearchable but reachable by URL would be a distinction without a
        difference -- slugs are guessable and get shared."""
        from accounts.privacy import erase_landlord_data

        prop = listing()
        erase_landlord_data(prop.landlord.user)

        assert get(api_client, f"{LIST_URL}{prop.slug}/", host).status_code == 404

    def test_the_property_row_survives(self, listing):
        """Deleting it would erase other people's tenancy history and their
        reviews, which is not the landlord's right to exercise."""
        from accounts.privacy import erase_landlord_data
        from properties.models import Property

        prop = listing()
        erase_landlord_data(prop.landlord.user)

        assert Property.all_objects.filter(pk=prop.pk).exists()

    def test_the_owner_reads_as_a_tombstone(self, listing):
        """Still attributed, just not to a named person."""
        from accounts.privacy import display_name_for, erase_landlord_data

        prop = listing()
        erase_landlord_data(prop.landlord.user)
        prop.landlord.user.refresh_from_db()

        assert display_name_for(prop.landlord.user) == "Former landlord"


class TestFiltering:
    def test_it_filters_by_rent(self, api_client, listing, host, campus_factory, university):
        campus = campus_factory(university=university, is_main=True)
        listing(campus=campus)

        assert get(api_client, LIST_URL, host, max_rent=5000).json()["count"] == 0
        assert get(api_client, LIST_URL, host, max_rent=8000).json()["count"] == 1

    def test_it_filters_by_amenity(self, api_client, listing, host, campus_factory, university):
        campus = campus_factory(university=university, is_main=True)
        listing(campus=campus, has_borehole=True)

        assert get(api_client, LIST_URL, host, has_borehole=True).json()["count"] == 1
        assert get(api_client, LIST_URL, host, has_backup_power=True).json()["count"] == 0

    def test_the_page_size_is_capped(self, api_client, listing, host):
        """`page_size` is caller-supplied, and an uncapped one is a
        one-parameter denial of service on any endpoint with a join."""
        listing()

        body = get(api_client, LIST_URL, host, page_size=100000).json()

        assert body["page_size"] <= 100

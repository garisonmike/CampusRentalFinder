"""
Campus joins, and the invisibility they prevent (ADR-002).

A `PropertyCampusDistance` row is what makes a property visible to a
university. `publish()` gates on having one, which covers the property
arriving after the campus. Nothing covered the campus arriving after the
property: `route_stale_distances` walks rows that exist, so a campus created
later left every nearby published listing invisible to it permanently, with
nothing erroring and nothing queued.

That is the same silent-invisibility failure the publish gate exists to
prevent, arriving through a different door.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import override_settings

from properties.constants import PropertyStatus
from properties.models import PropertyCampusDistance
from properties.services import backfill_campus_joins, properties_missing_a_join_to

pytestmark = pytest.mark.django_db

# Kenyatta University main campus, and points measured from it.
CAMPUS = (-1.1806, 36.9300)
NEARBY = (-1.1850, 36.9350)  # about 700 m
FAR = (-1.5000, 37.4000)  # about 60 km


@pytest.fixture
def host(university):
    return f"{university.subdomain}.example.co.ke"


class TestBackfillingACampusJoinsWhatIsAlreadyThere:
    """Driven by the operator command, not by a signal on campus save.

    Automatic repair was the obvious move and the wrong one: a join makes a
    property visible to a university's students, so creating one on every
    nearby campus save turns tenant visibility into a function of geography
    that nobody decided. Two universities 12 km apart would begin sharing
    every listing the moment a campus row was saved.
    """

    def test_a_published_property_in_range_is_joined(
        self, property_factory, campus_factory, university
    ):
        prop = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1]
        )

        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        assert PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).exists()

    def test_the_row_carries_a_computed_distance(
        self, property_factory, campus_factory, university
    ):
        """`straight_line_km` is haversine on save and NOT NULL. It is
        computed, not guessed, which is what makes it safe to write here."""
        prop = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1]
        )
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        row = PropertyCampusDistance.all_objects.get(property=prop, campus=campus)

        assert row.straight_line_km > 0
        assert row.straight_line_km < 2

    def test_walking_minutes_is_left_null(self, property_factory, campus_factory, university):
        """**Only the routing job may fill it** (ADR-002). A walking time the
        platform invented erodes exactly the trust the platform sells, and a
        straight line promoted into a walk is the specific way that happens."""
        prop = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1]
        )
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        row = PropertyCampusDistance.all_objects.get(property=prop, campus=campus)

        assert row.walking_minutes is None
        assert row.walking_distance_km is None
        assert row.routed_at is None

    def test_the_routing_sweep_then_picks_it_up(self, property_factory, campus_factory, university):
        """A null `routed_at` sorts first, so the backfilled row is at the
        front of the queue rather than behind everything already routed."""
        from properties.jobs import route_stale_distances

        property_factory(status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1])
        backfill_campus_joins(
            campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        )

        assert route_stale_distances() >= 1

    def test_a_property_out_of_range_is_not_joined(
        self, property_factory, campus_factory, university
    ):
        prop = property_factory(status=PropertyStatus.PUBLISHED, latitude=FAR[0], longitude=FAR[1])
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        assert not PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).exists()

    def test_a_draft_is_not_joined(self, property_factory, campus_factory, university):
        """Publishing runs the gate. A draft joined in advance would let a
        property go live without that gate ever having been satisfied."""
        prop = property_factory(
            status=PropertyStatus.DRAFT, latitude=NEARBY[0], longitude=NEARBY[1]
        )
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        assert not PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).exists()

    def test_an_unpinned_property_is_not_joined(self, property_factory, campus_factory, university):
        prop = property_factory(status=PropertyStatus.PUBLISHED, latitude=None, longitude=None)
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        assert not PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).exists()

    def test_moving_a_campus_joins_its_new_neighbours(
        self, property_factory, campus_factory, university
    ):
        """A campus pinned to the wrong side of town and then corrected has a
        different set of neighbours. Without this the properties that entered
        range would never be joined."""
        prop = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1]
        )
        campus = campus_factory(university=university, latitude=FAR[0], longitude=FAR[1])
        assert not PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).exists()

        campus.latitude, campus.longitude = CAMPUS
        campus.save()
        backfill_campus_joins(campus)

        assert PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).exists()

    def test_it_does_not_duplicate_an_existing_join(
        self, property_factory, campus_factory, campus_distance_factory, university
    ):
        prop = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1]
        )
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        backfill_campus_joins(campus)
        backfill_campus_joins(campus)

        assert PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).count() == 1


class TestTheAbsenceReconciler:
    """Absence is its own number with its own alert (`docs/OPERATIONS.md`).

    `straight_line_km` is NOT NULL and computed on save, so a row always has a
    distance. A property with **no row** has never been joined -- a different
    fact from a row whose routing is stale, and one that would disappear
    inside a routing backlog if the two were counted together.
    """

    def test_it_names_a_property_with_no_row(self, property_factory, campus_factory, university):
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)
        prop = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1]
        )
        PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).delete()

        assert properties_missing_a_join_to(campus) == [prop.pk]

    def test_a_joined_property_is_not_counted(self, property_factory, campus_factory, university):
        # Property first, then campus, so the campus-side backfill is what
        # joins them. The other order is `publish()`'s job, covered below.
        property_factory(status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1])
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        assert properties_missing_a_join_to(campus) == []

    def test_a_stale_row_is_not_reported_as_missing(
        self, property_factory, campus_factory, university
    ):
        """The distinction the whole reconciler exists to keep. A row that has
        never been routed is stale; the sweep already handles it."""
        property_factory(status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1])
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)

        assert PropertyCampusDistance.all_objects.filter(
            campus=campus, walking_minutes=None
        ).exists()
        assert properties_missing_a_join_to(campus) == []

    def test_the_radius_comes_from_settings(self, property_factory, campus_factory, university):
        """Declared once. A number written in two places is the shape
        `docs/OPERATIONS.md` collects."""
        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)
        prop = property_factory(status=PropertyStatus.PUBLISHED, latitude=FAR[0], longitude=FAR[1])

        assert prop.pk not in properties_missing_a_join_to(campus)

        with override_settings(CAMPUS_JOIN_RADIUS_KM=200.0):
            assert prop.pk in properties_missing_a_join_to(campus)

    def test_the_default_radius_is_the_one_documented(self):
        assert settings.CAMPUS_JOIN_RADIUS_KM == 15.0


class TestPublishingJoinsTheProperty:
    """The direction that was missing entirely.

    `publish()` refuses a property with no campus join, and **nothing in the
    product created one**. The seed wrote them directly and the tests built
    them by hand, so a landlord using the write surface could pin a property,
    satisfy every other rule, and be refused for ever by a gate that no
    available action could satisfy.
    """

    def test_publishing_joins_every_campus_in_range(
        self, property_factory, campus_factory, university, landlord_profile
    ):
        from properties.services import publish

        campus = campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        backfill_campus_joins(campus)
        prop = property_factory(
            landlord=landlord_profile,
            status=PropertyStatus.DRAFT,
            latitude=NEARBY[0],
            longitude=NEARBY[1],
        )
        PropertyCampusDistance.all_objects.filter(property=prop).delete()

        publish(prop)

        assert PropertyCampusDistance.all_objects.filter(property=prop, campus=campus).exists()
        assert prop.status == PropertyStatus.PUBLISHED

    def test_a_property_near_no_campus_is_still_refused(
        self, property_factory, campus_factory, university, landlord_profile
    ):
        """The gate keeps its meaning. It now refuses only a property that is
        genuinely near nothing -- a real refusal with a real explanation,
        rather than an unsatisfiable one."""
        from properties.services import PropertyNotPublishableError, publish

        campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        prop = property_factory(
            landlord=landlord_profile,
            status=PropertyStatus.DRAFT,
            latitude=FAR[0],
            longitude=FAR[1],
        )
        PropertyCampusDistance.all_objects.filter(property=prop).delete()

        with pytest.raises(PropertyNotPublishableError) as refusal:
            publish(prop)

        assert "campus" in str(refusal.value).lower()

    def test_the_refusal_names_the_radius(
        self, property_factory, campus_factory, university, landlord_profile
    ):
        from properties.services import PropertyNotPublishableError, publish

        campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        prop = property_factory(
            landlord=landlord_profile,
            status=PropertyStatus.DRAFT,
            latitude=FAR[0],
            longitude=FAR[1],
        )
        PropertyCampusDistance.all_objects.filter(property=prop).delete()

        with pytest.raises(PropertyNotPublishableError) as refusal:
            publish(prop)

        assert "15" in str(refusal.value)


class TestTheListingBecomesVisible:
    """The point of all of it: the property appears in that campus's results."""

    def test_a_backfilled_property_appears_in_the_listing(
        self, api_client, property_factory, campus_factory, university, unit_factory, host
    ):
        prop = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=NEARBY[0], longitude=NEARBY[1]
        )
        unit_factory(property=prop)
        backfill_campus_joins(
            campus_factory(university=university, latitude=CAMPUS[0], longitude=CAMPUS[1])
        )

        with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
            response = api_client.get("/api/v1/properties/", HTTP_HOST=host)

        assert response.status_code == 200
        assert prop.slug in [row["slug"] for row in response.data["results"]]


class TestWhetherTheTwentyFiveWereArtefact:
    """Auto-joining on campus save turned 25 tenant-scoping assertions red.

    Last round I called that an artefact of `CampusFactory` placing every
    campus at one coordinate, and reasoned it rather than testing it -- which
    is the shape `docs/OPERATIONS.md` calls a summary asserting a finding the
    evidence does not support. This is the evidence.

    The claim under test: the geography is correct, and the red assertions
    came from fixtures placing different universities on top of each other
    rather than from the join being wrong.
    """

    def test_a_distant_campus_does_not_join_another_universitys_property(
        self, property_factory, campus_factory, university_factory
    ):
        """Two universities 60 km apart, which is what they actually are.

        Kenyatta's Kahawa campus and JKUAT's Juja campus are about 13 km
        apart in reality -- close enough that a 15 km join radius really would
        overlap them, which is a finding of its own and the reason
        `CAMPUS_JOIN_RADIUS_KM` becomes per-campus. At 60 km there is no
        ambiguity, and this asserts the mechanism rather than the tuning.
        """
        near = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=CAMPUS[0], longitude=CAMPUS[1]
        )
        far_campus = campus_factory(
            university=university_factory(),
            latitude=CAMPUS[0] - 0.55,
            longitude=CAMPUS[1],
        )

        backfill_campus_joins(far_campus)

        assert not PropertyCampusDistance.all_objects.filter(
            property=near, campus=far_campus
        ).exists()

    def test_a_nearby_campus_of_another_university_does_join(
        self, property_factory, campus_factory, university_factory
    ):
        """The other half, and the reason auto-joining stayed an operator
        command: at 2 km the join is geographically correct and still changes
        which university's students can see the listing. That is a decision,
        not a repair.
        """
        near = property_factory(
            status=PropertyStatus.PUBLISHED, latitude=CAMPUS[0], longitude=CAMPUS[1]
        )
        other = campus_factory(
            university=university_factory(),
            latitude=CAMPUS[0] + 0.018,
            longitude=CAMPUS[1],
        )

        backfill_campus_joins(other)

        assert PropertyCampusDistance.all_objects.filter(property=near, campus=other).exists()

    def test_the_fixtures_no_longer_collapse_the_distance(
        self, property_factory, campus_factory, campus_distance_factory, university
    ):
        """Every campus and every property shared one coordinate, so every
        distance the suite could compute was 0.0 km.

        A fixture that collapses the dimension under test makes every
        assertion along it vacuous, and it passes.
        """
        campus = campus_factory(university=university)
        first = property_factory(status=PropertyStatus.PUBLISHED)
        second = property_factory(status=PropertyStatus.PUBLISHED)

        distances = {
            campus_distance_factory(
                property=prop, university=university, campus=campus
            ).straight_line_km
            for prop in (first, second)
        }

        assert 0 not in distances, "a property is sitting exactly on a campus"
        assert len(distances) == 2, "two properties produced one distance"

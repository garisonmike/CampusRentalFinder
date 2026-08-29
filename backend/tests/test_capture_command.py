"""The seeded-capture command chooses for coverage, not for looks.

`frontend/src/test/seeded-platform.json` is the only frontend fixture nobody
wrote, and its value is entirely in the shapes it carries that a hand-written
one would not: a unit whose vacancy nobody has ever stated, a campus join the
routing job has not reached. A capture that quietly selected a tidy property
would render perfectly and prove nothing -- the fixture failure this file
exists to avoid, rebuilt by the tool meant to avoid it.

So the selection rule is asserted here rather than trusted.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management.base import CommandError
from django.utils import timezone

from config.management.commands.capture_seeded_platform import Command


@pytest.fixture
def kyu(university_factory):
    return university_factory(subdomain="kyu", slug="kenyatta")


@pytest.fixture
def campus(kyu, campus_factory):
    return campus_factory(university=kyu)


def published(property_factory, **kwargs):
    return property_factory(status="published", published_at=timezone.now(), **kwargs)


class TestItRefusesAHappyPathCapture:
    def test_no_published_property_at_all(self, kyu, campus):
        with pytest.raises(CommandError, match="no published property"):
            Command().capture()

    def test_a_property_missing_the_awkward_branches_is_refused(
        self, kyu, campus, property_factory, unit_factory, campus_distance_factory
    ):
        """Complete data, and therefore useless as a capture.

        Every branch these frontend tests exist for is a null one. A property
        with a stated vacancy, a routed campus join and no reviews renders the
        happy path only, and the four tests that assert the awkward wording
        would pass against a page that never displays it.
        """
        prop = published(property_factory)
        unit_factory(property=prop, vacant_count_updated_at=timezone.now())
        campus_distance_factory(
            property=prop,
            university=kyu,
            campus=campus,
            # All three move together or none do: a walking time with no
            # distance and no timestamp is a number nobody can account for.
            walking_minutes=14,
            walking_distance_km=1.1,
            routed_at=timezone.now(),
        )

        with pytest.raises(CommandError, match="all three branches"):
            Command().capture()

    def test_the_message_names_which_branch_is_missing(
        self, kyu, campus, property_factory, unit_factory, campus_distance_factory
    ):
        """A refusal that does not say what is absent sends somebody reading
        the command instead of the seed."""
        prop = published(property_factory)
        unit_factory(property=prop, vacant_count_updated_at=None)
        campus_distance_factory(property=prop, university=kyu, campus=campus, walking_minutes=None)

        with pytest.raises(CommandError) as raised:
            Command().capture()

        message = str(raised.value)
        assert "never-stated vacancy: 1" in message
        assert "missing walking route: 1" in message
        assert "reviews: 0" in message


class TestItPrefersTheAwkwardProperty:
    def test_the_incomplete_property_wins_over_the_well_reviewed_one(
        self,
        kyu,
        campus,
        property_factory,
        unit_factory,
        campus_distance_factory,
        tenancy_factory,
        review_factory,
    ):
        """The rule that broke four tests when it was "most reviews".

        Selecting on review count selects for a *complete* property, because
        a property people have lived in and reviewed is one whose landlord has
        filled everything in. The tidy one here has more reviews and must
        still lose.
        """
        tidy = published(property_factory, name="Tidy Court")
        tidy_unit = unit_factory(property=tidy, vacant_count_updated_at=timezone.now())
        campus_distance_factory(
            property=tidy,
            university=kyu,
            campus=campus,
            # All three move together or none do: a walking time with no
            # distance and no timestamp is a number nobody can account for.
            walking_minutes=9,
            walking_distance_km=0.7,
            routed_at=timezone.now(),
        )

        awkward = published(property_factory, name="Awkward Court")
        awkward_unit = unit_factory(property=awkward, vacant_count_updated_at=None)
        campus_distance_factory(
            property=awkward, university=kyu, campus=campus, walking_minutes=None
        )

        start = dt.date.today() - dt.timedelta(days=400)
        for unit, count in ((tidy_unit, 3), (awkward_unit, 1)):
            for _ in range(count):
                tenancy = tenancy_factory(
                    unit=unit, start_date=start, end_date=start + dt.timedelta(days=200)
                )
                review_factory(tenancy=tenancy)

        captured = Command().capture()

        assert captured["detail"]["name"] == "Awkward Court"
        assert captured["detail"]["slug"] == awkward.slug

    def test_the_capture_holds_the_whole_tenant_config(
        self,
        kyu,
        campus,
        property_factory,
        unit_factory,
        campus_distance_factory,
        tenancy_factory,
        review_factory,
    ):
        """Not the three colours somebody trimmed it down to.

        The previous file's `themes` held only `primary`/`secondary`/`accent`,
        which means it had been edited by hand after capture -- and a capture
        somebody edited is a hand-written fixture wearing a capture's name.
        That editing is where a stale `stay_months` sat unnoticed for two
        rounds.
        """
        prop = published(property_factory)
        unit = unit_factory(property=prop, vacant_count_updated_at=None)
        campus_distance_factory(property=prop, university=kyu, campus=campus, walking_minutes=None)
        start = dt.date.today() - dt.timedelta(days=400)
        tenancy = tenancy_factory(
            unit=unit, start_date=start, end_date=start + dt.timedelta(days=200)
        )
        review_factory(tenancy=tenancy)

        captured = Command().capture()

        assert set(captured) == {"listing", "detail", "reviews", "rating", "themes"}
        assert captured["themes"]["kyu"]["subdomain"] == "kyu"
        assert "theme" in captured["themes"]["kyu"]

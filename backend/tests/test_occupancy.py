"""
Vacancy provenance (ADR-002).

`vacant_count` is stated by the landlord and never derived. That is the right
choice — they know about the room let off-platform last week and we do not —
and it has one consequence that shapes everything here:

> A stated number with no timestamp is a number of unknown age presented as
> current. The reader cannot tell the difference and assumes the flattering
> one, which is the same class of dishonesty as a fabricated rating.

So every write stamps provenance, staleness is banded server-side, and a stale
count is shown with its age rather than hidden or zeroed.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from properties.constants import PropertyStatus, VacancyFreshness
from properties.models import Unit
from properties.services import (
    state_vacancy,
    units_with_stale_vacancy,
    vacancy_age_days,
    vacancy_freshness,
)

pytestmark = pytest.mark.django_db


def age_the_count(unit: Unit, *, days: int) -> Unit:
    Unit.all_objects.filter(pk=unit.pk).update(
        vacant_count_updated_at=timezone.now() - dt.timedelta(days=days)
    )
    unit.refresh_from_db()
    return unit


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestStatingVacancy:
    def test_it_stamps_the_time_and_the_author(self, unit_factory, landlord):
        unit = unit_factory(total_count=40, vacant_count=0)

        state_vacancy(unit, vacant_count=12, stated_by=landlord)
        unit.refresh_from_db()

        assert unit.vacant_count == 12
        assert unit.vacant_count_updated_at is not None
        assert unit.vacant_count_updated_by == landlord

    def test_a_caretaker_edit_is_distinguishable(
        self,
        unit_factory,
        landlord,
        caretaker_assignment_factory,
        property_factory,
        landlord_profile,
    ):
        """A caretaker walking the block and a landlord updating from an office
        are different kinds of evidence, and an operator chasing a stale
        listing needs to know which they are chasing."""
        prop = property_factory(landlord=landlord_profile)
        unit = unit_factory(property=prop, total_count=40, vacant_count=0)
        caretaker = caretaker_assignment_factory(property=prop)

        state_vacancy(unit, vacant_count=5, stated_by=caretaker.user)
        unit.refresh_from_db()

        assert unit.vacant_count_updated_by == caretaker.user
        assert unit.vacant_count_updated_by != landlord_profile.user

    def test_more_free_than_exist_is_refused(self, unit_factory, landlord):
        unit = unit_factory(total_count=10, vacant_count=0)

        with pytest.raises(ValidationError):
            state_vacancy(unit, vacant_count=11, stated_by=landlord)

    def test_a_fresh_number_never_wears_an_old_date(self, unit_factory, landlord):
        """The reason there is a single write path.

        A bare `unit.vacant_count = n; unit.save()` would leave a fresh number
        with a stale timestamp -- worse than a stale number, because the
        staleness signal would then say it is current.
        """
        unit = unit_factory(total_count=40, vacant_count=0)
        state_vacancy(unit, vacant_count=10, stated_by=landlord)
        age_the_count(unit, days=90)

        state_vacancy(unit, vacant_count=3, stated_by=landlord)
        unit.refresh_from_db()

        assert vacancy_freshness(unit) == VacancyFreshness.FRESH

    def test_it_is_never_derived_from_tenancies(
        self, unit_factory, landlord, tenancy_factory, tenant
    ):
        """The landlord's number survives contact with the tenancy records.

        They know about the room let off-platform and we do not, so overwriting
        their figure with ours would replace a good number with a worse one.
        """
        unit = unit_factory(total_count=40, vacant_count=30)
        state_vacancy(unit, vacant_count=30, stated_by=landlord)
        tenancy_factory(unit=unit, tenant=tenant, current=True)
        unit.refresh_from_db()

        assert unit.vacant_count == 30


class TestFreshness:
    def test_never_stated_is_unknown_not_stale(self, unit_factory):
        """ "Nobody has ever said" and "somebody said, long ago" are different
        facts and the UI words them differently."""
        unit = unit_factory()

        assert unit.vacant_count_updated_at is None
        assert vacancy_freshness(unit) == VacancyFreshness.UNKNOWN
        assert vacancy_age_days(unit) is None

    def test_a_recent_statement_is_fresh(self, unit_factory, landlord):
        unit = state_vacancy(unit_factory(), vacant_count=1, stated_by=landlord)

        assert vacancy_freshness(unit) == VacancyFreshness.FRESH

    def test_it_ages_through_the_bands(self, unit_factory, landlord):
        unit = state_vacancy(unit_factory(), vacant_count=1, stated_by=landlord)

        age_the_count(unit, days=settings.VACANCY_FRESH_DAYS + 1)
        assert vacancy_freshness(unit) == VacancyFreshness.AGEING

        age_the_count(unit, days=settings.VACANCY_STALE_DAYS + 1)
        assert vacancy_freshness(unit) == VacancyFreshness.STALE

    def test_the_thresholds_come_from_settings(self, unit_factory, landlord):
        unit = state_vacancy(unit_factory(), vacant_count=1, stated_by=landlord)
        age_the_count(unit, days=10)

        with override_settings(VACANCY_FRESH_DAYS=30, VACANCY_STALE_DAYS=90):
            assert vacancy_freshness(unit) == VacancyFreshness.FRESH

    def test_the_age_is_reported_in_days(self, unit_factory, landlord):
        unit = state_vacancy(unit_factory(), vacant_count=1, stated_by=landlord)
        age_the_count(unit, days=45)

        assert vacancy_age_days(unit) == 45

    def test_a_stale_count_is_not_zeroed(self, unit_factory, landlord):
        """Never hidden and never silently zeroed. A stale number the reader
        can judge beats no number at all -- and zeroing it would assert
        something we do not know."""
        unit = state_vacancy(unit_factory(total_count=40), vacant_count=12, stated_by=landlord)
        age_the_count(unit, days=365)

        assert unit.vacant_count == 12
        assert vacancy_freshness(unit) == VacancyFreshness.STALE


class TestStalenessPrompts:
    def test_a_stale_unit_is_listed(self, unit_factory, property_factory, landlord):
        prop = property_factory(status=PropertyStatus.PUBLISHED)
        unit = state_vacancy(
            unit_factory(property=prop, total_count=4), vacant_count=2, stated_by=landlord
        )
        age_the_count(unit, days=settings.VACANCY_STALE_DAYS + 5)

        assert unit in units_with_stale_vacancy()

    def test_a_never_stated_unit_is_listed_too(self, unit_factory, property_factory):
        """A listing that has never said how many rooms are free is at least as
        misleading as one that said so two months ago."""
        prop = property_factory(status=PropertyStatus.PUBLISHED)
        unit = unit_factory(property=prop)

        assert unit in units_with_stale_vacancy()

    def test_a_fresh_unit_is_not(self, unit_factory, property_factory, landlord):
        prop = property_factory(status=PropertyStatus.PUBLISHED)
        unit = state_vacancy(
            unit_factory(property=prop, total_count=4), vacant_count=2, stated_by=landlord
        )

        assert unit not in units_with_stale_vacancy()

    def test_an_unpublished_property_is_not_prompted(self, unit_factory, draft_property_factory):
        """Nobody is looking at it, so nobody is misled."""
        unit = unit_factory(property=draft_property_factory())

        assert unit not in units_with_stale_vacancy()

    def test_one_message_per_landlord(
        self, unit_factory, property_factory, landlord_profile, mailoutbox
    ):
        """A landlord with forty units should get one message, not forty. A
        prompt that arrives as a flood is a prompt that gets filtered."""
        from properties.jobs import prompt_stale_vacancies

        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        for index in range(5):
            unit_factory(property=prop, label=f"Unit {index}")

        prompted = prompt_stale_vacancies()

        assert prompted == 1
        assert len(mailoutbox) == 1

    def test_the_message_names_the_units(
        self, unit_factory, property_factory, landlord_profile, mailoutbox
    ):
        from properties.jobs import prompt_stale_vacancies

        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        unit_factory(property=prop, label="Bedsitter A")
        prompt_stale_vacancies()

        assert "Bedsitter A" in mailoutbox[0].body

    def test_an_erased_landlord_is_not_prompted(
        self, unit_factory, property_factory, landlord_profile, mailoutbox
    ):
        """A dormant listing needs no prompt, and mailing a tombstoned address
        is at best pointless (ADR-008)."""
        from accounts.privacy import erase_landlord_data
        from properties.jobs import prompt_stale_vacancies

        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        unit_factory(property=prop)
        erase_landlord_data(landlord_profile.user)

        prompt_stale_vacancies()

        assert len(mailoutbox) == 0

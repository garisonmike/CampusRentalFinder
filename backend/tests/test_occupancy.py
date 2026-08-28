"""
Vacancy provenance and the occupancy cross-check (ADR-002).

`vacant_count` is stated by the landlord and never derived. That is the right
choice — they know about the room let off-platform last week and we do not —
and it has one consequence that shapes everything here:

> A stated number with no timestamp is a number of unknown age presented as
> current. The reader cannot tell the difference and assumes the flattering
> one, which is the same class of dishonesty as a fabricated rating.

So every write stamps provenance, staleness is banded server-side, and a stale
count is shown with its age rather than hidden or zeroed.

The cross-check exists to notice contradictions, not to replace the number. Its
asymmetry is the design: derived-above-stated is impossible and worth flagging;
derived-below-stated is the normal case for the whole seeding period and
alerting on it would train everyone to ignore the alert.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.forms.models import model_to_dict
from django.test import RequestFactory, override_settings
from django.utils import timezone

from properties.admin import PropertyAdmin, UnitAdmin, UnitInline
from properties.constants import PropertyStatus, VacancyFreshness
from properties.models import Property, Unit
from properties.services import (
    compare_occupancy,
    cross_check_coverage,
    occupancy_contradictions,
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


# ---------------------------------------------------------------------------
# The other write path
# ---------------------------------------------------------------------------


class TestTheAdminWritePath:
    """The admin edits `vacant_count` directly, which makes it a second door.

    A service function that guards one entrance is a guard on one entrance.
    `docs/OPERATIONS.md` records five occasions where a rule lived in two
    places and the wrong copy won silently; a ModelForm that saves the count
    without the timestamp is that shape exactly, and it is worse than a stale
    count because the staleness signal would then assert currency.
    """

    def admin_form(self, model_admin, unit, **changes):
        request = RequestFactory().get("/admin/")
        form_class = model_admin.get_form(request, obj=unit, change=True)

        data = {
            name: value
            for name, value in model_to_dict(unit, fields=form_class.base_fields).items()
            if value is not None
        }
        data.update(changes)

        form = form_class(instance=unit, data=data)
        assert form.is_valid(), form.errors
        return form

    def test_an_admin_edit_stamps_provenance(self, unit_factory, staff_user):
        unit = unit_factory(total_count=40, vacant_count=0)
        model_admin = UnitAdmin(Unit, AdminSite())

        form = self.admin_form(model_admin, unit, vacant_count=7)
        request = RequestFactory().post("/admin/")
        request.user = staff_user
        model_admin.save_model(request, form.save(commit=False), form, change=True)
        unit.refresh_from_db()

        assert unit.vacant_count == 7
        assert unit.vacant_count_updated_by == staff_user
        assert vacancy_freshness(unit) == VacancyFreshness.FRESH

    def test_saving_without_touching_the_count_does_not_refresh_it(
        self, unit_factory, staff_user, landlord
    ):
        """An unrelated edit is not a restatement.

        Stamping one would refresh the staleness signal without anybody having
        looked at the rooms -- a false claim of currency, which is worse than
        the honest stale label it replaced.
        """
        unit = state_vacancy(unit_factory(total_count=40), vacant_count=9, stated_by=landlord)
        age_the_count(unit, days=200)
        model_admin = UnitAdmin(Unit, AdminSite())

        form = self.admin_form(model_admin, unit, label="Block C")
        request = RequestFactory().post("/admin/")
        request.user = staff_user
        model_admin.save_model(request, form.save(commit=False), form, change=True)
        unit.refresh_from_db()

        assert unit.label == "Block C"
        assert unit.vacant_count_updated_by == landlord
        assert vacancy_freshness(unit) == VacancyFreshness.STALE

    def test_the_provenance_fields_are_not_hand_editable(self):
        """Otherwise the stamp becomes a third thing to keep in sync by hand,
        and a date somebody typed is not evidence of anything."""
        model_admin = UnitAdmin(Unit, AdminSite())

        assert "vacant_count_updated_at" in model_admin.readonly_fields
        assert "vacant_count_updated_by" in model_admin.readonly_fields

    def test_the_inline_is_covered_too(self, staff_user, property_factory, unit_factory):
        """The likeliest place for this edit is the property page, editing the
        listing and its rooms together -- which a `save_model` override on
        UnitAdmin alone would not reach."""
        prop = property_factory()
        unit = unit_factory(property=prop, total_count=40, vacant_count=0)
        model_admin = PropertyAdmin(Property, AdminSite())
        request = RequestFactory().post("/admin/")
        request.user = staff_user

        formset_class = UnitInline(Property, AdminSite()).get_formset(request, obj=prop)
        prefix = formset_class.get_default_prefix()
        formset = formset_class(
            instance=prop,
            prefix=prefix,
            data={
                f"{prefix}-TOTAL_FORMS": "1",
                f"{prefix}-INITIAL_FORMS": "1",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
                f"{prefix}-0-id": str(unit.pk),
                f"{prefix}-0-property": str(prop.pk),
                f"{prefix}-0-label": unit.label,
                f"{prefix}-0-unit_type": unit.unit_type,
                f"{prefix}-0-rent_kes": str(unit.rent_kes),
                f"{prefix}-0-total_count": str(unit.total_count),
                f"{prefix}-0-vacant_count": "5",
                f"{prefix}-0-is_active": "on",
            },
        )
        assert formset.is_valid(), formset.errors

        model_admin.save_formset(request, form=None, formset=formset, change=True)
        unit.refresh_from_db()

        assert unit.vacant_count == 5
        assert unit.vacant_count_updated_by == staff_user


# ---------------------------------------------------------------------------
# The cross-check
# ---------------------------------------------------------------------------


class TestThePromptLink:
    """The prompt has to land on the screen that does the thing.

    An email asking a landlord to update their vacancy counts, which drops
    them on a home page, is an email that teaches them not to open the next
    one -- and this is the only channel the freshness mechanism has.
    """

    def test_the_email_links_to_the_page_that_does_it(
        self,
        unit_factory,
        property_factory,
        landlord_profile,
        campus_distance_factory,
        university,
        mailoutbox,
    ):
        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        campus_distance_factory(property=prop, university=university)
        unit_factory(property=prop)

        from properties.jobs import prompt_stale_vacancies

        prompt_stale_vacancies()

        assert len(mailoutbox) == 1
        assert "/portal/vacancy" in mailoutbox[0].body
        assert university.subdomain in mailoutbox[0].body

    def test_no_link_rather_than_a_wrong_one(
        self, unit_factory, property_factory, landlord_profile, mailoutbox
    ):
        """A landlord whose property is not joined to any campus has no tenant
        host to be sent to. A broken link is worse than none: it spends the
        same trust and returns nothing."""
        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        unit_factory(property=prop)

        from properties.jobs import prompt_stale_vacancies

        prompt_stale_vacancies()

        assert len(mailoutbox) == 1
        assert "Update them here" not in mailoutbox[0].body


class TestTheCrossCheck:
    def test_it_never_writes_to_vacant_count(self, unit_factory, landlord, tenancy_factory, tenant):
        """Comparison only. It is not an alternative source of truth."""
        unit = state_vacancy(unit_factory(total_count=40), vacant_count=30, stated_by=landlord)
        tenancy_factory(unit=unit, tenant=tenant, current=True)

        compare_occupancy()
        unit.refresh_from_db()

        assert unit.vacant_count == 30

    def test_it_counts_only_current_tenancies(
        self, unit_factory, tenancy_factory, tenant, student_profile
    ):
        """A stay that ended is not occupancy. This is the bug my own phase 7
        summary described in a function that never existed -- worth having a
        real test for the real thing."""
        unit = unit_factory(total_count=40, vacant_count=10)
        tenancy_factory(unit=unit, tenant=tenant, current=True)
        tenancy_factory(unit=unit, tenant=student_profile.user)  # past, by default

        row = next(r for r in compare_occupancy() if r.unit_id == unit.pk)

        assert row.derived_occupied == 1

    def test_more_confirmed_than_rooms_is_a_contradiction(
        self, unit_factory, tenancy_factory, student_profile_factory
    ):
        """A physical impossibility: the capacity is wrong, a tenancy that
        ended was never closed, or somebody is letting more rooms than they
        have. All three are worth a person looking."""
        unit = unit_factory(total_count=2, vacant_count=0)
        for _ in range(3):
            tenancy_factory(unit=unit, tenant=student_profile_factory().user, current=True)

        contradictions = occupancy_contradictions()

        assert [row.unit_id for row in contradictions] == [unit.pk]

    def test_fewer_confirmed_than_stated_is_never_flagged(
        self, unit_factory, landlord, tenancy_factory, tenant
    ):
        """The normal case for the entire seeding period, and for ever after
        wherever a landlord lets rooms off-platform. Alerting on it would train
        everyone to ignore the alert, which costs more than the alert is
        worth."""
        unit = state_vacancy(unit_factory(total_count=40), vacant_count=2, stated_by=landlord)
        tenancy_factory(unit=unit, tenant=tenant, current=True)

        assert occupancy_contradictions() == []

    def test_a_unit_with_no_tenancies_is_never_flagged(self, unit_factory):
        unit_factory(total_count=40, vacant_count=0)

        assert occupancy_contradictions() == []

    def test_a_pooled_unit_at_exactly_capacity_is_not_a_contradiction(
        self, unit_factory, tenancy_factory, student_profile_factory
    ):
        """Forty bedsitters with forty tenants is a full block, not an error."""
        unit = unit_factory(total_count=3, vacant_count=0)
        for _ in range(3):
            tenancy_factory(unit=unit, tenant=student_profile_factory().user, current=True)

        assert occupancy_contradictions() == []


class TestCrossCheckCoverage:
    """ "No contradictions found" must never be mistaken for "everything checks
    out". Early on, almost nothing is checkable at all.
    """

    def test_a_unit_with_no_current_tenancies_is_uninformative(self, unit_factory):
        unit_factory(total_count=40, vacant_count=10)

        coverage = cross_check_coverage()

        assert coverage["units"] == 1
        assert coverage["informative"] == 0

    def test_a_unit_with_a_current_tenancy_is_informative(
        self, unit_factory, tenancy_factory, tenant
    ):
        unit = unit_factory(total_count=40, vacant_count=10)
        tenancy_factory(unit=unit, tenant=tenant, current=True)

        assert cross_check_coverage()["informative"] == 1

    def test_a_past_tenancy_does_not_make_a_unit_informative(
        self, unit_factory, tenancy_factory, tenant
    ):
        """The default fixture is a finished stay, which is the seeding case:
        history everywhere, current occupancy nowhere."""
        unit = unit_factory(total_count=40, vacant_count=10)
        tenancy_factory(unit=unit, tenant=tenant)

        assert cross_check_coverage()["informative"] == 0

    def test_coverage_is_reported_beside_the_finding(
        self, unit_factory, tenancy_factory, tenant, student_profile_factory
    ):
        """So an operator reading "0 contradictions" can see whether that is
        reassuring or vacuous."""
        # Scoped to the units this test made. `tenancy_factory` builds an
        # Application, which builds a Unit of its own -- so a count over the
        # whole table measures the fixtures as much as the subject.
        mine = [unit_factory(total_count=40, vacant_count=10) for _ in range(4)]
        checked = unit_factory(total_count=40, vacant_count=10)
        mine.append(checked)
        tenancy_factory(unit=checked, tenant=tenant, current=True)

        coverage = cross_check_coverage(Unit.all_objects.filter(pk__in=[unit.pk for unit in mine]))

        assert coverage["units"] == 5
        assert coverage["informative"] == 1
        assert coverage["contradictions"] == 0

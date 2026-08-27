"""
Tenancy currency is derived, never stored (ADR-004).

`status` used to carry `active` and `ended`. Both were wrong in the same way: a
lifecycle state that depends on the passage of time cannot be stored correctly.
It needs a job to keep it true, and when the job stops the data lies silently —
no error, no alert, just every historical stay reporting itself as running.

The symptom that made it concrete: `confirm_claim` marked every stay `ACTIVE`
regardless of its end date, so a seeded 2023 tenancy read as current, and
nothing in the codebase noticed because **nothing ever read the field**.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError, transaction

from tenancies.constants import (
    LIVE_TENANCY_STATUSES,
    VOID_TENANCY_STATUSES,
    TenancyCurrency,
    TenancyStatus,
)
from tenancies.models import Tenancy
from tenancies.services import confirm_termination, request_early_termination

pytestmark = pytest.mark.django_db

TODAY = dt.date.today()


def terminate_tenancy_early(tenancy, *, ended_on, reason, initiated_by=None):
    """Request and immediately confirm, as a landlord and tenant agreeing.

    The one-call helper these tests were written against is now a two-party
    state machine (ADR-004): a termination is proposed and then confirmed,
    because a date one party asserts about the other is exactly the shape that
    needed a confirmation window everywhere else.
    """
    request = request_early_termination(
        tenancy,
        initiated_by=initiated_by or tenancy.tenant,
        ended_on=ended_on,
        reason=reason,
    )
    return confirm_termination(request)


def stay(tenancy_factory, *, starts: int, ends: int | None, **kwargs):
    """A tenancy running from `starts` days ago to `ends` days ago.

    Negative offsets are in the future. `ends=None` is open-ended.
    """
    return tenancy_factory(
        start_date=TODAY - dt.timedelta(days=starts),
        end_date=None if ends is None else TODAY - dt.timedelta(days=ends),
        **kwargs,
    )


def scoped():
    return Tenancy.all_objects.across_tenants()


# ---------------------------------------------------------------------------
# The status field no longer says when
# ---------------------------------------------------------------------------


class TestStatusHoldsNoTimeState:
    def test_there_is_no_active_status(self):
        """The whole point. A value that has to be maintained by a job is a
        value that will eventually be wrong and say nothing about it."""
        assert not hasattr(TenancyStatus, "ACTIVE")
        assert "active" not in TenancyStatus.values

    def test_there_is_no_ended_status(self):
        assert not hasattr(TenancyStatus, "ENDED")
        assert "ended" not in TenancyStatus.values

    def test_every_remaining_status_is_changed_only_by_a_person(self):
        """Confirmed, disputed, withdrawn, rejected: each moves because
        somebody moved it, never because a day passed."""
        assert set(TenancyStatus.values) == {
            "confirmed",
            "disputed",
            "withdrawn",
            "rejected",
        }

    def test_a_tenancy_is_confirmed_by_default(self, tenancy_factory):
        """A Tenancy row exists only once a claim was confirmed or an
        application accepted, so there is no earlier state to model."""
        assert Tenancy._meta.get_field("status").default == TenancyStatus.CONFIRMED

    def test_live_and_void_partition_the_statuses(self):
        assert set(LIVE_TENANCY_STATUSES) | set(VOID_TENANCY_STATUSES) == set(TenancyStatus.values)
        assert not set(LIVE_TENANCY_STATUSES) & set(VOID_TENANCY_STATUSES)


# ---------------------------------------------------------------------------
# current / past / upcoming
# ---------------------------------------------------------------------------


class TestCurrent:
    def test_a_running_stay_is_current(self, tenancy_factory):
        tenancy = stay(tenancy_factory, starts=30, ends=-30)

        assert tenancy in scoped().current()

    def test_an_open_ended_stay_is_current(self, tenancy_factory):
        """A null end_date means "no agreed end", which is a real arrangement
        -- most Kenyan student lets are month-to-month with nothing written --
        and it must never be conflated with a historical stay."""
        tenancy = stay(tenancy_factory, starts=30, ends=None)

        assert tenancy.end_date is None
        assert tenancy in scoped().current()
        assert tenancy not in scoped().past()

    def test_a_finished_stay_is_not_current(self, tenancy_factory):
        tenancy = stay(tenancy_factory, starts=200, ends=30)

        assert tenancy not in scoped().current()
        assert tenancy in scoped().past()

    def test_a_future_stay_is_not_current(self, tenancy_factory):
        tenancy = stay(tenancy_factory, starts=-30, ends=-200)

        assert tenancy not in scoped().current()
        assert tenancy in scoped().upcoming()

    def test_the_boundaries_are_inclusive(self, tenancy_factory):
        """A stay that starts today is current today, and one that ends today
        is still current today. Somebody living there this morning did not stop
        because the calendar rolled."""
        starting = stay(tenancy_factory, starts=0, ends=-90)
        ending = stay(tenancy_factory, starts=90, ends=0)

        current = scoped().current()

        assert starting in current
        assert ending in current

    def test_currency_moves_with_the_date_and_nothing_else(self, tenancy_factory):
        """The property a stored status cannot have. No write, no job, no
        migration -- the same row answers differently on a different day."""
        tenancy = stay(tenancy_factory, starts=30, ends=-30)
        after = tenancy.end_date + dt.timedelta(days=1)

        assert tenancy in scoped().current()
        assert tenancy not in scoped().current(today=after)
        assert tenancy in scoped().past(today=after)

    def test_the_three_sets_are_disjoint(self, tenancy_factory):
        for starts, ends in ((30, -30), (200, 30), (-30, -200), (30, None)):
            stay(tenancy_factory, starts=starts, ends=ends)

        current = set(scoped().current().values_list("pk", flat=True))
        past = set(scoped().past().values_list("pk", flat=True))
        upcoming = set(scoped().upcoming().values_list("pk", flat=True))

        assert not current & past
        assert not current & upcoming
        assert not past & upcoming
        assert len(current | past | upcoming) == 4

    def test_the_model_method_agrees_with_the_queryset(self, tenancy_factory):
        """Two implementations of one predicate is how they drift, so the
        agreement is asserted rather than assumed."""
        for starts, ends in ((30, -30), (200, 30), (-30, -200), (30, None), (0, 0)):
            tenancy = stay(tenancy_factory, starts=starts, ends=ends)

            assert tenancy.is_current() == (tenancy in scoped().current())


class TestVoidTenanciesAreNeitherCurrentNorPast:
    """A withdrawn tenancy is not a stay that happened at another time. It is a
    stay that did not happen."""

    def test_a_withdrawn_stay_is_not_current(self, tenancy_factory):
        tenancy = stay(tenancy_factory, starts=30, ends=-30, status=TenancyStatus.WITHDRAWN)

        assert tenancy not in scoped().current()
        assert tenancy.is_current() is False

    def test_a_rejected_stay_is_not_past_either(self, tenancy_factory):
        tenancy = stay(tenancy_factory, starts=200, ends=30, status=TenancyStatus.REJECTED)

        assert tenancy not in scoped().past()
        assert tenancy not in scoped().live()

    def test_a_disputed_stay_is_still_a_stay(self, tenancy_factory):
        """Disputed means challenged, not void. The student lived there while
        the argument runs, and a review still rests on it."""
        tenancy = stay(tenancy_factory, starts=30, ends=-30, status=TenancyStatus.DISPUTED)

        assert tenancy in scoped().current()


# ---------------------------------------------------------------------------
# Early termination
# ---------------------------------------------------------------------------


class TestEarlyTermination:
    def test_it_moves_the_end_date(self, tenancy_factory):
        """`end_date` stays authoritative for currency, so the stay reads as
        past from the day it actually ended -- with no job involved."""
        tenancy = stay(tenancy_factory, starts=60, ends=-300)
        assert tenancy in scoped().current()

        terminate_tenancy_early(
            tenancy, ended_on=TODAY - dt.timedelta(days=1), reason="Transferred campus."
        )

        assert tenancy not in scoped().current()
        assert tenancy in scoped().past()

    def test_it_records_that_it_was_early(self, tenancy_factory):
        """ "Ended in March" and "ended early in March" are different facts
        about the same date."""
        tenancy = stay(tenancy_factory, starts=60, ends=-300)

        terminate_tenancy_early(tenancy, ended_on=TODAY, reason="Landlord sold the block.")
        tenancy.refresh_from_db()

        assert tenancy.terminated_early is True
        assert tenancy.termination_reason == "Landlord sold the block."

    def test_the_status_does_not_change(self, tenancy_factory):
        """It is still a confirmed stay. It just finished sooner."""
        tenancy = stay(tenancy_factory, starts=60, ends=-300)

        terminate_tenancy_early(tenancy, ended_on=TODAY, reason="Moved out.")
        tenancy.refresh_from_db()

        assert tenancy.status == TenancyStatus.CONFIRMED

    def test_a_reason_is_required(self, tenancy_factory):
        from django.core.exceptions import ValidationError

        tenancy = stay(tenancy_factory, starts=60, ends=-300)

        with pytest.raises(ValidationError):
            terminate_tenancy_early(tenancy, ended_on=TODAY, reason="")

    def test_it_cannot_end_before_it_started(self, tenancy_factory):
        from django.core.exceptions import ValidationError

        tenancy = stay(tenancy_factory, starts=60, ends=-300)

        with pytest.raises(ValidationError):
            terminate_tenancy_early(
                tenancy, ended_on=TODAY - dt.timedelta(days=200), reason="Wrong."
            )

    def test_the_database_refuses_an_undated_early_termination(self, tenancy_factory):
        """Without the date, currency would still report the stay as running --
        the exact class of lie the derived model exists to remove."""
        tenancy = stay(tenancy_factory, starts=60, ends=None)
        tenancy.terminated_early = True
        tenancy.termination_reason = "Moved out."

        with pytest.raises(IntegrityError), transaction.atomic():
            tenancy.save()

    def test_the_database_refuses_an_unexplained_early_termination(self, tenancy_factory):
        tenancy = stay(tenancy_factory, starts=60, ends=1)
        tenancy.terminated_early = True

        with pytest.raises(IntegrityError), transaction.atomic():
            tenancy.save()


# ---------------------------------------------------------------------------
# The predicate is shared
# ---------------------------------------------------------------------------


class TestOverlappingIsExpressedOnce:
    def test_the_duplicate_predicate_uses_the_queryset_method(
        self, tenancy_factory, unit_factory, tenant
    ):
        """`_find_covering_tenancy` and the exclusion constraint must agree.
        Expressing the range predicate once is what makes that structural
        rather than a matter of remembering."""
        unit = unit_factory()
        tenancy = tenancy_factory(
            unit=unit,
            tenant=tenant,
            start_date=dt.date(2024, 1, 1),
            end_date=dt.date(2024, 6, 30),
        )

        overlaps = scoped().overlapping(dt.date(2024, 3, 1), dt.date(2024, 9, 1))
        misses = scoped().overlapping(dt.date(2025, 1, 1), dt.date(2025, 6, 1))

        assert tenancy in overlaps
        assert tenancy not in misses

    def test_overlapping_ignores_status(self, tenancy_factory, unit_factory, tenant):
        """Status-independent, exactly like the constraint: an ended stay is
        still a duplicate of an overlapping claim."""
        tenancy = tenancy_factory(
            unit=unit_factory(),
            tenant=tenant,
            start_date=dt.date(2024, 1, 1),
            end_date=dt.date(2024, 6, 30),
            status=TenancyStatus.WITHDRAWN,
        )

        assert tenancy in scoped().overlapping(dt.date(2024, 3, 1), None)

    def test_an_open_ended_stay_overlaps_everything_after_it(
        self, tenancy_factory, unit_factory, tenant
    ):
        tenancy = tenancy_factory(
            unit=unit_factory(), tenant=tenant, start_date=dt.date(2024, 1, 1), end_date=None
        )

        assert tenancy in scoped().overlapping(dt.date(2030, 1, 1), dt.date(2030, 6, 1))


class TestCurrencyIsAvailableThroughTheScopedManager:
    def test_the_scoped_manager_carries_the_predicates(
        self,
        tenancy_factory,
        university,
        unit_factory,
        property_factory,
        campus_factory,
        campus_distance_factory,
    ):
        """`TenantScopedManager.from_queryset`, not a parallel manager -- so
        `for_tenant(...)` still returns something that knows about currency."""
        prop = property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        tenancy = stay(tenancy_factory, starts=30, ends=-30, unit=unit_factory(property=prop))

        assert tenancy in Tenancy.objects.for_tenant(university).current()

    def test_an_unqualified_query_still_raises(self, tenancy_factory):
        """Adding queryset methods must not have opened the guard."""
        from config.tenancy import TenantScopeError

        stay(tenancy_factory, starts=30, ends=-30)

        with pytest.raises(TenantScopeError):
            list(Tenancy.objects.current())


class TestTheFixtureDefaultShape:
    """The default fixture shape decides which bugs the suite can see.

    `TenancyFactory` produces a **finished** stay unless a trait says
    otherwise. This is pinned by test rather than left as a convention,
    because the convention is the whole safety property:

    > `Unit.vacant_count` reconciliation counted tenancies by `status='active'`,
    > stamped at the time on every stay regardless of dates. A pooled unit's
    > occupancy therefore included every historical tenancy it had ever had.
    > No test caught it because no fixture had any history -- every stay the
    > suite created was currently running, so the count was accidentally right
    > in every case the suite could construct.

    A running stay is the easy case; almost any implementation gets it right.
    A finished one is where "current" and "exists" stop being the same
    question.
    """

    def test_the_default_is_a_finished_stay(self, tenancy_factory):
        tenancy = tenancy_factory()

        assert tenancy.end_date is not None
        assert tenancy.end_date < dt.date.today()
        assert tenancy in Tenancy.all_objects.past()
        assert tenancy not in Tenancy.all_objects.current()

    def test_current_is_open_ended_by_default(self, tenancy_factory):
        """`end_date=None` means running with no agreed end -- a real case, and
        NOT the same as a stay that has ended (ADR-004)."""
        tenancy = tenancy_factory(current=True)

        assert tenancy.end_date is None
        assert tenancy in Tenancy.all_objects.current()
        assert tenancy not in Tenancy.all_objects.past()

    def test_a_fixed_term_stay_can_also_be_current(self, tenancy_factory):
        tenancy = tenancy_factory(current_fixed_term=True)

        assert tenancy.end_date > dt.date.today()
        assert tenancy in Tenancy.all_objects.current()

    def test_upcoming_has_not_started(self, tenancy_factory):
        tenancy = tenancy_factory(upcoming=True)

        assert tenancy.start_date > dt.date.today()
        assert tenancy in Tenancy.all_objects.upcoming()
        assert tenancy not in Tenancy.all_objects.current()

    def test_the_three_states_are_disjoint(self, tenancy_factory):
        """A tenancy is past, current or upcoming -- never two at once."""
        past = tenancy_factory()
        current = tenancy_factory(current=True)
        upcoming = tenancy_factory(upcoming=True)

        buckets = {
            "past": set(Tenancy.all_objects.past().values_list("pk", flat=True)),
            "current": set(Tenancy.all_objects.current().values_list("pk", flat=True)),
            "upcoming": set(Tenancy.all_objects.upcoming().values_list("pk", flat=True)),
        }

        assert buckets["past"] & buckets["current"] == set()
        assert buckets["current"] & buckets["upcoming"] == set()
        assert buckets["past"] & buckets["upcoming"] == set()
        assert past.pk in buckets["past"]
        assert current.pk in buckets["current"]
        assert upcoming.pk in buckets["upcoming"]


class TestTheRowAndTheQuerysetAgree:
    """Two implementations of one derivation is two chances to disagree.

    `Tenancy.currency()` answers for a single row; `.current()`, `.past()` and
    `.upcoming()` answer for a set. Both exist because both are needed -- a
    serializer has an instance, a list endpoint has a queryset -- and a
    disagreement between them would be invisible: the list would show a stay
    the detail page called past, or the other way round, and neither would
    error.
    """

    def cases(self, tenancy_factory):
        return {
            "past": tenancy_factory(),
            "current_open_ended": tenancy_factory(current=True),
            "current_fixed_term": tenancy_factory(current_fixed_term=True),
            "upcoming": tenancy_factory(upcoming=True),
        }

    def test_every_row_lands_in_the_bucket_it_reports(self, tenancy_factory):
        rows = self.cases(tenancy_factory)

        buckets = {
            TenancyCurrency.CURRENT: set(
                Tenancy.all_objects.current().values_list("pk", flat=True)
            ),
            TenancyCurrency.PAST: set(Tenancy.all_objects.past().values_list("pk", flat=True)),
            TenancyCurrency.UPCOMING: set(
                Tenancy.all_objects.upcoming().values_list("pk", flat=True)
            ),
        }

        for name, tenancy in rows.items():
            reported = tenancy.currency()
            assert tenancy.pk in buckets[reported], (
                f"{name}: currency() says {reported!r} but the {reported} "
                f"queryset does not contain it"
            )

    def test_a_row_appears_in_no_other_bucket(self, tenancy_factory):
        rows = self.cases(tenancy_factory)

        for name, tenancy in rows.items():
            reported = tenancy.currency()
            for bucket in (
                TenancyCurrency.CURRENT,
                TenancyCurrency.PAST,
                TenancyCurrency.UPCOMING,
            ):
                if bucket == reported:
                    continue
                queryset = getattr(Tenancy.all_objects, bucket)()
                assert tenancy.pk not in queryset.values_list("pk", flat=True), (
                    f"{name} reports {reported!r} but also appears in {bucket}"
                )

    def test_a_dead_record_is_in_no_bucket_at_all(self, tenancy_factory):
        """A rejected or withdrawn record is not a stay that happened at
        another time. It is a stay that did not happen."""
        dead = tenancy_factory(status=TenancyStatus.WITHDRAWN)

        assert dead.currency() == TenancyCurrency.NOT_A_STAY
        for bucket in ("current", "past", "upcoming"):
            assert dead.pk not in getattr(Tenancy.all_objects, bucket)().values_list(
                "pk", flat=True
            )

    def test_they_agree_on_a_boundary_day(self, tenancy_factory):
        """A stay whose end date is today is still current, not past. Off by
        one here silently ends every tenancy a day early."""
        today = dt.date.today()
        ending_today = tenancy_factory(start_date=today - dt.timedelta(days=60), end_date=today)

        assert ending_today.currency(today=today) == TenancyCurrency.CURRENT
        assert ending_today.pk in Tenancy.all_objects.current(today=today).values_list(
            "pk", flat=True
        )

    def test_they_agree_on_the_first_day(self, tenancy_factory):
        today = dt.date.today()
        starting_today = tenancy_factory(start_date=today, end_date=None)

        assert starting_today.currency(today=today) == TenancyCurrency.CURRENT
        assert starting_today.pk in Tenancy.all_objects.current(today=today).values_list(
            "pk", flat=True
        )

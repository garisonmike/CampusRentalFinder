"""
Rating aggregates (ADR-004).

Three caches over `Review`, rebuilt from it and nothing else. A denormalised
`Review.property_id` would have made the grouping cheaper; it was declined
because both options are denormalisation and the difference is how they fail.
A duplicated FK that drifts is silent corruption with no way to tell which
value is right. A stale cached number is found by the reconciler and rebuilt.

The central case here is
:meth:`TestDeduplication.test_student_count_and_review_count_diverge`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.management import call_command

from ratings.aggregates import (
    LandlordRatingAggregate,
    PropertyRatingAggregate,
    UnitRatingAggregate,
)
from ratings.jobs import reconcile_rating_aggregates, refresh_aggregates_for_review
from ratings.recompute import (
    recompute_all,
    recompute_landlord,
    recompute_property,
    recompute_unit,
    reconcile_properties,
    reconcile_units,
    summarise_property,
    summarise_unit,
)
from ratings.services import create_review

pytestmark = pytest.mark.django_db


MINIMUM_STAY = 60


def stay(tenancy_factory, unit, tenant, *, offset: int = 0, **kwargs):
    """A finished stay of 60 days, ending `offset` days ago."""
    end = dt.date.today() - dt.timedelta(days=1 + offset)
    return tenancy_factory(
        unit=unit,
        tenant=tenant,
        start_date=end - dt.timedelta(days=MINIMUM_STAY),
        end_date=end,
        **kwargs,
    )


@pytest.fixture
def block(property_factory, unit_factory):
    """One property with two different unit types, as a real block has."""
    prop = property_factory()
    return (
        prop,
        unit_factory(property=prop, label="Bedsitter"),
        unit_factory(property=prop, label="One-bedroom"),
    )


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """One contribution per (property, tenant).

    Uniqueness stays at the tenancy: one stay, one review. A (property, tenant)
    constraint would block a legitimate second review — a student who moves
    from a bedsitter to a one-bedroom in the same block has two genuinely
    different experiences — and there is no honest way to choose which to keep.
    So both are kept and the aggregate de-duplicates.
    """

    def one_mover(self, tenancy_factory, block, tenant, first: int, second: int):
        prop, bedsitter, one_bed = block
        create_review(stay(tenancy_factory, bedsitter, tenant, offset=200), rating=first)
        create_review(stay(tenancy_factory, one_bed, tenant), rating=second)
        return prop

    def test_student_count_and_review_count_diverge(self, tenancy_factory, block, tenant):
        """The divergence IS the de-duplication.

        One student, two stays in the same block, two reviews. Two rows behind
        the number; one voice in it.
        """
        prop = self.one_mover(tenancy_factory, block, tenant, 2, 4)

        aggregate = recompute_property(prop.pk)

        assert aggregate.review_count == 2
        assert aggregate.student_count == 1

    def test_the_mover_is_weighted_once(self, tenancy_factory, block, tenant):
        """Their two ratings average to one contribution, not two."""
        prop = self.one_mover(tenancy_factory, block, tenant, 2, 4)

        assert recompute_property(prop.pk).average_rating == Decimal("3.00")

    def test_a_mover_cannot_outweigh_two_other_students(
        self, tenancy_factory, block, tenant, student_profile, verified_student_profile
    ):
        """Without de-duplication the mover's two 5s would drag the block up.

        Mover: 5 and 5 -> one voice of 5. Two others: 1 and 1.
        Deduped mean = (5 + 1 + 1) / 3 = 2.33. Raw mean would be 3.00.
        """
        prop, bedsitter, one_bed = block
        create_review(stay(tenancy_factory, bedsitter, tenant, offset=200), rating=5)
        create_review(stay(tenancy_factory, one_bed, tenant), rating=5)
        create_review(stay(tenancy_factory, bedsitter, student_profile.user), rating=1)
        create_review(stay(tenancy_factory, one_bed, verified_student_profile.user), rating=1)

        aggregate = recompute_property(prop.pk)

        assert aggregate.student_count == 3
        assert aggregate.review_count == 4
        assert aggregate.average_rating == Decimal("2.33")

    def test_editing_one_of_a_movers_reviews_updates_their_single_voice(
        self, tenancy_factory, block, tenant
    ):
        """The case worth checking: a tenant with two reviews on one property
        edits one of them. Their contribution is the mean of their own reviews,
        so it moves halfway, and the counts do not change at all.
        """
        prop, bedsitter, one_bed = block
        first = create_review(stay(tenancy_factory, bedsitter, tenant, offset=200), rating=2)
        create_review(stay(tenancy_factory, one_bed, tenant), rating=4)

        before = recompute_property(prop.pk)
        assert before.average_rating == Decimal("3.00")

        first.rating = 4
        first.save(update_fields=["rating", "updated_at"])
        after = recompute_property(prop.pk)

        assert after.average_rating == Decimal("4.00")
        assert after.review_count == 2
        assert after.student_count == 1

    def test_the_distribution_counts_raw_reviews(self, tenancy_factory, block, tenant):
        """Deliberately not deduped: "how the scores fall" is a different
        question from "how many people spoke", and both deserve an honest
        answer."""
        prop = self.one_mover(tenancy_factory, block, tenant, 2, 4)

        distribution = recompute_property(prop.pk).rating_distribution

        assert distribution["2"] == 1
        assert distribution["4"] == 1

    def test_the_counts_cannot_be_stored_inverted(self, tenancy_factory, block, tenant):
        """student_count <= review_count always: de-duplication can only
        collapse rows, never invent contributors."""
        from django.db import IntegrityError, transaction

        prop = self.one_mover(tenancy_factory, block, tenant, 2, 4)
        aggregate = recompute_property(prop.pk)
        aggregate.student_count = 99

        with pytest.raises(IntegrityError), transaction.atomic():
            aggregate.save()


class TestUnitAggregate:
    def test_a_unit_does_not_dedupe(self, tenancy_factory, unit_factory, tenant, student_profile):
        """One stay, one review, and a tenant cannot hold overlapping stays in
        the same unit — so there is nothing left to collapse."""
        unit = unit_factory()
        create_review(stay(tenancy_factory, unit, tenant), rating=5)
        create_review(stay(tenancy_factory, unit, student_profile.user), rating=1)

        aggregate = recompute_unit(unit.pk)

        assert aggregate.review_count == 2
        assert aggregate.student_count == 2
        assert aggregate.average_rating == Decimal("3.00")

    def test_a_returning_student_contributes_twice_to_the_unit(
        self, tenancy_factory, unit_factory, tenant
    ):
        """Two separate experiences of the same room, years apart."""
        unit = unit_factory()
        create_review(stay(tenancy_factory, unit, tenant, offset=800), rating=1)
        create_review(stay(tenancy_factory, unit, tenant), rating=5)

        aggregate = recompute_unit(unit.pk)

        assert aggregate.review_count == 2
        assert aggregate.average_rating == Decimal("3.00")


class TestLandlordAggregate:
    def test_it_spans_every_property(
        self,
        tenancy_factory,
        property_factory,
        unit_factory,
        landlord_profile,
        tenant,
        student_profile,
    ):
        first = unit_factory(property=property_factory(landlord=landlord_profile))
        second = unit_factory(property=property_factory(landlord=landlord_profile))
        create_review(stay(tenancy_factory, first, tenant), rating=5)
        create_review(stay(tenancy_factory, second, student_profile.user), rating=3)

        aggregate = recompute_landlord(landlord_profile.pk)

        assert aggregate.average_rating == Decimal("4.00")
        assert aggregate.property_count == 2

    def test_it_dedupes_per_tenant_too(
        self, tenancy_factory, property_factory, unit_factory, landlord_profile, tenant
    ):
        """A student who rented two of the same landlord's blocks is one
        person's opinion of that landlord."""
        first = unit_factory(property=property_factory(landlord=landlord_profile))
        second = unit_factory(property=property_factory(landlord=landlord_profile))
        create_review(stay(tenancy_factory, first, tenant, offset=300), rating=1)
        create_review(stay(tenancy_factory, second, tenant), rating=5)

        aggregate = recompute_landlord(landlord_profile.pk)

        assert aggregate.student_count == 1
        assert aggregate.review_count == 2

    def test_it_is_not_tenant_scoped(self):
        """A landlord's record spans universities. Scoping it to one would
        report a different number to each, which is worse than reporting one
        number because both would look authoritative."""
        from config.tenancy import is_tenant_scoped

        assert is_tenant_scoped(LandlordRatingAggregate) is False
        assert is_tenant_scoped(PropertyRatingAggregate) is True


# ---------------------------------------------------------------------------
# The empty state
# ---------------------------------------------------------------------------


class TestEmptyState:
    """No review means "no verified reviews yet".

    Never a neutral score, never a placeholder star count, never an average
    over zero rows. On a trust platform a fabricated signal is worse than no
    signal, because it is indistinguishable from a real one and it is the
    platform itself doing the fabricating.
    """

    def test_an_unreviewed_property_has_no_score(self, property_factory):
        aggregate = recompute_property(property_factory().pk)

        assert aggregate.average_rating is None
        assert aggregate.is_empty() is True

    def test_it_is_null_and_not_zero(self, property_factory):
        assert recompute_property(property_factory().pk).average_rating != Decimal("0")

    def test_the_database_refuses_a_score_with_no_reviews(self, property_factory):
        """A later UI pass cannot quietly invent a default rating, because the
        constraint will not store one."""
        from django.db import IntegrityError, transaction

        aggregate = recompute_property(property_factory().pk)
        aggregate.average_rating = Decimal("3.50")

        with pytest.raises(IntegrityError), transaction.atomic():
            aggregate.save()

    def test_the_distribution_is_present_and_zero(self, property_factory):
        """Present-and-zero rather than absent, so a reader never has to decide
        whether a missing key means "none" or "not computed"."""
        distribution = recompute_property(property_factory().pk).rating_distribution

        assert distribution == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}

    def test_a_hidden_review_leaves_no_score_behind(self, tenancy_factory, unit_factory, tenant):
        """Moderation, not deletion: the row survives and restoring it
        recomputes the number."""
        unit = unit_factory()
        review = create_review(stay(tenancy_factory, unit, tenant), rating=5)

        assert recompute_unit(unit.pk).average_rating == Decimal("5.00")

        review.is_published = False
        review.hidden_reason = "Names a third party."
        review.save(update_fields=["is_published", "hidden_reason", "updated_at"])

        assert recompute_unit(unit.pk).average_rating is None

        review.is_published = True
        review.hidden_reason = ""
        review.save(update_fields=["is_published", "hidden_reason", "updated_at"])

        assert recompute_unit(unit.pk).average_rating == Decimal("5.00")


# ---------------------------------------------------------------------------
# One implementation, two entry points
# ---------------------------------------------------------------------------


class TestRebuild:
    def test_the_job_refreshes_all_three(
        self, tenancy_factory, property_factory, unit_factory, landlord_profile, tenant
    ):
        prop = property_factory(landlord=landlord_profile)
        unit = unit_factory(property=prop)
        review = create_review(stay(tenancy_factory, unit, tenant), rating=4)

        refresh_aggregates_for_review(review.pk)

        assert UnitRatingAggregate.all_objects.get(unit=unit).average_rating == Decimal("4.00")
        assert PropertyRatingAggregate.all_objects.get(
            property_reviewed=prop
        ).average_rating == Decimal("4.00")
        assert LandlordRatingAggregate.objects.get(landlord=landlord_profile).average_rating == (
            Decimal("4.00")
        )

    def test_the_job_tolerates_a_deleted_review(self):
        refresh_aggregates_for_review(999999)  # must not raise

    def test_the_command_and_the_job_agree(
        self, tenancy_factory, property_factory, unit_factory, landlord_profile, tenant
    ):
        """One implementation, two entry points. If the rebuild and the
        incremental update were separate code they would drift, and only one of
        them would be right with no way to tell which from the outside.
        """
        unit = unit_factory(property=property_factory(landlord=landlord_profile))
        review = create_review(stay(tenancy_factory, unit, tenant), rating=3)

        refresh_aggregates_for_review(review.pk)
        from_job = UnitRatingAggregate.all_objects.get(unit=unit).average_rating

        call_command("recompute_ratings")
        from_command = UnitRatingAggregate.all_objects.get(unit=unit).average_rating

        assert from_job == from_command

    def test_a_full_rebuild_reports_what_it_touched(
        self, tenancy_factory, property_factory, unit_factory, landlord_profile, tenant
    ):
        unit = unit_factory(property=property_factory(landlord=landlord_profile))
        create_review(stay(tenancy_factory, unit, tenant), rating=3)

        counts = recompute_all()

        assert counts["units"] >= 1
        assert counts["properties"] >= 1
        assert counts["landlords"] >= 1

    def test_the_command_can_target_one_property(self, property_factory):
        prop = property_factory()

        call_command("recompute_ratings", property=prop.pk)

        assert PropertyRatingAggregate.all_objects.filter(property_reviewed=prop).exists()

    def test_everything_is_rebuildable_from_review_alone(
        self, tenancy_factory, unit_factory, tenant
    ):
        """The property that makes these a cache rather than a second source of
        truth. Wipe every aggregate and the numbers come back identical."""
        unit = unit_factory()
        create_review(stay(tenancy_factory, unit, tenant), rating=5)
        recompute_all()
        before = UnitRatingAggregate.all_objects.get(unit=unit).average_rating

        UnitRatingAggregate.all_objects.all().delete()
        PropertyRatingAggregate.all_objects.all().delete()
        LandlordRatingAggregate.objects.all().delete()
        recompute_all()

        assert UnitRatingAggregate.all_objects.get(unit=unit).average_rating == before


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


class TestReconciliation:
    """It never silently corrects.

    Self-healing hides the bug that caused the drift, and the bug is the thing
    worth knowing about. A drift alert means "recompute this one and then find
    out why", not "the system fixed itself".
    """

    def test_a_correct_aggregate_reports_no_drift(self, tenancy_factory, unit_factory, tenant):
        unit = unit_factory()
        create_review(stay(tenancy_factory, unit, tenant), rating=4)
        recompute_all()

        assert reconcile_units() == []
        assert reconcile_properties() == []

    def test_a_drifted_average_is_found(self, tenancy_factory, unit_factory, tenant):
        unit = unit_factory()
        create_review(stay(tenancy_factory, unit, tenant), rating=4)
        recompute_all()
        UnitRatingAggregate.all_objects.filter(unit=unit).update(average_rating=Decimal("5.00"))

        drifts = reconcile_units()

        assert len(drifts) == 1
        assert drifts[0].field == "average_rating"
        assert drifts[0].stored == Decimal("5.00")
        assert drifts[0].computed == Decimal("4.00")

    def test_a_drifted_count_is_found(self, tenancy_factory, unit_factory, tenant):
        unit = unit_factory()
        create_review(stay(tenancy_factory, unit, tenant), rating=4)
        recompute_all()
        UnitRatingAggregate.all_objects.filter(unit=unit).update(review_count=7)

        assert any(drift.field == "review_count" for drift in reconcile_units())

    def test_it_does_not_correct_what_it_finds(self, tenancy_factory, unit_factory, tenant):
        """The whole point. The wrong number stays wrong until a person has
        seen the alert and decided what to do about it."""
        unit = unit_factory()
        create_review(stay(tenancy_factory, unit, tenant), rating=4)
        recompute_all()
        UnitRatingAggregate.all_objects.filter(unit=unit).update(average_rating=Decimal("5.00"))

        reconcile_units()

        assert UnitRatingAggregate.all_objects.get(unit=unit).average_rating == (Decimal("5.00"))

    def test_the_scheduled_job_counts_drifted_fields(self, tenancy_factory, unit_factory, tenant):
        unit = unit_factory()
        create_review(stay(tenancy_factory, unit, tenant), rating=4)
        recompute_all()

        assert reconcile_rating_aggregates() == 0

        UnitRatingAggregate.all_objects.filter(unit=unit).update(review_count=7)

        assert reconcile_rating_aggregates() >= 1

    def test_a_stale_aggregate_is_the_designed_failure_mode(
        self, tenancy_factory, unit_factory, tenant
    ):
        """Between a review landing and the job running, the number is old.

        That is the trade the aggregate tables were chosen for: a stale cached
        number found by the reconciler, rather than a duplicated foreign key
        that silently attributes reviews to the wrong building.
        """
        unit = unit_factory()
        recompute_unit(unit.pk)
        create_review(stay(tenancy_factory, unit, tenant), rating=4)

        stale = UnitRatingAggregate.all_objects.get(unit=unit)
        assert stale.average_rating is None

        fresh = summarise_unit(unit.pk)
        assert fresh.average_rating == Decimal("4.00")

    def test_summaries_are_computed_without_writing(self, property_factory):
        """So the reconciler can compare against what is stored without
        touching it."""
        prop = property_factory()

        summarise_property(prop.pk)

        assert not PropertyRatingAggregate.all_objects.filter(property_reviewed=prop).exists()

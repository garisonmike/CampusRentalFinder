"""
Tenancy claims and the exclusion constraint (ADR-004).

The claimed path: stays the platform did not witness. The tenant initiates, so
the abuse surface is on them, and the three controls are a partial unique
constraint, a rate limit, and a range exclusion the database enforces.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from config.tenancy import TenantScopeError
from tenancies.constants import ClaimStatus, ConfirmationSource, TenancyStatus
from tenancies.models import Tenancy, TenancyClaim
from tenancies.services import (
    ClaimRateLimitExceededError,
    confirm_claim,
    create_claim,
)

pytestmark = pytest.mark.django_db


def a_stay(unit, claimant, *, start_offset: int, days: int, **kwargs):
    """Create a claim for a stay ending ``start_offset`` days ago."""
    start = dt.date.today() - dt.timedelta(days=start_offset)
    return create_claim(
        unit=unit,
        claimant=claimant,
        start_date=start,
        end_date=start + dt.timedelta(days=days),
        monthly_rent_kes=Decimal("9500.00"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


class TestTenancyClaim:
    def test_a_claim_opens_a_confirmation_window(self, unit_factory, tenant):
        claim = a_stay(unit_factory(), tenant, start_offset=200, days=180)

        assert claim.status == ClaimStatus.PENDING
        assert claim.confirmation_deadline > timezone.now()

    def test_the_window_is_the_configured_length(self, unit_factory, tenant):
        now = timezone.now()
        claim = create_claim(
            unit=unit_factory(),
            claimant=tenant,
            start_date=dt.date.today() - dt.timedelta(days=100),
            end_date=dt.date.today() - dt.timedelta(days=10),
            monthly_rent_kes=Decimal("9500.00"),
            now=now,
        )

        expected = now + dt.timedelta(days=settings.TENANCY_CONFIRMATION_WINDOW_DAYS)
        assert claim.confirmation_deadline == expected

    def test_only_one_open_claim_per_unit_and_claimant(self, unit_factory, tenant):
        unit = unit_factory()
        a_stay(unit, tenant, start_offset=400, days=180)

        with pytest.raises(IntegrityError), transaction.atomic():
            a_stay(unit, tenant, start_offset=200, days=100)

    def test_a_resolved_claim_leaves_room_for_another(self, unit_factory, tenant, landlord):
        """A student who returns to the same block can claim the second stay."""
        unit = unit_factory()
        first = a_stay(unit, tenant, start_offset=400, days=100)
        confirm_claim(first, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        second = a_stay(unit, tenant, start_offset=200, days=100)

        assert second.pk != first.pk

    def test_end_date_cannot_precede_start(self, unit_factory, tenant):
        with pytest.raises(IntegrityError), transaction.atomic():
            create_claim(
                unit=unit_factory(),
                claimant=tenant,
                start_date=dt.date.today(),
                end_date=dt.date.today() - dt.timedelta(days=1),
                monthly_rent_kes=Decimal("9500.00"),
            )

    def test_a_terminal_status_requires_a_resolution_time(self, tenancy_claim_factory):
        claim = tenancy_claim_factory()
        claim.status = ClaimStatus.CONFIRMED

        with pytest.raises(IntegrityError), transaction.atomic():
            claim.save()

    def test_is_retrospective_defaults_false(self, tenancy_claim_factory):
        assert tenancy_claim_factory().is_retrospective is False

    def test_a_retrospective_claim_uses_the_same_machinery(self, unit_factory, tenant, landlord):
        """Launch seeding runs through claims with no lower bar (ADR-004).

        Same window, same statuses, same confirmation. The flag is for
        analytics and the operations queue only — never display, never
        weighting — so nothing about the record differs.
        """
        seeded = a_stay(unit_factory(), tenant, start_offset=700, days=270, is_retrospective=True)

        assert seeded.is_retrospective is True
        assert seeded.status == ClaimStatus.PENDING
        assert seeded.confirmation_deadline > timezone.now()

        tenancy = confirm_claim(seeded, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        # The resulting tenancy carries no marker at all: a retrospective stay
        # is exactly as verified as any other, because the mechanism is
        # identical.
        assert not hasattr(tenancy, "is_retrospective")
        assert tenancy.confirmation_source == ConfirmationSource.LANDLORD


class TestClaimRateLimit:
    def test_a_user_may_raise_claims_up_to_the_cap(self, unit_factory, tenant):
        with override_settings(MAX_CLAIMS_PER_USER_PER_MONTH=3):
            for index in range(3):
                a_stay(unit_factory(), tenant, start_offset=900 - index * 200, days=100)

    def test_the_cap_refuses_with_an_explanation(self, unit_factory, tenant):
        """Refused, not silently dropped: a genuine flood needs somewhere to go."""
        with override_settings(MAX_CLAIMS_PER_USER_PER_MONTH=2):
            a_stay(unit_factory(), tenant, start_offset=900, days=100)
            a_stay(unit_factory(), tenant, start_offset=700, days=100)

            with pytest.raises(ClaimRateLimitExceededError) as caught:
                a_stay(unit_factory(), tenant, start_offset=500, days=100)

        assert "support" in str(caught.value).lower()

    def test_the_cap_is_per_user(self, unit_factory, tenant, student_profile):
        with override_settings(MAX_CLAIMS_PER_USER_PER_MONTH=1):
            a_stay(unit_factory(), tenant, start_offset=900, days=100)

            # A different person is unaffected.
            a_stay(unit_factory(), student_profile.user, start_offset=900, days=100)

    def test_the_cap_is_a_rolling_window(self, unit_factory, tenant):
        old = a_stay(unit_factory(), tenant, start_offset=900, days=100)
        TenancyClaim.all_objects.filter(pk=old.pk).update(
            created_at=timezone.now() - dt.timedelta(days=40)
        )

        with override_settings(MAX_CLAIMS_PER_USER_PER_MONTH=1):
            a_stay(unit_factory(), tenant, start_offset=700, days=100)


# ---------------------------------------------------------------------------
# The exclusion constraint
# ---------------------------------------------------------------------------


class TestOverlapExclusion:
    """`(unit, tenant, daterange)` where status='active'.

    Scoped per unit **and per tenant**, not per unit alone. A `Unit` row can
    represent a pool — forty identical bedsitters in a block are one row with
    `total_count=40` — so a per-unit-only exclusion would let exactly one
    student occupy the whole building.
    """

    def confirmed(self, unit, tenant, landlord, *, start_offset: int, days: int):
        claim = a_stay(unit, tenant, start_offset=start_offset, days=days)
        return confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

    def test_one_person_cannot_hold_the_same_unit_twice_over_the_same_dates(
        self, unit_factory, tenant, landlord
    ):
        unit = unit_factory()
        self.confirmed(unit, tenant, landlord, start_offset=400, days=200)

        overlapping = a_stay(unit, tenant, start_offset=300, days=200)

        with pytest.raises(IntegrityError), transaction.atomic():
            confirm_claim(overlapping, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

    def test_two_students_may_hold_the_same_pooled_unit_at_once(
        self, unit_factory, tenant, student_profile, landlord
    ):
        """The case a per-unit-only exclusion would have broken.

        Forty bedsitters are one Unit row; two students in different physical
        rooms share it, and both stays are real.
        """
        pooled = unit_factory(total_count=40, vacant_count=38)

        first = self.confirmed(pooled, tenant, landlord, start_offset=400, days=200)
        second = self.confirmed(pooled, student_profile.user, landlord, start_offset=400, days=200)

        assert first.pk != second.pk
        assert Tenancy.all_objects.filter(unit=pooled, status=TenancyStatus.ACTIVE).count() == 2

    def test_consecutive_stays_by_the_same_person_are_allowed(self, unit_factory, tenant, landlord):
        """Moving out and back in later is not an overlap."""
        unit = unit_factory()
        self.confirmed(unit, tenant, landlord, start_offset=600, days=180)
        later = self.confirmed(unit, tenant, landlord, start_offset=300, days=180)

        assert later.pk is not None

    def test_an_ongoing_stay_blocks_a_later_overlapping_one(self, unit_factory, tenant, landlord):
        """A null end_date coalesces to infinity, so "still living there"
        overlaps everything after it."""
        unit = unit_factory()
        ongoing = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=dt.date.today() - dt.timedelta(days=100),
            end_date=None,
            monthly_rent_kes=Decimal("9500.00"),
        )
        confirm_claim(ongoing, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        later = a_stay(unit, tenant, start_offset=30, days=10)

        with pytest.raises(IntegrityError), transaction.atomic():
            confirm_claim(later, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

    def test_an_ended_tenancy_still_blocks(self, unit_factory, tenant, landlord):
        """The exclusion is UNCONDITIONAL, and this test used to assert the
        opposite.

        The old version read "an ended stay is history, and history should not
        block a correction or a re-entry", which sounds reasonable and is the
        vulnerability. Ended stays are exactly what retrospective seeding
        creates, so a status='active' condition switched the protection off for
        precisely the rows that needed it.
        """
        unit = unit_factory()
        first = self.confirmed(unit, tenant, landlord, start_offset=400, days=200)
        Tenancy.all_objects.filter(pk=first.pk).update(status=TenancyStatus.ENDED)

        overlapping = a_stay(unit, tenant, start_offset=300, days=200)

        with pytest.raises(IntegrityError), transaction.atomic():
            confirm_claim(overlapping, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

    def test_the_seeding_attack_is_refused_by_the_database(self, unit_factory, tenant, landlord):
        """Jan-Jun and Feb-Aug, same tenant, same unit, both retrospective.

        The launch-seeding path is the one place a student supplies both ends
        of a historical stay, so it is the one place overlapping claims are
        cheap to manufacture. Each would auto-confirm on landlord silence, and
        Review is OneToOne to Tenancy -- so two overlapping tenancies are two
        reviews of a single stay, inflating the unit rating, the review count
        and the visible review list. Only the property aggregate would survive,
        because it dedupes per (property, tenant).

        Refused in the schema, not by a serializer: the seeding path may be run
        by a management command or a data import that never touches one.
        """
        unit = unit_factory()
        january = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=dt.date(2024, 1, 1),
            end_date=dt.date(2024, 6, 30),
            monthly_rent_kes=Decimal("9500.00"),
            is_retrospective=True,
        )
        confirm_claim(january, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        february = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=dt.date(2024, 2, 1),
            end_date=dt.date(2024, 8, 31),
            monthly_rent_kes=Decimal("9500.00"),
            is_retrospective=True,
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            confirm_claim(february, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        assert Tenancy.all_objects.filter(unit=unit, tenant=tenant).count() == 1

    def test_the_seeding_attack_is_refused_after_the_stay_is_marked_ended(
        self, unit_factory, tenant, landlord
    ):
        """The same attack with the first stay ENDED.

        This is the form that actually worked before the fix. Nothing in
        production code sets ENDED yet, which is the only reason the plain
        version above was already refused -- confirm_claim marks every stay
        ACTIVE regardless of its end date. The first lifecycle job to mark past
        stays ended would have armed it.
        """
        unit = unit_factory()
        january = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=dt.date(2024, 1, 1),
            end_date=dt.date(2024, 6, 30),
            monthly_rent_kes=Decimal("9500.00"),
            is_retrospective=True,
        )
        first = confirm_claim(january, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)
        Tenancy.all_objects.filter(pk=first.pk).update(status=TenancyStatus.ENDED)

        february = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=dt.date(2024, 2, 1),
            end_date=dt.date(2024, 8, 31),
            monthly_rent_kes=Decimal("9500.00"),
            is_retrospective=True,
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            confirm_claim(february, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

    def test_a_non_overlapping_return_in_a_later_year_is_still_legal(
        self, unit_factory, tenant, landlord
    ):
        """A student returning to the same block a year later is a real case
        and produces a legitimate second review. The fix must not cost this."""
        unit = unit_factory()
        first = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=dt.date(2023, 1, 1),
            end_date=dt.date(2023, 6, 30),
            monthly_rent_kes=Decimal("9500.00"),
            is_retrospective=True,
        )
        confirm_claim(first, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        later = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=dt.date(2024, 9, 1),
            end_date=dt.date(2025, 6, 30),
            monthly_rent_kes=Decimal("9500.00"),
            is_retrospective=True,
        )
        second = confirm_claim(later, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        assert second.pk is not None
        assert Tenancy.all_objects.filter(unit=unit, tenant=tenant).count() == 2

    def test_a_rejected_claim_blocks_nothing(self, unit_factory, tenant, landlord):
        """A withdrawn or rejected claim produces no Tenancy at all, so there
        is no row for the constraint to consider. Asserted rather than assumed,
        because "every row is a confirmed stay" is the premise that makes the
        unconditional exclusion safe.
        """
        unit = unit_factory()
        rejected = a_stay(unit, tenant, start_offset=400, days=200)
        rejected.status = ClaimStatus.WITHDRAWN
        rejected.resolved_at = timezone.now()
        rejected.save(update_fields=["status", "resolved_at", "updated_at"])

        assert Tenancy.all_objects.filter(unit=unit, tenant=tenant).count() == 0

        overlapping = a_stay(unit, tenant, start_offset=300, days=200)
        confirmed = confirm_claim(
            overlapping, source=ConfirmationSource.LANDLORD, confirmed_by=landlord
        )

        assert confirmed.pk is not None

    def test_the_same_person_may_hold_two_different_units_at_once(
        self, unit_factory, property_factory, tenant, landlord
    ):
        """Subletting, or a mid-month move. Unusual but not impossible, and the
        database should not be the thing deciding it is disallowed."""
        prop = property_factory()
        first_unit = unit_factory(property=prop, label="A1")
        second_unit = unit_factory(property=prop, label="A2")

        self.confirmed(first_unit, tenant, landlord, start_offset=400, days=200)
        second = self.confirmed(second_unit, tenant, landlord, start_offset=400, days=200)

        assert second.pk is not None


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


class TestConfirmingAClaim:
    def test_confirmation_produces_a_claim_sourced_tenancy(self, unit_factory, tenant, landlord):
        claim = a_stay(unit_factory(), tenant, start_offset=300, days=180)

        tenancy = confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        assert tenancy.claim == claim
        assert tenancy.application is None
        assert tenancy.is_witnessed() is False

    def test_an_auto_confirmation_has_no_actor(self, unit_factory, tenant):
        """Silence is a signal, not a person."""
        claim = a_stay(unit_factory(), tenant, start_offset=300, days=180)

        tenancy = confirm_claim(claim, source=ConfirmationSource.AUTO)

        assert tenancy.confirmed_by is None
        assert tenancy.confirmation_source == ConfirmationSource.AUTO

    def test_the_claim_is_marked_resolved(self, unit_factory, tenant, landlord):
        claim = a_stay(unit_factory(), tenant, start_offset=300, days=180)

        confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)
        claim.refresh_from_db()

        assert claim.status == ClaimStatus.CONFIRMED
        assert claim.resolved_at is not None

    def test_the_claim_cannot_be_deleted_out_from_under_the_tenancy(
        self, unit_factory, tenant, landlord
    ):
        """PROTECT: the review's dispute annotation is derived from this record."""
        claim = a_stay(unit_factory(), tenant, start_offset=300, days=180)
        confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        with pytest.raises(IntegrityError), transaction.atomic():
            claim.delete()

    def test_a_claim_sourced_tenancy_must_not_also_name_an_application(
        self, unit_factory, tenant, landlord, application_factory
    ):
        claim = a_stay(unit_factory(), tenant, start_offset=300, days=180)
        tenancy = confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)
        tenancy.application = application_factory()

        with pytest.raises(IntegrityError), transaction.atomic():
            tenancy.save()


class TestClaimScoping:
    def test_unqualified_queries_raise(self, tenancy_claim_factory):
        tenancy_claim_factory()

        with pytest.raises(TenantScopeError):
            list(TenancyClaim.objects.all())

    def test_claims_scope_through_the_unit(
        self,
        tenancy_claim_factory,
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
        claim = tenancy_claim_factory(unit=unit_factory(property=prop))

        assert claim in TenancyClaim.objects.for_tenant(university)
        assert claim not in TenancyClaim.objects.for_tenant(university_factory())

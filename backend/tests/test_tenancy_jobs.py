"""
The two deadline jobs (ADR-004 §3, docs/OPERATIONS.md §1 and §2).

Both fail silently when the worker stops, and both failures *restore the bug
they exist to fix*: claims sit pending for ever and it looks exactly like
landlords not confirming, or escalated disputes accumulate and the platform
silently vetoes reviews on behalf of the landlords who disputed them.

So these tests check the direction of each default as much as the mechanism.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.conf import settings
from django.utils import timezone

from tenancies.constants import (
    ClaimStatus,
    ConfirmationSource,
    DisputeReason,
    EscalationReason,
)
from tenancies.jobs import (
    auto_confirm_claim,
    auto_resolve_dispute,
    overdue_claims,
    overdue_disputes,
    sweep_overdue_claims,
    sweep_overdue_disputes,
)
from tenancies.models import Tenancy, TenancyClaim
from tenancies.services import confirm_claim, create_claim, raise_dispute

pytestmark = pytest.mark.django_db


LONG_ENOUGH = settings.REVIEW_MINIMUM_STAY_DAYS + 15


@pytest.fixture
def a_claim(unit_factory, tenant):
    def build(claimant=None, **kwargs):
        start = dt.date.today() - dt.timedelta(days=200)
        return create_claim(
            unit=unit_factory(),
            claimant=claimant or tenant,
            start_date=start,
            end_date=start + dt.timedelta(days=LONG_ENOUGH),
            monthly_rent_kes=Decimal("9500.00"),
            **kwargs,
        )

    return build


def make_overdue(claim, *, days: int = 1):
    """Push a claim's confirmation deadline into the past."""
    TenancyClaim.all_objects.filter(pk=claim.pk).update(
        confirmation_deadline=timezone.now() - dt.timedelta(days=days)
    )
    claim.refresh_from_db()
    return claim


# ---------------------------------------------------------------------------
# Claim auto-confirmation
# ---------------------------------------------------------------------------


class TestClaimAutoConfirmation:
    def test_a_claim_past_its_window_confirms(self, a_claim):
        """Landlord silence is a signal, not a veto. The failure of this job
        restores exactly the bug ADR-004 exists to remove."""
        claim = make_overdue(a_claim())

        auto_confirm_claim(claim.pk)
        claim.refresh_from_db()

        assert claim.status == ClaimStatus.CONFIRMED
        assert Tenancy.all_objects.count() == 1

    def test_the_confirmation_names_no_actor(self, a_claim):
        """Silence has no author, and the constraint enforces that."""
        claim = make_overdue(a_claim())

        auto_confirm_claim(claim.pk)
        tenancy = Tenancy.all_objects.get()

        assert tenancy.confirmation_source == ConfirmationSource.AUTO
        assert tenancy.confirmed_by is None

    def test_a_claim_still_inside_its_window_is_untouched(self, a_claim):
        claim = a_claim()

        assert claim not in overdue_claims()
        assert sweep_overdue_claims() == 0

    def test_a_confirmed_claim_is_not_swept_again(self, a_claim, landlord):
        claim = make_overdue(a_claim())
        confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        assert sweep_overdue_claims() == 0

    def test_a_disputed_claim_is_not_auto_confirmed(self, a_claim, landlord):
        """Disputing is the landlord's answer. The timeout is for silence."""
        claim = make_overdue(a_claim())
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)

        assert sweep_overdue_claims() == 0

    def test_the_job_tolerates_a_deleted_row(self, a_claim):
        """Jobs must be idempotent and survive the row being gone."""
        claim = make_overdue(a_claim())
        claim_id = claim.pk
        TenancyClaim.all_objects.filter(pk=claim_id).delete()

        auto_confirm_claim(claim_id)  # must not raise

    def test_the_job_yields_to_a_human_who_got_there_first(self, a_claim, landlord):
        """The sweep enqueues, then a landlord confirms before the worker runs.
        The race is expected and the human action wins."""
        claim = make_overdue(a_claim())
        confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        auto_confirm_claim(claim.pk)

        assert Tenancy.all_objects.get().confirmation_source == ConfirmationSource.LANDLORD

    def test_the_sweep_drains_oldest_first(self, a_claim, tenant, student_profile):
        """A backlog must drain in the order the deadlines passed, not starve
        the earliest claims."""
        older = make_overdue(a_claim(), days=10)
        newer = make_overdue(a_claim(claimant=student_profile.user), days=1)

        ordered = list(
            overdue_claims().order_by("confirmation_deadline").values_list("pk", flat=True)
        )

        assert ordered == [older.pk, newer.pk]

    def test_the_sweep_respects_its_limit(self, a_claim, tenant, student_profile):
        make_overdue(a_claim(), days=10)
        make_overdue(a_claim(claimant=student_profile.user), days=5)

        assert sweep_overdue_claims(limit=1) == 1


# ---------------------------------------------------------------------------
# Dispute auto-resolution
# ---------------------------------------------------------------------------


class TestDisputeAutoResolution:
    def escalated(self, claim, landlord, *, overdue_days: int | None = None):
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)
        claim.refresh_from_db()
        if overdue_days is not None:
            TenancyClaim.all_objects.filter(pk=claim.pk).update(
                escalation_deadline=timezone.now() - dt.timedelta(days=overdue_days)
            )
            claim.refresh_from_db()
        return claim

    def test_an_overdue_dispute_resolves_in_the_tenants_favour(self, a_claim, landlord):
        """The deadline binds the PLATFORM. Missing it is our failure, and the
        default on our own failure must favour the party with less power."""
        claim = self.escalated(a_claim(), landlord, overdue_days=1)

        auto_resolve_dispute(claim.pk)
        claim.refresh_from_db()

        assert claim.status == ClaimStatus.CONFIRMED
        assert Tenancy.all_objects.count() == 1

    def test_the_resolution_names_no_actor(self, a_claim, landlord):
        claim = self.escalated(a_claim(), landlord, overdue_days=1)

        auto_resolve_dispute(claim.pk)
        tenancy = Tenancy.all_objects.get()

        assert tenancy.confirmation_source == ConfirmationSource.DISPUTE_TIMEOUT
        assert tenancy.confirmed_by is None

    def test_the_dispute_is_carried_forward_as_a_fact(self, a_claim, landlord):
        """The review gets a neutral annotation, derived from this. Never a
        discredit — a landlord who disputes honestly and one who disputes
        tactically produce the same annotation, which is exactly why it must
        not read as a verdict."""
        claim = self.escalated(a_claim(), landlord, overdue_days=1)

        auto_resolve_dispute(claim.pk)

        assert Tenancy.all_objects.get().was_disputed is True

    def test_a_dispute_inside_its_window_is_untouched(self, a_claim, landlord):
        self.escalated(a_claim(), landlord)

        assert sweep_overdue_disputes() == 0

    def test_a_dispute_still_between_the_parties_is_not_swept(self, a_claim, landlord):
        """Only escalated claims have an escalation deadline. A dates dispute
        the parties are still working has its own path."""
        claim = a_claim()
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=claim.start_date,
            proposed_end_date=claim.end_date - dt.timedelta(days=2),
        )

        assert sweep_overdue_disputes() == 0

    def test_the_job_yields_to_an_admin_who_got_there_first(self, a_claim, landlord, staff_user):
        from tenancies.services import resolve_escalation

        claim = self.escalated(a_claim(), landlord, overdue_days=1)
        resolve_escalation(claim, resolved_by=staff_user, uphold_claim=True)

        auto_resolve_dispute(claim.pk)

        assert Tenancy.all_objects.get().confirmation_source == ConfirmationSource.ADMIN

    def test_the_job_tolerates_a_deleted_row(self, a_claim, landlord):
        claim = self.escalated(a_claim(), landlord, overdue_days=1)
        claim_id = claim.pk
        TenancyClaim.all_objects.filter(pk=claim_id).delete()

        auto_resolve_dispute(claim_id)  # must not raise

    def test_the_sweep_drains_oldest_first(self, a_claim, student_profile, landlord):
        older = self.escalated(a_claim(), landlord, overdue_days=20)
        newer = self.escalated(a_claim(claimant=student_profile.user), landlord, overdue_days=2)

        ordered = list(
            overdue_disputes().order_by("escalation_deadline").values_list("pk", flat=True)
        )

        assert ordered == [older.pk, newer.pk]


# ---------------------------------------------------------------------------
# The NULL-ordering trap
# ---------------------------------------------------------------------------


class TestSweepOrdering:
    """PostgreSQL sorts NULLs LAST in an ascending order.

    `properties.jobs.route_stale_distances` ordered a nullable `routed_at`
    ascending and therefore handed back rows that already had an answer, while
    the never-routed ones waited for ever — a backlog that grows while the job
    reports success. These sweeps avoid it by construction rather than by
    ordering hints.
    """

    def test_a_claim_with_no_deadline_cannot_exist(self, a_claim):
        """confirmation_deadline is NOT NULL, so the ascending sweep has no
        NULLs to strand."""
        field = TenancyClaim._meta.get_field("confirmation_deadline")

        assert field.null is False

    def test_a_null_escalation_deadline_is_excluded_by_the_filter(self, a_claim):
        """escalation_deadline IS nullable — null until the claim escalates.

        The `__lte` filter excludes nulls regardless of where they would sort,
        so the ordering is not what makes this safe. That is worth asserting,
        because the safety would otherwise look like a coincidence.
        """
        claim = a_claim()

        assert claim.escalation_deadline is None
        assert claim not in overdue_disputes()

    def test_a_pending_claim_never_appears_in_the_dispute_sweep(self, a_claim):
        make_overdue(a_claim())

        assert sweep_overdue_disputes() == 0

    def test_an_escalated_claim_never_appears_in_the_confirmation_sweep(self, a_claim, landlord):
        claim = make_overdue(a_claim())
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)

        assert claim not in overdue_claims()


class TestOverdueMetrics:
    """The alerts read the OLDEST row, not the count (docs/OPERATIONS.md).

    A count tells you the queue is big — which it may legitimately be. The age
    of the oldest overdue row tells you whether anything has been abandoned,
    which is the failure that matters when a worker dies quietly.
    """

    def test_the_sweep_reports_nothing_when_the_queue_is_empty(self, a_claim):
        a_claim()

        assert sweep_overdue_claims() == 0
        assert sweep_overdue_disputes() == 0

    def test_an_escalated_claim_carries_a_deadline_from_the_moment_it_queues(
        self, a_claim, landlord
    ):
        """A queue entry with no deadline is the indefinite block again, and
        the check constraint forbids it."""
        claim = a_claim()
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)
        claim.refresh_from_db()

        assert claim.escalation_deadline is not None
        assert claim.escalation_reason == EscalationReason.IDENTITY_DISPUTED

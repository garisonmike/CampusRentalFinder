"""
The dispute state machine (ADR-004 §2).

Disputing is cheap, so it will not be rare, and every disputed claim that
reaches a human lands on a team of one. Everything here exists to keep the
admin queue bounded: disputes are typed so most can be routed without a person,
transitions come from one table so an unroutable state cannot be built, and the
timeout binds the platform rather than the tenant.

The test that matters most is
:meth:`TestCorrectionDefeatsReview.test_a_review_defeating_correction_escalates_even_when_accepted`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from tenancies.constants import (
    DISPUTE_TRANSITIONS,
    ClaimStatus,
    ConfirmationSource,
    DisputeReason,
    EscalationReason,
    TenancyStatus,
)
from tenancies.models import Tenancy, TenancyClaim
from tenancies.services import (
    DisputeNotOpenError,
    UnroutableDisputeError,
    accept_correction,
    accept_counter,
    confirm_claim,
    counter_correction,
    create_claim,
    escalate,
    raise_dispute,
    reject_counter,
    resolve_escalation,
)

pytestmark = pytest.mark.django_db


LONG_ENOUGH = settings.REVIEW_MINIMUM_STAY_DAYS + 15  # 45 days
TOO_SHORT = settings.REVIEW_MINIMUM_STAY_DAYS - 10  # 20 days


@pytest.fixture
def claim(unit_factory, tenant):
    """A pending claim for a 45-day stay: comfortably reviewable."""
    start = dt.date.today() - dt.timedelta(days=200)
    return create_claim(
        unit=unit_factory(),
        claimant=tenant,
        start_date=start,
        end_date=start + dt.timedelta(days=LONG_ENOUGH),
        monthly_rent_kes=Decimal("9500.00"),
    )


# ---------------------------------------------------------------------------
# The transition table
# ---------------------------------------------------------------------------


class TestDisputeTransitions:
    """ADR-004 §2c: one table, and nothing else encodes a transition."""

    def test_every_dispute_reason_has_a_table_entry(self):
        """A reason with no entry could be raised and never routed."""
        for reason in DisputeReason.values:
            assert reason in DISPUTE_TRANSITIONS

    def test_every_dispute_reason_has_an_escalation_path(self):
        """Otherwise it sits in `disputed` for ever — the indefinite block the
        timeout was introduced to remove."""
        for reason, transition in DISPUTE_TRANSITIONS.items():
            assert transition.escalates_to, f"{reason} can be raised but never routed"

    def test_every_escalation_reason_is_reachable(self):
        """A reason nobody can reach is dead code in a state machine, which is
        its own kind of drift."""
        reachable = {
            escalation
            for transition in DISPUTE_TRANSITIONS.values()
            for escalation in transition.escalates_to
        }

        assert set(EscalationReason.values) == reachable

    def test_an_unknown_reason_is_refused_at_the_point_of_use(self, claim, landlord):
        with pytest.raises(UnroutableDisputeError) as caught:
            raise_dispute(claim, reason="rent_wrong", disputed_by=landlord)

        assert "DISPUTE_TRANSITIONS" in str(caught.value)

    def test_the_database_refuses_a_pairing_the_table_forbids(self, claim, landlord):
        """The constraint is generated from the table, so it cannot drift."""
        raise_dispute(
            claim,
            reason=DisputeReason.NEVER_TENANTED,
            disputed_by=landlord,
        )
        claim.refresh_from_db()
        claim.escalation_reason = EscalationReason.COUNTER_UNRESOLVED

        with pytest.raises(IntegrityError), transaction.atomic():
            claim.save()

    def test_the_service_refuses_it_too(self, claim, landlord):
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)
        claim.refresh_from_db()

        with pytest.raises(UnroutableDisputeError):
            escalate(claim, reason=EscalationReason.DUPLICATE_UNMATCHED)


# ---------------------------------------------------------------------------
# Raising a dispute
# ---------------------------------------------------------------------------


class TestRaisingADispute:
    def test_a_dispute_must_be_typed_and_attributed(self, tenancy_claim_factory):
        """An untyped dispute cannot be routed, so it could only go to a human."""
        claim = tenancy_claim_factory()
        claim.status = ClaimStatus.DISPUTED

        with pytest.raises(IntegrityError), transaction.atomic():
            claim.save()

    def test_the_note_is_context_never_a_substitute(self, claim, landlord):
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            note="He moved out in March, not May.",
            proposed_start_date=claim.start_date,
            proposed_end_date=claim.start_date + dt.timedelta(days=LONG_ENOUGH - 5),
        )
        claim.refresh_from_db()

        assert claim.dispute_note
        assert claim.dispute_reason == DisputeReason.DATES_INCORRECT

    def test_a_dates_dispute_must_state_its_dates(self, claim, landlord):
        with pytest.raises(Exception) as caught:
            raise_dispute(claim, reason=DisputeReason.DATES_INCORRECT, disputed_by=landlord)

        assert "proposed_start_date" in str(caught.value)

    def test_only_a_pending_claim_can_be_disputed(self, claim, landlord):
        confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        with pytest.raises(DisputeNotOpenError):
            raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)

    def test_dispute_reason_is_never_rewritten(self, claim, landlord):
        """ADR-004 §2a: it records what the disputer actually claimed.

        The admin receiving "this person never lived here" for a case where
        both parties agree the stay happened and disagree by a fortnight is
        exactly the confusion two fields exist to prevent.
        """
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=claim.start_date,
            proposed_end_date=claim.start_date + dt.timedelta(days=TOO_SHORT),
        )
        claim.refresh_from_db()
        accept_correction(claim)
        claim.refresh_from_db()

        assert claim.dispute_reason == DisputeReason.DATES_INCORRECT
        assert claim.escalation_reason == EscalationReason.CORRECTION_DEFEATS_REVIEW


class TestNeverTenanted:
    def test_it_goes_straight_to_an_admin(self, claim, landlord):
        """An identity dispute is not something the parties can settle."""
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)
        claim.refresh_from_db()

        assert claim.status == ClaimStatus.ESCALATED
        assert claim.escalation_reason == EscalationReason.IDENTITY_DISPUTED

    def test_it_opens_a_deadline_that_binds_the_platform(self, claim, landlord):
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)
        claim.refresh_from_db()

        expected = claim.escalated_at + dt.timedelta(days=settings.DISPUTE_RESOLUTION_WINDOW_DAYS)
        assert claim.escalation_deadline == expected


class TestDuplicate:
    def test_a_real_duplicate_resolves_with_no_admin(self, unit_factory, tenant, landlord):
        """A database query, not a judgement call — the same predicate the
        exclusion constraint enforces."""
        unit = unit_factory()
        start = dt.date.today() - dt.timedelta(days=300)
        first = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=start,
            end_date=start + dt.timedelta(days=LONG_ENOUGH),
            monthly_rent_kes=Decimal("9500.00"),
        )
        confirm_claim(first, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        second = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=start + dt.timedelta(days=5),
            end_date=start + dt.timedelta(days=LONG_ENOUGH),
            monthly_rent_kes=Decimal("9500.00"),
        )
        raise_dispute(second, reason=DisputeReason.DUPLICATE, disputed_by=landlord)
        second.refresh_from_db()

        assert second.status == ClaimStatus.WITHDRAWN
        assert second.escalation_reason == ""
        assert second.resolved_at is not None

    def test_a_duplicate_that_matches_nothing_escalates(self, claim, landlord):
        """If no covering tenancy exists the claim is not in fact a duplicate,
        and somebody has to say so."""
        raise_dispute(claim, reason=DisputeReason.DUPLICATE, disputed_by=landlord)
        claim.refresh_from_db()

        assert claim.status == ClaimStatus.ESCALATED
        assert claim.escalation_reason == EscalationReason.DUPLICATE_UNMATCHED

    def test_an_ended_tenancy_still_makes_a_claim_a_duplicate(self, unit_factory, tenant, landlord):
        unit = unit_factory()
        start = dt.date.today() - dt.timedelta(days=300)
        first = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=start,
            end_date=start + dt.timedelta(days=LONG_ENOUGH),
            monthly_rent_kes=Decimal("9500.00"),
        )
        tenancy = confirm_claim(first, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)
        Tenancy.all_objects.filter(pk=tenancy.pk).update(status=TenancyStatus.ENDED)

        second = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=start + dt.timedelta(days=5),
            end_date=start + dt.timedelta(days=LONG_ENOUGH),
            monthly_rent_kes=Decimal("9500.00"),
        )
        raise_dispute(second, reason=DisputeReason.DUPLICATE, disputed_by=landlord)
        second.refresh_from_db()

        # This test used to assert ESCALATED, back when both the exclusion
        # constraint and this predicate filtered on status='active'. The pair
        # were consistent and both wrong: an administrator would have been
        # handed a plainly duplicate stay under the label "no confirmed
        # overlapping tenancy exists", which is the one thing the typed-dispute
        # routing is supposed to prevent.
        assert second.status == ClaimStatus.WITHDRAWN
        assert second.escalation_reason == ""


# ---------------------------------------------------------------------------
# dates_incorrect: the correction exchange
# ---------------------------------------------------------------------------


class TestDateCorrection:
    def disputed(self, claim, landlord, *, days: int):
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=claim.start_date,
            proposed_end_date=claim.start_date + dt.timedelta(days=days),
        )
        claim.refresh_from_db()
        return claim

    def test_it_stays_between_the_parties(self, claim, landlord):
        self.disputed(claim, landlord, days=LONG_ENOUGH - 5)

        assert claim.status == ClaimStatus.DISPUTED
        assert claim.escalation_reason == ""

    def test_acceptance_confirms_with_the_corrected_dates(self, claim, landlord):
        """No admin is involved. This is the common, honest case."""
        original_end = claim.end_date
        self.disputed(claim, landlord, days=LONG_ENOUGH - 5)

        tenancy = accept_correction(claim)

        assert isinstance(tenancy, Tenancy)
        assert tenancy.end_date == original_end - dt.timedelta(days=5)
        assert tenancy.was_disputed is True

    def test_the_tenant_may_counter_once(self, claim, landlord):
        self.disputed(claim, landlord, days=LONG_ENOUGH - 5)

        counter_correction(
            claim,
            start_date=claim.start_date,
            end_date=claim.start_date + dt.timedelta(days=LONG_ENOUGH - 2),
        )

        with pytest.raises(DisputeNotOpenError):
            counter_correction(
                claim,
                start_date=claim.start_date,
                end_date=claim.start_date + dt.timedelta(days=LONG_ENOUGH),
            )

    def test_an_accepted_counter_confirms(self, claim, landlord):
        self.disputed(claim, landlord, days=LONG_ENOUGH - 5)
        counter_end = claim.start_date + dt.timedelta(days=LONG_ENOUGH - 2)
        counter_correction(claim, start_date=claim.start_date, end_date=counter_end)

        tenancy = accept_counter(claim)

        assert tenancy.end_date == counter_end

    def test_a_rejected_counter_escalates_as_a_date_question(self, claim, landlord):
        """Not as an identity question. Two parties who disagree by a fortnight
        about *when* someone lived somewhere both agree that they did, and an
        admin needs completely different evidence for the two."""
        self.disputed(claim, landlord, days=LONG_ENOUGH - 5)
        counter_correction(
            claim,
            start_date=claim.start_date,
            end_date=claim.start_date + dt.timedelta(days=LONG_ENOUGH - 2),
        )

        reject_counter(claim)
        claim.refresh_from_db()

        assert claim.escalation_reason == EscalationReason.COUNTER_UNRESOLVED
        assert claim.dispute_reason == DisputeReason.DATES_INCORRECT

    def test_a_counter_cannot_be_accepted_before_it_exists(self, claim, landlord):
        self.disputed(claim, landlord, days=LONG_ENOUGH - 5)

        with pytest.raises(DisputeNotOpenError):
            accept_counter(claim)


class TestCorrectionDefeatsReview:
    """ADR-004 §2b — the cheapest attack on the whole mechanism.

    Dispute with `dates_incorrect`, propose dates that put the stay under the
    review minimum, and wait for the tenant to accept. It does not read as
    suppression; it reads as a settled disagreement, and no admin ever sees it.
    """

    def disputed_short(self, claim, landlord):
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=claim.start_date,
            proposed_end_date=claim.start_date + dt.timedelta(days=TOO_SHORT),
        )
        claim.refresh_from_db()
        return claim

    def test_a_review_defeating_correction_escalates_even_when_accepted(self, claim, landlord):
        """The adversarial case, end to end.

        A 45-day stay, corrected to 20, and the tenant accepts. Before this
        rule the claim would confirm and the review would be silently
        impossible. After it, an admin sees the case under a label naming
        precisely what is being attempted.
        """
        assert claim.stay_days() == LONG_ENOUGH

        self.disputed_short(claim, landlord)
        result = accept_correction(claim)
        claim.refresh_from_db()

        assert isinstance(result, TenancyClaim)
        assert claim.status == ClaimStatus.ESCALATED
        assert claim.escalation_reason == EscalationReason.CORRECTION_DEFEATS_REVIEW
        assert Tenancy.all_objects.count() == 0

    def test_the_tenants_acceptance_is_recorded_as_evidence(self, claim, landlord):
        """Not as a resolution. The admin will usually find the correction
        honest; the landlord is not presumed to be lying. The point is that
        this correction has a side effect the parties cannot settle privately,
        because one of them may not realise it has one.
        """
        self.disputed_short(claim, landlord)
        accept_correction(claim)
        claim.refresh_from_db()

        assert claim.tenant_accepted_correction_at is not None

    def test_the_claimed_dates_are_not_overwritten_by_the_correction(self, claim, landlord):
        """The admin has to be able to see both versions to decide between them."""
        original_end = claim.end_date
        self.disputed_short(claim, landlord)
        accept_correction(claim)
        claim.refresh_from_db()

        assert claim.end_date == original_end
        assert claim.proposed_end_date == claim.start_date + dt.timedelta(days=TOO_SHORT)

    def test_the_same_guard_applies_to_a_counter(self, claim, landlord):
        """A correction laundered through a counter is still a correction."""
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=claim.start_date,
            proposed_end_date=claim.start_date + dt.timedelta(days=LONG_ENOUGH - 5),
        )
        claim.refresh_from_db()
        counter_correction(
            claim,
            start_date=claim.start_date,
            end_date=claim.start_date + dt.timedelta(days=TOO_SHORT),
        )

        accept_counter(claim)
        claim.refresh_from_db()

        assert claim.status == ClaimStatus.ESCALATED
        assert claim.escalation_reason == EscalationReason.CORRECTION_DEFEATS_REVIEW

    def test_a_correction_at_exactly_the_minimum_settles_normally(self, claim, landlord):
        """The threshold is a floor, not a fence. Escalating a stay that is
        exactly long enough would push honest cases into a queue of one."""
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=claim.start_date,
            proposed_end_date=claim.start_date
            + dt.timedelta(days=settings.REVIEW_MINIMUM_STAY_DAYS),
        )
        claim.refresh_from_db()

        assert isinstance(accept_correction(claim), Tenancy)

    def test_an_ongoing_stay_cannot_be_shortened_this_way(self, claim, landlord):
        """No end date means still living there, and there is nothing to cut."""
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=claim.start_date,
            proposed_end_date=None,
        )
        claim.refresh_from_db()

        assert isinstance(accept_correction(claim), Tenancy)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestResolvingAnEscalation:
    def test_upholding_confirms_the_claim_as_an_admin_decision(self, claim, landlord, staff_user):
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)
        claim.refresh_from_db()

        tenancy = resolve_escalation(claim, resolved_by=staff_user, uphold_claim=True)

        assert tenancy.confirmation_source == ConfirmationSource.ADMIN
        assert tenancy.confirmed_by == staff_user
        assert tenancy.was_disputed is True

    def test_rejecting_closes_the_claim_with_no_tenancy(self, claim, landlord, staff_user):
        raise_dispute(claim, reason=DisputeReason.NEVER_TENANTED, disputed_by=landlord)
        claim.refresh_from_db()

        resolve_escalation(claim, resolved_by=staff_user, uphold_claim=False)
        claim.refresh_from_db()

        assert claim.status == ClaimStatus.WITHDRAWN
        assert Tenancy.all_objects.count() == 0

    def test_only_an_escalated_claim_can_be_resolved(self, claim, staff_user):
        with pytest.raises(DisputeNotOpenError):
            resolve_escalation(claim, resolved_by=staff_user, uphold_claim=True)

    def test_a_disputed_claim_that_confirms_carries_the_fact_forward(self, claim, landlord):
        """was_disputed is read from disputed_at, not the current status: a
        claim that was disputed and then confirmed is no longer *in* a disputed
        status but was still disputed, and the review annotation depends on it.
        """
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=claim.start_date,
            proposed_end_date=claim.end_date - dt.timedelta(days=3),
        )
        claim.refresh_from_db()

        tenancy = accept_correction(claim)
        claim.refresh_from_db()

        assert claim.status == ClaimStatus.CONFIRMED
        assert claim.was_disputed_at_any_point() is True
        assert tenancy.was_disputed is True

    def test_an_undisputed_claim_carries_no_dispute(self, claim, landlord):
        tenancy = confirm_claim(claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord)

        assert tenancy.was_disputed is False


class TestEscalationQueue:
    def test_the_queue_is_filterable_by_what_must_be_decided(
        self, unit_factory, tenant, student_profile, landlord
    ):
        """Working a mixed queue oldest-first is right; working it without
        knowing which kind of question each item is means gathering the wrong
        evidence first (ADR-004 §2a)."""
        for claimant in (tenant, student_profile.user):
            start = dt.date.today() - dt.timedelta(days=200)
            raise_dispute(
                create_claim(
                    unit=unit_factory(),
                    claimant=claimant,
                    start_date=start,
                    end_date=start + dt.timedelta(days=LONG_ENOUGH),
                    monthly_rent_kes=Decimal("9500.00"),
                ),
                reason=DisputeReason.NEVER_TENANTED,
                disputed_by=landlord,
            )

        queue = TenancyClaim.all_objects.filter(
            status=ClaimStatus.ESCALATED,
            escalation_reason=EscalationReason.IDENTITY_DISPUTED,
        ).order_by("escalated_at")

        assert queue.count() == 2
        assert all(claim.escalation_deadline > timezone.now() for claim in queue)

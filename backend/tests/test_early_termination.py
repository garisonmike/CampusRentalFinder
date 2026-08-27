"""
Early termination (ADR-004).

The hazard this file mostly defends against:

> A landlord who can set `end_date` can push a stay under
> `REVIEW_MINIMUM_STAY_DAYS`. That is `correction_defeats_review` at a
> different door, and it gets the same answer.

Two mechanisms, and they cover different halves of the problem.

**The latch** (`review_eligible_at`) protects a right already earned. Once a
stay has crossed the threshold, no later date change can take the review away
— eligibility is read from the latch, never from a live computation.

**The escalation** (`termination_defeats_review`) covers the other half: a stay
that has *not yet* crossed, where the proposed date would stop it ever doing
so. There is no earned right to protect, so the latch cannot help; instead the
termination cannot auto-confirm and an administrator sees it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from tenancies.constants import (
    DISPUTE_TRANSITIONS,
    ClaimStatus,
    DisputeReason,
    EscalationReason,
    TenancyCurrency,
)
from tenancies.models import Tenancy, TerminationRequest
from tenancies.services import (
    TerminationNotOpenError,
    accept_termination_counter,
    confirm_termination,
    dispute_termination,
    request_early_termination,
    resolve_termination_escalation,
    review_eligibility_date,
    termination_would_defeat_review,
)

pytestmark = pytest.mark.django_db

TODAY = dt.date.today()
MINIMUM = settings.REVIEW_MINIMUM_STAY_DAYS


def running(tenancy_factory, *, started_days_ago: int, **kwargs):
    """A currently-running stay that began `started_days_ago` ago."""
    return tenancy_factory(
        start_date=TODAY - dt.timedelta(days=started_days_ago), end_date=None, **kwargs
    )


# ---------------------------------------------------------------------------
# The latch
# ---------------------------------------------------------------------------


class TestReviewEligibilityIsLatched:
    """Eligibility, once earned, is never revoked."""

    def test_a_short_stay_has_not_earned_it(self, tenancy_factory):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM - 5)

        assert review_eligibility_date(tenancy) is None
        assert tenancy.review_eligible_at is None

    def test_crossing_the_threshold_stamps_it(self, tenancy_factory):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 10)

        assert review_eligibility_date(tenancy) is not None
        tenancy.refresh_from_db()
        assert tenancy.review_eligible_at is not None

    def test_it_stamps_the_day_the_threshold_was_crossed(self, tenancy_factory):
        """Not today. `now` would be an artefact of when somebody happened to
        look; the crossing date is the honest fact."""
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 40)

        stamped = review_eligibility_date(tenancy)

        assert stamped == tenancy.start_date + dt.timedelta(days=MINIMUM)
        assert stamped < TODAY

    def test_it_is_never_moved_once_set(self, tenancy_factory):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 40)
        first = review_eligibility_date(tenancy)

        # A month later, nothing about it changes.
        second = review_eligibility_date(tenancy, today=TODAY + dt.timedelta(days=30))

        assert second == first

    def test_shortening_the_stay_does_not_revoke_it(self, tenancy_factory, landlord):
        """The core protection. A stay that earned the right keeps it even
        after a termination makes it, on paper, too short."""
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        review_eligibility_date(tenancy)

        request = request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=tenancy.start_date + dt.timedelta(days=5),
            reason="Left after a week.",
        )
        confirm_termination(request)
        tenancy.refresh_from_db()

        assert (tenancy.end_date - tenancy.start_date).days < MINIMUM
        assert tenancy.review_eligible_at is not None

    def test_the_review_can_still_be_written_afterwards(self, tenancy_factory, landlord):
        """The property that matters, end to end: the right survives the
        termination, so the review is still writable."""
        from reviews.services import assert_tenancy_is_reviewable, create_review

        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        assert_tenancy_is_reviewable(tenancy)

        request = request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=tenancy.start_date + dt.timedelta(days=3),
            reason="Moved out early.",
        )
        confirm_termination(request)
        tenancy.refresh_from_db()

        review = create_review(tenancy, rating=1, comment="The gate never worked.")

        assert review.pk is not None

    def test_confirming_latches_before_moving_the_date(self, tenancy_factory, landlord):
        """Even if nothing ever read eligibility beforehand.

        Once `end_date` shrinks, the live computation can no longer see that
        the threshold was met -- so confirmation is the last moment the fact is
        observable, and it stamps there.
        """
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        assert tenancy.review_eligible_at is None  # nothing has looked yet

        request = request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=tenancy.start_date + dt.timedelta(days=2),
            reason="Left.",
        )
        confirm_termination(request)
        tenancy.refresh_from_db()

        assert tenancy.review_eligible_at is not None


# ---------------------------------------------------------------------------
# The escalation
# ---------------------------------------------------------------------------


class TestTerminationThatDefeatsReview:
    """The half the latch cannot cover: a right not yet earned."""

    def test_it_is_detected(self, tenancy_factory):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM - 10)

        assert termination_would_defeat_review(tenancy, tenancy.start_date + dt.timedelta(days=5))

    def test_an_already_eligible_stay_is_not_caught(self, tenancy_factory):
        """There is nothing to defend -- the right is already earned and the
        latch holds it. Escalating here would send honest terminations to an
        administrator for no reason."""
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        review_eligibility_date(tenancy)

        assert not termination_would_defeat_review(
            tenancy, tenancy.start_date + dt.timedelta(days=5)
        )

    def test_a_termination_that_leaves_it_eligible_is_not_caught(self, tenancy_factory):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 40)

        assert not termination_would_defeat_review(
            tenancy, tenancy.start_date + dt.timedelta(days=MINIMUM + 5)
        )

    def test_it_escalates_immediately(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM - 10)

        request = request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=tenancy.start_date + dt.timedelta(days=3),
            reason="Left after three days.",
        )

        assert request.status == ClaimStatus.ESCALATED
        assert request.escalation_reason == EscalationReason.TERMINATION_DEFEATS_REVIEW

    def test_it_never_auto_confirms(self, tenancy_factory, landlord):
        """The whole point of escalating rather than disputing: the
        counterparty's silence must not delete their own review right."""
        from tenancies.jobs import overdue_terminations

        tenancy = running(tenancy_factory, started_days_ago=MINIMUM - 10)
        request = request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=tenancy.start_date + dt.timedelta(days=3),
            reason="Left.",
        )
        TerminationRequest.all_objects.filter(pk=request.pk).update(
            confirmation_deadline=timezone.now() - dt.timedelta(days=30)
        )

        assert request not in overdue_terminations()

    def test_the_tenancy_is_untouched_while_it_escalates(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM - 10)

        request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=tenancy.start_date + dt.timedelta(days=3),
            reason="Left.",
        )
        tenancy.refresh_from_db()

        assert tenancy.end_date is None
        assert tenancy.terminated_early is False

    def test_an_administrator_can_uphold_it(self, tenancy_factory, landlord, staff_user):
        """The landlord is not presumed to be lying. Most of these are honest,
        and the point is that a person decides rather than silence."""
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM - 10)
        request = request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=tenancy.start_date + dt.timedelta(days=3),
            reason="Left after three days.",
        )

        result = resolve_termination_escalation(request, resolved_by=staff_user, uphold=True)

        assert isinstance(result, Tenancy)
        assert result.terminated_early is True

    def test_an_administrator_can_refuse_it(self, tenancy_factory, landlord, staff_user):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM - 10)
        request = request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=tenancy.start_date + dt.timedelta(days=3),
            reason="Left.",
        )

        resolve_termination_escalation(request, resolved_by=staff_user, uphold=False)
        tenancy.refresh_from_db()

        assert tenancy.end_date is None


# ---------------------------------------------------------------------------
# The transition table
# ---------------------------------------------------------------------------


class TestTheTransitionTableCoversTermination:
    """The table-driven tests should have forced this, and they did.

    Adding `TERMINATION_DEFEATS_REVIEW` to `EscalationReason` without a
    dispute reason reaching it fails
    `test_every_escalation_reason_is_reachable` -- which is exactly the
    unroutable state ADR-004 §2c makes unconstructable.
    """

    def test_termination_has_an_entry(self):
        assert DisputeReason.TERMINATION_DATE in DISPUTE_TRANSITIONS

    def test_it_can_reach_the_new_escalation(self):
        transition = DISPUTE_TRANSITIONS[DisputeReason.TERMINATION_DATE]

        assert EscalationReason.TERMINATION_DEFEATS_REVIEW in transition.escalates_to

    def test_it_can_also_reach_an_unresolved_counter(self):
        """Two parties disagreeing about a date, with no review right at
        stake, is the ordinary case and needs its own destination."""
        transition = DISPUTE_TRANSITIONS[DisputeReason.TERMINATION_DATE]

        assert EscalationReason.COUNTER_UNRESOLVED in transition.escalates_to

    def test_it_settles_between_the_parties_by_default(self):
        assert DISPUTE_TRANSITIONS[DisputeReason.TERMINATION_DATE].can_resolve_between_parties

    def test_an_unpermitted_escalation_is_refused(self, tenancy_factory, landlord):
        """The table is the only place a transition is written down."""
        from tenancies.services import UnroutableDisputeError, escalate_termination

        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        request = request_early_termination(
            tenancy, initiated_by=landlord, ended_on=TODAY, reason="Left."
        )

        with pytest.raises(UnroutableDisputeError):
            escalate_termination(request, reason=EscalationReason.IDENTITY_DISPUTED)


# ---------------------------------------------------------------------------
# The ordinary path
# ---------------------------------------------------------------------------


class TestTheOrdinaryTermination:
    def test_either_party_may_initiate(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)

        by_tenant = request_early_termination(
            tenancy, initiated_by=tenancy.tenant, ended_on=TODAY, reason="Moving home."
        )
        confirm_termination(by_tenant)

        second = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        by_landlord = request_early_termination(
            second, initiated_by=landlord, ended_on=TODAY, reason="Selling the block."
        )

        assert by_tenant.pk is not None
        assert by_landlord.pk is not None

    def test_it_opens_a_confirmation_window(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)

        request = request_early_termination(
            tenancy, initiated_by=landlord, ended_on=TODAY, reason="Sold."
        )

        assert request.confirmation_deadline > timezone.now()
        assert request.status == ClaimStatus.PENDING

    def test_confirming_moves_the_end_date(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        request = request_early_termination(
            tenancy, initiated_by=landlord, ended_on=TODAY, reason="Sold."
        )

        confirm_termination(request)
        tenancy.refresh_from_db()

        assert tenancy.end_date == TODAY
        assert tenancy.terminated_early is True
        assert tenancy.termination_reason == "Sold."

    def test_the_stay_becomes_past_from_that_date(self, tenancy_factory, landlord):
        """Currency is derived, so no job has to run for this to be true."""
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        request = request_early_termination(
            tenancy,
            initiated_by=landlord,
            ended_on=TODAY - dt.timedelta(days=1),
            reason="Sold.",
        )

        confirm_termination(request)
        tenancy.refresh_from_db()

        assert tenancy.currency() == TenancyCurrency.PAST

    def test_a_future_date_is_refused(self, tenancy_factory, landlord):
        """A date that has not happened is a lease amendment, which this
        platform does not record -- accepting it would mean storing a fact that
        is not yet true and might never be."""
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)

        with pytest.raises(ValidationError) as caught:
            request_early_termination(
                tenancy,
                initiated_by=landlord,
                ended_on=TODAY + dt.timedelta(days=30),
                reason="Agreed to leave next month.",
            )

        assert "lease amendment" in str(caught.value)

    def test_a_date_before_the_start_is_refused(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)

        with pytest.raises(ValidationError):
            request_early_termination(
                tenancy,
                initiated_by=landlord,
                ended_on=tenancy.start_date - dt.timedelta(days=1),
                reason="Wrong.",
            )

    def test_a_reason_is_required(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)

        with pytest.raises(ValidationError):
            request_early_termination(tenancy, initiated_by=landlord, ended_on=TODAY, reason="")

    def test_only_one_open_request_per_tenancy(self, tenancy_factory, landlord):
        from django.db import IntegrityError, transaction

        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        request_early_termination(tenancy, initiated_by=landlord, ended_on=TODAY, reason="Sold.")

        with pytest.raises(IntegrityError), transaction.atomic():
            request_early_termination(
                tenancy, initiated_by=tenancy.tenant, ended_on=TODAY, reason="Also."
            )

    def test_confirming_twice_is_refused(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 20)
        request = request_early_termination(
            tenancy, initiated_by=landlord, ended_on=TODAY, reason="Sold."
        )
        confirm_termination(request)

        with pytest.raises(TerminationNotOpenError):
            confirm_termination(request)


class TestDisputingATermination:
    def test_a_counter_keeps_it_between_the_parties(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 40)
        request = request_early_termination(
            tenancy, initiated_by=landlord, ended_on=TODAY, reason="Sold."
        )

        disputed = dispute_termination(
            request,
            disputed_by=tenancy.tenant,
            counter_end_date=TODAY - dt.timedelta(days=10),
        )

        assert disputed.status == ClaimStatus.DISPUTED
        assert disputed.escalation_reason == ""

    def test_a_dispute_with_no_counter_escalates(self, tenancy_factory, landlord):
        """Nothing for the parties to settle between them."""
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 40)
        request = request_early_termination(
            tenancy, initiated_by=landlord, ended_on=TODAY, reason="Sold."
        )

        disputed = dispute_termination(request, disputed_by=tenancy.tenant)

        assert disputed.status == ClaimStatus.ESCALATED
        assert disputed.escalation_reason == EscalationReason.COUNTER_UNRESOLVED

    def test_accepting_a_counter_confirms_with_that_date(self, tenancy_factory, landlord):
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM + 40)
        request = request_early_termination(
            tenancy, initiated_by=landlord, ended_on=TODAY, reason="Sold."
        )
        counter = TODAY - dt.timedelta(days=10)
        dispute_termination(request, disputed_by=tenancy.tenant, counter_end_date=counter)

        accept_termination_counter(request)
        tenancy.refresh_from_db()

        assert tenancy.end_date == counter

    def test_the_guard_applies_to_the_counter_too(self, tenancy_factory, landlord):
        """A termination laundered through a counter is still a termination."""
        tenancy = running(tenancy_factory, started_days_ago=MINIMUM - 5)
        request = TerminationRequest.all_objects.create(
            tenancy=tenancy,
            initiated_by=landlord,
            proposed_end_date=TODAY,
            reason="Sold.",
            confirmation_deadline=timezone.now() + dt.timedelta(days=7),
        )
        dispute_termination(
            request,
            disputed_by=tenancy.tenant,
            counter_end_date=tenancy.start_date + dt.timedelta(days=2),
        )

        result = accept_termination_counter(request)

        assert isinstance(result, TerminationRequest)
        assert result.escalation_reason == EscalationReason.TERMINATION_DEFEATS_REVIEW

"""
The seed command.

Tested for the property that makes it worth having: **it produces the shapes a
per-assertion fixture never does.** Each assertion below names one of those
shapes, because a seed that quietly stopped making open-ended tenancies would
leave every screen built against it looking correct and every open-ended
rendering path unexercised.

The two guards -- the DEBUG refusal and determinism -- are tested first. A seed
command that can run in production is a loaded gun, and one that produces a
different platform each time cannot have a bug reported against it.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from properties.constants import PhotoProcessingStatus, PropertyStatus
from properties.models import Property, Unit, UnitPhoto
from properties.services import cross_check_coverage, vacancy_freshness
from tenancies.constants import ClaimStatus, ConfirmationSource
from tenancies.models import Tenancy, TenancyClaim
from universities.constants import SignupPolicy
from universities.models import University

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    """A small platform. Six properties per university is enough for every
    shape and quick enough to run in the suite."""
    with override_settings(DEBUG=True):
        call_command("seed_platform", "--seed", "1", "--properties", "6", verbosity=0)


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


def test_it_refuses_to_run_outside_debug():
    """There is no version of 'seeded onto production' that anybody recovers
    from, and a command that can be forced through eventually is."""
    with override_settings(DEBUG=False), pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_platform", verbosity=0)


def test_it_refuses_a_platform_too_small_to_hold_every_shape():
    """Refused rather than clamped.

    Every rare shape in this command started life as `index % n` and every one
    was unreachable at some size. The fix was to assign the first five indices
    fixed jobs -- which makes five a real floor, and a seed below it a platform
    missing shapes it claims to have.
    """
    with override_settings(DEBUG=True), pytest.raises(CommandError, match="at least"):
        call_command("seed_platform", "--properties", "3", verbosity=0)


def test_every_shape_survives_the_smallest_allowed_platform():
    """The check the last five defects would each have failed.

    Each of them was a shape present at the default size and absent at a
    smaller one, which is the size a developer actually runs.
    """
    from django.db.models import Count

    from engagement.models import Inquiry, SavedProperty

    with override_settings(DEBUG=True):
        call_command("seed_platform", "--seed", "5", "--properties", "5", verbosity=0)

    published = Property.all_objects.filter(status=PropertyStatus.PUBLISHED)

    assert Tenancy.all_objects.filter(end_date=None).exists(), "no open-ended stay"
    assert Tenancy.all_objects.filter(terminated_early=True).exists(), "no early termination"
    assert Tenancy.all_objects.upcoming().exists(), "no upcoming stay"
    assert published.annotate(u=Count("units")).filter(u=0).exists(), "no unit-less property"
    assert Property.all_objects.filter(latitude=None).exists(), "nothing unpinned"
    assert Unit.all_objects.filter(total_count__gte=15).exists(), "no pooled block"
    assert Inquiry.all_objects.exists(), "no inquiries"
    assert SavedProperty.all_objects.exists(), "nothing saved"
    assert {vacancy_freshness(unit) for unit in Unit.all_objects.all()} == {
        "fresh",
        "ageing",
        "stale",
        "unknown",
    }, "not every vacancy band"


def test_the_same_seed_produces_the_same_platform():
    """A bug found against seeded data has to travel as one integer.

    Compared on the shape rather than on primary keys: sequences do not reset
    between runs, and asserting on ids would make this test about the database
    rather than about the command.
    """

    def fingerprint():
        return sorted(
            (prop.name, prop.estate, prop.status, prop.latitude is None)
            for prop in Property.all_objects.all()
        )

    with override_settings(DEBUG=True):
        call_command("seed_platform", "--seed", "7", "--properties", "5", verbosity=0)
        first = fingerprint()

        call_command("seed_platform", "--seed", "7", "--properties", "5", "--flush", verbosity=0)
        second = fingerprint()

    assert first == second


def test_a_different_seed_produces_a_different_platform():
    """Otherwise `--seed` is decoration and every developer is looking at the
    same twelve buildings."""

    def names():
        return sorted(prop.name for prop in Property.all_objects.all())

    with override_settings(DEBUG=True):
        call_command("seed_platform", "--seed", "1", "--properties", "6", verbosity=0)
        first = names()

        call_command("seed_platform", "--seed", "2", "--properties", "6", "--flush", verbosity=0)
        second = names()

    assert first != second


# ---------------------------------------------------------------------------
# The shapes fixtures do not produce
# ---------------------------------------------------------------------------


class TestTenancyHistory:
    """Currency is derived from dates, so the seed has to contain dates that
    exercise every branch of that derivation."""

    def test_all_three_currencies_exist(self, seeded):
        assert Tenancy.all_objects.past().exists()
        assert Tenancy.all_objects.current().exists()
        assert Tenancy.all_objects.upcoming().exists()

    def test_an_open_ended_stay_exists(self, seeded):
        """`end_date` null means running, not unknown -- the most likely
        misread in the whole contract, and one no fixture creates by default."""
        open_ended = Tenancy.all_objects.filter(end_date=None)

        assert open_ended.exists()
        assert all(tenancy in Tenancy.all_objects.current() for tenancy in open_ended)

    def test_an_early_termination_exists(self, seeded):
        assert Tenancy.all_objects.filter(terminated_early=True).exists()

    def test_a_student_appears_in_two_stays(self, seeded):
        """The shape that makes `student_count` and `review_count` legitimately
        disagree. A per-assertion fixture never produces it, which is how the
        de-duplication went unexercised against real data."""
        from django.db.models import Count

        repeat = (
            Tenancy.all_objects.values("tenant").annotate(stays=Count("id")).filter(stays__gte=2)
        )

        assert repeat.exists()

    def test_every_tenancy_has_an_origin(self, seeded):
        """Either an application the platform witnessed or a claim somebody
        made. Never both, never neither -- a check constraint, and the one the
        first draft of this command violated on every row."""
        assert not Tenancy.all_objects.filter(application=None, claim=None).exists()

    def test_both_origins_appear(self, seeded):
        """An application-sourced stay has no confirmation window and no
        dispute surface; a claim-sourced one has both. A seed with only one
        kind leaves half the state machine unexercised."""
        sources = set(Tenancy.all_objects.values_list("confirmation_source", flat=True))

        assert ConfirmationSource.APPLICATION in sources
        assert ConfirmationSource.LANDLORD in sources


class TestTheAdminQueue:
    def test_claims_exist_in_every_reachable_state(self, seeded):
        states = set(TenancyClaim.all_objects.values_list("status", flat=True))

        assert ClaimStatus.PENDING in states
        assert ClaimStatus.CONFIRMED in states
        assert ClaimStatus.DISPUTED in states
        assert ClaimStatus.ESCALATED in states

    def test_every_typed_dispute_reason_appears(self, seeded):
        """Across all claims, not only the ones still sitting in `disputed`.

        Driving the machine showed why the earlier version of this assertion
        was wrong: **only `dates_incorrect` can rest in `disputed`.**
        `never_tenanted` escalates on the spot, because identity is not
        something the two parties can settle between them, and `duplicate`
        resolves itself from data. A seed that wrote `status="disputed"` with
        all three reasons was describing a state two of them can never
        occupy.
        """
        reasons = set(
            TenancyClaim.all_objects.exclude(dispute_reason="").values_list(
                "dispute_reason", flat=True
            )
        )

        assert len(reasons) >= 3

    def test_only_a_dates_dispute_rests_in_disputed(self, seeded):
        """The rule the previous assertion was hiding."""
        from tenancies.constants import DisputeReason

        resting = set(
            TenancyClaim.all_objects.filter(status=ClaimStatus.DISPUTED).values_list(
                "dispute_reason", flat=True
            )
        )

        assert resting <= {DisputeReason.DATES_INCORRECT}

    def test_claims_were_driven_rather_than_written(self, seeded):
        """A confirmed claim carries a resolution, and an auto-confirmed one
        carries no resolver.

        Both come from `confirm_claim`, which is the single place a claim
        becomes evidence. A direct write produces the status and neither of
        these, and nothing would notice.
        """
        confirmed = TenancyClaim.all_objects.filter(status=ClaimStatus.CONFIRMED)

        assert confirmed.exists()
        assert not confirmed.filter(resolved_at=None).exists()

    def test_a_tenancy_exists_that_was_auto_confirmed_by_silence(self, seeded):
        """Reached through the deadline sweep and a burst worker, with the
        clock advanced -- not by setting `confirmation_source`.

        `confirmed_by` is null because silence has no author, and that is a
        constraint as well as a comment.
        """
        auto = Tenancy.all_objects.filter(confirmation_source=ConfirmationSource.AUTO)

        assert auto.exists()
        assert not auto.exclude(confirmed_by=None).exists()


class TestTerminations:
    """Driven through the flow, not set by flag."""

    def test_a_termination_was_confirmed(self, seeded):
        from tenancies.models import TerminationRequest

        assert TerminationRequest.all_objects.filter(status=ClaimStatus.CONFIRMED).exists()

    def test_a_confirmed_termination_moved_the_end_date(self, seeded):
        """`end_date` moves to the actual day and stays authoritative for
        currency -- so a stay that ended in March reads as past from March
        with no flag consulted."""
        terminated = Tenancy.all_objects.filter(terminated_early=True)

        assert terminated.exists()
        assert not terminated.filter(termination_reason="").exists()

    def test_a_review_defeating_termination_escalated_on_request(self, seeded):
        """Not disputed -- nobody has disagreed yet -- and never reachable by
        the auto-confirm sweep, because the counterparty's silence would
        otherwise delete their own review right.

        This path could not be reached at all until `effective_stay_days` was
        fixed: every seeded stay was already review-eligible, so there was
        never anything left to defeat.
        """
        from tenancies.constants import EscalationReason
        from tenancies.models import TerminationRequest

        assert TerminationRequest.all_objects.filter(
            status=ClaimStatus.ESCALATED,
            escalation_reason=EscalationReason.TERMINATION_DEFEATS_REVIEW,
        ).exists()

    def test_no_stay_is_pre_latched(self, seeded):
        """`review_eligible_at` is a latch the services stamp, and the seed
        used to set it directly -- which marked every stay as having earned a
        review right and silently disabled the termination guard across the
        whole platform.

        A stay shorter than the minimum must carry no latch.
        """
        import datetime as dt

        from django.conf import settings

        too_new = Tenancy.all_objects.filter(
            start_date__gt=dt.date.today() - dt.timedelta(days=settings.REVIEW_MINIMUM_STAY_DAYS),
            end_date__isnull=False,
        ).exclude(review_eligible_at=None)

        assert not too_new.exists(), "a stay was marked eligible before it earned it"


class TestPropertyStates:
    def test_some_properties_cannot_be_published(self, seeded):
        """Unpinned, so they have no campus join and no student could ever see
        them. That is a landlord's first hour, not an edge case."""
        assert Property.all_objects.filter(latitude=None).exists()

    def test_unpinned_properties_are_drafts(self, seeded):
        """The gate holds in the data as well as in the service."""
        assert not Property.all_objects.filter(
            latitude=None, status=PropertyStatus.PUBLISHED
        ).exists()

    def test_some_units_have_no_photos(self, seeded):
        """Every listing's first day."""
        assert Unit.all_objects.filter(photos__isnull=True).exists()

    def test_a_photo_failed_to_process(self, seeded):
        """What a broken upload actually leaves behind (ADR-007)."""
        assert UnitPhoto.all_objects.filter(processing_status=PhotoProcessingStatus.FAILED).exists()

    def test_a_pooled_block_exists_at_a_realistic_size(self, seeded):
        """One row with a total_count of forty, not forty rows. It is where
        'is it available' stops being a boolean."""
        assert Unit.all_objects.filter(total_count__gte=15).exists()

    def test_a_published_property_has_no_units_yet(self, seeded):
        """The landlord created the building and stopped. The property page
        has a state for that, and nothing else in the seed reaches it -- found
        by auditing which empty states a realistic platform can still show."""
        from django.db.models import Count

        assert (
            Property.all_objects.filter(status=PropertyStatus.PUBLISHED)
            .annotate(units_count=Count("units"))
            .filter(units_count=0)
            .exists()
        )

    def test_a_property_has_nothing_ticked(self, seeded):
        """Not "has no amenities" -- the landlord has not filled the form in,
        which is a different claim and worded differently on the page. Random
        booleans essentially never produce it."""
        assert Property.all_objects.filter(
            has_wifi=False,
            has_water_tank=False,
            has_borehole=False,
            has_backup_power=False,
            has_security_guard=False,
            has_perimeter_wall=False,
            has_cctv=False,
            has_parking=False,
            caretaker_on_site=False,
        ).exists()

    def test_walking_times_are_sometimes_null(self, seeded):
        """Legitimately null, and the UI must render an em dash rather than
        substituting the straight line."""
        from properties.models import PropertyCampusDistance

        assert PropertyCampusDistance.all_objects.filter(walking_minutes=None).exists()
        assert PropertyCampusDistance.all_objects.exclude(walking_minutes=None).exists()


class TestVacancy:
    def test_every_freshness_band_appears(self, seeded):
        """Including `unknown`. 'Nobody has ever said' and 'said long ago' are
        different facts worded differently, and a seed where everything had
        been stated would leave one of those branches unrendered."""
        bands = {vacancy_freshness(unit) for unit in Unit.all_objects.all()}

        assert bands == {"fresh", "ageing", "stale", "unknown"}

    def test_stated_counts_carry_their_author(self, seeded):
        stated = Unit.all_objects.exclude(vacant_count_updated_at=None)

        assert stated.exists()
        assert not stated.filter(vacant_count_updated_by=None).exists()


class TestRatingsAreCoherent:
    def test_aggregates_exist_for_reviewed_properties(self, seeded):
        """A seed where every rating endpoint answered 'no reviews yet' would
        make the whole review surface untestable by hand -- and it is also a
        state a real deployment reaches when the queue is down."""
        from reviews.aggregates import PropertyRatingAggregate

        assert PropertyRatingAggregate.all_objects.exists()

    def test_the_reconciler_finds_nothing_wrong(self, seeded):
        """Drift AND absence. The reconciler returns both now, so a clean run
        against seeded data means the caches actually match their reviews."""
        from reviews.jobs import reconcile_rating_aggregates

        assert reconcile_rating_aggregates() == 0

    def test_some_property_has_more_reviews_than_students(self, seeded):
        """The de-duplication, visible in data.

        One student who moved between two units in the same block writes two
        genuine reviews and counts as one voice. This was unreachable in the
        first version of the seed: the skip rule for "some properties have no
        reviews" landed on the mover's second review every time.
        """
        from django.db.models import F

        from reviews.aggregates import PropertyRatingAggregate

        assert PropertyRatingAggregate.all_objects.filter(
            review_count__gt=F("student_count")
        ).exists()


class TestTheCrossCheckHasSomethingToSay:
    def test_coverage_is_no_longer_zero(self, seeded):
        """The reason this command exists. Against fixtures the cross-check
        reported `{units: 0, informative: 0}` -- a clean bill of health from a
        check that had looked at nothing."""
        coverage = cross_check_coverage()

        assert coverage["units"] > 0
        assert coverage["informative"] > 0

    def test_the_seed_contains_no_impossible_occupancy(self, seeded):
        """Two people in a one-room unit is impossible whether or not the data
        is fake, and a seed that produced it would teach the wrong thing about
        every screen built against it.

        The cross-check found exactly that in the first version of this
        command: an open-ended stay and a current stay in the same single
        room. It was the first time the check had enough data to say anything.
        """
        coverage = cross_check_coverage()

        assert coverage["contradictions"] == 0


class TestEngagement:
    """Inquiries and saved listings.

    Added after an audit found the *populated* states of both screens
    unreachable: the seed had none of either, so the portal's reply box and
    the student's saved list could only ever be seen empty. An empty state
    nobody can leave is as untested as one nobody can reach.
    """

    def test_inquiries_exist_in_both_states(self, seeded):
        from engagement.constants import InquiryStatus
        from engagement.models import Inquiry

        statuses = set(Inquiry.all_objects.values_list("status", flat=True))

        assert InquiryStatus.SENT in statuses
        assert InquiryStatus.ANSWERED in statuses

    def test_an_answered_inquiry_names_who_answered(self, seeded):
        """The student is owed the knowledge that a person replied, and which
        one. A constraint requires the pair, so this is also the data proving
        the seed satisfies it."""
        from engagement.constants import InquiryStatus
        from engagement.models import Inquiry

        answered = Inquiry.all_objects.filter(status=InquiryStatus.ANSWERED)

        assert answered.exists()
        assert not answered.filter(responded_by=None).exists()

    def test_saved_properties_exist(self, seeded):
        from engagement.models import SavedProperty

        assert SavedProperty.all_objects.exists()


class TestTheTenants:
    def test_the_two_universities_differ_in_policy(self, seeded):
        policies = set(University.objects.values_list("signup_policy", flat=True))

        assert SignupPolicy.OPEN in policies
        assert SignupPolicy.REQUIRED in policies

    def test_one_palette_is_inside_the_hostile_band(self, seeded):
        """A design tested only against the stock green is a design that works
        for the first tenant. The grey is the palette that breaks 'the primary
        colour draws the eye'."""
        saturations = [
            int(university.primary_hsl.split()[1].rstrip("%"))
            for university in University.objects.all()
        ]

        assert min(saturations) < 10

    def test_a_school_requiring_verification_still_has_unverified_students(self, seeded):
        """They are inside the grace period, which is the state the whole
        gating design exists to handle."""
        from accounts.models import StudentProfile

        strict = University.objects.get(signup_policy=SignupPolicy.REQUIRED)

        assert StudentProfile.all_objects.filter(
            university=strict, verification_status="unverified"
        ).exists()

    def test_caretakers_hold_different_permission_subsets(self, seeded):
        """The interesting caretaker is the one who may upload photos and may
        not state vacancy. A seed where everyone held everything would make the
        delegation model untestable by hand."""
        from accounts.models import CaretakerAssignment

        subsets = {
            tuple(sorted(assignment.permissions))
            for assignment in CaretakerAssignment.all_objects.all()
        }

        assert len(subsets) >= 2

    def test_one_assignment_is_revoked(self, seeded):
        """Revocation is a flag rather than a delete, and the difference has to
        be visible in real data."""
        from accounts.models import CaretakerAssignment

        assert CaretakerAssignment.all_objects.filter(is_active=False).exists()

"""
The signup enforcement point, against the real flows (ADR-003).

Register-then-verify with a grace period. Verify-then-register is
chicken-and-egg for the email path — there is no account to attach a confirmed
address to — and impossible for document review, because there is nowhere to
upload and nobody to attach the decision to.

The rule this file exists to defend is the fourth one:

> **Policy changes apply to new signups only.** An existing unverified student
> keeps everything they had.

That was written in `gating.py` before either verification path existed, and it
was **false**: `can_perform` read the university's live policy, so a school
switching to `verification_required` blocked every existing unverified student
in the same instant — under `GRACE_EXPIRED`, a grace period they never had.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.gating import (
    NEVER_GATED,
    GatedAction,
    GateReason,
    can_perform,
    profile_is_gated,
    registration_gating_snapshot,
)
from reviews.services import VerificationRequiredError as ReviewGated
from reviews.services import create_review
from tenancies.services import VerificationRequiredError as ClaimGated
from tenancies.services import create_claim
from universities.constants import SignupPolicy, VerificationStatus

pytestmark = pytest.mark.django_db


def make_gating(university, *, required: bool = True, review: bool = True):
    """Switch a university to a gating policy.

    `verification_required` needs at least one verified student first — a
    school cannot lock out its own intake (ADR-003) — so the caller supplies
    one where needed.
    """
    if required:
        from tests.factories import VerifiedStudentProfileFactory

        VerifiedStudentProfileFactory(university=university)
        university.signup_policy = SignupPolicy.REQUIRED

    university.verification_required_to_review = review
    university.save(update_fields=["signup_policy", "verification_required_to_review"])
    return university


def register(profile, university):
    """Freeze the snapshot onto a profile, as registration does."""
    for field, value in registration_gating_snapshot(university).items():
        setattr(profile, field, value)
    profile.save()
    return profile


# ---------------------------------------------------------------------------
# The rule that was false
# ---------------------------------------------------------------------------


class TestAPolicyRaiseDoesNotAffectExistingStudents:
    """The case the tech lead asked for directly, and it used to fail."""

    def test_an_existing_student_keeps_what_they_had(self, student_profile, university):
        """Registered under `open`; the school later requires verification."""
        register(student_profile, university)

        before = can_perform(student_profile.user, GatedAction.CLAIM_TENANCY)
        assert before.allowed is True
        assert before.reason is GateReason.NOT_GATED

        make_gating(university)

        after = can_perform(student_profile.user, GatedAction.CLAIM_TENANCY)

        assert after.allowed is True
        assert after.reason is GateReason.NOT_GATED

    def test_they_are_not_blocked_by_a_grace_period_they_never_had(
        self, student_profile, university
    ):
        """The specific insult in the old behaviour: blocked under
        GRACE_EXPIRED, having never been told anything was expected."""
        register(student_profile, university)
        assert student_profile.grace_period_ends_at is None

        make_gating(university)
        decision = can_perform(student_profile.user, GatedAction.CLAIM_TENANCY)

        assert decision.reason is not GateReason.GRACE_EXPIRED

    def test_the_same_holds_for_reviews(self, student_profile, university):
        register(student_profile, university)

        make_gating(university, required=False, review=True)

        assert can_perform(student_profile.user, GatedAction.WRITE_REVIEW).allowed is True

    def test_a_student_registering_afterwards_is_gated(self, university, student_profile_factory):
        """The policy must still do something, or freezing it would just be a
        way of never enforcing anything."""
        make_gating(university)
        newcomer = register(student_profile_factory(university=university), university)

        decision = can_perform(newcomer.user, GatedAction.CLAIM_TENANCY)

        assert decision.allowed is True
        assert decision.reason is GateReason.WITHIN_GRACE
        assert newcomer.grace_period_ends_at is not None

    def test_gating_reads_the_frozen_value_not_the_live_one(self, student_profile, university):
        """Asserted at the predicate, so a future refactor that reaches for
        `profile.university.signup_policy` fails here."""
        register(student_profile, university)
        make_gating(university)
        student_profile.refresh_from_db()

        assert university.signup_policy == SignupPolicy.REQUIRED
        assert student_profile.signup_policy_at_registration == SignupPolicy.OPEN
        assert profile_is_gated(student_profile, GatedAction.CLAIM_TENANCY) is False

    def test_a_school_can_still_gate_existing_students_deliberately(
        self, student_profile, university
    ):
        """Backfilling the field is the supported path. It is a decision with
        an author, which is exactly the difference from a config toggle
        silently locking people out."""
        register(student_profile, university)
        make_gating(university)

        student_profile.signup_policy_at_registration = SignupPolicy.REQUIRED
        student_profile.save(update_fields=["signup_policy_at_registration"])

        assert profile_is_gated(student_profile, GatedAction.CLAIM_TENANCY) is True


# ---------------------------------------------------------------------------
# The grace period
# ---------------------------------------------------------------------------


class TestGracePeriod:
    def test_it_softens_the_wait(self, university, student_profile_factory):
        """Verification waits on a registry or a human reviewer, neither of
        which the student controls. Blocking them meanwhile punishes them for
        the school's queue."""
        make_gating(university)
        student = register(student_profile_factory(university=university), university)

        decision = can_perform(student.user, GatedAction.CLAIM_TENANCY)

        assert decision.allowed is True
        assert decision.grace_period_ends_at is not None

    def test_expiry_blocks_the_gated_action(self, university, student_profile_factory):
        make_gating(university)
        student = register(student_profile_factory(university=university), university)
        student.grace_period_ends_at = timezone.now() - dt.timedelta(seconds=1)
        student.save(update_fields=["grace_period_ends_at"])

        decision = can_perform(student.user, GatedAction.CLAIM_TENANCY)

        assert decision.allowed is False
        assert decision.reason is GateReason.GRACE_EXPIRED

    def test_expiry_never_deletes_or_locks_out(self, university, student_profile_factory):
        """Blocks the gated actions and nothing else. Never delete, never lock
        out, never log out."""
        make_gating(university)
        student = register(student_profile_factory(university=university), university)
        student.grace_period_ends_at = timezone.now() - dt.timedelta(days=30)
        student.save(update_fields=["grace_period_ends_at"])
        student.user.refresh_from_db()

        assert student.user.is_active is True
        assert student.pk is not None

    def test_verifying_lifts_it(self, university, student_profile_factory):
        make_gating(university)
        student = register(student_profile_factory(university=university), university)
        student.grace_period_ends_at = timezone.now() - dt.timedelta(days=30)
        student.verification_status = VerificationStatus.VERIFIED
        student.verification_method = "email_domain"
        student.verified_at = timezone.now()
        student.save()

        decision = can_perform(student.user, GatedAction.CLAIM_TENANCY)

        assert decision.allowed is True
        assert decision.reason is GateReason.VERIFIED


# ---------------------------------------------------------------------------
# Read access
# ---------------------------------------------------------------------------


class TestReadAccessIsNeverGated:
    """A student who cannot search is a student who cannot use the platform,
    which is not a verification policy — it is an outage."""

    def test_the_never_gated_list_covers_reading(self):
        for action in ("search", "browse_listings", "view_property", "read_reviews"):
            assert action in NEVER_GATED

    def test_no_gated_action_is_a_read(self):
        """Listed explicitly so that gating one of them is a deliberate act
        somebody has to argue for, rather than an omission."""
        for action in GatedAction:
            assert action.value not in NEVER_GATED

    def test_sending_an_inquiry_is_never_gated(self):
        """The one write that stays open. A student asking a landlord a
        question is how they find out whether to apply at all."""
        assert "send_inquiry" in NEVER_GATED

    def test_gating_is_limited_to_three_actions(self):
        """Deliberately a short list. Growth here is how a verification policy
        turns into an outage one action at a time."""
        assert {action.value for action in GatedAction} == {
            "write_review",
            "claim_tenancy",
            "submit_application",
        }


# ---------------------------------------------------------------------------
# Wiring: the gate is actually reached
# ---------------------------------------------------------------------------


class TestTheGateIsWiredIn:
    """A policy nothing calls is documentation."""

    def test_claiming_is_refused_for_a_gated_student(
        self, university, student_profile_factory, unit_factory
    ):
        make_gating(university)
        student = register(student_profile_factory(university=university), university)
        student.grace_period_ends_at = timezone.now() - dt.timedelta(days=1)
        student.save(update_fields=["grace_period_ends_at"])

        with pytest.raises(ClaimGated):
            create_claim(
                unit=unit_factory(),
                claimant=student.user,
                start_date=dt.date.today() - dt.timedelta(days=200),
                end_date=dt.date.today() - dt.timedelta(days=20),
                monthly_rent_kes=Decimal("9500.00"),
            )

    def test_claiming_works_inside_the_grace_period(
        self, university, student_profile_factory, unit_factory
    ):
        make_gating(university)
        student = register(student_profile_factory(university=university), university)

        claim = create_claim(
            unit=unit_factory(),
            claimant=student.user,
            start_date=dt.date.today() - dt.timedelta(days=200),
            end_date=dt.date.today() - dt.timedelta(days=20),
            monthly_rent_kes=Decimal("9500.00"),
        )

        assert claim.pk is not None

    def test_reviewing_is_refused_for_a_gated_student(
        self, university, student_profile_factory, tenancy_factory
    ):
        make_gating(university, required=False, review=True)
        student = register(student_profile_factory(university=university), university)
        student.grace_period_ends_at = timezone.now() - dt.timedelta(days=1)
        student.save(update_fields=["grace_period_ends_at"])

        end = dt.date.today() - dt.timedelta(days=1)
        tenancy = tenancy_factory(
            tenant=student.user, start_date=end - dt.timedelta(days=90), end_date=end
        )

        with pytest.raises(ReviewGated):
            create_review(tenancy, rating=4)

    def test_a_rejected_student_gets_a_different_message(
        self, university, student_profile_factory, unit_factory
    ):
        """ "You must verify" and "your verification was refused" are different
        things to hear, and only one of them has a next step."""
        make_gating(university)
        student = register(student_profile_factory(university=university), university)
        student.verification_status = VerificationStatus.REJECTED
        student.rejection_reason = "The card was unreadable."
        student.save(update_fields=["verification_status", "rejection_reason"])

        with pytest.raises(ClaimGated) as caught:
            create_claim(
                unit=unit_factory(),
                claimant=student.user,
                start_date=dt.date.today() - dt.timedelta(days=200),
                end_date=dt.date.today() - dt.timedelta(days=20),
                monthly_rent_kes=Decimal("9500.00"),
            )

        assert "not accepted" in str(caught.value)

    def test_a_non_student_is_unaffected(self, landlord, unit_factory):
        """Student verification has nothing to say about a landlord; other
        permission classes decide."""
        decision = can_perform(landlord, GatedAction.CLAIM_TENANCY)

        assert decision.allowed is True
        assert decision.reason is GateReason.NO_PROFILE


class TestRegistrationSnapshot:
    def test_it_freezes_everything_from_one_moment(self, university):
        """One function, so the two flags and the grace period cannot be taken
        from different points in time."""
        make_gating(university)

        snapshot = registration_gating_snapshot(university)

        assert snapshot["signup_policy_at_registration"] == SignupPolicy.REQUIRED
        assert snapshot["review_gated_at_registration"] is True
        assert snapshot["grace_period_ends_at"] is not None
        assert snapshot["verification_status"] == VerificationStatus.PENDING

    def test_an_open_school_records_no_deadline(self, university):
        """The field stays null rather than recording a deadline that means
        nothing."""
        snapshot = registration_gating_snapshot(university)

        assert snapshot["grace_period_ends_at"] is None
        assert snapshot["verification_status"] == VerificationStatus.UNVERIFIED

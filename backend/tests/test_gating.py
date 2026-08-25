"""
Signup gating and the grace period (ADR-003).

Register-then-verify. Accounts always create; gating happens afterwards, and
only for the actions a school has explicitly gated. Read access is never gated,
and a policy change never touches an existing account.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.gating import (
    NEVER_GATED,
    GatedAction,
    GateReason,
    can_perform,
    grace_period_end_for,
    initial_verification_status,
    university_gates,
)
from accounts.models import StudentProfile
from universities.constants import SignupPolicy, VerificationStatus

pytestmark = pytest.mark.django_db


def require_verification(university, *, grace_days: int = 14):
    """Put a university into the strictest policy, legitimately.

    The lockout guard means this needs a verified student on record first,
    which is exactly the sequence a real school would go through.
    """
    university.signup_policy = SignupPolicy.REQUIRED
    university.verification_grace_period_days = grace_days
    university.verification_required_to_review = True
    university.save()
    return university


def registered_under(profile, university):
    """Freeze the university's current policy onto a profile.

    Gating reads what was in force when a student registered, not the live
    value, so a test that only changes the university has changed nothing for
    an existing profile -- which is the whole point of
    `TestPolicyChangeAppliesForwardOnly`. Tests that want a *gated* student
    have to say the student registered under the gate.
    """
    from accounts.gating import registration_gating_snapshot

    for field, value in registration_gating_snapshot(university).items():
        setattr(profile, field, value)
    profile.save()
    return profile


# ---------------------------------------------------------------------------
# Registration always succeeds
# ---------------------------------------------------------------------------


class TestRegistrationIsNeverBlocked:
    def test_signup_succeeds_at_a_university_requiring_verification(
        self, api_client, university, verified_student_profile
    ):
        """Verify-then-register is chicken-and-egg, so registration cannot gate.

        There is no account to attach a confirmed address to, and nowhere to
        upload an ID document.
        """
        require_verification(university)

        response = api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "newcomer@students.ku.ac.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "New",
                "last_name": "Comer",
            },
            format="json",
            HTTP_HOST=f"{university.subdomain}.example.co.ke",
        )

        assert response.status_code == status.HTTP_201_CREATED

    def test_the_new_profile_is_pending_with_a_grace_period(
        self, api_client, university, verified_student_profile
    ):
        require_verification(university, grace_days=14)

        api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "grace@students.ku.ac.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "Grace",
                "last_name": "Period",
            },
            format="json",
            HTTP_HOST=f"{university.subdomain}.example.co.ke",
        )

        profile = StudentProfile.all_objects.get(user__email="grace@students.ku.ac.ke")

        assert profile.verification_status == VerificationStatus.PENDING
        assert profile.grace_period_ends_at is not None
        assert profile.grace_period_ends_at > timezone.now()

    def test_an_open_university_records_no_grace_period(self, api_client, university):
        """Nothing is outstanding, so a deadline would mean nothing."""
        api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "open@students.ku.ac.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "Op",
                "last_name": "En",
            },
            format="json",
            HTTP_HOST=f"{university.subdomain}.example.co.ke",
        )

        profile = StudentProfile.all_objects.get(user__email="open@students.ku.ac.ke")

        assert profile.verification_status == VerificationStatus.UNVERIFIED
        assert profile.grace_period_ends_at is None

    def test_the_capability_block_carries_the_grace_deadline(
        self, api_client, university, verified_student_profile
    ):
        """The client needs it to show the right prompt."""
        require_verification(university)

        response = api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "caps@students.ku.ac.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "Ca",
                "last_name": "Ps",
            },
            format="json",
            HTTP_HOST=f"{university.subdomain}.example.co.ke",
        )

        capabilities = response.json()["user"]["capabilities"]

        assert capabilities["verification_status"] == VerificationStatus.PENDING
        assert capabilities["grace_period_ends_at"] is not None
        assert capabilities["is_verified_student"] is False


# ---------------------------------------------------------------------------
# What gating actually blocks
# ---------------------------------------------------------------------------


class TestGatingScope:
    def test_reading_is_never_in_the_gated_set(self):
        """A student who cannot search is not gated, they are locked out.

        Listed explicitly so that gating one of these has to be argued for
        rather than slipped in.
        """
        gated = {action.value for action in GatedAction}

        assert not (gated & NEVER_GATED)
        for read_action in ("search", "browse_listings", "save_property", "send_inquiry"):
            assert read_action in NEVER_GATED

    def test_an_open_university_gates_nothing(self, university):
        for action in GatedAction:
            assert university_gates(university, action) is False

    def test_encouraged_gates_nothing_either(self, university):
        """Identical to open, plus prompts."""
        university.signup_policy = SignupPolicy.ENCOURAGED
        university.save(update_fields=["signup_policy"])

        for action in GatedAction:
            assert university_gates(university, action) is False

    def test_required_gates_the_transactional_actions(self, university, verified_student_profile):
        require_verification(university)

        assert university_gates(university, GatedAction.CLAIM_TENANCY) is True
        assert university_gates(university, GatedAction.SUBMIT_APPLICATION) is True

    def test_review_gating_is_its_own_switch(self, university):
        """`verification_required_to_review` is independent of signup policy."""
        university.verification_required_to_review = True
        university.save(update_fields=["verification_required_to_review"])

        assert university_gates(university, GatedAction.WRITE_REVIEW) is True
        assert university_gates(university, GatedAction.SUBMIT_APPLICATION) is False


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


class TestCanPerform:
    def test_a_non_student_is_unaffected(self, landlord):
        decision = can_perform(landlord, GatedAction.SUBMIT_APPLICATION)

        assert decision.allowed is True
        assert decision.reason is GateReason.NO_PROFILE

    def test_anonymous_is_unaffected_here(self, db):
        from django.contrib.auth.models import AnonymousUser

        assert can_perform(AnonymousUser(), GatedAction.WRITE_REVIEW).allowed is True

    def test_an_ungated_action_is_allowed_however_unverified(self, student_profile):
        decision = can_perform(student_profile.user, GatedAction.WRITE_REVIEW)

        assert decision.allowed is True
        assert decision.reason is GateReason.NOT_GATED

    def test_a_verified_student_is_allowed(self, university, verified_student_profile):
        require_verification(university)
        registered_under(verified_student_profile, university)
        verified_student_profile.verification_status = VerificationStatus.VERIFIED
        verified_student_profile.save(update_fields=["verification_status"])

        decision = can_perform(verified_student_profile.user, GatedAction.WRITE_REVIEW)

        assert decision.allowed is True
        assert decision.reason is GateReason.VERIFIED

    def test_within_grace_is_allowed(self, university, verified_student_profile, student_profile):
        require_verification(university)
        registered_under(student_profile, university)
        student_profile.verification_status = VerificationStatus.PENDING
        student_profile.grace_period_ends_at = timezone.now() + dt.timedelta(days=3)
        student_profile.save()

        decision = can_perform(student_profile.user, GatedAction.WRITE_REVIEW)

        assert decision.allowed is True
        assert decision.reason is GateReason.WITHIN_GRACE
        assert decision.grace_period_ends_at is not None

    def test_expired_grace_blocks_the_gated_action(
        self, university, verified_student_profile, student_profile
    ):
        require_verification(university)
        registered_under(student_profile, university)
        student_profile.verification_status = VerificationStatus.PENDING
        student_profile.grace_period_ends_at = timezone.now() - dt.timedelta(days=1)
        student_profile.save()

        decision = can_perform(student_profile.user, GatedAction.WRITE_REVIEW)

        assert decision.allowed is False
        assert decision.reason is GateReason.GRACE_EXPIRED

    def test_expiry_never_touches_the_account_itself(
        self, university, verified_student_profile, student_profile
    ):
        """Never delete, never lock out, never log out (ADR-003)."""
        require_verification(university)
        student_profile.grace_period_ends_at = timezone.now() - dt.timedelta(days=30)
        student_profile.save()

        student_profile.user.refresh_from_db()

        assert student_profile.user.is_active is True
        assert StudentProfile.all_objects.filter(pk=student_profile.pk).exists()

    def test_a_rejected_student_is_blocked_without_a_grace_reprieve(
        self, university, verified_student_profile, student_profile
    ):
        require_verification(university)
        registered_under(student_profile, university)
        student_profile.verification_status = VerificationStatus.REJECTED
        student_profile.rejection_reason = "Document did not match"
        student_profile.grace_period_ends_at = timezone.now() + dt.timedelta(days=10)
        student_profile.save()

        decision = can_perform(student_profile.user, GatedAction.WRITE_REVIEW)

        assert decision.allowed is False
        assert decision.reason is GateReason.REJECTED

    def test_a_null_grace_period_under_a_gating_policy_blocks(
        self, university, verified_student_profile, student_profile
    ):
        """A student who registered under the gate but has no deadline.

        Not within grace, because none was granted -- and the gated action is
        the only thing affected. Distinct from a student who predates the
        policy entirely, who is not gated at all: see
        `TestPolicyChangeAppliesForwardOnly`.
        """
        require_verification(university)
        registered_under(student_profile, university)
        student_profile.grace_period_ends_at = None
        student_profile.save()

        assert can_perform(student_profile.user, GatedAction.WRITE_REVIEW).allowed is False


# ---------------------------------------------------------------------------
# Policy changes apply to new signups only
# ---------------------------------------------------------------------------


class TestPolicyChangeAppliesForwardOnly:
    def test_raising_the_policy_grants_no_retroactive_grace_period(
        self, university, verified_student_profile, student_profile
    ):
        """The rule most likely to be broken by a later "cleanup".

        Consistency would say backfill everyone's grace period. ADR-003 says
        the opposite: an existing unverified student keeps exactly what they
        had, and a school changing a dropdown must not be able to change the
        terms under an account that already exists.
        """
        assert student_profile.grace_period_ends_at is None

        require_verification(university)
        student_profile.refresh_from_db()

        assert student_profile.grace_period_ends_at is None
        assert student_profile.verification_status == VerificationStatus.UNVERIFIED

    def test_raising_the_policy_never_deactivates_anyone(
        self, university, verified_student_profile, student_profile
    ):
        require_verification(university)

        student_profile.user.refresh_from_db()
        verified_student_profile.user.refresh_from_db()

        assert student_profile.user.is_active is True
        assert verified_student_profile.user.is_active is True

    def test_lowering_the_policy_restores_the_gated_actions(
        self, university, verified_student_profile, student_profile
    ):
        """A policy change can only ever WIDEN what an existing student may do.

        Freezing the policy at registration stops a raise reaching backwards;
        this is the other direction. Leaving a student gated after their school
        stopped requiring it would be punitive for no reason.
        """
        require_verification(university)
        registered_under(student_profile, university)
        student_profile.grace_period_ends_at = timezone.now() - dt.timedelta(days=1)
        student_profile.save()

        assert can_perform(student_profile.user, GatedAction.WRITE_REVIEW).allowed is False

        university.signup_policy = SignupPolicy.ENCOURAGED
        university.verification_required_to_review = False
        university.save()
        student_profile.refresh_from_db()

        assert can_perform(student_profile.user, GatedAction.WRITE_REVIEW).allowed is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_grace_end_is_null_when_nothing_is_gated(self, university):
        assert grace_period_end_for(university) is None

    def test_grace_end_uses_the_universitys_own_window(self, university, verified_student_profile):
        require_verification(university, grace_days=30)
        now = timezone.now()

        assert grace_period_end_for(university, now=now) == now + dt.timedelta(days=30)

    def test_initial_status_is_unverified_when_open(self, university):
        assert initial_verification_status(university) == VerificationStatus.UNVERIFIED

    def test_initial_status_is_pending_when_gated(self, university, verified_student_profile):
        require_verification(university)

        assert initial_verification_status(university) == VerificationStatus.PENDING

    def test_an_enforcement_date_in_the_future_leaves_signup_ungated(
        self, university, verified_student_profile
    ):
        """A school can announce a change before it bites."""
        require_verification(university)
        university.verification_enforced_from = timezone.localdate() + dt.timedelta(days=30)
        university.save(update_fields=["verification_enforced_from"])

        assert grace_period_end_for(university) is None
        assert initial_verification_status(university) == VerificationStatus.UNVERIFIED

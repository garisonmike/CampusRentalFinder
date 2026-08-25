"""
Tests for the tenant model, its theming tokens, and the signup-policy guard.

The guard is the important part of this file. ADR-003 moved it from a database
constraint to a service function because it spans tables, and a rule that lives
outside the database and outside a test is a comment.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from config.tenancy import TenantScopeError
from universities.constants import SignupPolicy, VerificationMethod
from universities.models import Campus, University
from universities.services import (
    UnsafeSignupPolicyError,
    VerificationMethodNotOfferedError,
    assert_signup_policy_is_safe,
    assert_verification_method_is_enabled,
    signup_verification_is_enforced,
    verification_method_is_enabled,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# University
# ---------------------------------------------------------------------------


class TestUniversity:
    def test_defaults_are_the_safe_ones(self, university):
        """Nothing is gated until a school opts in."""
        assert university.signup_policy == SignupPolicy.OPEN
        assert university.verification_required_to_review is False
        assert university.id_review_retention_days == 7

    def test_no_verification_method_is_offered_by_default(self):
        """Read from the MODEL, not the factory.

        `UniversityFactory` enables both methods so that the several hundred
        tests about something else do not each have to know that verification
        is per-university configuration. The field's own default is what has
        to be safe, and that is what this asserts.
        """
        assert University._meta.get_field("verification_methods_enabled").default() == []

    def test_subdomain_is_unique(self, university, university_factory):
        with pytest.raises(IntegrityError), transaction.atomic():
            university_factory(subdomain=university.subdomain)

    def test_theme_tokens_are_the_three_adr_005_overrides(self, university):
        assert set(university.theme_tokens) == {"primary", "secondary", "accent"}

    @pytest.mark.parametrize(
        "bad",
        [
            "hsl(142 71% 45%)",  # wrapped
            "142, 71%, 45%",  # comma separated
            "#22c55e",  # hex
            "142 71 45",  # no percent signs
        ],
    )
    def test_the_database_rejects_a_malformed_colour(self, university_factory, bad):
        """shadcn needs the bare triple for `hsl(var(--primary) / 0.5)`.

        A constraint rather than a validator: the admin, a data migration and a
        shell session all bypass validators, and a malformed token silently
        breaks every colour on a tenant's site.
        """
        with pytest.raises(IntegrityError), transaction.atomic():
            university_factory(primary_hsl=bad)

    def test_retention_window_is_bounded(self, university_factory):
        with pytest.raises(IntegrityError), transaction.atomic():
            university_factory(id_review_retention_days=0)


# ---------------------------------------------------------------------------
# Campus
# ---------------------------------------------------------------------------


class TestCampus:
    def test_only_one_main_campus_per_university(self, university, campus_factory):
        campus_factory(university=university, name="Main", is_main=True)

        with pytest.raises(IntegrityError), transaction.atomic():
            campus_factory(university=university, name="Ruiru", is_main=True)

    def test_two_universities_may_each_have_a_main_campus(
        self, university, university_factory, campus_factory
    ):
        other = university_factory()
        campus_factory(university=university, name="Main", is_main=True)
        campus_factory(university=other, name="Main", is_main=True)

        assert Campus.all_objects.filter(is_main=True).count() == 2

    def test_campus_names_are_unique_within_a_university(self, university, campus_factory):
        campus_factory(university=university, name="Main")

        with pytest.raises(IntegrityError), transaction.atomic():
            campus_factory(university=university, name="Main")

    def test_coordinates_are_range_checked(self, university, campus_factory):
        with pytest.raises(IntegrityError), transaction.atomic():
            campus_factory(university=university, latitude=91.0)


# ---------------------------------------------------------------------------
# Tenant scoping (ADR-001)
# ---------------------------------------------------------------------------


class TestCampusScoping:
    def test_an_unqualified_query_raises_rather_than_leaking(self, university, campus_factory):
        """The whole point of the scoped manager.

        Returning every tenant's rows would be a data leak that looks like a
        working feature, so forgetting to scope is a loud error instead.
        """
        campus_factory(university=university)

        with pytest.raises(TenantScopeError, match="tenant-scoped"):
            Campus.objects.all()

        with pytest.raises(TenantScopeError):
            Campus.objects.filter(name="Main")

        with pytest.raises(TenantScopeError):
            Campus.objects.count()

    def test_for_tenant_returns_only_that_tenants_rows(
        self, university, university_factory, campus_factory
    ):
        other = university_factory()
        campus_factory(university=university, name="Main")
        campus_factory(university=other, name="Main")

        assert Campus.objects.for_tenant(university).count() == 1
        assert Campus.objects.for_tenant(other).count() == 1

    def test_for_tenant_refuses_an_unresolved_tenant(self, university, campus_factory):
        """An unresolved tenant must error, not silently mean 'everyone'."""
        campus_factory(university=university)

        with pytest.raises(TenantScopeError, match="needs a university"):
            Campus.objects.for_tenant(None)

    def test_across_tenants_is_the_explicit_escape_hatch(
        self, university, university_factory, campus_factory
    ):
        campus_factory(university=university, name="Main")
        campus_factory(university=university_factory(), name="Main")

        assert Campus.objects.across_tenants().count() == 2

    def test_all_objects_stays_available_for_django_internals(self, university, campus_factory):
        """The admin, related descriptors and dumpdata need an unfiltered manager."""
        campus_factory(university=university)
        assert Campus.all_objects.count() == 1

    def test_related_descriptor_still_works(self, university, campus_factory):
        """`university.campuses` must not raise; it is already scoped by the FK."""
        campus_factory(university=university, name="Main")
        assert university.campuses.count() == 1


# ---------------------------------------------------------------------------
# The signup-policy guard (ADR-003)
# ---------------------------------------------------------------------------


class TestSignupPolicyGuard:
    def test_a_school_with_verification_enabled_but_no_verified_students_cannot_require_it(
        self, university
    ):
        """The exact failure the old boolean's constraint missed.

        A school enables email-domain verification, sets the flag, and has not
        yet issued addresses to its first-years. The old guard checked whether
        any method was enabled, so it passed — and locked out an entire intake.
        This guard checks whether verification has ever actually worked here.
        """
        university.verification_methods_enabled = [VerificationMethod.EMAIL_DOMAIN]
        university.student_email_domains = ["s.kyu.ac.ke"]
        university.save()

        with pytest.raises(UnsafeSignupPolicyError) as caught:
            assert_signup_policy_is_safe(university, SignupPolicy.REQUIRED)

        assert caught.value.code == "no_verified_students"

    def test_requiring_verification_is_allowed_once_one_student_is_verified(
        self, university, verified_student_profile
    ):
        """The guard tests an outcome, not a configuration flag."""
        assert_signup_policy_is_safe(university, SignupPolicy.REQUIRED)

    def test_an_unverified_student_does_not_satisfy_the_guard(self, university, student_profile):
        """Existing is not the same as verified."""
        with pytest.raises(UnsafeSignupPolicyError):
            assert_signup_policy_is_safe(university, SignupPolicy.REQUIRED)

    def test_a_verified_student_at_another_university_does_not_count(
        self, university, university_factory, verified_student_profile_factory
    ):
        """Per-tenant, or one school unlocks the setting for every other."""
        verified_student_profile_factory(university=university_factory())

        with pytest.raises(UnsafeSignupPolicyError):
            assert_signup_policy_is_safe(university, SignupPolicy.REQUIRED)

    @pytest.mark.parametrize("policy", [SignupPolicy.OPEN, SignupPolicy.ENCOURAGED])
    def test_the_softer_policies_are_always_allowed(self, university, policy):
        """Only the locking-out one is guarded."""
        assert_signup_policy_is_safe(university, policy)

    def test_the_guard_raises_a_validation_error_so_drf_renders_it_as_400(self, university):
        with pytest.raises(ValidationError):
            assert_signup_policy_is_safe(university, SignupPolicy.REQUIRED)


class TestSignupEnforcementDate:
    def test_not_enforced_when_the_policy_is_not_required(self, university):
        assert signup_verification_is_enforced(university) is False

    def test_inert_before_the_enforcement_date(self, university):
        """`verification_enforced_from` lets a school announce a change first."""
        university.signup_policy = SignupPolicy.REQUIRED
        university.verification_enforced_from = dt.date(2030, 1, 1)

        assert signup_verification_is_enforced(university, on=dt.date(2029, 12, 31)) is False

    def test_active_on_and_after_the_enforcement_date(self, university):
        university.signup_policy = SignupPolicy.REQUIRED
        university.verification_enforced_from = dt.date(2030, 1, 1)

        assert signup_verification_is_enforced(university, on=dt.date(2030, 1, 1)) is True
        assert signup_verification_is_enforced(university, on=dt.date(2030, 6, 1)) is True

    def test_enforced_immediately_with_no_date(self, university):
        university.signup_policy = SignupPolicy.REQUIRED

        assert signup_verification_is_enforced(university) is True


class TestPolicyChangeDoesNotInvalidateAccounts:
    def test_existing_unverified_students_keep_their_access(
        self, university, student_profile, verified_student_profile
    ):
        """ADR-003: enforcement applies at signup only.

        A university switching a setting must never be able to sign out its own
        student body. Existing unverified users are prompted, not blocked — the
        difference between a policy change and an outage.
        """
        university.signup_policy = SignupPolicy.REQUIRED
        university.save(update_fields=["signup_policy"])

        student_profile.refresh_from_db()

        assert student_profile.pk is not None
        assert student_profile.user.is_active is True
        assert not student_profile.is_verified


# ---------------------------------------------------------------------------
# Enum shape
# ---------------------------------------------------------------------------


def test_the_old_signup_boolean_is_gone():
    """`verification_required_to_signup` was replaced, not supplemented.

    Leaving both would give two sources of truth for the same question.
    """
    field_names = {field.name for field in University._meta.get_fields()}

    assert "verification_required_to_signup" not in field_names
    assert "signup_policy" in field_names
    assert "verification_enforced_from" in field_names


class TestVerificationMethodsAreOffered:
    """`verification_methods_enabled` was configuration nothing read.

    It existed from phase 2, appeared in the admin, and was consulted by
    neither verification path until both were built in phase 6. A school that
    had turned email-domain verification off could still have students verify
    that way, and one with no document reviewers could still receive identity
    documents into a queue nobody would work -- which, since the retention
    clock starts at upload, meant collecting national IDs purely to delete
    them 30 days later.
    """

    def test_an_enabled_method_is_offered(self, university):
        assert verification_method_is_enabled(university, VerificationMethod.EMAIL_DOMAIN)

    def test_a_disabled_method_is_not(self, university_factory):
        university = university_factory(
            verification_methods_enabled=[VerificationMethod.EMAIL_DOMAIN]
        )

        assert not verification_method_is_enabled(university, VerificationMethod.STUDENT_ID_UPLOAD)

    def test_a_school_offering_nothing_offers_nothing(self, university_factory):
        university = university_factory(verification_methods_enabled=[])

        for method in VerificationMethod.values:
            assert not verification_method_is_enabled(university, method)

    def test_the_gate_refuses_with_an_explanation(self, university_factory):
        university = university_factory(verification_methods_enabled=[])

        with pytest.raises(VerificationMethodNotOfferedError) as caught:
            assert_verification_method_is_enabled(university, VerificationMethod.EMAIL_DOMAIN)

        assert "does not offer" in str(caught.value)

    def test_email_verification_honours_it(self, university_factory, student_profile_factory):
        from accounts.verification import issue_email_token

        university = university_factory(
            verification_methods_enabled=[VerificationMethod.STUDENT_ID_UPLOAD],
            student_email_domains=["s.example.ac.ke"],
        )
        profile = student_profile_factory(university=university)

        with pytest.raises(VerificationMethodNotOfferedError):
            issue_email_token(profile, "brenda@s.example.ac.ke")

    def test_document_upload_honours_it(self, university_factory, student_profile_factory):
        """A school with no reviewers must not be able to receive identity
        documents at all."""
        import io

        from PIL import Image

        from accounts.documents import VerificationDocument, submit_verification_document

        university = university_factory(
            verification_methods_enabled=[VerificationMethod.EMAIL_DOMAIN]
        )
        profile = student_profile_factory(university=university)
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buffer, format="JPEG")

        with pytest.raises(VerificationMethodNotOfferedError):
            submit_verification_document(profile, buffer.getvalue())

        assert VerificationDocument.objects.count() == 0

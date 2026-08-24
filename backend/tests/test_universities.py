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
    assert_signup_policy_is_safe,
    signup_verification_is_enforced,
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
        assert university.verification_methods_enabled == []
        assert university.id_review_retention_days == 7

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

"""
Object-level authorization (ADR-003).

The draft carried the whole authorization model in one string, ``user_type``,
which the client set at registration and which the object-permission checks
trusted. These tests cover the replacement: capability comes from a
relationship another party created, and an unknown capability denies.
"""

from __future__ import annotations

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory

from accounts import capabilities
from accounts.capabilities import NEVER_DELEGABLE, CaretakerPermission
from accounts.permissions import (
    IsLandlord,
    IsPlatformStaff,
    IsStudent,
    IsUniversityStaffForTenant,
    IsVerifiedStudent,
)

pytestmark = pytest.mark.django_db


def request_from(user):
    request = APIRequestFactory().get("/")
    request.user = user
    return request


# ---------------------------------------------------------------------------
# Capability derivation
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_a_student_is_a_student_because_of_the_profile(self, tenant):
        assert capabilities.is_student(tenant) is True
        assert capabilities.is_landlord(tenant) is False

    def test_a_landlord_is_a_landlord_because_of_the_profile(self, landlord):
        assert capabilities.is_landlord(landlord) is True
        assert capabilities.is_student(landlord) is False

    def test_one_user_may_hold_both(self, tenant, landlord_profile_factory):
        """A postgraduate who also sublets a room is both.

        One string could not say so, which is one of the reasons ADR-003
        replaced it.
        """
        landlord_profile_factory(user=tenant)
        tenant.refresh_from_db()

        assert capabilities.is_student(tenant) is True
        assert capabilities.is_landlord(tenant) is True

    def test_platform_staff_is_is_staff_and_nothing_else(self, staff_user, platform_admin):
        assert capabilities.is_platform_staff(staff_user) is True
        assert capabilities.is_platform_staff(platform_admin) is False

    def test_verification_is_read_from_the_student_profile(
        self, student_profile, verified_student_profile
    ):
        assert capabilities.is_verified_student(student_profile.user) is False
        assert capabilities.is_verified_student(verified_student_profile.user) is True

    def test_a_non_student_is_never_a_verified_student(self, landlord):
        assert capabilities.is_verified_student(landlord) is False

    def test_university_staff_must_be_active(self, university_staff):
        assert capabilities.is_university_staff(university_staff) is True

        university_staff.staff_profile.is_active = False
        university_staff.staff_profile.save(update_fields=["is_active"])
        university_staff.refresh_from_db()

        assert capabilities.is_university_staff(university_staff) is False

    def test_the_capability_set_is_complete_and_false_by_default(self, db):
        from django.contrib.auth.models import AnonymousUser

        result = capabilities.capabilities_for(AnonymousUser())

        assert result == capabilities.anonymous_capabilities()
        assert not any(value for key, value in result.items() if isinstance(value, bool))

    def test_the_capability_set_names_the_students_university(self, tenant, university):
        assert capabilities.capabilities_for(tenant)["university"] == university.subdomain

    def test_managed_properties_is_empty_until_caretaker_assignments_exist(self, landlord):
        """The shape is fixed now so the frontend contract does not change when
        CaretakerAssignment lands with the Property model."""
        assert capabilities.capabilities_for(landlord)["manages_properties"] == []


# ---------------------------------------------------------------------------
# Permission classes
# ---------------------------------------------------------------------------


class TestPermissionClasses:
    @pytest.mark.parametrize(
        ("permission_class", "allowed_fixture"),
        [
            (IsLandlord, "landlord"),
            (IsStudent, "tenant"),
            (IsPlatformStaff, "staff_user"),
        ],
    )
    def test_the_right_holder_is_admitted(self, permission_class, allowed_fixture, request):
        user = request.getfixturevalue(allowed_fixture)

        assert permission_class().has_permission(request_from(user), None) is True

    @pytest.mark.parametrize(
        "permission_class", [IsLandlord, IsStudent, IsPlatformStaff, IsVerifiedStudent]
    )
    def test_a_bare_user_holds_nothing(self, permission_class, platform_admin):
        """The account that would have declared itself an admin in the draft."""
        assert permission_class().has_permission(request_from(platform_admin), None) is False

    @pytest.mark.parametrize(
        "permission_class", [IsLandlord, IsStudent, IsPlatformStaff, IsVerifiedStudent]
    )
    def test_anonymous_holds_nothing(self, permission_class, db):
        from django.contrib.auth.models import AnonymousUser

        assert permission_class().has_permission(request_from(AnonymousUser()), None) is False

    def test_verified_student_needs_the_badge_not_just_the_profile(
        self, student_profile, verified_student_profile
    ):
        assert IsVerifiedStudent().has_permission(request_from(student_profile.user), None) is False
        assert (
            IsVerifiedStudent().has_permission(request_from(verified_student_profile.user), None)
            is True
        )

    def test_university_staff_reach_only_their_own_tenant(
        self,
        university_staff,
        student_profile,
        university_factory,
        verified_student_profile_factory,
    ):
        """This role can read student ID documents, so the scope is the point."""
        permission = IsUniversityStaffForTenant()
        request = request_from(university_staff)

        assert permission.has_object_permission(request, None, student_profile) is True

        foreign = verified_student_profile_factory(university=university_factory())
        assert permission.has_object_permission(request, None, foreign) is False


# ---------------------------------------------------------------------------
# The caretaker permission set (ADR-003)
# ---------------------------------------------------------------------------


class TestCaretakerPermissionSet:
    def test_the_delegable_set_is_exactly_what_the_adr_lists(self):
        assert set(CaretakerPermission.values) == {
            "manage_units",
            "manage_vacancy",
            "manage_photos",
            "set_availability",
            "resolve_tenancy_claims",
            "respond_inquiries",
        }

    @pytest.mark.parametrize(
        "forbidden",
        [
            "delete_property",
            "transfer_ownership",
            "grant_caretaker_assignments",
            "edit_landlord_profile",
            "edit_payout_details",
            "post_review_response",
        ],
    )
    def test_the_forbidden_capabilities_are_not_delegable(self, forbidden):
        """A landlord cannot hand these to a caretaker, whatever they set."""
        assert forbidden in NEVER_DELEGABLE
        assert forbidden not in CaretakerPermission.values

    def test_the_two_sets_do_not_overlap(self):
        assert not (set(CaretakerPermission.values) & NEVER_DELEGABLE)

    def test_resolving_tenancy_claims_is_delegable(self):
        """Safe because the tenant initiates the claim (ADR-004).

        Confirming is acknowledging someone else's assertion, not creating a
        reviewer out of nothing, so it no longer lets one actor manufacture a
        review on their own.
        """
        assert CaretakerPermission.RESOLVE_TENANCY_CLAIMS in CaretakerPermission


# ---------------------------------------------------------------------------
# The escalation path is closed
# ---------------------------------------------------------------------------


class TestEscalationPathIsClosed:
    def test_no_registration_field_grants_any_capability(self, api_client, university):
        """The whole point of ADR-003.

        Every field the draft honoured, plus the flags an attacker would try.
        """
        response = api_client.post(
            "/api/v1/auth/register/",
            {
                "email": "escalate@example.co.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "Esc",
                "last_name": "Alate",
                "user_type": "admin",
                "is_staff": True,
                "is_superuser": True,
                "role": "admin",
            },
            format="json",
            HTTP_HOST=f"{university.subdomain}.example.co.ke",
        )

        assert response.status_code == status.HTTP_201_CREATED

        from accounts.models import User

        created = User.objects.get(email="escalate@example.co.ke")

        assert created.is_staff is False
        assert created.is_superuser is False
        assert not hasattr(created, "landlord_profile")
        assert capabilities.capabilities_for(created)["is_staff"] is False

    def test_a_user_cannot_grant_themselves_staff_via_the_profile_endpoint(
        self, tenant_client, tenant
    ):
        tenant_client.patch(
            "/api/v1/auth/profile/",
            {"is_staff": True, "is_superuser": True},
            format="json",
        )
        tenant.refresh_from_db()

        assert tenant.is_staff is False
        assert tenant.is_superuser is False

    def test_the_admin_user_endpoint_will_not_elevate_to_platform_staff(self, staff_client, tenant):
        """Even platform staff promote through Django admin, not the API."""
        staff_client.patch(
            f"/api/v1/auth/admin/users/{tenant.pk}/", {"is_staff": True}, format="json"
        )
        tenant.refresh_from_db()

        assert tenant.is_staff is False

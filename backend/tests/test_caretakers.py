"""
Caretaker assignments (ADR-003).

A caretaker's authority is scoped to one property and granted by that
property's landlord. The two things this file guards: the scope is real, and
the delegable set is enforced rather than documented.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from accounts import capabilities
from accounts.capabilities import NEVER_DELEGABLE, CaretakerPermission
from accounts.models import CaretakerAssignment
from accounts.permissions import IsPropertyManager
from config.tenancy import TenantScopeError

pytestmark = pytest.mark.django_db


def request_for(user, method: str = "PATCH"):
    request = getattr(APIRequestFactory(), method.lower())("/")
    request.user = user
    return request


class ManageVacancy(IsPropertyManager):
    required_permission = CaretakerPermission.MANAGE_VACANCY


class DeleteProperty(IsPropertyManager):
    required_permission = "delete_property"


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


class TestCaretakerAssignment:
    def test_it_is_scoped_to_one_property(self, caretaker_assignment_factory, property_factory):
        """A fourth user_type string would have granted every caretaker
        authority over every property (ADR-003)."""
        assignment = caretaker_assignment_factory()
        other = property_factory()

        assert assignment.property != other
        assert capabilities.caretaker_permissions_for(assignment.user, other.pk) == set()

    def test_one_active_assignment_per_user_and_property(
        self, caretaker_assignment_factory, property_factory
    ):
        prop = property_factory()
        first = caretaker_assignment_factory(property=prop)

        with pytest.raises(IntegrityError), transaction.atomic():
            caretaker_assignment_factory(user=first.user, property=prop)

    def test_a_revoked_assignment_leaves_room_for_a_new_one(
        self, caretaker_assignment_factory, property_factory
    ):
        """Revocation is a flag, so the grant history survives — but it must
        not block re-granting later."""
        prop = property_factory()
        first = caretaker_assignment_factory(property=prop)
        first.is_active = False
        first.revoked_at = timezone.now()
        first.save()

        again = caretaker_assignment_factory(user=first.user, property=prop)

        assert again.pk != first.pk
        assert CaretakerAssignment.all_objects.filter(user=first.user, property=prop).count() == 2

    def test_revoking_requires_a_timestamp(self, caretaker_assignment_factory):
        assignment = caretaker_assignment_factory()
        assignment.is_active = False

        with pytest.raises(IntegrityError), transaction.atomic():
            assignment.save()

    def test_the_granting_landlord_cannot_be_deleted_away(self, caretaker_assignment_factory):
        """PROTECT on granted_by: the audit trail is the point."""
        assignment = caretaker_assignment_factory()

        with pytest.raises(IntegrityError), transaction.atomic():
            assignment.granted_by.delete()


# ---------------------------------------------------------------------------
# The delegable set is enforced, not documented
# ---------------------------------------------------------------------------


class TestPermissionSetIsEnforced:
    def test_a_valid_subset_is_accepted(self, caretaker_assignment_factory):
        assignment = caretaker_assignment_factory(permissions=[CaretakerPermission.MANAGE_PHOTOS])

        assert assignment.permissions == [CaretakerPermission.MANAGE_PHOTOS]

    @pytest.mark.parametrize("forbidden", sorted(NEVER_DELEGABLE))
    def test_a_never_delegable_value_is_rejected_on_write(
        self, caretaker_assignment_factory, forbidden
    ):
        """ADR-003 fixes what a landlord may hand over.

        Rejected at the model layer rather than merely unused, so the list
        cannot drift from the code that checks it.
        """
        with pytest.raises(ValidationError) as caught:
            caretaker_assignment_factory(permissions=[forbidden])

        assert "permissions" in caught.value.message_dict

    def test_an_unknown_value_is_rejected(self, caretaker_assignment_factory):
        with pytest.raises(ValidationError):
            caretaker_assignment_factory(permissions=["manage_evrything"])

    def test_an_empty_permission_list_grants_nothing(self, caretaker_assignment_factory):
        assignment = caretaker_assignment_factory(permissions=[])

        assert (
            capabilities.caretaker_permissions_for(assignment.user, assignment.property_id) == set()
        )


# ---------------------------------------------------------------------------
# Capability derivation
# ---------------------------------------------------------------------------


class TestCaretakerCapabilities:
    def test_managed_properties_lists_the_assignments(self, caretaker_assignment_factory):
        assignment = caretaker_assignment_factory()

        assert capabilities.managed_property_ids(assignment.user) == [assignment.property_id]

    def test_revocation_is_immediate(self, caretaker_assignment_factory):
        """A cached permission map that outlives a revocation is a real hole."""
        assignment = caretaker_assignment_factory()
        assert capabilities.managed_property_ids(assignment.user)

        assignment.is_active = False
        assignment.revoked_at = timezone.now()
        assignment.save()

        assert capabilities.managed_property_ids(assignment.user) == []
        assert (
            capabilities.caretaker_permissions_for(assignment.user, assignment.property_id) == set()
        )

    def test_can_manage_property_honours_the_granted_subset(self, caretaker_assignment_factory):
        assignment = caretaker_assignment_factory(permissions=[CaretakerPermission.MANAGE_VACANCY])

        assert capabilities.can_manage_property(
            assignment.user, assignment.property_id, CaretakerPermission.MANAGE_VACANCY
        )
        assert not capabilities.can_manage_property(
            assignment.user, assignment.property_id, CaretakerPermission.MANAGE_PHOTOS
        )

    @pytest.mark.parametrize("forbidden", sorted(NEVER_DELEGABLE))
    def test_never_delegable_is_refused_even_if_it_reached_the_column(
        self, caretaker_assignment_factory, forbidden
    ):
        """Defence in depth.

        The model rejects these on write, but a data migration or raw SQL could
        still put one in the array. can_manage_property refuses regardless.
        """
        assignment = caretaker_assignment_factory()
        CaretakerAssignment.all_objects.filter(pk=assignment.pk).update(permissions=[forbidden])
        assignment.refresh_from_db()

        assert not capabilities.can_manage_property(
            assignment.user, assignment.property_id, forbidden
        )

    def test_the_capability_block_reports_managed_properties(self, caretaker_assignment_factory):
        assignment = caretaker_assignment_factory()

        result = capabilities.capabilities_for(assignment.user)

        assert result["manages_properties"] == [assignment.property_id]


# ---------------------------------------------------------------------------
# The permission class
# ---------------------------------------------------------------------------


class TestIsPropertyManager:
    def test_the_owning_landlord_may_write(self, property_factory):
        prop = property_factory()

        assert (
            IsPropertyManager().has_object_permission(request_for(prop.landlord.user), None, prop)
            is True
        )

    def test_an_owners_authority_is_not_limited_by_a_permission_list(
        self, property_factory, caretaker_assignment_factory
    ):
        """An assignment's subset must never restrict the person who granted it."""
        prop = property_factory()
        caretaker_assignment_factory(property=prop, permissions=[])

        assert (
            DeleteProperty().has_object_permission(request_for(prop.landlord.user), None, prop)
            is True
        )

    def test_an_assigned_caretaker_may_write_what_they_were_granted(
        self, caretaker_assignment_factory
    ):
        assignment = caretaker_assignment_factory(permissions=[CaretakerPermission.MANAGE_VACANCY])

        assert (
            ManageVacancy().has_object_permission(
                request_for(assignment.user), None, assignment.property
            )
            is True
        )

    def test_a_caretaker_may_not_do_what_they_were_not_granted(self, caretaker_assignment_factory):
        assignment = caretaker_assignment_factory(permissions=[CaretakerPermission.MANAGE_PHOTOS])

        assert (
            ManageVacancy().has_object_permission(
                request_for(assignment.user), None, assignment.property
            )
            is False
        )

    def test_no_caretaker_may_delete_a_property(self, caretaker_assignment_factory):
        """Never delegable, whatever the assignment says."""
        assignment = caretaker_assignment_factory(permissions=list(CaretakerPermission.values))

        assert (
            DeleteProperty().has_object_permission(
                request_for(assignment.user), None, assignment.property
            )
            is False
        )

    def test_a_caretaker_at_one_property_may_not_write_at_another(
        self, caretaker_assignment_factory, property_factory
    ):
        assignment = caretaker_assignment_factory()
        elsewhere = property_factory()

        assert (
            ManageVacancy().has_object_permission(request_for(assignment.user), None, elsewhere)
            is False
        )

    def test_a_stranger_may_not_write(self, property_factory, tenant):
        prop = property_factory()

        assert IsPropertyManager().has_object_permission(request_for(tenant), None, prop) is False

    def test_reads_are_open(self, property_factory, tenant):
        prop = property_factory()

        assert (
            IsPropertyManager().has_object_permission(request_for(tenant, method="GET"), None, prop)
            is True
        )

    def test_it_resolves_the_property_from_a_child_object(
        self, caretaker_assignment_factory, unit_factory
    ):
        """Units and photos hang off a property; the check follows the FK."""
        assignment = caretaker_assignment_factory(permissions=[CaretakerPermission.MANAGE_VACANCY])
        unit = unit_factory(property=assignment.property)

        assert ManageVacancy().has_object_permission(request_for(assignment.user), None, unit)

    def test_platform_staff_may_write(self, property_factory, staff_user):
        prop = property_factory()

        assert DeleteProperty().has_object_permission(request_for(staff_user), None, prop) is True


# ---------------------------------------------------------------------------
# Tenant scoping
# ---------------------------------------------------------------------------


class TestCaretakerScoping:
    def test_an_unqualified_query_raises(self, caretaker_assignment_factory):
        caretaker_assignment_factory()

        with pytest.raises(TenantScopeError):
            list(CaretakerAssignment.objects.all())

    def test_assignments_scope_through_their_property(
        self,
        caretaker_assignment_factory,
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
        assignment = caretaker_assignment_factory(property=prop)

        assert assignment in CaretakerAssignment.objects.for_tenant(university)
        assert assignment not in CaretakerAssignment.objects.for_tenant(university_factory())

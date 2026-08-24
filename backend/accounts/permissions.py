"""
DRF permission classes that check relationships, not strings (ADR-003).

Every class here answers a question with a definite answer — "does this user
stand in this relationship to this object?" — which is what makes the negative
cases straightforward to assert. The draft's ``user.user_type == 'landlord'``
had no negative case at all, because the field was whatever the client said at
registration.
"""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts import capabilities


class IsPlatformStaff(BasePermission):
    """Platform administrators only.

    ``is_staff``, which is the only meaning of the word here.
    """

    message = "This endpoint is for platform staff."

    def has_permission(self, request, view) -> bool:
        return capabilities.is_platform_staff(request.user)


class IsLandlord(BasePermission):
    """A user holding a LandlordProfile.

    Holding one is not self-service: it is granted, not declared. That is the
    difference from ``user_type``, which the registration payload set.
    """

    message = "You need a landlord profile to do this."

    def has_permission(self, request, view) -> bool:
        return capabilities.is_landlord(request.user)


class IsLandlordOrReadOnly(BasePermission):
    """Reads are open; writes need a landlord profile and ownership."""

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return capabilities.is_landlord(request.user)

    def has_object_permission(self, request, view, obj) -> bool:
        if request.method in SAFE_METHODS:
            return True
        owner = getattr(obj, "landlord", None)
        return owner == request.user or capabilities.is_platform_staff(request.user)


class IsStudent(BasePermission):
    """A user holding a StudentProfile."""

    message = "You need a student profile to do this."

    def has_permission(self, request, view) -> bool:
        return capabilities.is_student(request.user)


class IsVerifiedStudent(BasePermission):
    """A student carrying the verification badge.

    Use only where a university has actually asked for it. Verification is off
    by default (ADR-003), so requiring it unconditionally would exclude every
    student at a school that has not enabled it.
    """

    message = "This action needs a verified student profile."

    def has_permission(self, request, view) -> bool:
        return capabilities.is_verified_student(request.user)


class IsUniversityStaffForTenant(BasePermission):
    """University staff, and only for their own university.

    The scope matters: this role can read student ID documents, so staff at one
    school must never reach another's queue.
    """

    message = "This queue belongs to a different university."

    def has_permission(self, request, view) -> bool:
        return capabilities.is_university_staff(request.user)

    def has_object_permission(self, request, view, obj) -> bool:
        if not capabilities.is_university_staff(request.user):
            return False
        university = getattr(obj, "university", None)
        if university is None:
            profile = getattr(obj, "student_profile", None)
            university = getattr(profile, "university", None)
        return university is not None and university == request.user.staff_profile.university


class IsSelf(BasePermission):
    """The object is the requesting user, or belongs to them."""

    def has_object_permission(self, request, view, obj) -> bool:
        if not request.user.is_authenticated:
            return False
        if obj == request.user:
            return True
        return getattr(obj, "user_id", None) == request.user.id


class IsOwnerOrReadOnly(BasePermission):
    """Reads are open; writes need the object to be the caller's.

    Checks a ``user`` or ``tenant`` relation, whichever the object has.
    """

    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS or request.user.is_authenticated

    def has_object_permission(self, request, view, obj) -> bool:
        if request.method in SAFE_METHODS:
            return True
        if capabilities.is_platform_staff(request.user):
            return True
        for attribute in ("user_id", "tenant_id", "author_id"):
            owner_id = getattr(obj, attribute, None)
            if owner_id is not None:
                return owner_id == request.user.id
        return False

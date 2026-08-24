"""
What a user may do, derived from their relationships (ADR-003).

One module, so authorization is answered in one place. Nothing here reads a
string field on ``User``: every answer comes from a relationship that another
party created and can revoke.

The frontend receives this set from ``/auth/me/`` and never re-derives it from
raw model shapes. The previous client tried to, guessed the field name wrong,
and silently disabled its own navigation for every role (docs/AUDIT.md §5).
"""

from __future__ import annotations

from typing import TypedDict

from django.db import models


class CaretakerPermission(models.TextChoices):
    """What a landlord may delegate to a caretaker (ADR-003).

    This is the maximum an assignment may contain. Values outside it are
    rejected at the model layer rather than merely unused, so the list cannot
    drift from the code that checks it.

    Deliberately absent: deleting a property, transferring ownership, creating
    or revoking assignments, editing the landlord profile or any payout field,
    and posting a ReviewResponse.

    ``RESOLVE_TENANCY_CLAIMS`` is safe to delegate because the *tenant*
    initiates the claim (ADR-004), so confirming is acknowledging someone
    else's assertion rather than creating a reviewer out of nothing.
    """

    MANAGE_UNITS = "manage_units", "Create and edit units"
    MANAGE_VACANCY = "manage_vacancy", "Set vacancy counts"
    MANAGE_PHOTOS = "manage_photos", "Upload and manage photos"
    SET_AVAILABILITY = "set_availability", "Set availability"
    RESOLVE_TENANCY_CLAIMS = "resolve_tenancy_claims", "Confirm or dispute tenancy claims"
    RESPOND_INQUIRIES = "respond_inquiries", "Respond to inquiries"


#: Capabilities a caretaker may never hold, whatever an assignment says.
#:
#: Kept as an explicit list rather than "anything not in CaretakerPermission",
#: so that adding a new permission forces a reader past this comment.
NEVER_DELEGABLE = frozenset(
    {
        "delete_property",
        "transfer_ownership",
        "grant_caretaker_assignments",
        "edit_landlord_profile",
        "edit_payout_details",
        "post_review_response",
    }
)


class Capabilities(TypedDict):
    """The shape ``/auth/me/`` returns.

    Explicit rather than derived client-side, so the client never has to know
    what a LandlordProfile is.
    """

    is_student: bool
    is_landlord: bool
    is_university_staff: bool
    is_staff: bool
    is_verified_student: bool
    verification_status: str | None
    grace_period_ends_at: str | None
    university: str | None
    manages_properties: list[int]


def _has_profile(user, attribute: str) -> bool:
    """Whether a related profile exists, without a query per check.

    ``hasattr`` on a reverse one-to-one raises ``RelatedObjectDoesNotExist``
    internally and returns False, which is the behaviour we want, but it costs
    a query. Callers that need several checks should ``select_related`` first.
    """
    return getattr(user, attribute, None) is not None


def is_landlord(user) -> bool:
    """Whether the user may own properties."""
    if not user.is_authenticated:
        return False
    return _has_profile(user, "landlord_profile")


def is_student(user) -> bool:
    if not user.is_authenticated:
        return False
    return _has_profile(user, "student_profile")


def is_verified_student(user) -> bool:
    """Whether the student's profile carries the verification badge.

    Absence is not a discredit: verification is off by default, and where a
    university has not enabled it nobody here is verified.
    """
    if not is_student(user):
        return False
    return user.student_profile.is_verified


def is_university_staff(user) -> bool:
    if not user.is_authenticated:
        return False
    profile = getattr(user, "staff_profile", None)
    return profile is not None and profile.is_active


def is_platform_staff(user) -> bool:
    """The only meaning of 'platform administrator'.

    The draft had two unrelated ones: ``user_type == 'admin'``, which anyone
    could self-assign at registration, and ``is_staff``. The object-permission
    checks trusted the former.
    """
    return bool(user.is_authenticated and user.is_staff)


def managed_property_ids(user) -> list[int]:
    """Properties this user may manage as a caretaker.

    Only active assignments. Revocation is a flag rather than a delete, so
    filtering on it every time is what makes revocation immediate — a cached
    permission map that outlives a revocation is a real hole (ADR-003).
    """
    if not user.is_authenticated:
        return []

    assignments = getattr(user, "caretaker_assignments", None)
    if assignments is None:
        return []

    return sorted(assignments.filter(is_active=True).values_list("property_id", flat=True))


def caretaker_permissions_for(user, property_id: int) -> set[str]:
    """What this user may do on one property as a caretaker.

    Empty when there is no active assignment, which is also the answer for the
    landlord — ownership is checked separately, and conflating the two would let
    a permission subset restrict the person who granted it.
    """
    if not user.is_authenticated:
        return set()

    assignments = getattr(user, "caretaker_assignments", None)
    if assignments is None:
        return set()

    granted: set[str] = set()
    for assignment in assignments.filter(is_active=True, property_id=property_id):
        granted.update(assignment.permissions)
    return granted


def can_manage_property(user, property_id: int, permission: str) -> bool:
    """Whether ``user`` may do ``permission`` on this property as a caretaker.

    Anything in NEVER_DELEGABLE is refused outright, whatever an assignment
    says, so a malformed permissions array cannot widen the grant.
    """
    if permission in NEVER_DELEGABLE:
        return False
    return permission in caretaker_permissions_for(user, property_id)


def capabilities_for(user) -> Capabilities:
    """The full capability set for a user."""
    student = is_student(user)
    university = None
    if student:
        university = user.student_profile.university.subdomain
    elif is_university_staff(user):
        university = user.staff_profile.university.subdomain

    profile = getattr(user, "student_profile", None) if student else None
    grace_ends = getattr(profile, "grace_period_ends_at", None)

    return Capabilities(
        is_student=student,
        is_landlord=is_landlord(user),
        is_university_staff=is_university_staff(user),
        is_staff=is_platform_staff(user),
        is_verified_student=is_verified_student(user),
        verification_status=getattr(profile, "verification_status", None),
        grace_period_ends_at=grace_ends.isoformat() if grace_ends else None,
        university=university,
        manages_properties=managed_property_ids(user),
    )


def anonymous_capabilities() -> Capabilities:
    """Every capability false. An unknown capability denies."""
    return Capabilities(
        is_student=False,
        is_landlord=False,
        is_university_staff=False,
        is_staff=False,
        is_verified_student=False,
        verification_status=None,
        grace_period_ends_at=None,
        university=None,
        manages_properties=[],
    )

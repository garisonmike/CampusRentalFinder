"""
Shared pytest fixtures.

Provides an API client (anonymous and per-user-type authenticated variants)
plus one fixture per user type the platform currently recognises.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from tests.factories import (
    CampusFactory,
    LandlordFactory,
    LandlordProfileFactory,
    PlatformAdminFactory,
    RentalFactory,
    ReviewFactory,
    StaffFactory,
    StudentProfileFactory,
    TenantFactory,
    UniversityFactory,
    UniversityStaffProfileFactory,
    VerifiedStudentProfileFactory,
)

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client() -> APIClient:
    """An unauthenticated DRF API client."""
    return APIClient()


@pytest.fixture
def authenticate() -> Callable[[User], APIClient]:
    """Return a callable producing a *fresh* client authenticated as a user.

    Deliberately not reusing the ``api_client`` fixture: mutating one shared
    client would silently authenticate the "anonymous" client in any test that
    asks for both, which hides authorisation bugs instead of catching them.
    """

    def _authenticate(user: User) -> APIClient:
        client = APIClient()
        token = RefreshToken.for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        return client

    return _authenticate


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------


@pytest.fixture
def university_factory(db):
    """Build additional universities, for cross-tenant assertions."""
    return UniversityFactory


@pytest.fixture
def campus_factory(db):
    return CampusFactory


@pytest.fixture
def university(db):
    """The tenant under test."""
    return UniversityFactory(
        name="Kenyatta University",
        display_name="KyU",
        slug="kenyatta",
        subdomain="kyu",
        domain="ku.ac.ke",
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant(db, university) -> User:
    """A student.

    Carries a StudentProfile, because under ADR-003 that relationship — not a
    string on User — is what makes someone a student.
    """
    user = cast(User, TenantFactory())
    StudentProfileFactory(user=user, university=university)
    return user


@pytest.fixture
def student_profile(db, university):
    """An unverified student profile."""
    return StudentProfileFactory(university=university)


@pytest.fixture
def verified_student_profile(db, university):
    """A student carrying the verification badge."""
    return VerifiedStudentProfileFactory(university=university)


@pytest.fixture
def verified_student_profile_factory(db):
    return VerifiedStudentProfileFactory


@pytest.fixture
def landlord_profile_factory(db):
    return LandlordProfileFactory


@pytest.fixture
def university_staff(db, university) -> User:
    """A member of university staff, scoped to one institution."""
    return cast(User, UniversityStaffProfileFactory(university=university).user)


@pytest.fixture
def landlord(db) -> User:
    """A landlord account."""
    return cast(User, LandlordFactory())


@pytest.fixture
def platform_admin(db) -> User:
    """A plain user with no platform-staff flag.

    Kept to prove the escalation path is closed. Under the draft this account
    would have set ``user_type='admin'`` in its own registration payload and
    gained edit rights over every listing and review (docs/AUDIT.md §4.4).
    There is now no field it could have set.
    """
    return cast(User, PlatformAdminFactory())


@pytest.fixture
def staff_user(db) -> User:
    """A Django staff/superuser -- what ``IsAdminUser`` actually admits."""
    return cast(User, StaffFactory())


# ---------------------------------------------------------------------------
# Authenticated clients
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_client(authenticate, tenant) -> APIClient:
    return authenticate(tenant)


@pytest.fixture
def landlord_client(authenticate, landlord) -> APIClient:
    return authenticate(landlord)


@pytest.fixture
def staff_client(authenticate, staff_user) -> APIClient:
    return authenticate(staff_user)


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


@pytest.fixture
def rental(db, landlord) -> RentalFactory:
    return RentalFactory(landlord=landlord)


@pytest.fixture
def review(db, rental, tenant) -> ReviewFactory:
    return ReviewFactory(rental=rental, tenant=tenant)

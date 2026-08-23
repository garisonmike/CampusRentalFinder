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
    LandlordFactory,
    PlatformAdminFactory,
    RentalFactory,
    ReviewFactory,
    StaffFactory,
    TenantFactory,
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
# Users
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant(db) -> User:
    """A student/tenant account."""
    return cast(User, TenantFactory())


@pytest.fixture
def landlord(db) -> User:
    """A landlord account."""
    return cast(User, LandlordFactory())


@pytest.fixture
def platform_admin(db) -> User:
    """A user with ``user_type='admin'`` but no Django staff flag.

    Worth keeping distinct: DRF's ``IsAdminUser`` checks ``is_staff``, not
    ``user_type``, so these two are not interchangeable. See docs/AUDIT.md.
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

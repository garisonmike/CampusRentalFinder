"""
The public tenant configuration endpoint (ADR-005).

The React app fetches this before first paint and applies the tokens to
`:root`. It is unauthenticated because the login page itself has to be branded,
and it renders before any token exists.
"""

from __future__ import annotations

import pytest
from rest_framework import status

pytestmark = pytest.mark.django_db

URL = "/api/v1/tenant/config/"


def as_tenant(client, university):
    """Address the request to a tenant subdomain."""
    return client.get(URL, HTTP_HOST=f"{university.subdomain}.example.co.ke")


class TestTenantConfig:
    def test_is_served_without_authentication(self, api_client, university):
        """The login page is branded, and it renders before any token exists."""
        response = as_tenant(api_client, university)

        assert response.status_code == status.HTTP_200_OK

    def test_returns_the_tenants_identity_and_tokens(self, api_client, university):
        body = as_tenant(api_client, university).json()

        assert body["subdomain"] == university.subdomain
        assert body["name"] == university.name
        assert body["display_name"] == university.display_name
        assert body["theme"] == {
            "primary": university.primary_hsl,
            "secondary": university.secondary_hsl,
            "accent": university.accent_hsl,
        }

    def test_sends_only_the_three_overridden_tokens(self, api_client, university):
        """ADR-005 derives foregrounds and --ring by contrast rather than
        storing them, so a tenant cannot configure an unreadable button."""
        theme = as_tenant(api_client, university).json()["theme"]

        assert set(theme) == {"primary", "secondary", "accent"}

    def test_the_payload_stays_small(self, api_client, university):
        """Fetched before first paint on every cold visit."""
        body = as_tenant(api_client, university).json()

        assert set(body) == {
            "subdomain",
            "name",
            "display_name",
            "logo_url",
            "favicon_url",
            "theme",
        }

    def test_leaks_no_verification_policy(self, api_client, university):
        """Policy is internal. It affects flows, not branding."""
        body = as_tenant(api_client, university).json()

        for leaked in (
            "signup_policy",
            "student_email_domains",
            "verification_methods_enabled",
            "id_review_retention_days",
        ):
            assert leaked not in body

    def test_a_different_subdomain_gets_its_own_branding(
        self, api_client, university, university_factory
    ):
        other = university_factory(subdomain="jkuat", primary_hsl="210 90% 40%")

        mine = as_tenant(api_client, university).json()
        theirs = as_tenant(api_client, other).json()

        assert mine["theme"]["primary"] != theirs["theme"]["primary"]

    def test_the_neutral_host_gets_a_404(self, api_client):
        """ADR-001: www is tenant-neutral, so it has no tenant configuration.

        A 404 rather than an error page: the client falls back to the neutral
        palette in index.css, and an unbranded page is a working page.
        """
        response = api_client.get(URL, HTTP_HOST="www.example.co.ke")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_unknown_subdomain_gets_a_404(self, api_client, db):
        response = api_client.get(URL, HTTP_HOST="nosuchschool.example.co.ke")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_deactivated_university_gets_a_404(self, api_client, university):
        university.is_active = False
        university.save(update_fields=["is_active"])

        assert as_tenant(api_client, university).status_code == status.HTTP_404_NOT_FOUND

    def test_is_read_only(self, api_client, university):
        response = api_client.post(
            URL, {}, format="json", HTTP_HOST=f"{university.subdomain}.example.co.ke"
        )

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_appears_in_the_openapi_schema(self, api_client):
        """The frontend generates its client from this."""
        from django.urls import reverse

        schema = api_client.get(reverse("schema"), HTTP_ACCEPT="application/json").json()

        assert "/api/v1/tenant/config/" in schema["paths"]

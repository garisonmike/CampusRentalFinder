"""
Tenant resolution and scoping (ADR-001).

ADR-001 accepts that isolation is enforced by application code, which makes a
single missing filter a cross-tenant leak. These tests cover the two halves of
the mitigation: resolution picks the right tenant (and no tenant when there is
none), and the manager refuses to answer without one.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from config.middleware import (
    TENANT_HEADER,
    TenantResolutionMiddleware,
    _subdomain_from_host,
    get_current_university,
)
from config.tenancy import TenantScopeError
from universities.models import Campus

pytestmark = pytest.mark.django_db


def run_middleware(request):
    """Push a request through the middleware, capturing the resolved tenant."""
    captured = {}

    def view(req):
        captured["university"] = req.university
        captured["source"] = req.university_source
        captured["contextvar"] = get_current_university()
        from django.http import HttpResponse

        return HttpResponse("ok")

    TenantResolutionMiddleware(view)(request)
    return captured


# ---------------------------------------------------------------------------
# Host parsing
# ---------------------------------------------------------------------------


class TestSubdomainParsing:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("kyu.example.co.ke", "kyu"),
            ("KYU.Example.CO.KE", "kyu"),
            ("kyu.example.co.ke:8000", "kyu"),
            ("kyu.example.co.ke.", "kyu"),
        ],
    )
    def test_extracts_the_tenant_label(self, host, expected):
        assert _subdomain_from_host(host) == expected

    def test_a_deeper_name_is_not_a_tenant(self):
        """`a.b.example.co.ke` names no university under example.co.ke."""
        assert _subdomain_from_host("jkuat.staging.example.co.ke") is None

    def test_a_staging_root_resolves_its_own_tenants(self):
        """Environments differ only by SITE_DOMAIN, not by parsing rules."""
        assert (
            _subdomain_from_host("jkuat.staging.example.co.ke", site_domain="staging.example.co.ke")
            == "jkuat"
        )

    def test_a_host_outside_the_site_domain_is_not_a_tenant(self):
        """Guards the .co.ke shape specifically.

        A label-counting rule reads `example.co.ke` as a university called
        "example", because the apex has three labels. Stripping the configured
        domain gets two-part TLDs right, and they are the rule in this market.
        """
        assert _subdomain_from_host("kyu.attacker.test") is None
        assert _subdomain_from_host("example.co.ke") is None

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "localhost:8000",
            "example.co.ke",
            "",
            "   ",
        ],
    )
    def test_no_label_means_no_tenant(self, host):
        """A bare host is not a tenant, and must not be looked up as one."""
        assert _subdomain_from_host(host) is None

    @pytest.mark.parametrize(
        "host", ["www.example.co.ke", "api.example.co.ke", "admin.example.co.ke"]
    )
    def test_reserved_labels_are_never_tenants(self, host):
        """`www` is the canonical public host (ADR-001), not a university."""
        assert _subdomain_from_host(host) is None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestTenantResolution:
    def test_resolves_from_the_subdomain(self, university):
        request = RequestFactory().get("/", HTTP_HOST=f"{university.subdomain}.example.co.ke")

        result = run_middleware(request)

        assert result["university"] == university
        assert result["source"] == "subdomain"

    def test_exposes_the_tenant_through_the_context_variable(self, university):
        """Queue jobs have no request, so the ContextVar is the shared channel."""
        request = RequestFactory().get("/", HTTP_HOST=f"{university.subdomain}.example.co.ke")

        assert run_middleware(request)["contextvar"] == university

    def test_the_context_variable_is_cleared_after_the_response(self, university):
        """Leaking a tenant into the next request on the same worker is the
        exact failure this whole mechanism exists to prevent."""
        request = RequestFactory().get("/", HTTP_HOST=f"{university.subdomain}.example.co.ke")
        run_middleware(request)

        assert get_current_university() is None

    def test_an_inactive_university_does_not_resolve(self, university):
        university.is_active = False
        university.save(update_fields=["is_active"])
        request = RequestFactory().get("/", HTTP_HOST=f"{university.subdomain}.example.co.ke")

        result = run_middleware(request)

        assert result["university"] is None
        assert result["source"] == "unknown"

    def test_an_unknown_subdomain_resolves_to_no_tenant(self, db):
        request = RequestFactory().get("/", HTTP_HOST="nosuchschool.example.co.ke")

        result = run_middleware(request)

        assert result["university"] is None
        assert result["source"] == "unknown"

    def test_the_canonical_host_resolves_to_no_tenant(self, university):
        """ADR-001: public listing content is canonical on a neutral host.

        It must not accidentally pick up a tenant, or the canonical page would
        vary by whichever university happened to match.
        """
        request = RequestFactory().get("/", HTTP_HOST="www.example.co.ke")

        result = run_middleware(request)

        assert result["university"] is None
        assert result["source"] == "unresolved"

    def test_a_request_without_a_tenant_is_not_rejected(self, db):
        """Auth, the schema and the probes all serve tenant-less requests."""
        request = RequestFactory().get("/", HTTP_HOST="example.co.ke")

        assert run_middleware(request)["university"] is None


class TestHeaderFallback:
    def test_the_header_resolves_a_tenant_when_the_host_has_no_subdomain(
        self, university, settings
    ):
        """Local development and the test suite have no usable subdomain."""
        settings.TENANT_HEADER_FALLBACK_ENABLED = True
        request = RequestFactory().get(
            "/", HTTP_HOST="localhost:8000", **{TENANT_HEADER: university.subdomain}
        )

        result = run_middleware(request)

        assert result["university"] == university
        assert result["source"] == "header"

    def test_the_header_is_ignored_when_the_fallback_is_disabled(self, university, settings):
        """Production disables this. If it were honoured on a deployed host,
        any client could read another tenant's data by setting a header."""
        settings.TENANT_HEADER_FALLBACK_ENABLED = False
        request = RequestFactory().get(
            "/", HTTP_HOST="localhost:8000", **{TENANT_HEADER: university.subdomain}
        )

        assert run_middleware(request)["university"] is None

    def test_the_subdomain_wins_over_the_header(self, university, university_factory, settings):
        """A header must never override a real host, even in development."""
        settings.TENANT_HEADER_FALLBACK_ENABLED = True
        other = university_factory(subdomain="other")
        request = RequestFactory().get(
            "/",
            HTTP_HOST=f"{university.subdomain}.example.co.ke",
            **{TENANT_HEADER: other.subdomain},
        )

        assert run_middleware(request)["university"] == university


# ---------------------------------------------------------------------------
# The manager, end to end
# ---------------------------------------------------------------------------


class TestScopedQueriesAcrossTenants:
    def test_a_query_scoped_to_one_tenant_never_sees_another(
        self, university, university_factory, campus_factory
    ):
        other = university_factory()
        mine = campus_factory(university=university, name="Main")
        theirs = campus_factory(university=other, name="Main")

        visible = list(Campus.objects.for_tenant(university))

        assert mine in visible
        assert theirs not in visible

    def test_forgetting_to_scope_raises_rather_than_returning_everything(
        self, university, university_factory, campus_factory
    ):
        campus_factory(university=university)
        campus_factory(university=university_factory())

        # The mistake this catches would have silently returned both rows.
        with pytest.raises(TenantScopeError):
            list(Campus.objects.all())

    def test_scoping_by_primary_key_works_too(self, university, campus_factory):
        campus_factory(university=university)

        assert Campus.objects.for_tenant(university.pk).count() == 1

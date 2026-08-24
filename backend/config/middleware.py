"""
Tenant resolution (ADR-001).

Resolves the current ``University`` once per request and puts it on the request
and in a context variable. The middleware is deliberately thin: it resolves and
stores, nothing more. ADR-001 flags ``request.university`` as ambient state, and
the mitigation is that service functions take the university as an explicit
argument rather than reaching for the request.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

import structlog
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from universities.models import University

logger = structlog.get_logger("campusrental.tenancy")

#: The resolved tenant for the current request.
#:
#: A ContextVar rather than thread-local state so it survives async views and
#: is isolated per task. Jobs on the queue set it explicitly; they have no
#: request to read.
current_university: ContextVar[University | None] = ContextVar("current_university", default=None)

#: Header naming a tenant when the host has no usable subdomain.
#:
#: Development and tests only. On a deployed host it would let any client read
#: another tenant's data, so prod.py raises at import if it is ever enabled.
TENANT_HEADER = "HTTP_X_UNIVERSITY"

#: Host labels that are never a tenant.
RESERVED_SUBDOMAINS = frozenset({"www", "api", "admin", "internal", "static", "media", "localhost"})


def get_current_university() -> University | None:
    """The tenant for the current request or job, if one was resolved."""
    return current_university.get()


def _subdomain_from_host(host: str, site_domain: str | None = None) -> str | None:
    """The tenant label from a Host header, or None.

    ``kyu.example.co.ke`` -> ``kyu``, given a site domain of ``example.co.ke``.

    Derived by stripping the configured site domain rather than by counting
    labels. Counting looks fine on ``kyu.example.com`` and is wrong on the
    launch market's own domain shape: ``example.co.ke`` has three labels, so a
    "three or more means a tenant" rule reads the apex as a university called
    "example". Two-part TLDs are the rule here, not the exception.
    """
    hostname = host.split(":", 1)[0].strip().lower().rstrip(".")
    if not hostname:
        return None

    root = site_domain if site_domain is not None else settings.SITE_DOMAIN
    root = root.split(":", 1)[0].strip().lower().rstrip(".")
    if not root:
        return None

    if hostname == root:
        return None

    suffix = f".{root}"
    if not hostname.endswith(suffix):
        # A host we do not serve. Not a tenant, and not our business to guess.
        return None

    label = hostname[: -len(suffix)]
    if not label or "." in label:
        # Empty, or a deeper name than one label. Neither is a tenant.
        return None

    if label in RESERVED_SUBDOMAINS:
        return None

    return label


class TenantResolutionMiddleware:
    """Resolve the tenant from the subdomain, or from a header in dev/test.

    A request whose tenant cannot be resolved is **not** rejected here: public
    listing content is canonical on a tenant-neutral host (ADR-001), and the
    health probes, the schema and authentication all serve requests with no
    tenant. Views that need one ask for it; ``config.tenancy`` makes forgetting
    a loud error rather than a silent leak.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        university, source = self._resolve(request)

        request.university = university  # type: ignore[attr-defined]
        request.university_source = source  # type: ignore[attr-defined]
        token = current_university.set(university)

        structlog.contextvars.bind_contextvars(tenant=university.subdomain if university else None)

        try:
            return self.get_response(request)
        finally:
            current_university.reset(token)
            structlog.contextvars.unbind_contextvars("tenant")

    def _resolve(self, request: HttpRequest) -> tuple[University | None, str]:
        subdomain = _subdomain_from_host(request.get_host())
        source = "subdomain"

        if subdomain is None and settings.TENANT_HEADER_FALLBACK_ENABLED:
            header_value = request.META.get(TENANT_HEADER, "").strip().lower()
            if header_value:
                subdomain, source = header_value, "header"

        if not subdomain:
            return None, "unresolved"

        university = (
            University.objects.filter(subdomain=subdomain, is_active=True)
            .only(
                "id",
                "name",
                "display_name",
                "slug",
                "subdomain",
                "signup_policy",
                "verification_required_to_review",
            )
            .first()
        )

        if university is None:
            # A host that looks like a tenant but names none. Logged because it
            # is usually a DNS record pointing at a university we deactivated.
            logger.info("tenant_not_found", subdomain=subdomain, source=source)
            return None, "unknown"

        return university, source


class RequireTenantMiddleware:
    """Optional: reject tenant-scoped paths that resolved no tenant.

    Not installed by default. ``config.tenancy`` already turns a missing tenant
    into an exception at query time, which is a better error than a blanket 400
    — it names the model. This exists for the case where a deployment wants the
    boundary enforced at the edge as well.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from config.hosts import HostClass, classify

        match = getattr(request, "resolver_match", None)
        if match is not None:
            host_class = classify(match.view_name, namespace=match.namespace or "")
            if host_class is HostClass.TENANT_SCOPED and get_current_university() is None:
                return JsonResponse({"detail": "No university resolved for this host."}, status=400)

        return self.get_response(request)

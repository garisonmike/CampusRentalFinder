"""
Startup checks for the configuration mistakes that are one step away.

A Django system check rather than a runtime assertion, so it runs on
`manage.py check` in CI and on every `runserver`, and names the problem rather
than surfacing as something stranger later.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register


def _media_hosts() -> list[str]:
    """Hosts the public media bucket can be served from.

    Both the configured endpoint and any `custom_domain` on the public
    storage backend -- the second does not exist today, and it is the one that
    makes this check worth having before it does.
    """
    hosts: list[str] = []

    endpoint = getattr(settings, "S3_ENDPOINT_URL", "") or ""
    if "//" in endpoint:
        hosts.append(endpoint.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0])

    # `settings.STORAGES` is typed as a bare mapping, so the nested reads come
    # back as `object`. Narrowed rather than ignored: this check exists to be
    # right about a configuration nobody has written yet.
    storages: dict = settings.STORAGES
    default = storages.get("default", {})
    options = default.get("OPTIONS", {}) if isinstance(default, dict) else {}
    custom = options.get("custom_domain") if isinstance(options, dict) else None
    if custom:
        hosts.append(str(custom).split("/", 1)[0])

    return [host for host in hosts if host]


def _contains(cookie_domain: str, host: str) -> bool:
    """Whether a cookie scoped to `cookie_domain` would be sent to `host`.

    A leading dot is the old syntax and still widely written; browsers treat
    `.example.com` and `example.com` identically for this purpose, so both are
    normalised before comparing.
    """
    scope = cookie_domain.lstrip(".").lower()
    target = host.lower()

    return target == scope or target.endswith(f".{scope}")


@register()
def check_cookie_scope_excludes_the_media_host(app_configs, **kwargs):
    """Refuse to boot if a cookie would be sent to the media host.

    ADR-001 requires session and CSRF cookies to stay host-only. The reason
    this is a check and not a comment: two ordinary changes -- a branded media
    domain, and a domain cookie so sessions work across tenant subdomains --
    are individually harmless and together put the object store inside the
    application's cookie scope. At that point any file served with an active
    content type is stored XSS with the session cookie in reach.

    Neither change would look dangerous in review, which is exactly why the
    second one has to fail loudly.
    """
    errors = []
    hosts = _media_hosts()

    for setting in ("SESSION_COOKIE_DOMAIN", "CSRF_COOKIE_DOMAIN"):
        value = getattr(settings, setting, None)
        if not value:
            continue

        errors.append(
            Error(
                f"{setting} is set to {value!r}. ADR-001 requires host-only cookies.",
                hint=(
                    "Tenants are separated by subdomain, and a domain-wide "
                    "cookie stops distinguishing what the subdomains exist to "
                    "distinguish. Cross-tenant identity is a feature to "
                    "design, not a cookie attribute to widen."
                ),
                id="security.E001",
            )
        )

        for host in hosts:
            if _contains(str(value), host):
                errors.append(
                    Error(
                        f"{setting}={value!r} would send cookies to the media host {host!r}.",
                        hint=(
                            "Public media is user-uploaded. A file served with "
                            "an active content type from a host inside the "
                            "app's cookie scope is stored XSS with the session "
                            "cookie in reach. Serve media from a domain the "
                            "cookie does not cover."
                        ),
                        id="security.E002",
                    )
                )

    return errors

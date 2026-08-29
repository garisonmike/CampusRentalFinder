"""The system checks that refuse to boot, exercised (E1/E2).

A check registered and never asserted on is a check whose first real run is
in production. These call it directly with the settings it exists to reject,
including the branded-media-domain shape that does not exist in this
configuration yet -- which is the case it was written for.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from config.security_checks import (
    _contains,
    _media_hosts,
    check_cookie_scope_excludes_the_media_host,
)


def ids(errors) -> set[str]:
    return {error.id for error in errors}


class TestCookiesStayHostOnly:
    def test_the_default_configuration_passes(self):
        """Host-only today because nobody set a domain -- which is the reason
        the check exists rather than a reason it is unnecessary."""
        assert check_cookie_scope_excludes_the_media_host(None) == []

    @pytest.mark.parametrize("setting", ["SESSION_COOKIE_DOMAIN", "CSRF_COOKIE_DOMAIN"])
    def test_any_cookie_domain_at_all_is_refused(self, setting):
        """E001 does not depend on where media lives.

        ADR-001 separates tenants by subdomain, so a domain-wide cookie stops
        distinguishing what the subdomains exist to distinguish -- regardless
        of whether the media host happens to fall inside it today.
        """
        with override_settings(**{setting: ".campusrentalfinder.co.ke"}):
            assert "security.E001" in ids(check_cookie_scope_excludes_the_media_host(None))

    def test_a_cookie_domain_covering_the_endpoint_host_is_named_separately(self):
        with override_settings(
            SESSION_COOKIE_DOMAIN=".campusrentalfinder.co.ke",
            S3_ENDPOINT_URL="https://media.campusrentalfinder.co.ke",
        ):
            assert ids(check_cookie_scope_excludes_the_media_host(None)) == {
                "security.E001",
                "security.E002",
            }

    def test_it_catches_the_custom_domain_that_does_not_exist_yet(self):
        """The case worth having the check for.

        Two individually harmless changes -- a branded media domain, and a
        domain cookie so sessions work across tenant subdomains -- combine
        into stored XSS with the session cookie in reach. `custom_domain` is
        unset in this configuration, so a check written only against today's
        settings would pass silently on the day somebody adds one.
        """
        storages = {
            "default": {
                "BACKEND": "storages.backends.s3.S3Storage",
                "OPTIONS": {"custom_domain": "cdn.campusrentalfinder.co.ke"},
            }
        }
        with override_settings(
            STORAGES=storages,
            S3_ENDPOINT_URL="",
            CSRF_COOKIE_DOMAIN="campusrentalfinder.co.ke",
        ):
            assert _media_hosts() == ["cdn.campusrentalfinder.co.ke"]
            assert "security.E002" in ids(check_cookie_scope_excludes_the_media_host(None))

    def test_a_media_host_outside_the_cookie_scope_is_only_the_general_warning(self):
        with override_settings(
            SESSION_COOKIE_DOMAIN=".campusrentalfinder.co.ke",
            S3_ENDPOINT_URL="https://media.example-cdn.net",
        ):
            assert ids(check_cookie_scope_excludes_the_media_host(None)) == {"security.E001"}


class TestTheDomainComparison:
    @pytest.mark.parametrize(
        ("scope", "host", "covered"),
        [
            # The leading dot is the old syntax and browsers ignore the
            # difference, so the check must too.
            (".example.com", "media.example.com", True),
            ("example.com", "media.example.com", True),
            ("example.com", "example.com", True),
            ("EXAMPLE.com", "MEDIA.example.COM", True),
            # Not a subdomain: a suffix match on the string alone would call
            # this covered, and it is a different registrable domain.
            ("example.com", "notexample.com", False),
            ("media.example.com", "example.com", False),
        ],
    )
    def test_only_real_subdomains_count(self, scope, host, covered):
        assert _contains(scope, host) is covered

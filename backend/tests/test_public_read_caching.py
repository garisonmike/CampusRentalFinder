"""
Edge caching and the shared-egress throttle (ADR-001).

Two halves of one problem, and only one of them is solved here.

Kenyan campus wifi puts hundreds of students behind a single egress address.
DRF's anonymous throttle keys on IP, so a `public_read` rate generous for one
person is that rate divided by four hundred for a lecture hall -- and the
failure lands on browsing listings, which is the product, on the busiest day of
the intake.

Caching removes most of that traffic from the origin and therefore from the
throttle. It does not make the keying correct. The second test below models the
hall deliberately and asserts what happens today rather than what should:
`docs/PRE_LAUNCH.md` carries the sizing as an open question, and the point of
having it here is that the *assumption* stops being invisible.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings

from properties.constants import PropertyStatus

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/properties/"


@pytest.fixture
def host(university):
    return f"{university.subdomain}.example.co.ke"


@pytest.fixture
def listing(university, campus_factory, property_factory, campus_distance_factory, unit_factory):
    campus = campus_factory(university=university, is_main=True)
    prop = property_factory(status=PropertyStatus.PUBLISHED)
    campus_distance_factory(property=prop, university=university, campus=campus)
    unit_factory(property=prop)
    return prop


def get(client, url, host, **extra):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.get(url, HTTP_HOST=host, **extra)


class TestPublicReadsAreCacheable:
    def test_the_listing_carries_a_shared_cache_directive(self, api_client, host, listing):
        """`public` is load-bearing: without it a CDN must treat the response
        as private and store nothing at all."""
        response = get(api_client, LIST_URL, host)

        directives = response.headers["Cache-Control"]
        assert "public" in directives
        assert f"s-maxage={settings.PUBLIC_READ_SHARED_MAX_AGE_SECONDS}" in directives

    def test_it_allows_serving_stale_while_refreshing(self, api_client, host, listing):
        """The difference between a slow page and a spinning one when the
        origin is busy."""
        response = get(api_client, LIST_URL, host)

        assert (
            f"stale-while-revalidate={settings.PUBLIC_READ_STALE_SECONDS}"
            in response.headers["Cache-Control"]
        )

    def test_the_detail_and_unit_reads_carry_it_too(self, api_client, host, listing, unit_factory):
        unit = listing.units.first()

        for url in (f"{LIST_URL}{listing.slug}/", f"{LIST_URL}units/{unit.pk}/"):
            assert "public" in get(api_client, url, host).headers["Cache-Control"]

    def test_the_browser_ttl_is_shorter_than_the_shared_one(self):
        """A vacancy count five minutes old is fine; an hour old is the
        staleness the freshness banding exists to expose, and our own cache
        must not introduce it."""
        assert settings.PUBLIC_READ_MAX_AGE_SECONDS < settings.PUBLIC_READ_SHARED_MAX_AGE_SECONDS

    def test_a_signed_in_reader_is_never_marked_public(self, authenticate, tenant, host, listing):
        """Same body today. Marking it `public` would put it in a shared cache
        keyed by URL alone, and the first personalised field added later would
        leak silently into everybody's copy.
        """
        response = get(authenticate(tenant), LIST_URL, host)

        directives = response.headers["Cache-Control"]
        assert "private" in directives
        assert "public" not in directives


class TestOneAddressManyStudents:
    """A lecture hall behind one NAT, modelled.

    This asserts the **keying assumption**, not the number. What it proves is
    that `public_read` is counted per address and not per person -- which is
    what makes the rate a question worth measuring in production rather than a
    detail nobody wrote down.
    """

    #: The configured rate, read rather than restated. `override_settings` on
    #: `REST_FRAMEWORK` does **not** reach the throttle: DRF binds
    #: `SimpleRateThrottle.THROTTLE_RATES` as a class attribute at import, so
    #: an override updates `api_settings`, reports itself as applied, and the
    #: requests run against the real rate. A throttle test written that way
    #: proves nothing and looks like it proves something.
    @staticmethod
    def configured_limit() -> int:
        # `settings.REST_FRAMEWORK` is typed as a bare mapping, so the value
        # comes back as `object`. Read through DRF's own accessor instead,
        # which is where the throttle reads it too.
        from rest_framework.settings import api_settings

        rate = str(api_settings.DEFAULT_THROTTLE_RATES["public_read"])
        return int(rate.split("/")[0])

    def setup_method(self):
        # The throttle history lives in the cache; another test's requests
        # would otherwise count against this one's budget.
        cache.clear()

    def teardown_method(self):
        cache.clear()

    def test_hundreds_of_students_on_one_address_share_one_budget(self, api_client, host, listing):
        limit = self.configured_limit()
        throttled_at = None

        for student in range(1, limit + 20):
            response = get(api_client, LIST_URL, host, REMOTE_ADDR="41.90.64.7")
            if response.status_code == 429:
                throttled_at = student
                break

        assert throttled_at is not None, (
            f"{limit + 19} requests from one address were all served: the "
            f"limit is not reaching this endpoint."
        )

        # The number is not the point and is an open question for production
        # (docs/PRE_LAUNCH.md). The point is that it is spent per address: a
        # hall of four hundred students shares this one budget, so a
        # `public_read` rate is a per-hall rate wearing a per-person name.
        assert throttled_at <= limit + 1

    def test_a_different_address_has_its_own_budget(self, api_client, host, listing):
        """The other half of the same fact. Two students on mobile data are
        two clients; the same two on campus wifi are one."""
        limit = self.configured_limit()
        for _ in range(limit + 5):
            get(api_client, LIST_URL, host, REMOTE_ADDR="41.90.64.7")

        elsewhere = get(api_client, LIST_URL, host, REMOTE_ADDR="197.232.10.1")

        assert elsewhere.status_code == 200

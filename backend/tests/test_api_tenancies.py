"""
Application, claim, tenancy and dispute endpoints (ADR-004).

The two paths must not blur, and the API is where they would. An accepted
application creates a confirmed tenancy directly; a claim opens a confirmation
window. If the API ever routed the first through the second "for uniformity",
the dispute queue would become unbounded and nothing would fail.

The other thing this file guards is the currency contract: `?currency=` works
and there is no status value that means "current", so a client that filtered on
one would silently get an empty page.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.test import override_settings

from properties.constants import PropertyStatus
from tenancies.constants import ClaimStatus, DisputeReason, TenancyStatus
from tenancies.models import Tenancy, TenancyClaim

pytestmark = pytest.mark.django_db

APPLICATIONS = "/api/v1/tenancies/applications/"
TENANCIES = "/api/v1/tenancies/"
CLAIMS = "/api/v1/tenancies/claims/"
DISPUTES = "/api/v1/tenancies/disputes/"


@pytest.fixture
def host(university):
    return f"{university.subdomain}.example.co.ke"


@pytest.fixture
def listing(university, campus_factory, property_factory, unit_factory, campus_distance_factory):
    campus = campus_factory(university=university, is_main=True)
    prop = property_factory(status=PropertyStatus.PUBLISHED)
    campus_distance_factory(property=prop, university=university, campus=campus)
    return prop, unit_factory(property=prop, total_count=40, vacant_count=40)


def get(client, url, host, **params):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.get(url, params, HTTP_HOST=host)


def post(client, url, host, payload=None):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.post(url, payload or {}, format="json", HTTP_HOST=host)


# ---------------------------------------------------------------------------
# The witnessed path
# ---------------------------------------------------------------------------


class TestTheWitnessedPath:
    """ADR-004 §1.1, and the reason the dispute queue is bounded at all."""

    def apply(self, authenticate, tenant, listing, host):
        _prop, unit = listing
        return post(
            authenticate(tenant),
            APPLICATIONS,
            host,
            {
                "unit": unit.pk,
                "move_in_date": (dt.date.today() + dt.timedelta(days=14)).isoformat(),
                "intended_months": 9,
            },
        )

    def test_a_student_can_apply(self, authenticate, tenant, listing, host):
        response = self.apply(authenticate, tenant, listing, host)

        assert response.status_code == 201
        assert response.json()["status"] == "submitted"

    def test_accepting_creates_a_confirmed_tenancy_directly(
        self, authenticate, tenant, listing, host
    ):
        prop, _unit = listing
        application_id = self.apply(authenticate, tenant, listing, host).json()["id"]

        response = post(
            authenticate(prop.landlord.user),
            f"{APPLICATIONS}{application_id}/accept/",
            host,
        )

        assert response.status_code == 201
        assert response.json()["confirmation_source"] == "application"

    def test_the_on_platform_path_creates_no_claim(self, authenticate, tenant, listing, host):
        """The whole volume control. Routing this through a claim "for
        uniformity" would restore the unbounded queue, silently, because the
        code would look tidier."""
        prop, _unit = listing
        application_id = self.apply(authenticate, tenant, listing, host).json()["id"]

        post(authenticate(prop.landlord.user), f"{APPLICATIONS}{application_id}/accept/", host)

        assert TenancyClaim.all_objects.count() == 0

    def test_a_stranger_cannot_accept(self, authenticate, tenant, listing, host, student_profile):
        application_id = self.apply(authenticate, tenant, listing, host).json()["id"]

        response = post(
            authenticate(student_profile.user),
            f"{APPLICATIONS}{application_id}/accept/",
            host,
        )

        assert response.status_code == 404

    def test_a_caretaker_can_accept(
        self, authenticate, tenant, listing, host, caretaker_assignment_factory
    ):
        """A caretaker confirming a stay is a fact they are well placed to
        know (ADR-003)."""
        prop, _unit = listing
        assignment = caretaker_assignment_factory(property=prop)
        application_id = self.apply(authenticate, tenant, listing, host).json()["id"]

        response = post(
            authenticate(assignment.user), f"{APPLICATIONS}{application_id}/accept/", host
        )

        assert response.status_code == 201

    def test_rejecting_creates_nothing(self, authenticate, tenant, listing, host):
        prop, _unit = listing
        application_id = self.apply(authenticate, tenant, listing, host).json()["id"]

        response = post(
            authenticate(prop.landlord.user),
            f"{APPLICATIONS}{application_id}/reject/",
            host,
            {"note": "Already taken."},
        )

        assert response.status_code == 200
        assert Tenancy.all_objects.count() == 0

    def test_the_applicant_can_withdraw(self, authenticate, tenant, listing, host):
        application_id = self.apply(authenticate, tenant, listing, host).json()["id"]

        response = post(authenticate(tenant), f"{APPLICATIONS}{application_id}/withdraw/", host)

        assert response.status_code == 200
        assert response.json()["status"] == "withdrawn"

    def test_accepting_twice_is_a_conflict(self, authenticate, tenant, listing, host):
        prop, _unit = listing
        application_id = self.apply(authenticate, tenant, listing, host).json()["id"]
        client = authenticate(prop.landlord.user)
        post(client, f"{APPLICATIONS}{application_id}/accept/", host)

        response = post(client, f"{APPLICATIONS}{application_id}/accept/", host)

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------


class TestCurrencyIsDerived:
    """The most likely misread in the contract.

    There is no status value meaning 'current', so a client that filtered on
    one would get an empty page and no error.
    """

    def test_the_serialiser_reports_a_derived_currency(
        self, authenticate, tenant, listing, host, tenancy_factory
    ):
        _prop, unit = listing
        tenancy_factory(unit=unit, tenant=tenant, current=True)

        results = get(authenticate(tenant), TENANCIES, host).json()["results"]

        assert results[0]["currency"] == "current"

    def test_no_status_value_means_current(self):
        """Asserted against the enum itself, so adding one fails here."""
        assert "active" not in TenancyStatus.values
        assert "current" not in TenancyStatus.values

    def test_filtering_by_currency_works(
        self, authenticate, tenant, listing, host, tenancy_factory, unit_factory
    ):
        prop, unit = listing
        tenancy_factory(unit=unit, tenant=tenant, current=True)
        tenancy_factory(unit=unit_factory(property=prop, label="B"), tenant=tenant)

        current = get(authenticate(tenant), TENANCIES, host, currency="current").json()
        past = get(authenticate(tenant), TENANCIES, host, currency="past").json()

        assert current["count"] == 1
        assert past["count"] == 1
        assert current["results"][0]["currency"] == "current"

    def test_an_open_ended_stay_is_current_not_past(
        self, authenticate, tenant, listing, host, tenancy_factory
    ):
        """`end_date: null` means running with no agreed end. Most Kenyan
        student lets are month-to-month, so this is the common case."""
        _prop, unit = listing
        tenancy_factory(unit=unit, tenant=tenant, current=True)

        body = get(authenticate(tenant), TENANCIES, host, currency="current").json()

        assert body["results"][0]["end_date"] is None
        assert body["results"][0]["currency"] == "current"

    def test_upcoming_is_its_own_bucket(self, authenticate, tenant, listing, host, tenancy_factory):
        _prop, unit = listing
        tenancy_factory(unit=unit, tenant=tenant, upcoming=True)

        assert get(authenticate(tenant), TENANCIES, host, currency="upcoming").json()["count"] == 1
        assert get(authenticate(tenant), TENANCIES, host, currency="current").json()["count"] == 0

    def test_you_only_see_your_own(
        self, authenticate, tenant, listing, host, tenancy_factory, student_profile
    ):
        _prop, unit = listing
        tenancy_factory(unit=unit, tenant=student_profile.user)

        assert get(authenticate(tenant), TENANCIES, host).json()["count"] == 0

    def test_a_landlord_sees_their_properties_tenancies(
        self, authenticate, listing, host, tenancy_factory, tenant
    ):
        prop, unit = listing
        tenancy_factory(unit=unit, tenant=tenant)

        assert get(authenticate(prop.landlord.user), TENANCIES, host).json()["count"] == 1


# ---------------------------------------------------------------------------
# The claimed path
# ---------------------------------------------------------------------------


class TestTheClaimedPath:
    def raise_claim(self, authenticate, tenant, listing, host, **overrides):
        _prop, unit = listing
        start = dt.date.today() - dt.timedelta(days=300)
        payload = {
            "unit": unit.pk,
            "start_date": start.isoformat(),
            "end_date": (start + dt.timedelta(days=120)).isoformat(),
            "monthly_rent_kes": "9500.00",
            **overrides,
        }
        return post(authenticate(tenant), CLAIMS, host, payload)

    def test_a_student_can_claim_a_past_stay(self, authenticate, tenant, listing, host):
        response = self.raise_claim(authenticate, tenant, listing, host)

        assert response.status_code == 201
        assert response.json()["status"] == "pending"

    def test_the_claim_opens_a_confirmation_window(self, authenticate, tenant, listing, host):
        body = self.raise_claim(authenticate, tenant, listing, host).json()

        assert body["confirmation_deadline"] is not None

    def test_the_landlord_can_confirm(self, authenticate, tenant, listing, host):
        prop, _unit = listing
        claim_id = self.raise_claim(authenticate, tenant, listing, host).json()["id"]

        response = post(authenticate(prop.landlord.user), f"{CLAIMS}{claim_id}/confirm/", host)

        assert response.status_code == 201
        assert response.json()["confirmation_source"] == "landlord"

    def test_the_landlord_can_dispute_with_a_typed_reason(
        self, authenticate, tenant, listing, host
    ):
        prop, _unit = listing
        claim_id = self.raise_claim(authenticate, tenant, listing, host).json()["id"]

        response = post(
            authenticate(prop.landlord.user),
            f"{CLAIMS}{claim_id}/dispute/",
            host,
            {"reason": DisputeReason.NEVER_TENANTED},
        )

        assert response.status_code == 200
        assert response.json()["status"] == ClaimStatus.ESCALATED

    def test_an_untyped_dispute_is_refused(self, authenticate, tenant, listing, host):
        """An untyped dispute cannot be routed, so it could only go to a
        human -- and most must not, or the queue is unbounded."""
        prop, _unit = listing
        claim_id = self.raise_claim(authenticate, tenant, listing, host).json()["id"]

        response = post(
            authenticate(prop.landlord.user),
            f"{CLAIMS}{claim_id}/dispute/",
            host,
            {"note": "I disagree."},
        )

        assert response.status_code == 400

    def test_a_dates_dispute_stays_between_the_parties(self, authenticate, tenant, listing, host):
        prop, _unit = listing
        claim = self.raise_claim(authenticate, tenant, listing, host).json()
        start = dt.date.fromisoformat(claim["start_date"])

        response = post(
            authenticate(prop.landlord.user),
            f"{CLAIMS}{claim['id']}/dispute/",
            host,
            {
                "reason": DisputeReason.DATES_INCORRECT,
                "proposed_start_date": start.isoformat(),
                "proposed_end_date": (start + dt.timedelta(days=100)).isoformat(),
            },
        )

        assert response.json()["status"] == ClaimStatus.DISPUTED
        assert response.json()["escalation_reason"] == ""

    def test_accepting_a_correction_confirms(self, authenticate, tenant, listing, host):
        prop, _unit = listing
        claim = self.raise_claim(authenticate, tenant, listing, host).json()
        start = dt.date.fromisoformat(claim["start_date"])
        post(
            authenticate(prop.landlord.user),
            f"{CLAIMS}{claim['id']}/dispute/",
            host,
            {
                "reason": DisputeReason.DATES_INCORRECT,
                "proposed_start_date": start.isoformat(),
                "proposed_end_date": (start + dt.timedelta(days=100)).isoformat(),
            },
        )

        response = post(authenticate(tenant), f"{CLAIMS}{claim['id']}/accept-correction/", host)

        assert response.status_code == 201

    def test_a_review_defeating_correction_escalates_even_when_accepted(
        self, authenticate, tenant, listing, host
    ):
        """The cheapest attack on the whole mechanism, through the API.

        Dispute, propose dates under the review minimum, wait for the tenant to
        accept. Before ADR-004 §2b the claim would confirm and the review would
        be silently impossible.
        """
        prop, _unit = listing
        claim = self.raise_claim(authenticate, tenant, listing, host).json()
        start = dt.date.fromisoformat(claim["start_date"])
        post(
            authenticate(prop.landlord.user),
            f"{CLAIMS}{claim['id']}/dispute/",
            host,
            {
                "reason": DisputeReason.DATES_INCORRECT,
                "proposed_start_date": start.isoformat(),
                "proposed_end_date": (start + dt.timedelta(days=20)).isoformat(),
            },
        )

        response = post(authenticate(tenant), f"{CLAIMS}{claim['id']}/accept-correction/", host)

        assert response.status_code == 200
        assert response.json()["escalation_reason"] == "correction_defeats_review"
        assert Tenancy.all_objects.count() == 0

    def test_a_stranger_cannot_confirm(self, authenticate, tenant, listing, host, student_profile):
        claim_id = self.raise_claim(authenticate, tenant, listing, host).json()["id"]

        response = post(authenticate(student_profile.user), f"{CLAIMS}{claim_id}/confirm/", host)

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# The administrator's queue
# ---------------------------------------------------------------------------


class TestTheDisputeQueue:
    def escalate(self, authenticate, tenant, listing, host):
        prop, unit = listing
        start = dt.date.today() - dt.timedelta(days=300)
        claim = post(
            authenticate(tenant),
            CLAIMS,
            host,
            {
                "unit": unit.pk,
                "start_date": start.isoformat(),
                "end_date": (start + dt.timedelta(days=120)).isoformat(),
                "monthly_rent_kes": "9500.00",
            },
        ).json()
        post(
            authenticate(prop.landlord.user),
            f"{CLAIMS}{claim['id']}/dispute/",
            host,
            {"reason": DisputeReason.NEVER_TENANTED},
        )
        return claim["id"]

    def test_platform_staff_see_the_queue(self, authenticate, tenant, listing, host, staff_user):
        self.escalate(authenticate, tenant, listing, host)

        response = get(authenticate(staff_user), DISPUTES, host)

        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_a_landlord_does_not(self, authenticate, tenant, listing, host):
        prop, _unit = listing
        self.escalate(authenticate, tenant, listing, host)

        assert get(authenticate(prop.landlord.user), DISPUTES, host).status_code == 403

    def test_it_filters_by_what_must_be_decided(
        self, authenticate, tenant, listing, host, staff_user
    ):
        """Working a mixed queue without knowing which kind of question each
        item is means gathering the wrong evidence first (ADR-004 §2a)."""
        self.escalate(authenticate, tenant, listing, host)
        client = authenticate(staff_user)

        identity = get(client, DISPUTES, host, escalation_reason="identity_disputed")
        counters = get(client, DISPUTES, host, escalation_reason="counter_unresolved")

        assert identity.json()["count"] == 1
        assert counters.json()["count"] == 0

    def test_the_dispute_reason_is_not_rewritten_on_the_way_to_the_queue(
        self, authenticate, tenant, listing, host, staff_user
    ):
        """`dispute_reason` records what the disputer claimed;
        `escalation_reason` says what the admin decides. Collapsing them makes
        the queue harder to work."""
        self.escalate(authenticate, tenant, listing, host)

        row = get(authenticate(staff_user), DISPUTES, host).json()["results"][0]

        assert row["dispute_reason"] == "never_tenanted"
        assert row["escalation_reason"] == "identity_disputed"

    def test_staff_can_resolve(self, authenticate, tenant, listing, host, staff_user):
        claim_id = self.escalate(authenticate, tenant, listing, host)

        response = post(
            authenticate(staff_user),
            f"{DISPUTES}{claim_id}/resolve/",
            host,
            {"uphold_claim": True},
        )

        assert response.status_code == 201
        assert response.json()["confirmation_source"] == "admin"

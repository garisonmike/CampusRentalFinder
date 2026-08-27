"""
Saved-property and inquiry endpoints (ADR-004 §1.1).

The rule this file mostly defends is that **no contact details cross the
boundary in either direction**. That is not squeamishness. The moment a
conversation moves off-platform, a resulting tenancy is one the platform did
not witness — so it arrives later as a *claim* rather than as an accepted
application, and every claim is a dispute surface and a queue entry the
on-platform path never creates.

The filter is deliberately blunt and does not need to be unbeatable: a
determined pair will exchange numbers regardless and the design survives that.
What it stops is the *default* drifting off-platform.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.test import override_settings

from engagement.models import Inquiry, SavedProperty
from properties.constants import PropertyStatus

pytestmark = pytest.mark.django_db

SAVED = "/api/v1/engagement/saved/"
INQUIRIES = "/api/v1/engagement/inquiries/"


@pytest.fixture
def host(university):
    return f"{university.subdomain}.example.co.ke"


@pytest.fixture
def listing(university, campus_factory, property_factory, unit_factory, campus_distance_factory):
    campus = campus_factory(university=university, is_main=True)
    prop = property_factory(status=PropertyStatus.PUBLISHED)
    campus_distance_factory(property=prop, university=university, campus=campus)
    return prop, unit_factory(property=prop)


def get(client, url, host, **params):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.get(url, params, HTTP_HOST=host)


def post(client, url, host, payload=None):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.post(url, payload or {}, format="json", HTTP_HOST=host)


def delete(client, url, host):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.delete(url, HTTP_HOST=host)


# ---------------------------------------------------------------------------
# Saved properties
# ---------------------------------------------------------------------------


class TestSavedProperties:
    def test_a_student_can_save_one(self, authenticate, tenant, listing, host):
        prop, _unit = listing

        response = post(authenticate(tenant), SAVED, host, {"property_slug": prop.slug})

        assert response.status_code == 201
        assert response.json()["property_slug"] == prop.slug

    def test_saving_twice_is_not_an_error(self, authenticate, tenant, listing, host):
        """A double tap on a phone is not a conflict."""
        prop, _unit = listing
        client = authenticate(tenant)
        post(client, SAVED, host, {"property_slug": prop.slug})

        response = post(client, SAVED, host, {"property_slug": prop.slug})

        assert response.status_code == 201
        assert SavedProperty.all_objects.count() == 1

    def test_you_only_see_your_own(self, authenticate, tenant, listing, host, student_profile):
        """What somebody is considering is not public."""
        prop, _unit = listing
        post(authenticate(student_profile.user), SAVED, host, {"property_slug": prop.slug})

        assert get(authenticate(tenant), SAVED, host).json()["count"] == 0

    def test_unsaving_works(self, authenticate, tenant, listing, host):
        prop, _unit = listing
        client = authenticate(tenant)
        post(client, SAVED, host, {"property_slug": prop.slug})

        response = delete(client, f"{SAVED}{prop.slug}/", host)

        assert response.status_code == 204
        assert SavedProperty.all_objects.count() == 0

    def test_unsaving_something_unsaved_is_not_a_404(self, authenticate, tenant, listing, host):
        prop, _unit = listing

        assert delete(authenticate(tenant), f"{SAVED}{prop.slug}/", host).status_code == 204

    def test_anonymous_cannot_save(self, api_client, listing, host):
        prop, _unit = listing

        assert post(api_client, SAVED, host, {"property_slug": prop.slug}).status_code == 401


# ---------------------------------------------------------------------------
# Inquiries
# ---------------------------------------------------------------------------


class TestSendingAnInquiry:
    def test_a_student_can_ask_about_a_unit(self, authenticate, tenant, listing, host):
        _prop, unit = listing

        response = post(
            authenticate(tenant),
            INQUIRIES,
            host,
            {"unit": unit.pk, "message": "Is the water tank shared between blocks?"},
        )

        assert response.status_code == 201
        assert response.json()["status"] == "sent"

    def test_it_is_never_gated_on_verification(
        self, authenticate, listing, host, student_profile_factory, university
    ):
        """A student who cannot ask a landlord a question cannot work out
        whether to apply. Gating it makes verification a precondition for
        USING the platform rather than for transacting on it (ADR-003)."""
        from accounts.gating import registration_gating_snapshot
        from tests.factories import VerifiedStudentProfileFactory

        VerifiedStudentProfileFactory(university=university)
        university.signup_policy = "verification_required"
        university.save(update_fields=["signup_policy"])

        student = student_profile_factory(university=university)
        for field, value in registration_gating_snapshot(university).items():
            setattr(student, field, value)
        student.grace_period_ends_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
        student.save()

        _prop, unit = listing
        response = post(
            authenticate(student.user),
            INQUIRIES,
            host,
            {"unit": unit.pk, "message": "Is there a water tank?"},
        )

        assert response.status_code == 201

    def test_an_optional_move_in_date_is_accepted(self, authenticate, tenant, listing, host):
        _prop, unit = listing
        when = dt.date.today() + dt.timedelta(days=30)

        response = post(
            authenticate(tenant),
            INQUIRIES,
            host,
            {
                "unit": unit.pk,
                "message": "Available then?",
                "preferred_move_in_date": when.isoformat(),
            },
        )

        assert response.json()["preferred_move_in_date"] == when.isoformat()

    def test_the_rate_limit_is_enforced(self, authenticate, tenant, listing, host, unit_factory):
        """An inquiry is an unsolicited message to a stranger, so the limit is
        part of the feature rather than a later hardening pass."""
        prop, _unit = listing
        client = authenticate(tenant)

        with override_settings(INQUIRY_MAX_PER_USER=2):
            for index in range(2):
                unit = unit_factory(property=prop, label=f"Unit {index}")
                assert (
                    post(
                        client, INQUIRIES, host, {"unit": unit.pk, "message": "Hello?"}
                    ).status_code
                    == 201
                )

            extra = unit_factory(property=prop, label="One too many")
            response = post(client, INQUIRIES, host, {"unit": extra.pk, "message": "Hello?"})

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"


class TestContactDetailsAreRefused:
    """Both directions, because the leak has the same consequence either way."""

    def send(self, authenticate, tenant, listing, host, message):
        _prop, unit = listing
        return post(authenticate(tenant), INQUIRIES, host, {"unit": unit.pk, "message": message})

    @pytest.mark.parametrize(
        "message",
        [
            "Call me on 0722123456",
            "Reach me at +254722123456",
            "My number is 0722 123 456",
            "Email me at brenda@example.com",
            "WhatsApp me",
            "find me on Telegram",
        ],
    )
    def test_a_message_with_contact_details_is_refused(
        self, authenticate, tenant, listing, host, message
    ):
        response = self.send(authenticate, tenant, listing, host, message)

        assert response.status_code == 400
        assert "on the platform" in str(response.json())

    def test_an_ordinary_message_goes_through(self, authenticate, tenant, listing, host):
        """The filter must not eat normal questions. A rent figure and a room
        count are not contact details."""
        response = self.send(
            authenticate,
            tenant,
            listing,
            host,
            "Is 12000 the total including water? And are there 2 rooms free in March?",
        )

        assert response.status_code == 201

    def test_the_landlords_reply_is_filtered_too(self, authenticate, tenant, listing, host):
        """A landlord answering "call me on 07..." is the same leak with the
        same consequence."""
        prop, unit = listing
        inquiry_id = post(
            authenticate(tenant), INQUIRIES, host, {"unit": unit.pk, "message": "Free in March?"}
        ).json()["id"]

        response = post(
            authenticate(prop.landlord.user),
            f"{INQUIRIES}{inquiry_id}/respond/",
            host,
            {"response": "Yes -- call me on 0722123456 to arrange a viewing."},
        )

        assert response.status_code == 400

    def test_no_contact_details_appear_in_the_payload(self, authenticate, tenant, listing, host):
        """Neither party's, in either direction, even for a legitimate
        exchange."""
        prop, unit = listing
        post(authenticate(tenant), INQUIRIES, host, {"unit": unit.pk, "message": "Free in March?"})

        body = str(get(authenticate(prop.landlord.user), INQUIRIES, host).json())

        assert tenant.email not in body
        assert prop.landlord.user.email not in body


class TestRespondingToAnInquiry:
    def send(self, authenticate, tenant, listing, host):
        _prop, unit = listing
        return post(
            authenticate(tenant), INQUIRIES, host, {"unit": unit.pk, "message": "Free in March?"}
        ).json()["id"]

    def test_the_landlord_can_respond(self, authenticate, tenant, listing, host):
        prop, _unit = listing
        inquiry_id = self.send(authenticate, tenant, listing, host)

        response = post(
            authenticate(prop.landlord.user),
            f"{INQUIRIES}{inquiry_id}/respond/",
            host,
            {"response": "Yes, two bedsitters are free. Apply through the listing."},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "answered"

    def test_a_caretaker_with_the_permission_can_respond(
        self, authenticate, tenant, listing, host, caretaker_assignment_factory
    ):
        from accounts.capabilities import CaretakerPermission

        prop, _unit = listing
        assignment = caretaker_assignment_factory(
            property=prop, permissions=[CaretakerPermission.RESPOND_INQUIRIES]
        )
        inquiry_id = self.send(authenticate, tenant, listing, host)

        response = post(
            authenticate(assignment.user),
            f"{INQUIRIES}{inquiry_id}/respond/",
            host,
            {"response": "Yes, two are free."},
        )

        assert response.status_code == 200

    def test_a_caretaker_without_it_cannot(
        self, authenticate, tenant, listing, host, caretaker_assignment_factory
    ):
        prop, _unit = listing
        assignment = caretaker_assignment_factory(property=prop, permissions=[])
        inquiry_id = self.send(authenticate, tenant, listing, host)

        response = post(
            authenticate(assignment.user),
            f"{INQUIRIES}{inquiry_id}/respond/",
            host,
            {"response": "Yes."},
        )

        assert response.status_code == 404

    def test_a_stranger_gets_a_404_not_a_403(
        self, authenticate, tenant, listing, host, student_profile
    ):
        """An inquiry they have no relationship to is one whose existence they
        are not entitled to learn."""
        inquiry_id = self.send(authenticate, tenant, listing, host)

        response = post(
            authenticate(student_profile.user),
            f"{INQUIRIES}{inquiry_id}/respond/",
            host,
            {"response": "Not mine."},
        )

        assert response.status_code == 404

    def test_either_party_can_close(self, authenticate, tenant, listing, host):
        inquiry_id = self.send(authenticate, tenant, listing, host)

        response = post(authenticate(tenant), f"{INQUIRIES}{inquiry_id}/close/", host)

        assert response.status_code == 200
        assert response.json()["status"] == "closed"

    def test_a_second_response_is_refused(self, authenticate, tenant, listing, host):
        """One response, and it closes the exchange. A thread is a messaging
        product, and a messaging product is where the conversation stops
        producing an application."""
        prop, _unit = listing
        inquiry_id = self.send(authenticate, tenant, listing, host)
        client = authenticate(prop.landlord.user)
        post(client, f"{INQUIRIES}{inquiry_id}/respond/", host, {"response": "Yes."})

        response = post(client, f"{INQUIRIES}{inquiry_id}/respond/", host, {"response": "Also..."})

        assert response.status_code == 409


class TestInquiryVisibility:
    def test_the_sender_sees_it(self, authenticate, tenant, listing, host):
        _prop, unit = listing
        post(authenticate(tenant), INQUIRIES, host, {"unit": unit.pk, "message": "Free?"})

        assert get(authenticate(tenant), INQUIRIES, host).json()["count"] == 1

    def test_the_landlord_sees_it(self, authenticate, tenant, listing, host):
        prop, unit = listing
        post(authenticate(tenant), INQUIRIES, host, {"unit": unit.pk, "message": "Free?"})

        assert get(authenticate(prop.landlord.user), INQUIRIES, host).json()["count"] == 1

    def test_nobody_else_does(self, authenticate, tenant, listing, host, student_profile):
        _prop, unit = listing
        post(authenticate(tenant), INQUIRIES, host, {"unit": unit.pk, "message": "Free?"})

        assert get(authenticate(student_profile.user), INQUIRIES, host).json()["count"] == 0

    def test_an_inquiry_can_be_traced_to_its_application(self, authenticate, tenant, listing, host):
        """The optional FK that makes the on-platform path traceable end to
        end (ADR-004 §1.1)."""
        _prop, unit = listing
        inquiry_id = post(
            authenticate(tenant), INQUIRIES, host, {"unit": unit.pk, "message": "Free?"}
        ).json()["id"]

        application = post(
            authenticate(tenant),
            "/api/v1/tenancies/applications/",
            host,
            {
                "unit": unit.pk,
                "move_in_date": (dt.date.today() + dt.timedelta(days=14)).isoformat(),
                "intended_months": 9,
                "inquiry": inquiry_id,
            },
        )

        assert application.status_code == 201
        assert application.json()["inquiry"] == inquiry_id
        assert Inquiry.all_objects.get(pk=inquiry_id).applications.count() == 1

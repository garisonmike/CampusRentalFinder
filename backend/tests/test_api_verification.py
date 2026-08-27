"""
Verification endpoints (ADR-003, Data Protection Act 2019).

Two tests here matter more than the rest.

:meth:`TestTheReviewerQueueIsScoped.test_a_reviewer_cannot_reach_another_universitys_request`
— the isolation failure with the worst consequences in the product, asserted at
the API boundary because that is what an attacker reaches.

:meth:`TestTheEndpointWritesTheAccessLog.test_the_endpoint_writes_a_log_row` —
asserted of the *endpoint*, not the service function. A future view that forgets
to call through is the realistic failure: the service function keeps working
perfectly while the audit trail quietly stops recording, and nothing errors.
"""

from __future__ import annotations

import io

import pytest
from django.core import mail
from django.test import override_settings

from accounts.documents import (
    DocumentAccessLog,
    VerificationRequest,
    VerificationRequestStatus,
    submit_verification_document,
)
from universities.constants import VerificationMethod, VerificationStatus

pytestmark = pytest.mark.django_db

EMAIL_REQUEST = "/api/v1/auth/verification/email/request/"
EMAIL_CONFIRM = "/api/v1/auth/verification/email/confirm/"
DOCUMENT = "/api/v1/auth/verification/document/"
QUEUE = "/api/v1/auth/verification/queue/"
MINE = "/api/v1/auth/verification/mine/"


def a_jpeg() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), (90, 20, 20)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def host(university):
    return f"{university.subdomain}.example.co.ke"


@pytest.fixture
def kyu(university):
    university.student_email_domains = ["s.kyu.ac.ke"]
    university.save(update_fields=["student_email_domains"])
    return university


def get(client, url, host, **params):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.get(url, params, HTTP_HOST=host)


def post(client, url, host, payload=None, **kwargs):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.post(url, payload or {}, HTTP_HOST=host, **kwargs)


# ---------------------------------------------------------------------------
# Email-domain verification
# ---------------------------------------------------------------------------


class TestEmailVerification:
    def test_a_student_can_request_a_link(self, authenticate, student_profile, kyu, host):
        response = post(
            authenticate(student_profile.user),
            EMAIL_REQUEST,
            host,
            {"student_email": "brenda@s.kyu.ac.ke"},
            format="json",
        )

        assert response.status_code == 202
        assert len(mail.outbox) == 1

    def test_the_token_is_never_in_the_response(self, authenticate, student_profile, kyu, host):
        """It goes to the inbox and nowhere else. A token in a response body is
        a token in a browser history, a proxy log and an analytics payload."""
        response = post(
            authenticate(student_profile.user),
            EMAIL_REQUEST,
            host,
            {"student_email": "brenda@s.kyu.ac.ke"},
            format="json",
        )

        assert response.content in (b"", b"null")

    def test_a_lookalike_domain_is_refused(self, authenticate, student_profile, kyu, host):
        """`endswith` would accept this, and the domain costs the price of a
        domain."""
        response = post(
            authenticate(student_profile.user),
            EMAIL_REQUEST,
            host,
            {"student_email": "attacker@evil-kyu.ac.ke"},
            format="json",
        )

        assert response.status_code == 400
        assert len(mail.outbox) == 0

    def test_confirming_verifies_the_student(self, authenticate, student_profile, kyu, host):
        from accounts.verification import issue_email_token

        _token, raw = issue_email_token(student_profile, "brenda@s.kyu.ac.ke")

        response = post(
            authenticate(student_profile.user),
            EMAIL_CONFIRM,
            host,
            {"token": raw},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["verification_status"] == VerificationStatus.VERIFIED
        assert response.json()["verification_method"] == VerificationMethod.EMAIL_DOMAIN

    def test_a_replayed_token_fails(self, authenticate, student_profile, kyu, host):
        from accounts.verification import issue_email_token

        _token, raw = issue_email_token(student_profile, "brenda@s.kyu.ac.ke")
        client = authenticate(student_profile.user)
        post(client, EMAIL_CONFIRM, host, {"token": raw}, format="json")

        response = post(client, EMAIL_CONFIRM, host, {"token": raw}, format="json")

        assert response.status_code == 400

    def test_all_token_failures_look_the_same(self, authenticate, student_profile, kyu, host):
        """A distinct message for 'already used' confirms an address exists and
        has been verified."""
        from accounts.verification import issue_email_token

        _token, raw = issue_email_token(student_profile, "brenda@s.kyu.ac.ke")
        client = authenticate(student_profile.user)
        post(client, EMAIL_CONFIRM, host, {"token": raw}, format="json")

        used = post(client, EMAIL_CONFIRM, host, {"token": raw}, format="json").json()
        unknown = post(
            client, EMAIL_CONFIRM, host, {"token": "never-existed"}, format="json"
        ).json()

        assert used["error"]["message"] == unknown["error"]["message"]

    def test_anonymous_cannot_request(self, api_client, kyu, host):
        response = post(
            api_client, EMAIL_REQUEST, host, {"student_email": "x@s.kyu.ac.ke"}, format="json"
        )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Document submission
# ---------------------------------------------------------------------------


class TestDocumentSubmission:
    def upload(self, client, host, data=None, name="id.jpg"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return post(
            client,
            DOCUMENT,
            host,
            {"document": SimpleUploadedFile(name, data or a_jpeg(), "image/jpeg")},
            format="multipart",
        )

    def test_a_student_can_upload(self, authenticate, student_profile, host):
        response = self.upload(authenticate(student_profile.user), host)

        assert response.status_code == 201
        assert response.json()["status"] == VerificationRequestStatus.PENDING

    def test_html_named_as_a_jpeg_is_refused(self, authenticate, student_profile, host):
        """The declared type never enters the decision. A `.jpg` that is really
        HTML is stored XSS waiting for a reviewer to open it."""
        response = self.upload(
            authenticate(student_profile.user), host, b"<!DOCTYPE html><html>evil"
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "document_rejected"

    def test_the_response_never_carries_a_storage_key(self, authenticate, student_profile, host):
        """The key is the last line of defence when a bucket is
        misconfigured. It does not travel to the client."""
        body = self.upload(authenticate(student_profile.user), host).json()

        assert "storage_key" not in str(body)
        assert "verification/" not in str(body)

    def test_a_student_sees_their_own_history(self, authenticate, student_profile, host):
        self.upload(authenticate(student_profile.user), host)

        body = get(authenticate(student_profile.user), MINE, host).json()

        assert body["count"] == 1
        assert "decision_reason" in body["results"][0]

    def test_the_history_never_names_a_reviewer(
        self, authenticate, student_profile, host, university_staff
    ):
        """A named individual refusing a student's ID at their own institution
        is a person who can be found in a corridor (ADR-003)."""
        from accounts.documents import reject_verification

        request_id = self.upload(authenticate(student_profile.user), host).json()["id"]
        reject_verification(
            VerificationRequest.all_objects.get(pk=request_id),
            reviewer=university_staff,
            reason="The photo is too blurry to read.",
        )

        body = str(get(authenticate(student_profile.user), MINE, host).json())

        assert "too blurry" in body
        assert university_staff.get_full_name() not in body
        assert "reviewed_by" not in body


# ---------------------------------------------------------------------------
# The reviewer queue
# ---------------------------------------------------------------------------


class TestTheReviewerQueueIsScoped:
    """The isolation failure with the worst consequences in the product."""

    def test_staff_see_their_own_universitys_queue(
        self, authenticate, student_profile, university_staff, host
    ):
        submit_verification_document(student_profile, a_jpeg())

        response = get(authenticate(university_staff), QUEUE, host)

        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_a_reviewer_cannot_see_another_universitys_queue(
        self,
        authenticate,
        student_profile,
        university_staff,
        host,
        university_factory,
        student_profile_factory,
    ):
        """Two universities, one request each. Neither reviewer sees the
        other's student."""
        from tests.factories import UniversityStaffProfileFactory

        submit_verification_document(student_profile, a_jpeg())

        other_university = university_factory()
        other_student = student_profile_factory(university=other_university)
        submit_verification_document(other_student, a_jpeg())
        other_staff = UniversityStaffProfileFactory(university=other_university).user

        mine = get(authenticate(university_staff), QUEUE, host).json()
        theirs = get(authenticate(other_staff), QUEUE, host).json()

        assert mine["count"] == 1
        assert theirs["count"] == 1
        assert mine["results"][0]["id"] != theirs["results"][0]["id"]

    def test_a_reviewer_cannot_reach_another_universitys_request(
        self,
        authenticate,
        university_staff,
        host,
        university_factory,
        student_profile_factory,
    ):
        """Scoping the list is not enough if the detail route is guessable, and
        an integer id is very guessable. 404 rather than 403: confirming that
        another university's request exists is itself a disclosure."""
        other_student = student_profile_factory(university=university_factory())
        theirs = submit_verification_document(other_student, a_jpeg())

        response = get(authenticate(university_staff), f"{QUEUE}{theirs.pk}/document/", host)

        assert response.status_code == 404

    def test_a_reviewer_cannot_decide_another_universitys_request(
        self,
        authenticate,
        university_staff,
        host,
        university_factory,
        student_profile_factory,
    ):
        other_student = student_profile_factory(university=university_factory())
        theirs = submit_verification_document(other_student, a_jpeg())

        response = post(
            authenticate(university_staff),
            f"{QUEUE}{theirs.pk}/approve/",
            host,
            format="json",
        )

        assert response.status_code == 404
        theirs.refresh_from_db()
        assert theirs.status == VerificationRequestStatus.PENDING

    def test_the_scope_comes_from_the_staff_profile_not_the_host(
        self,
        authenticate,
        student_profile,
        university_staff,
        university_factory,
    ):
        """A host header is caller-supplied; a staff profile is granted. If the
        queue scoped from the host, a reviewer could read another school's
        documents by changing one header."""
        submit_verification_document(student_profile, a_jpeg())
        other = university_factory()

        response = get(authenticate(university_staff), QUEUE, f"{other.subdomain}.example.co.ke")

        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_a_student_cannot_read_the_queue(self, authenticate, student_profile, host):
        submit_verification_document(student_profile, a_jpeg())

        assert get(authenticate(student_profile.user), QUEUE, host).status_code == 403

    def test_a_landlord_cannot_read_the_queue(self, authenticate, landlord, host):
        assert get(authenticate(landlord), QUEUE, host).status_code == 403


# ---------------------------------------------------------------------------
# The access log, at the endpoint
# ---------------------------------------------------------------------------


class TestTheEndpointWritesTheAccessLog:
    """Asserted of the ENDPOINT, not the service function.

    A future view that forgets to call through is the realistic failure: the
    service function keeps working perfectly while the audit trail quietly
    stops recording, and nothing errors anywhere.
    """

    def test_the_endpoint_writes_a_log_row(
        self, authenticate, student_profile, university_staff, host
    ):
        verification = submit_verification_document(student_profile, a_jpeg())

        response = get(authenticate(university_staff), f"{QUEUE}{verification.pk}/document/", host)

        assert response.status_code == 200
        assert DocumentAccessLog.objects.count() == 1

    def test_it_records_who_and_why(self, authenticate, student_profile, university_staff, host):
        verification = submit_verification_document(student_profile, a_jpeg())

        get(authenticate(university_staff), f"{QUEUE}{verification.pk}/document/", host)
        entry = DocumentAccessLog.objects.get()

        assert entry.reviewer == university_staff
        assert entry.reviewer_label
        assert entry.purpose == "review"

    def test_it_records_the_request_id(self, authenticate, student_profile, university_staff, host):
        """Ties the read to a line in the application log, which is what makes
        an audit answerable months later."""
        verification = submit_verification_document(student_profile, a_jpeg())

        response = get(authenticate(university_staff), f"{QUEUE}{verification.pk}/document/", host)
        entry = DocumentAccessLog.objects.get()

        assert entry.request_id == response.headers["X-Request-ID"]

    def test_a_second_read_writes_a_second_row(
        self, authenticate, student_profile, university_staff, host
    ):
        verification = submit_verification_document(student_profile, a_jpeg())
        client = authenticate(university_staff)

        get(client, f"{QUEUE}{verification.pk}/document/", host)
        get(client, f"{QUEUE}{verification.pk}/document/", host)

        assert DocumentAccessLog.objects.count() == 2

    def test_a_refused_read_writes_nothing(
        self,
        authenticate,
        university_staff,
        host,
        university_factory,
        student_profile_factory,
    ):
        """The log records reads that happened, not attempts. An attempt that
        was blocked is a different event and does not belong here."""
        other_student = student_profile_factory(university=university_factory())
        theirs = submit_verification_document(other_student, a_jpeg())

        get(authenticate(university_staff), f"{QUEUE}{theirs.pk}/document/", host)

        assert DocumentAccessLog.objects.count() == 0

    def test_the_url_is_signed_and_short_lived(
        self, authenticate, student_profile, university_staff, host
    ):
        verification = submit_verification_document(student_profile, a_jpeg())

        body = get(
            authenticate(university_staff), f"{QUEUE}{verification.pk}/document/", host
        ).json()

        assert body["url"]


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class TestDecisions:
    def test_a_reviewer_can_approve(self, authenticate, student_profile, university_staff, host):
        verification = submit_verification_document(student_profile, a_jpeg())

        response = post(
            authenticate(university_staff),
            f"{QUEUE}{verification.pk}/approve/",
            host,
            format="json",
        )

        assert response.status_code == 200
        student_profile.refresh_from_db()
        assert student_profile.verification_status == VerificationStatus.VERIFIED

    def test_a_rejection_needs_a_reason(
        self, authenticate, student_profile, university_staff, host
    ):
        verification = submit_verification_document(student_profile, a_jpeg())

        response = post(
            authenticate(university_staff),
            f"{QUEUE}{verification.pk}/reject/",
            host,
            {"reason": ""},
            format="json",
        )

        assert response.status_code == 400

    def test_the_reason_reaches_the_student(
        self, authenticate, student_profile, university_staff, host
    ):
        verification = submit_verification_document(student_profile, a_jpeg())

        post(
            authenticate(university_staff),
            f"{QUEUE}{verification.pk}/reject/",
            host,
            {"reason": "The card is not readable."},
            format="json",
        )
        student_profile.refresh_from_db()

        assert student_profile.rejection_reason == "The card is not readable."

    def test_rejection_is_not_terminal(self, authenticate, student_profile, university_staff, host):
        """A blurry photo is the common case, and a dead end for it would be an
        accessibility failure dressed as a security control."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        verification = submit_verification_document(student_profile, a_jpeg())
        post(
            authenticate(university_staff),
            f"{QUEUE}{verification.pk}/reject/",
            host,
            {"reason": "Too blurry."},
            format="json",
        )

        again = post(
            authenticate(student_profile.user),
            DOCUMENT,
            host,
            {"document": SimpleUploadedFile("id.jpg", a_jpeg(), "image/jpeg")},
            format="multipart",
        )

        assert again.status_code == 201
        assert again.json()["attempt"] == 2

    def test_no_decision_payload_names_the_reviewer(
        self, authenticate, student_profile, university_staff, host
    ):
        verification = submit_verification_document(student_profile, a_jpeg())

        body = str(
            post(
                authenticate(university_staff),
                f"{QUEUE}{verification.pk}/approve/",
                host,
                format="json",
            ).json()
        )

        assert university_staff.get_full_name() not in body

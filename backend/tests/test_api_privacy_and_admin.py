"""
Privacy and university-administration endpoints (ADR-003, ADR-008).

Two rules do most of the work here.

**Identity is confirmed before anything wholesale or irreversible.** A bearer
token proves the session, not the person. For an export — everything the
platform knows about somebody, in one payload — and for erasure, that
difference matters, and a stolen phone should be able to do neither.

**The lockout guard is enforced at the boundary.** A school setting
`verification_required` before it has issued student addresses locks out an
entire intake in the week they most need the platform.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from accounts.privacy_api import ErasureRequest
from universities.constants import SignupPolicy, VerificationMethod

pytestmark = pytest.mark.django_db

EXPORT = "/api/v1/auth/privacy/export/"
ERASURE = "/api/v1/auth/privacy/erasure/"
POLICY = "/api/v1/tenant/policy/"

PASSWORD = "test-password-123"


@pytest.fixture
def host(university):
    return f"{university.subdomain}.example.co.ke"


def get(client, url, host):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.get(url, HTTP_HOST=host)


def post(client, url, host, payload=None):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.post(url, payload or {}, format="json", HTTP_HOST=host)


def patch(client, url, host, payload):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.patch(url, payload, format="json", HTTP_HOST=host)


# ---------------------------------------------------------------------------
# Subject access
# ---------------------------------------------------------------------------


class TestExport:
    def test_it_needs_the_password(self, authenticate, tenant, host):
        """A bearer token proves the session, not the person. This is
        everything we hold about somebody in one payload."""
        response = post(authenticate(tenant), EXPORT, host, {})

        assert response.status_code == 400

    def test_a_wrong_password_is_403_not_401(self, authenticate, tenant, host):
        """401 would make a client discard a perfectly good token and log the
        user out over a typo."""
        response = post(authenticate(tenant), EXPORT, host, {"password": "wrong"})

        assert response.status_code == 403

    def test_the_right_password_returns_the_export(self, authenticate, tenant, host):
        response = post(authenticate(tenant), EXPORT, host, {"password": PASSWORD})

        assert response.status_code == 200
        assert "account" in response.json()
        assert "reviews" in response.json()

    def test_it_carries_no_document_image(self, authenticate, student_profile, host):
        """Returning one would re-expose an identity document to whatever
        channel this travels over."""
        import io

        from PIL import Image

        from accounts.documents import submit_verification_document

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buffer, format="JPEG")
        submit_verification_document(student_profile, buffer.getvalue())

        body = str(
            post(authenticate(student_profile.user), EXPORT, host, {"password": PASSWORD}).json()
        )

        assert "verification/" not in body
        assert "storage_key" not in body

    def test_anonymous_cannot_export(self, api_client, host):
        assert post(api_client, EXPORT, host, {"password": PASSWORD}).status_code == 401


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


class TestErasure:
    def payload(self, **overrides):
        return {"password": PASSWORD, "confirm_understanding": True, **overrides}

    def test_it_needs_the_password(self, authenticate, tenant, host):
        response = post(authenticate(tenant), ERASURE, host, {"confirm_understanding": True})

        assert response.status_code == 400

    def test_it_needs_explicit_confirmation(self, authenticate, tenant, host):
        """Irreversible, and it cannot run twice. A misclick has no undo."""
        response = post(
            authenticate(tenant),
            ERASURE,
            host,
            {"password": PASSWORD, "confirm_understanding": False},
        )

        assert response.status_code == 400

    def test_it_opens_a_cooling_off_window(self, authenticate, tenant, host):
        """Not an immediate delete. A coerced erasure is a real risk where a
        landlord has leverage over a student who reviewed them badly, and the
        window is the only thing that gives the real owner a chance to notice.
        """
        response = post(authenticate(tenant), ERASURE, host, self.payload())

        assert response.status_code == 202
        assert response.json()["status"] == "cooling_off"
        assert response.json()["executes_after"] is not None

    def test_nothing_is_erased_yet(self, authenticate, tenant, host):
        original = tenant.email

        post(authenticate(tenant), ERASURE, host, self.payload())
        tenant.refresh_from_db()

        assert tenant.email == original
        assert tenant.erased_at is None

    def test_the_account_is_not_suspended(self, authenticate, tenant, host):
        """A suspended account cannot cancel its own request, which would turn
        the protection into a trap."""
        post(authenticate(tenant), ERASURE, host, self.payload())
        tenant.refresh_from_db()

        assert tenant.is_active is True

    def test_the_owner_is_notified_out_of_band(self, authenticate, tenant, host):
        """The whole point of the window. A request made from a stolen session
        is invisible to the real owner unless something reaches them by
        another route."""
        from django.core import mail

        post(authenticate(tenant), ERASURE, host, self.payload())

        assert len(mail.outbox) == 1
        assert tenant.email in mail.outbox[0].to
        assert "cancel" in mail.outbox[0].body.lower()

    def test_executing_it_tombstones_the_account(self, authenticate, tenant, host):
        from accounts.retention import execute_erasure

        erasure_id = post(authenticate(tenant), ERASURE, host, self.payload()).json()["id"]
        original = tenant.email

        execute_erasure(erasure_id)
        tenant.refresh_from_db()

        assert tenant.email != original
        assert tenant.erased_at is not None

    def test_the_response_says_what_survives(self, authenticate, tenant, host):
        """The subject is told what is retained and why, not just that
        something happened."""
        body = post(authenticate(tenant), ERASURE, host, self.payload()).json()

        retained = " ".join(body["retained"]).lower()
        assert "former student" in retained
        assert "landlord" in retained

    def test_the_subject_can_cancel_inside_the_window(self, authenticate, tenant, host):
        erasure_id = post(authenticate(tenant), ERASURE, host, self.payload()).json()["id"]

        response = post(
            authenticate(tenant),
            f"{ERASURE}{erasure_id}/cancel/",
            host,
            {"password": PASSWORD},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_a_cancelled_request_never_executes(self, authenticate, tenant, host):
        from accounts.retention import execute_erasure

        erasure_id = post(authenticate(tenant), ERASURE, host, self.payload()).json()["id"]
        post(
            authenticate(tenant),
            f"{ERASURE}{erasure_id}/cancel/",
            host,
            {"password": PASSWORD},
        )

        execute_erasure(erasure_id)
        tenant.refresh_from_db()

        assert tenant.erased_at is None

    def test_nobody_else_can_cancel(self, authenticate, tenant, host, student_profile):
        """Not support, not an administrator. A third party who could cancel
        could also be leaned on, which is the situation the window exists for."""
        erasure_id = post(authenticate(tenant), ERASURE, host, self.payload()).json()["id"]

        response = post(
            authenticate(student_profile.user),
            f"{ERASURE}{erasure_id}/cancel/",
            host,
            {"password": PASSWORD},
        )

        assert response.status_code == 404

    def test_cancelling_needs_the_password(self, authenticate, tenant, host):
        erasure_id = post(authenticate(tenant), ERASURE, host, self.payload()).json()["id"]

        response = post(authenticate(tenant), f"{ERASURE}{erasure_id}/cancel/", host, {})

        assert response.status_code == 400

    def test_a_completed_request_cannot_be_cancelled(self, authenticate, tenant, host):
        from accounts.retention import execute_erasure

        erasure_id = post(authenticate(tenant), ERASURE, host, self.payload()).json()["id"]
        execute_erasure(erasure_id)

        response = post(
            authenticate(tenant),
            f"{ERASURE}{erasure_id}/cancel/",
            host,
            {"password": PASSWORD},
        )

        assert response.status_code >= 400

    def test_reviews_survive_the_erasure(
        self, authenticate, tenant, host, tenancy_factory, unit_factory
    ):
        """Deleting them would make erasure a suppression tool: a landlord
        wanting a bad review gone would need one cooperating student and one
        support ticket."""
        import datetime as dt

        from reviews.models import Review
        from reviews.services import create_review

        end = dt.date.today() - dt.timedelta(days=1)
        tenancy = tenancy_factory(
            unit=unit_factory(),
            tenant=tenant,
            start_date=end - dt.timedelta(days=90),
            end_date=end,
        )
        review = create_review(tenancy, rating=1, comment="The gate never worked.")

        from accounts.retention import execute_erasure

        erasure_id = post(authenticate(tenant), ERASURE, host, self.payload()).json()["id"]
        execute_erasure(erasure_id)

        review.refresh_from_db()
        assert Review.all_objects.filter(pk=review.pk).exists()
        assert review.comment == "The gate never worked."
        assert review.is_published is True

    def test_a_landlord_with_a_running_tenancy_is_blocked(
        self,
        authenticate,
        landlord_profile,
        host,
        property_factory,
        unit_factory,
        tenancy_factory,
        tenant,
    ):
        """A party to a running contract. Erasing their contact details
        mid-tenancy leaves those students with nobody to call."""
        prop = property_factory(landlord=landlord_profile)
        tenancy_factory(unit=unit_factory(property=prop), tenant=tenant, current=True)

        response = post(authenticate(landlord_profile.user), ERASURE, host, self.payload())

        assert response.status_code == 409
        assert response.json()["status"] == "blocked"
        assert response.json()["blockers"]

    def test_an_upcoming_tenancy_blocks_too(
        self,
        authenticate,
        landlord_profile,
        host,
        property_factory,
        unit_factory,
        tenancy_factory,
        tenant,
    ):
        """The student has not needed their counterparty yet, which makes it
        worse rather than better."""
        prop = property_factory(landlord=landlord_profile)
        tenancy_factory(unit=unit_factory(property=prop), tenant=tenant, upcoming=True)

        response = post(authenticate(landlord_profile.user), ERASURE, host, self.payload())

        assert response.status_code == 409
        assert "due to start" in " ".join(response.json()["blockers"])

    def test_a_blocked_erasure_changes_nothing(
        self,
        authenticate,
        landlord_profile,
        host,
        property_factory,
        unit_factory,
        tenancy_factory,
        tenant,
    ):
        """Flag, never silently partial: the subject believing they are erased
        while the platform believes it complied is the worst outcome."""
        prop = property_factory(landlord=landlord_profile)
        tenancy_factory(unit=unit_factory(property=prop), tenant=tenant, current=True)
        original = landlord_profile.user.email

        post(authenticate(landlord_profile.user), ERASURE, host, self.payload())
        landlord_profile.user.refresh_from_db()

        assert landlord_profile.user.email == original
        assert landlord_profile.user.erased_at is None

    def test_the_subject_can_see_their_requests(self, authenticate, tenant, host):
        post(authenticate(tenant), ERASURE, host, self.payload())

        body = get(authenticate(tenant), ERASURE, host).json()

        assert len(body) == 1
        assert body[0]["status"] == "cooling_off"


# ---------------------------------------------------------------------------
# University administration
# ---------------------------------------------------------------------------


class TestUniversityPolicy:
    def test_staff_can_read_their_own_policy(self, authenticate, university_staff, host):
        response = get(authenticate(university_staff), POLICY, host)

        assert response.status_code == 200
        assert response.json()["signup_policy"] == SignupPolicy.OPEN

    def test_a_student_cannot(self, authenticate, tenant, host):
        assert get(authenticate(tenant), POLICY, host).status_code == 403

    def test_a_landlord_cannot(self, authenticate, landlord, host):
        assert get(authenticate(landlord), POLICY, host).status_code == 403

    def test_staff_can_change_the_theme(self, authenticate, university_staff, host):
        response = patch(
            authenticate(university_staff), POLICY, host, {"primary_hsl": "210 90% 40%"}
        )

        assert response.status_code == 200
        assert response.json()["primary_hsl"] == "210 90% 40%"

    def test_the_lockout_guard_refuses_a_premature_requirement(
        self, authenticate, university_staff, host
    ):
        """The specific failure: a school enables verification, sets the
        policy, and only then discovers it has not issued addresses to its
        first-years."""
        response = patch(
            authenticate(university_staff),
            POLICY,
            host,
            {"signup_policy": SignupPolicy.REQUIRED},
        )

        assert response.status_code == 400

    def test_it_is_allowed_once_someone_is_verified(
        self, authenticate, university_staff, host, verified_student_profile
    ):
        """The guard checks an OUTCOME -- has anyone actually got through --
        not configuration, because 'are any methods enabled?' returns yes in
        exactly the case that locks out an intake."""
        response = patch(
            authenticate(university_staff),
            POLICY,
            host,
            {"signup_policy": SignupPolicy.REQUIRED},
        )

        assert response.status_code == 200
        assert response.json()["signup_policy"] == SignupPolicy.REQUIRED

    def test_encouraged_is_allowed_with_nobody_verified(self, authenticate, university_staff, host):
        """Encouraged prompts and lets the student skip, so it cannot lock
        anyone out."""
        response = patch(
            authenticate(university_staff),
            POLICY,
            host,
            {"signup_policy": SignupPolicy.ENCOURAGED},
        )

        assert response.status_code == 200

    def test_an_unknown_verification_method_is_refused(self, authenticate, university_staff, host):
        response = patch(
            authenticate(university_staff),
            POLICY,
            host,
            {"verification_methods_enabled": ["telepathy"]},
        )

        assert response.status_code == 400

    def test_domains_are_normalised(self, authenticate, university_staff, host):
        response = patch(
            authenticate(university_staff),
            POLICY,
            host,
            {"student_email_domains": ["  S.KYU.AC.KE ", ""]},
        )

        assert response.json()["student_email_domains"] == ["s.kyu.ac.ke"]

    def test_an_address_is_not_a_domain(self, authenticate, university_staff, host):
        """A stray `@` here would silently accept nobody."""
        response = patch(
            authenticate(university_staff),
            POLICY,
            host,
            {"student_email_domains": ["someone@s.kyu.ac.ke"]},
        )

        assert response.status_code == 400

    def test_staff_cannot_edit_another_universitys_policy(
        self, authenticate, university_staff, university_factory
    ):
        """Scoped from the staff profile, not the host -- otherwise changing
        one header would reach another school's settings."""
        other = university_factory()

        response = patch(
            authenticate(university_staff),
            POLICY,
            f"{other.subdomain}.example.co.ke",
            {"primary_hsl": "0 100% 50%"},
        )
        other.refresh_from_db()

        assert response.status_code == 200
        assert other.primary_hsl != "0 100% 50%"

    def test_enabling_a_method_takes_effect(self, authenticate, university_staff, host, university):
        patch(
            authenticate(university_staff),
            POLICY,
            host,
            {"verification_methods_enabled": [VerificationMethod.EMAIL_DOMAIN]},
        )
        university.refresh_from_db()

        assert university.verification_methods_enabled == [VerificationMethod.EMAIL_DOMAIN]


class TestTheErasureSweep:
    """A request that enters cooling-off and never executes is a Data
    Protection Act breach that looks like nothing at all: the subject was told
    a date, the date passed, and the record still says `cooling_off`.
    """

    def cooling_off(self, authenticate, tenant, host):
        return post(
            authenticate(tenant),
            ERASURE,
            host,
            {"password": PASSWORD, "confirm_understanding": True},
        ).json()["id"]

    def test_a_request_inside_its_window_is_not_due(self, authenticate, tenant, host):
        from accounts.retention import erasures_due, sweep_due_erasures

        self.cooling_off(authenticate, tenant, host)

        assert erasures_due().count() == 0
        assert sweep_due_erasures() == 0

    def test_it_becomes_due_when_the_window_closes(self, authenticate, tenant, host):
        import datetime as dt

        from django.utils import timezone

        from accounts.retention import sweep_due_erasures

        erasure_id = self.cooling_off(authenticate, tenant, host)
        ErasureRequest.objects.filter(pk=erasure_id).update(
            executes_after=timezone.now() - dt.timedelta(hours=1)
        )

        assert sweep_due_erasures() == 1

    def test_a_cancelled_request_is_never_due(self, authenticate, tenant, host):
        """`executes_after` is nulled on cancel, and the sweep filters on
        `__lte` -- so the null is excluded regardless of where it would sort."""
        from accounts.retention import erasures_due

        erasure_id = self.cooling_off(authenticate, tenant, host)
        post(
            authenticate(tenant),
            f"{ERASURE}{erasure_id}/cancel/",
            host,
            {"password": PASSWORD},
        )

        assert erasures_due().count() == 0

    def test_execution_rechecks_the_blockers(
        self,
        authenticate,
        landlord_profile,
        host,
        property_factory,
        unit_factory,
        tenancy_factory,
        tenant,
    ):
        """A landlord with no running tenancies a week ago may have one now.
        Erasing them mid-tenancy would leave those students with nobody to
        call, so the check runs again at execution rather than only at
        request time."""
        from accounts.retention import execute_erasure

        erasure_id = post(
            authenticate(landlord_profile.user),
            ERASURE,
            host,
            {"password": PASSWORD, "confirm_understanding": True},
        ).json()["id"]

        prop = property_factory(landlord=landlord_profile)
        tenancy_factory(unit=unit_factory(property=prop), tenant=tenant, current=True)

        assert execute_erasure(erasure_id) is False
        landlord_profile.user.refresh_from_db()
        assert landlord_profile.user.erased_at is None

    def test_the_job_tolerates_a_deleted_row(self):
        from accounts.retention import execute_erasure

        assert execute_erasure(999999) is True

    def test_there_is_no_approval_step(self):
        """Asserted structurally, because somebody will want to add one.

        An approval gate would give the platform discretion to REFUSE a
        data-subject erasure request, which is a worse problem than the one it
        solves: the cooling-off window protects the subject from coercion,
        whereas an approver protects nobody and creates a party who can say no.
        """

        statuses = set(ErasureRequest.Status.values)

        assert not statuses & {"pending_approval", "awaiting_review", "approved", "refused"}
        assert statuses == {"cooling_off", "completed", "blocked", "cancelled"}

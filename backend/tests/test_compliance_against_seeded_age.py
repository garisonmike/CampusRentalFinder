"""
The retention and erasure sweeps, against documents that are genuinely old.

These are the jobs whose failure is invisible until a subject access request.
Nothing in the product changes when they stop, nothing errors, and the only
signal is regulated personal data sitting in a bucket long after it should
have gone.

Everything here runs against the seeded compliance surface: documents at every
age relative to **both** independent deadlines, including one exactly on each,
and erasure requests across the cooling-off window. The boundary rows are the
point -- "past the deadline" and "exactly on it" are different questions, and a
`lte` that should have been `lt` is the kind of thing nobody notices until an
auditor asks why a document outlived its own retention window by a day.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from accounts.documents import (
    DocumentAccessLog,
    VerificationDocument,
    VerificationRequest,
    VerificationRequestStatus,
)
from accounts.privacy_api import ErasureRequest
from accounts.retention import (
    delete_verification_document,
    documents_due_for_deletion,
    erasures_due,
    execute_erasure,
    orphaned_document_objects,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def compliance():
    """A seeded compliance surface. Small, because the ages are what matter."""
    with override_settings(DEBUG=True):
        call_command(
            "seed_platform", "--seed", "3", "--properties", "5", "--compliance", verbosity=0
        )


class TestTheSurfaceItself:
    def test_documents_exist_at_both_ends_of_both_deadlines(self, compliance):
        from django.conf import settings

        now = timezone.now()
        decision = settings.VERIFICATION_DECISION_RETENTION_DAYS
        absolute = settings.VERIFICATION_ABSOLUTE_RETENTION_DAYS

        ages = [
            (now - document.uploaded_at).days for document in VerificationDocument.objects.all()
        ]

        assert any(age > absolute for age in ages), "nothing past the absolute deadline"
        assert any(age < decision for age in ages), "nothing comfortably within it"

    def test_a_document_sits_exactly_on_the_absolute_boundary(self, compliance):
        from django.conf import settings

        now = timezone.now()
        boundary = settings.VERIFICATION_ABSOLUTE_RETENTION_DAYS

        assert any(
            (now - document.uploaded_at).days == boundary
            for document in VerificationDocument.objects.all()
        ), "no boundary case, so `lte` versus `lt` is untested"

    def test_a_document_was_actually_viewed_by_a_reviewer(self, compliance):
        """Otherwise the access log is empty and ADR-008's pseudonymisation --
        the thing that has to survive the subject being erased -- has nothing
        to act on."""
        assert DocumentAccessLog.objects.exists()

    def test_erasures_span_the_cooling_off_window(self, compliance):
        statuses = set(ErasureRequest.objects.values_list("status", flat=True))

        assert ErasureRequest.Status.COOLING_OFF in statuses
        assert ErasureRequest.Status.CANCELLED in statuses
        assert erasures_due(timezone.now()).exists(), "nothing is actually due"


class TestRetentionAgainstRealAge:
    def test_the_boundary_document_is_swept_on_the_day(self, compliance):
        """Inclusive, which is the safe direction: delete on the deadline
        rather than a day after it."""
        from django.conf import settings

        now = timezone.now()
        boundary = now - dt.timedelta(days=settings.VERIFICATION_ABSOLUTE_RETENTION_DAYS)
        document = (
            VerificationDocument.objects.filter(uploaded_at__date=boundary.date())
            .order_by("uploaded_at")
            .first()
        )

        assert document is not None
        assert documents_due_for_deletion(now).filter(pk=document.pk).exists()

    def test_a_document_within_both_windows_is_left_alone(self, compliance):
        now = timezone.now()
        recent = VerificationDocument.objects.filter(
            uploaded_at__gte=now - dt.timedelta(days=5)
        ).first()

        assert recent is not None
        assert not documents_due_for_deletion(now).filter(pk=recent.pk).exists()

    def test_deletion_is_confirmed_against_storage(self, compliance):
        """`deleted_at` is written only after a re-read proves the object is
        gone. A store that answers a delete with 204 and keeps the file would
        otherwise leave a compliance record asserting something false."""
        now = timezone.now()
        due = list(documents_due_for_deletion(now).values_list("pk", flat=True))

        assert due, "nothing due, so this asserts nothing"
        for document_id in due:
            assert delete_verification_document(document_id, now=now)

        for document in VerificationDocument.objects.filter(pk__in=due):
            assert document.deleted_at is not None
            # The constraint says a deleted document holds no key; checked
            # here too, because a constraint is only as good as the write path.
            assert document.storage_key == ""

    def test_an_unreviewed_document_is_auto_rejected_and_the_student_told_why(self, compliance):
        """Otherwise they wait on a queue that will never reach them. The
        reason names expiry rather than reading as a refusal -- the difference
        between "try again" and "you were turned down"."""
        now = timezone.now()
        for document_id in list(documents_due_for_deletion(now).values_list("pk", flat=True)):
            delete_verification_document(document_id, now=now)

        expired = VerificationRequest.all_objects.filter(
            status=VerificationRequestStatus.REJECTED, reviewed_by__isnull=True
        )

        assert expired.exists()
        for request in expired:
            assert "resubmit" in request.decision_reason.lower()
            # No reviewer: nobody decided this, a clock did.
            assert request.reviewed_by is None
            assert request.profile.rejection_reason

    def test_every_live_document_row_has_its_object(self, compliance):
        """The direction the sweeps can see. The other one -- an object with
        no row -- is checked by `orphaned_document_objects`, and asserting it
        is empty belongs in the MinIO-gated suite: this one shares an
        in-memory store across the whole session, so leftovers from earlier
        tests would make the claim about them rather than about the seed.
        """
        from django.core.files.storage import storages

        storage = storages["documents"]
        live = VerificationDocument.objects.exclude(storage_key="")

        assert live.exists()
        for document in live:
            assert storage.exists(document.storage_key), f"{document.storage_key} is missing"

    def test_the_orphan_scan_works_at_all(self, compliance):
        """It is the only thing that can see an object the database has never
        heard of, so a scan that silently returned nothing would restore the
        blindness it exists to remove."""
        from django.core.files.base import ContentFile
        from django.core.files.storage import storages

        planted = "verification/planted-orphan-for-this-test.jpg"
        storages["documents"].save(planted, ContentFile(b"not a real document"))

        try:
            assert planted in orphaned_document_objects()
        finally:
            storages["documents"].delete(planted)


class TestErasureAgainstAPopulatedDatabase:
    def test_it_executes_when_due(self, compliance):
        now = timezone.now()
        due = list(erasures_due(now).values_list("pk", flat=True))

        assert due
        for erasure_id in due:
            assert execute_erasure(erasure_id, now=now)

    def test_the_subject_is_pseudonymised_not_deleted(self, compliance):
        """A blank unique column collides on the second erasure, so the
        address becomes a tombstone rather than nothing."""
        now = timezone.now()
        for erasure_id in list(erasures_due(now).values_list("pk", flat=True)):
            execute_erasure(erasure_id, now=now)

        for erasure in ErasureRequest.objects.filter(status=ErasureRequest.Status.COMPLETED):
            user = erasure.user
            user.refresh_from_db()

            assert user.erased_at is not None
            assert user.email.endswith("@erased.invalid")
            assert user.last_name == ""

    def test_a_cancelled_request_is_never_executed(self, compliance):
        """The cooling-off window is the subject's protection against a
        coerced request. A cancellation that did not actually stop it would
        make the window decorative."""
        cancelled = ErasureRequest.objects.filter(status=ErasureRequest.Status.CANCELLED)

        assert cancelled.exists()
        assert not erasures_due(timezone.now()).filter(pk__in=cancelled.values("pk")).exists()

    def test_the_access_log_survives_the_subject(self, compliance):
        """ADR-008: the trail still answers "who opened this, when and why"
        after every link to the person is gone, and can no longer answer
        "which person was that".

        Run against a populated database for the first time -- the FK walk had
        only ever seen a nearly empty one.
        """
        now = timezone.now()
        for erasure_id in list(erasures_due(now).values_list("pk", flat=True)):
            execute_erasure(erasure_id, now=now)

        detached = DocumentAccessLog.objects.filter(document__isnull=True)

        assert detached.exists(), "no log row belonged to an erased subject"
        for row in detached:
            # What survives: who looked, and which case.
            assert row.subject_token
            assert row.reviewer_label
            # What does not: any route back to the person.
            assert row.verification_request_id is None

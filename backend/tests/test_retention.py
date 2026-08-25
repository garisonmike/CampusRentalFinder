"""
Retention deadlines and the auto-rejection on expiry (ADR-003).

The deadline *logic* — which documents are due, what happens to the request
when one expires — runs against the in-memory store, because none of it
depends on storage semantics.

**The verified delete does not, and is not tested here.** `InMemoryStorage` is
a dict whose `delete()` always works and whose `exists()` always tells the
truth, so a verified-delete test against it passes whether or not the
verification does anything. That property lives in `test_retention_minio.py`,
against a real S3 API.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from accounts.documents import (
    VerificationDocument,
    VerificationRequest,
    VerificationRequestStatus,
    approve_verification,
    submit_verification_document,
)
from accounts.retention import (
    delete_verification_document,
    documents_due_for_deletion,
    documents_past_absolute_retention,
    documents_past_decision_retention,
    oldest_overdue_document_age,
    sweep_expired_documents,
    unconfirmed_deletions,
)
from universities.constants import VerificationStatus

pytestmark = pytest.mark.django_db


def a_jpeg() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (80, 80, 80)).save(buffer, format="JPEG")
    return buffer.getvalue()


def age_document(document, *, days: int):
    VerificationDocument.objects.filter(pk=document.pk).update(
        uploaded_at=timezone.now() - dt.timedelta(days=days)
    )
    document.refresh_from_db()
    return document


def age_decision(request, *, days: int):
    VerificationRequest.all_objects.filter(pk=request.pk).update(
        reviewed_at=timezone.now() - dt.timedelta(days=days)
    )
    request.refresh_from_db()
    return request


@pytest.fixture
def submitted(student_profile):
    return submit_verification_document(student_profile, a_jpeg())


# ---------------------------------------------------------------------------
# Deadline one: after a decision
# ---------------------------------------------------------------------------


class TestDecisionRetention:
    def test_a_freshly_decided_document_is_not_due(self, submitted, university_staff):
        approve_verification(submitted, reviewer=university_staff)

        assert submitted.document not in documents_past_decision_retention()

    def test_a_document_is_due_once_its_decision_ages_out(self, submitted, university_staff):
        approve_verification(submitted, reviewer=university_staff)
        age_decision(submitted, days=settings.VERIFICATION_DECISION_RETENTION_DAYS + 1)

        assert submitted.document in documents_past_decision_retention()

    def test_an_undecided_document_is_never_due_on_this_deadline(self, submitted):
        age_document(submitted.document, days=365)

        assert submitted.document not in documents_past_decision_retention()

    def test_the_threshold_comes_from_settings(self, submitted, university_staff):
        approve_verification(submitted, reviewer=university_staff)
        age_decision(submitted, days=3)

        assert submitted.document not in documents_past_decision_retention()

        with override_settings(VERIFICATION_DECISION_RETENTION_DAYS=1):
            assert submitted.document in documents_past_decision_retention()


# ---------------------------------------------------------------------------
# Deadline two: absolute
# ---------------------------------------------------------------------------


class TestAbsoluteRetention:
    """The gap the earlier spec had.

    With only a post-decision deadline, a document nobody ever reviews lives
    for ever — and an unworked queue is the likeliest real-world case, not an
    edge one.
    """

    def test_an_unreviewed_document_is_due_on_the_absolute_deadline(self, submitted):
        age_document(submitted.document, days=settings.VERIFICATION_ABSOLUTE_RETENTION_DAYS + 1)

        assert submitted.document in documents_past_absolute_retention()
        assert submitted.document in documents_due_for_deletion()

    def test_a_recent_unreviewed_document_is_not_due(self, submitted):
        assert submitted.document not in documents_due_for_deletion()

    def test_the_two_deadlines_are_independent(self, submitted, university_staff):
        """A decided document hits the short deadline long before the absolute
        one; an undecided document hits only the absolute one. Neither
        subsumes the other."""
        approve_verification(submitted, reviewer=university_staff)
        age_decision(submitted, days=settings.VERIFICATION_DECISION_RETENTION_DAYS + 1)
        age_document(submitted.document, days=settings.VERIFICATION_DECISION_RETENTION_DAYS + 2)

        assert submitted.document in documents_past_decision_retention()
        assert submitted.document not in documents_past_absolute_retention()
        assert submitted.document in documents_due_for_deletion()

    def test_a_deleted_document_is_never_due_again(self, submitted):
        age_document(submitted.document, days=400)
        delete_verification_document(submitted.document.pk)

        assert documents_due_for_deletion().count() == 0


# ---------------------------------------------------------------------------
# Auto-rejection on expiry
# ---------------------------------------------------------------------------


class TestAutoRejectionOnExpiry:
    """The student must not wait on a queue that will never reach them."""

    def test_an_expired_request_is_rejected(self, submitted):
        age_document(submitted.document, days=400)

        delete_verification_document(submitted.document.pk)
        submitted.refresh_from_db()

        assert submitted.status == VerificationRequestStatus.REJECTED

    def test_the_reason_names_expiry(self, submitted):
        """ "Try again" and "you were refused" are different messages, and the
        student can only act on the first."""
        age_document(submitted.document, days=400)

        delete_verification_document(submitted.document.pk)
        submitted.refresh_from_db()

        assert "resubmit" in submitted.decision_reason.lower()

    def test_no_reviewer_is_recorded(self, submitted):
        """Nobody decided this; a clock did."""
        age_document(submitted.document, days=400)

        delete_verification_document(submitted.document.pk)
        submitted.refresh_from_db()

        assert submitted.reviewed_by is None
        assert submitted.reviewed_at is not None

    def test_the_student_can_resubmit(self, submitted, student_profile):
        age_document(submitted.document, days=400)
        delete_verification_document(submitted.document.pk)

        again = submit_verification_document(student_profile, a_jpeg())

        assert again.pk != submitted.pk

    def test_the_profile_reflects_it(self, submitted, student_profile):
        age_document(submitted.document, days=400)

        delete_verification_document(submitted.document.pk)
        student_profile.refresh_from_db()

        assert student_profile.verification_status == VerificationStatus.REJECTED
        assert "resubmit" in student_profile.rejection_reason.lower()

    def test_an_already_decided_request_is_not_rewritten(self, submitted, university_staff):
        """Expiry closes an *open* request. A decision already made stands —
        the outcome is what retention keeps."""
        approve_verification(submitted, reviewer=university_staff)
        age_decision(submitted, days=400)

        delete_verification_document(submitted.document.pk)
        submitted.refresh_from_db()

        assert submitted.status == VerificationRequestStatus.APPROVED
        assert submitted.reviewed_by == university_staff


# ---------------------------------------------------------------------------
# What survives
# ---------------------------------------------------------------------------


class TestTheOutcomeSurvivesTheImage:
    """The decision outcome is retained. The image is not."""

    def test_the_request_keeps_its_decision(self, submitted, university_staff):
        approve_verification(submitted, reviewer=university_staff, reason="Card matches the roll.")
        age_decision(submitted, days=400)
        decided_at = submitted.reviewed_at

        delete_verification_document(submitted.document.pk)
        submitted.refresh_from_db()

        assert submitted.status == VerificationRequestStatus.APPROVED
        assert submitted.decision_reason == "Card matches the roll."
        assert submitted.reviewed_at == decided_at
        assert submitted.reviewed_by == university_staff

    def test_the_storage_reference_is_cleared(self, submitted):
        age_document(submitted.document, days=400)

        delete_verification_document(submitted.document.pk)
        submitted.document.refresh_from_db()

        assert submitted.document.storage_key == ""
        assert submitted.document.deleted_at is not None

    def test_the_student_stays_verified(self, submitted, university_staff, student_profile):
        """Deleting the evidence must not revoke the badge it earned. The
        decision is the record; the image was only ever how it was reached."""
        approve_verification(submitted, reviewer=university_staff)
        age_decision(submitted, days=400)

        delete_verification_document(submitted.document.pk)
        student_profile.refresh_from_db()

        assert student_profile.verification_status == VerificationStatus.VERIFIED

    def test_the_tombstone_cannot_claim_to_hold_a_key(self, submitted):
        """A row marked deleted while still naming an object is exactly the
        compliance failure this table exists to prevent, so the database
        refuses it."""
        from django.db import IntegrityError, transaction

        age_document(submitted.document, days=400)
        delete_verification_document(submitted.document.pk)
        submitted.document.refresh_from_db()
        submitted.document.storage_key = "verification/still-here.jpg"

        with pytest.raises(IntegrityError), transaction.atomic():
            submitted.document.save()


# ---------------------------------------------------------------------------
# The sweep and its alert
# ---------------------------------------------------------------------------


class TestSweep:
    def test_it_enqueues_what_is_due(self, submitted):
        age_document(submitted.document, days=400)

        assert sweep_expired_documents() == 1

    def test_it_enqueues_nothing_when_nothing_is_due(self, submitted):
        assert sweep_expired_documents() == 0

    def test_it_respects_its_limit(self, student_profile, student_profile_factory):
        for profile in (student_profile, student_profile_factory()):
            request = submit_verification_document(profile, a_jpeg())
            age_document(request.document, days=400)

        assert sweep_expired_documents(limit=1) == 1

    def test_the_alert_reads_the_oldest_overdue_document(self, submitted):
        """Age, not volume, and not job success. One document abandoned for
        six months is a worse breach than a thousand deleted on time."""
        age_document(submitted.document, days=200)

        overdue = oldest_overdue_document_age()

        assert overdue is not None
        assert overdue > dt.timedelta(days=150)

    def test_the_alert_is_silent_when_nothing_is_overdue(self, submitted):
        assert oldest_overdue_document_age() is None

    def test_unconfirmed_deletions_start_empty(self, submitted):
        assert unconfirmed_deletions().count() == 0

    def test_the_sweep_tolerates_a_deleted_row(self):
        assert delete_verification_document(999999) is True

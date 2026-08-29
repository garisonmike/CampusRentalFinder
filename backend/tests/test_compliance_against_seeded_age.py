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
    OrphanScanUnavailableError,
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

        # Within a day, for the same midnight-straddling reason as above.
        assert any(
            abs((now - document.uploaded_at).days - boundary) <= 1
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
        boundary = settings.VERIFICATION_ABSOLUTE_RETENTION_DAYS

        # Selected by age rather than by calendar date. The first version
        # matched `uploaded_at__date` against `(now - 30 days).date()`, where
        # `now` is read here and the seed read its own moments earlier -- so
        # the two disagreed by a day whenever a run straddled midnight, which
        # this one did. A test that fails once a day is a test people learn to
        # re-run.
        document = min(
            VerificationDocument.objects.all(),
            key=lambda candidate: abs((now - candidate.uploaded_at).days - boundary),
        )

        assert abs((now - document.uploaded_at).days - boundary) <= 1
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
        blindness it exists to remove.

        Scanned with the clock advanced past the grace period, because a
        freshly written object is deliberately not an orphan yet -- see the
        next test.
        """
        from django.conf import settings
        from django.core.files.base import ContentFile
        from django.core.files.storage import storages

        planted = "verification/planted-orphan-for-this-test.jpg"
        storages["documents"].save(planted, ContentFile(b"not a real document"))
        later = timezone.now() + dt.timedelta(seconds=settings.DOCUMENT_ORPHAN_GRACE_SECONDS + 10)

        try:
            assert planted in orphaned_document_objects(now=later)
        finally:
            storages["documents"].delete(planted)

    def test_a_freshly_written_object_is_not_an_orphan_yet(self, compliance):
        """The race the grace period exists for.

        An upload writes its bytes inside the transaction that creates the
        row, so between the store and the commit the object exists and the row
        is not yet visible to another connection. A scan without a grace
        period would find that object, call it an orphan, and a sweep acting
        on the finding would delete a student's identity document out from
        under a request that is about to succeed.
        """
        from django.core.files.base import ContentFile
        from django.core.files.storage import storages

        in_flight = "verification/being-uploaded-right-now.jpg"
        storages["documents"].save(in_flight, ContentFile(b"mid-upload"))

        try:
            assert in_flight not in orphaned_document_objects()
        finally:
            storages["documents"].delete(in_flight)

    def test_an_unlistable_bucket_is_not_an_empty_bucket(self, compliance):
        """The scan used to answer a listing failure with `[]`.

        Which is the value it returns when it looked and found nothing. Every
        caller then printed "0 orphans", the operator read a clean bill, and
        the one class of object no row-walking sweep can ever see went
        unreported for exactly as long as the bucket was unreachable.

        This is the rule the function's own docstring states, applied to the
        function: a reconciler must report what it could not check as its own
        number, not fold it into the reassuring one.
        """
        from unittest import mock

        with mock.patch("accounts.retention._storage") as storage:
            storage.return_value.listdir.side_effect = OSError("bucket unreachable")

            with pytest.raises(OrphanScanUnavailableError):
                orphaned_document_objects()

    def test_the_scheduled_reconciler_fails_rather_than_reporting_zero(self, compliance):
        """And the job propagates it.

        A job that swallowed this would log `orphans=0` on every run while the
        bucket was unreachable -- an alert that stays quiet precisely when the
        thing it watches cannot be watched.
        """
        from unittest import mock

        from accounts.retention import reconcile_document_objects

        with mock.patch("accounts.retention._storage") as storage:
            storage.return_value.listdir.side_effect = OSError("bucket unreachable")

            with pytest.raises(OrphanScanUnavailableError):
                reconcile_document_objects()

    def test_the_reconciler_counts_and_alerts_with_the_keys(self, compliance):
        """The path that runs every night.

        "Seventeen" is not a compliance answer -- the operator's next question
        is which ones -- so the alert carries keys, and this asserts it does
        rather than asserting only the count.
        """
        from django.conf import settings
        from django.core.files.base import ContentFile
        from django.core.files.storage import storages

        from accounts.retention import reconcile_document_objects

        planted = "verification/planted-for-the-reconciler.jpg"
        storages["documents"].save(planted, ContentFile(b"orphan"))
        later = timezone.now() + dt.timedelta(seconds=settings.DOCUMENT_ORPHAN_GRACE_SECONDS + 10)

        try:
            assert reconcile_document_objects(now=later) >= 1
            assert planted in orphaned_document_objects(now=later)
        finally:
            storages["documents"].delete(planted)

    def test_the_count_it_reports_is_the_scan_it_ran(self, compliance):
        """Distinct from the unlistable case above, which raises.

        Not asserted as zero: this suite shares one in-memory store across the
        session, so a bare `== 0` would be a claim about whatever earlier tests
        left behind rather than about the reconciler. What must hold is that
        the number reported is the length of the list the scan produced.
        """
        from accounts.retention import reconcile_document_objects

        now = timezone.now()

        assert reconcile_document_objects(now=now) == len(orphaned_document_objects(now=now))

    def test_the_reconciler_is_scheduled(self):
        """The upload ordering narrows the orphan window rather than closing
        it, so this scan is load-bearing. A load-bearing check that runs only
        when an operator remembers to run it is not one.
        """
        from config.jobs.schedule import SCHEDULE

        scheduled = {job.func for job in SCHEDULE if job.enabled}
        assert "accounts.retention.reconcile_document_objects" in scheduled


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


class TestTheReportItself:
    """`run_compliance_sweeps` is how these jobs get watched.

    A reporting command with no test is a command that breaks silently and is
    then not run -- which returns these jobs to the state this whole round
    existed to leave: working or not, with nobody looking.
    """

    def report(self) -> str:
        import io

        buffer = io.StringIO()
        with override_settings(DEBUG=True):
            call_command("run_compliance_sweeps", stdout=buffer)
        return buffer.getvalue()

    def test_it_runs_and_reports_every_section(self, compliance):
        output = self.report()

        for heading in ("Documents", "After the sweep", "Access log", "Erasure"):
            assert heading in output, f"the report lost its {heading} section"

    def test_it_reports_confirmed_deletions(self, compliance):
        output = self.report()

        assert "deletions confirmed:" in output
        assert "deletions unconfirmed:   0" in output

    def test_it_shows_what_the_expired_student_was_told(self, compliance):
        """The question is not whether a rejection happened but whether it
        reads as "try again" rather than "you were refused"."""
        output = self.report()

        assert "auto-rejected on expiry" in output
        # The report truncates the message at 80 characters, so this matches
        # its opening rather than the word "resubmit" at the end of it.
        assert "did not review this in time" in output

    def test_it_reports_erasure_as_pseudonymisation(self, compliance):
        output = self.report()

        assert "email=pseudonymised" in output
        assert "STILL REAL" not in output

    def test_it_refuses_to_run_outside_debug(self):
        """It executes real deletions. Development only."""
        from django.core.management.base import CommandError

        with override_settings(DEBUG=False), pytest.raises(CommandError):
            call_command("run_compliance_sweeps")

    def test_advancing_the_clock_changes_what_is_due(self, compliance):
        """The jobs take `now` rather than being mocked, precisely so history
        can be examined without lying to the clock."""
        import io

        buffer = io.StringIO()
        with override_settings(DEBUG=True):
            call_command("run_compliance_sweeps", "--advance-days", "60", stdout=buffer)
        output = buffer.getvalue()

        # Sixty days on, everything seeded is past its absolute deadline.
        assert "due for deletion:        6" in output

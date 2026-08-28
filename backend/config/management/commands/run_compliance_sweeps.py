"""
Run every retention and erasure sweep against seeded data, and report.

These are the jobs whose failure is invisible until a subject access request.
Nothing in the product changes when they stop; no page errors; the only signal
is regulated personal data sitting in a bucket long after it should have gone.
Watching them work once, against documents that are genuinely old and images
that genuinely exist, is worth more than any unit test of them.

What this reports, and why each question is the one worth asking:

**Did verified deletion actually confirm?** `_delete_and_verify` re-reads after
deleting, because S3-compatible stores answer a delete of an unremovable key
with a 204 in several situations. A job that trusts the return value writes
`deleted_at` over a file that is still there -- a compliance record asserting
something false, which is worse than no record.

**Did absolute expiry auto-reject, and was the student told why?** A document
nobody reviewed is deleted at thirty days regardless. If the request were left
pending the student would wait on a queue that will never reach them, so the
rejection has to name expiry rather than read as a refusal.

**Did erasure execute on schedule, and does the FK walk still find only the
expected survivor?** The walk has only ever run against a nearly empty
database.

Read-only about its own conclusions: it prints what happened and exits 0 even
when something is wrong, because it is an observation tool and a non-zero exit
would make it a check somebody wires into CI and then stops reading.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Run the retention and erasure sweeps against seeded data and report."

    def add_arguments(self, parser):
        parser.add_argument(
            "--advance-days",
            type=int,
            default=0,
            help="Run the sweeps as if this many days had passed. The clock is "
            "passed to the jobs rather than mocked -- every one of them takes "
            "`now` precisely so history can be examined without lying to it.",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        if not settings.DEBUG:
            raise CommandError("This command runs the real sweeps. Development only.")

        self.now = timezone.now() + dt.timedelta(days=options["advance_days"])
        self.stdout.write(f"Running as if it were {self.now:%d %B %Y}.\n")

        self.report_documents_before()
        self.run_retention()
        self.report_documents_after()
        self.report_orphans()
        self.run_erasures()

    # -- documents ---------------------------------------------------------

    def report_documents_before(self) -> None:
        from accounts.retention import (
            documents_due_for_deletion,
            documents_past_absolute_retention,
            documents_past_decision_retention,
            oldest_overdue_document_age,
        )

        due = documents_due_for_deletion(self.now)
        oldest = oldest_overdue_document_age(self.now)

        self.stdout.write(self.style.MIGRATE_HEADING("Documents"))
        self.stdout.write(
            f"  past decision retention: {documents_past_decision_retention(self.now).count()}"
        )
        self.stdout.write(
            f"  past absolute retention: {documents_past_absolute_retention(self.now).count()}"
        )
        self.stdout.write(f"  due for deletion:        {due.count()}")
        self.stdout.write(
            "  oldest overdue:          "
            + ("nothing overdue" if oldest is None else f"{oldest.days} days")
        )

    def run_retention(self) -> None:
        import django_rq

        from accounts.retention import sweep_expired_documents

        enqueued = sweep_expired_documents(now=self.now)
        self.stdout.write(f"  swept:                   {enqueued} enqueued")

        # The jobs take `now` from their own default, so the worker would use
        # the real clock. Run them directly with the advanced one instead --
        # the sweep's selection is what the clock affects, and that has
        # already happened.
        from accounts.documents import VerificationDocument
        from accounts.retention import delete_verification_document

        django_rq.get_queue("default").empty()

        confirmed = unconfirmed = 0
        for document_id in list(
            VerificationDocument.objects.filter(deleted_at__isnull=True).values_list(
                "pk", flat=True
            )
        ):
            from accounts.retention import documents_due_for_deletion

            if not documents_due_for_deletion(self.now).filter(pk=document_id).exists():
                continue
            if delete_verification_document(document_id, now=self.now):
                confirmed += 1
            else:
                unconfirmed += 1

        self.stdout.write(f"  deletions confirmed:     {confirmed}")
        self.stdout.write(
            f"  deletions unconfirmed:   {unconfirmed}"
            + ("" if unconfirmed == 0 else "  <- these keep their key and are retried")
        )

    def report_documents_after(self) -> None:
        from accounts.documents import (
            VerificationDocument,
            VerificationRequest,
            VerificationRequestStatus,
        )
        from accounts.retention import unconfirmed_deletions

        deleted = VerificationDocument.objects.filter(deleted_at__isnull=False)

        self.stdout.write(self.style.MIGRATE_HEADING("After the sweep"))
        self.stdout.write(f"  deleted rows:            {deleted.count()}")
        # The constraint says a deleted document holds no key. Checked here
        # anyway: the halfway state is the compliance failure the table exists
        # to prevent, and a constraint is only as good as the write path.
        self.stdout.write(f"  deleted but keeping a key: {deleted.exclude(storage_key='').count()}")
        self.stdout.write(f"  unconfirmed queue:       {unconfirmed_deletions().count()}")

        expired = VerificationRequest.all_objects.filter(
            status=VerificationRequestStatus.REJECTED, reviewed_by__isnull=True
        )
        self.stdout.write(f"  auto-rejected on expiry: {expired.count()}")
        for request in expired[:3]:
            told = request.decision_reason or "(nothing)"
            self.stdout.write(f"    student was told: {told[:80]}")
            self.stdout.write(
                f"    profile now reads: {request.profile.verification_status}, "
                f"reason {'set' if request.profile.rejection_reason else 'EMPTY'}"
            )

    def report_orphans(self) -> None:
        """Access-log rows whose document is gone, and objects with no row.

        Two directions, because they fail differently. A log row pointing at a
        deleted document is expected -- the row is a tombstone and "who looked
        at this" outlives the image. An **object in the bucket with no row** is
        the one nothing can see: every retention sweep enumerates rows.
        """
        from django.core.files.storage import storages

        from accounts.documents import DocumentAccessLog, VerificationDocument

        logs = DocumentAccessLog.objects.count()
        orphaned_logs = DocumentAccessLog.objects.filter(document__deleted_at__isnull=False).count()

        self.stdout.write(self.style.MIGRATE_HEADING("Access log"))
        self.stdout.write(f"  rows:                    {logs}")
        self.stdout.write(
            f"  pointing at a deleted document: {orphaned_logs}  "
            "(expected: the row is the audit trail and outlives the image)"
        )

        # The question ADR-008 answers: after the subject is erased, can the
        # trail still say who looked at what, without saying who the subject
        # was? A row that lost its subject AND its handle is an audit trail
        # that cannot answer either question.
        erased_subjects = DocumentAccessLog.objects.filter(
            document__requests__profile__user__erased_at__isnull=False
        ).distinct()
        self.stdout.write(f"  whose subject is erased: {erased_subjects.count()}")
        for row in erased_subjects[:3]:
            self.stdout.write(
                f"    reviewer={'set' if row.reviewer_id else 'NULL'}, "
                f"label={row.reviewer_label or 'EMPTY'}, "
                f"case handle={'set' if row.subject_token else 'EMPTY'}, "
                f"purpose={row.purpose}"
            )

        known = set(
            VerificationDocument.objects.exclude(storage_key="").values_list(
                "storage_key", flat=True
            )
        )
        try:
            _dirs, files = storages["documents"].listdir("verification")
            stored = {f"verification/{name}" for name in files}
        except Exception as error:
            self.stdout.write(f"  bucket listing unavailable: {type(error).__name__}: {error}")
            return

        orphans = stored - known
        self.stdout.write(f"  objects in the bucket:   {len(stored)}")
        self.stdout.write(
            f"  objects with no row:     {len(orphans)}"
            + ("" if not orphans else "  <- INVISIBLE to every sweep")
        )

    # -- erasure -----------------------------------------------------------

    def run_erasures(self) -> None:
        from accounts.privacy_api import ErasureRequest
        from accounts.retention import erasures_due, sweep_due_erasures

        self.stdout.write(self.style.MIGRATE_HEADING("Erasure"))
        for status in ErasureRequest.Status:
            self.stdout.write(
                f"  {status.value:<14} {ErasureRequest.objects.filter(status=status).count()}"
            )
        self.stdout.write(f"  due now:                 {erasures_due(self.now).count()}")

        due_ids = list(erasures_due(self.now).values_list("pk", flat=True))
        sweep_due_erasures(now=self.now)

        from accounts.retention import execute_erasure

        executed = 0
        for erasure_id in due_ids:
            if execute_erasure(erasure_id, now=self.now):
                executed += 1

        self.stdout.write(f"  executed:                {executed}")

        for erasure in ErasureRequest.objects.filter(pk__in=due_ids).select_related("user"):
            user = erasure.user
            user.refresh_from_db()
            # Asked as "is it still the real address", not "is it set". The
            # first version of this check read truthiness and reported
            # `email=still set` for a correctly pseudonymised account: the
            # tombstone address is deliberately non-blank, because a blank
            # unique column collides on the second erasure.
            pseudonymous = user.email.endswith("@erased.invalid")
            self.stdout.write(
                f"    {erasure.status}: erased_at={user.erased_at is not None}, "
                f"email={'pseudonymised' if pseudonymous else 'STILL REAL'}, "
                f"name={user.first_name!r}"
            )

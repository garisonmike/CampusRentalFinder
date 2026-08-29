"""
Verification document retention (ADR-003, docs/OPERATIONS.md §3).

**The one job to wire up first.** Nothing in the product changes when it stops.
Nothing errors. National ID documents simply accumulate in object storage, and
the first signal is a subject access request, a breach, or an audit — all of
which arrive after the exposure rather than before.

Two deadlines, and they are **independent**:

``decision_retention_days`` (7)
    After a decision is recorded. The image has served its purpose; the outcome
    is what the platform needs to keep.

``absolute_retention_days`` (30)
    After upload, **whether or not a decision exists**. An earlier version of
    this spec had only the first deadline, which meant a document nobody ever
    reviewed lived for ever — and an unworked queue is the likeliest
    real-world case, not an edge one. On absolute expiry the document is
    deleted *and the request is auto-rejected naming expiry*, so the student
    knows to resubmit instead of waiting on a queue that will never reach them.

**Deletion is verified, never assumed.** S3-compatible stores return success
for a delete of a key that was never removed — a permissions edge, an
eventually-consistent replica, a bucket policy — and a job that trusts the
return value writes ``deleted_at`` over a file that is still there. That is a
compliance record asserting something false, which is worse than no record. So
every delete is followed by a re-read, and ``deleted_at`` is written only when
the object is confirmed gone.
"""

from __future__ import annotations

import datetime as dt

import django_rq
import structlog
from django.conf import settings
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from config.jobs.sweeps import oldest_overdue_age

from .documents import (
    VerificationDocument,
    VerificationRequest,
    VerificationRequestStatus,
)

logger = structlog.get_logger("campusrental.jobs")


class DeletionUnconfirmedError(Exception):
    """The object was still readable after the delete returned."""


def _storage():
    return storages["documents"]


# ---------------------------------------------------------------------------
# What is due
# ---------------------------------------------------------------------------


def documents_past_decision_retention(now: dt.datetime | None = None):
    """Reviewed documents whose decision is older than the short deadline."""
    now = now or timezone.now()
    cutoff = now - dt.timedelta(days=settings.VERIFICATION_DECISION_RETENTION_DAYS)

    return VerificationDocument.objects.filter(
        deleted_at__isnull=True,
        requests__reviewed_at__isnull=False,
        requests__reviewed_at__lte=cutoff,
    ).distinct()


def documents_past_absolute_retention(now: dt.datetime | None = None):
    """Documents older than the absolute deadline, decision or not.

    The clause the earlier spec was missing. Without it an unreviewed document
    lives for ever, and the queue nobody works is the common case.
    """
    now = now or timezone.now()
    cutoff = now - dt.timedelta(days=settings.VERIFICATION_ABSOLUTE_RETENTION_DAYS)

    return VerificationDocument.objects.filter(deleted_at__isnull=True, uploaded_at__lte=cutoff)


def documents_due_for_deletion(now: dt.datetime | None = None):
    """Everything either deadline has caught."""
    now = now or timezone.now()

    return VerificationDocument.objects.filter(
        Q(pk__in=documents_past_decision_retention(now).values("pk"))
        | Q(pk__in=documents_past_absolute_retention(now).values("pk"))
    )


def oldest_overdue_document_age(now: dt.datetime | None = None) -> dt.timedelta | None:
    """How long the longest-overdue document has been waiting.

    **The alerting signal**, per docs/OPERATIONS.md: the age of the oldest
    undeleted document past its deadline, never the volume and never job
    success. A count tells you the queue is big, which it may legitimately be.
    This tells you whether something has been abandoned — and one document
    abandoned for six months is a worse breach than a thousand deleted on time.
    """
    now = now or timezone.now()
    oldest = (
        documents_due_for_deletion(now)
        .order_by("uploaded_at")
        .values_list("uploaded_at", flat=True)
        .first()
    )
    return None if oldest is None else now - oldest


# ---------------------------------------------------------------------------
# Deleting, and proving it
# ---------------------------------------------------------------------------


def _delete_and_verify(key: str) -> None:
    """Delete an object and re-read to prove it is gone.

    ``storage.delete()`` returning without raising is **not** evidence. S3 and
    S3-compatible stores answer a delete of an unremovable key with a 204 in
    several situations — a bucket policy denying DeleteObject, an
    eventually-consistent replica, a versioned bucket where deletion writes a
    marker but leaves the version readable.
    """
    storage = _storage()
    storage.delete(key)

    if storage.exists(key):
        raise DeletionUnconfirmedError(
            f"{key} is still readable after delete() returned successfully"
        )


@transaction.atomic
def _auto_reject_on_expiry(document: VerificationDocument, now: dt.datetime) -> None:
    """Close any still-open request naming expiry as the reason.

    Without this the student waits on a queue that will never reach them, and
    the platform holds a pending request with no document behind it. Naming
    expiry — rather than a generic rejection — is the difference between "try
    again" and "you were refused".
    """
    from universities.constants import VerificationStatus

    reason = str(
        _("We did not review this in time and the document has been deleted. Please resubmit.")
    )

    for request in VerificationRequest.all_objects.filter(
        document=document, status=VerificationRequestStatus.PENDING
    ).select_related("profile"):
        request.status = VerificationRequestStatus.REJECTED
        request.reviewed_at = now
        # No reviewed_by. Nobody decided this; a clock did.
        request.reviewed_by = None
        request.decision_reason = reason
        request.save(
            update_fields=[
                "status",
                "reviewed_at",
                "reviewed_by",
                "decision_reason",
                "updated_at",
            ]
        )

        profile = request.profile
        profile.verification_status = VerificationStatus.REJECTED
        profile.rejection_reason = reason
        profile.save(update_fields=["verification_status", "rejection_reason", "updated_at"])


def delete_verification_document(document_id: int, now: dt.datetime | None = None) -> bool:
    """Delete one document's image, verify it is gone, then record that.

    Returns whether the deletion was confirmed. **The decision outcome is
    retained; the image is not.** After this the request row still carries its
    status, reason, timestamps and reviewer — only the bytes are gone and the
    storage key is cleared.
    """
    now = now or timezone.now()
    document = VerificationDocument.objects.filter(pk=document_id).first()

    if document is None:
        logger.info("retention_skipped", document_id=document_id, reason="deleted_row")
        return True

    if document.deleted_at is not None:
        # Already done. Jobs must be idempotent.
        return True

    key = document.storage_key

    try:
        _delete_and_verify(key)
    except Exception as exc:
        # NEVER mark success. An unconfirmed delete is retried and alerted on;
        # writing deleted_at here would be a compliance record asserting
        # something false, which is worse than having no record.
        VerificationDocument.objects.filter(pk=document_id).update(
            delete_attempts=document.delete_attempts + 1,
            last_delete_error=str(exc)[:255],
        )
        logger.error(
            "retention_delete_unconfirmed",
            document_id=document_id,
            attempts=document.delete_attempts + 1,
            error=str(exc),
        )
        return False

    with transaction.atomic():
        _auto_reject_on_expiry(document, now)
        VerificationDocument.objects.filter(pk=document_id).update(
            storage_key="", deleted_at=now, last_delete_error=""
        )

    logger.info("retention_deleted", document_id=document_id)
    return True


def sweep_expired_documents(limit: int = 500, now: dt.datetime | None = None) -> int:
    """Enqueue deletion for every document past either deadline.

    Scheduled daily. Oldest first — `uploaded_at` is NOT NULL, so there is no
    null-ordering trap here (docs/OPERATIONS.md).
    """
    now = now or timezone.now()
    queryset = documents_due_for_deletion(now)

    waiting = oldest_overdue_document_age(now)
    document_ids = list(queryset.order_by("uploaded_at").values_list("pk", flat=True)[:limit])

    for document_id in document_ids:
        django_rq.get_queue("default").enqueue(delete_verification_document, document_id)

    logger.info(
        "retention_sweep",
        enqueued=len(document_ids),
        oldest_overdue_seconds=None if waiting is None else int(waiting.total_seconds()),
    )
    return len(document_ids)


class OrphanScanUnavailableError(RuntimeError):
    """The bucket could not be listed, so the scan has no answer.

    Distinct from "no orphans". A reconciler that cannot see the thing it
    reconciles against must say so rather than return the reassuring value.
    """


def orphaned_document_objects(
    prefix: str = "verification", *, now: dt.datetime | None = None
) -> list[str]:
    """Objects in the document bucket that **no row points at**.

    **Only objects older than `DOCUMENT_ORPHAN_GRACE_SECONDS`.** An upload
    stores its bytes inside the transaction that creates the row, so between
    the store and the commit there is a window in which the object exists and
    the row is not yet visible to another connection. Without a grace period
    this scan would find that object, call it an orphan, and a sweep acting on
    the finding would delete the bytes out from under a request that is about
    to succeed -- turning a reconciler into the thing it exists to catch.

    Sixty seconds. The window it has to cover is one `storages.save()` plus
    the remainder of a request transaction: sub-second on a healthy path, and
    the number is two orders of magnitude above that because the cost of being
    generous is that a genuinely orphaned file survives one extra sweep, while
    the cost of being tight is deleting a student's identity document mid-
    upload. It is a settings value rather than a literal so the two places
    that care -- this scan and the alert threshold -- read the same number.

    Every sweep in this module enumerates rows, so an object whose row never
    existed -- or no longer does -- is invisible to all of them for ever.

    `submit_verification_document` now writes the bytes **inside** the
    transaction that creates the row, which narrows the window rather than
    closing it: a store that succeeds and a commit that does not -- the
    process killed, the connection lost, a deferred constraint firing at
    COMMIT -- still leaves exactly this. A national ID document nothing will
    ever delete. So this scan is load-bearing, not residual, and it is
    scheduled (`config.jobs.schedule`) rather than available.

    `docs/OPERATIONS.md` states the general rule -- a reconciler must count
    what should exist and does not -- and this is its other direction. There
    the database was the authority and the cache was missing; here the bucket
    is the authority and the row is missing. Both are only visible from the
    side that is not being walked.

    Returns keys rather than a count, because the operator's next question is
    "which ones", and a compliance answer of "seventeen" is not one.

    Raises `OrphanScanUnavailableError` when the bucket cannot be listed. It used
    to log a warning and return `[]`, which is the same shape the docstring
    above complains about: an empty list from a scan that could not look is
    indistinguishable from an empty list from a scan that looked and found
    nothing, and the caller printing "0 orphans" is not lying on purpose.
    """
    storage = _storage()

    try:
        _directories, files = storage.listdir(prefix)
    except Exception as error:
        logger.warning("orphan_scan_failed", error=str(error))
        raise OrphanScanUnavailableError(
            f"The document bucket could not be listed ({type(error).__name__}: {error}). "
            f"No conclusion about orphans is available from this run."
        ) from error

    known = set(
        VerificationDocument.objects.exclude(storage_key="").values_list("storage_key", flat=True)
    )

    cutoff = (now or timezone.now()) - dt.timedelta(seconds=settings.DOCUMENT_ORPHAN_GRACE_SECONDS)
    storage = _storage()

    orphans = []
    for name in files:
        key = f"{prefix}/{name}"
        if key in known:
            continue

        try:
            written = storage.get_created_time(key)
        except Exception:
            # No timestamp means the grace period cannot be applied, and a
            # scan that cannot tell a new object from an old one must not
            # report either. Skipped and logged rather than guessed.
            logger.warning("orphan_scan_no_timestamp", key=key)
            continue

        if written <= cutoff:
            orphans.append(key)

    return sorted(orphans)


def unconfirmed_deletions():
    """Documents a delete has been attempted on and could not be confirmed.

    Alerted on separately from the sweep: a growing sweep backlog means the
    worker is behind, but a row here means the delete **ran and did not work**,
    which is a bucket-permissions problem no amount of retrying fixes.
    """
    return VerificationDocument.objects.filter(
        deleted_at__isnull=True, delete_attempts__gt=0
    ).order_by("uploaded_at")


# ---------------------------------------------------------------------------
# Erasure execution (ADR-008)
# ---------------------------------------------------------------------------


def erasures_due(now: dt.datetime | None = None):
    """Requests whose cooling-off window has closed.

    Filtered on `executes_after__lte`, which excludes the nulls that a blocked
    or cancelled request carries -- so the ordering is not what makes this
    safe (docs/OPERATIONS.md).
    """
    from .privacy_api import ErasureRequest

    return ErasureRequest.objects.filter(
        status=ErasureRequest.Status.COOLING_OFF,
        executes_after__lte=now or timezone.now(),
    )


def execute_erasure(erasure_id: int, now: dt.datetime | None = None) -> bool:
    """Carry out one erasure, irreversibly.

    **No approval step, deliberately.** An approval gate would give the
    platform discretion to refuse a data-subject erasure request, which is a
    worse problem than the one it solves: the cooling-off window protects the
    subject from coercion, whereas an approver protects nobody and creates a
    party who can say no. ADR-008 records this so it is not added later in the
    belief that it is a safeguard.
    """
    from .privacy import (
        AlreadyErasedError,
        erase_landlord_data,
        erase_personal_data,
        landlord_erasure_blockers,
    )
    from .privacy_api import ErasureRequest

    now = now or timezone.now()
    erasure = ErasureRequest.objects.filter(pk=erasure_id).select_related("user").first()

    if erasure is None:
        logger.info("erasure_skipped", erasure_id=erasure_id, reason="deleted")
        return True

    if erasure.status != ErasureRequest.Status.COOLING_OFF:
        # Cancelled inside the window, which is the outcome the window exists
        # to make possible.
        logger.info(
            "erasure_skipped",
            erasure_id=erasure_id,
            reason="not_cooling_off",
            status=erasure.status,
        )
        return True

    user = erasure.user
    is_landlord = getattr(user, "landlord_profile", None) is not None

    # Re-checked at execution, not only at request time. A landlord with no
    # running tenancies a week ago may have one now, and erasing them
    # mid-tenancy would leave those students with nobody to call.
    blockers = landlord_erasure_blockers(user) if is_landlord else []
    if blockers:
        erasure.status = ErasureRequest.Status.BLOCKED
        erasure.blockers = blockers
        erasure.executes_after = None
        erasure.save(update_fields=["status", "blockers", "executes_after"])
        logger.warning("erasure_blocked_at_execution", erasure_id=erasure_id)
        return False

    try:
        if is_landlord:
            erase_landlord_data(user)
        else:
            erase_personal_data(user)
    except AlreadyErasedError:
        # Two requests for one account. Not an error: the outcome the subject
        # asked for is already true.
        logger.info("erasure_already_done", erasure_id=erasure_id)

    erasure.status = ErasureRequest.Status.COMPLETED
    erasure.completed_at = now
    erasure.executes_after = None
    erasure.save(update_fields=["status", "completed_at", "executes_after"])

    logger.info("erasure_executed", erasure_id=erasure_id)
    return True


def sweep_due_erasures(limit: int = 200, now: dt.datetime | None = None) -> int:
    """Execute every erasure whose window has closed.

    Scheduled hourly. A request that enters cooling-off and never executes is
    a compliance breach that looks like nothing at all -- the subject was told
    a date, the date passed, and the record still says `cooling_off`.
    """

    now = now or timezone.now()
    queryset = erasures_due(now)

    waiting = oldest_overdue_age(queryset, "executes_after")
    erasure_ids = list(queryset.order_by("executes_after").values_list("pk", flat=True)[:limit])

    for erasure_id in erasure_ids:
        django_rq.get_queue("default").enqueue(execute_erasure, erasure_id)

    logger.info(
        "erasure_sweep",
        enqueued=len(erasure_ids),
        oldest_overdue_seconds=None if waiting is None else int(waiting.total_seconds()),
    )
    return len(erasure_ids)


def reconcile_document_objects(*, now: dt.datetime | None = None) -> int:
    """Scheduled counterpart to :func:`orphaned_document_objects`.

    **Why this is a job and not a report.** `submit_verification_document`
    stores the bytes inside the transaction that creates the row, which makes
    an orphan rare -- store succeeds, commit does not -- rather than
    impossible. Rare is not zero, the residue is a national ID document in a
    private bucket that no row-walking sweep can see, and until now the only
    thing that looked for it was a development-only observation command and a
    seed cross-check. A defence that runs when somebody remembers to run it is
    not a defence.

    It reports and alerts; it does not delete. An automatic delete here acts
    on the one class of object whose row is missing, which is precisely the
    situation where the scan's own correctness is least verifiable -- and the
    cost of being wrong is destroying a student's identity document. The
    operator gets the keys and decides.

    Absence is a separate number with a separate alert (docs/OPERATIONS.md):
    an unlistable bucket logs `orphan_scan_unavailable` and re-raises, so the
    job fails visibly instead of recording a comfortable zero.
    """
    try:
        orphans = orphaned_document_objects(now=now)
    except OrphanScanUnavailableError:
        logger.error("orphan_scan_unavailable")
        raise

    logger.info("orphan_reconcile", orphans=len(orphans))

    if orphans:
        # Loud, with the keys. "Seventeen" is not a compliance answer; the
        # operator's next question is which ones, and the log is where they
        # will look at 2am.
        logger.error("orphaned_documents_found", count=len(orphans), keys=orphans[:20])

    return len(orphans)

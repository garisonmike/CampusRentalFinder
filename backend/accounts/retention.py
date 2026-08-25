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


def unconfirmed_deletions():
    """Documents a delete has been attempted on and could not be confirmed.

    Alerted on separately from the sweep: a growing sweep backlog means the
    worker is behind, but a row here means the delete **ran and did not work**,
    which is a bucket-permissions problem no amount of retrying fixes.
    """
    return VerificationDocument.objects.filter(
        deleted_at__isnull=True, delete_attempts__gt=0
    ).order_by("uploaded_at")

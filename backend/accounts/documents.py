"""
Manual ID verification, and the access log over it (ADR-003, ADR-007).

For schools that issue no student addresses. A student uploads a national ID or
student card, a member of *their own* university's staff looks at it, and a
decision is recorded.

This module holds the most sensitive data in the product, and Kenya's Data
Protection Act 2019 governs all of it. Every choice below takes the option that
holds less data for less time:

- The image goes to the **private** bucket under a **random** key. A key
  derived from a user id is an enumeration oracle even behind auth.
- Content type is sniffed from the **leading bytes**. A declared header and a
  file extension are both attacker-controlled.
- **EXIF is stripped on ingest.** A photo of a student ID carries the GPS
  coordinates of wherever it was taken, which is usually where that student
  lives. We have no business holding that.
- Reviewers get a **short-lived signed URL generated per request**, never a
  stored one. A stored URL is a permanent capability sitting in a database.
- **Every read writes a `DocumentAccessLog` row.** The log is append-only and
  outlives the document, because "who looked at this" is the question an audit
  actually asks and the image will be long gone by then.
"""

from __future__ import annotations

import datetime as dt
import io
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from config.tenancy import TenantScopedModel
from universities.constants import VerificationMethod, VerificationStatus
from universities.services import assert_verification_method_is_enabled

from .models import StudentProfile, User


class DocumentTypeNotAllowedError(ValidationError):
    """The uploaded bytes are not an image or a PDF."""


class DocumentTooLargeError(ValidationError):
    """The upload exceeds the configured cap."""


class ResubmissionLimitError(ValidationError):
    """This student has resubmitted too many times."""


class DocumentUnavailableError(ValidationError):
    """The document has been deleted under the retention policy."""


# ---------------------------------------------------------------------------
# Content sniffing
# ---------------------------------------------------------------------------

#: Leading-byte signatures, mapped to the content type we will store.
#:
#: Sniffed rather than trusted. `Content-Type` is set by the client and the
#: extension is part of a filename the client chose; neither is evidence. A
#: `.jpg` that is really an HTML document is a stored XSS waiting for someone
#: to open it from the reviewer queue.
MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)

#: WebP needs a two-part check: "RIFF" then "WEBP" four bytes later.
WEBP_PREFIX = b"RIFF"
WEBP_FORMAT = b"WEBP"

#: Types we will re-encode to strip metadata. PDFs carry no EXIF and are stored
#: as uploaded.
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def sniff_content_type(head: bytes) -> str | None:
    """The real content type, from the leading bytes alone.

    Returns ``None`` for anything unrecognised, which the caller treats as a
    refusal. An allowlist, never a blocklist: a blocklist is a list of the
    attacks somebody already thought of.
    """
    for signature, content_type in MAGIC_SIGNATURES:
        if head.startswith(signature):
            return content_type

    if head[:4] == WEBP_PREFIX and head[8:12] == WEBP_FORMAT:
        return "image/webp"

    return None


def strip_image_metadata(data: bytes, content_type: str) -> bytes:
    """Re-encode an image without its metadata.

    A phone photo of a student ID carries GPS coordinates, a device serial and
    a timestamp. The coordinates are usually the student's home. None of it is
    needed to decide whether the card is real, so none of it is kept.

    **Re-encoded with an empty EXIF segment**, which drops EXIF, GPS and the
    rest without touching pixels in Python.

    The earlier implementation copied the image through
    `putdata(list(image.getdata()))`, which materialises one Python tuple per
    pixel. Measured on a 4 MB, 4032x3024 phone photo -- the ordinary case, and
    one no test had ever supplied -- that took **15.7 seconds and peaked at
    838 MB**, synchronously, inside the upload request. The document cap is
    5 MB, so a handful of concurrent students uploading ordinary phone photos
    of their ID cards would have exhausted the worker, and anyone who noticed
    could have done it on purpose.

    The same file through this version: 0.28 seconds, 5 MB peak, identical
    result -- no GPS, no make, no model.

    PDFs are returned unchanged; they carry no EXIF, and rewriting one risks
    destroying the document.
    """
    if content_type not in IMAGE_TYPES:
        return data

    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        buffer = io.BytesIO()
        # `exif=b""` writes an empty segment rather than copying `info["exif"]`
        # across, which is what Pillow does by default for JPEG. `quality`
        # is not specified for the non-JPEG formats, which do not take it.
        options: dict = {"exif": b""}
        if image.format == "JPEG":
            # Keep the original quantisation tables: re-encoding an already
            # lossy image at some other quality degrades it for no reason.
            options["quality"] = "keep"

        image.save(buffer, format=image.format, **options)
        return buffer.getvalue()


def random_document_key(extension: str) -> str:
    """An unguessable object key.

    **Never derived from the user id, the profile id or the filename.** A
    predictable key turns any storage misconfiguration into an enumeration of
    every student's ID document, and misconfigured buckets are the single most
    common way this data leaks.
    """
    from config.storage import assert_storage_key_is_safe

    return assert_storage_key_is_safe(f"verification/{secrets.token_urlsafe(32)}{extension}")


EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class VerificationDocument(models.Model):
    """One uploaded identity document, and later its tombstone.

    **The row outlives the image.** When retention deletes the object, the
    storage key is cleared and ``deleted_at`` is set, but the row stays: the
    access log points at it, and "who looked at this document" is the question
    an audit asks long after the image itself is gone.

    Not tenant-scoped directly — it is reached through
    ``VerificationRequest``, which is. Registered as exempt with that reason.
    """

    #: Empty once deleted. This is the only pointer to the actual bytes.
    storage_key = models.CharField(_("storage key"), max_length=255, blank=True)
    content_type = models.CharField(_("content type"), max_length=64)
    byte_size = models.PositiveIntegerField(_("size in bytes"))

    uploaded_at = models.DateTimeField(auto_now_add=True)
    #: Set only after a re-read confirms the object is gone. Object storage
    #: deletes fail quietly, so this must never be optimistic.
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)
    delete_attempts = models.PositiveSmallIntegerField(_("delete attempts"), default=0)
    last_delete_error = models.CharField(_("last delete error"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("Verification document")
        verbose_name_plural = _("Verification documents")
        ordering = ["-uploaded_at"]
        indexes = [
            # The retention sweeps read the oldest undeleted row.
            models.Index(fields=["deleted_at", "uploaded_at"], name="verifdoc_retention_idx"),
        ]
        constraints = [
            # A deleted document holds no key, and a live one holds one. The
            # halfway state -- marked deleted but the object still there -- is
            # exactly the compliance failure this table exists to prevent.
            models.CheckConstraint(
                condition=(Q(deleted_at__isnull=True) & ~Q(storage_key=""))
                | (Q(deleted_at__isnull=False) & Q(storage_key="")),
                name="verifdoc_deleted_has_no_key",
            ),
            models.CheckConstraint(condition=Q(byte_size__gt=0), name="verifdoc_size_positive"),
        ]

    def __str__(self) -> str:
        return f"document {self.pk} ({'deleted' if self.deleted_at else self.content_type})"

    def is_available(self) -> bool:
        """A method, not a property. See tools/check_field_shadowing.py."""
        return self.deleted_at is None and bool(self.storage_key)


class VerificationRequestStatus(models.TextChoices):
    PENDING = "pending", _("Awaiting review")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")


#: Statuses in which a request is still waiting on a human.
OPEN_REQUEST_STATUSES = (VerificationRequestStatus.PENDING,)


class VerificationRequest(TenantScopedModel):
    """A student asking to be verified by document review.

    Tenant-scoped through the profile. **A reviewer sees only their own
    university's queue**, and that is the isolation failure with the worst
    consequences in the product: the data on the other side of it is national
    ID numbers belonging to people who never agreed to show them to another
    institution.
    """

    tenant_lookup = "profile__university"

    profile = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="verification_requests"
    )
    #: PROTECT: the audit trail points at this row and must not lose its target.
    #: The IMAGE is deleted by retention; the row is a tombstone.
    document = models.ForeignKey(
        VerificationDocument,
        on_delete=models.PROTECT,
        related_name="requests",
    )

    status = models.CharField(
        _("status"),
        max_length=16,
        choices=VerificationRequestStatus.choices,
        default=VerificationRequestStatus.PENDING,
    )

    #: Shown to the student. The REVIEWER'S IDENTITY IS NOT: a named individual
    #: refusing a student's ID at their own institution is a person who can be
    #: found in a corridor.
    decision_reason = models.CharField(_("decision reason"), max_length=255, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verification_reviews",
        help_text=_("Internal. Never serialised to the student."),
    )
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)

    #: Which attempt this is, for the resubmission cap.
    attempt = models.PositiveSmallIntegerField(_("attempt"), default=1)

    #: The opaque handle this case is known by in the access log after erasure.
    #: Random, generated once, never derived from anything about the student.
    subject_token = models.CharField(
        _("subject token"), max_length=64, default=secrets.token_hex, editable=False
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Verification request")
        verbose_name_plural = _("Verification requests")
        ordering = ["created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            # The reviewer queue: their university's pending requests, oldest
            # first. Scoping happens through profile__university.
            models.Index(fields=["status", "created_at"], name="verifreq_queue_idx"),
            models.Index(fields=["profile", "-created_at"], name="verifreq_profile_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["profile"],
                condition=Q(status__in=OPEN_REQUEST_STATUSES),
                name="verifreq_one_open_per_profile",
            ),
            # A decision names a time and a reason. A rejection with no reason
            # is a student told "no" with nothing to act on.
            models.CheckConstraint(
                condition=Q(status=VerificationRequestStatus.PENDING)
                | Q(reviewed_at__isnull=False),
                name="verifreq_decision_has_a_time",
            ),
            models.CheckConstraint(
                condition=~Q(status=VerificationRequestStatus.REJECTED) | ~Q(decision_reason=""),
                name="verifreq_rejection_has_a_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"verification request {self.pk} ({self.status})"

    def is_open(self) -> bool:
        return self.status in OPEN_REQUEST_STATUSES


class AccessPurpose(models.TextChoices):
    """Why a document was read. Enumerated, so the log can be queried."""

    REVIEW = "review", _("Reviewing a verification request")
    AUDIT = "audit", _("Responding to an audit or a subject access request")
    SUPPORT = "support", _("Investigating a support ticket")


class DocumentAccessLog(models.Model):
    """One row per read of one document. **Append only.**

    No update path and no delete path — not by convention, by construction:
    :meth:`save` refuses to write a second time and :meth:`delete` refuses
    always. A log a reviewer can edit is not a log, and the entire value of
    this table is that it is evidence against the people who can write to it.

    It outlives the document deliberately. Retention removes the image after
    30 days; the question "who looked at this student's ID" is asked months or
    years later, and by then the only honest answer lives here.
    """

    #: NULLED at erasure. The row survives; the link to a person does not.
    document = models.ForeignKey(
        VerificationDocument,
        on_delete=models.PROTECT,
        related_name="access_log",
        null=True,
        blank=True,
    )
    #: Also nulled at erasure, for the same reason. Kept as a separate FK from
    #: `document` because a document may back more than one request, and the
    #: audit question is usually "case X", not "file Y".
    verification_request = models.ForeignKey(
        "accounts.VerificationRequest",
        on_delete=models.SET_NULL,
        related_name="access_log",
        null=True,
        blank=True,
    )

    #: An opaque, RANDOM handle for the case this access belonged to.
    #:
    #: Generated at creation and stored. **Not derived from any identifier** --
    #: a hash of a user id is reversible by enumerating the users, which is not
    #: pseudonymisation, it is obfuscation with extra steps.
    #:
    #: It is what survives erasure. Rows sharing a token were accesses to the
    #: same case, so the trail still answers "who opened this, when, and why"
    #: after every link to a person is gone. It cannot answer "which person was
    #: that", and ADR-008 records that as deliberate and irreversible.
    #: Defaulted so a row can never exist without one; the caller always
    #: passes the case's token explicitly, and the constraint below refuses
    #: a blank.
    subject_token = models.CharField(
        _("subject token"), max_length=64, default=secrets.token_hex, db_index=True
    )
    #: SET_NULL rather than PROTECT: a reviewer who leaves must be deletable,
    #: and the row survives to say a read happened even if it can no longer
    #: say by whom. `reviewer_label` keeps the human-readable trace.
    reviewer = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="document_accesses"
    )
    #: Copied at write time, so the trail survives the account being deleted.
    reviewer_label = models.CharField(_("reviewer"), max_length=255)

    purpose = models.CharField(_("purpose"), max_length=16, choices=AccessPurpose.choices)
    #: Ties this read to a line in the application log.
    request_id = models.CharField(_("request id"), max_length=64, blank=True)

    accessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Document access log")
        verbose_name_plural = _("Document access log")
        ordering = ["-accessed_at"]
        indexes = [
            models.Index(fields=["document", "-accessed_at"], name="doclog_document_idx"),
            models.Index(fields=["reviewer", "-accessed_at"], name="doclog_reviewer_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=~Q(reviewer_label=""), name="doclog_names_a_reader"),
            # The token is what remains after erasure. A row without one is an
            # access nobody can group, which is the trail failing quietly.
            models.CheckConstraint(condition=~Q(subject_token=""), name="doclog_has_a_token"),
        ]

    def __str__(self) -> str:
        return f"{self.reviewer_label} read document {self.document_id}"

    def save(self, *args, **kwargs):
        """Append only. A second save is refused."""
        if self.pk is not None:
            raise ValidationError(
                "DocumentAccessLog is append-only. An access record cannot be "
                "edited: the table's whole value is that it is evidence "
                "against the people who can write to it."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Never. Retention removes the document, never the record of access."""
        raise ValidationError(
            "DocumentAccessLog is append-only. Retention deletes the document; "
            "the record that someone looked at it is what survives."
        )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def _private_storage():
    return storages["documents"]


def _assert_within_resubmission_limit(profile: StudentProfile) -> int:
    """Cap resubmissions, and return which attempt this is.

    Rejection is deliberately **not terminal** — a genuine student whose photo
    was blurry must be able to try again. But an uncapped retry loop is an
    uncapped upload channel for identity documents, which is the opposite of
    holding less data.
    """
    used = VerificationRequest.all_objects.filter(profile=profile).count()

    if used >= settings.VERIFICATION_MAX_SUBMISSIONS:
        raise ResubmissionLimitError(
            {
                "detail": _(
                    "You have used all %(limit)d verification attempts. "
                    "Contact your university directly."
                )
                % {"limit": settings.VERIFICATION_MAX_SUBMISSIONS}
            }
        )

    return used + 1


def submit_verification_document(profile: StudentProfile, data: bytes) -> VerificationRequest:
    """Accept an identity document and open a review request.

    The single write path. Sniffs, caps, strips and stores in that order —
    nothing touches the bucket until the bytes have been proven to be an image
    or a PDF of an acceptable size.
    """
    from django.db import transaction

    if len(data) > settings.VERIFICATION_MAX_DOCUMENT_BYTES:
        raise DocumentTooLargeError(
            {
                "document": _("Documents must be under %(mb).1f MB.")
                % {"mb": settings.VERIFICATION_MAX_DOCUMENT_BYTES / 1_048_576}
            }
        )
    if not data:
        raise DocumentTypeNotAllowedError({"document": _("The file is empty.")})

    content_type = sniff_content_type(data[:16])
    if content_type is None:
        raise DocumentTypeNotAllowedError(
            {
                "document": _(
                    "Only JPEG, PNG, WebP images and PDF files are accepted. "
                    "The file's contents did not match any of those."
                )
            }
        )

    assert_verification_method_is_enabled(profile.university, VerificationMethod.STUDENT_ID_UPLOAD)
    attempt = _assert_within_resubmission_limit(profile)

    try:
        clean = strip_image_metadata(data, content_type)
    except OSError as error:
        # A valid header and a missing body -- what a dropped upload leaves.
        # The sniff cannot catch it, because the header is genuinely a PNG's.
        #
        # The photo path grew this guard first (efdae0f) and this one did not,
        # so an interrupted upload here surfaced as an unhandled OSError: a
        # 500 where a sentence belongs, on the endpoint a student uses to
        # prove who they are.
        raise DocumentTypeNotAllowedError(
            {
                "document": _(
                    "That file could not be read -- it looks incomplete. If "
                    "the upload was interrupted, try it again."
                )
            }
        ) from error
    key = random_document_key(EXTENSIONS[content_type])

    # **Row first, bytes second, both inside one transaction.** The class of
    # failure is NARROWED, not closed, and the difference matters enough to
    # say plainly here rather than let the previous wording stand.
    #
    # The old order was store-then-open-the-transaction, so any failure in the
    # transaction body -- a constraint, a lost connection, a rollback higher up
    # -- stranded a national ID document in the private bucket with nothing
    # pointing at it. That window was the whole transaction. It is now the gap
    # between the PUT returning and COMMIT returning: the process killed, the
    # connection lost, a deferred constraint firing at commit. Small. Not zero.
    #
    # And note which residue this ordering actually produces. The argument
    # below is that a *dangling row* is the better failure -- visible,
    # self-announcing, harmless, since the retention sweep deletes a key that
    # does not exist, the store answers successfully, the re-read confirms it
    # is gone, and the row closes out correctly, which is the direction a
    # tombstone table is designed to fail in anyway. But this ordering
    # produces the other one: a *leaked file* nothing can see.
    #
    # The shape that produces the preferred residue is row-first, COMMIT, then
    # write in `transaction.on_commit` and mark the document ready. That was
    # not considered when this was written -- the previous ordering was fixed
    # by moving the store inside the transaction, which was the first thing
    # that worked, and the justification was written afterwards about the
    # residue it was hoped to leave rather than the one it leaves. It is
    # written up in docs/OPERATIONS.md rather than changed here, because it
    # adds a not-yet-ready state that every reader of a document row has to
    # handle, and that is a design decision, not a cleanup.
    #
    # Because the class is narrowed rather than closed, the bucket-side scan
    # is **load-bearing**: `accounts.retention.reconcile_document_objects`
    # runs on a schedule and alerts, rather than existing as a function an
    # operator could call.
    with transaction.atomic():
        document = VerificationDocument.objects.create(
            storage_key=key, content_type=content_type, byte_size=len(clean)
        )
        request = VerificationRequest.all_objects.create(
            profile=profile, document=document, attempt=attempt
        )
        profile.verification_status = VerificationStatus.PENDING
        profile.save(update_fields=["verification_status", "updated_at"])

        # Inside the transaction, so a store failure rolls the rows back and
        # the student sees an error rather than a request pointing at nothing.
        _private_storage().save(key, ContentFile(clean))

    return request


def signed_document_url(
    document: VerificationDocument,
    *,
    reviewer: User,
    purpose: str = AccessPurpose.REVIEW,
    request_id: str = "",
) -> str:
    """A short-lived URL for one reviewer to open one document, **once logged**.

    The log row is written *before* the URL is returned, not after. If logging
    fails there is no URL, which is the correct direction: an unlogged read is
    worse than a blocked one.

    Never stored. A stored URL is a permanent capability sitting in a database
    row, and it would outlive both the review and the reviewer's employment.
    """
    if not document.is_available():
        raise DocumentUnavailableError(
            {
                "document": _(
                    "This document was deleted under the retention policy. The "
                    "decision on the request is still recorded."
                )
            }
        )

    # The token identifies the CASE, so it comes from the request rather than
    # being minted here -- otherwise every access to one document would carry a
    # different handle and the post-erasure trail could not be grouped at all.
    verification_request = document.requests.order_by("created_at").first()

    DocumentAccessLog.objects.create(
        document=document,
        verification_request=verification_request,
        subject_token=(
            verification_request.subject_token if verification_request else secrets.token_hex(32)
        ),
        reviewer=reviewer,
        reviewer_label=reviewer.get_full_name() or reviewer.email,
        purpose=purpose,
        request_id=request_id,
    )

    return _private_storage().url(document.storage_key)


def _decide(
    request: VerificationRequest,
    *,
    status: str,
    reviewer: User,
    reason: str,
    now: dt.datetime | None = None,
) -> VerificationRequest:
    from django.db import transaction

    now = now or timezone.now()

    with transaction.atomic():
        request.status = status
        request.reviewed_by = reviewer
        request.reviewed_at = now
        request.decision_reason = reason
        request.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "decision_reason", "updated_at"]
        )

        profile = request.profile
        if status == VerificationRequestStatus.APPROVED:
            from universities.constants import VerificationMethod

            profile.verification_status = VerificationStatus.VERIFIED
            profile.verification_method = VerificationMethod.STUDENT_ID_UPLOAD
            profile.verified_at = now
            profile.verified_by = reviewer
            profile.rejection_reason = ""
        else:
            profile.verification_status = VerificationStatus.REJECTED
            profile.rejection_reason = reason

        profile.save(
            update_fields=[
                "verification_status",
                "verification_method",
                "verified_at",
                "verified_by",
                "rejection_reason",
                "updated_at",
            ]
        )

    return request


def approve_verification(
    request: VerificationRequest, *, reviewer: User, reason: str = "", now=None
) -> VerificationRequest:
    """Approve. Records the reviewer internally; the student is not told who."""
    return _decide(
        request,
        status=VerificationRequestStatus.APPROVED,
        reviewer=reviewer,
        reason=reason,
        now=now,
    )


def reject_verification(
    request: VerificationRequest, *, reviewer: User, reason: str, now=None
) -> VerificationRequest:
    """Reject, with a reason the student sees.

    Not terminal: the student may resubmit up to
    ``settings.VERIFICATION_MAX_SUBMISSIONS``. A blurry photo is the common
    case and a dead end for it would be an accessibility failure dressed as a
    security control.
    """
    if not reason:
        raise ValidationError(
            {"reason": _("A rejection must give the student something to act on.")}
        )
    return _decide(
        request,
        status=VerificationRequestStatus.REJECTED,
        reviewer=reviewer,
        reason=reason,
        now=now,
    )

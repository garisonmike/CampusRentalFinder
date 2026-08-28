"""
Manual ID verification and the access log (ADR-003, Data Protection Act 2019).

The most sensitive data in the product. Every test here is a compliance
obligation rather than a feature preference, and where a choice existed the one
that holds less data for less time was taken.

The two that matter most:
:meth:`TestReviewerQueueIsolation.test_a_reviewer_cannot_see_another_universitys_queue`
and :class:`TestAccessLogIsAppendOnly`.
"""

from __future__ import annotations

import io

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from accounts.documents import (
    AccessPurpose,
    DocumentAccessLog,
    DocumentTooLargeError,
    DocumentTypeNotAllowedError,
    DocumentUnavailableError,
    ResubmissionLimitError,
    VerificationDocument,
    VerificationRequest,
    VerificationRequestStatus,
    approve_verification,
    random_document_key,
    reject_verification,
    signed_document_url,
    sniff_content_type,
    strip_image_metadata,
    submit_verification_document,
)
from config.tenancy import TenantScopeError
from universities.constants import VerificationMethod, VerificationStatus

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def a_jpeg(*, with_exif: bool = False, size: tuple[int, int] = (24, 24)) -> bytes:
    """A real JPEG, optionally carrying GPS EXIF."""
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational

    image = Image.new("RGB", size, (120, 30, 30))
    buffer = io.BytesIO()

    if with_exif:
        exif = Image.Exif()
        # Nairobi, roughly. The point of the fixture is that this is a real
        # location a real phone would have written.
        gps = exif.get_ifd(0x8825)
        gps[1] = "S"
        gps[2] = (IFDRational(1, 1), IFDRational(17, 1), IFDRational(0, 1))
        gps[3] = "E"
        gps[4] = (IFDRational(36, 1), IFDRational(49, 1), IFDRational(0, 1))
        exif[0x010F] = "TestPhone"  # Make
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")

    return buffer.getvalue()


def a_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 90, 10)).save(buffer, format="PNG")
    return buffer.getvalue()


A_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


@pytest.fixture
def submitted(student_profile):
    return submit_verification_document(student_profile, a_jpeg())


# ---------------------------------------------------------------------------
# Content sniffing
# ---------------------------------------------------------------------------


class TestContentSniffing:
    """The declared header and the extension are both attacker-controlled.

    A `.jpg` that is really an HTML document is stored XSS waiting for someone
    to open it from the reviewer queue.
    """

    def test_a_jpeg_is_recognised(self):
        assert sniff_content_type(a_jpeg()[:16]) == "image/jpeg"

    def test_a_png_is_recognised(self):
        assert sniff_content_type(a_png()[:16]) == "image/png"

    def test_a_pdf_is_recognised(self):
        assert sniff_content_type(A_PDF[:16]) == "application/pdf"

    def test_a_webp_needs_both_halves_of_its_signature(self):
        assert sniff_content_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
        assert sniff_content_type(b"RIFF\x00\x00\x00\x00AVI LIST") is None

    def test_html_is_refused(self):
        assert sniff_content_type(b"<!DOCTYPE html><html>") is None

    def test_a_script_is_refused(self):
        assert sniff_content_type(b"#!/bin/sh\nrm -rf /") is None

    def test_an_svg_is_refused(self):
        """SVG is an image by name and a script host in fact."""
        assert sniff_content_type(b"<svg xmlns='http://www.w3.org/2000/svg'>") is None

    def test_html_named_as_a_jpeg_is_refused_on_upload(self, student_profile):
        """The declared type never enters the decision."""
        with pytest.raises(DocumentTypeNotAllowedError):
            submit_verification_document(student_profile, b"<!DOCTYPE html><html>evil")

    def test_an_empty_file_is_refused(self, student_profile):
        with pytest.raises(DocumentTypeNotAllowedError):
            submit_verification_document(student_profile, b"")

    def test_a_jpeg_header_glued_to_a_script_is_still_stored_as_an_image(self, student_profile):
        """Polyglot check: the sniffer decides, and re-encoding through Pillow
        discards whatever was appended. The stored bytes are a real image or
        the upload fails."""
        payload = a_jpeg() + b"\n<script>alert(1)</script>"

        request = submit_verification_document(student_profile, payload)
        stored = storages["documents"].open(request.document.storage_key).read()

        assert b"<script>" not in stored


class TestSizeCap:
    def test_an_oversized_upload_is_refused(self, student_profile):
        with (
            override_settings(VERIFICATION_MAX_DOCUMENT_BYTES=100),
            pytest.raises(DocumentTooLargeError),
        ):
            submit_verification_document(student_profile, a_jpeg(size=(400, 400)))

    def test_nothing_reaches_the_bucket_when_it_is_refused(self, student_profile):
        """Sniff, cap, strip, store -- in that order. The bucket is the last
        thing touched, never the first."""
        with (
            override_settings(VERIFICATION_MAX_DOCUMENT_BYTES=100),
            pytest.raises(DocumentTooLargeError),
        ):
            submit_verification_document(student_profile, a_jpeg(size=(400, 400)))

        assert VerificationDocument.objects.count() == 0


# ---------------------------------------------------------------------------
# Metadata stripping
# ---------------------------------------------------------------------------


class TestMetadataStripping:
    """A phone photo of a student ID carries the GPS coordinates of wherever it
    was taken, which is usually where that student lives. We have no business
    holding that, so it never reaches the bucket.
    """

    def test_the_fixture_really_carries_exif(self):
        """Otherwise the stripping test below passes vacuously."""
        from PIL import Image

        with Image.open(io.BytesIO(a_jpeg(with_exif=True))) as image:
            assert image.getexif()

    def test_exif_is_gone_after_stripping(self):
        from PIL import Image

        clean = strip_image_metadata(a_jpeg(with_exif=True), "image/jpeg")

        with Image.open(io.BytesIO(clean)) as image:
            assert not image.getexif()

    def test_gps_is_specifically_gone(self):
        from PIL import Image

        clean = strip_image_metadata(a_jpeg(with_exif=True), "image/jpeg")

        with Image.open(io.BytesIO(clean)) as image:
            assert 0x8825 not in image.getexif()

    def test_the_bytes_in_the_bucket_carry_no_exif(self, student_profile):
        """The property that actually matters: not that a helper strips, but
        that nothing with EXIF is ever stored."""
        from PIL import Image

        request = submit_verification_document(student_profile, a_jpeg(with_exif=True))
        stored = storages["documents"].open(request.document.storage_key).read()

        with Image.open(io.BytesIO(stored)) as image:
            assert not image.getexif()

    def test_the_image_is_still_readable(self, student_profile):
        """Stripping must not destroy the thing a reviewer has to look at."""
        from PIL import Image

        request = submit_verification_document(student_profile, a_jpeg(with_exif=True))
        stored = storages["documents"].open(request.document.storage_key).read()

        with Image.open(io.BytesIO(stored)) as image:
            assert image.size == (24, 24)

    def test_a_pdf_is_stored_unchanged(self, student_profile):
        """PDFs carry no EXIF, and rewriting one risks destroying the
        document."""
        request = submit_verification_document(student_profile, A_PDF)
        stored = storages["documents"].open(request.document.storage_key).read()

        assert stored == A_PDF


# ---------------------------------------------------------------------------
# Storage keys
# ---------------------------------------------------------------------------


class TestStorageKeys:
    def test_the_same_student_gets_a_different_key_every_time(
        self, student_profile, university_staff
    ):
        """The property that rules out derivation.

        A key derived from anything about the student -- id, email, a hash of
        either -- would repeat for the same student. Asserting "the pk does not
        appear in the key" proves nothing: a 43-character base64 string
        contains most short digit sequences by chance.
        """
        first = submit_verification_document(student_profile, a_jpeg())
        reject_verification(first, reviewer=university_staff, reason="Blurry.")
        second = submit_verification_document(student_profile, a_jpeg())

        assert first.document.storage_key != second.document.storage_key

    def test_the_key_carries_enough_entropy_to_be_unguessable(self, submitted):
        """A misconfigured bucket is the most common way this data leaks, and
        the key is the last line of defence when it happens."""
        secret = submitted.document.storage_key.removeprefix("verification/")
        secret = secret.rsplit(".", 1)[0]

        assert len(secret) >= 32
        assert len(set(secret)) >= 12

    def test_two_keys_never_collide(self):
        keys = {random_document_key(".jpg") for _ in range(200)}

        assert len(keys) == 200

    def test_the_key_lands_under_the_verification_prefix(self, submitted):
        assert submitted.document.storage_key.startswith("verification/")

    def test_documents_go_to_the_private_store(self, submitted):
        """Never the public CDN. Asserted through the alias, so a settings
        change that repointed it would fail here."""
        assert storages["documents"].exists(submitted.document.storage_key)
        assert not storages["default"].exists(submitted.document.storage_key)

    def test_the_private_store_signs_its_urls(self):
        from config.storage import PrivateDocumentStorage

        assert PrivateDocumentStorage.querystring_auth is True
        assert PrivateDocumentStorage.querystring_expire == (
            settings.VERIFICATION_URL_EXPIRY_SECONDS
        )

    def test_the_expiry_is_minutes_not_hours(self):
        assert settings.VERIFICATION_URL_EXPIRY_SECONDS <= 900


# ---------------------------------------------------------------------------
# The access log
# ---------------------------------------------------------------------------


class TestAccessLogging:
    def test_every_read_writes_a_row(self, submitted, university_staff):
        signed_document_url(submitted.document, reviewer=university_staff)

        assert DocumentAccessLog.objects.count() == 1

    def test_a_second_read_writes_a_second_row(self, submitted, university_staff):
        signed_document_url(submitted.document, reviewer=university_staff)
        signed_document_url(submitted.document, reviewer=university_staff)

        assert DocumentAccessLog.objects.count() == 2

    def test_the_row_names_reviewer_document_time_and_purpose(self, submitted, university_staff):
        signed_document_url(
            submitted.document,
            reviewer=university_staff,
            purpose=AccessPurpose.REVIEW,
            request_id="req-abc123",
        )

        entry = DocumentAccessLog.objects.get()

        assert entry.reviewer == university_staff
        assert entry.document == submitted.document
        assert entry.accessed_at is not None
        assert entry.purpose == AccessPurpose.REVIEW
        assert entry.request_id == "req-abc123"

    def test_the_reviewer_label_survives_the_account(self, submitted, university_staff):
        """SET_NULL on the FK, plus a copied label. A reviewer who leaves must
        be deletable, and the row must still say a read happened."""
        signed_document_url(submitted.document, reviewer=university_staff)
        label = DocumentAccessLog.objects.get().reviewer_label

        assert label

        university_staff.delete()
        entry = DocumentAccessLog.objects.get()

        assert entry.reviewer is None
        assert entry.reviewer_label == label

    def test_the_log_is_written_before_the_url_is_returned(
        self, submitted, university_staff, monkeypatch
    ):
        """If logging fails there must be no URL. An unlogged read is worse
        than a blocked one."""

        def explode(*args, **kwargs):
            raise RuntimeError("log write failed")

        monkeypatch.setattr(DocumentAccessLog.objects, "create", explode)

        with pytest.raises(RuntimeError):
            signed_document_url(submitted.document, reviewer=university_staff)

    def test_a_deleted_document_cannot_be_read(self, submitted, university_staff):
        VerificationDocument.objects.filter(pk=submitted.document.pk).update(
            storage_key="", deleted_at=timezone.now()
        )
        submitted.document.refresh_from_db()

        with pytest.raises(DocumentUnavailableError):
            signed_document_url(submitted.document, reviewer=university_staff)


class TestAccessLogIsAppendOnly:
    """No update path, no delete path -- by construction, not convention.

    The entire value of this table is that it is evidence against the people
    who can write to it.
    """

    def entry(self, submitted, reviewer):
        signed_document_url(submitted.document, reviewer=reviewer)
        return DocumentAccessLog.objects.get()

    def test_an_entry_cannot_be_edited(self, submitted, university_staff):
        entry = self.entry(submitted, university_staff)
        entry.purpose = AccessPurpose.AUDIT

        with pytest.raises(ValidationError):
            entry.save()

    def test_an_entry_cannot_be_deleted(self, submitted, university_staff):
        entry = self.entry(submitted, university_staff)

        with pytest.raises(ValidationError):
            entry.delete()

    def test_the_refusal_explains_itself(self, submitted, university_staff):
        entry = self.entry(submitted, university_staff)

        with pytest.raises(ValidationError) as caught:
            entry.delete()

        assert "append-only" in str(caught.value)

    def test_an_entry_must_name_a_reader(self, submitted):
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentAccessLog.objects.create(
                document=submitted.document,
                reviewer=None,
                reviewer_label="",
                purpose=AccessPurpose.REVIEW,
            )

    def test_the_document_cannot_be_deleted_out_from_under_the_log(
        self, submitted, university_staff
    ):
        """PROTECT. Retention clears the storage key and leaves a tombstone;
        it never removes the row the audit trail points at."""
        self.entry(submitted, university_staff)

        with pytest.raises(IntegrityError), transaction.atomic():
            submitted.document.delete()

    def test_the_log_outlives_the_document(self, submitted, university_staff):
        self.entry(submitted, university_staff)
        VerificationDocument.objects.filter(pk=submitted.document.pk).update(
            storage_key="", deleted_at=timezone.now()
        )

        entry = DocumentAccessLog.objects.get()

        assert entry.document.deleted_at is not None
        assert entry.reviewer_label


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestReviewerQueueIsolation:
    """The isolation failure with the worst consequences in the product.

    On the other side of this boundary are national ID numbers belonging to
    people who never agreed to show them to another institution.
    """

    def test_unqualified_queries_raise(self, submitted):
        with pytest.raises(TenantScopeError):
            list(VerificationRequest.objects.all())

    def test_a_reviewer_sees_their_own_universitys_queue(self, student_profile, university):
        submit_verification_document(student_profile, a_jpeg())

        queue = VerificationRequest.objects.for_tenant(university)

        assert queue.count() == 1

    def test_a_reviewer_cannot_see_another_universitys_queue(
        self, student_profile, university, university_factory, student_profile_factory
    ):
        """Two universities, one request each. Neither queue may contain the
        other's student."""
        other_university = university_factory()
        other_student = student_profile_factory(university=other_university)

        mine = submit_verification_document(student_profile, a_jpeg())
        theirs = submit_verification_document(other_student, a_jpeg())

        my_queue = VerificationRequest.objects.for_tenant(university)
        their_queue = VerificationRequest.objects.for_tenant(other_university)

        assert mine in my_queue
        assert theirs not in my_queue
        assert theirs in their_queue
        assert mine not in their_queue

    def test_the_other_universitys_document_is_not_reachable_by_traversal(
        self, student_profile, university, university_factory, student_profile_factory
    ):
        """Scoping the queue is not enough if the document is reachable from
        an unscoped join. Asserted from the document side."""
        other_university = university_factory()
        other_student = student_profile_factory(university=other_university)
        submit_verification_document(student_profile, a_jpeg())
        theirs = submit_verification_document(other_student, a_jpeg())

        reachable = VerificationDocument.objects.filter(
            requests__in=VerificationRequest.objects.for_tenant(university)
        )

        assert theirs.document not in reachable


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class TestDecisions:
    def test_approval_verifies_the_student(self, submitted, university_staff):
        approve_verification(submitted, reviewer=university_staff)
        submitted.profile.refresh_from_db()

        assert submitted.profile.verification_status == VerificationStatus.VERIFIED
        assert submitted.profile.verification_method == (VerificationMethod.STUDENT_ID_UPLOAD)

    def test_approval_records_the_reviewer_internally(self, submitted, university_staff):
        approve_verification(submitted, reviewer=university_staff)
        submitted.refresh_from_db()

        assert submitted.reviewed_by == university_staff
        assert submitted.reviewed_at is not None

    def test_the_student_is_not_told_who_reviewed(self, submitted, university_staff):
        """A named individual refusing a student's ID at their own institution
        is a person who can be found in a corridor. The field carries a
        help_text saying so; this asserts the reason is recorded, since the
        serialiser that must honour it does not exist yet."""
        field = VerificationRequest._meta.get_field("reviewed_by")

        assert "never serialised" in str(field.help_text).lower()

    def test_rejection_needs_a_reason(self, submitted, university_staff):
        with pytest.raises(ValidationError):
            reject_verification(submitted, reviewer=university_staff, reason="")

    def test_the_database_refuses_a_reasonless_rejection(self, submitted):
        submitted.status = VerificationRequestStatus.REJECTED
        submitted.reviewed_at = timezone.now()

        with pytest.raises(IntegrityError), transaction.atomic():
            submitted.save()

    def test_the_reason_reaches_the_student(self, submitted, university_staff):
        reject_verification(
            submitted, reviewer=university_staff, reason="The photo is too blurry to read."
        )
        submitted.profile.refresh_from_db()

        assert submitted.profile.rejection_reason == "The photo is too blurry to read."

    def test_a_decision_must_name_a_time(self, submitted):
        submitted.status = VerificationRequestStatus.APPROVED

        with pytest.raises(IntegrityError), transaction.atomic():
            submitted.save()

    def test_only_one_open_request_per_student(self, student_profile):
        submit_verification_document(student_profile, a_jpeg())

        with pytest.raises(IntegrityError), transaction.atomic():
            submit_verification_document(student_profile, a_jpeg())


class TestResubmission:
    def test_rejection_is_not_terminal(self, student_profile, university_staff):
        """A blurry photo is the common case, and a dead end for it would be
        an accessibility failure dressed as a security control."""
        first = submit_verification_document(student_profile, a_jpeg())
        reject_verification(first, reviewer=university_staff, reason="Too blurry.")

        second = submit_verification_document(student_profile, a_jpeg())

        assert second.pk != first.pk
        assert second.attempt == 2

    def test_resubmission_is_capped(self, student_profile, university_staff):
        """An uncapped retry loop is an uncapped upload channel for identity
        documents."""
        with override_settings(VERIFICATION_MAX_SUBMISSIONS=2):
            for _ in range(2):
                request = submit_verification_document(student_profile, a_jpeg())
                reject_verification(request, reviewer=university_staff, reason="No.")

            with pytest.raises(ResubmissionLimitError):
                submit_verification_document(student_profile, a_jpeg())

    def test_the_cap_message_says_what_to_do_next(self, student_profile):
        with (
            override_settings(VERIFICATION_MAX_SUBMISSIONS=0),
            pytest.raises(ResubmissionLimitError) as caught,
        ):
            submit_verification_document(student_profile, a_jpeg())

        assert "university" in str(caught.value).lower()


class TestStrippingIsAffordable:
    """The strip runs synchronously inside the upload request.

    An implementation that is correct but costs 15 seconds and 838 MB per file
    is a denial of service with a valid explanation. Both properties are
    asserted here, because only one of them was ever true.

    The photo used is a real 4032x3024 JPEG with EXIF and GPS -- the ordinary
    case, and the one no test had ever supplied. Everything before this used a
    synthetic image a few pixels wide, which exercises the code path and none
    of its cost.
    """

    @staticmethod
    def phone_photo() -> bytes:
        from config.management.commands._seed_images import generate

        data, _content_type, _name = generate("phone_4mb", seed=1)
        return data

    def test_it_removes_gps_from_a_real_phone_photo(self):
        import piexif

        from accounts.documents import strip_image_metadata

        raw = self.phone_photo()
        assert piexif.load(raw)["GPS"], "the fixture should carry GPS to begin with"

        clean = strip_image_metadata(raw, "image/jpeg")

        assert not piexif.load(clean)["GPS"]

    def test_it_removes_the_device_make_and_model(self):
        import piexif

        from accounts.documents import strip_image_metadata

        clean = strip_image_metadata(self.phone_photo(), "image/jpeg")
        zeroth = piexif.load(clean)["0th"]

        assert piexif.ImageIFD.Make not in zeroth
        assert piexif.ImageIFD.Model not in zeroth

    def test_it_does_not_allocate_a_python_object_per_pixel(self):
        """12 million pixels became 12 million tuples, in the request path.

        Asserted as a memory ceiling rather than a timing, because timings are
        flaky on shared CI and this failure is a hundredfold, not a fraction.
        """
        import tracemalloc

        from accounts.documents import strip_image_metadata

        raw = self.phone_photo()

        tracemalloc.start()
        strip_image_metadata(raw, "image/jpeg")
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()

        # The old implementation peaked at 838 MB on this exact file. Fifty is
        # far above the 5 MB this version needs and far below that.
        assert peak < 50 * 1024 * 1024, f"peaked at {peak / 1024 / 1024:.0f} MB"

    def test_the_image_still_decodes_afterwards(self):
        """A strip that corrupts the file would pass both assertions above."""
        import io

        from PIL import Image

        from accounts.documents import strip_image_metadata

        clean = strip_image_metadata(self.phone_photo(), "image/jpeg")

        with Image.open(io.BytesIO(clean)) as image:
            assert image.size == (4032, 3024)

    def test_a_pdf_is_returned_untouched(self):
        from accounts.documents import strip_image_metadata

        pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 64

        assert strip_image_metadata(pdf, "application/pdf") == pdf

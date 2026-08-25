"""
Verified deletion, against a real S3-compatible store.

`InMemoryStorage` is a dict. Its `delete()` always removes the key and its
`exists()` always tells the truth, so **every verified-delete test passes
against it whether or not the verification does anything at all**. That makes
the in-memory suite worthless for the one property this job exists to have.

These tests run against MinIO over the real S3 API and are skipped when it is
not reachable. CI runs MinIO as a service container, so they run there.

The property under test: `deleted_at` is written **only** when a re-read
confirms the object is gone, and a delete that silently fails leaves the row
undeleted, counted and alertable.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.documents import VerificationDocument
from accounts.retention import (
    DeletionUnconfirmedError,
    delete_verification_document,
    unconfirmed_deletions,
)

pytestmark = [pytest.mark.django_db, pytest.mark.minio]


MINIO_ENDPOINT = "http://127.0.0.1:9000"
DOCUMENTS_BUCKET = "campusrental-documents"


def _minio_storage():
    """A real S3Storage pointed at the local MinIO, or None if unreachable."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
        from storages.backends.s3 import S3Storage
    except ImportError:  # pragma: no cover - slim build
        return None

    try:
        client = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id="minioadmin",
            aws_secret_access_key="minioadmin",
            region_name="us-east-1",
        )
        client.head_bucket(Bucket=DOCUMENTS_BUCKET)
    except (ClientError, BotoCoreError, OSError):
        return None

    return S3Storage(
        bucket_name=DOCUMENTS_BUCKET,
        endpoint_url=MINIO_ENDPOINT,
        access_key="minioadmin",
        secret_key="minioadmin",
        region_name="us-east-1",
        querystring_auth=True,
        querystring_expire=300,
        file_overwrite=False,
        default_acl=None,
    )


def test_minio_is_reachable_in_ci() -> None:
    """A silent skip of a compliance test is indistinguishable from a pass.

    Locally, MinIO may not be running and skipping is right. In CI it is a
    service container, so its absence means the workflow is misconfigured and
    the verified-delete property is going untested while the build stays
    green -- which is the worst of both.
    """
    import os

    if not os.environ.get("CI"):
        pytest.skip("not CI; MinIO is optional locally")

    assert _minio_storage() is not None, (
        "MinIO is unreachable in CI. The verified-delete tests would skip, and "
        "a skipped compliance test looks exactly like a passing one."
    )


@pytest.fixture
def minio():
    storage = _minio_storage()
    if storage is None:
        pytest.skip("MinIO is not reachable at 127.0.0.1:9000")
    return storage


@pytest.fixture
def stored(minio, monkeypatch):
    """A real object in a real bucket, with the job pointed at that store."""
    monkeypatch.setattr("accounts.retention._storage", lambda: minio)

    key = f"verification/test-{uuid.uuid4().hex}.jpg"
    minio.save(key, ContentFile(b"\xff\xd8\xff\xe0 pretend this is an ID card"))

    document = VerificationDocument.objects.create(
        storage_key=key, content_type="image/jpeg", byte_size=32
    )
    yield document, key, minio

    if minio.exists(key):
        minio.delete(key)


class TestVerifiedDeleteAgainstMinio:
    def test_the_object_really_exists_first(self, stored):
        """Otherwise the deletion test below passes vacuously — which is
        exactly how an in-memory suite hides a broken delete."""
        _document, key, storage = stored

        assert storage.exists(key) is True

    def test_deletion_removes_the_object_from_the_bucket(self, stored):
        document, key, storage = stored

        assert delete_verification_document(document.pk) is True
        assert storage.exists(key) is False

    def test_the_row_is_marked_only_after_the_object_is_gone(self, stored):
        document, _key, _storage = stored

        delete_verification_document(document.pk)
        document.refresh_from_db()

        assert document.deleted_at is not None
        assert document.storage_key == ""

    def test_a_silently_failing_delete_is_not_marked_deleted(self, stored, monkeypatch):
        """The whole point of the job.

        S3-compatible stores answer a delete of an unremovable key with a 204
        in several situations — a bucket policy denying DeleteObject, an
        eventually-consistent replica, a versioned bucket where deletion writes
        a marker and leaves the version readable. Simulated here by making
        delete() a no-op that returns cleanly, which is precisely what those
        cases look like from the client side.
        """
        document, key, storage = stored
        monkeypatch.setattr(storage, "delete", lambda _key: None)

        confirmed = delete_verification_document(document.pk)
        document.refresh_from_db()

        assert confirmed is False
        assert document.deleted_at is None
        assert document.storage_key == key
        assert storage.exists(key) is True

    def test_an_unconfirmed_delete_is_counted_and_alertable(self, stored, monkeypatch):
        document, _key, storage = stored
        monkeypatch.setattr(storage, "delete", lambda _key: None)

        delete_verification_document(document.pk)
        document.refresh_from_db()

        assert document.delete_attempts == 1
        assert "still readable" in document.last_delete_error
        assert document in unconfirmed_deletions()

    def test_it_retries_and_succeeds_once_the_store_recovers(self, stored, monkeypatch):
        """A delete that cannot be confirmed retries; it never silently marks
        success and moves on."""
        document, key, storage = stored
        real_delete = storage.delete
        monkeypatch.setattr(storage, "delete", lambda _key: None)

        assert delete_verification_document(document.pk) is False

        monkeypatch.setattr(storage, "delete", real_delete)

        assert delete_verification_document(document.pk) is True
        document.refresh_from_db()

        assert document.deleted_at is not None
        assert storage.exists(key) is False

    def test_deleting_twice_is_harmless(self, stored):
        """Jobs must be idempotent: the sweep may enqueue a row twice."""
        document, _key, _storage = stored

        assert delete_verification_document(document.pk) is True
        assert delete_verification_document(document.pk) is True

    def test_the_verification_step_is_what_catches_it(self, stored, monkeypatch):
        """Asserted at the helper, so a future refactor that drops the re-read
        fails here rather than silently passing everything above."""
        from accounts.retention import _delete_and_verify

        _document, key, storage = stored
        monkeypatch.setattr(storage, "delete", lambda _key: None)

        with pytest.raises(DeletionUnconfirmedError):
            _delete_and_verify(key)


class TestBucketIsolationOnMinio:
    def test_documents_are_not_anonymously_readable(self, minio, stored):
        """The documents bucket is private; the media bucket is not. A key
        prefix inside one shared bucket would be one careless policy change
        away from publishing a national ID (ADR-007)."""
        import urllib.error
        import urllib.request

        _document, key, _storage = stored
        unsigned = f"{MINIO_ENDPOINT}/{DOCUMENTS_BUCKET}/{key}"

        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(unsigned, timeout=10)  # noqa: S310

        assert caught.value.code in (401, 403)

    def test_a_signed_url_does_work(self, minio, stored):
        """The private bucket is unreadable anonymously and readable with a
        signature — both halves, or the test above proves only that the URL
        was wrong."""
        import urllib.request

        _document, key, storage = stored

        with urllib.request.urlopen(storage.url(key), timeout=10) as response:  # noqa: S310
            body = response.read()

        assert body.startswith(b"\xff\xd8\xff")
        assert io.BytesIO(body).read(3) == b"\xff\xd8\xff"


class TestRetentionDeadlinesAgainstMinio:
    def test_an_unreviewed_document_is_deleted_on_the_absolute_deadline(
        self, stored, student_profile
    ):
        """The gap the earlier spec had: without this the document lives for
        ever, and an unworked queue is the likeliest real-world case."""
        from accounts.retention import documents_past_absolute_retention

        document, key, storage = stored
        VerificationDocument.objects.filter(pk=document.pk).update(
            uploaded_at=timezone.now() - dt.timedelta(days=400)
        )

        assert document in documents_past_absolute_retention()

        delete_verification_document(document.pk)

        assert storage.exists(key) is False

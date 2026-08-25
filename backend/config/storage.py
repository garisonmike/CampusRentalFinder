"""
Object storage (ADR-007).

Two buckets, two backend classes, and they must not be able to become one by
misconfiguration:

- **public media** — listing photos and university logos. Unsigned URLs, served
  through the CDN.
- **private documents** — student ID uploads and landlord ID documents.
  Short-lived signed URLs, never the CDN.

A key-prefix convention inside one bucket would be one careless
``default_storage.save()`` away from publishing someone's national ID, which is
regulated personal data under Kenya's Data Protection Act 2019 (ADR-003). The
separation is therefore two backends, and ``check_storage_separation`` asserts
at startup that they really are separate.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register
from django.core.files.storage import InMemoryStorage, storages

try:  # pragma: no cover - exercised by whichever branch the env selects
    from storages.backends.s3 import S3Storage
except ImportError:  # pragma: no cover - django-storages absent in a slim build
    S3Storage = None  # type: ignore[assignment,misc]


class PublicMediaStorage(S3Storage if S3Storage else InMemoryStorage):  # type: ignore[misc]
    """Listing photos and logos. Public by design."""

    #: Unsigned: these are public content and the CDN caches them.
    querystring_auth = False
    file_overwrite = False
    #: R2 does not implement ACLs.
    default_acl = None


class PrivateDocumentStorage(S3Storage if S3Storage else InMemoryStorage):  # type: ignore[misc]
    """Verification documents. Never public, never CDN-served.

    Access is by short-lived signed URL, generated per reviewer request and
    logged (ADR-003).
    """

    querystring_auth = True
    #: Minutes, not hours. Long enough to open, short enough that a leaked URL
    #: in a log or a referrer header is worth little. Read from settings so the
    #: expiry is one knob rather than two that can disagree.
    querystring_expire = settings.VERIFICATION_URL_EXPIRY_SECONDS
    file_overwrite = False
    default_acl = None


def _bucket_of(alias: str) -> str | None:
    """The bucket a configured storage alias points at, if it has one."""
    try:
        backend = storages[alias]
    except Exception:
        return None
    return getattr(backend, "bucket_name", None)


@register()
def check_storage_separation(app_configs, **kwargs):
    """Refuse to start with the two buckets pointing at the same place.

    ADR-003 treats ID documents as regulated personal data. Sharing a bucket
    with public photos would make the CDN configuration the only thing standing
    between a national ID and the open internet, and CDN configuration is not
    where that guarantee belongs.

    A Django system check rather than an assertion in a module, so it runs on
    ``manage.py check`` in CI and on every ``runserver``, and names the problem
    rather than raising an opaque error later.
    """
    errors = []

    for alias in ("default", "documents"):
        try:
            storages[alias]
        except Exception:
            errors.append(
                Error(
                    f'STORAGES is missing the "{alias}" alias.',
                    hint="ADR-007 requires a public media backend and a private document backend.",
                    id="storage.E001",
                )
            )

    if errors:
        return errors

    public_bucket = _bucket_of("default")
    document_bucket = _bucket_of("documents")

    # Both None means neither is an S3 backend -- the in-memory test setup,
    # which is isolated by construction.
    if public_bucket is None and document_bucket is None:
        return errors

    if public_bucket == document_bucket:
        errors.append(
            Error(
                "The public media bucket and the private document bucket are the same "
                f"bucket ({public_bucket!r}).",
                hint=(
                    "Set S3_MEDIA_BUCKET and S3_DOCUMENTS_BUCKET to different buckets. "
                    "Verification documents are regulated personal data (ADR-003) and "
                    "must not share a bucket with public listing photos."
                ),
                id="storage.E002",
            )
        )

    documents = storages["documents"]
    if getattr(documents, "querystring_auth", None) is False:
        errors.append(
            Error(
                "The document storage backend serves unsigned URLs.",
                hint="querystring_auth must be True: documents are access-controlled.",
                id="storage.E003",
            )
        )

    return errors

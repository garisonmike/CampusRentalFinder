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


#: Every extension a stored object key may end in.
#:
#: **An allowlist, at the point keys are generated.** The first version of this
#: rule was a test asserting no key ends in `.svg` -- a denylist of one, which
#: leaves `.html`, `.xhtml`, `.xml`, `.svgz`, `.js`, `.mhtml` and whatever a
#: browser decides to render next.
#:
#: The risk is specific: object stores derive `Content-Type` from the key's
#: extension. MinIO returns `image/svg+xml` for a `.svg` key, and an SVG is a
#: script container. Served from a host inside the application's cookie scope
#: -- one branded media domain and one domain cookie away, see ADR-001 -- that
#: is stored XSS.
#:
#: Nothing here is active content in any browser. Adding to this list is a
#: security decision and should read like one in a diff.
ALLOWED_STORED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".pdf"})


class UnsafeStorageKeyError(ValueError):
    """This key would be served as something a browser executes."""


def assert_storage_key_is_safe(key: str) -> str:
    """Refuse a key whose extension could be served as active content.

    Called wherever a key is generated, not only asserted in a test: a rule
    enforced by a test holds for the code the test knows about, and a rule
    enforced at the point of construction holds for the code nobody has
    written yet.
    """
    _, _, extension = key.rpartition(".")
    suffix = f".{extension.lower()}" if extension and extension != key else ""

    if suffix not in ALLOWED_STORED_EXTENSIONS:
        raise UnsafeStorageKeyError(
            f"{key!r} ends in {suffix or 'no extension'}, which is not in "
            f"ALLOWED_STORED_EXTENSIONS. Object stores derive Content-Type "
            f"from the key, so this could be served as active content."
        )

    return key


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

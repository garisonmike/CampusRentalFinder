"""
The public/private storage split (ADR-007).

Verification documents are regulated personal data under Kenya's Data
Protection Act 2019. They live in a bucket of their own, behind a separate
backend class, and the two must not be able to become one by misconfiguration.
"""

from __future__ import annotations

import pytest
from django.core.files.storage import storages
from django.test import override_settings

from config.storage import (
    PrivateDocumentStorage,
    PublicMediaStorage,
    check_storage_separation,
)

pytestmark = pytest.mark.architecture

PUBLIC = "config.storage.PublicMediaStorage"
PRIVATE = "config.storage.PrivateDocumentStorage"


def s3_settings(*, media_bucket: str, documents_bucket: str) -> dict:
    common = {
        "endpoint_url": "https://example.r2.cloudflarestorage.com",
        "region_name": "auto",
        "access_key": "not-a-real-key",
        "secret_key": "not-a-real-secret",
    }
    return {
        "default": {"BACKEND": PUBLIC, "OPTIONS": {**common, "bucket_name": media_bucket}},
        "documents": {"BACKEND": PRIVATE, "OPTIONS": {**common, "bucket_name": documents_bucket}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


class TestBackendClasses:
    def test_the_public_backend_serves_unsigned_urls(self):
        """Listing photos are public content and the CDN caches them."""
        assert PublicMediaStorage.querystring_auth is False

    def test_the_private_backend_signs_and_expires(self):
        assert PrivateDocumentStorage.querystring_auth is True
        assert PrivateDocumentStorage.querystring_expire == 300

    def test_neither_overwrites(self):
        """An overwrite would silently replace one person's document with another's."""
        assert PublicMediaStorage.file_overwrite is False
        assert PrivateDocumentStorage.file_overwrite is False

    def test_they_are_different_classes(self):
        """Not a key-prefix convention inside one backend (ADR-007).

        A convention is one careless default_storage.save() away from
        publishing someone's national ID; a separate class makes the public
        path unreachable from the document model's field.
        """
        assert PublicMediaStorage is not PrivateDocumentStorage


class TestStorageSeparationCheck:
    def test_separate_buckets_pass(self):
        with override_settings(
            STORAGES=s3_settings(media_bucket="crf-media", documents_bucket="crf-documents")
        ):
            storages._storages.clear()
            assert check_storage_separation(None) == []

    def test_the_same_bucket_for_both_is_an_error(self):
        """The check that has to bite.

        Sharing a bucket would leave CDN configuration as the only thing
        between a national ID and the open internet, and that is not where the
        guarantee belongs.
        """
        with override_settings(
            STORAGES=s3_settings(media_bucket="crf-shared", documents_bucket="crf-shared")
        ):
            storages._storages.clear()
            errors = check_storage_separation(None)

        assert [error.id for error in errors] == ["storage.E002"]
        assert "crf-shared" in errors[0].msg

    def test_a_missing_documents_alias_is_an_error(self):
        with override_settings(
            STORAGES={
                "default": {"BACKEND": PUBLIC, "OPTIONS": {"bucket_name": "crf-media"}},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            }
        ):
            storages._storages.clear()
            errors = check_storage_separation(None)

        assert [error.id for error in errors] == ["storage.E001"]

    def test_an_unsigned_document_backend_is_an_error(self):
        """querystring_auth=False on the document bucket makes it public."""
        settings = s3_settings(media_bucket="crf-media", documents_bucket="crf-documents")
        settings["documents"]["BACKEND"] = PUBLIC

        with override_settings(STORAGES=settings):
            storages._storages.clear()
            errors = check_storage_separation(None)

        assert "storage.E003" in [error.id for error in errors]

    def test_the_in_memory_test_setup_reports_nothing(self):
        """Both aliases are in-memory and isolated by construction."""
        storages._storages.clear()

        assert check_storage_separation(None) == []

    def test_the_check_is_registered_with_django(self):
        """It runs on `manage.py check`, so CI catches a misconfiguration.

        A module-level assertion would only fire when something imported it.
        """
        from django.core.checks import registry

        assert check_storage_separation in registry.registry.get_checks()


class TestStorageIsNeverLocalDisk:
    def test_the_default_alias_is_the_public_bucket_not_a_filesystem(self):
        """A model field that forgets to name a storage writes a listing photo
        to a public place, not a document to it (ADR-007)."""
        from django.conf import settings as django_settings

        backends = {
            alias: config["BACKEND"]
            for alias, config in django_settings.STORAGES.items()
            if alias != "staticfiles"
        }

        for alias, backend in backends.items():
            assert "FileSystemStorage" not in backend, f"{alias} writes to local disk"


class TestStoredKeysCannotBeActiveContent:
    """An allowlist of permitted extensions, enforced where keys are made.

    The first version of this rule was a test asserting no key ends in `.svg`
    -- a denylist of one, which leaves `.html`, `.xhtml`, `.xml`, `.svgz`,
    `.js` and whatever a browser decides to render next.

    The risk is specific. Object stores derive `Content-Type` from the key's
    extension; MinIO returns `image/svg+xml` for a `.svg` key, and an SVG is a
    script container. Served from a host inside the app's cookie scope -- one
    branded media domain and one domain cookie away (ADR-001) -- that is
    stored XSS.
    """

    @pytest.mark.parametrize(
        "extension",
        [".svg", ".svgz", ".html", ".xhtml", ".xml", ".js", ".mhtml", ".htm"],
    )
    def test_active_content_extensions_are_refused(self, extension):
        from config.storage import UnsafeStorageKeyError, assert_storage_key_is_safe

        with pytest.raises(UnsafeStorageKeyError):
            assert_storage_key_is_safe(f"units/1/abc{extension}")

    @pytest.mark.parametrize("extension", [".jpg", ".jpeg", ".png", ".webp", ".pdf"])
    def test_the_permitted_ones_pass(self, extension):
        from config.storage import assert_storage_key_is_safe

        assert assert_storage_key_is_safe(f"units/1/abc{extension}").endswith(extension)

    def test_a_key_with_no_extension_is_refused(self):
        """`Content-Type` for an extensionless object is the store's guess,
        and a guess is not a guarantee."""
        from config.storage import UnsafeStorageKeyError, assert_storage_key_is_safe

        with pytest.raises(UnsafeStorageKeyError):
            assert_storage_key_is_safe("units/1/no-extension-here")

    def test_case_does_not_get_past_it(self):
        from config.storage import UnsafeStorageKeyError, assert_storage_key_is_safe

        with pytest.raises(UnsafeStorageKeyError):
            assert_storage_key_is_safe("units/1/abc.SVG")

    def test_it_is_enforced_at_generation_not_only_in_this_file(self, unit_factory):
        """A rule enforced by a test holds for the code the test knows about.
        A rule enforced at the point of construction holds for code nobody has
        written yet."""
        import inspect

        from accounts.documents import random_document_key
        from properties import services

        assert "assert_storage_key_is_safe" in inspect.getsource(random_document_key)
        assert "assert_storage_key_is_safe" in inspect.getsource(services.add_photo)

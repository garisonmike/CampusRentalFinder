"""Settings for the automated test suite."""

from __future__ import annotations

from .base import *
from .base import BASE_DIR, SIMPLE_JWT

DEBUG = False

SECRET_KEY = "test-secret-key-not-used-outside-the-test-suite"
SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY

ALLOWED_HOSTS = ["*", "testserver"]

CORS_ALLOWED_ORIGINS = ["http://testserver"]

# Fast, deterministic hashing. Real hashers make the suite several times slower
# for no additional coverage.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Tests must never depend on a running Redis.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

MEDIA_ROOT = BASE_DIR / "test-media"

"""Local development settings. Never use these for a deployed environment."""

from __future__ import annotations

from decouple import Csv, config

from config.logging_config import configure_logging

from .base import *
from .base import BASE_DIR, LOG_JSON, SIMPLE_JWT

DEBUG = True

# A fixed throwaway key so local sessions survive a restart. This branch is
# unreachable in prod.py, which refuses to start without a real key.
SECRET_KEY = config("SECRET_KEY", default="django-insecure-local-development-only")
SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY

ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1,0.0.0.0,[::1],backend",
    cast=Csv(),
)

# Explicit, even locally. Add your machine's LAN address via the env var
# rather than reaching for CORS_ALLOW_ALL_ORIGINS.
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default=(
        "http://localhost:8080,http://127.0.0.1:8080,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    ),
    cast=Csv(),
)

CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="http://localhost:8080,http://127.0.0.1:8080",
    cast=Csv(),
)

INSTALLED_APPS = [*INSTALLED_APPS, "django_extensions"]

MEDIA_ROOT = BASE_DIR / "media"

configure_logging(level=config("LOG_LEVEL", default="DEBUG"), json_output=LOG_JSON)

# Local development and tests have no usable subdomain, so the header fallback
# is available here. It is impossible in production; see prod.py.
TENANT_HEADER_FALLBACK_ENABLED = True


# Local object storage. MinIO speaks the S3 API, so the development path
# exercises the same code as production (ADR-007).
S3_ENDPOINT_URL = config("S3_ENDPOINT_URL", default="http://minio:9000")
S3_ACCESS_KEY_ID = config("S3_ACCESS_KEY_ID", default="minioadmin")
S3_SECRET_ACCESS_KEY = config("S3_SECRET_ACCESS_KEY", default="minioadmin")

STORAGES = {
    "default": {
        "BACKEND": "config.storage.PublicMediaStorage",
        "OPTIONS": {
            "endpoint_url": S3_ENDPOINT_URL,
            "region_name": "auto",
            "access_key": S3_ACCESS_KEY_ID,
            "secret_key": S3_SECRET_ACCESS_KEY,
            "bucket_name": config("S3_MEDIA_BUCKET", default="campusrental-media"),
            "url_protocol": "http:",
        },
    },
    "documents": {
        "BACKEND": "config.storage.PrivateDocumentStorage",
        "OPTIONS": {
            "endpoint_url": S3_ENDPOINT_URL,
            "region_name": "auto",
            "access_key": S3_ACCESS_KEY_ID,
            "secret_key": S3_SECRET_ACCESS_KEY,
            "bucket_name": config("S3_DOCUMENTS_BUCKET", default="campusrental-documents"),
            "url_protocol": "http:",
        },
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

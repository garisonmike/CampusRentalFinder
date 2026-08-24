"""
Production settings.

This module refuses to import unless the environment is configured safely.
Failing at import time is deliberate: a container that will not start is far
easier to notice than one serving traffic with a known secret key.
"""

from __future__ import annotations

from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

from config.logging_config import configure_logging

from .base import *
from .base import SIMPLE_JWT

DEBUG = False

SECRET_KEY = config("SECRET_KEY", default="")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY environment variable is required in production. "
        "Generate one with: python -c "
        "'from django.core.management.utils import get_random_secret_key; "
        "print(get_random_secret_key())'"
    )
if SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured(
        "SECRET_KEY is a Django development placeholder. Generate a real one."
    )
SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="", cast=Csv())
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")

CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())
if not CORS_ALLOWED_ORIGINS:
    raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS must list the frontend origins explicitly.")

CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# --------------------------------------------------------------------------
# Transport security
# --------------------------------------------------------------------------

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
# Set only when a trusted proxy terminates TLS and sets this header. An
# untrusted proxy makes this a downgrade vector, hence the env switch.
if config("USE_X_FORWARDED_PROTO", default=True, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False  # the SPA must read this to send X-CSRFToken
CSRF_COOKIE_SAMESITE = "Lax"

# --------------------------------------------------------------------------
# Tenant isolation (ADR-001)
# --------------------------------------------------------------------------

# Absence is not sufficient. If this is ever true on a deployed host, any
# client can read another tenant's scoped data by setting a header, so a
# container misconfigured this way must fail to boot rather than serve traffic
# with the bypass available.
TENANT_HEADER_FALLBACK_ENABLED = config("TENANT_HEADER_FALLBACK_ENABLED", default=False, cast=bool)
if TENANT_HEADER_FALLBACK_ENABLED:
    raise ImproperlyConfigured(
        "TENANT_HEADER_FALLBACK_ENABLED must never be true in production: it "
        "lets any client select a tenant with the X-University header, which "
        "defeats the isolation boundary in ADR-001."
    )

SITE_DOMAIN = config("SITE_DOMAIN", default="")
if not SITE_DOMAIN:
    raise ImproperlyConfigured("SITE_DOMAIN must be set in production.")

USE_HTTPS_URLS = True

configure_logging(
    level=config("LOG_LEVEL", default="INFO"),
    json_output=config("LOG_JSON", default=True, cast=bool),
)

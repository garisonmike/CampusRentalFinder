"""
Base settings shared by every environment.

Environment-specific modules (dev, prod, test) import from here and override.
Nothing in this module may enable an insecure default: anything that would be
unsafe in production must be switched on explicitly in ``dev.py``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

# backend/config/settings/base.py -> backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

# No default. dev.py and test.py supply a throwaway key; prod.py requires the
# environment to provide one and raises if it does not.
SECRET_KEY = config("SECRET_KEY", default="")

DEBUG = config("DEBUG", default=False, cast=bool)

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="", cast=Csv())

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # ArrayField, ExclusionConstraint and the extension operations.
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    # Required for token blacklisting on logout and for
    # SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"] to do anything at all.
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "django_rq",
]

LOCAL_APPS = [
    "universities",
    "accounts",
    "properties",
    "tenancies",
    "ratings",
    "rentals",
    "reviews",
]

# The project package itself, so its cross-app management commands are
# discovered. Deliberately NOT in LOCAL_APPS: that list is the domain apps, and
# the architecture test walks it for tenant scoping. config holds no models.
PROJECT_APPS = ["config"]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + PROJECT_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # After authentication so a view sees both; before anything that queries
    # tenant-scoped data (ADR-001).
    "config.middleware.TenantResolutionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB", default="campus_rental"),
        "USER": config("POSTGRES_USER", default="postgres"),
        "PASSWORD": config("POSTGRES_PASSWORD", default="postgres"),
        "HOST": config("POSTGRES_HOST", default="localhost"),
        "PORT": config("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": config("CONN_MAX_AGE", default=60, cast=int),
    }
}

# DATABASE_URL, when present, wins. Keeps twelve-factor deploys simple.
_database_url = config("DATABASE_URL", default="")
if _database_url:
    import dj_database_url

    DATABASES["default"] = dict(
        dj_database_url.parse(
            _database_url,
            conn_max_age=config("CONN_MAX_AGE", default=60, cast=int),
        )
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# --------------------------------------------------------------------------
# Cache / Redis
# --------------------------------------------------------------------------

REDIS_URL = config("REDIS_URL", default="redis://127.0.0.1:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# Django REST Framework
# --------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_MINUTES", default=15, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_DAYS", default=7, cast=int)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "CampusRentalFinder API",
    "DESCRIPTION": "API for the multi-university student rental platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1/",
}

# --------------------------------------------------------------------------
# Hosts (ADR-001)
# --------------------------------------------------------------------------

# Root domain. Tenant subdomains and the canonical host are built from it.
SITE_DOMAIN = config("SITE_DOMAIN", default="localhost:8000")

# Public listing content is canonical here, tenant-neutral.
CANONICAL_HOST_PREFIX = config("CANONICAL_HOST_PREFIX", default="www")

# Admin, schema and probes.
INTERNAL_HOST_PREFIX = config("INTERNAL_HOST_PREFIX", default="internal")

USE_HTTPS_URLS = config("USE_HTTPS_URLS", default=False, cast=bool)

# Resolve the tenant from an X-University header when there is no usable
# subdomain. Local development and the test suite only: on a deployed host it
# would let any client read another tenant's data by setting a header.
# prod.py raises at import time if this is ever true there.
TENANT_HEADER_FALLBACK_ENABLED = False

# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------

# Explicit list only. CORS_ALLOW_ALL_ORIGINS is deliberately never set anywhere
# in this project -- widen this env var instead.
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# --------------------------------------------------------------------------
# Internationalisation
# --------------------------------------------------------------------------

# Kenya is the launch market; see docs/DOMAIN_MODEL.md.
LANGUAGE_CODE = "en-ke"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static and media
# --------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [d for d in [BASE_DIR / "static"] if d.exists()]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ADR-007: two buckets, two backend classes, never local disk. The default
# alias is the PUBLIC one, so a model field that forgets to name a storage
# writes a listing photo to a public place rather than a document to it.
S3_ENDPOINT_URL = config("S3_ENDPOINT_URL", default="")
S3_REGION = config("S3_REGION", default="auto")
S3_MEDIA_BUCKET = config("S3_MEDIA_BUCKET", default="campusrental-media")
S3_DOCUMENTS_BUCKET = config("S3_DOCUMENTS_BUCKET", default="campusrental-documents")
S3_ACCESS_KEY_ID = config("S3_ACCESS_KEY_ID", default="")
S3_SECRET_ACCESS_KEY = config("S3_SECRET_ACCESS_KEY", default="")

_S3_COMMON = {
    "endpoint_url": S3_ENDPOINT_URL,
    "region_name": S3_REGION,
    "access_key": S3_ACCESS_KEY_ID,
    "secret_key": S3_SECRET_ACCESS_KEY,
}

STORAGES = {
    "default": {
        "BACKEND": "config.storage.PublicMediaStorage",
        "OPTIONS": {**_S3_COMMON, "bucket_name": S3_MEDIA_BUCKET},
    },
    "documents": {
        "BACKEND": "config.storage.PrivateDocumentStorage",
        "OPTIONS": {**_S3_COMMON, "bucket_name": S3_DOCUMENTS_BUCKET},
    },
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@campusrentalfinder.co.ke")

# --------------------------------------------------------------------------
# Logging (structlog; see config/logging_config.py)
# --------------------------------------------------------------------------

LOG_LEVEL = config("LOG_LEVEL", default="INFO")
LOG_JSON = config("LOG_JSON", default=False, cast=bool)

# --------------------------------------------------------------------------
# Job queue (ADR-007)
# --------------------------------------------------------------------------

# Four jobs are load-bearing, and each fails SILENTLY if the worker stops:
# tenancy auto-confirmation, dispute auto-resolution, verification-document
# retention, and image variants. docs/OPERATIONS.md states what each failure
# looks like and what to alert on -- always the age of the oldest unresolved
# item, never the queue depth.
RQ_QUEUES = {
    "default": {"URL": REDIS_URL, "DEFAULT_TIMEOUT": 360},
    "media": {"URL": REDIS_URL, "DEFAULT_TIMEOUT": 900},
}
RQ_SHOW_ADMIN_LINK = True

# --------------------------------------------------------------------------
# Tenancy and reviews (ADR-004)
# --------------------------------------------------------------------------

# How long a landlord or caretaker has to confirm or dispute a claim before
# silence auto-confirms it. Landlord silence is a signal, not a veto.
TENANCY_CONFIRMATION_WINDOW_DAYS = config("TENANCY_CONFIRMATION_WINDOW_DAYS", default=7, cast=int)

# How long WE have to resolve an escalated dispute before it auto-resolves in
# the tenant's favour. This deadline binds the platform: an indefinite block
# would turn our backlog into a landlord veto by proxy.
DISPUTE_RESOLUTION_WINDOW_DAYS = config("DISPUTE_RESOLUTION_WINDOW_DAYS", default=14, cast=int)

# Minimum stay before a tenancy can be reviewed. Policy, not an invariant, so
# it lives here rather than in a constraint -- and it cannot be one anyway,
# because an ongoing stay is measured against today.
REVIEW_MINIMUM_STAY_DAYS = config("REVIEW_MINIMUM_STAY_DAYS", default=30, cast=int)

# How long a review stays editable before it freezes.
REVIEW_EDIT_WINDOW_DAYS = config("REVIEW_EDIT_WINDOW_DAYS", default=14, cast=int)

# Per-landlord cap on disputes raised in a rolling 30 days.
MAX_DISPUTES_PER_LANDLORD_PER_MONTH = config(
    "MAX_DISPUTES_PER_LANDLORD_PER_MONTH", default=20, cast=int
)

# Per-claimant cap on claims raised in a rolling 30 days.
MAX_CLAIMS_PER_USER_PER_MONTH = config("MAX_CLAIMS_PER_USER_PER_MONTH", default=10, cast=int)

# --------------------------------------------------------------------------
# Student verification (ADR-003)
# --------------------------------------------------------------------------

# How long an emailed verification link stays usable. Short: the student is
# looking at their inbox when it arrives.
EMAIL_VERIFICATION_TOKEN_HOURS = config("EMAIL_VERIFICATION_TOKEN_HOURS", default=24, cast=int)

# Rate limits are per user AND per address, independently. Per user alone lets
# one attacker mail-bomb many addresses; per address alone lets one account
# grind through a university's namespace.
EMAIL_VERIFICATION_RATE_WINDOW_HOURS = config(
    "EMAIL_VERIFICATION_RATE_WINDOW_HOURS", default=24, cast=int
)
EMAIL_VERIFICATION_MAX_PER_USER = config("EMAIL_VERIFICATION_MAX_PER_USER", default=5, cast=int)
EMAIL_VERIFICATION_MAX_PER_ADDRESS = config(
    "EMAIL_VERIFICATION_MAX_PER_ADDRESS", default=3, cast=int
)

# The dispute annotation on a review is DERIVED, not stored (ADR-004 3a), so
# these are policy knobs rather than a migration over live reviews.
#
# OFF by default. Suppressing the annotation for a landlord whose disputes are
# rarely upheld is defensible, but it is also a judgement the platform makes
# about a named person, and it should be switched on deliberately.
REVIEW_ANNOTATION_RESPECTS_DISPUTE_RECORD = config(
    "REVIEW_ANNOTATION_RESPECTS_DISPUTE_RECORD", default=False, cast=bool
)

# A rate over a small sample says nothing, so both guards must pass.
REVIEW_ANNOTATION_MINIMUM_DISPUTE_SAMPLE = config(
    "REVIEW_ANNOTATION_MINIMUM_DISPUTE_SAMPLE", default=10, cast=int
)
REVIEW_ANNOTATION_MINIMUM_UPHELD_RATE = config(
    "REVIEW_ANNOTATION_MINIMUM_UPHELD_RATE", default=0.2, cast=float
)

# --------------------------------------------------------------------------
# Routing (ADR-002)
# --------------------------------------------------------------------------

# Walking distance and time come from here, never from the straight line.
# The null provider is the default: an unconfigured deployment leaves the
# walking fields null, which renders as an em dash, rather than inventing a
# number.
ROUTE_PROVIDER = config(
    "ROUTE_PROVIDER",
    default="properties.routing.openrouteservice.NullRouteProvider",
)
OPENROUTESERVICE_API_KEY = config("OPENROUTESERVICE_API_KEY", default="")

"""
Smoke tests.

These prove the plumbing works: the project boots, the settings modules are
coherent, migrations apply cleanly to an empty database, the OpenAPI schema
generates, and the health probes behave. They assert nothing about domain
behaviour -- that arrives with the schema rewrite.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import yaml
from django.apps import apps
from django.core.management import call_command
from django.urls import reverse
from rest_framework import status

pytestmark = pytest.mark.smoke

# backend/ -- the directory that must be importable for `config` to resolve.
settings_base_dir = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# The project boots
# ---------------------------------------------------------------------------


def test_django_checks_pass() -> None:
    """``manage.py check --deploy`` style system checks raise nothing."""
    call_command("check", verbosity=0)


def test_all_local_apps_are_installed() -> None:
    installed = {config.name for config in apps.get_app_configs()}
    assert {"accounts", "rentals", "reviews"} <= installed


def test_every_app_declares_an_explicit_appconfig() -> None:
    """Each local app ships its own AppConfig rather than the Django default."""
    for label in ("accounts", "rentals", "reviews"):
        config = apps.get_app_config(label)
        assert type(config).__module__ == f"{label}.apps", (
            f"{label} is using Django's default AppConfig"
        )
        assert config.default_auto_field == "django.db.models.BigAutoField"


def test_token_blacklist_app_is_installed() -> None:
    """Logout blacklists refresh tokens, which needs this app present."""
    assert apps.is_installed("rest_framework_simplejwt.token_blacklist")


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_migrations_apply_to_an_empty_database(django_db_setup) -> None:
    """The test database exists, which means every migration applied."""
    from django.db import connection

    tables = set(connection.introspection.table_names())
    assert "accounts_user" in tables
    assert "rentals_rental" in tables
    assert "reviews_review" in tables


@pytest.mark.django_db
def test_no_model_changes_are_missing_a_migration() -> None:
    """``makemigrations --check`` finds nothing, i.e. models and migrations agree."""
    out = io.StringIO()
    try:
        call_command("makemigrations", "--check", "--dry-run", stdout=out, verbosity=1)
    except SystemExit as exc:  # pragma: no cover - only on drift
        pytest.fail(f"Model changes without a migration:\n{out.getvalue()}\n{exc}")


# ---------------------------------------------------------------------------
# OpenAPI schema
# ---------------------------------------------------------------------------


def test_openapi_schema_generates() -> None:
    """drf-spectacular can build the schema without erroring."""
    out = io.StringIO()
    # Deliberately not --fail-on-warn. The draft views and serializers produce
    # ~75 spectacular warnings and 16 "unable to guess serializer" errors; they
    # are catalogued in docs/AUDIT.md and are the schema rewrite's problem.
    # This test only guards the thing that would break the frontend today:
    # that a schema is produced at all.
    call_command("spectacular", stdout=out)
    schema = yaml.safe_load(out.getvalue())

    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "CampusRentalFinder API"
    assert schema["paths"], "schema generated no paths"
    # A few paths the frontend depends on must be present.
    assert "/api/v1/auth/login/" in schema["paths"]
    assert "/api/v1/rentals/properties/" in schema["paths"]


@pytest.mark.django_db
def test_schema_endpoint_is_served(api_client) -> None:
    response = api_client.get(reverse("schema"))
    assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Health probes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_liveness_probe_needs_no_dependencies(api_client) -> None:
    response = api_client.get(reverse("health-live"))
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_probe_reports_ok_when_dependencies_answer(api_client) -> None:
    with mock.patch("config.health._check_redis", return_value=(True, None)):
        response = api_client.get(reverse("health-ready"))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["redis"]["ok"] is True


@pytest.mark.django_db
def test_readiness_probe_returns_503_when_redis_is_down(api_client) -> None:
    with mock.patch("config.health._check_redis", return_value=(False, "connection refused")):
        response = api_client.get(reverse("health-ready"))

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["redis"]["ok"] is False


@pytest.mark.django_db
def test_readiness_probe_returns_503_when_the_database_is_down(api_client) -> None:
    with (
        mock.patch("config.health._check_database", return_value=(False, "could not connect")),
        mock.patch("config.health._check_redis", return_value=(True, None)),
    ):
        response = api_client.get(reverse("health-ready"))

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["checks"]["database"]["ok"] is False


# ---------------------------------------------------------------------------
# Settings hardening
# ---------------------------------------------------------------------------


def test_cors_allow_all_origins_is_never_enabled(settings) -> None:
    assert getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False) is False


def test_debug_is_off_under_test_settings(settings) -> None:
    assert settings.DEBUG is False


def _load_prod_settings(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Import config.settings.prod in a clean subprocess with the given env.

    A subprocess is used deliberately: settings modules are imported once per
    process, so reloading in-process would corrupt the running test session.
    """
    child_env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        # decouple reads a .env file from the working directory upward; point
        # it somewhere empty so the developer's local .env cannot mask the test.
        "PYTHONPATH": str(settings_base_dir),
        **env,
    }
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, test-controlled env
        [sys.executable, "-c", "import django; django.setup()"],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
        env=child_env,
        check=False,
    )


@pytest.mark.parametrize(
    ("secret_key", "expected"),
    [
        pytest.param("", "SECRET_KEY", id="missing"),
        pytest.param("django-insecure-placeholder", "placeholder", id="django-placeholder"),
    ],
)
def test_production_settings_refuse_an_unsafe_secret_key(secret_key: str, expected: str) -> None:
    """prod.py fails loudly rather than falling back to an insecure key."""
    result = _load_prod_settings({"SECRET_KEY": secret_key})

    assert result.returncode != 0, "prod settings loaded with an unsafe SECRET_KEY"
    assert "ImproperlyConfigured" in result.stderr
    assert expected in result.stderr


def test_production_settings_require_allowed_hosts() -> None:
    result = _load_prod_settings(
        {"SECRET_KEY": "a-real-looking-production-secret-key-value", "ALLOWED_HOSTS": ""}
    )

    assert result.returncode != 0
    assert "ALLOWED_HOSTS" in result.stderr


def test_production_settings_require_explicit_cors_origins() -> None:
    result = _load_prod_settings(
        {
            "SECRET_KEY": "a-real-looking-production-secret-key-value",
            "ALLOWED_HOSTS": "api.example.co.ke",
            "CORS_ALLOWED_ORIGINS": "",
        }
    )

    assert result.returncode != 0
    assert "CORS_ALLOWED_ORIGINS" in result.stderr


def test_production_settings_load_when_fully_configured() -> None:
    """The positive case, so the tests above cannot pass for the wrong reason."""
    result = _load_prod_settings(
        {
            "SECRET_KEY": "a-real-looking-production-secret-key-value",
            "ALLOWED_HOSTS": "api.example.co.ke",
            "CORS_ALLOWED_ORIGINS": "https://kyu.example.co.ke",
        }
    )

    assert result.returncode == 0, result.stderr

"""
Liveness and readiness probes.

``/health/live/``  -- the process is up and can serve a request. Never touches
                      a dependency, so an orchestrator will not restart the
                      container merely because Postgres blinked.
``/health/ready/`` -- the process can do useful work: database and Redis both
                      answer. Returns 503 when either is down so a load
                      balancer takes the instance out of rotation.
"""

from __future__ import annotations

from typing import Any

import redis
import structlog
from django.conf import settings
from django.db import connections
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from config.api.throttling import Scope
from config.api.throttling import scope as throttle_scope

logger = structlog.get_logger("campusrental.health")


@extend_schema(
    summary="Liveness probe",
    description="Returns 200 whenever the process can serve a request.",
    responses={200: dict},
    tags=["Health"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
@never_cache
@throttle_scope(Scope.PUBLIC_READ)
def health_live(request: Request) -> Response:
    """Liveness: no dependency is checked, by design."""
    return Response({"status": "ok"})


def _check_database() -> tuple[bool, str | None]:
    try:
        connection = connections["default"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return False, str(exc)
    return True, None


def _check_redis() -> tuple[bool, str | None]:
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        client.close()
    except Exception as exc:
        return False, str(exc)
    return True, None


@extend_schema(
    summary="Readiness probe",
    description=("Returns 200 when the database and Redis are both reachable, 503 otherwise."),
    responses={200: dict, 503: dict},
    tags=["Health"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
@never_cache
@throttle_scope(Scope.PUBLIC_READ)
def health_ready(request: Request) -> Response:
    """Readiness: the instance should only take traffic when this passes."""
    db_ok, db_error = _check_database()
    redis_ok, redis_error = _check_redis()

    checks: dict[str, Any] = {
        "database": {"ok": db_ok},
        "redis": {"ok": redis_ok},
    }
    # Error strings can carry host names and ports. They are useful to an
    # operator and are not secrets, but they stay out of the response unless
    # the check actually failed.
    if db_error:
        checks["database"]["error"] = db_error
    if redis_error:
        checks["redis"]["error"] = redis_error

    ready = db_ok and redis_ok
    if not ready:
        logger.warning("readiness_check_failed", checks=checks)

    return Response(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def health_live_plain(request: HttpRequest) -> JsonResponse:
    """Dependency-free liveness view for use before DRF is importable."""
    return JsonResponse({"status": "ok"})

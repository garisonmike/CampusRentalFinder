"""OpenRouteService walking directions (ADR-002)."""

from __future__ import annotations

import structlog
from django.conf import settings

from .base import RouteResult

logger = structlog.get_logger("campusrental.routing")


class OpenRouteServiceProvider:
    """Foot-walking directions from OpenRouteService.

    The free tier covers the expected volume. Quota exhaustion returns None
    rather than raising: an absent walking time is a supported state, and a
    provider outage must not block a property from being listed.
    """

    name = "openrouteservice"
    endpoint = "https://api.openrouteservice.org/v2/directions/foot-walking"

    def __init__(self, api_key: str | None = None, timeout: float = 10.0) -> None:
        self.api_key = api_key if api_key is not None else settings.OPENROUTESERVICE_API_KEY
        self.timeout = timeout

    def route(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> RouteResult | None:
        if not self.api_key:
            logger.info("routing_skipped", reason="no_api_key", provider=self.name)
            return None

        import requests

        # OpenRouteService takes longitude first.
        payload = {
            "coordinates": [
                [origin[1], origin[0]],
                [destination[1], destination[0]],
            ]
        }

        try:
            response = requests.post(
                self.endpoint,
                json=payload,
                headers={"Authorization": self.api_key},
                timeout=self.timeout,
            )
            response.raise_for_status()
            summary = response.json()["routes"][0]["summary"]
        except Exception as exc:
            # Never a fallback estimate. ADR-002: a fabricated walking time
            # erodes exactly the trust the platform is selling.
            logger.warning("routing_failed", provider=self.name, error=str(exc))
            return None

        return RouteResult(
            distance_km=summary["distance"] / 1000.0,
            duration_minutes=max(1, round(summary["duration"] / 60.0)),
            provider=self.name,
        )


class NullRouteProvider:
    """A provider that never routes.

    The default. Walking figures stay null until a real provider is configured,
    which is the honest state rather than a placeholder.
    """

    name = "null"

    def route(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> RouteResult | None:
        return None

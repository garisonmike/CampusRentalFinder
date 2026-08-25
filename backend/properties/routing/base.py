"""
Routing provider interface (ADR-002).

Walking distance and time come from a routing service, never from the straight
line. Swapping provider is a settings change and one new class, so the choice
of OpenRouteService is not baked into the model layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RouteResult:
    """A routed walking leg."""

    distance_km: float
    duration_minutes: int
    provider: str


class RouteProvider(Protocol):
    """Anything that can route a walking leg between two points.

    Returning ``None`` is a supported outcome, not an error: no route exists,
    the quota is exhausted, or the service is down. The caller leaves the
    walking fields null, which renders as "—".

    **A provider must never fall back to a straight-line estimate.** That is the
    one thing ADR-002 forbids, and the interface is where it would be tempting.
    """

    name: str

    def route(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> RouteResult | None: ...

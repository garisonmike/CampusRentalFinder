"""Provider selection (ADR-002)."""

from __future__ import annotations

from django.conf import settings
from django.utils.module_loading import import_string

from .base import RouteProvider


def get_route_provider() -> RouteProvider:
    """The configured routing provider.

    A settings change and one new class, as ADR-002 requires. Defaults to the
    null provider, so an unconfigured deployment leaves walking figures null
    rather than inventing them.
    """
    return import_string(settings.ROUTE_PROVIDER)()

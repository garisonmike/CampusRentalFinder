"""
The project's router.

`DefaultRouter` generates an `APIRootView` that falls through to
`DEFAULT_PERMISSION_CLASSES` and carries no throttle scope, so it fails both
architecture walks -- correctly, since nobody chose its policy. Rather than
exempting it, the policy is chosen here once and every router inherits it.

The root view lists endpoint URLs and nothing else, so it is safe to leave
open; being open is still a decision, and this is where it is recorded.
"""

from __future__ import annotations

from rest_framework.permissions import AllowAny
from rest_framework.routers import APIRootView, DefaultRouter

from .throttling import Scope


class AnnotatedAPIRootView(APIRootView):
    """The router index, with its policy stated rather than inherited."""

    permission_classes = [AllowAny]
    throttle_scope = Scope.PUBLIC_READ


class Router(DefaultRouter):
    """Use this, not `DefaultRouter`, so the root view carries a policy."""

    APIRootView = AnnotatedAPIRootView

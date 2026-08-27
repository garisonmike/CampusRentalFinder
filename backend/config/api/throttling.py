"""
Throttle scopes, defined in one place.

Rates live in settings so they are tunable without a deploy of new code, and
the scope names live here so a view cannot invent one that no rate matches --
a `throttle_scope` with no configured rate throttles **nothing**, silently,
which is the failure mode this module exists to make impossible.

These sit **in addition to** the service-layer limits, not instead of them.
The service limits are the correctness boundary: they are what a management
command, the admin and a future job go through. These are the cheap boundary,
refusing floods before they reach a database query at all.
"""

from __future__ import annotations

from functools import cache

from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class Scope:
    """Every throttle scope the API uses. A view must name one of these."""

    #: Anonymous browsing. Generous: reading listings is the product.
    PUBLIC_READ = "public_read"
    #: Authenticated reads.
    AUTHENTICATED_READ = "authenticated_read"
    #: Ordinary writes.
    WRITE = "write"
    #: Unsolicited messages to strangers. Tightest of the write scopes.
    INQUIRY = "inquiry"
    #: Anything that mails a person or accepts an identity document.
    VERIFICATION = "verification"
    #: Assertions the platform has to test rather than witness.
    CLAIM = "claim"
    #: Login and refresh. Tight, and separate from `WRITE` so a credential
    #: stuffing attempt cannot be hidden inside ordinary write traffic.
    AUTH = "auth"
    #: Subject access and erasure. Rare by nature; a burst is a signal.
    PRIVACY = "privacy"


ALL_SCOPES = frozenset(value for name, value in vars(Scope).items() if not name.startswith("_"))


class ScopedThrottle(ScopedRateThrottle):
    """`ScopedRateThrottle` that refuses to be silently inert.

    DRF's own implementation returns `True` -- allow -- when a scope has no
    configured rate. A typo in `throttle_scope` therefore disables throttling
    on that view and looks exactly like it working.
    """

    def allow_request(self, request, view):
        scope = getattr(view, "throttle_scope", None)
        if scope is not None and scope not in ALL_SCOPES:
            raise RuntimeError(
                f"{type(view).__name__}.throttle_scope = {scope!r} is not a "
                f"scope in config.api.throttling.Scope. DRF would silently "
                f"apply no throttle at all."
            )
        return super().allow_request(request, view)


@cache
def throttle_class_for(name: str) -> type[SimpleRateThrottle]:
    """A throttle class bound to one scope.

    Needed because `@api_view` builds its class behind the scenes and copies
    only a fixed set of attributes across -- `throttle_classes` among them,
    `throttle_scope` **not**. Setting `throttle_scope` on a function view
    therefore looks right and throttles nothing, which is the same silent-pass
    shape as an unconfigured scope.

    So the scope travels inside a class instead, where the copying rule
    guarantees it survives.
    """
    if name not in ALL_SCOPES:
        raise RuntimeError(f"{name!r} is not a scope in config.api.throttling.Scope.")

    return type(
        f"{name.title().replace('_', '')}Throttle",
        (SimpleRateThrottle,),
        {
            "scope": name,
            "get_cache_key": lambda self, request, view: self.cache_format
            % {
                "scope": self.scope,
                "ident": self.get_ident(request)
                if not request.user.is_authenticated
                else request.user.pk,
            },
        },
    )


def scope(name: str):
    """Attach a throttle scope to a function-based view.

    Sets both: the class for DRF to honour, and the attribute for the
    architecture walk to read.
    """

    def decorate(func):
        func.throttle_scope = name
        func.throttle_classes = [throttle_class_for(name)]
        return func

    return decorate

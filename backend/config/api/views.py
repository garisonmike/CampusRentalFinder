"""
Shared view plumbing.

One mixin, for one problem that bites every tenant-scoped list endpoint the
same way.
"""

from __future__ import annotations

from typing import Any, ClassVar


class SchemaSafeQuerysetMixin:
    """Return an empty queryset when drf-spectacular is introspecting.

    The schema generator instantiates each view and calls ``get_queryset()``
    **with no request** to work out the response shape. Every list endpoint
    here starts by resolving the tenant from the request host, so under
    introspection they raise ``NotFound`` or, worse, ``TenantScopeError`` from
    the scoped manager -- and schema generation fails for the whole API because
    one view was written normally.

    drf-spectacular sets ``swagger_fake_view`` on the instance for exactly this
    case. Subclasses override ``empty_queryset()`` to say which model, so the
    generated schema still names the right serializer.

    The alternative -- making `get_queryset` tolerate a missing tenant -- is
    worse: it would turn a real misconfiguration in production into an empty
    page rather than an error.
    """

    #: Set by the view. `Model.all_objects` rather than `objects`, because a
    #: scoped manager raises before `.none()` can be reached.
    schema_queryset: ClassVar[Any] = None

    def is_schema_generation(self) -> bool:
        """Whether this view is being introspected rather than serving.

        Two signals, because one is not enough. drf-spectacular sets
        ``swagger_fake_view`` on the instance -- but the django-filter
        integration reaches `get_queryset()` on a path where it has not been
        set yet, so a view relying on that alone still raises.

        The second signal is the honest one: a view with **no request** is by
        definition not serving a user. Nothing in a request cycle can reach
        here without one.
        """
        if getattr(self, "swagger_fake_view", False):
            return True
        return getattr(self, "request", None) is None

    def empty_queryset(self):
        if self.schema_queryset is None:  # pragma: no cover - defensive
            raise NotImplementedError(
                f"{type(self).__name__} needs `schema_queryset` so the OpenAPI "
                f"generator can determine its response shape."
            )
        return self.schema_queryset.none()

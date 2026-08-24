"""
Tenant scoping (ADR-001).

Multi-tenancy is shared-database, shared-schema, so isolation is enforced by
application code rather than by the database. ADR-001 names that as the central
risk of the approach: **one missing filter is a cross-tenant leak.**

The mitigation is that forgetting has to be impossible rather than merely
discouraged. A tenant-scoped model's ``objects`` manager **refuses** to produce
an unfiltered queryset — ``Property.objects.all()`` raises instead of quietly
returning every tenant's rows. Callers must say which they mean:

    Property.objects.for_tenant(request.university)   # the normal path
    Property.objects.across_tenants()                 # deliberate, greppable

Django itself needs unfiltered access — the admin, related-object descriptors,
``dumpdata``, migrations — so scoped models also declare a plain
``all_objects`` manager and point ``Meta.base_manager_name`` and
``Meta.default_manager_name`` at it. Framework internals use that; application
code uses ``objects`` and gets the guard rail.
"""

from __future__ import annotations

from typing import Any

from django.db import models

#: A tenant, however the caller has it to hand.
#:
#: Deliberately not typed as ``University``: this module is generic
#: infrastructure and importing the concrete tenant model would couple config
#: to an app, and create an import cycle the moment that app imports from here.
type Tenant = models.Model | int


class TenantScopeError(RuntimeError):
    """Raised when tenant-scoped data is queried without naming a tenant.

    Deliberately loud. The alternative — returning every tenant's rows — is a
    data leak that looks like a working feature.
    """


class TenantScopedQuerySet(models.QuerySet):
    """A queryset that knows how to narrow itself to one university."""

    #: Marker read by tests/test_architecture.py. A plain QuerySet lacks it, so
    #: a view that sidesteps the scoped manager is detectable.
    _is_tenant_scoped = True

    def for_tenant(self, university: Tenant | None) -> TenantScopedQuerySet:
        """Narrow to a single university."""
        if university is None:
            raise TenantScopeError(
                f"{self.model.__name__}.for_tenant() needs a university. "
                "An unresolved tenant must produce an error, not every tenant's rows."
            )
        lookup = self.model.tenant_lookup
        return self.filter(**{lookup: university})

    def across_tenants(self) -> TenantScopedQuerySet:
        """Every tenant's rows.

        Named so that ``git grep across_tenants`` finds every place the tenant
        boundary is deliberately crossed. Platform staff tooling and scheduled
        jobs are the legitimate callers.
        """
        return self


class TenantScopedManager(models.Manager.from_queryset(TenantScopedQuerySet)):  # type: ignore[misc]
    """Manager whose unqualified queryset raises.

    ``get_queryset`` is what ``.all()``, ``.filter()``, ``.get()`` and every
    other implicit entry point call, so overriding it here closes all of them at
    once.
    """

    #: Lets a subclass opt out for a model that is scoped but has a legitimate
    #: unfiltered default (none today).
    strict = True

    def get_queryset(self) -> TenantScopedQuerySet:
        if self.strict:
            raise TenantScopeError(
                f"{self.model.__name__}.objects is tenant-scoped: call "
                f"{self.model.__name__}.objects.for_tenant(university) or, if you "
                f"genuinely mean every tenant, "
                f"{self.model.__name__}.objects.across_tenants()."
            )
        return self._unscoped()  # pragma: no cover - no non-strict model today

    def _unscoped(self) -> TenantScopedQuerySet:
        return TenantScopedQuerySet(self.model, using=self._db, hints=self._hints)

    # The two explicit entry points bypass the guard by construction.
    def for_tenant(self, university: Tenant | None) -> TenantScopedQuerySet:
        return self._unscoped().for_tenant(university)

    def across_tenants(self) -> TenantScopedQuerySet:
        return self._unscoped().across_tenants()


class TenantModelConfigurationError(TypeError):
    """A tenant-scoped model that does not say how to reach its tenant."""


class TenantScopedModel(models.Model):
    """Base class for anything carrying tenant data.

    Subclasses set ``tenant_lookup`` to the ORM path from this model to its
    ``University``. It is a path rather than a field name because most models
    reach the tenant indirectly — ``Unit`` through ``property``, ``Review``
    through ``tenancy__unit__property`` (docs/DOMAIN_MODEL.md).
    """

    #: ORM lookup path from this model to its University. Required.
    tenant_lookup: str = ""

    # ruff reads `objects = TenantScopedManager()` as a field, because the
    # right-hand side is not a recognised Manager call, and then wants the real
    # manager below it moved above it. The order here is the Django style
    # guide's own: attributes, managers, Meta.
    objects = TenantScopedManager()
    all_objects = models.Manager()  # noqa: DJ012 - see the note above

    class Meta:
        abstract = True
        # Django internals (admin, related descriptors, dumpdata, migrations)
        # need an unfiltered manager; application code uses `objects`.
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "_meta", None) or cls._meta.abstract:
            return
        if not cls.tenant_lookup:
            raise TenantModelConfigurationError(
                f"{cls.__name__} inherits TenantScopedModel but sets no "
                f"tenant_lookup. Name the ORM path to its University."
            )


def is_tenant_scoped(model: type[models.Model]) -> bool:
    """Whether ``model`` carries tenant data and is guarded.

    Read by ``tests/test_architecture.py``: any local model that is neither
    scoped nor explicitly exempt fails the build.
    """
    manager = model.__dict__.get("objects") or getattr(model, "objects", None)
    return isinstance(manager, TenantScopedManager)


def tenant_lookup_for(model: type[models.Model]) -> str:
    """The ORM path from ``model`` to its University."""
    lookup = getattr(model, "tenant_lookup", "")
    if not lookup:
        raise TenantScopeError(f"{model.__name__} declares no tenant_lookup.")
    return lookup

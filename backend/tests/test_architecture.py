"""
Architecture tests.

These enforce structural rules that would otherwise live only in an ADR and in
developers' heads. A rule in a document erodes; a rule in a test does not.

Each one fails on *addition* rather than on misuse: adding an unclassified
route, or a model that is neither tenant-scoped nor explicitly exempt, fails the
build and forces the decision at the point it is being made.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest
from django.apps import apps
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

from config.hosts import (
    NAMESPACE_HOST_CLASSES,
    ROUTE_HOST_CLASSES,
    HostClass,
    classify,
)

pytestmark = pytest.mark.architecture

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

SAFE_METHODS = frozenset({"get", "head", "options"})


# ---------------------------------------------------------------------------
# Route discovery
# ---------------------------------------------------------------------------


class Route:
    """One resolved URL pattern, with everything the rules need."""

    def __init__(self, pattern: str, name: str | None, namespace: str, callback) -> None:
        self.pattern = pattern
        self.name = name
        self.namespace = namespace
        self.callback = callback

    @property
    def qualified_name(self) -> str | None:
        if self.name is None:
            return None
        return f"{self.namespace}{self.name}" if self.namespace else self.name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Route {self.qualified_name or '(unnamed)'} /{self.pattern}>"


def _walk(resolver, prefix: str = "", namespace: str = ""):
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            child_namespace = f"{namespace}{entry.namespace}:" if entry.namespace else namespace
            yield from _walk(entry, prefix + str(entry.pattern), child_namespace)
        elif isinstance(entry, URLPattern):
            yield Route(
                pattern=prefix + str(entry.pattern),
                name=entry.name,
                namespace=namespace,
                callback=entry.callback,
            )


def all_routes() -> list[Route]:
    return list(_walk(get_resolver()))


def allowed_methods(callback) -> set[str]:
    """The HTTP methods a view actually serves, lower-cased."""
    view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)

    # DRF @action / @api_view record the methods on the function itself.
    bindings = getattr(callback, "actions", None)
    if bindings:
        return {method.lower() for method in bindings}

    if view_class is not None:
        declared = getattr(view_class, "http_method_names", None)
        handlers = {
            method
            for method in ("get", "post", "put", "patch", "delete", "head", "options")
            if hasattr(view_class, method)
        }
        if declared is not None:
            handlers &= {method.lower() for method in declared}
        return handlers

    return set()


# ---------------------------------------------------------------------------
# 1. Every route carries an explicit host class
# ---------------------------------------------------------------------------


def test_every_route_is_classified() -> None:
    """An unclassified route fails. Adding a route must force the decision.

    ADR-001 puts public reads on a tenant-neutral canonical host and everything
    else on a tenant subdomain. Which one a route belongs to is a security
    question for write endpoints, so it is answered explicitly, once, per route.
    """
    unclassified = [
        route
        for route in all_routes()
        if classify(route.qualified_name, namespace=route.namespace) is None
    ]

    assert not unclassified, (
        "These routes have no host class:\n"
        + "\n".join(
            f"  {route.qualified_name or '(unnamed)'}  /{route.pattern}" for route in unclassified
        )
        + "\n\nAdd each to config.hosts.ROUTE_HOST_CLASSES. Choose:\n"
        "  PUBLIC_CANONICAL — tenant-neutral, publicly cacheable, READ-ONLY\n"
        "  TENANT_SCOPED    — every write, every authenticated read\n"
        "  API_INTERNAL     — admin, schema, probes"
    )


def test_classification_registry_has_no_dead_entries() -> None:
    """A registry entry for a route that no longer exists is stale."""
    live = {route.qualified_name for route in all_routes() if route.qualified_name}
    dead = sorted(set(ROUTE_HOST_CLASSES) - live)

    assert not dead, (
        "config.hosts.ROUTE_HOST_CLASSES classifies routes that do not exist: "
        f"{dead}. Remove them, or the registry stops describing reality."
    )


def test_namespace_classifications_are_only_for_third_party_trees() -> None:
    """Our own routes are classified individually, never by namespace.

    A namespace rule would let a new route in an existing app inherit a
    classification silently, which defeats the forcing function.
    """
    ours = {"accounts", "rentals", "reviews", "universities"}
    overreach = ours & set(NAMESPACE_HOST_CLASSES)

    assert not overreach, (
        f"{sorted(overreach)} are our namespaces and must be classified per "
        "route in ROUTE_HOST_CLASSES, not wholesale."
    )


# ---------------------------------------------------------------------------
# 2. No write endpoint is reachable on the public canonical host
# ---------------------------------------------------------------------------


def test_public_canonical_routes_are_read_only() -> None:
    """The neutral host serves published public content and nothing else.

    This is the rule with security weight. The canonical host is tenant-neutral
    by construction, so a write reaching it would be a write with no tenant —
    and a DRF router route cannot satisfy it at all, because one URL name covers
    both the safe and the unsafe methods.
    """
    offenders: list[tuple[str, set[str]]] = []

    for route in all_routes():
        if (
            classify(route.qualified_name, namespace=route.namespace)
            is not HostClass.PUBLIC_CANONICAL
        ):
            continue
        unsafe = allowed_methods(route.callback) - SAFE_METHODS
        if unsafe:
            offenders.append((route.qualified_name or route.pattern, unsafe))

    assert not offenders, (
        "These routes are classified PUBLIC_CANONICAL but serve unsafe methods:\n"
        + "\n".join(f"  {name}: {sorted(methods)}" for name, methods in offenders)
        + "\n\nEither reclassify as TENANT_SCOPED, or split the read-only part "
        "into its own route. A ModelViewSet route cannot be public canonical."
    )


def test_public_canonical_set_is_not_empty() -> None:
    """Guards against the previous test passing vacuously.

    A registry with zero PUBLIC_CANONICAL routes would satisfy the read-only
    rule trivially while ADR-001's canonical host quietly ceased to exist.
    """
    public = [name for name, cls in ROUTE_HOST_CLASSES.items() if cls is HostClass.PUBLIC_CANONICAL]
    assert public, "No route is PUBLIC_CANONICAL; ADR-001's neutral host has no content."


# ---------------------------------------------------------------------------
# 3. Absolute URLs come from exactly one helper
# ---------------------------------------------------------------------------

#: Modules permitted to construct a scheme and host anywhere in the file.
#:
#: Deliberately tiny, and only for modules whose whole purpose is host
#: configuration. Everything else uses the per-line marker below, so an
#: exception is visible at the line it applies to rather than blanketing a file
#: that will later grow new code.
ABSOLUTE_URL_ALLOWLIST: dict[str, str] = {
    "config/hosts.py": "The single builder. This is the helper the rule protects.",
    "config/settings/base.py": "Declares SITE_DOMAIN and the host prefixes.",
    "config/settings/dev.py": "Local CORS and CSRF origin defaults.",
    "config/settings/prod.py": "Validates deployment host configuration.",
    "tests/test_architecture.py": "Contains the patterns it searches for.",
}

#: Per-line escape hatch: ``# absolute-url-ok: <reason>``.
#:
#: A reason is mandatory and is asserted to be non-empty, so the exemption
#: carries its own justification to whoever reads the line next.
_LINE_EXEMPTION = re.compile(r"#\s*absolute-url-ok:\s*(?P<reason>.+?)\s*$")

_BUILD_ABSOLUTE_URI = re.compile(r"\bbuild_absolute_uri\b")

#: An f-string or concatenation that pastes a scheme onto a host expression.
_SCHEME_CONCAT = re.compile(
    r"""["']https?://["']\s*\+          # "http://" + something
      | ["']https?://\{                 # f"http://{host}"
      | ["']https?://%s                 # "http://%s" % host
    """,
    re.VERBOSE,
)


def _exempt_line_numbers(lines: list[str]) -> set[int]:
    """Line numbers covered by an ``absolute-url-ok`` marker.

    A marker exempts the line it sits on, and — since these lines are usually
    long enough that the justification belongs above them — the next line that
    is not itself a comment.
    """
    exempt: set[int] = set()

    for index, line in enumerate(lines):
        match = _LINE_EXEMPTION.search(line)
        if not match or not match.group("reason"):
            continue

        exempt.add(index + 1)

        for offset in range(index + 1, len(lines)):
            stripped = lines[offset].strip()
            if not stripped or stripped.startswith("#"):
                continue
            exempt.add(offset + 1)
            break

    return exempt


def python_sources() -> list[Path]:
    return [
        path
        for path in BACKEND_ROOT.rglob("*.py")
        if "migrations" not in path.parts
        and ".venv" not in path.parts
        and "__pycache__" not in path.parts
    ]


def test_absolute_urls_are_built_only_by_the_host_helper() -> None:
    """``request.build_absolute_uri`` echoes the Host header it was given.

    On a tenant subdomain it emits a tenant URL for something that should be
    canonical; behind a proxy it emits whatever an attacker put in the header.
    ADR-001 requires one helper, so the host decision happens once.
    """
    offenders: list[str] = []

    for path in python_sources():
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative in ABSOLUTE_URL_ALLOWLIST:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        exempt = _exempt_line_numbers(lines)

        for line_number, line in enumerate(lines, start=1):
            if line_number in exempt:
                continue
            if _BUILD_ABSOLUTE_URI.search(line):
                offenders.append(f"{relative}:{line_number}  build_absolute_uri")
            elif _SCHEME_CONCAT.search(line):
                offenders.append(f"{relative}:{line_number}  scheme/host concatenation")

    assert not offenders, (
        "Absolute URLs must come from config.hosts.build_absolute_url:\n"
        + "\n".join(f"  {entry}" for entry in offenders)
        + "\n\nIf a case genuinely cannot use the helper, add the module to "
        "ABSOLUTE_URL_ALLOWLIST with a reason."
    )


def test_absolute_url_allowlist_entries_all_exist() -> None:
    """A stale allowlist entry silently exempts nothing, or the wrong thing."""
    missing = [entry for entry in ABSOLUTE_URL_ALLOWLIST if not (BACKEND_ROOT / entry).exists()]
    assert not missing, f"Allowlisted paths that no longer exist: {missing}"


def test_absolute_url_allowlist_entries_all_have_a_reason() -> None:
    for entry, reason in ABSOLUTE_URL_ALLOWLIST.items():
        assert reason.strip(), f"{entry} is allowlisted without a reason."


def test_every_line_exemption_carries_a_reason() -> None:
    """``# absolute-url-ok`` without a reason is not an exemption."""
    bare = re.compile(r"#\s*absolute-url-ok\s*(?::\s*)?$")
    offenders: list[str] = []

    for path in python_sources():
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative == "tests/test_architecture.py":
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if bare.search(line):
                offenders.append(f"{relative}:{line_number}")

    assert not offenders, "These lines are exempted without a reason:\n" + "\n".join(
        f"  {entry}" for entry in offenders
    )


# ---------------------------------------------------------------------------
# 4. Tenant data is reachable only through the scoped manager
# ---------------------------------------------------------------------------

#: Models that legitimately hold no tenant-scoped data.
#:
#: Every entry needs a reason, and the list is expected to *shrink* as the
#: schema rewrite lands. A new model must be either tenant-scoped or listed
#: here — inheriting neither is what this test prevents.
NOT_TENANT_SCOPED: dict[str, str] = {
    # The tenant itself. University does not belong to a scope, it *is* the
    # scope, so a scoped manager on it would be circular.
    "universities.University": "The tenant model. It is the scope, not scoped data.",
    # Identity is deliberately unscoped: a landlord may serve several
    # universities, so User cannot belong to one (docs/DOMAIN_MODEL.md).
    "accounts.User": "Identity, not tenant data. A landlord may serve several universities.",
    # A landlord near two campuses serves both universities, which is the whole
    # reason ADR-002 exists. Scoping the profile would contradict it.
    "accounts.LandlordProfile": "A landlord may own property serving several universities.",
    # A landlord's record spans universities, for the same reason their
    # profile does. Scoping it would report a different number to each
    # university, which is worse than reporting one number because both would
    # look authoritative (ADR-004).
    "ratings.LandlordRatingAggregate": (
        "A landlord's reputation spans universities; a per-tenant figure would "
        "give two authoritative-looking answers to the same question."
    ),
    # Pre-rewrite draft models. These predate the tenant boundary entirely and
    # are removed by the schema rewrite; see docs/AUDIT.md.
    "rentals.Rental": "Pre-rewrite draft model, removed by the schema rewrite.",
    "rentals.RentalImage": "Pre-rewrite draft model.",
    "rentals.RentalFavorite": "Pre-rewrite draft model.",
    "rentals.RentalInquiry": "Pre-rewrite draft model.",
    "reviews.Review": "Pre-rewrite draft model.",
    "reviews.ReviewHelpfulness": "Pre-rewrite draft model.",
    "reviews.ReviewReport": "Pre-rewrite draft model.",
}


#: App labels whose models are ours to classify.
#:
#: Derived from settings rather than hard-coded: the hand-written list silently
#: stopped covering `properties` when that app was added, so the scoping walk
#: reported success on models it had never looked at.
def _local_app_labels() -> frozenset[str]:
    from django.conf import settings

    return frozenset(app.rsplit(".", 1)[-1] for app in settings.LOCAL_APPS)


LOCAL_APP_LABELS = _local_app_labels()


def local_models():
    return [model for model in apps.get_models() if model._meta.app_label in LOCAL_APP_LABELS]


def test_every_local_model_is_scoped_or_explicitly_exempt() -> None:
    """Adding a model must force a decision about tenant scoping.

    ADR-001 accepts that isolation is enforced by application code, which makes
    a single unscoped queryset a cross-tenant leak. The mitigation is that
    forgetting is impossible rather than merely discouraged.
    """
    from config.tenancy import (
        is_tenant_scoped,
    )

    undecided = [
        f"{model._meta.app_label}.{model.__name__}"
        for model in local_models()
        if not is_tenant_scoped(model)
        and f"{model._meta.app_label}.{model.__name__}" not in NOT_TENANT_SCOPED
    ]

    assert not undecided, (
        "These models are neither tenant-scoped nor exempt:\n"
        + "\n".join(f"  {label}" for label in undecided)
        + "\n\nEither give the model a TenantScopedManager, or add it to "
        "NOT_TENANT_SCOPED in this file with a reason."
    )


def test_exemption_list_has_no_dead_entries() -> None:
    live = {f"{model._meta.app_label}.{model.__name__}" for model in local_models()}
    dead = sorted(set(NOT_TENANT_SCOPED) - live)
    assert not dead, f"NOT_TENANT_SCOPED names models that do not exist: {dead}"


def test_exemption_list_entries_all_have_a_reason() -> None:
    for label, reason in NOT_TENANT_SCOPED.items():
        assert reason.strip(), f"{label} is exempted without a reason."


def test_no_viewset_exposes_a_tenant_model_through_the_default_manager() -> None:
    """A tenant-scoped model reached via ``.objects`` is a cross-tenant leak.

    The scoped manager raises rather than returning unfiltered rows, so this
    test catches the case where a view sidesteps it by naming a different
    manager or building a raw queryset.
    """
    from config.tenancy import is_tenant_scoped

    offenders: list[str] = []

    for route in all_routes():
        view_class = getattr(route.callback, "cls", None)
        if view_class is None:
            continue
        queryset = getattr(view_class, "queryset", None)
        if queryset is None:
            continue
        model = queryset.model
        if not is_tenant_scoped(model):
            continue
        # A queryset from the scoped manager carries the marker; one built from
        # `all_objects` or a raw QuerySet does not.
        if not getattr(queryset, "_is_tenant_scoped", False):
            offenders.append(f"{view_class.__name__} -> {model.__name__}")

    assert not offenders, (
        "These viewsets expose tenant models through an unscoped queryset:\n"
        + "\n".join(f"  {entry}" for entry in sorted(set(offenders)))
    )


# ---------------------------------------------------------------------------
# 5. A `property` field and the @property decorator cannot coexist
# ---------------------------------------------------------------------------


def model_class_nodes() -> list[tuple[Path, ast.ClassDef]]:
    """Every class defined in a models.py under an app we own."""
    found: list[tuple[Path, ast.ClassDef]] = []

    for path in python_sources():
        if path.name != "models.py":
            continue
        if path.parent.name not in LOCAL_APP_LABELS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                found.append((path, node))

    return found


def _declares_property_field(node: ast.ClassDef) -> bool:
    """Whether the class body assigns a model field named ``property``."""
    for statement in node.body:
        targets = []
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]

        for target in targets:
            if isinstance(target, ast.Name) and target.id == "property":
                return True

    return False


def _uses_property_decorator(node: ast.ClassDef) -> list[str]:
    """Names of methods in the class body decorated with a bare ``@property``."""
    offenders = []

    for statement in node.body:
        if not isinstance(statement, ast.FunctionDef):
            continue
        for decorator in statement.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "property":
                offenders.append(statement.name)

    return offenders


def test_a_model_with_a_property_field_never_uses_the_property_decorator() -> None:
    """``property = ForeignKey(...)`` shadows the builtin inside the class body.

    ``@property`` further down then resolves to a ForeignKey instance and the
    module raises ``TypeError: 'ForeignKey' object is not callable`` at import
    — before any test runs, so the failure is a stack trace at collection
    rather than a readable assertion.

    ``Unit`` hit this first (see properties/models.py). Anything on such a model
    that would naturally be a property has to be a method instead.
    """
    offenders: list[str] = []

    for path, node in model_class_nodes():
        if not _declares_property_field(node):
            continue
        for method in _uses_property_decorator(node):
            offenders.append(
                f"{path.relative_to(BACKEND_ROOT).as_posix()}: {node.name}.{method} uses @property"
            )

    assert not offenders, (
        "These models declare a field named `property` and also use the "
        "@property decorator, which resolves to that field:\n"
        + "\n".join(f"  {entry}" for entry in offenders)
        + "\n\nMake it a method. See Unit.is_available in properties/models.py "
        "for the precedent and the comment explaining why."
    )


def test_the_shadowing_check_can_actually_see_a_violation() -> None:
    """Guards against the check passing because it parses nothing.

    A structural test that silently matches zero classes is worse than no test:
    it reports success for a rule it never applied.
    """
    source = """
class Thing:
    property = models.ForeignKey("x", on_delete=models.CASCADE)

    @property
    def broken(self):
        return True
"""
    node = next(entry for entry in ast.walk(ast.parse(source)) if isinstance(entry, ast.ClassDef))

    assert _declares_property_field(node) is True
    assert _uses_property_decorator(node) == ["broken"]


def test_the_shadowing_check_examines_real_models() -> None:
    """And that it is looking at our actual models, not an empty list."""
    names = {node.name for _path, node in model_class_nodes()}

    assert {"Property", "Unit", "User", "University"} <= names


# ---------------------------------------------------------------------------
# 6. The header fallback cannot exist in production
# ---------------------------------------------------------------------------


def test_prod_settings_reject_the_tenant_header_fallback() -> None:
    """ADR-001: absence is not enough, it must be impossible.

    Read as source rather than imported: prod.py raises on import by design, so
    the behaviour is exercised in a subprocess by test_smoke.py. Here we assert
    the guard exists at all, so it cannot be deleted quietly.
    """
    source = (BACKEND_ROOT / "config" / "settings" / "prod.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    raises_on_fallback = any(
        isinstance(node, ast.If)
        and any(isinstance(inner, ast.Raise) for inner in ast.walk(node))
        and "TENANT_HEADER_FALLBACK_ENABLED" in ast.dump(node)
        for node in ast.walk(tree)
    )

    assert raises_on_fallback, (
        "config/settings/prod.py must raise ImproperlyConfigured when "
        "TENANT_HEADER_FALLBACK_ENABLED is true. Without it, any client can "
        "select a tenant with a header."
    )


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------


class TestScheduledJobs:
    """Every job in SCHEDULE must be real and must be documented.

    These jobs fail silently. A schedule entry pointing at a renamed function
    would fail at *schedule install time* on a deploy machine and be noticed;
    an OPERATIONS.md entry for a job nobody schedules would never be noticed at
    all, because its symptom is indistinguishable from the feature working.
    """

    def test_every_scheduled_function_is_importable(self):
        from config.jobs.schedule import SCHEDULE

        for job in SCHEDULE:
            module_path, _, name = job.func.rpartition(".")
            module = importlib.import_module(module_path)

            assert hasattr(module, name), f"{job.func} does not exist"
            assert callable(getattr(module, name))

    def test_every_scheduled_job_says_what_its_failure_looks_like(self):
        """The on_failure text is for whoever is reading this at 2am."""
        from config.jobs.schedule import SCHEDULE

        for job in SCHEDULE:
            assert len(job.on_failure) > 40, f"{job.func} has no failure description"

    def test_every_scheduled_job_targets_a_configured_queue(self):
        from django.conf import settings

        from config.jobs.schedule import SCHEDULE

        for job in SCHEDULE:
            assert job.queue in settings.RQ_QUEUES

    def test_every_scheduled_job_appears_in_the_operations_runbook(self):
        """A job that runs but is not in OPERATIONS.md has no alert, and a job
        with no alert may as well not run: its failure is invisible."""
        from config.jobs.schedule import SCHEDULE

        runbook = (REPO_ROOT / "docs" / "OPERATIONS.md").read_text()

        for job in SCHEDULE:
            _, _, name = job.func.rpartition(".")
            subject = name.replace("sweep_overdue_", "").replace("_", " ")

            assert subject.split()[0] in runbook.lower(), (
                f"{job.func} is scheduled but not described in docs/OPERATIONS.md"
            )

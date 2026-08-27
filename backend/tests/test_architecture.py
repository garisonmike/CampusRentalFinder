"""
Architecture tests.

These enforce structural rules that would otherwise live only in an ADR and in
developers' heads. A rule in a document erodes; a rule in a test does not.

Each one fails on *addition* rather than on misuse: adding an unclassified
route, or a model that is neither tenant-scoped nor explicitly exempt, fails the
build and forces the decision at the point it is being made.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
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
    ours = {"accounts", "reviews", "universities"}
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


def test_the_neutral_host_machinery_still_works_with_no_public_routes() -> None:
    """**There are currently no PUBLIC_CANONICAL routes at all.**

    This test used to assert the set was non-empty, guarding the read-only
    rule against passing vacuously. Deleting the draft apps emptied it: every
    public route in the codebase belonged to `rentals` or `reviews`, and the
    rebuilt property and review APIs have not been written yet.

    That is a real gap, recorded in docs/ENGINEERING.md rather than papered
    over — but it is a gap in *coverage*, not in the mechanism. So what is
    asserted here is the mechanism: classification is still total (the test
    above), and `build_absolute_url` still refuses anything not classified as
    public. When the public listing routes land, the read-only rule starts
    biting again on its own, with nothing here to remember to flip.
    """
    from config.hosts import build_absolute_url

    public = [name for name, cls in ROUTE_HOST_CLASSES.items() if cls is HostClass.PUBLIC_CANONICAL]
    assert public == [], (
        "A PUBLIC_CANONICAL route now exists, which is good. Delete this test "
        "and restore the non-empty assertion it replaced."
    )

    # The guard that keeps a non-public route off the neutral host is what
    # actually protects ADR-001, and it does not depend on any route existing.
    with pytest.raises(ValueError):
        build_absolute_url("accounts:login")


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
    # Reached only through VerificationRequest, which IS scoped. Scoping the
    # document too would mean the retention job -- which must see every
    # university's overdue documents to delete them -- had to opt out of the
    # protection, and a job that opts out is a job that can be made to opt out
    # of the wrong thing.
    "accounts.VerificationDocument": (
        "Reached through the scoped VerificationRequest; the retention job "
        "must sweep every tenant's overdue documents."
    ),
    # The audit trail over the above. It must be readable when answering "who
    # looked at this student's ID", which is a question asked by a regulator
    # rather than by a university, and often after the tenant relationship has
    # ended entirely.
    "accounts.DocumentAccessLog": (
        "A regulator-facing audit trail, not tenant data. Append-only, and it "
        "outlives both the document and the tenancy."
    ),
    # Reached only by its own secret hash, never listed, and the profile it
    # points at carries the tenant -- so a scoped manager would be ceremony
    # around a table nobody queries by university. The per-address rate limit
    # is also deliberately GLOBAL: scoping it per university would let an
    # attacker reset their budget by targeting a different school.
    "accounts.EmailVerificationToken": (
        "Reached only by its secret; the profile it points at carries the "
        "tenant, and its rate limit must span universities to work."
    ),
    # A landlord's record spans universities, for the same reason their
    # profile does. Scoping it would report a different number to each
    # university, which is worse than reporting one number because both would
    # look authoritative (ADR-004).
    "reviews.LandlordRatingAggregate": (
        "A landlord's reputation spans universities; a per-tenant figure would "
        "give two authoritative-looking answers to the same question."
    ),
}


#: App labels whose models are ours to classify.
#:
#: Derived from **where the code lives**, not from a settings list. Two earlier
#: versions of this leaked:
#:
#: 1. A hand-written list silently stopped covering `properties` when that app
#:    was added, so the walk reported success on models it had never seen.
#: 2. Deriving it from `settings.LOCAL_APPS` fixed that and introduced a new
#:    bypass: `config` was later added to its own `PROJECT_APPS` slot, which
#:    the walk did not read. A settings slot the walk does not cover is a
#:    bypass by construction, and the walk's whole value is that adding a model
#:    forces a decision.
#:
#: An app whose package sits inside `backend/` is ours. That is a fact about
#: the repository rather than a fact about a settings file, so no new slot,
#: rename or reordering can route around it.
def _first_party_app_labels() -> frozenset[str]:
    from django.apps import apps as django_apps

    labels = set()
    for app_config in django_apps.get_app_configs():
        try:
            path = Path(app_config.path).resolve()
        except (TypeError, ValueError):  # pragma: no cover - defensive
            continue
        if path.is_relative_to(BACKEND_ROOT):
            labels.add(app_config.label)
    return frozenset(labels)


LOCAL_APP_LABELS = _first_party_app_labels()


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


def test_the_walk_covers_every_first_party_app() -> None:
    """Every app whose code lives in `backend/` is walked.

    Asserted against the settings slots explicitly, so that adding an app to
    any of them -- LOCAL_APPS, PROJECT_APPS, or a slot invented next year --
    cannot quietly place it outside the walk.
    """
    from django.conf import settings

    declared = {
        app.rsplit(".", 1)[-1]
        for slot in ("LOCAL_APPS", "PROJECT_APPS")
        for app in getattr(settings, slot, [])
    }
    missed = sorted(declared - set(LOCAL_APP_LABELS))

    assert not missed, (
        "These first-party apps are declared in settings but not walked:\n"
        + "\n".join(f"  {label}" for label in missed)
    )


def test_the_project_package_holds_no_models() -> None:
    """`config` is an installed app so its cross-app management commands are
    discovered. It must not become a place models live.

    A model in the project package would sit outside every app boundary the
    tenant rules are organised around, and `config` is imported by everything,
    so it is the one package where a model creates an import cycle rather than
    a dependency. If this ever needs to change it should be a deliberate argued
    edit, not a silent addition.
    """
    from django.apps import apps

    models = [model.__name__ for model in apps.get_app_config("config").get_models()]

    assert not models, (
        f"config now defines models: {models}. Move them to a domain app, or "
        "argue the case here and change this test on purpose."
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


def test_the_shadowing_check_runs_before_django_does() -> None:
    """The `property`-field rule lives in `tools/check_field_shadowing.py`.

    It cannot live here, and the reason is worth stating because it looks like
    an arbitrary split. `property = ForeignKey(...)` beside an `@property`
    raises `TypeError` **at import**, and Django imports every model module
    while populating its app registry -- before pytest collects a single test.
    An assertion in this file would never run: the suite would die at
    collection with the same stack trace the rule exists to replace.

    So the check is pure AST, imports nothing, and runs from pre-commit and CI
    ahead of everything else. This test asserts it is wired up and passing, not
    that the repository is clean -- the check itself decides that.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, str(BACKEND_ROOT / "tools" / "check_field_shadowing.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_shadowing_check_actually_detects_the_pattern(tmp_path) -> None:
    """A check that has never fired is a check nobody knows the state of.

    Exercised against a synthetic file rather than the repository, so it proves
    the detector works without depending on the repository being dirty.
    """
    from tools.check_field_shadowing import find_offenders

    offender = tmp_path / "models.py"
    offender.write_text(
        "class Unit:\n"
        "    property = object()\n"
        "\n"
        "    @property\n"
        "    def is_available(self):\n"
        "        return True\n"
    )
    clean = tmp_path / "clean.py"
    clean.write_text(
        "class Unit:\n"
        "    property_reviewed = object()\n"
        "\n"
        "    @property\n"
        "    def is_available(self):\n"
        "        return True\n"
    )

    assert any("is_available" in entry for entry in find_offenders([offender]))
    assert find_offenders([clean]) == []


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


def test_scoping_detection_sees_a_manager_declared_in_the_class_body() -> None:
    """`is_tenant_scoped` must resolve the descriptor, not read `__dict__`.

    A manager declared in a class body is stored as a `ManagerDescriptor`,
    which is truthy. An implementation reading `model.__dict__["objects"]`
    first therefore never reaches its own `getattr` fallback, and reports every
    such model as unscoped.

    It stayed invisible for six phases because every scoped model inherited
    `objects` from `TenantScopedModel` — absent from the subclass `__dict__`,
    so the fallback always ran. `Tenancy` is the first to declare its own, and
    was reported unscoped while being correctly scoped. Asserted on both shapes
    so a future rewrite cannot regress just one.
    """
    from config.tenancy import is_tenant_scoped

    declares_its_own = apps.get_model("tenancies", "Tenancy")
    inherits_it = apps.get_model("reviews", "Review")

    assert "objects" in declares_its_own.__dict__
    assert "objects" not in inherits_it.__dict__
    assert is_tenant_scoped(declares_its_own) is True
    assert is_tenant_scoped(inherits_it) is True


def test_coverage_measures_every_first_party_package() -> None:
    """The `--cov` list must not silently exclude an app.

    It listed `accounts`, `config`, `rentals` and `reviews` for six phases,
    which meant `properties`, `tenancies`, `universities` and `engagement` were
    never measured -- every coverage figure before 2026-08-26 was taken over
    roughly half the codebase, and the number looked healthy the whole time.

    A hand-maintained list that must be updated when an app is added is a
    ratchet with a hole in it, so this closes the hole rather than trusting the
    next person to remember.
    """
    import tomllib

    config = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text())
    measured = set(config["tool"]["coverage"]["run"]["source"])

    # `tools` holds the shadowing checker; it is first-party code with tests.
    expected = set(LOCAL_APP_LABELS) | {"tools"}
    missing = sorted(expected - measured)

    assert not missing, (
        "These first-party packages are not measured by coverage:\n"
        + "\n".join(f"  {name}" for name in missing)
        + "\n\nAdd them to the --cov list in pyproject.toml."
    )


def test_every_recurring_job_is_actually_scheduled() -> None:
    """A sweep nobody calls is a function, not a job.

    `expire_stale_inquiries` was written, tested, documented in its own
    docstring, and scheduled by nothing -- the existing scheduled-job tests all
    ran in the other direction, checking that entries in SCHEDULE are real
    rather than that real sweeps are in SCHEDULE.

    Any module-level callable whose name starts with `sweep_`, `reconcile_` or
    `expire_` is a recurring job by convention and must appear in the table.
    """
    from config.jobs.schedule import SCHEDULE

    scheduled = {job.func for job in SCHEDULE}
    prefixes = ("sweep_", "reconcile_", "expire_")
    unscheduled: list[str] = []

    for label in sorted(LOCAL_APP_LABELS):
        for module_name in ("jobs", "services", "retention"):
            try:
                module = importlib.import_module(f"{label}.{module_name}")
            except ModuleNotFoundError:
                continue
            for name in dir(module):
                if not name.startswith(prefixes):
                    continue
                attr = getattr(module, name)
                if not callable(attr) or getattr(attr, "__module__", "") != module.__name__:
                    continue
                dotted = f"{label}.{module_name}.{name}"
                if dotted not in scheduled:
                    unscheduled.append(dotted)

    assert not unscheduled, (
        "These look like recurring jobs but nothing schedules them:\n"
        + "\n".join(f"  {name}" for name in unscheduled)
        + "\n\nAdd them to config/jobs/schedule.py, or rename them if they are "
        "not recurring jobs."
    )


# ---------------------------------------------------------------------------
# The API layer
# ---------------------------------------------------------------------------


#: Third-party route trees we do not own and cannot annotate.
_FOREIGN_ROUTE_PREFIXES = ("admin", "django-rq")

#: drf-spectacular's own schema and documentation views.
#:
#: Exempt because they serve the OpenAPI document and its two viewers, not
#: product data -- there is nothing behind them a permission class would
#: protect. They are already classified in `config/hosts.py` as API_INTERNAL,
#: which is where the decision about who may reach them actually lives.
_DOC_VIEW_NAMES = frozenset({"schema", "swagger-ui", "redoc"})


def _api_view_classes() -> list[tuple[str, type]]:
    """Every view class the API router exposes, with its route name."""
    found = []
    for route in all_routes():
        if route.namespace in _FOREIGN_ROUTE_PREFIXES:
            continue
        if route.name in _DOC_VIEW_NAMES:
            continue
        view_class = getattr(route.callback, "cls", None)
        if view_class is None:
            continue
        found.append((route.name or route.pattern, view_class))
    return found


def test_every_api_view_declares_its_permission_classes() -> None:
    """No falling through to `DEFAULT_PERMISSION_CLASSES`.

    A default exists so that a forgotten view is not accidentally public, but
    relying on it is how a view ends up with the wrong policy: the default is
    `IsAuthenticated`, which is right for roughly half the API and silently
    wrong for the rest -- too open for a reviewer queue, too closed for a
    public listing.

    Declaring it at the view makes the decision visible in review, at the point
    where someone can judge whether it is correct.
    """
    from rest_framework.settings import api_settings

    default = tuple(api_settings.DEFAULT_PERMISSION_CLASSES)
    undeclared = []

    for name, view_class in _api_view_classes():
        declared = view_class.__dict__.get("permission_classes")
        if declared is None and tuple(getattr(view_class, "permission_classes", ())) == default:
            undeclared.append(f"{view_class.__name__} ({name})")

    assert not undeclared, (
        "These views fall through to DEFAULT_PERMISSION_CLASSES:\n"
        + "\n".join(f"  {entry}" for entry in sorted(set(undeclared)))
        + "\n\nDeclare permission_classes on the view, even if the answer is "
        "the same as the default. The point is that someone chose it."
    )


def _effective_throttle_scope(view_class) -> str | None:
    """The scope actually in force, however it was declared.

    Two mechanisms exist, for a reason rather than by accident: a class view
    sets `throttle_scope`, and a function view cannot, because `@api_view`
    copies `throttle_classes` onto its generated class but not
    `throttle_scope`. What matters is that *a* scope is in effect.
    """
    declared = getattr(view_class, "throttle_scope", None)
    if declared:
        return declared

    for throttle_class in getattr(view_class, "throttle_classes", ()):
        bound = getattr(throttle_class, "scope", None)
        if bound:
            return bound

    return None


def test_every_api_view_declares_a_throttle_scope() -> None:
    """A view with no scope inherits whatever the default throttle does.

    `config.api.throttling.Scope` is the vocabulary; `ScopedThrottle` raises on
    a scope with no configured rate, so the remaining failure is a view that
    names none at all and is therefore unthrottled.
    """
    unscoped = []

    for name, view_class in _api_view_classes():
        if _effective_throttle_scope(view_class) is None:
            unscoped.append(f"{view_class.__name__} ({name})")

    assert not unscoped, (
        "These views declare no throttle_scope:\n"
        + "\n".join(f"  {entry}" for entry in sorted(set(unscoped)))
        + "\n\nPick one from config.api.throttling.Scope."
    )

"""
Standalone AST check: a field named ``property`` beside an ``@property``.

**Deliberately importable without Django, and runnable before it loads.**
``property = ForeignKey(...)`` shadows the builtin inside the class body, so a
later ``@property`` resolves to a ForeignKey instance and the module raises
``TypeError: 'ForeignKey' object is not callable`` *at import*.

That import happens while Django populates its app registry — before pytest
collects a single test. A pytest assertion therefore never runs: the suite dies
at collection with a stack trace, which is exactly the outcome this check
exists to replace. So the check lives here, runs from pre-commit and CI ahead
of anything that imports Django, and prints a readable message naming the file,
the class and the method.

`tests/test_architecture.py` invokes this as a subprocess so the rule is
visible where the other structural rules are, but pre-commit is where it
actually catches anything.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {"migrations", "__pycache__", ".venv", "node_modules", "staticfiles"}


def python_sources(root: Path = BACKEND_ROOT) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not any(part in SKIP_DIRS for part in path.parts)
    ]


def declares_property_field(node: ast.ClassDef) -> bool:
    """Whether the class body assigns something to the name ``property``."""
    for statement in node.body:
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]

        for target in targets:
            if isinstance(target, ast.Name) and target.id == "property":
                return True

    return False


def uses_property_decorator(node: ast.ClassDef) -> list[str]:
    """Methods in the class body decorated with a bare ``@property``."""
    offenders = []

    for statement in node.body:
        if not isinstance(statement, ast.FunctionDef):
            continue
        for decorator in statement.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "property":
                offenders.append(statement.name)

    return offenders


def find_offenders(paths: list[Path] | None = None) -> list[str]:
    """Every class that shadows ``property`` and then uses the decorator.

    Every class, not only model subclasses: the pattern is broken in any class
    body, and deciding whether a base class is a model would need the import
    this must avoid.
    """
    offenders: list[str] = []

    for path in paths if paths is not None else python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - ruff owns syntax
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not declares_property_field(node):
                continue
            for method in uses_property_decorator(node):
                try:
                    location = path.relative_to(BACKEND_ROOT).as_posix()
                except ValueError:
                    # A path passed in from outside the tree, as the test does.
                    location = path.as_posix()
                offenders.append(f"{location}:{node.lineno}: {node.name}.{method}")

    return offenders


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    paths = [Path(arg).resolve() for arg in argv] or None
    offenders = find_offenders(paths)

    if not offenders:
        return 0

    print("A field named `property` shadows the builtin in these classes,")
    print("so the @property below it resolves to that field:")
    print()
    for entry in offenders:
        print(f"  {entry}")
    print()
    print("Make it a method, or rename the field. See Unit.is_available in")
    print("properties/models.py and PropertyRatingAggregate.property_reviewed")
    print("in ratings/aggregates.py for both fixes.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

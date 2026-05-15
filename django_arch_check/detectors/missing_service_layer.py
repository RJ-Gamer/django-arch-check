"""Missing Service Layer detector.

Flags views that contain business logic that belongs in a service layer:
    - Direct ORM manager calls (``Model.objects.*``) → ``warning``
    - View methods exceeding the line threshold AND containing ORM calls
      (proxy for "complex business logic in views") → ``critical``

Detection strategy
------------------
1. Find all ``views.py`` files in the project.
2. Parse with AST.
3. For each top-level function and each method inside a class:
   a. Count the non-blank, non-comment source lines in the function body.
   b. Walk the function's AST subtree looking for ORM call patterns:
      ``<Name>.objects.<anything>`` — covers ``User.objects.filter(…)``,
      ``Order.objects.get(…)``, etc.
4. Apply severity rules:
   - ``critical``: function has ORM calls **and** body lines > ``threshold``
   - ``warning``:  function has ORM calls (regardless of length)

"Body lines" are counted from the source directly using ``ast.get_source_segment``
where available (Python 3.8+), falling back to a line-range heuristic.

No threshold CLI flag is required by the spec; ``threshold`` (default 10) is an
internal constant that can be passed to ``detect()`` if desired.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MissingServiceLayerFinding:
    """A single missing-service-layer finding."""

    file_path: str  # relative path, e.g. "orders/views.py"
    view_name: str  # "create_order" or "OrderView.post"
    line_count: int  # non-blank, non-comment lines in the function body
    has_orm_calls: bool
    severity: Literal["warning", "critical"]


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "dist",
        "build",
        ".eggs",
    }
)

_DEFAULT_LINE_THRESHOLD = 10


# ---------------------------------------------------------------------------
# ORM call detection helpers
# ---------------------------------------------------------------------------


def _has_orm_calls(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function body contains any ``X.objects.Y(…)`` call.

    Pattern matched::

        User.objects.filter(...)   → Attribute(value=Attribute(value=Name("User"),
                                                                attr="objects"),
                                                attr="filter")
    """
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Attribute):
            continue
        # node is the outer attribute: .filter, .get, .all, .create, etc.
        inner = node.value
        if not isinstance(inner, ast.Attribute):
            continue
        # inner should be .objects
        if inner.attr != "objects":
            continue
        # inner.value should be a Name (the model class)
        if isinstance(inner.value, ast.Name):
            return True
    return False


# ---------------------------------------------------------------------------
# Line-count helper
# ---------------------------------------------------------------------------


def _count_body_lines(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
) -> int:
    """Count non-blank, non-comment lines in the function body.

    Uses the AST line numbers to extract the relevant slice of source, then
    applies the same heuristic as the god-app LOC counter.
    """
    # ast line numbers are 1-based; end_lineno is inclusive
    start = func_node.body[0].lineno - 1  # first statement in body
    end = func_node.end_lineno  # last line of the function (1-based, inclusive)
    body_lines = source_lines[start:end]
    return sum(1 for raw in body_lines if (s := raw.strip()) and not s.startswith("#"))


# ---------------------------------------------------------------------------
# Function/method discovery
# ---------------------------------------------------------------------------


def _iter_view_functions(
    tree: ast.Module,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Yield ``(qualified_name, node)`` pairs for every view function/method.

    Covers:
    - Top-level functions (FBVs): ``def create_order(request): …``
    - Methods inside classes (CBVs): ``class OrderView: def post(self, …): …``
    """
    results: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            results.append((node.name, node))

        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = f"{node.name}.{item.name}"
                    results.append((qualified, item))

    return results


# ---------------------------------------------------------------------------
# Severity helper
# ---------------------------------------------------------------------------


def _severity(
    has_orm: bool,
    line_count: int,
    threshold: int,
) -> Literal["warning", "critical"] | None:
    """Return severity, or ``None`` if the function should not be flagged."""
    if not has_orm:
        return None
    if line_count > threshold:
        return "critical"
    return "warning"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(
    project_path: str,
    line_threshold: int = _DEFAULT_LINE_THRESHOLD,
) -> list[MissingServiceLayerFinding]:
    """Walk *project_path* and return all missing-service-layer findings.

    Args:
        project_path:   Root directory of the Django project to analyse.
        line_threshold: Function body lines above which an ORM-using view
                        is escalated from ``warning`` to ``critical``.

    Returns:
        A list of :class:`MissingServiceLayerFinding` instances.
    """
    findings: list[MissingServiceLayerFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in filenames:
            if filename != "views.py":
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, project_path)

            try:
                with open(full_path, encoding="utf-8") as fh:
                    source = fh.read()
            except (OSError, UnicodeDecodeError):
                continue

            try:
                tree = ast.parse(source, filename=full_path)
            except SyntaxError:
                continue

            source_lines = source.splitlines()

            for view_name, func_node in _iter_view_functions(tree):
                has_orm = _has_orm_calls(func_node)
                line_count = _count_body_lines(func_node, source_lines)
                sev = _severity(has_orm, line_count, line_threshold)

                if sev is None:
                    continue

                findings.append(
                    MissingServiceLayerFinding(
                        file_path=rel_path,
                        view_name=view_name,
                        line_count=line_count,
                        has_orm_calls=has_orm,
                        severity=sev,
                    )
                )

    return findings

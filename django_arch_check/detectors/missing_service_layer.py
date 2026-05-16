"""Missing Service Layer detector.

Flags views that contain business logic that belongs in a service layer
by counting direct ORM calls (``Model.objects.*``) inside each view function.

Detection strategy
------------------
1. Find all ``views.py`` files in the project.
2. Parse with AST.
3. For each top-level function and each method inside a class:
   a. Count direct ORM calls: ``X.objects.filter/get/create/update/delete/all``
   b. Apply severity rules based on ORM call count:
      - ``warning``:  2 or more ORM calls in a single view function
      - ``critical``: 4 or more ORM calls in a single view function

Using ORM call *count* rather than line count avoids false positives on
views that are long only because of context dictionary building
(e.g. many ``context['key'] = value`` assignments).
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

    file_path: str          # relative path, e.g. "orders/views.py"
    view_name: str          # "create_order" or "OrderView.post"
    orm_call_count: int     # number of X.objects.* calls found in the function
    has_orm_calls: bool     # always True for any finding (kept for compatibility)
    severity: Literal["warning", "critical"]


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", ".tox",
        ".venv", "venv", "env", ".env",
        "__pycache__", "node_modules",
        ".mypy_cache", ".ruff_cache", ".pytest_cache",
        "htmlcov", "dist", "build", ".eggs",
    }
)

#: Minimum ORM call count to emit a warning.
_WARNING_ORM_THRESHOLD = 2

#: Minimum ORM call count to escalate to critical.
_CRITICAL_ORM_THRESHOLD = 4


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
# ORM call count helper
# ---------------------------------------------------------------------------


def _count_orm_calls(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    """Count ``X.objects.Y(…)`` call patterns in the function body.

    Each occurrence of the two-level attribute chain
    ``<Name>.objects.<method>`` counts as one ORM call.  This is the same
    pattern used by ``_has_orm_calls`` but returns a count instead of a bool.
    """
    count = 0
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Attribute):
            continue
        inner = node.value
        if not isinstance(inner, ast.Attribute):
            continue
        if inner.attr != "objects":
            continue
        if isinstance(inner.value, ast.Name):
            count += 1
    return count


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
    orm_call_count: int,
) -> Literal["warning", "critical"] | None:
    """Return severity based on ORM call count, or ``None`` if below threshold.

    - ``None``     — fewer than :data:`_WARNING_ORM_THRESHOLD` ORM calls
    - ``warning``  — :data:`_WARNING_ORM_THRESHOLD` or more ORM calls
    - ``critical`` — :data:`_CRITICAL_ORM_THRESHOLD` or more ORM calls
    """
    if orm_call_count >= _CRITICAL_ORM_THRESHOLD:
        return "critical"
    if orm_call_count >= _WARNING_ORM_THRESHOLD:
        return "warning"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(
    project_path: str,
) -> list[MissingServiceLayerFinding]:
    """Walk *project_path* and return all missing-service-layer findings.

    Args:
        project_path: Root directory of the Django project to analyse.

    Returns:
        A list of :class:`MissingServiceLayerFinding` instances for every
        view function with 2 or more direct ORM calls.
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

            for view_name, func_node in _iter_view_functions(tree):
                orm_count = _count_orm_calls(func_node)
                sev = _severity(orm_count)

                if sev is None:
                    continue

                findings.append(
                    MissingServiceLayerFinding(
                        file_path=rel_path,
                        view_name=view_name,
                        orm_call_count=orm_count,
                        has_orm_calls=True,
                        severity=sev,
                    )
                )

    return findings

"""Missing Service Layer detector.

Flags views that contain business logic that belongs in a service layer
by counting direct ORM calls (``Model.objects.*``) inside each view function.

Detection strategy
------------------
1. Find all ``views.py`` files in the project.
2. Parse with AST.
3. For each top-level function and each method inside a class:
   a. Skip DRF/Django override methods where ORM calls are expected.
   b. Count direct ORM calls: ``X.objects.filter/get/create/update/delete/all``
   c. Apply severity rules based on ORM call count:
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

from django_arch_check.detectors import filter_dirnames, should_ignore_file

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

#: DRF and Django CBV override methods where ORM calls are expected and correct.
#: Flagging these would be a false positive — their entire purpose is queryset work.
_EXEMPT_METHOD_NAMES: frozenset[str] = frozenset(
    {
        # DRF ViewSet / GenericAPIView overrides
        "get_queryset",
        "get_object",
        "get_serializer",
        "get_serializer_class",
        "perform_create",
        "perform_update",
        "perform_destroy",
        "get_permissions",
        "get_throttles",
        "get_authenticators",
        # Django CBV overrides
        "get_context_data",
        "get_form_kwargs",
        "form_valid",
        "form_invalid",
        # Common custom override names
        "get_success_url",
    }
)


# ---------------------------------------------------------------------------
# ORM call detection helpers
# ---------------------------------------------------------------------------


def _has_orm_calls(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function body contains any ``X.objects.Y(…)`` call."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Attribute):
            continue
        inner = node.value
        if not isinstance(inner, ast.Attribute):
            continue
        if inner.attr != "objects":
            continue
        if isinstance(inner.value, ast.Name):
            return True
    return False


# ---------------------------------------------------------------------------
# ORM call count helper
# ---------------------------------------------------------------------------


def _count_orm_calls(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    """Count ``X.objects.Y(…)`` call patterns in the function body."""
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
    """Return ``(qualified_name, node)`` pairs for every view function/method.

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
    """Return severity based on ORM call count, or ``None`` if below threshold."""
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
    ignore_paths: tuple[str, ...] = (),
) -> list[MissingServiceLayerFinding]:
    """Walk *project_path* and return all missing-service-layer findings."""
    findings: list[MissingServiceLayerFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        filter_dirnames(project_path, dirpath, dirnames, _SKIP_DIRS, ignore_paths)

        for filename in filenames:
            if filename != "views.py":
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, project_path)
            if should_ignore_file(rel_path, ignore_paths):
                continue

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

                # ← THIS IS WHAT WAS MISSING IN YOUR VERSION
                # Strip class prefix before checking: "PostViewSet.get_queryset" → "get_queryset"
                bare_name = view_name.split(".")[-1]
                if bare_name in _EXEMPT_METHOD_NAMES:
                    continue

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
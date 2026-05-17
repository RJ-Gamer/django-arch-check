"""N+1 Query Risk detector.

Finds ORM calls inside for-loops or list-comprehensions in view files and
serializer files where no ``select_related`` or ``prefetch_related`` call
appears in the same function scope before the loop.

Detection strategy
------------------
1. Scan all ``views.py`` and ``serializers.py`` files.
2. For each function/method in the file:
   a. Walk the function body looking for ``for`` loops and list
      comprehensions (``ListComp``).
   b. Inside each loop body, look for ORM call patterns:
      - ``X.objects.method()`` — the full two-level attribute chain is
        required so plain ``.get(`` on dicts/lists is not flagged.
        Matched methods: ``filter``, ``get``, ``all``, ``exclude``,
        ``first``, ``last``, ``values``, ``values_list``,
        ``annotate``, ``aggregate``, ``count``
      - or variable names containing ``queryset``
   c. Before flagging, check whether ``select_related`` or
      ``prefetch_related`` appear anywhere **earlier** in the same
      function scope (as attribute names in any expression).
   d. If an ORM call is found inside the loop and the function lacks
      ``select_related``/``prefetch_related``, emit a finding.

Severity: always ``warning``.

Limitations (acknowledged, minimal-fix policy):
    - Only inspects the same function scope — does not follow calls across
      function boundaries.
    - ``ListComp`` ORM detection checks the element expression and the
      ``if`` clauses, not the iterator expression.
    - False positives are possible if ``select_related`` is called in a
      helper that is invoked before the loop.
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
class NPlusOneFinding:
    """A single N+1 query-risk finding."""

    file_path: str                  # relative path, e.g. "orders/views.py"
    line_number: int                # 1-based line of the loop/comprehension
    severity: Literal["warning"]    # always warning


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

#: File names to inspect.
_TARGET_FILES: frozenset[str] = frozenset({"views.py", "serializers.py"})

#: ORM queryset method names.  These are only flagged when preceded by
#: ``.objects.`` — plain ``.get(`` on dicts/lists is intentionally excluded.
_ORM_METHODS: frozenset[str] = frozenset(
    {
        "filter", "get", "all", "exclude",
        "first", "last", "values", "values_list",
        "annotate", "aggregate", "count",
    }
)

#: Methods that indicate the queryset is already optimised.
_PREFETCH_METHODS: frozenset[str] = frozenset(
    {"select_related", "prefetch_related"}
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _collect_attr_names(nodes: list[ast.stmt]) -> set[str]:
    """Collect every attribute name (``node.attr``) appearing in *nodes*.

    Used to check whether ``select_related`` / ``prefetch_related`` appears
    anywhere in the function body before a given loop.
    """
    names: set[str] = set()
    for stmt in nodes:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
    return names


def _is_queryset_orm_call(node: ast.Attribute) -> bool:
    """Return True only for ``X.objects.method(…)`` patterns.

    Requires the two-level chain::

        Order.objects.filter(...)
        ↑ Name   ↑ Attribute("objects")  ↑ Attribute(attr in _ORM_METHODS)

    This prevents plain dict/list ``.get(`` calls from being flagged.
    """
    if node.attr not in _ORM_METHODS:
        return False
    inner = node.value
    if not isinstance(inner, ast.Attribute):
        return False
    return inner.attr == "objects"


def _loop_has_orm_call(loop_body: list[ast.stmt]) -> bool:
    """Return True if any statement in *loop_body* contains a queryset ORM call.

    Matches:
    - ``X.objects.method(…)`` — full two-level attribute chain only.
    - ``ast.Name`` nodes whose ``id`` contains the substring ``queryset``
      (case-insensitive).
    """
    for stmt in loop_body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Attribute) and _is_queryset_orm_call(node):
                return True
            if isinstance(node, ast.Name) and "queryset" in node.id.lower():
                return True
    return False


def _listcomp_has_orm_call(comp_node: ast.ListComp) -> bool:
    """Return True if the element or conditions of a list-comprehension
    contain queryset ORM calls (``X.objects.method``)."""
    # Check the element expression
    for node in ast.walk(comp_node.elt):
        if isinstance(node, ast.Attribute) and _is_queryset_orm_call(node):
            return True
    # Check each generator's condition (ifs)
    for generator in comp_node.generators:
        for cond in generator.ifs:
            for node in ast.walk(cond):
                if isinstance(node, ast.Attribute) and _is_queryset_orm_call(node):
                    return True
    return False


# ---------------------------------------------------------------------------
# Per-function analysis
# ---------------------------------------------------------------------------


def _check_function(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[int]:
    """Return line numbers of N+1 risks found within *func_node*.

    Strategy:
    1. Collect all attribute names in the *entire* function body to check
       for ``select_related``/``prefetch_related`` anywhere in scope.
    2. Walk the function body statement-by-statement.
    3. For each ``for`` loop, check if the body has ORM calls.
    4. For each statement, check for ``ListComp`` containing ORM calls.
    5. Skip flagging if the function uses prefetch optimisations anywhere.
    """
    body = func_node.body

    # Gather all attr names in scope to check for optimisation hints.
    all_attrs = _collect_attr_names(body)
    if _PREFETCH_METHODS & all_attrs:
        # Function already uses select_related or prefetch_related — skip.
        return []

    risky_lines: list[int] = []

    for stmt in body:
        # ── For loops ───────────────────────────────────────────────────
        if isinstance(stmt, ast.For):
            if _loop_has_orm_call(stmt.body):
                risky_lines.append(stmt.lineno)

        # ── List comprehensions inside Expr statements ───────────────────
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.ListComp):
            if _listcomp_has_orm_call(stmt.value):
                risky_lines.append(stmt.lineno)

        # ── Assignments containing list comprehensions ───────────────────
        elif isinstance(stmt, ast.Assign):
            for node in ast.walk(stmt):
                if isinstance(node, ast.ListComp) and _listcomp_has_orm_call(node):
                    risky_lines.append(stmt.lineno)
                    break

    return risky_lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(
    project_path: str,
    ignore_paths: tuple[str, ...] = (),
) -> list[NPlusOneFinding]:
    """Walk *project_path* and return all N+1 query-risk findings.

    Args:
        project_path: Root directory of the Django project to analyse.

    Returns:
        A list of :class:`NPlusOneFinding` instances, one per risky loop.
    """
    findings: list[NPlusOneFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        filter_dirnames(project_path, dirpath, dirnames, _SKIP_DIRS, ignore_paths)

        for filename in filenames:
            if filename not in _TARGET_FILES:
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

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for lineno in _check_function(node):
                    findings.append(
                        NPlusOneFinding(
                            file_path=rel_path,
                            line_number=lineno,
                            severity="warning",
                        )
                    )

    return findings

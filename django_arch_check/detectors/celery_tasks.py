"""Celery Tasks Without Retry detector.

Finds Celery task functions decorated with ``@app.task`` or ``@shared_task``
that lack retry configuration.  Tasks with no retry policy will silently
drop work on transient failures (network blips, DB locks, rate limits).

Retry configuration is considered present when any of the following appear
as keyword arguments on the decorator:
    - ``max_retries``
    - ``autoretry_for``
    - ``retry_backoff``

Severity rules:
    - ``critical``: task name contains a high-stakes keyword
      (``payment``, ``email``, ``invoice``, ``notification``) **and** no
      retry configured.
    - ``warning``:  any other task with no retry configured.

High-stakes keyword matching is case-insensitive substring search.
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
class CeleryTaskFinding:
    """A single Celery-task finding."""

    file_path: str                      # relative path, e.g. "payments/tasks.py"
    task_name: str                      # function name, e.g. "charge_customer"
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

#: Decorator names that mark a function as a Celery task.
_TASK_DECORATORS: frozenset[str] = frozenset({"task", "shared_task"})

#: Keyword arguments on a task decorator that indicate retry is configured.
_RETRY_KWARGS: frozenset[str] = frozenset(
    {"max_retries", "autoretry_for", "retry_backoff"}
)

#: Substrings (case-insensitive) that make a task "high stakes".
_HIGH_STAKES_KEYWORDS: tuple[str, ...] = (
    "payment", "email", "invoice", "notification",
)

#: Path component that marks a file as a migration — excluded from scanning.
_MIGRATIONS_DIR = "migrations"


# ---------------------------------------------------------------------------
# Migration path helper
# ---------------------------------------------------------------------------


def _is_migration_file(rel_path: str) -> bool:
    """Return True when *rel_path* lives inside a ``migrations`` directory.

    Normalises backslashes to forward slashes before splitting so the check
    works correctly on Windows paths.
    """
    parts = rel_path.replace("\\", "/").split("/")
    return _MIGRATIONS_DIR in parts


# ---------------------------------------------------------------------------
# Decorator recognition helpers
# ---------------------------------------------------------------------------


def _decorator_name(dec: ast.expr) -> str | None:
    """Extract the base attribute/name from a decorator expression.

    Handles:
    - ``@shared_task``               → ``"shared_task"``
    - ``@app.task``                  → ``"task"``
    - ``@shared_task(bind=True)``    → ``"shared_task"``
    - ``@app.task(max_retries=3)``   → ``"task"``
    """
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    if isinstance(dec, ast.Call):
        return _decorator_name(dec.func)
    return None


def _decorator_kwargs(dec: ast.expr) -> set[str]:
    """Return the set of keyword argument names passed to a decorator call.

    Returns an empty set when the decorator is not called with arguments
    (e.g. bare ``@shared_task``).
    """
    if isinstance(dec, ast.Call):
        return {kw.arg for kw in dec.keywords if kw.arg is not None}
    return set()


def _is_task_decorator(dec: ast.expr) -> bool:
    """Return True if *dec* is a ``@app.task`` or ``@shared_task`` decorator."""
    return _decorator_name(dec) in _TASK_DECORATORS


def _has_retry_config(dec: ast.expr) -> bool:
    """Return True if the task decorator includes retry configuration."""
    kwargs = _decorator_kwargs(dec)
    return bool(kwargs & _RETRY_KWARGS)


# ---------------------------------------------------------------------------
# Severity helper
# ---------------------------------------------------------------------------


def _is_high_stakes(task_name: str) -> bool:
    """Return True if the task name contains a high-stakes keyword."""
    lower = task_name.lower()
    return any(kw in lower for kw in _HIGH_STAKES_KEYWORDS)


def _severity(task_name: str) -> Literal["warning", "critical"]:
    if _is_high_stakes(task_name):
        return "critical"
    return "warning"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(project_path: str) -> list[CeleryTaskFinding]:
    """Walk *project_path* and return all Celery-task findings.

    Args:
        project_path: Root directory of the Django project to analyse.

    Returns:
        A list of :class:`CeleryTaskFinding` instances for every task that
        lacks retry configuration.
    """
    findings: list[CeleryTaskFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, project_path)

            if _is_migration_file(rel_path):
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

            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not node.decorator_list:
                    continue

                task_decorators = [d for d in node.decorator_list if _is_task_decorator(d)]
                if not task_decorators:
                    continue

                # At least one @task or @shared_task decorator found.
                # Check if *any* of them specifies retry config.
                has_retry = any(_has_retry_config(d) for d in task_decorators)
                if has_retry:
                    continue

                findings.append(
                    CeleryTaskFinding(
                        file_path=rel_path,
                        task_name=node.name,
                        severity=_severity(node.name),
                    )
                )

    return findings

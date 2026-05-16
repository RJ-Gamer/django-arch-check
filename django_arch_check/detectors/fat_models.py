"""Fat Model detector.

A Django model is considered "fat" when it accumulates too many methods,
turning it into a god object that violates the Single Responsibility Principle.

Severity thresholds (configurable via `threshold` parameter):
    - warning:  threshold     <= method_count < threshold * 2
    - critical: method_count >= threshold * 2

Default threshold: 15 methods → warning at 15+, critical at 30+.

Default threshold: 15 methods.
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
class FatModelFinding:
    """A single fat-model finding emitted by the detector."""

    file_path: str
    class_name: str
    method_count: int
    severity: Literal["warning", "critical"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _base_names(class_node: ast.ClassDef) -> list[str]:
    """Return a flat list of base-class name strings for a class node.

    Handles both plain names (`Model`) and attribute access (`models.Model`).
    Returns the full dotted string, e.g. ``"models.Model"``.
    """
    names: list[str] = []
    for base in class_node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            # Reconstruct dotted name: models.Model, django.db.models.Model, …
            parts: list[str] = []
            node: ast.expr = base
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            names.append(".".join(reversed(parts)))
    return names


def _is_model_class(class_node: ast.ClassDef) -> bool:
    """Return True if any base class name contains the word 'Model'."""
    return any("Model" in name for name in _base_names(class_node))


def _is_dunder(method_name: str) -> bool:
    """Return True for names like __init__, __str__, __eq__, etc."""
    return method_name.startswith("__") and method_name.endswith("__")


def _count_methods(class_node: ast.ClassDef) -> int:
    """Count non-dunder method definitions directly on a class body.

    Counts both ``def`` and ``async def`` statements. Does not descend
    into nested class bodies.
    """
    count = 0
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _is_dunder(node.name):
                count += 1
    return count


def _severity(method_count: int, threshold: int) -> Literal["warning", "critical"]:
    if method_count >= threshold * 2:
        return "critical"
    return "warning"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

#: Directories that are never part of a Django project's application code.
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


def detect(project_path: str, threshold: int = 15) -> list[FatModelFinding]:
    """Walk *project_path* and return all fat-model findings.

    Args:
        project_path: Root directory of the Django project to analyse.
        threshold:    Minimum non-dunder method count to flag a model.
                      Classes with ``>= threshold * 2`` methods are
                      ``critical``; those with ``>= threshold`` are
                      ``warning``.

    Returns:
        A list of :class:`FatModelFinding` instances, one per fat model
        class discovered, in file-system traversal order.
    """
    findings: list[FatModelFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        # Prune traversal in-place so os.walk never descends into these dirs.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            full_path = os.path.join(dirpath, filename)
            # Use a relative path in findings so output is not machine-specific.
            rel_path = os.path.relpath(full_path, project_path)

            try:
                source = _read_source(full_path)
            except (OSError, UnicodeDecodeError):
                # Silently skip unreadable or non-UTF-8 files.
                continue

            try:
                tree = ast.parse(source, filename=full_path)
            except SyntaxError:
                # Skip files that cannot be parsed (e.g. Python 2 syntax).
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not _is_model_class(node):
                    continue

                method_count = _count_methods(node)
                if method_count < threshold:
                    continue

                findings.append(
                    FatModelFinding(
                        file_path=rel_path,
                        class_name=node.name,
                        method_count=method_count,
                        severity=_severity(method_count, threshold),
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# Private I/O helper (isolated for easy testing / mocking)
# ---------------------------------------------------------------------------


def _read_source(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()

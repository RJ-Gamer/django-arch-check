"""God App detector.

A Django "god app" is one that owns a disproportionately large share of the
total project's business logic. Concentration of code in a single app is a
structural smell: it resists decomposition, creates merge conflicts, and
makes onboarding harder.

Severity thresholds (relative to *threshold*, default 30 %):
    - warning:  percentage >= threshold          (default: 30 %)
    - critical: percentage >= threshold + 20     (default: 50 %)

The +20 gap is fixed so that the two bands always have a meaningful spread
regardless of what the user sets ``threshold`` to.

Lines of code (LOC) definition used here:
    A line counts if, after stripping whitespace, it is non-empty and does
    not begin with ``#``.  This intentionally excludes blank lines and
    standalone comment lines; it does *not* attempt to parse docstrings.
    This is a fast, good-enough heuristic for *relative* app sizing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GodAppFinding:
    """A single god-app finding emitted by the detector."""

    app_path: str  # Relative path to the app directory, e.g. "core/"
    app_loc: int  # Counted LOC inside this app
    total_loc: int  # Counted LOC across the entire project
    percentage: int  # Rounded integer: app_loc / total_loc * 100
    severity: Literal["warning", "critical"]


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

#: Directories that are never part of a Django project's application code.
#: NOTE: this duplicates the same constant in fat_models.py intentionally —
#: sibling-detector imports create lateral coupling. Extract to
#: detectors/__init__.py once a third detector needs it.
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

#: Files whose presence marks a directory as a Django application.
_APP_MARKERS: frozenset[str] = frozenset({"models.py", "apps.py"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_py_files(root: str) -> list[str]:
    """Return absolute paths of all ``.py`` files under *root*.

    Prunes :data:`_SKIP_DIRS` so virtual-env and cache directories are
    never traversed.
    """
    result: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                result.append(os.path.join(dirpath, filename))
    return result


def _count_loc(file_path: str) -> int:
    """Return the number of non-blank, non-comment lines in *file_path*.

    Returns 0 for any file that cannot be opened or decoded.
    """
    try:
        with open(file_path, encoding="utf-8") as fh:
            return sum(
                1
                for raw_line in fh
                if (stripped := raw_line.strip()) and not stripped.startswith("#")
            )
    except (OSError, UnicodeDecodeError):
        return 0


def _find_app_dirs(project_path: str) -> list[str]:
    """Return absolute paths of all Django app directories under *project_path*.

    A directory qualifies as a Django app if it contains at least one of
    the files in :data:`_APP_MARKERS` (``models.py`` or ``apps.py``).
    """
    app_dirs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if _APP_MARKERS & set(filenames):
            app_dirs.append(dirpath)
    return app_dirs


def _severity(percentage: int, threshold: int) -> Literal["warning", "critical"]:
    if percentage >= threshold + 20:
        return "critical"
    return "warning"


def _to_display_path(abs_app_dir: str, project_path: str) -> str:
    """Return a display-friendly relative path with a trailing slash.

    Uses forward slashes on all platforms for consistent output.
    """
    rel = os.path.relpath(abs_app_dir, project_path)
    # Normalise to forward slashes and append trailing slash.
    return rel.replace(os.sep, "/") + "/"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(project_path: str, threshold: int = 30) -> list[GodAppFinding]:
    """Walk *project_path* and return all god-app findings.

    Args:
        project_path: Root directory of the Django project to analyse.
        threshold:    Warning threshold as an integer percentage (0-100).
                      Apps at or above this share are flagged ``warning``;
                      apps at or above ``threshold + 20`` are ``critical``.

    Returns:
        A list of :class:`GodAppFinding` instances sorted by percentage
        descending (most severe first).  Empty list if the project has no
        countable code or no app exceeds the threshold.
    """
    # ------------------------------------------------------------------
    # 1. Count total project LOC (denominator for all percentages).
    # ------------------------------------------------------------------
    all_py_files = _collect_py_files(project_path)
    total_loc = sum(_count_loc(f) for f in all_py_files)

    if total_loc == 0:
        return []

    # ------------------------------------------------------------------
    # 2. Discover Django apps and count their LOC.
    # ------------------------------------------------------------------
    app_dirs = _find_app_dirs(project_path)

    # A single-app project owns 100% of its own code by definition.
    # That is not a structural smell — it is just a small project.
    # Require at least 2 apps before the detector makes any sense.
    if len(app_dirs) < 2:
        return []

    findings: list[GodAppFinding] = []

    for app_dir in app_dirs:
        app_files = _collect_py_files(app_dir)
        app_loc = sum(_count_loc(f) for f in app_files)
        percentage = round(app_loc / total_loc * 100)

        if percentage < threshold:
            continue

        findings.append(
            GodAppFinding(
                app_path=_to_display_path(app_dir, project_path),
                app_loc=app_loc,
                total_loc=total_loc,
                percentage=percentage,
                severity=_severity(percentage, threshold),
            )
        )

    # Sort most-severe first so the CLI can print without re-sorting.
    findings.sort(key=lambda f: f.percentage, reverse=True)
    return findings

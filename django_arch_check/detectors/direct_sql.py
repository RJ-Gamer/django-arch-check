"""Direct SQL detector.

Scans all Python source files for patterns indicating raw SQL usage that
bypasses Django's ORM.  Raw SQL queries are harder to test, harder to
migrate across databases, and bypass Django's SQL injection protections
when used carelessly.

Patterns detected (as literal string searches on each source line):
    - ``cursor.execute(``
    - ``connection.cursor()``
    - ``.raw(``
    - ``.extra(select=``

Severity: always ``warning``.

Exclusions:
    - Any file whose path includes a ``migrations`` directory component is
      silently skipped — raw SQL in migrations is an accepted pattern.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectSQLFinding:
    """A single direct-SQL finding."""

    file_path: str  # relative path, e.g. "orders/models.py"
    line_number: int  # 1-based line number
    pattern: str  # the matched pattern, e.g. "cursor.execute("
    severity: Literal["warning"]  # always warning


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

#: Raw-SQL patterns to scan for.  Checked as case-sensitive substrings of
#: each stripped source line.
_SQL_PATTERNS: tuple[str, ...] = (
    "cursor.execute(",
    "connection.cursor()",
    ".raw(",
    ".extra(select=",
)

#: Path component that marks a file as a migration — excluded from scanning.
_MIGRATIONS_DIR = "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_migration_file(rel_path: str) -> bool:
    """Return True when *rel_path* lives inside a ``migrations`` directory."""
    # Normalise separators for reliable splitting.
    parts = rel_path.replace(os.sep, "/").split("/")
    return _MIGRATIONS_DIR in parts


def _scan_file(full_path: str, rel_path: str) -> list[DirectSQLFinding]:
    """Return all SQL-pattern findings within a single file."""
    findings: list[DirectSQLFinding] = []
    try:
        with open(full_path, encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                for pattern in _SQL_PATTERNS:
                    if pattern in line:
                        findings.append(
                            DirectSQLFinding(
                                file_path=rel_path,
                                line_number=lineno,
                                pattern=pattern,
                                severity="warning",
                            )
                        )
                        # One finding per line per pattern; don't double-report
                        # if two patterns match the same line.
                        break
    except (OSError, UnicodeDecodeError):
        pass
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(project_path: str) -> list[DirectSQLFinding]:
    """Walk *project_path* and return all direct-SQL findings.

    Args:
        project_path: Root directory of the Django project to analyse.

    Returns:
        A list of :class:`DirectSQLFinding` instances, one per matching
        line.  Migration files are excluded.
    """
    findings: list[DirectSQLFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, project_path)

            if _is_migration_file(rel_path):
                continue

            findings.extend(_scan_file(full_path, rel_path))

    return findings

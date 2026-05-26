"""Migration Safety detector.

Scans Django migration files and flags operations that carry deployment
or data-safety risk.

This detector ADVISES — it never blocks. Every finding explains:
  - What could go wrong if this was unintentional
  - When the pattern is safe to ignore
  - A safer alternative approach

Operations detected
-------------------
Operation       Condition                          Severity
──────────────  ─────────────────────────────────  ────────
AddField        NOT NULL column, no default        warning
RemoveField     always                             warning
RenameField     always                             warning
RunPython       Migration class lacks atomic=False warning
RunSQL          always                             warning

Philosophy
----------
These are not bugs. They are deliberate decisions that carry risk when
unintentional. The tool says "here is what could go wrong" — not
"your decision is wrong". Suppress a known-safe finding by adding
``# django-arch-check: ignore`` on the operation line.
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
class MigrationSafetyFinding:
    """A single migration safety finding."""

    file_path: str               # relative path  e.g. "orders/migrations/0042_remove_phone.py"
    migration_name: str          # e.g. "0042_remove_phone"
    operation: str               # e.g. "RemoveField"
    model_name: str              # e.g. "order"  — empty for RunPython / RunSQL
    field_name: str              # e.g. "phone"  — empty for RunPython / RunSQL
                                 #               — "old → new" for RenameField
    message: str                 # advisory text
    severity: Literal["warning"] # always warning — migrations are intentional acts


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

_MIGRATIONS_DIR = "migrations"

#: Operation names this detector watches.
_WATCHED_OPERATIONS: frozenset[str] = frozenset(
    {"AddField", "RemoveField", "RenameField", "RunPython", "RunSQL"}
)

#: Advisory messages — keyed by operation name.
_MESSAGES: dict[str, str] = {
    "AddField": (
        "Adding a NOT NULL column without a default will fail on non-empty tables. "
        "Safe if this is a brand-new table. Otherwise: add the field as nullable first, "
        "backfill existing rows, then apply the NOT NULL constraint in a separate migration."
    ),
    "RemoveField": (
        "Field removal is irreversible. Ensure no live code still references this field "
        "before deploying. For zero-downtime deploys: deprecate the field first, ship that "
        "deploy, then remove the field in the next release."
    ),
    "RenameField": (
        "Renaming a field breaks any code referencing the old name during a rolling deploy. "
        "For zero-downtime: add the new field, backfill data, update all references in one "
        "release, then remove the old field in a separate migration."
    ),
    "RunPython": (
        "Data migrations run inside a transaction by default. Long-running operations "
        "hold locks and can cause timeouts on large tables. For large datasets, set "
        "'atomic = False' on the Migration class and manage transactions manually inside "
        "the forward function."
    ),
    "RunSQL": (
        "Raw SQL in migrations bypasses Django's ORM safety layer. Ensure the statement "
        "is idempotent, tested against your target database version, and that a safe "
        "reverse SQL is provided."
    ),
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_in_migrations_dir(rel_path: str) -> bool:
    """Return True when *rel_path* lives inside a ``migrations`` directory."""
    parts = rel_path.replace("\\", "/").split("/")
    return _MIGRATIONS_DIR in parts


def _get_operation_name(call: ast.Call) -> str | None:
    """Extract the bare operation name from a Call node.

    Handles both ``migrations.AddField(...)`` and bare ``AddField(...)``.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr          # migrations.AddField → "AddField"
    if isinstance(func, ast.Name):
        return func.id            # AddField            → "AddField"
    return None


def _get_string_kwarg(call: ast.Call, name: str) -> str:
    """Return the string value of keyword argument *name*, or empty string."""
    for kw in call.keywords:
        if kw.arg == name:
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
    return ""


def _add_field_is_risky(call: ast.Call) -> bool:
    """Return True when AddField adds a NOT NULL column with no default.

    A field is considered safe when it has ``null=True`` **or** an explicit
    ``default=`` kwarg.  If the ``field=`` argument cannot be statically
    inspected (e.g. it is a variable) the function returns ``False`` to
    avoid a false positive.
    """
    field_node: ast.expr | None = None
    for kw in call.keywords:
        if kw.arg == "field":
            field_node = kw.value
            break

    if not isinstance(field_node, ast.Call):
        # Variable or complex expression — cannot inspect safely; skip.
        return False

    has_null_true = False
    has_default = False

    for kw in field_node.keywords:
        if kw.arg == "null":
            if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                has_null_true = True
        if kw.arg == "default":
            has_default = True

    return not has_null_true and not has_default


def _migration_has_atomic_false(class_node: ast.ClassDef) -> bool:
    """Return True when the Migration class body contains ``atomic = False``."""
    for item in class_node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if isinstance(target, ast.Name) and target.id == "atomic":
                if isinstance(item.value, ast.Constant) and item.value.value is False:
                    return True
    return False


def _find_migration_class(tree: ast.Module) -> ast.ClassDef | None:
    """Return the ``Migration`` class node from *tree*, or ``None``."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            return node
    return None


def _extract_operations(migration_class: ast.ClassDef) -> list[ast.Call]:
    """Return the list of operation Call nodes from ``operations = [...]``."""
    for item in migration_class.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if isinstance(target, ast.Name) and target.id == "operations":
                if isinstance(item.value, ast.List):
                    return [
                        elt
                        for elt in item.value.elts
                        if isinstance(elt, ast.Call)
                    ]
    return []


def _has_ignore_comment(source_lines: list[str], call: ast.Call) -> bool:
    """Return True when the operation line carries a suppress comment."""
    lineno = call.lineno - 1  # convert to 0-based index
    if 0 <= lineno < len(source_lines):
        return "# django-arch-check: ignore" in source_lines[lineno]
    return False


# ---------------------------------------------------------------------------
# Finding builders — one per operation type
# ---------------------------------------------------------------------------


def _build_add_field(
    call: ast.Call, rel_path: str, migration_name: str, source_lines: list[str]
) -> MigrationSafetyFinding | None:
    if not _add_field_is_risky(call):
        return None
    if _has_ignore_comment(source_lines, call):
        return None
    return MigrationSafetyFinding(
        file_path=rel_path,
        migration_name=migration_name,
        operation="AddField",
        model_name=_get_string_kwarg(call, "model_name"),
        field_name=_get_string_kwarg(call, "name"),
        message=_MESSAGES["AddField"],
        severity="warning",
    )


def _build_remove_field(
    call: ast.Call, rel_path: str, migration_name: str, source_lines: list[str]
) -> MigrationSafetyFinding | None:
    if _has_ignore_comment(source_lines, call):
        return None
    return MigrationSafetyFinding(
        file_path=rel_path,
        migration_name=migration_name,
        operation="RemoveField",
        model_name=_get_string_kwarg(call, "model_name"),
        field_name=_get_string_kwarg(call, "name"),
        message=_MESSAGES["RemoveField"],
        severity="warning",
    )


def _build_rename_field(
    call: ast.Call, rel_path: str, migration_name: str, source_lines: list[str]
) -> MigrationSafetyFinding | None:
    if _has_ignore_comment(source_lines, call):
        return None
    old_name = _get_string_kwarg(call, "old_name")
    new_name = _get_string_kwarg(call, "new_name")
    field_display = f"{old_name} → {new_name}" if old_name else ""
    return MigrationSafetyFinding(
        file_path=rel_path,
        migration_name=migration_name,
        operation="RenameField",
        model_name=_get_string_kwarg(call, "model_name"),
        field_name=field_display,
        message=_MESSAGES["RenameField"],
        severity="warning",
    )


def _build_run_python(
    call: ast.Call,
    rel_path: str,
    migration_name: str,
    has_atomic_false: bool,
    source_lines: list[str],
) -> MigrationSafetyFinding | None:
    if has_atomic_false:
        return None  # Migration already opts out of the default transaction
    if _has_ignore_comment(source_lines, call):
        return None
    return MigrationSafetyFinding(
        file_path=rel_path,
        migration_name=migration_name,
        operation="RunPython",
        model_name="",
        field_name="",
        message=_MESSAGES["RunPython"],
        severity="warning",
    )


def _build_run_sql(
    call: ast.Call, rel_path: str, migration_name: str, source_lines: list[str]
) -> MigrationSafetyFinding | None:
    if _has_ignore_comment(source_lines, call):
        return None
    return MigrationSafetyFinding(
        file_path=rel_path,
        migration_name=migration_name,
        operation="RunSQL",
        model_name="",
        field_name="",
        message=_MESSAGES["RunSQL"],
        severity="warning",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(
    project_path: str,
    ignore_paths: tuple[str, ...] = (),
) -> list[MigrationSafetyFinding]:
    """Walk *project_path* and return all migration safety findings.

    Only files inside ``migrations/`` directories are scanned.
    ``__init__.py`` files are skipped.

    Args:
        project_path: Root directory of the Django project to analyse.
        ignore_paths: File-path substrings to exclude from scanning.

    Returns:
        A list of :class:`MigrationSafetyFinding` instances.
    """
    findings: list[MigrationSafetyFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        filter_dirnames(project_path, dirpath, dirnames, _SKIP_DIRS, ignore_paths)

        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            if filename == "__init__.py":
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, project_path)

            if should_ignore_file(rel_path, ignore_paths):
                continue
            if not _is_in_migrations_dir(rel_path):
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

            migration_class = _find_migration_class(tree)
            if migration_class is None:
                continue

            source_lines = source.splitlines()
            has_atomic_false = _migration_has_atomic_false(migration_class)
            migration_name = filename[:-3]  # strip .py suffix

            for op_call in _extract_operations(migration_class):
                op_name = _get_operation_name(op_call)
                if op_name not in _WATCHED_OPERATIONS:
                    continue

                finding: MigrationSafetyFinding | None = None

                if op_name == "AddField":
                    finding = _build_add_field(op_call, rel_path, migration_name, source_lines)
                elif op_name == "RemoveField":
                    finding = _build_remove_field(op_call, rel_path, migration_name, source_lines)
                elif op_name == "RenameField":
                    finding = _build_rename_field(op_call, rel_path, migration_name, source_lines)
                elif op_name == "RunPython":
                    finding = _build_run_python(
                        op_call, rel_path, migration_name, has_atomic_false, source_lines
                    )
                elif op_name == "RunSQL":
                    finding = _build_run_sql(op_call, rel_path, migration_name, source_lines)

                if finding is not None:
                    findings.append(finding)

    return findings

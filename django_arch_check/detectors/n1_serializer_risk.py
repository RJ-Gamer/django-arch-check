"""N+1 Serializer Risk detector.

Detects N+1 query patterns in DRF serializers and viewsets via AST analysis.
Separate from the N+1 query risk detector which focuses on loops in views.

Pattern 1 — ORM call inside SerializerMethodField get_ method
Pattern 2 — Nested serializer field with no prefetch in paired ViewSet
Pattern 3 — Model @property with ORM call used as serializer source=
Pattern 4 — Bare queryset = Model.objects.all() with relational serializer (warning)

All patterns use the ast module only — no Django/DRF imports at analysis time.

# TODO: Tune score weight after detector is confirmed working in production.
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
class N1SerializerFinding:
    """A single N+1 serializer-risk finding."""

    detector: str                           # always "N1SerializerRisk"
    severity: Literal["error", "warning"]
    file: str                               # relative path
    line: int                               # 1-based
    message: str
    code_snippet: dict                      # {"start_line": int, "end_line": int, "lines": list[str]}


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", ".tox",
    ".venv", "venv", "env", ".env",
    "__pycache__", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "htmlcov", "dist", "build", ".eggs",
})

_ORM_ATTRS: frozenset[str] = frozenset({
    "filter", "exclude", "get", "all", "count", "exists",
    "first", "last", "values", "values_list",
})

_RELATIONAL_FIELD_NAMES: frozenset[str] = frozenset({
    "PrimaryKeyRelatedField", "StringRelatedField", "HyperlinkedRelatedField",
    "ManyRelatedField", "SlugRelatedField",
})


# ---------------------------------------------------------------------------
# Internal class-entry helper
# ---------------------------------------------------------------------------


@dataclass
class _ClassEntry:
    name: str
    rel_path: str
    source_lines: list[str]
    node: ast.ClassDef


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _base_names(cls_node: ast.ClassDef) -> frozenset[str]:
    """Return the simple name of each base class."""
    names: set[str] = set()
    for base in cls_node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return frozenset(names)


def _is_serializer(cls_node: ast.ClassDef) -> bool:
    return any("Serializer" in name for name in _base_names(cls_node))


def _is_viewset(cls_node: ast.ClassDef) -> bool:
    return any(
        "ViewSet" in name or "APIView" in name
        for name in _base_names(cls_node)
    )


def _call_func_name(call_node: ast.Call) -> str:
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _has_orm_call(func_node: ast.FunctionDef) -> bool:
    """Return True if the function body has any ORM queryset method call.

    Excludes subscript receivers (dict[key].method()) to avoid flagging
    patterns like self.context['map'].get(key, default).
    """
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _ORM_ATTRS
                and not isinstance(func.value, ast.Subscript)
            ):
                return True
    return False


def _extract_snippet(source_lines: list[str], node: ast.AST) -> dict:
    start: int = getattr(node, "lineno", 1)
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start = min(getattr(dec, "lineno", start) for dec in decorators)
    end: int = getattr(node, "end_lineno", start)
    lines = source_lines[start - 1:end]
    return {"start_line": start, "end_line": end, "lines": lines}


def _is_smf(value_node: ast.AST) -> bool:
    """Return True if value_node is a SerializerMethodField() call."""
    if not isinstance(value_node, ast.Call):
        return False
    func = value_node.func
    if isinstance(func, ast.Name):
        return func.id == "SerializerMethodField"
    if isinstance(func, ast.Attribute):
        return func.attr == "SerializerMethodField"
    return False


def _get_keyword_str(call_node: ast.Call, keyword: str) -> str | None:
    """Return the string value of a keyword argument, or None."""
    for kw in call_node.keywords:
        if kw.arg == keyword and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def _get_meta_model_name(cls_node: ast.ClassDef) -> str | None:
    """Return the class name from Meta.model = <ClassName>, or None."""
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.ClassDef) or stmt.name != "Meta":
            continue
        for inner in stmt.body:
            if not isinstance(inner, ast.Assign):
                continue
            for tgt in inner.targets:
                if not (isinstance(tgt, ast.Name) and tgt.id == "model"):
                    continue
                if isinstance(inner.value, ast.Name):
                    return inner.value.id
                if isinstance(inner.value, ast.Attribute):
                    return inner.value.attr
    return None


def _has_prefetch_for_field(node: ast.AST, field_name: str) -> bool:
    """Return True if node subtree calls prefetch_related/select_related with field_name."""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in ("prefetch_related", "select_related"):
            continue
        for arg in n.args:
            if isinstance(arg, ast.Constant) and arg.value == field_name:
                return True
    return False


def _call_attr_chain(node: ast.AST) -> list[str]:
    """Return the dotted attribute/call chain for *node*."""
    if isinstance(node, ast.Call):
        return _call_attr_chain(node.func)
    if isinstance(node, ast.Attribute):
        return _call_attr_chain(node.value) + [node.attr]
    if isinstance(node, ast.Name):
        return [node.id]
    return []


def _viewset_has_prefetch(cls_node: ast.ClassDef, field_name: str) -> bool:
    """Return True if viewset queryset or get_queryset covers field_name."""
    for stmt in cls_node.body:
        if isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "queryset":
                    if _has_prefetch_for_field(stmt.value, field_name):
                        return True
        elif isinstance(stmt, ast.FunctionDef) and stmt.name == "get_queryset":
            if _has_prefetch_for_field(stmt, field_name):
                return True
    return False


def _viewset_serializer_class_name(cls_node: ast.ClassDef) -> str | None:
    """Return the class name from serializer_class = <ClassName>, or None."""
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for tgt in stmt.targets:
            if not (isinstance(tgt, ast.Name) and tgt.id == "serializer_class"):
                continue
            if isinstance(stmt.value, ast.Name):
                return stmt.value.id
            if isinstance(stmt.value, ast.Attribute):
                return stmt.value.attr
    return None


def _queryset_is_bare(cls_node: ast.ClassDef) -> tuple[bool, int]:
    """Return (True, lineno) if viewset has a queryset assignment without prefetch."""
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for tgt in stmt.targets:
            if not (isinstance(tgt, ast.Name) and tgt.id == "queryset"):
                    continue
            if not isinstance(stmt.value, ast.Call):
                continue
            if _call_attr_chain(stmt.value)[-2:] != ["objects", "all"]:
                continue
            has_prefetch = any(
                isinstance(n, ast.Attribute)
                and n.attr in ("prefetch_related", "select_related")
                for n in ast.walk(stmt.value)
            )
            if not has_prefetch:
                return True, stmt.lineno
    return False, 0


def _serializer_has_relational_fields(cls_node: ast.ClassDef) -> bool:
    """Return True if serializer class has any relational or nested serializer fields."""
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        func_name = _call_func_name(stmt.value)
        if func_name in _RELATIONAL_FIELD_NAMES:
            return True
        if "Serializer" in func_name and func_name != "SerializerMethodField":
            return True
    return False


def _nested_serializer_fields(
    cls_node: ast.ClassDef,
) -> list[tuple[str, ast.Assign]]:
    """Return nested serializer assignments declared on *cls_node*."""
    nested_fields: list[tuple[str, ast.Assign]] = []
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        func_name = _call_func_name(stmt.value)
        if "Serializer" not in func_name or func_name == "SerializerMethodField":
            continue
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name):
                nested_fields.append((tgt.id, stmt))
    return nested_fields


# ---------------------------------------------------------------------------
# Pattern checks
# ---------------------------------------------------------------------------


def _check_pattern1(entry: _ClassEntry) -> list[N1SerializerFinding]:
    """Pattern 1: ORM call inside a SerializerMethodField get_ method."""
    if not _is_serializer(entry.node):
        return []

    smf_fields: set[str] = set()
    for stmt in entry.node.body:
        if isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and _is_smf(stmt.value):
                    smf_fields.add(tgt.id)

    findings: list[N1SerializerFinding] = []
    for stmt in entry.node.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        if not stmt.name.startswith("get_"):
            continue
        field_name = stmt.name[4:]
        if field_name not in smf_fields:
            continue
        if not _has_orm_call(stmt):
            continue
        findings.append(N1SerializerFinding(
            detector="N1SerializerRisk",
            severity="error",
            file=entry.rel_path,
            line=stmt.lineno,
            message=f"ORM call inside SerializerMethodField: {stmt.name} in {entry.node.name}",
            code_snippet=_extract_snippet(entry.source_lines, stmt),
        ))

    return findings


def _check_pattern2(
    entry: _ClassEntry,
    viewset_entries: list[_ClassEntry],
) -> list[N1SerializerFinding]:
    """Pattern 2: Nested serializer field with no prefetch in paired ViewSet."""
    if not _is_serializer(entry.node):
        return []

    nested_fields = _nested_serializer_fields(entry.node)
    if not nested_fields:
        return []

    paired_viewsets = [
        vs for vs in viewset_entries
        if _is_viewset(vs.node)
        and _viewset_serializer_class_name(vs.node) == entry.name
    ]
    if not paired_viewsets:
        return []

    findings: list[N1SerializerFinding] = []
    for field_name, stmt in nested_fields:
        snippet = {
            "start_line": stmt.lineno,
            "end_line": stmt.lineno,
            "lines": [entry.source_lines[stmt.lineno - 1]],
        }
        for vs in paired_viewsets:
            if not _viewset_has_prefetch(vs.node, field_name):
                findings.append(N1SerializerFinding(
                    detector="N1SerializerRisk",
                    severity="error",
                    file=entry.rel_path,
                    line=stmt.lineno,
                    message=(
                        f"Nested serializer field '{field_name}' in {entry.name} "
                        f"— {vs.name} has no prefetch_related()/select_related() "
                        f"for '{field_name}'"
                    ),
                    code_snippet=snippet,
                ))

    return findings


def _check_pattern3(
    entry: _ClassEntry,
    class_index: dict[str, _ClassEntry],
) -> list[N1SerializerFinding]:
    """Pattern 3: Serializer source= pointing to model @property with ORM calls."""
    if not _is_serializer(entry.node):
        return []

    model_name = _get_meta_model_name(entry.node)
    if not model_name or model_name not in class_index:
        return []

    model_entry = class_index[model_name]

    # Build index of @property methods on the model class
    model_properties: dict[str, ast.FunctionDef] = {}
    for stmt in model_entry.node.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        has_property = any(
            (isinstance(d, ast.Name) and d.id == "property")
            or (isinstance(d, ast.Attribute) and d.attr == "property")
            for d in stmt.decorator_list
        )
        if has_property:
            model_properties[stmt.name] = stmt

    findings: list[N1SerializerFinding] = []
    for stmt in entry.node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        source_val = _get_keyword_str(stmt.value, "source")
        if not source_val:
            continue
        prop_node = model_properties.get(source_val)
        if prop_node is None:
            continue
        if not _has_orm_call(prop_node):
            continue
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name):
                findings.append(N1SerializerFinding(
                    detector="N1SerializerRisk",
                    severity="error",
                    file=model_entry.rel_path,
                    line=prop_node.lineno,
                    message=(
                        f"Field '{tgt.id}' uses source='{source_val}' "
                        f"— {model_name}.{source_val} is a @property with ORM calls"
                    ),
                    code_snippet=_extract_snippet(model_entry.source_lines, prop_node),
                ))

    return findings


def _check_pattern4(
    entry: _ClassEntry,
    serializer_index: dict[str, _ClassEntry],
) -> list[N1SerializerFinding]:
    """Pattern 4 (warning): Bare queryset with relational/nested serializer."""
    if not _is_viewset(entry.node):
        return []

    is_bare, lineno = _queryset_is_bare(entry.node)
    if not is_bare:
        return []

    sc_name = _viewset_serializer_class_name(entry.node)
    if not sc_name or sc_name not in serializer_index:
        return []

    serializer_entry = serializer_index[sc_name]
    if _nested_serializer_fields(serializer_entry.node):
        return []
    if not _serializer_has_relational_fields(serializer_entry.node):
        return []

    return [N1SerializerFinding(
        detector="N1SerializerRisk",
        severity="warning",
        file=entry.rel_path,
        line=lineno,
        message=(
            f"{entry.name} has bare queryset without select_related/prefetch_related "
            f"but {sc_name} has relational fields"
        ),
        code_snippet={
            "start_line": lineno,
            "end_line": lineno,
            "lines": [entry.source_lines[lineno - 1]],
        },
    )]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(
    project_path: str,
    ignore_paths: tuple[str, ...] = (),
) -> list[N1SerializerFinding]:
    """Walk *project_path* and return all N+1 serializer-risk findings.

    Args:
        project_path: Root directory of the Django project to analyse.
        ignore_paths: File-path substrings to exclude.

    Returns:
        A list of :class:`N1SerializerFinding` instances.
    """
    all_entries: list[_ClassEntry] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        filter_dirnames(project_path, dirpath, dirnames, _SKIP_DIRS, ignore_paths)

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, project_path).replace("\\", "/")
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

            source_lines = source.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    all_entries.append(_ClassEntry(
                        name=node.name,
                        rel_path=rel_path,
                        source_lines=source_lines,
                        node=node,
                    ))

    serializer_entries = [e for e in all_entries if _is_serializer(e.node)]
    viewset_entries = [e for e in all_entries if _is_viewset(e.node)]

    # class_index: last definition of each name wins (handles multi-file projects)
    class_index: dict[str, _ClassEntry] = {e.name: e for e in all_entries}
    serializer_index: dict[str, _ClassEntry] = {e.name: e for e in serializer_entries}

    findings: list[N1SerializerFinding] = []

    for entry in serializer_entries:
        findings.extend(_check_pattern1(entry))
        findings.extend(_check_pattern2(entry, viewset_entries))
        findings.extend(_check_pattern3(entry, class_index))

    for entry in viewset_entries:
        findings.extend(_check_pattern4(entry, serializer_index))

    return findings

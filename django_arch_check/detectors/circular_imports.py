"""Circular Import detector.

Builds a directed graph of intra-project module dependencies using only
Python's ``ast`` module and the stdlib.  Detects cycles in that graph using
iterative DFS (no third-party libraries required).

Only **top-level** import statements are analysed.  Function- or method-level
imports are a valid Python pattern for deferring circular dependencies and
are intentionally excluded.

Severity: every detected cycle is ``critical``.
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from django_arch_check.detectors import filter_dirnames, should_ignore_file

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CircularImportFinding:
    """A single circular-import finding."""

    cycle_display: str  # "orders.models → payments.models → orders.models"
    severity: Literal["critical"]  # always critical — cycles are never acceptable


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


# ---------------------------------------------------------------------------
# Module-name utilities
# ---------------------------------------------------------------------------


def _file_to_module(rel_path: str) -> str:
    """Convert a relative file path to a dotted module name.

    Examples::

        orders/models.py      → orders.models
        orders/__init__.py    → orders
        core/utils/helpers.py → core.utils.helpers
    """
    module = rel_path.replace(os.sep, ".").replace("/", ".")
    if module.endswith(".py"):
        module = module[:-3]
    if module.endswith(".__init__"):
        module = module[:-9]  # strip trailing .__init__
    return module


def _is_init_file(rel_path: str) -> bool:
    """Return True if *rel_path* is a package ``__init__.py`` file."""
    return rel_path.replace(os.sep, "/").endswith("/__init__.py")


def _base_package(module: str, is_init: bool, level: int) -> str:
    """Resolve the anchor package for a relative import.

    Args:
        module:  Dotted module name of the file containing the import.
        is_init: True when the file is a package ``__init__.py``.
        level:   Number of leading dots (1 = ``.``, 2 = ``..``).

    Returns:
        The dotted package name that the relative import is anchored to.

    Examples::

        # In orders/views.py  (module="orders.views", is_init=False)
        _base_package("orders.views", False, 1)  → "orders"
        _base_package("orders.views", False, 2)  → ""

        # In orders/__init__.py  (module="orders", is_init=True)
        _base_package("orders",       True,  1)  → "orders"
        _base_package("orders",       True,  2)  → ""
    """
    parts = module.split(".")
    # For a regular file level=1 means "parent package" → remove 1 component.
    # For __init__ level=1 means "this package"         → remove 0 components.
    remove = level if not is_init else level - 1
    if remove <= 0:
        return module if is_init else ".".join(parts[:-1])
    remaining = parts[:-remove] if remove < len(parts) else []
    return ".".join(remaining)


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


def _extract_candidate_imports(
    tree: ast.Module,
    current_module: str,
    is_init: bool,
) -> list[str]:
    """Return a list of candidate module names imported at module level.

    'Candidate' means we are liberal — we add both ``X`` and ``X.name``
    for ``from X import name`` statements.  The caller filters this list
    down to modules that actually exist in the project.

    Only top-level statements are inspected (``tree.body``), not imports
    nested inside functions or classes.
    """
    candidates: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                candidates.append(alias.name)
                # Also add each dotted prefix so "import a.b.c" covers a, a.b
                parts = alias.name.split(".")
                for i in range(1, len(parts)):
                    candidates.append(".".join(parts[:i]))

        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                # Absolute import
                if node.module:
                    candidates.append(node.module)
                    for alias in node.names:
                        candidates.append(f"{node.module}.{alias.name}")
            else:
                # Relative import
                base = _base_package(current_module, is_init, node.level)
                if node.module:
                    target = f"{base}.{node.module}" if base else node.module
                    candidates.append(target)
                    for alias in node.names:
                        candidates.append(f"{target}.{alias.name}")
                else:
                    # from . import X, Y, Z
                    for alias in node.names:
                        target = f"{base}.{alias.name}" if base else alias.name
                        candidates.append(target)

    return candidates


# ---------------------------------------------------------------------------
# Cycle detection (iterative DFS)
# ---------------------------------------------------------------------------


def _find_cycles(
    graph: dict[str, set[str]],
) -> list[list[str]]:
    """Return all unique cycles in *graph* using iterative DFS.

    Each cycle is represented as a list of node names forming a closed path,
    e.g. ``["orders.models", "payments.models", "orders.models"]``.

    Cycles are deduplicated: ``A → B → C`` and ``B → C → A`` are the same
    cycle and appear only once.
    """
    # We use an explicit stack instead of recursion to avoid hitting Python's
    # default recursion limit on large import graphs.
    #
    # Stack frames: (node, iterator_over_neighbours, path_so_far)
    # When we pop a frame we remove the node from the in-path set.

    seen_canonical: set[tuple[str, ...]] = set()
    found: list[list[str]] = []
    globally_visited: set[str] = set()

    for start in sorted(graph):
        if start in globally_visited:
            continue

        # Each stack entry: (current_node, neighbour_iterator, path_list)
        path: list[str] = []
        in_path: set[str] = set()
        stack: list[tuple[str, iter, list[str]]] = []  # type: ignore[type-arg]

        stack.append((start, iter(sorted(graph.get(start, set()))), path))
        in_path.add(start)
        path.append(start)
        globally_visited.add(start)

        while stack:
            node, neighbours, _ = stack[-1]
            try:
                neighbour = next(neighbours)
            except StopIteration:
                # Exhausted all neighbours — backtrack
                stack.pop()
                if path:
                    in_path.discard(path[-1])
                    path.pop()
                continue

            if neighbour in in_path:
                # Back edge → cycle found
                cycle_start = path.index(neighbour)
                cycle = path[cycle_start:] + [neighbour]
                canonical = _canonical_cycle(cycle)
                if canonical not in seen_canonical:
                    seen_canonical.add(canonical)
                    found.append(cycle)
                # Do not descend — we've already marked the cycle

            elif neighbour not in globally_visited:
                globally_visited.add(neighbour)
                path.append(neighbour)
                in_path.add(neighbour)
                stack.append(
                    (neighbour, iter(sorted(graph.get(neighbour, set()))), path)
                )

    return found


def _canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """Normalise a cycle by rotating it to start at the lexicographically
    smallest node, so duplicate cycles can be identified."""
    nodes = cycle[:-1]  # drop the repeated last element
    if not nodes:
        return ()
    min_idx = min(range(len(nodes)), key=lambda i: nodes[i])
    rotated = nodes[min_idx:] + nodes[:min_idx]
    return tuple(rotated)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(
    project_path: str,
    ignore_paths: tuple[str, ...] = (),
) -> list[CircularImportFinding]:
    """Walk *project_path*, build a module dependency graph, and return all
    circular-import findings.

    Args:
        project_path: Root directory of the Django project to analyse.

    Returns:
        A list of :class:`CircularImportFinding` instances, one per unique
        cycle.  Empty list when no cycles are detected.
    """
    # ------------------------------------------------------------------
    # 1. Collect all .py files and build module name → rel-path map.
    # ------------------------------------------------------------------
    module_map: dict[str, str] = {}  # "orders.models" → "orders/models.py"
    is_init_map: dict[str, bool] = {}  # "orders" → True (for __init__ files)

    for dirpath, dirnames, filenames in os.walk(project_path):
        filter_dirnames(project_path, dirpath, dirnames, _SKIP_DIRS, ignore_paths)
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, project_path)
            if should_ignore_file(rel_path, ignore_paths):
                continue
            module_name = _file_to_module(rel_path)
            module_map[module_name] = rel_path
            is_init_map[module_name] = _is_init_file(rel_path)

    known_modules: set[str] = set(module_map.keys())

    # ------------------------------------------------------------------
    # 2. Parse each file and build directed dependency graph.
    #    Only add edges where the target is a known project module.
    # ------------------------------------------------------------------
    graph: dict[str, set[str]] = defaultdict(set)

    for module_name, rel_path in module_map.items():
        full_path = os.path.join(project_path, rel_path)
        try:
            with open(full_path, encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError):
            continue

        try:
            tree = ast.parse(source, filename=full_path)
        except SyntaxError:
            continue

        is_init = is_init_map[module_name]
        candidates = _extract_candidate_imports(tree, module_name, is_init)

        for candidate in candidates:
            if candidate in known_modules and candidate != module_name:
                graph[module_name].add(candidate)

        # Ensure every module appears as a graph key (even with no outgoing edges)
        # so _find_cycles can start a DFS from it.
        if module_name not in graph:
            graph[module_name] = set()

    # ------------------------------------------------------------------
    # 3. Detect cycles and build findings.
    # ------------------------------------------------------------------
    cycles = _find_cycles(dict(graph))
    findings: list[CircularImportFinding] = []

    for cycle in cycles:
        display = " → ".join(cycle)
        findings.append(
            CircularImportFinding(
                cycle_display=display,
                severity="critical",
            )
        )

    return findings

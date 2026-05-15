"""Tests for the circular-import detector."""

from __future__ import annotations

from django_arch_check.detectors.circular_imports import (
    _base_package,
    _canonical_cycle,
    _find_cycles,
    detect,
)
from tests.conftest import ProjectBuilder

# ---------------------------------------------------------------------------
# Unit tests for internal helpers
# ---------------------------------------------------------------------------


class TestBasePackage:
    def test_regular_file_level_1(self) -> None:
        assert _base_package("orders.views", False, 1) == "orders"

    def test_regular_file_level_2(self) -> None:
        assert _base_package("orders.utils.helpers", False, 2) == "orders"

    def test_init_file_level_1(self) -> None:
        # level=1 in __init__ means "this package"
        assert _base_package("orders", True, 1) == "orders"

    def test_init_file_level_2(self) -> None:
        assert _base_package("orders", True, 2) == ""

    def test_nested_package(self) -> None:
        assert _base_package("apps.orders.views", False, 1) == "apps.orders"


class TestCanonicalCycle:
    def test_already_canonical(self) -> None:
        cycle = ["a", "b", "c", "a"]
        assert _canonical_cycle(cycle) == ("a", "b", "c")

    def test_rotated_is_same(self) -> None:
        assert _canonical_cycle(["b", "c", "a", "b"]) == _canonical_cycle(
            ["a", "b", "c", "a"]
        )
        assert _canonical_cycle(["c", "a", "b", "c"]) == _canonical_cycle(
            ["a", "b", "c", "a"]
        )

    def test_single_node_cycle(self) -> None:
        result = _canonical_cycle(["a", "a"])
        assert result == ("a",)


class TestFindCycles:
    def test_two_node_cycle(self) -> None:
        graph = {"a": {"b"}, "b": {"a"}}
        cycles = _find_cycles(graph)
        assert len(cycles) == 1

    def test_three_node_cycle(self) -> None:
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        cycles = _find_cycles(graph)
        assert len(cycles) == 1

    def test_no_cycle(self) -> None:
        graph = {"a": {"b", "c"}, "b": {"c"}, "c": set()}
        assert _find_cycles(graph) == []

    def test_two_independent_cycles(self) -> None:
        # a→b→a and c→d→c
        graph = {"a": {"b"}, "b": {"a"}, "c": {"d"}, "d": {"c"}}
        cycles = _find_cycles(graph)
        assert len(cycles) == 2

    def test_no_duplicate_cycles(self) -> None:
        """A→B→C→A and B→C→A→B are the same cycle — report only once."""
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        cycles = _find_cycles(graph)
        assert len(cycles) == 1

    def test_empty_graph(self) -> None:
        assert _find_cycles({}) == []

    def test_isolated_node(self) -> None:
        assert _find_cycles({"a": set()}) == []


# ---------------------------------------------------------------------------
# Integration tests via detect()
# ---------------------------------------------------------------------------


def test_two_module_cycle(proj: ProjectBuilder) -> None:
    """orders.models ↔ payments.models → 1 critical finding."""
    proj.write("orders/__init__.py", "")
    proj.write("orders/models.py", "from payments.models import Payment\n")
    proj.write("payments/__init__.py", "")
    proj.write("payments/models.py", "from orders.models import Order\n")
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert "orders.models" in findings[0].cycle_display
    assert "payments.models" in findings[0].cycle_display


def test_three_module_cycle(proj: ProjectBuilder) -> None:
    """alpha → beta → gamma → alpha → 1 finding."""
    proj.write("alpha/__init__.py", "")
    proj.write("alpha/models.py", "from beta.models import B\n")
    proj.write("beta/__init__.py", "")
    proj.write("beta/models.py", "from gamma.models import C\n")
    proj.write("gamma/__init__.py", "")
    proj.write("gamma/models.py", "from alpha.models import A\n")
    findings = detect(proj.path)
    assert len(findings) == 1


def test_no_cycle_dag(proj: ProjectBuilder) -> None:
    """A → B → C with no back edges → no findings."""
    proj.write("a/__init__.py", "")
    proj.write("a/models.py", "from b.models import BModel\n")
    proj.write("b/__init__.py", "")
    proj.write("b/models.py", "from c.models import CModel\n")
    proj.write("c/__init__.py", "")
    proj.write("c/models.py", "x = 1\n")
    assert detect(proj.path) == []


def test_external_stdlib_and_django_imports_ignored(proj: ProjectBuilder) -> None:
    """Imports of django, os, sys etc. must not create graph edges."""
    proj.write("myapp/__init__.py", "")
    proj.write(
        "myapp/models.py",
        "import os\nimport sys\nfrom django.db import models\nfrom collections import defaultdict\n",
    )
    assert detect(proj.path) == []


def test_relative_import_resolved(proj: ProjectBuilder) -> None:
    """``from . import models`` resolves to the sibling module."""
    proj.write("orders/__init__.py", "")
    proj.write("orders/models.py", "from django.db import models\n")
    proj.write("orders/views.py", "from . import models\n")
    # No cycle: views → models, models has no back edge
    assert detect(proj.path) == []


def test_relative_import_creates_cycle(proj: ProjectBuilder) -> None:
    """``from . import views`` in models.py creates a cycle."""
    proj.write("orders/__init__.py", "")
    proj.write("orders/models.py", "from . import views\n")
    proj.write("orders/views.py", "from . import models\n")
    findings = detect(proj.path)
    assert len(findings) == 1


def test_two_separate_cycles_both_reported(proj: ProjectBuilder) -> None:
    """Two independent cycles each produce a separate finding."""
    # Cycle 1: a ↔ b
    proj.write("a/__init__.py", "")
    proj.write("a/models.py", "from b.models import B\n")
    proj.write("b/__init__.py", "")
    proj.write("b/models.py", "from a.models import A\n")
    # Cycle 2: c ↔ d
    proj.write("c/__init__.py", "")
    proj.write("c/models.py", "from d.models import D\n")
    proj.write("d/__init__.py", "")
    proj.write("d/models.py", "from c.models import C\n")
    findings = detect(proj.path)
    assert len(findings) == 2


def test_function_level_import_ignored(proj: ProjectBuilder) -> None:
    """Imports inside a function body must not be added to the graph."""
    proj.write("orders/__init__.py", "")
    proj.write(
        "orders/models.py",
        textwrap.dedent("""\
        from django.db import models

        def get_payment():
            from payments.models import Payment  # function-level — not top-level
            return Payment
    """),
    )
    proj.write("payments/__init__.py", "")
    proj.write("payments/models.py", "from orders.models import Order\n")
    # payments→orders exists, but orders→payments is function-level so should NOT be an edge
    # Therefore no cycle should be detected
    assert detect(proj.path) == []


def test_syntax_error_file_skipped_gracefully(proj: ProjectBuilder) -> None:
    proj.write("bad/__init__.py", "")
    proj.write("bad/models.py", ")(][this is not python")
    assert detect(proj.path) == []


import textwrap  # noqa: E402 — needed by test_function_level_import_ignored

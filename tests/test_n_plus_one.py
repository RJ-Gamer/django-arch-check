"""Tests for the N+1 query-risk detector."""

from __future__ import annotations

import textwrap

from django_arch_check.detectors.n_plus_one import detect
from tests.conftest import ProjectBuilder

# ---------------------------------------------------------------------------
# For-loop findings
# ---------------------------------------------------------------------------


def test_orm_inside_for_loop_is_warning(proj: ProjectBuilder) -> None:
    """ORM queryset method call inside a for-loop → warning."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order, Item
        def list_view(request):
            orders = Order.objects.all()
            for order in orders:
                items = Item.objects.filter(order=order)
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "warning"


def test_correct_line_number_for_loop(proj: ProjectBuilder) -> None:
    """line_number should point to the for-loop line, not the ORM call inside."""
    source = textwrap.dedent("""\
        from orders.models import Order, Item
        def view(request):
            orders = Order.objects.all()
            for order in orders:
                items = Item.objects.filter(order=order)
    """)
    proj.write("orders/views.py", source)
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].line_number == 4  # the "for order in orders:" line


# ---------------------------------------------------------------------------
# List comprehension findings
# ---------------------------------------------------------------------------


def test_orm_inside_listcomp_is_warning(proj: ProjectBuilder) -> None:
    """ORM call in a list-comprehension element → warning."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order, Item
        def view(request):
            orders = Order.objects.all()
            counts = [Item.objects.filter(order=o).count() for o in orders]
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# select_related / prefetch_related suppresses findings
# ---------------------------------------------------------------------------


def test_select_related_suppresses_finding(proj: ProjectBuilder) -> None:
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order, Item
        def view(request):
            orders = Order.objects.select_related('user').all()
            for order in orders:
                items = Item.objects.filter(order=order)
    """),
    )
    assert detect(proj.path) == []


def test_prefetch_related_suppresses_finding(proj: ProjectBuilder) -> None:
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order
        def view(request):
            orders = Order.objects.prefetch_related('items').all()
            for order in orders:
                items = order.items.all()
    """),
    )
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Clean loops
# ---------------------------------------------------------------------------


def test_loop_without_orm_is_clean(proj: ProjectBuilder) -> None:
    """A for-loop that doesn't touch the ORM must not produce a finding."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        def view(request):
            items = [1, 2, 3]
            for item in items:
                print(item)
    """),
    )
    assert detect(proj.path) == []


def test_listcomp_without_orm_is_clean(proj: ProjectBuilder) -> None:
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        def view(request):
            doubled = [x * 2 for x in range(10)]
    """),
    )
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Scoping: only views.py and serializers.py
# ---------------------------------------------------------------------------


def test_serializer_file_scanned(proj: ProjectBuilder) -> None:
    """serializers.py is also scanned for N+1 risks."""
    proj.write(
        "orders/serializers.py",
        textwrap.dedent("""\
        from orders.models import Item
        class OrderSerializer:
            def serialize(self, orders):
                for order in orders:
                    items = Item.objects.filter(order=order)
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1


def test_models_file_not_scanned(proj: ProjectBuilder) -> None:
    """ORM loops inside models.py must not produce findings."""
    proj.write(
        "orders/models.py",
        textwrap.dedent("""\
        from django.db import models
        class Order(models.Model):
            def get_all_items(self):
                items = []
                for pk in self.item_pks:
                    items.append(Item.objects.get(pk=pk))
                return items
    """),
    )
    assert detect(proj.path) == []


def test_tasks_file_not_scanned(proj: ProjectBuilder) -> None:
    proj.write(
        "orders/tasks.py",
        textwrap.dedent("""\
        from orders.models import Order, Item
        def process():
            for o in Order.objects.all():
                Item.objects.filter(order=o).delete()
    """),
    )
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Multiple findings in one file
# ---------------------------------------------------------------------------


def test_multiple_risky_loops_same_file(proj: ProjectBuilder) -> None:
    """Two separate risky loops → two findings."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order, Item
        def view_a(request):
            for o in Order.objects.all():
                Item.objects.filter(order=o)

        def view_b(request):
            for o in Order.objects.all():
                Item.objects.get(pk=o.id)
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 2


def test_one_function_with_prefetch_one_without(proj: ProjectBuilder) -> None:
    """Suppression is per-function; another function without prefetch is still flagged."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order, Item
        def safe_view(request):
            orders = Order.objects.prefetch_related('items')
            for order in orders:
                items = Item.objects.filter(order=order)

        def risky_view(request):
            for order in Order.objects.all():
                items = Item.objects.filter(order=order)
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].line_number > 1  # from risky_view, not safe_view


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_skip_dirs_not_scanned(proj: ProjectBuilder) -> None:
    proj.write(
        ".venv/app/views.py",
        textwrap.dedent("""\
        from orders.models import Order, Item
        def view(request):
            for o in Order.objects.all():
                Item.objects.filter(order=o)
    """),
    )
    assert detect(proj.path) == []


def test_syntax_error_file_skipped(proj: ProjectBuilder) -> None:
    proj.write("orders/views.py", ")(][ not python")
    assert detect(proj.path) == []


def test_file_path_is_relative(proj: ProjectBuilder) -> None:
    proj.write(
        "myapp/views.py",
        textwrap.dedent("""\
        from myapp.models import M, N
        def v(r):
            for m in M.objects.all():
                N.objects.filter(parent=m)
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert not findings[0].file_path.startswith("/")

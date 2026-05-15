"""Tests for the missing-service-layer detector."""

from __future__ import annotations

import textwrap

from django_arch_check.detectors.missing_service_layer import detect
from tests.conftest import ProjectBuilder


def _views(content: str, app: str = "orders") -> ProjectBuilder:
    """Return a writer function; used as a pattern inside tests."""
    return content  # caller does proj.write(...)


# ---------------------------------------------------------------------------
# Warning: short view with direct ORM call
# ---------------------------------------------------------------------------


def test_warning_short_view_with_orm(proj: ProjectBuilder) -> None:
    """A short view that calls Model.objects.* → warning."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order
        def order_list(request):
            return Order.objects.all()
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    f = findings[0]
    assert f.view_name == "order_list"
    assert f.severity == "warning"
    assert f.has_orm_calls is True


def test_warning_cbv_method_qualified_name(proj: ProjectBuilder) -> None:
    """A CBV method with ORM calls uses ClassName.method format."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order
        class OrderView:
            def get(self, request):
                return Order.objects.filter(status='active')
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].view_name == "OrderView.get"


# ---------------------------------------------------------------------------
# Critical: long view with ORM call
# ---------------------------------------------------------------------------


def test_critical_long_view_with_orm(proj: ProjectBuilder) -> None:
    """A view with > 10 body lines AND ORM calls → critical."""
    lines = [
        "from orders.models import Order",
        "def fat_view(request):",
    ]
    for i in range(15):
        lines.append(f"    x_{i} = {i}")
    lines.append("    return Order.objects.all()")
    source = "\n".join(lines) + "\n"
    proj.write("orders/views.py", source)
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert findings[0].line_count > 10


def test_threshold_boundary_exactly_at_threshold(proj: ProjectBuilder) -> None:
    """A view with exactly line_threshold lines (not exceeding) → warning, not critical."""
    # Default threshold = 10; body needs exactly 10 lines (10 > 10 is False → warning)
    # range(9) gives 9 assignment lines + 1 return = 10 body lines
    lines = ["from orders.models import Order", "def view(request):"]
    for i in range(9):
        lines.append(f"    y_{i} = {i}")
    lines.append("    return Order.objects.get(pk=1)")
    source = "\n".join(lines) + "\n"
    proj.write("orders/views.py", source)
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "warning"


# ---------------------------------------------------------------------------
# Clean: no ORM call in view
# ---------------------------------------------------------------------------


def test_clean_view_no_orm(proj: ProjectBuilder) -> None:
    """A view with no ORM calls must not be flagged."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from django.http import JsonResponse
        def health_check(request):
            return JsonResponse({'status': 'ok'})
    """),
    )
    assert detect(proj.path) == []


def test_cbv_method_without_orm_not_flagged(proj: ProjectBuilder) -> None:
    """A CBV method that does not touch the ORM must be clean."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        class PingView:
            def get(self, request):
                return 'pong'
    """),
    )
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Scoping: only views.py files are scanned
# ---------------------------------------------------------------------------


def test_models_file_not_scanned(proj: ProjectBuilder) -> None:
    """ORM calls inside models.py must not produce findings."""
    proj.write(
        "orders/models.py",
        textwrap.dedent("""\
        from django.db import models
        class Order(models.Model):
            def get_items(self):
                return Item.objects.filter(order=self)
    """),
    )
    assert detect(proj.path) == []


def test_tasks_file_not_scanned(proj: ProjectBuilder) -> None:
    """ORM calls inside tasks.py must not produce findings."""
    proj.write(
        "orders/tasks.py",
        "from orders.models import Order\ndef do(): return Order.objects.all()\n",
    )
    assert detect(proj.path) == []


def test_non_py_file_ignored(proj: ProjectBuilder) -> None:
    proj.write("orders/views.txt", "Order.objects.all()")
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_async_view_detected(proj: ProjectBuilder) -> None:
    """async def views are also checked for ORM calls."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order
        async def async_list(request):
            return Order.objects.all()
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].view_name == "async_list"


def test_multiple_views_in_one_file(proj: ProjectBuilder) -> None:
    """Each view function in a file is evaluated independently."""
    proj.write(
        "orders/views.py",
        textwrap.dedent("""\
        from orders.models import Order
        def clean_view(request):
            return 'ok'
        def dirty_view(request):
            return Order.objects.all()
    """),
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].view_name == "dirty_view"


def test_file_path_is_relative(proj: ProjectBuilder) -> None:
    proj.write(
        "myapp/views.py",
        "from myapp.models import M\ndef v(r): return M.objects.all()\n",
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert not findings[0].file_path.startswith("/")

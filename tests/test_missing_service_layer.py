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
    """A view with 2+ ORM calls → warning (new threshold is 2)."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from orders.models import Order, Item
        def order_list(request):
            orders = Order.objects.all()
            items = Item.objects.filter(order__in=orders)
            return items
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    f = findings[0]
    assert f.view_name == "order_list"
    assert f.severity == "warning"
    assert f.has_orm_calls is True
    assert f.orm_call_count == 2


def test_warning_cbv_method_qualified_name(proj: ProjectBuilder) -> None:
    """A CBV method with 2+ ORM calls uses ClassName.method format."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from orders.models import Order, Item
        class OrderView:
            def list(self, request):          # ← not exempt
                orders = Order.objects.filter(status='active')
                items = Item.objects.all()
                return orders
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].view_name == "OrderView.list"
    assert findings[0].orm_call_count == 2

# ---------------------------------------------------------------------------
# Critical: long view with ORM call
# ---------------------------------------------------------------------------

def test_critical_view_with_four_or_more_orm_calls(proj: ProjectBuilder) -> None:
    """A view with 4+ ORM calls → critical (new threshold)."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from orders.models import Order, Item, User, Payment
        def fat_view(request):
            orders = Order.objects.all()
            items = Item.objects.filter(order__in=orders)
            users = User.objects.filter(is_active=True)
            payments = Payment.objects.filter(status='paid')
            return orders
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert findings[0].orm_call_count == 4


def test_threshold_boundary_three_orm_calls_is_warning(proj: ProjectBuilder) -> None:
    """Exactly 3 ORM calls → warning (warning=2+, critical=4+)."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from orders.models import Order, Item, User
        def view(request):
            orders = Order.objects.all()
            items = Item.objects.filter(order__in=orders)
            users = User.objects.filter(is_active=True)
            return orders
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].orm_call_count == 3


# ---------------------------------------------------------------------------
# Clean: no ORM call in view
# ---------------------------------------------------------------------------

def test_clean_view_no_orm(proj: ProjectBuilder) -> None:
    """A view with no ORM calls must not be flagged."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from django.http import JsonResponse
        def health_check(request):
            return JsonResponse({'status': 'ok'})
    """))
    assert detect(proj.path) == []


def test_single_orm_call_not_flagged(proj: ProjectBuilder) -> None:
    """A view with only 1 ORM call is below the warning threshold → clean."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from orders.models import Order
        def order_list(request):
            return Order.objects.all()  # 1 call — under warning threshold of 2
    """))
    assert detect(proj.path) == []


def test_context_dict_building_not_flagged(proj: ProjectBuilder) -> None:
    """A view long only because of context dict assignments → clean."""
    lines = [
        "def context_view(request):",
    ]
    for i in range(20):
        lines.append(f"    context_{'key' + str(i)} = {i}")
    lines.append("    return context_key0")
    proj.write("orders/views.py", "\n".join(lines) + "\n")
    assert detect(proj.path) == []


def test_cbv_method_without_orm_not_flagged(proj: ProjectBuilder) -> None:
    """A CBV method that does not touch the ORM must be clean."""
    proj.write("orders/views.py", textwrap.dedent("""\
        class PingView:
            def get(self, request):
                return 'pong'
    """))
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Scoping: only views.py files are scanned
# ---------------------------------------------------------------------------

def test_models_file_not_scanned(proj: ProjectBuilder) -> None:
    """ORM calls inside models.py must not produce findings."""
    proj.write("orders/models.py", textwrap.dedent("""\
        from django.db import models
        class Order(models.Model):
            def get_items(self):
                return Item.objects.filter(order=self)
    """))
    assert detect(proj.path) == []


def test_tasks_file_not_scanned(proj: ProjectBuilder) -> None:
    """ORM calls inside tasks.py must not produce findings."""
    proj.write("orders/tasks.py", "from orders.models import Order\ndef do(): return Order.objects.all()\n")
    assert detect(proj.path) == []


def test_non_py_file_ignored(proj: ProjectBuilder) -> None:
    proj.write("orders/views.txt", "Order.objects.all()")
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_async_view_detected(proj: ProjectBuilder) -> None:
    """async def views with 2+ ORM calls are also checked."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from orders.models import Order, Item
        async def async_list(request):
            orders = Order.objects.all()
            items = Item.objects.filter(order__in=orders)
            return orders
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].view_name == "async_list"


def test_multiple_views_in_one_file(proj: ProjectBuilder) -> None:
    """Each view function in a file is evaluated independently."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from orders.models import Order, Item
        def clean_view(request):
            return Order.objects.all()  # only 1 ORM call — clean
        def dirty_view(request):
            orders = Order.objects.all()
            items = Item.objects.filter(order__in=orders)
            return orders
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].view_name == "dirty_view"


def test_file_path_is_relative(proj: ProjectBuilder) -> None:
    proj.write("myapp/views.py",
        "from myapp.models import M, N\n"
        "def v(r):\n"
        "    a = M.objects.all()\n"
        "    b = N.objects.filter(x=1)\n"
        "    return a\n"
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert not findings[0].file_path.startswith("/")
    

def test_exempt_drf_methods_not_flagged(proj: ProjectBuilder) -> None:
    """DRF override methods with ORM calls must not be flagged."""
    proj.write("orders/views.py", textwrap.dedent("""\
        from orders.models import Order, Item, User, Payment
        class OrderViewSet:
            def get_queryset(self):
                return Order.objects.filter(
                    user=self.request.user
                ).select_related('user').prefetch_related('items')

            def perform_create(self, serializer):
                user = User.objects.get(id=self.request.user.id)
                payment = Payment.objects.filter(user=user).first()
                serializer.save(user=user, payment=payment)

            def list(self, request):
                orders = Order.objects.all()
                items = Item.objects.filter(order__in=orders)
                return orders
    """))
    findings = detect(proj.path)
    # get_queryset and perform_create are exempt — only list() should be flagged
    assert len(findings) == 1
    assert findings[0].view_name == "OrderViewSet.list"

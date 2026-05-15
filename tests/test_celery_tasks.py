"""Tests for the Celery-tasks-without-retry detector."""

from __future__ import annotations

import textwrap

import pytest

from django_arch_check.detectors.celery_tasks import detect
from tests.conftest import ProjectBuilder

# ---------------------------------------------------------------------------
# Basic severity rules
# ---------------------------------------------------------------------------

def test_generic_task_no_retry_is_warning(proj: ProjectBuilder) -> None:
    """A @shared_task with no retry config and a generic name → warning."""
    proj.write("reports/tasks.py", textwrap.dedent("""\
        from celery import shared_task

        @shared_task
        def generate_report(report_id):
            pass
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    f = findings[0]
    assert f.task_name == "generate_report"
    assert f.severity == "warning"


@pytest.mark.parametrize("name,keyword", [
    ("process_payment",       "payment"),
    ("send_invoice_email",    "email"),
    ("process_invoice",       "invoice"),
    ("push_notification",     "notification"),
    ("SEND_PAYMENT_CONFIRM",  "payment"),   # case-insensitive match
])
def test_high_stakes_task_is_critical(proj: ProjectBuilder, name: str, keyword: str) -> None:
    """Tasks whose name contains a high-stakes keyword and lack retry → critical."""
    proj.write("app/tasks.py", textwrap.dedent(f"""\
        from celery import shared_task

        @shared_task
        def {name}(task_id):
            pass
    """))
    findings = detect(proj.path)
    assert len(findings) == 1, f"Expected 1 finding for {name!r}"
    assert findings[0].severity == "critical", f"{name!r} should be critical (keyword={keyword!r})"


# ---------------------------------------------------------------------------
# Retry configuration suppresses findings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwarg", ["max_retries=3", "autoretry_for=(Exception,)", "retry_backoff=True"])
def test_retry_kwarg_suppresses_finding(proj: ProjectBuilder, kwarg: str) -> None:
    """A task with any recognised retry kwarg must not be flagged."""
    proj.write("payments/tasks.py", textwrap.dedent(f"""\
        from celery import shared_task

        @shared_task({kwarg})
        def charge_customer(payment_id):
            pass
    """))
    assert detect(proj.path) == []


def test_app_task_decorator_detected(proj: ProjectBuilder) -> None:
    """@app.task (attribute form) is also matched."""
    proj.write("payments/tasks.py", textwrap.dedent("""\
        from myapp import app

        @app.task
        def process_invoice(invoice_id):
            pass
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "critical"


def test_app_task_with_retry_is_clean(proj: ProjectBuilder) -> None:
    proj.write("payments/tasks.py", textwrap.dedent("""\
        from myapp import app

        @app.task(max_retries=5, autoretry_for=(Exception,))
        def charge_customer(payment_id):
            pass
    """))
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Non-task functions must not be flagged
# ---------------------------------------------------------------------------

def test_regular_function_ignored(proj: ProjectBuilder) -> None:
    """A function without @task or @shared_task decorator → no finding."""
    proj.write("utils/helpers.py", textwrap.dedent("""\
        def send_payment_email(user_id):
            pass
    """))
    assert detect(proj.path) == []


def test_class_method_ignored(proj: ProjectBuilder) -> None:
    """Methods on classes are not Celery tasks."""
    proj.write("services/email.py", textwrap.dedent("""\
        class EmailService:
            def send_payment_email(self, user_id):
                pass
    """))
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Multiple tasks in one file
# ---------------------------------------------------------------------------

def test_only_unprotected_tasks_flagged(proj: ProjectBuilder) -> None:
    """Tasks with retry config are clean; others are flagged."""
    proj.write("payments/tasks.py", textwrap.dedent("""\
        from celery import shared_task

        @shared_task(max_retries=3)
        def safe_task(x): pass

        @shared_task
        def send_payment_email(x): pass

        @shared_task
        def generate_report(x): pass
    """))
    findings = detect(proj.path)
    names = {f.task_name for f in findings}
    assert names == {"send_payment_email", "generate_report"}
    severities = {f.task_name: f.severity for f in findings}
    assert severities["send_payment_email"] == "critical"
    assert severities["generate_report"] == "warning"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_async_task_detected(proj: ProjectBuilder) -> None:
    """async def tasks are also checked."""
    proj.write("app/tasks.py", textwrap.dedent("""\
        from celery import shared_task

        @shared_task
        async def process_invoice(invoice_id):
            pass
    """))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "critical"


def test_file_path_is_relative(proj: ProjectBuilder) -> None:
    proj.write("payments/tasks.py", "@shared_task\ndef t(): pass\nfrom celery import shared_task\n")
    # Rewrite with correct order
    proj.write("payments/tasks.py", "from celery import shared_task\n@shared_task\ndef t(): pass\n")
    findings = detect(proj.path)
    assert len(findings) == 1
    assert not findings[0].file_path.startswith("/")


def test_skip_dirs_not_scanned(proj: ProjectBuilder) -> None:
    proj.write(".venv/celery/tasks.py", "from celery import shared_task\n@shared_task\ndef send_payment_email(): pass\n")
    assert detect(proj.path) == []


def test_migration_file_skipped(proj: ProjectBuilder) -> None:
    """Task-decorated functions inside migrations/ must not be flagged."""
    proj.write("orders/migrations/__init__.py", "")
    proj.write("orders/migrations/0001_initial.py",
        "from celery import shared_task\n"
        "@shared_task\n"
        "def send_payment_email(user_id): pass\n"  # would be critical outside migrations
    )
    assert detect(proj.path) == []


def test_migration_sibling_still_flagged(proj: ProjectBuilder) -> None:
    """A tasks.py next to migrations/ must still be scanned."""
    proj.write("orders/migrations/__init__.py", "")
    proj.write("orders/migrations/0001_initial.py",
        "from celery import shared_task\n@shared_task\ndef send_payment_email(): pass\n"
    )
    proj.write("orders/tasks.py",
        "from celery import shared_task\n@shared_task\ndef send_payment_email(): pass\n"
    )
    findings = detect(proj.path)
    assert len(findings) == 1
    assert "migrations" not in findings[0].file_path


def test_windows_path_migration_skipped(proj: ProjectBuilder) -> None:
    """Migration detection must work when rel_path uses backslashes (Windows)."""
    from django_arch_check.detectors.celery_tasks import _is_migration_file
    assert _is_migration_file("orders\\migrations\\0001_initial.py") is True
    assert _is_migration_file("orders\\tasks.py") is False
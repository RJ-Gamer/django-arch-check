"""Tests for the direct-SQL detector."""

from __future__ import annotations

import textwrap

import pytest

from django_arch_check.detectors.direct_sql import _is_migration_file, detect
from tests.conftest import ProjectBuilder

# ---------------------------------------------------------------------------
# Unit test: migration path detection
# ---------------------------------------------------------------------------


class TestIsMigrationFile:
    def test_direct_migrations_child(self) -> None:
        assert _is_migration_file("orders/migrations/0001_initial.py") is True

    def test_nested_migrations(self) -> None:
        assert _is_migration_file("apps/orders/migrations/0002.py") is True

    def test_regular_file(self) -> None:
        assert _is_migration_file("orders/models.py") is False

    def test_file_named_migrations_py(self) -> None:
        # A file literally named migrations.py at the app root — not a migrations dir
        assert _is_migration_file("orders/migrations.py") is False


# ---------------------------------------------------------------------------
# All four patterns detected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,source_line",
    [
        ("cursor.execute(", "    cursor.execute('SELECT 1')"),
        ("connection.cursor()", "cursor = connection.cursor()"),
        (".raw(", "    qs = Order.objects.raw('SELECT * FROM orders')"),
        (".extra(select=", "    qs = Order.objects.filter().extra(select={'x': '1'})"),
    ],
)
def test_each_pattern_detected(
    proj: ProjectBuilder, pattern: str, source_line: str
) -> None:
    source = f"from django.db import connection\n{source_line}\n"
    proj.write("orders/views.py", source)
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].pattern == pattern
    assert findings[0].severity == "warning"


# ---------------------------------------------------------------------------
# Line number accuracy
# ---------------------------------------------------------------------------


def test_correct_line_number(proj: ProjectBuilder) -> None:
    """Line numbers in findings must match actual source positions."""
    source = textwrap.dedent("""\
        # line 1: comment
        x = 1
        cursor.execute('SELECT 1')
        y = 2
    """)
    proj.write("app/models.py", source)
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].line_number == 3


def test_multiple_patterns_in_same_file(proj: ProjectBuilder) -> None:
    """Each matching line produces one finding; two lines → two findings."""
    source = textwrap.dedent("""\
        from django.db import connection
        cursor = connection.cursor()
        cursor.execute('UPDATE foo SET x=1')
    """)
    proj.write("app/views.py", source)
    findings = detect(proj.path)
    assert len(findings) == 2
    line_numbers = {f.line_number for f in findings}
    assert line_numbers == {2, 3}


def test_one_finding_per_line_not_double_reported(proj: ProjectBuilder) -> None:
    """If two patterns appear on the same line, only the first match is reported."""
    # cursor.execute( contains both .execute and cursor
    proj.write("app/views.py", "    cursor.execute(connection.cursor())\n")
    findings = detect(proj.path)
    # Only one finding for that line, not two
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Migration files are excluded
# ---------------------------------------------------------------------------


def test_migration_file_skipped(proj: ProjectBuilder) -> None:
    proj.write("orders/migrations/__init__.py", "")
    proj.write(
        "orders/migrations/0001_initial.py",
        textwrap.dedent("""\
        from django.db import migrations
        def forwards(apps, schema_editor):
            schema_editor.execute('ALTER TABLE orders ADD COLUMN x INT')
            cursor = schema_editor.connection.cursor()
            cursor.execute('UPDATE orders SET x = 1')
    """),
    )
    assert detect(proj.path) == []


def test_non_migration_file_with_same_patterns_flagged(proj: ProjectBuilder) -> None:
    """The same patterns outside migrations/ must be flagged."""
    proj.write("orders/models.py", "cursor.execute('SELECT 1')\n")
    findings = detect(proj.path)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Skip dirs and non-py files
# ---------------------------------------------------------------------------


def test_venv_files_not_scanned(proj: ProjectBuilder) -> None:
    proj.write(".venv/lib/django/db.py", "cursor.execute('SELECT 1')\n")
    assert detect(proj.path) == []


def test_non_py_file_not_scanned(proj: ProjectBuilder) -> None:
    proj.write("orders/views.txt", "cursor.execute('SELECT 1')\n")
    assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Severity is always warning
# ---------------------------------------------------------------------------


def test_severity_always_warning(proj: ProjectBuilder) -> None:
    proj.write("core/models.py", "cursor.execute('DROP TABLE orders')\n")
    findings = detect(proj.path)
    assert all(f.severity == "warning" for f in findings)


# ---------------------------------------------------------------------------
# Relative file path
# ---------------------------------------------------------------------------


def test_file_path_is_relative(proj: ProjectBuilder) -> None:
    proj.write("myapp/views.py", "cursor.execute('SELECT 1')\n")
    findings = detect(proj.path)
    assert len(findings) == 1
    assert not findings[0].file_path.startswith("/")
    assert "myapp" in findings[0].file_path

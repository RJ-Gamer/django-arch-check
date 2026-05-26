"""Tests for the migration-safety detector."""

from __future__ import annotations

import textwrap

from django_arch_check.detectors.migration_safety import (
    _is_in_migrations_dir,
    detect,
)
from tests.conftest import ProjectBuilder

# ---------------------------------------------------------------------------
# Unit tests: _is_in_migrations_dir
# ---------------------------------------------------------------------------


class TestIsMigrationDir:
    def test_direct_child_of_migrations(self) -> None:
        assert _is_in_migrations_dir("orders/migrations/0001_initial.py") is True

    def test_nested_app_migrations(self) -> None:
        assert _is_in_migrations_dir("apps/orders/migrations/0002_add_field.py") is True

    def test_regular_models_file(self) -> None:
        assert _is_in_migrations_dir("orders/models.py") is False

    def test_file_named_migrations_py(self) -> None:
        # A file literally named migrations.py — NOT a migrations directory
        assert _is_in_migrations_dir("orders/migrations.py") is False

    def test_windows_backslash_path(self) -> None:
        assert _is_in_migrations_dir("orders\\migrations\\0001_initial.py") is True

    def test_tasks_file_next_to_migrations(self) -> None:
        assert _is_in_migrations_dir("orders/tasks.py") is False


# ---------------------------------------------------------------------------
# AddField
# ---------------------------------------------------------------------------


class TestAddField:
    def test_not_null_no_default_is_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_add_phone.py", textwrap.dedent("""\
            from django.db import migrations, models
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.AddField(
                        model_name='order',
                        name='phone_number',
                        field=models.CharField(max_length=20),
                    ),
                ]
        """))
        findings = detect(proj.path)
        assert len(findings) == 1
        f = findings[0]
        assert f.operation == "AddField"
        assert f.model_name == "order"
        assert f.field_name == "phone_number"
        assert f.severity == "warning"
        assert "NOT NULL" in f.message

    def test_null_true_is_safe(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_add_phone.py", textwrap.dedent("""\
            from django.db import migrations, models
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.AddField(
                        model_name='order',
                        name='phone_number',
                        field=models.CharField(max_length=20, null=True),
                    ),
                ]
        """))
        assert detect(proj.path) == []

    def test_with_default_is_safe(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_add_status.py", textwrap.dedent("""\
            from django.db import migrations, models
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.AddField(
                        model_name='order',
                        name='status',
                        field=models.CharField(max_length=20, default='pending'),
                    ),
                ]
        """))
        assert detect(proj.path) == []

    def test_null_true_and_default_both_safe(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_add.py", textwrap.dedent("""\
            from django.db import migrations, models
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.AddField(
                        model_name='order',
                        name='notes',
                        field=models.TextField(null=True, default=''),
                    ),
                ]
        """))
        assert detect(proj.path) == []

    def test_variable_field_not_flagged(self, proj: ProjectBuilder) -> None:
        """Field passed as a variable cannot be statically inspected — no false positive."""
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_add.py", textwrap.dedent("""\
            from django.db import migrations, models
            MY_FIELD = models.CharField(max_length=20)
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.AddField(
                        model_name='order',
                        name='phone',
                        field=MY_FIELD,
                    ),
                ]
        """))
        # Can't inspect variable — should not flag
        assert detect(proj.path) == []

    def test_ignore_comment_suppresses(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_add.py", textwrap.dedent("""\
            from django.db import migrations, models
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.AddField(  # django-arch-check: ignore
                        model_name='order',
                        name='phone_number',
                        field=models.CharField(max_length=20),
                    ),
                ]
        """))
        assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# RemoveField
# ---------------------------------------------------------------------------


class TestRemoveField:
    def test_always_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0002_remove_phone.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RemoveField(
                        model_name='order',
                        name='legacy_status',
                    ),
                ]
        """))
        findings = detect(proj.path)
        assert len(findings) == 1
        f = findings[0]
        assert f.operation == "RemoveField"
        assert f.model_name == "order"
        assert f.field_name == "legacy_status"
        assert f.severity == "warning"
        assert "irreversible" in f.message

    def test_ignore_comment_suppresses(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0002_remove.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RemoveField(  # django-arch-check: ignore
                        model_name='order',
                        name='legacy_status',
                    ),
                ]
        """))
        assert detect(proj.path) == []

    def test_migration_name_is_filename_without_extension(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0042_remove_old_field.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RemoveField(model_name='order', name='x'),
                ]
        """))
        findings = detect(proj.path)
        assert findings[0].migration_name == "0042_remove_old_field"


# ---------------------------------------------------------------------------
# RenameField
# ---------------------------------------------------------------------------


class TestRenameField:
    def test_always_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0003_rename_phone.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RenameField(
                        model_name='order',
                        old_name='phone',
                        new_name='phone_number',
                    ),
                ]
        """))
        findings = detect(proj.path)
        assert len(findings) == 1
        f = findings[0]
        assert f.operation == "RenameField"
        assert f.model_name == "order"
        assert f.severity == "warning"

    def test_field_name_uses_arrow_notation(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0003_rename.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RenameField(
                        model_name='user',
                        old_name='fname',
                        new_name='first_name',
                    ),
                ]
        """))
        findings = detect(proj.path)
        assert findings[0].field_name == "fname → first_name"

    def test_ignore_comment_suppresses(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0003_rename.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RenameField(  # django-arch-check: ignore
                        model_name='order',
                        old_name='phone',
                        new_name='phone_number',
                    ),
                ]
        """))
        assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# RunPython
# ---------------------------------------------------------------------------


class TestRunPython:
    def test_without_atomic_false_is_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0004_data_migration.py", textwrap.dedent("""\
            from django.db import migrations

            def forward(apps, schema_editor):
                Order = apps.get_model('orders', 'Order')
                Order.objects.update(status='pending')

            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RunPython(forward),
                ]
        """))
        findings = detect(proj.path)
        assert len(findings) == 1
        f = findings[0]
        assert f.operation == "RunPython"
        assert f.model_name == ""
        assert f.field_name == ""
        assert f.severity == "warning"
        assert "atomic" in f.message

    def test_with_atomic_false_is_safe(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0004_data_migration.py", textwrap.dedent("""\
            from django.db import migrations

            def forward(apps, schema_editor):
                pass

            class Migration(migrations.Migration):
                atomic = False
                dependencies = []
                operations = [
                    migrations.RunPython(forward),
                ]
        """))
        assert detect(proj.path) == []

    def test_atomic_true_still_flagged(self, proj: ProjectBuilder) -> None:
        """atomic = True (the default) must NOT suppress the finding."""
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0004_data.py", textwrap.dedent("""\
            from django.db import migrations

            def forward(apps, schema_editor): pass

            class Migration(migrations.Migration):
                atomic = True
                dependencies = []
                operations = [migrations.RunPython(forward)]
        """))
        findings = detect(proj.path)
        assert len(findings) == 1

    def test_multiple_run_python_each_flagged(self, proj: ProjectBuilder) -> None:
        """Two RunPython ops without atomic=False → two findings."""
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0004_data.py", textwrap.dedent("""\
            from django.db import migrations

            def forward1(apps, schema_editor): pass
            def forward2(apps, schema_editor): pass

            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RunPython(forward1),
                    migrations.RunPython(forward2),
                ]
        """))
        findings = [f for f in detect(proj.path) if f.operation == "RunPython"]
        assert len(findings) == 2

    def test_multiple_run_python_with_atomic_false_all_safe(self, proj: ProjectBuilder) -> None:
        """atomic=False at class level suppresses all RunPython findings."""
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0004_data.py", textwrap.dedent("""\
            from django.db import migrations

            def forward1(apps, schema_editor): pass
            def forward2(apps, schema_editor): pass

            class Migration(migrations.Migration):
                atomic = False
                dependencies = []
                operations = [
                    migrations.RunPython(forward1),
                    migrations.RunPython(forward2),
                ]
        """))
        assert [f for f in detect(proj.path) if f.operation == "RunPython"] == []

    def test_ignore_comment_suppresses(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0004_data.py", textwrap.dedent("""\
            from django.db import migrations

            def forward(apps, schema_editor): pass

            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RunPython(forward),  # django-arch-check: ignore
                ]
        """))
        assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# RunSQL
# ---------------------------------------------------------------------------


class TestRunSQL:
    def test_always_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0005_raw_sql.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RunSQL(
                        "ALTER TABLE orders ADD COLUMN extra VARCHAR(100)",
                        reverse_sql="ALTER TABLE orders DROP COLUMN extra",
                    ),
                ]
        """))
        findings = detect(proj.path)
        assert len(findings) == 1
        f = findings[0]
        assert f.operation == "RunSQL"
        assert f.model_name == ""
        assert f.field_name == ""
        assert f.severity == "warning"
        assert "SQL" in f.message

    def test_ignore_comment_suppresses(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0005_raw_sql.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RunSQL(  # django-arch-check: ignore
                        "ALTER TABLE orders ADD COLUMN extra VARCHAR(100)",
                    ),
                ]
        """))
        assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Multiple operations in one migration
# ---------------------------------------------------------------------------


class TestMultipleOperations:
    def test_all_risky_operations_detected(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0006_mixed.py", textwrap.dedent("""\
            from django.db import migrations, models

            def forward(apps, schema_editor): pass

            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RemoveField(model_name='order', name='old_col'),
                    migrations.RenameField(model_name='order', old_name='a', new_name='b'),
                    migrations.AddField(
                        model_name='order',
                        name='risky',
                        field=models.IntegerField(),
                    ),
                    migrations.RunPython(forward),
                    migrations.RunSQL("SELECT 1"),
                ]
        """))
        findings = detect(proj.path)
        ops = [f.operation for f in findings]
        assert "RemoveField" in ops
        assert "RenameField" in ops
        assert "AddField" in ops
        assert "RunPython" in ops
        assert "RunSQL" in ops
        assert len(findings) == 5

    def test_safe_operations_not_flagged(self, proj: ProjectBuilder) -> None:
        """CreateModel, DeleteModel, AlterField etc. are not watched."""
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0007_create.py", textwrap.dedent("""\
            from django.db import migrations, models
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.CreateModel(
                        name='Order',
                        fields=[('id', models.AutoField(primary_key=True))],
                    ),
                ]
        """))
        assert detect(proj.path) == []

    def test_multiple_migrations_all_scanned(self, proj: ProjectBuilder) -> None:
        """Findings are collected across multiple migration files."""
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_remove.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [migrations.RemoveField(model_name='order', name='x')]
        """))
        proj.write("orders/migrations/0002_rename.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RenameField(model_name='order', old_name='a', new_name='b')
                ]
        """))
        findings = detect(proj.path)
        assert len(findings) == 2
        assert {f.migration_name for f in findings} == {"0001_remove", "0002_rename"}


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


class TestExclusions:
    def test_init_py_is_skipped(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "# empty")
        assert detect(proj.path) == []

    def test_non_migration_dir_not_scanned(self, proj: ProjectBuilder) -> None:
        """A file with Migration class outside a migrations/ dir is ignored."""
        proj.write("orders/0001_initial.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RemoveField(model_name='order', name='x'),
                ]
        """))
        assert detect(proj.path) == []

    def test_ignore_paths_excludes_app(self, proj: ProjectBuilder) -> None:
        proj.write("legacy/migrations/__init__.py", "")
        proj.write("legacy/migrations/0001_remove.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [migrations.RemoveField(model_name='old', name='field')]
        """))
        assert detect(proj.path, ignore_paths=("legacy",)) == []

    def test_venv_not_scanned(self, proj: ProjectBuilder) -> None:
        proj.write(".venv/app/migrations/__init__.py", "")
        proj.write(".venv/app/migrations/0001_initial.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [migrations.RemoveField(model_name='x', name='y')]
        """))
        assert detect(proj.path) == []

    def test_no_migration_class_produces_no_findings(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0007_util.py", textwrap.dedent("""\
            # helper with no Migration class
            def some_helper():
                pass
        """))
        assert detect(proj.path) == []

    def test_syntax_error_file_skipped_gracefully(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_bad.py", ")(][ this is not python")
        assert detect(proj.path) == []

    def test_non_py_file_ignored(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_note.txt", "RemoveField something")
        assert detect(proj.path) == []


# ---------------------------------------------------------------------------
# Finding field correctness
# ---------------------------------------------------------------------------


class TestFindingFields:
    def test_all_fields_are_correct_types(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0008_check_types.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RemoveField(model_name='order', name='phone'),
                ]
        """))
        f = detect(proj.path)[0]
        assert isinstance(f.file_path, str)
        assert isinstance(f.migration_name, str)
        assert isinstance(f.operation, str)
        assert isinstance(f.model_name, str)
        assert isinstance(f.field_name, str)
        assert isinstance(f.message, str)
        assert f.severity == "warning"

    def test_file_path_is_relative(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0001_remove.py", textwrap.dedent("""\
            from django.db import migrations
            class Migration(migrations.Migration):
                dependencies = []
                operations = [migrations.RemoveField(model_name='order', name='x')]
        """))
        findings = detect(proj.path)
        assert not findings[0].file_path.startswith("/")
        assert "orders" in findings[0].file_path

    def test_message_is_non_empty_for_all_operations(self, proj: ProjectBuilder) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0009_all_ops.py", textwrap.dedent("""\
            from django.db import migrations, models

            def fwd(apps, schema_editor): pass

            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RemoveField(model_name='order', name='x'),
                    migrations.RenameField(model_name='order', old_name='a', new_name='b'),
                    migrations.AddField(
                        model_name='order', name='y',
                        field=models.IntegerField(),
                    ),
                    migrations.RunPython(fwd),
                    migrations.RunSQL("SELECT 1"),
                ]
        """))
        findings = detect(proj.path)
        assert all(len(f.message) > 0 for f in findings)

    def test_run_python_and_run_sql_have_empty_model_and_field(
        self, proj: ProjectBuilder
    ) -> None:
        proj.write("orders/migrations/__init__.py", "")
        proj.write("orders/migrations/0010_run.py", textwrap.dedent("""\
            from django.db import migrations

            def fwd(apps, schema_editor): pass

            class Migration(migrations.Migration):
                dependencies = []
                operations = [
                    migrations.RunPython(fwd),
                    migrations.RunSQL("SELECT 1"),
                ]
        """))
        findings = detect(proj.path)
        for f in findings:
            assert f.model_name == ""
            assert f.field_name == ""
            
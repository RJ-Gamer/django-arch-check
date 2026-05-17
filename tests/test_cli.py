"""Integration tests for the CLI layer using Click's CliRunner."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

from click.testing import CliRunner

from django_arch_check import analyzer
from django_arch_check.cli import main
from tests.conftest import ProjectBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(*args: str, proj_path: str | None = None) -> object:
    """Invoke the CLI and return the Click Result object."""
    runner = CliRunner()
    cmd = list(args)
    if proj_path:
        cmd.append(proj_path)
    return runner.invoke(main, cmd, catch_exceptions=False)


def _clean_project(proj: ProjectBuilder) -> str:
    """Write a clean Django project with four balanced apps.

    Four apps at ~25% each keeps all well under the 30% god-app threshold.
    Views have no ORM calls so missing-service-layer stays silent too.
    """
    for app in ("blog", "authors", "tags", "comments"):
        proj.write(f"{app}/__init__.py", "")
        proj.write(
            f"{app}/models.py",
            "from django.db import models\nclass M(models.Model): pass\n",
        )
        proj.write(
            f"{app}/views.py",
            "from django.http import JsonResponse\n"
            "def index(r): return JsonResponse({'ok': True})\n",
        )
    return proj.path


def _critical_project(proj: ProjectBuilder) -> str:
    """Write a project guaranteed to produce at least one critical finding."""
    proj.write("orders/__init__.py", "")
    proj.write(
        "orders/models.py",
        (
            "from django.db import models\n"
            "class Order(models.Model):\n"
            + "\n".join(f"    def m{i}(self): pass" for i in range(30))
        ),
    )
    return proj.path


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_flag() -> None:
    result = run("--version")
    assert result.exit_code == 0
    assert "django-arch-check" in result.output


# ---------------------------------------------------------------------------
# analyze — text mode (default)
# ---------------------------------------------------------------------------


def test_analyze_text_clean_project_exits_zero(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", proj_path=path)
    assert result.exit_code == 0


def test_analyze_text_critical_finding_exits_one(proj: ProjectBuilder) -> None:
    path = _critical_project(proj)
    result = run("analyze", proj_path=path)
    assert result.exit_code == 1


def test_analyze_text_output_contains_all_section_headers(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", proj_path=path)
    for header in [
        "Fat Models",
        "God Apps",
        "Circular Imports",
        "Missing Service Layer",
        "Celery Tasks Without Retry",
        "Direct SQL",
        "N+1 Query Risks",
    ]:
        assert header in result.output, f"Missing section header: {header!r}"


def test_analyze_text_shows_project_path(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", proj_path=path)
    assert path in result.output


def test_analyze_text_fat_model_finding_in_output(proj: ProjectBuilder) -> None:
    path = _critical_project(proj)
    result = run("analyze", proj_path=path)
    assert "Order" in result.output
    assert "CRITICAL" in result.output


# ---------------------------------------------------------------------------
# analyze — html mode
# ---------------------------------------------------------------------------


def test_analyze_html_creates_report_file(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", "--format", "html", proj_path=path)
    assert result.exit_code == 0
    assert os.path.exists(os.path.join(path, "arch-report.html"))


def test_analyze_html_output_contains_score(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", "--format", "html", proj_path=path)
    assert "Health score" in result.output


def test_analyze_html_output_contains_report_path(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", "--format", "html", proj_path=path)
    assert "arch-report.html" in result.output


def test_analyze_html_report_is_valid_html(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    run("analyze", "--format", "html", proj_path=path)
    report = Path(path) / "arch-report.html"
    content = report.read_text()
    assert "<!DOCTYPE html>" in content
    assert "<html" in content
    assert "</html>" in content


def test_analyze_html_does_not_print_text_sections(proj: ProjectBuilder) -> None:
    """In html mode, the fat-models section heading should NOT appear on stdout."""
    path = _clean_project(proj)
    result = run("analyze", "--format", "html", proj_path=path)
    assert "── Fat Models" not in result.output


def test_analyze_html_exits_zero_clean(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", "--format", "html", proj_path=path)
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Threshold flags
# ---------------------------------------------------------------------------


def test_fat_model_threshold_flag(proj: ProjectBuilder) -> None:
    """--fat-model-threshold 5 should flag a 6-method model."""
    proj.write("app/__init__.py", "")
    proj.write(
        "app/models.py",
        (
            "from django.db import models\n"
            "class Small(models.Model):\n"
            + "\n".join(f"    def m{i}(self): pass" for i in range(6))
        ),
    )
    # Default threshold=10 → clean
    result_default = run("analyze", proj_path=proj.path)
    assert "Small" not in result_default.output
    # Threshold=5 → flagged
    result_low = run("analyze", "--fat-model-threshold", "5", proj_path=proj.path)
    assert "Small" in result_low.output


def test_god_app_threshold_flag(proj: ProjectBuilder) -> None:
    """--god-app-threshold 90 should suppress a 70% god-app finding."""
    proj.write("big/__init__.py", "")
    proj.write("big/models.py", "\n".join(f"x_{i}={i}" for i in range(70)))
    proj.write("small/__init__.py", "")
    proj.write("small/models.py", "\n".join(f"x_{i}={i}" for i in range(30)))
    # Default 30% threshold → big/ flagged
    result_default = run("analyze", proj_path=proj.path)
    assert "big/" in result_default.output
    # Raise threshold to 90% → nothing flagged
    result_high = run("analyze", "--god-app-threshold", "90", proj_path=proj.path)
    assert "No god apps found" in result_high.output


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_invalid_path_exits_nonzero() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["analyze", "/nonexistent/path/xyz"])
    assert result.exit_code != 0


def test_help_shows_all_options(proj: ProjectBuilder) -> None:
    result = run("analyze", "--help")
    for option in [
        "--fat-model-threshold",
        "--god-app-threshold",
        "--ignore",
        "--ignore-path",
        "--format",
    ]:
        assert option in result.output


def test_ignore_invalid_detector_name_shows_clear_error(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", "--ignore", "fat_modelz", proj_path=path)
    assert result.exit_code != 0
    assert "Error: Unknown detector 'fat_modelz'." in result.output
    assert "Valid detectors are: fat_models, god_apps," in result.output


def test_ignore_detector_skips_it_entirely(
    monkeypatch: object,
    proj: ProjectBuilder,
) -> None:
    path = _clean_project(proj)
    skipped = Mock(return_value=[])
    called = Mock(return_value=[])

    monkeypatch.setattr(analyzer.fat_models, "detect", skipped)
    monkeypatch.setattr(analyzer.god_apps, "detect", called)
    monkeypatch.setattr(analyzer.circular_imports, "detect", called)
    monkeypatch.setattr(analyzer.missing_service_layer, "detect", called)
    monkeypatch.setattr(analyzer.celery_tasks, "detect", called)
    monkeypatch.setattr(analyzer.direct_sql, "detect", called)
    monkeypatch.setattr(analyzer.n_plus_one, "detect", called)

    result = run("analyze", "--ignore", "fat_models", proj_path=path)

    assert result.exit_code == 0
    skipped.assert_not_called()
    assert called.call_count == 6
    assert "⊘ Skipped (--ignore flag)" in result.output


def test_ignore_path_skips_matching_files(proj: ProjectBuilder) -> None:
    proj.write("legacy/__init__.py", "")
    proj.write(
        "legacy/models.py",
        (
            "from django.db import models\n"
            "class LegacyOrder(models.Model):\n"
            + "\n".join(f"    def m{i}(self): pass" for i in range(30))
        ),
    )
    proj.write("live/__init__.py", "")
    proj.write(
        "live/models.py",
        (
            "from django.db import models\n"
            "class LiveOrder(models.Model):\n"
            + "\n".join(f"    def m{i}(self): pass" for i in range(30))
        ),
    )

    result = run("analyze", "--ignore-path", "legacy/", proj_path=proj.path)

    assert result.exit_code == 1
    assert "LegacyOrder" not in result.output
    assert "legacy/models.py" not in result.output
    assert "LiveOrder" in result.output


def test_ignore_detector_note_appears_in_html_report(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    run("analyze", "--ignore", "celery_tasks", "--format", "html", proj_path=path)
    report = Path(path) / "arch-report.html"
    content = report.read_text(encoding="utf-8")
    assert "Celery Tasks Without Retry" in content
    assert "⊘ Skipped (--ignore flag)" in content

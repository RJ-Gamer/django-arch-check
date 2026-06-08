"""Integration tests for the CLI layer using Click's CliRunner."""

from __future__ import annotations

import json
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
        "Migration Safety",
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


def test_analyze_text_missing_service_layer_uses_orm_call_count(
    proj: ProjectBuilder,
) -> None:
    path = _clean_project(proj)
    proj.write(
        "blog/views.py",
        (
            "from blog.models import Post, Comment\n"
            "def dashboard(request):\n"
            "    posts = Post.objects.all()\n"
            "    comments = Comment.objects.filter(is_public=True)\n"
            "    return posts\n"
        ),
    )

    result = run("analyze", proj_path=path)

    assert result.exit_code == 0
    assert "dashboard()" in result.output
    assert "makes 2 direct ORM calls" in result.output


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


def test_analyze_html_critical_finding_exits_one_and_writes_report(
    proj: ProjectBuilder,
) -> None:
    path = _critical_project(proj)
    result = run("analyze", "--format", "html", proj_path=path)
    assert result.exit_code == 1
    assert os.path.exists(os.path.join(path, "arch-report.html"))


def test_analyze_json_output_is_valid_json(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", "--format", "json", proj_path=path)

    assert result.exit_code == 0
    assert "Analyzing:" not in result.output

    payload = json.loads(result.output)
    assert payload["tool"]["name"] == "django-arch-check"
    assert payload["project_path"] == path
    assert payload["summary"]["health_score"] == 100
    assert payload["summary"]["skipped_detectors"] == []
    result.output.encode("cp1252")


def test_analyze_json_critical_output_still_parses(proj: ProjectBuilder) -> None:
    path = _critical_project(proj)
    result = run("analyze", "--format", "json", proj_path=path)

    assert result.exit_code == 1
    assert "Analyzing:" not in result.output

    payload = json.loads(result.output)
    fat_models = next(d for d in payload["detectors"] if d["id"] == "fat_models")
    assert fat_models["finding_count"] == 1
    assert fat_models["critical_count"] == 1
    assert fat_models["findings"][0]["class_name"] == "Order"
    assert fat_models["findings"][0]["location"]["path"] == "orders/models.py"
    result.output.encode("cp1252")


def test_analyze_json_marks_skipped_detectors(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", "--format", "json", "--ignore", "celery_tasks", proj_path=path)

    assert result.exit_code == 0

    payload = json.loads(result.output)
    assert payload["summary"]["skipped_detectors"] == ["celery_tasks"]
    detector = next(d for d in payload["detectors"] if d["id"] == "celery_tasks")
    assert detector["skipped"] is True
    assert detector["findings"] == []


def test_analyze_sarif_output_is_valid_sarif(proj: ProjectBuilder) -> None:
    path = _clean_project(proj)
    result = run("analyze", "--format", "sarif", proj_path=path)

    assert result.exit_code == 0
    assert "Analyzing:" not in result.output

    payload = json.loads(result.output)
    assert payload["version"] == "2.1.0"
    assert payload["runs"][0]["tool"]["driver"]["name"] == "django-arch-check"
    assert len(payload["runs"][0]["tool"]["driver"]["rules"]) == 9
    assert payload["runs"][0]["results"] == []
    result.output.encode("cp1252")


def test_analyze_sarif_critical_output_contains_result_location(
    proj: ProjectBuilder,
) -> None:
    path = _critical_project(proj)
    result = run("analyze", "--format", "sarif", proj_path=path)

    assert result.exit_code == 1

    payload = json.loads(result.output)
    sarif_result = payload["runs"][0]["results"][0]
    assert sarif_result["ruleId"] == "fat_models"
    assert sarif_result["level"] == "error"
    assert sarif_result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == (
        "orders/models.py"
    )
    result.output.encode("cp1252")


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
    assert "json" in result.output
    assert "sarif" in result.output


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
    monkeypatch.setattr(analyzer.migration_safety, "detect", called)  # ← ADD


    result = run("analyze", "--ignore", "fat_models", proj_path=path)

    assert result.exit_code == 0
    skipped.assert_not_called()
    assert called.call_count == 7
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


# ---------------------------------------------------------------------------
# --watch mode
# ---------------------------------------------------------------------------


def test_watch_rejects_non_text_format(proj: ProjectBuilder) -> None:
    """--watch combined with html/json/sarif must exit with a clear error."""
    path = _clean_project(proj)
    runner = CliRunner()
    for fmt in ("html", "json", "sarif"):
        result = runner.invoke(
            main, ["analyze", "--watch", "--format", fmt, path]
        )
        assert result.exit_code != 0
        assert "--watch is only supported with --format text" in result.output


def test_watch_flag_appears_in_help(proj: ProjectBuilder) -> None:
    result = run("analyze", "--help")
    assert "--watch" in result.output


def test_watch_delegates_to_run_watch(proj: ProjectBuilder) -> None:
    """--watch calls _run_watch and exits 0 without running normal analysis."""
    from unittest.mock import patch

    path = _clean_project(proj)
    runner = CliRunner()
    with patch("django_arch_check.cli._run_watch") as mock_watch:
        result = runner.invoke(
            main, ["analyze", "--watch", path], catch_exceptions=False
        )
    mock_watch.assert_called_once_with(
        project_path=path,
        fat_model_threshold=15,
        god_app_threshold=30,
        ignored_detectors=(),
        ignore_paths=(),
    )
    assert result.exit_code == 0


def test_watch_passes_threshold_flags(proj: ProjectBuilder) -> None:
    """--watch forwards custom thresholds to _run_watch."""
    from unittest.mock import patch

    path = _clean_project(proj)
    runner = CliRunner()
    with patch("django_arch_check.cli._run_watch") as mock_watch:
        runner.invoke(
            main,
            ["analyze", "--watch", "--fat-model-threshold", "5",
             "--god-app-threshold", "50", path],
            catch_exceptions=False,
        )
    _, kwargs = mock_watch.call_args
    assert kwargs["fat_model_threshold"] == 5
    assert kwargs["god_app_threshold"] == 50


def test_finding_key_is_stable() -> None:
    """_finding_key returns the same string for the same finding."""
    from django_arch_check.cli import _finding_key
    from django_arch_check.detectors.fat_models import FatModelFinding

    f = FatModelFinding(
        file_path="app/models.py", class_name="Order",
        method_count=20, severity="critical",
    )
    assert _finding_key(f) == _finding_key(f)
    assert isinstance(_finding_key(f), str)


def test_all_finding_keys_counts_all_detectors() -> None:
    """_all_finding_keys returns one key per finding across all detectors."""
    from django_arch_check.cli import _all_finding_keys
    from django_arch_check.analyzer import AnalysisResult
    from django_arch_check.detectors.fat_models import FatModelFinding
    from django_arch_check.detectors.circular_imports import CircularImportFinding

    result = AnalysisResult(
        fat_models=[FatModelFinding(
            file_path="a.py", class_name="A", method_count=20, severity="critical"
        )],
        circular_imports=[CircularImportFinding(
            cycle_display="a -> b -> a", severity="critical"
        )],
    )
    assert len(_all_finding_keys(result)) == 2


def test_has_critical_findings_covers_all_detectors() -> None:
    """_has_critical_findings detects criticals from any detector field."""
    from django_arch_check.cli import _has_critical_findings
    from django_arch_check.analyzer import AnalysisResult
    from django_arch_check.detectors.direct_sql import DirectSQLFinding
    from django_arch_check.detectors.migration_safety import MigrationSafetyFinding

    assert not _has_critical_findings(AnalysisResult())

    with_sql = AnalysisResult(
        direct_sql=[DirectSQLFinding(
            file_path="v.py", line_number=1,
            pattern="cursor.execute(", severity="critical",
        )]
    )
    assert _has_critical_findings(with_sql)


def test_watch_print_diff_no_changes(proj: ProjectBuilder) -> None:
    """_print_watch_diff reports 'No changes' when findings are identical."""
    from django_arch_check.cli import _print_watch_diff
    from django_arch_check.analyzer import AnalysisResult

    result = AnalysisResult()
    runner = CliRunner()
    with runner.isolated_filesystem():
        output = runner.invoke(
            main, ["analyze", "--help"]  # just to get a runner context
        )
    # Call directly and capture via click echo
    import io
    buf: list[str] = []
    import unittest.mock as mock
    with mock.patch("click.echo", side_effect=lambda s, **kw: buf.append(str(s))):
        _print_watch_diff(result, result, [])
    assert any("No changes" in line for line in buf)


def test_watch_print_diff_shows_new_findings(proj: ProjectBuilder) -> None:
    """_print_watch_diff highlights new findings introduced between runs."""
    from django_arch_check.cli import _print_watch_diff
    from django_arch_check.analyzer import AnalysisResult
    from django_arch_check.detectors.fat_models import FatModelFinding

    prev = AnalysisResult()
    curr = AnalysisResult(
        fat_models=[FatModelFinding(
            file_path="app/models.py", class_name="BigModel",
            method_count=20, severity="critical",
        )]
    )
    buf: list[str] = []
    import unittest.mock as mock
    with mock.patch("click.echo", side_effect=lambda s, **kw: buf.append(str(s))):
        _print_watch_diff(prev, curr, [])
    combined = " ".join(buf)
    assert "new finding" in combined


def test_watch_print_diff_shows_resolved_findings(proj: ProjectBuilder) -> None:
    """_print_watch_diff highlights findings resolved between runs."""
    from django_arch_check.cli import _print_watch_diff
    from django_arch_check.analyzer import AnalysisResult
    from django_arch_check.detectors.fat_models import FatModelFinding

    prev = AnalysisResult(
        fat_models=[FatModelFinding(
            file_path="app/models.py", class_name="BigModel",
            method_count=20, severity="critical",
        )]
    )
    curr = AnalysisResult()
    buf: list[str] = []
    import unittest.mock as mock
    with mock.patch("click.echo", side_effect=lambda s, **kw: buf.append(str(s))):
        _print_watch_diff(prev, curr, [])
    combined = " ".join(buf)
    assert "resolved" in combined

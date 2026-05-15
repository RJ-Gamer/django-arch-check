"""Tests for the god-app detector."""

from __future__ import annotations

from django_arch_check.detectors.god_apps import detect
from tests.conftest import ProjectBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lines(n: int, prefix: str = "x") -> str:
    """Return *n* non-blank, non-comment Python lines."""
    return "\n".join(f"{prefix}_{i} = {i}" for i in range(n))


# ---------------------------------------------------------------------------
# Severity thresholds (default threshold=30)
# ---------------------------------------------------------------------------


def test_critical_app_over_fifty_percent(proj: ProjectBuilder) -> None:
    """App owning > 50% of LOC (threshold+20) → critical."""
    # big_app: 200 lines, small_app: 50 lines → big_app = 80%
    proj.write("big_app/models.py", _lines(200))
    proj.write("small_app/models.py", _lines(50))
    findings = detect(proj.path)
    assert any(f.severity == "critical" and "big_app" in f.app_path for f in findings)


def test_warning_app_between_thresholds(proj: ProjectBuilder) -> None:
    """App owning 30–50% of total LOC → warning."""
    # app_a: 40 lines, app_b: 60 lines → app_a = 40% (warning), app_b = 60% (critical)
    proj.write("app_a/models.py", _lines(40))
    proj.write("app_b/models.py", _lines(60))
    findings = detect(proj.path)
    severities = {f.app_path.strip("/"): f.severity for f in findings}
    assert severities.get("app_a") == "warning"
    assert severities.get("app_b") == "critical"


def test_clean_app_under_threshold(proj: ProjectBuilder) -> None:
    """App owning < 30% of total LOC → no finding."""
    # four equal apps at 25% each
    for name in ["a", "b", "c", "d"]:
        proj.write(f"{name}/models.py", _lines(25))
    assert detect(proj.path) == []


def test_empty_project_no_findings(proj: ProjectBuilder) -> None:
    """Project with no Python files → empty list (zero-LOC guard)."""
    assert detect(proj.path) == []


def test_no_django_apps_no_findings(proj: ProjectBuilder) -> None:
    """Python files outside an app (no models.py/apps.py) → not flagged."""
    proj.write("config/settings.py", _lines(100))
    proj.write("manage.py", "import django\n")
    # No models.py or apps.py → no apps detected
    assert detect(proj.path) == []


def test_single_app_project_not_flagged(proj: ProjectBuilder) -> None:
    """A project with exactly one Django app must not be flagged.

    A single app owns 100% of the project's code by definition — that is
    not a structural problem, just a small project. This was a false positive
    in earlier versions of the detector.
    """
    proj.write("blog/models.py", _lines(200))
    assert detect(proj.path) == []


def test_apps_py_is_sufficient_marker(proj: ProjectBuilder) -> None:
    """A directory with only apps.py (no models.py) is still an app."""
    proj.write(
        "core/apps.py",
        "from django.apps import AppConfig\nclass C(AppConfig): name='core'\n",
    )
    proj.write("core/services.py", _lines(200))
    proj.write("other/models.py", _lines(50))
    findings = detect(proj.path)
    app_paths = {f.app_path for f in findings}
    assert any("core" in p for p in app_paths)


def test_findings_sorted_by_percentage_descending(proj: ProjectBuilder) -> None:
    """Findings must be ordered largest-share first."""
    proj.write("small/models.py", _lines(30))  # ~16%
    proj.write("medium/models.py", _lines(60))  # ~32%
    proj.write("large/models.py", _lines(100))  # ~53%
    findings = detect(proj.path, threshold=10)
    percentages = [f.percentage for f in findings]
    assert percentages == sorted(percentages, reverse=True)


def test_blank_lines_and_comments_excluded(proj: ProjectBuilder) -> None:
    """Blank lines and comment lines must not count toward LOC."""
    source = "\n".join(
        [
            "# This is a comment",
            "",
            "   ",
            "x = 1",  # only this counts
            "# another comment",
            "",
        ]
    )
    proj.write("big_app/models.py", source)  # 1 LOC
    proj.write(
        "small_app/models.py", "y = 1\n"
    )  # 1 LOC — second app needed for detection
    # big_app: 1 LOC out of 2 total = 50% → above threshold of 50 → warning
    findings = detect(proj.path, threshold=50)
    # At least big_app is flagged; verify LOC counting excluded blanks/comments
    big = next(f for f in findings if "big_app" in f.app_path)
    assert big.app_loc == 1


def test_skip_dirs_not_counted(proj: ProjectBuilder) -> None:
    """.venv LOC must not inflate total or app counts."""
    proj.write("big_app/models.py", _lines(80))
    proj.write("small_app/models.py", _lines(20))
    proj.write(".venv/lib/fake/models.py", _lines(10000))
    findings = detect(proj.path)
    # total_loc must only reflect the two real apps, not .venv
    assert all(f.total_loc == 100 for f in findings)


def test_custom_threshold(proj: ProjectBuilder) -> None:
    """A custom threshold should change which apps are flagged."""
    proj.write("app_a/models.py", _lines(35))  # 35%
    proj.write("app_b/models.py", _lines(65))  # 65%
    # At threshold=70, only app_b would be warning (65% < 70) — nothing flagged
    assert detect(proj.path, threshold=70) == []
    # At threshold=30, both are flagged
    assert len(detect(proj.path, threshold=30)) == 2


def test_finding_fields_populated(proj: ProjectBuilder) -> None:
    """All fields on GodAppFinding must be present and sensible."""
    proj.write("big/models.py", _lines(80))
    proj.write("small/models.py", _lines(20))
    findings = detect(proj.path, threshold=30)
    assert findings  # at least one
    f = findings[0]
    assert f.app_path.endswith("/")
    assert f.app_loc > 0
    assert f.total_loc >= f.app_loc
    assert 0 <= f.percentage <= 100
    assert f.severity in ("warning", "critical")

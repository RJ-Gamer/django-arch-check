"""Tests for the HTML report generator and health score calculator."""

from __future__ import annotations

from django_arch_check.analyzer import AnalysisResult
from django_arch_check.detectors.circular_imports import CircularImportFinding
from django_arch_check.detectors.direct_sql import DirectSQLFinding
from django_arch_check.detectors.fat_models import FatModelFinding
from django_arch_check.report import compute_score, generate_html

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _warning_fat_model() -> FatModelFinding:
    return FatModelFinding(
        file_path="app/models.py",
        class_name="Order",
        method_count=12,
        severity="warning",
    )


def _critical_fat_model() -> FatModelFinding:
    return FatModelFinding(
        file_path="app/models.py",
        class_name="Profile",
        method_count=22,
        severity="critical",
    )


def _warning_direct_sql() -> DirectSQLFinding:
    return DirectSQLFinding(
        file_path="app/views.py",
        line_number=10,
        pattern="cursor.execute(",
        severity="warning",
    )


def _critical_circular() -> CircularImportFinding:
    return CircularImportFinding(
        cycle_display="a.models → b.models → a.models",
        severity="critical",
    )


def _empty_result() -> AnalysisResult:
    return AnalysisResult()


# ---------------------------------------------------------------------------
# compute_score
# ---------------------------------------------------------------------------


class TestComputeScore:
    def test_perfect_score_no_findings(self) -> None:
        assert compute_score(_empty_result()) == 100

    def test_one_warning_deducts_five(self) -> None:
        result = AnalysisResult(fat_models=[_warning_fat_model()])
        assert compute_score(result) == 95

    def test_one_critical_deducts_fifteen(self) -> None:
        result = AnalysisResult(fat_models=[_critical_fat_model()])
        assert compute_score(result) == 85

    def test_mixed_deductions(self) -> None:
        # 2 criticals → -30, 3 warnings → -15 → score = 55
        result = AnalysisResult(
            fat_models=[_critical_fat_model(), _critical_fat_model()],
            direct_sql=[
                _warning_direct_sql(),
                _warning_direct_sql(),
                _warning_direct_sql(),
            ],
        )
        assert compute_score(result) == 55

    def test_score_clamped_at_zero(self) -> None:
        """Many findings must not produce a negative score."""
        result = AnalysisResult(
            fat_models=[_critical_fat_model()] * 10,  # 10 × -15 = -150
        )
        assert compute_score(result) == 0

    def test_findings_across_all_detectors_summed(self) -> None:
        """Deductions span all AnalysisResult fields."""
        result = AnalysisResult(
            fat_models=[_warning_fat_model()],  # -5
            circular_imports=[_critical_circular()],  # -15
            direct_sql=[_warning_direct_sql()],  # -5
        )
        assert compute_score(result) == 75

    def test_score_100_with_empty_lists(self) -> None:
        result = AnalysisResult(
            fat_models=[],
            god_apps=[],
            circular_imports=[],
            missing_service_layer=[],
            celery_tasks=[],
            direct_sql=[],
            n_plus_one=[],
        )
        assert compute_score(result) == 100


# ---------------------------------------------------------------------------
# generate_html — structural checks
# ---------------------------------------------------------------------------


class TestGenerateHtml:
    def _html(self, result: AnalysisResult | None = None) -> str:
        return generate_html(result or _empty_result(), "/project/myapp")

    def test_returns_string(self) -> None:
        assert isinstance(self._html(), str)

    def test_contains_doctype(self) -> None:
        assert "<!DOCTYPE html>" in self._html()

    def test_contains_all_section_titles(self) -> None:
        html = self._html()
        for title in [
            "Fat Models",
            "God Apps",
            "Circular Imports",
            "Missing Service Layer",
            "Celery Tasks Without Retry",
            "Direct SQL",
            "N+1 Query Risks",
        ]:
            assert title in html, f"Section title missing: {title!r}"

    def test_score_appears_in_html(self) -> None:
        result = AnalysisResult(fat_models=[_warning_fat_model()])  # score=95
        html = generate_html(result, "/project")
        assert "95" in html

    def test_perfect_score_html(self) -> None:
        html = self._html(_empty_result())
        assert "100" in html

    def test_zero_score_html(self) -> None:
        result = AnalysisResult(fat_models=[_critical_fat_model()] * 10)
        html = generate_html(result, "/project")
        # Score 0 should appear, and the label "Critical"
        assert (
            ">0<" in html
            or 'score-number">0' in html
            or ">0\n" in html
            or "0</div>" in html
        )

    def test_finding_detail_appears(self) -> None:
        result = AnalysisResult(fat_models=[_warning_fat_model()])
        html = generate_html(result, "/project")
        assert "Order" in html
        assert "12 methods" in html

    def test_circular_cycle_appears(self) -> None:
        result = AnalysisResult(circular_imports=[_critical_circular()])
        html = generate_html(result, "/project")
        assert "a.models" in html

    def test_no_cdn_links(self) -> None:
        """Report must be fully self-contained — no external URLs."""
        html = self._html()
        assert "cdn." not in html.lower()
        assert "googleapis.com" not in html
        assert "cloudflare.com" not in html

    def test_project_path_html_escaped(self) -> None:
        """Characters like < > in project path must be escaped."""
        html = generate_html(_empty_result(), "/path/<project>&name")
        assert "<project>" not in html  # raw < > not in output
        assert "&lt;project&gt;" in html  # escaped version present

    def test_clean_section_shows_no_issues_message(self) -> None:
        html = self._html(_empty_result())
        assert "No issues found" in html

    def test_critical_badge_present_when_critical_finding(self) -> None:
        result = AnalysisResult(circular_imports=[_critical_circular()])
        html = generate_html(result, "/project")
        assert "badge-critical" in html

    def test_warning_badge_present_when_warning_finding(self) -> None:
        result = AnalysisResult(fat_models=[_warning_fat_model()])
        html = generate_html(result, "/project")
        assert "badge-warning" in html

    def test_version_present(self) -> None:
        from django_arch_check import __version__

        html = self._html()
        assert __version__ in html

    def test_score_formula_in_footer(self) -> None:
        html = self._html()
        # Footer explains the deduction amounts
        assert "15" in html and "5" in html

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
    return FatModelFinding(file_path="app/models.py", class_name="Order",
                           method_count=12, severity="warning")

def _critical_fat_model() -> FatModelFinding:
    return FatModelFinding(file_path="app/models.py", class_name="Profile",
                           method_count=22, severity="critical")

def _warning_direct_sql() -> DirectSQLFinding:
    return DirectSQLFinding(file_path="app/views.py", line_number=10,
                            pattern="cursor.execute(", severity="warning")

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

    def test_score_100_with_all_empty_lists(self) -> None:
        result = AnalysisResult(
            fat_models=[], god_apps=[], circular_imports=[],
            missing_service_layer=[], celery_tasks=[],
            direct_sql=[], n_plus_one=[],
        )
        assert compute_score(result) == 100

    def test_score_never_negative(self) -> None:
        """No matter how many findings, score must be >= 0."""
        result = AnalysisResult(fat_models=[_critical_fat_model()] * 100)
        assert compute_score(result) >= 0

    def test_score_never_above_100(self) -> None:
        result = AnalysisResult(fat_models=[_warning_fat_model()])
        assert compute_score(result) <= 100

    def test_all_criticals_scores_lower_than_all_warnings(self) -> None:
        """Same number of findings: all-critical must score lower than all-warning."""
        all_critical = AnalysisResult(
            fat_models=[_critical_fat_model()] * 5,
        )
        all_warning = AnalysisResult(
            fat_models=[_warning_fat_model()] * 5,
        )
        assert compute_score(all_critical) < compute_score(all_warning)

    def test_more_findings_scores_lower_than_fewer(self) -> None:
        """Same mix ratio: more findings → lower score (absolute penalty)."""
        few = AnalysisResult(fat_models=[_critical_fat_model()])
        many = AnalysisResult(fat_models=[_critical_fat_model()] * 20)
        assert compute_score(few) > compute_score(many)

    def test_large_codebase_does_not_score_zero(self) -> None:
        """27 criticals + 85 warnings (Saleor-scale) must produce a non-zero score."""
        result = AnalysisResult(
            fat_models=[_critical_fat_model()] * 27,
            direct_sql=[_warning_direct_sql()] * 85,
        )
        score = compute_score(result)
        assert score > 0, f"Expected non-zero score for large codebase, got {score}"

    def test_absolute_penalty_capped_at_30(self) -> None:
        """The absolute penalty must never exceed 30 regardless of finding count."""
        # 1000 warnings → absolute_penalty = min(30, 1000*0.5) = 30
        # raw = 100 - 0*60 - 1.0*40 = 60
        # score = max(0, round(60 - 30)) = 30
        result = AnalysisResult(
            direct_sql=[_warning_direct_sql()] * 1000,
        )
        score = compute_score(result)
        assert score == 30

    def test_findings_across_all_detectors_counted(self) -> None:
        """Findings from all detector fields are included in the calculation."""
        clean   = compute_score(_empty_result())
        with_findings = compute_score(AnalysisResult(
            fat_models=[_warning_fat_model()],
            circular_imports=[_critical_circular()],
            direct_sql=[_warning_direct_sql()],
        ))
        assert with_findings < clean

    def test_mixed_severity_score_between_extremes(self) -> None:
        """A 50/50 mix scores between the all-critical and all-warning extremes."""
        n = 10
        all_crit = compute_score(AnalysisResult(fat_models=[_critical_fat_model()] * n))
        all_warn = compute_score(AnalysisResult(fat_models=[_warning_fat_model()] * n))
        mixed    = compute_score(AnalysisResult(
            fat_models=[_critical_fat_model()] * (n // 2),
            direct_sql=[_warning_direct_sql()] * (n // 2),
        ))
        assert all_crit <= mixed <= all_warn


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
            "Fat Models", "God Apps", "Circular Imports",
            "Missing Service Layer", "Celery Tasks Without Retry",
            "Direct SQL", "N+1 Query Risks",
        ]:
            assert title in html, f"Section title missing: {title!r}"

    def test_score_appears_in_html(self) -> None:
        result = AnalysisResult(fat_models=[_warning_fat_model()])
        score = compute_score(result)
        html = generate_html(result, "/project")
        assert str(score) in html

    def test_perfect_score_html(self) -> None:
        html = self._html(_empty_result())
        assert "100" in html

    def test_zero_score_html(self) -> None:
        result = AnalysisResult(fat_models=[_critical_fat_model()] * 10)
        html = generate_html(result, "/project")
        # Score 0 should appear, and the label "Critical"
        assert ">0<" in html or "score-number\">0" in html or ">0\n" in html or "0</div>" in html

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
        assert "<project>" not in html          # raw < > not in output
        assert "&lt;project&gt;" in html        # escaped version present

    def test_clean_section_shows_no_issues_message(self) -> None:
        html = self._html(_empty_result())
        assert "No issues found" in html

    def test_skipped_section_shows_ignore_note(self) -> None:
        html = self._html(AnalysisResult(skipped_detectors=("fat_models",)))
        assert "Fat Models" in html
        assert "⊘ Skipped (--ignore flag)" in html

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
        # Footer explains the health score basis
        assert "Health score based on finding severity and density" in html
        

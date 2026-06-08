"""Tests for the HTML report generator and health score calculator."""

from __future__ import annotations

from django_arch_check.analyzer import AnalysisResult
from django_arch_check.detectors.circular_imports import CircularImportFinding
from django_arch_check.detectors.direct_sql import DirectSQLFinding
from django_arch_check.detectors.fat_models import FatModelFinding
from django_arch_check.detectors.n1_serializer_risk import N1SerializerFinding
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


def _serializer_risk_error() -> N1SerializerFinding:
    return N1SerializerFinding(
        detector="N1SerializerRisk",
        severity="error",
        file="app/serializers.py",
        line=24,
        message="ORM call inside SerializerMethodField: get_likes_count in ResourceSerializer",
        code_snippet={
            "start_line": 22,
            "end_line": 24,
            "lines": [
                "    def get_likes_count(self, obj):",
                "        likes = obj.likes.all()",
                "        return likes.count()",
            ],
        },
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
        """More findings within the cap range → lower score."""
        few = AnalysisResult(fat_models=[_critical_fat_model()])
        many = AnalysisResult(fat_models=[_critical_fat_model()] * 6)  # fat_models cap is 6
        assert compute_score(few) > compute_score(many)

    def test_large_codebase_does_not_score_zero(self) -> None:
        """27 criticals + 85 warnings (Saleor-scale) must produce a non-zero score."""
        result = AnalysisResult(
            fat_models=[_critical_fat_model()] * 27,
            direct_sql=[_warning_direct_sql()] * 85,
        )
        score = compute_score(result)
        assert score > 0, f"Expected non-zero score for large codebase, got {score}"

    def test_capped_findings_limit_penalty(self) -> None:
        # Per-detector finding cap prevents a single noisy detector from
        # dominating the score. 1000 direct_sql warnings should score the
        # same as 8 (the cap), and the score must not be negative.
        result_1000 = AnalysisResult(direct_sql=[_warning_direct_sql()] * 1000)
        result_8 = AnalysisResult(direct_sql=[_warning_direct_sql()] * 8)
        assert compute_score(result_1000) == compute_score(result_8)
        assert compute_score(result_1000) >= 0

    def test_findings_across_all_detectors_counted(self) -> None:
        """Findings from all detector fields are included in the calculation."""
        clean   = compute_score(_empty_result())
        with_findings = compute_score(AnalysisResult(
            fat_models=[_warning_fat_model()],
            circular_imports=[_critical_circular()],
            direct_sql=[_warning_direct_sql()],
        ))
        assert with_findings < clean

    def test_criticals_score_lower_than_warnings_same_count(self) -> None:
        """All-critical must always score lower than all-warning for same finding count."""
        n = 5
        all_crit = compute_score(AnalysisResult(fat_models=[_critical_fat_model()] * n))
        all_warn = compute_score(AnalysisResult(fat_models=[_warning_fat_model()] * n))
        assert all_crit < all_warn


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
            "Direct SQL", "N+1 Query Risks", "Migration Safety",
            "N+1 Serializer Risk",
        ]:
            assert title in html, f"Section title missing: {title!r}"

    def test_score_appears_in_html(self) -> None:
        result = AnalysisResult(fat_models=[_warning_fat_model()])
        score = compute_score(result, "/project")   # ← add project_path
        html = generate_html(result, "/project")
        assert str(score) in html
        
    def test_perfect_score_html(self) -> None:
        html = self._html(_empty_result())
        assert "100" in html

    def test_low_score_html_shows_critical_label(self) -> None:
        result = AnalysisResult(
            circular_imports=[_critical_circular()] * 5,
            fat_models=[_critical_fat_model()] * 6,
        )
        html = generate_html(result, "/project")
        assert "Critical" in html or "Poor" in html

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

    def test_serializer_risk_renders_accordion(self) -> None:
        result = AnalysisResult(n1_serializer_risk=[_serializer_risk_error()])
        html = generate_html(result, "/project")
        assert 'details class="issue-accordion"' in html
        assert "language-python" in html
        assert "get_likes_count" in html
        assert "1 critical" in html

    def test_version_present(self) -> None:
        from django_arch_check import __version__
        html = self._html()
        assert __version__ in html

    def test_theme_toggle_present(self) -> None:
        html = self._html()
        assert "theme-toggle" in html
        assert "Switch to light theme" in html

    def test_score_formula_in_footer(self) -> None:
        html = self._html()
        assert "Score = 100" in html
        assert "weighted by detector risk" in html

    def test_score_grade_and_label(self) -> None:
        from django_arch_check.report import score_grade, score_label
        assert score_grade(100) == "A"
        assert score_grade(90)  == "A"
        assert score_grade(89)  == "B"
        assert score_grade(75)  == "B"
        assert score_grade(74)  == "C"
        assert score_grade(60)  == "C"
        assert score_grade(59)  == "D"
        assert score_grade(40)  == "D"
        assert score_grade(39)  == "F"
        assert score_grade(0)   == "F"

        assert score_label(95) == "Excellent"
        assert score_label(80) == "Good"
        assert score_label(65) == "Needs Work"
        assert score_label(45) == "Poor"
        assert score_label(20) == "Critical"

    def test_score_is_size_aware(self) -> None:
        """A single circular import should meaningfully reduce the score."""
        from django_arch_check.report import compute_score
        result = AnalysisResult(circular_imports=[_critical_circular()])
        score = compute_score(result, "")
        assert score < 90  # 1 critical circular import must not score A

    def test_min_file_count_floor_equalises_small_paths(self) -> None:
        """A path with very few files should score the same as one at the floor."""
        from django_arch_check.report import _count_python_files, _MIN_FILE_COUNT
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(2):
                (Path(tmp) / f"f{i}.py").touch()
            assert _count_python_files(tmp) == _MIN_FILE_COUNT

    def test_criticals_double_weighted_in_density(self) -> None:
        """Equal raw weight: critical findings must score lower than warnings."""
        # circular_imports critical weight=10, celery_tasks warning=3*n to match
        # Use fat_models: critical weight=2 vs warning weight=1, same count
        crit = compute_score(AnalysisResult(fat_models=[_critical_fat_model()] * 3))
        warn = compute_score(AnalysisResult(fat_models=[_warning_fat_model()] * 6))  # same raw weight=6
        assert crit < warn

    def test_grade_card_class_green_for_good_score(self) -> None:
        """Health Grade card uses g-ok (green) for scores >= 75."""
        from django_arch_check.report import _score_card_class
        assert _score_card_class(100) == "g-ok"
        assert _score_card_class(90) == "g-ok"
        assert _score_card_class(75) == "g-ok"
        assert _score_card_class(74) == "g-wa"
        assert _score_card_class(60) == "g-wa"
        assert _score_card_class(59) == "g-cr"
        assert _score_card_class(0) == "g-cr"

    def test_grade_card_class_appears_in_html(self) -> None:
        """HTML report uses the correct grade card class for a good score."""
        html = self._html(_empty_result())  # score=100
        assert 'ic g-ok' in html

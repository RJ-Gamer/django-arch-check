"""HTML report generator for django-arch-check.

Score calculation
-----------------
Each finding carries a weight determined by its detector and severity.
Weights reflect real-world risk — not just code style:

    circular_imports  critical = 10   (can prevent the app from starting)
    celery_tasks      critical =  8   (data loss on payment/email tasks)
    migration_safety  warning  =  6   (production outage risk)
    missing_service   critical =  4   (architecture boundary violation)
    n_plus_one        warning  =  3   (performance degradation under load)
    direct_sql        warning  =  2   (bypasses ORM safety layer)
    god_apps          critical =  3   (structural centralization)
    fat_models        warning  =  1   (code smell, no runtime risk)

Formula
-------
    critical_weight    = sum of weights for critical/error findings only
    warning_weight     = sum of weights for warning findings only
    normalized_density = (critical_weight * 2 + warning_weight) / ln(file_count + 1)
    density_penalty    = min(45, round(normalized_density × 4))
    absolute_penalty   = min(10, round((critical_weight + warning_weight) × 0.05))
    score              = max(0, 100 − density_penalty − absolute_penalty)

The logarithmic normalization ensures a fair comparison across codebase sizes:
a 5-file project with 1 critical should score lower than a 500-file project
with the same finding — because the finding represents a higher proportion
of the total surface area.

Critical findings are double-weighted in the density calculation so that
architecture-breaking issues (circular imports, unsafe celery tasks) cause
a steep penalty while warning-only findings (direct SQL, fat models) can
never push a project into F territory alone.

Grades
------
    A  90–100   Excellent
    B  75– 89   Good
    C  60– 74   Needs Work
    D  40– 59   Poor
    F   0– 39   Critical
"""

from __future__ import annotations

import html
import math
import os
from datetime import datetime, timezone

from django_arch_check import __version__
from django_arch_check.analyzer import AnalysisResult

# ---------------------------------------------------------------------------
# Detector weights
# ---------------------------------------------------------------------------

_DETECTOR_WEIGHTS: dict[str, dict[str, float]] = {
    "circular_imports": {"critical": 10.0},
    "celery_tasks": {"critical": 8.0, "warning": 3.0},
    "migration_safety": {"warning": 6.0},
    "missing_service_layer": {"critical": 4.0, "warning": 2.0},
    "n_plus_one": {"warning": 3.0},
    "direct_sql": {"warning": 2.0},
    "god_apps": {"critical": 3.0, "warning": 1.5},
    "fat_models": {"critical": 2.0, "warning": 1.0},
    # TODO: Tune n1_serializer_risk weight after confirming detector in production.
    "n1_serializer_risk": {"error": 3.0, "warning": 1.5},
    "secret_leakage": {"critical": 9.0, "warning": 3.0},
}

# Maximum findings counted per detector — prevents a single noisy detector
# (e.g. 27 direct_sql hits in test fixtures) from dominating the score.
_DETECTOR_FINDING_CAP: dict[str, int] = {
    "direct_sql": 8,
    "migration_safety": 10,
    "n_plus_one": 8,
    "n1_serializer_risk": 8,
    "fat_models": 6,
}

# ---------------------------------------------------------------------------
# Section registry
# ---------------------------------------------------------------------------

_SECTIONS: list[tuple[str, str]] = [
    ("fat_models", "Fat Models"),
    ("god_apps", "God Apps"),
    ("circular_imports", "Circular Imports"),
    ("missing_service_layer", "Missing Service Layer"),
    ("celery_tasks", "Celery Tasks Without Retry"),
    ("direct_sql", "Direct SQL"),
    ("n_plus_one", "N+1 Query Risks"),
    ("migration_safety", "Migration Safety"),
    ("n1_serializer_risk", "N+1 Serializer Risk"),
    ("secret_leakage", "Secret Leakage"),
]

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

_DENSITY_FACTOR: float = 4.0
_ABSOLUTE_FACTOR: float = 0.05
_DENSITY_PENALTY_CAP: int = 45
_ABSOLUTE_PENALTY_CAP: int = 10
_DEFAULT_FILE_COUNT: int = 50
_MIN_FILE_COUNT: int = 30

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "dist",
        "build",
        ".eggs",
    }
)


_ACCORDION_CSS: str = """
    /* ── N+1 Serializer Risk accordion cards ───────────────────────── */
    .issue-accordion { display:block; width:100%; }
    .issue-summary {
      display:flex; align-items:center; gap:10px; cursor:pointer;
      padding:13px 20px; background:transparent; border:none; width:100%;
      list-style:none; user-select:none; flex-wrap:wrap;
    }
    .issue-summary::-webkit-details-marker { display:none; }
    .issue-accordion[open] .issue-summary { border-bottom:1px solid var(--br); }
    .issue-sev {
      font-family:var(--mono); font-size:10px; font-weight:700;
      padding:2px 7px; border-radius:4px; text-transform:uppercase; flex-shrink:0;
      letter-spacing:.04em;
    }
    .severity-critical { background:var(--critical-bg); color:var(--critical); border:1px solid var(--critical-b); }
    .severity-error    { background:var(--critical-bg); color:var(--critical); border:1px solid var(--critical-b); }
    .severity-warning  { background:var(--warning-bg);  color:var(--warning);  border:1px solid var(--warning-b); }
    .issue-msg { flex:1; font-size:13px; font-family:var(--mono); word-break:break-word; }
    .issue-loc { font-family:var(--mono); font-size:11px; color:var(--mu); flex-shrink:0; }
    .issue-detail {
      padding:12px 20px 16px;
      background:var(--s1); border-top:1px solid var(--br2);
    }
    .issue-file-path { font-family:var(--mono); font-size:11px; color:var(--mu); margin-bottom:8px; }
    .code-block {
      background:var(--s3); border:1px solid var(--br); border-radius:6px;
      padding:10px 14px; overflow-x:auto; margin:0;
    }
    .code-block code { font-family:var(--mono); font-size:12px; color:var(--fg); white-space:pre; }
"""


def _count_python_files(project_path: str) -> int:
    """Count .py files in *project_path*, excluding tool and cache directories."""
    if not project_path:
        return _DEFAULT_FILE_COUNT
    count = 0
    for _, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        count += sum(1 for f in filenames if f.endswith(".py"))
    return max(_MIN_FILE_COUNT, count)


def _compute_weighted_score(result: AnalysisResult) -> float:
    """Return the total weighted impact of all findings, with per-detector caps."""
    total = 0.0
    for detector_id, _ in _SECTIONS:
        findings = getattr(result, detector_id, [])
        weights = _DETECTOR_WEIGHTS.get(detector_id, {})
        cap = _DETECTOR_FINDING_CAP.get(detector_id, len(findings))
        for finding in findings[:cap]:
            severity = getattr(finding, "severity", "warning")
            total += weights.get(severity, 1.0)
    return total


def compute_score(result: AnalysisResult, project_path: str = "") -> int:
    """Return a health score 0–100 using weighted, size-normalized formula."""
    weighted = _compute_weighted_score(result)
    if weighted == 0:
        return 100

    # Split critical vs warning weight so criticals hit density harder
    critical_weight = 0.0
    warning_weight = 0.0
    for detector_id, _ in _SECTIONS:
        findings = getattr(result, detector_id, [])
        weights = _DETECTOR_WEIGHTS.get(detector_id, {})
        cap = _DETECTOR_FINDING_CAP.get(detector_id, len(findings))
        for finding in findings[:cap]:
            severity = getattr(finding, "severity", "warning")
            w = weights.get(severity, 1.0)
            if severity in ("critical", "error"):
                critical_weight += w
            else:
                warning_weight += w

    file_count = _count_python_files(project_path)
    # Criticals count double in density so architecture-breaking issues hurt more
    blended = critical_weight * 2 + warning_weight
    normalized_density = blended / math.log(file_count + 1)
    density_penalty = min(
        _DENSITY_PENALTY_CAP, round(normalized_density * _DENSITY_FACTOR)
    )
    absolute_penalty = min(
        _ABSOLUTE_PENALTY_CAP, round(weighted * _ABSOLUTE_FACTOR)
    )
    return max(0, 100 - density_penalty - absolute_penalty)


def score_grade(score: int) -> str:
    """Return letter grade A–F for *score*."""
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def score_label(score: int) -> str:
    """Return a human-readable label for *score*."""
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Needs Work"
    if score >= 40:
        return "Poor"
    return "Critical"


def _score_color(score: int) -> str:
    if score >= 90:
        return "var(--clean)"
    if score >= 75:
        return "var(--clean)"
    if score >= 60:
        return "var(--warning)"
    return "var(--critical)"


def _score_card_class(score: int) -> str:
    """Return the ic card CSS class based on score."""
    if score >= 75:
        return "g-ok"
    if score >= 60:
        return "g-wa"
    return "g-cr"


def _score_status_text(score: int) -> str:
    if score >= 90:
        return "STRUCTURE STABLE"
    if score >= 75:
        return "RISK CONTAINED"
    if score >= 60:
        return "ATTENTION REQUIRED"
    if score >= 40:
        return "ARCHITECTURE STRAIN"
    return "CRITICAL INTEGRITY FAILURE"


def _score_status_class(score: int) -> str:
    if score >= 75:
        return "status-clean"
    if score >= 60:
        return "status-warning"
    return "status-critical"


def _e(text: object) -> str:
    return html.escape(str(text))


def _all_findings(result: AnalysisResult) -> list[list[object]]:
    """Legacy helper kept for compatibility with older code paths."""
    return [getattr(result, attr) for attr, _ in _SECTIONS]


def _count_severities(findings: list[object]) -> tuple[int, int]:
    critical = sum(
        1 for finding in findings
        if getattr(finding, "severity", "") in ("critical", "error")
    )
    warning = sum(
        1 for finding in findings if getattr(finding, "severity", "") == "warning"
    )
    return critical, warning


def _section_slug(attr: str) -> str:
    return attr.replace("_", "-")


def _project_name(project_path: str) -> str:
    normalised = str(project_path).replace("\\", "/").rstrip("/")
    return normalised.split("/")[-1] if normalised else str(project_path)


def _detector_weight(detector_id: str, findings: list[object]) -> float:
    weights = _DETECTOR_WEIGHTS.get(detector_id, {})
    return sum(weights.get(getattr(f, "severity", "warning"), 1.0) for f in findings)


def _impact_label_class(detector_weight: float) -> tuple[str, str]:
    if detector_weight == 0:
        return "NONE", "impact-none"
    if detector_weight <= 3:
        return "LOW", "impact-low"
    if detector_weight <= 8:
        return "MEDIUM", "impact-medium"
    return "HIGH", "impact-high"


def _hero_insights(result: AnalysisResult) -> list[tuple[str, str]]:
    insights: list[tuple[str, str]] = []

    for detector_id, title in _SECTIONS:
        findings = getattr(result, detector_id, [])
        if detector_id in result.skipped_detectors:
            continue
        if not findings:
            continue

        critical_count, warning_count = _count_severities(findings)
        severity = "critical" if critical_count else "warning"

        if detector_id == "missing_service_layer":
            text = f"Service layer pressure — {len(findings)} view issue(s) detected"
        elif detector_id == "migration_safety":
            text = f"Migration risk active — {len(findings)} unsafe operation(s)"
        elif detector_id == "god_apps":
            top = findings[0]
            text = (
                f"God app detected — {_e(getattr(top, 'app_path', 'app'))} owns "
                f"{getattr(top, 'percentage', '?')}% of the codebase"
            )
        elif detector_id == "circular_imports":
            text = f"Circular dependency risk — {len(findings)} cycle(s) detected"
        elif detector_id == "fat_models":
            text = f"Model bloat detected — {len(findings)} fat model(s)"
        elif detector_id == "direct_sql":
            text = f"Direct SQL usage — {len(findings)} raw query hotspot(s)"
        elif detector_id == "n_plus_one":
            text = f"Query inefficiency risk — {len(findings)} N+1 signal(s)"
        elif detector_id == "celery_tasks":
            text = f"Task reliability gap — {len(findings)} task(s) missing retry"
        elif detector_id == "n1_serializer_risk":
            text = f"Serializer N+1 risk — {len(findings)} pattern(s) detected"
        elif detector_id == "secret_leakage":
            text = f"Secret leakage risk — {len(findings)} exposure(s) detected"
        else:
            text = f"{title} — {len(findings)} finding(s)"

        insights.append((severity, text))

    if not insights:
        return [("clean", "All detectors clean — no architectural issues found")]

    insights.sort(key=lambda item: 0 if item[0] == "critical" else 1)

    if len(insights) < 4:
        clean_titles = [
            title
            for detector_id, title in _SECTIONS
            if detector_id not in result.skipped_detectors
            and not getattr(result, detector_id)
        ]
        for title in clean_titles:
            insights.append(("clean", f"{title} — Clean"))
            if len(insights) >= 4:
                break

    return insights[:4]


def _detector_badges(findings: list[object]) -> str:
    critical_count, warning_count = _count_severities(findings)
    parts: list[str] = []
    if critical_count:
        parts.append(
            f'<span class="badge badge-critical">{critical_count} critical</span>'
        )
    if warning_count:
        parts.append(
            f'<span class="badge badge-warning">{warning_count} warning</span>'
        )
    return "".join(parts)


def _finding_path(finding: object) -> str:
    if hasattr(finding, "file_path"):
        return str(getattr(finding, "file_path"))
    if hasattr(finding, "file"):
        return str(getattr(finding, "file"))
    if hasattr(finding, "app_path"):
        return str(getattr(finding, "app_path"))
    return "project"


def _finding_title(finding: object) -> str:
    if hasattr(finding, "detector") and getattr(finding, "detector") == "N1SerializerRisk":
        msg = str(getattr(finding, "message", ""))
        return _e(msg[:80] + "…" if len(msg) > 80 else msg)
    if hasattr(finding, "kind") and hasattr(finding, "detail"):
        kind = getattr(finding, "kind")
        label = {"hardcoded_secret": "Hardcoded secret", "logged_secret": "Secret logged", "debug_true": "DEBUG = True"}.get(kind, kind)
        return _e(label)
    if hasattr(finding, "class_name"):
        return f"{_e(getattr(finding, 'class_name'))}"
    if hasattr(finding, "view_name"):
        return f"{_e(getattr(finding, 'view_name'))}()"
    if hasattr(finding, "task_name"):
        return f"{_e(getattr(finding, 'task_name'))}()"
    if hasattr(finding, "cycle_display"):
        return "Circular import detected"
    if hasattr(finding, "app_path") and hasattr(finding, "percentage"):
        return "God app detected"
    if hasattr(finding, "pattern"):
        return "Raw SQL detected"
    if hasattr(finding, "migration_name") and hasattr(finding, "operation"):
        operation = _e(getattr(finding, "operation"))
        migration = _e(getattr(finding, "migration_name"))
        return f"{operation} in {migration}"
    if hasattr(finding, "line_number"):
        return "Possible N+1 query risk"
    return _e(str(finding))


def _finding_summary(finding: object) -> str:
    severity = str(getattr(finding, "severity", "warning"))

    if hasattr(finding, "detector") and getattr(finding, "detector") == "N1SerializerRisk":
        return f"line {_e(str(getattr(finding, 'line', '?')))}"

    if hasattr(finding, "kind") and hasattr(finding, "detail"):
        return f"line {_e(str(getattr(finding, 'line_number', '?')))} · {_e(getattr(finding, 'detail'))}"

    if hasattr(finding, "class_name") and hasattr(finding, "method_count"):
        return f"{getattr(finding, 'method_count')} methods"

    if hasattr(finding, "app_path") and hasattr(finding, "percentage"):
        return (
            f"{getattr(finding, 'percentage')}% of project code · "
            f"{getattr(finding, 'app_loc'):,} / {getattr(finding, 'total_loc'):,} lines"
        )

    if hasattr(finding, "cycle_display"):
        return _e(getattr(finding, "cycle_display"))

    if hasattr(finding, "view_name") and hasattr(finding, "orm_call_count"):
        orm_call_count = int(getattr(finding, "orm_call_count"))
        call_label = "call" if orm_call_count == 1 else "calls"
        verb = "contains" if severity == "critical" else "makes"
        return f"{verb} {orm_call_count} direct ORM {call_label}"

    if hasattr(finding, "task_name"):
        return (
            "high-stakes task, no retry configured"
            if severity == "critical"
            else "no retry configured"
        )

    if hasattr(finding, "pattern") and hasattr(finding, "line_number"):
        return (
            f"line {getattr(finding, 'line_number')} · "
            f"{_e(getattr(finding, 'pattern'))}"
        )

    if hasattr(finding, "migration_name") and hasattr(finding, "operation"):
        model = getattr(finding, "model_name", "")
        field = getattr(finding, "field_name", "")
        if model and field:
            return f"{_e(model)}.{_e(field)}"
        if model:
            return _e(model)
        return _e(getattr(finding, "message"))

    if hasattr(finding, "line_number"):
        return f"line {getattr(finding, 'line_number')} · ORM call inside loop"

    return _e(str(finding))


def _finding_rows(finding: object, detector_title: str) -> list[tuple[str, str]]:
    if hasattr(finding, "kind") and hasattr(finding, "detail"):
        kind = getattr(finding, "kind")
        label = {"hardcoded_secret": "Hardcoded secret", "logged_secret": "Secret logged", "debug_true": "DEBUG = True"}.get(kind, kind)
        return [
            ("Detector", _e(detector_title)),
            ("Severity", _e(str(getattr(finding, "severity", "warning")).upper())),
            ("File", _e(str(getattr(finding, "file_path", "")))),
            ("Line", _e(str(getattr(finding, "line_number", "")))),
            ("Issue", _e(label)),
            ("Detail", _e(str(getattr(finding, "detail", "")))),
        ]

    rows: list[tuple[str, str]] = [
        ("Detector", _e(detector_title)),
        ("Severity", _e(str(getattr(finding, "severity", "warning")).upper())),
    ]

    if hasattr(finding, "detector") and getattr(finding, "detector") == "N1SerializerRisk":
        rows.append(("File", _e(str(getattr(finding, "file", "")))))
        rows.append(("Line", _e(str(getattr(finding, "line", "")))))
        rows.append(("Issue", _e(str(getattr(finding, "message", "")))))
        return rows

    if hasattr(finding, "class_name") and hasattr(finding, "method_count"):
        rows.append(("Methods", _e(getattr(finding, "method_count"))))

    elif hasattr(finding, "app_path") and hasattr(finding, "percentage"):
        rows.append(("Ownership", f"{_e(getattr(finding, 'percentage'))}%"))
        rows.append(
            (
                "Lines",
                f"{_e(getattr(finding, 'app_loc'))} / {_e(getattr(finding, 'total_loc'))}",
            )
        )

    elif hasattr(finding, "cycle_display"):
        rows.append(("Cycle", _e(getattr(finding, "cycle_display"))))

    elif hasattr(finding, "view_name") and hasattr(finding, "orm_call_count"):
        rows.append(("ORM Calls", _e(getattr(finding, "orm_call_count"))))

    elif hasattr(finding, "task_name"):
        rows.append(("Reliability", "Retry configuration missing"))

    elif hasattr(finding, "pattern") and hasattr(finding, "line_number"):
        rows.append(("Line", _e(getattr(finding, "line_number"))))
        rows.append(("Pattern", _e(getattr(finding, "pattern"))))

    elif hasattr(finding, "migration_name") and hasattr(finding, "operation"):
        rows.append(("Operation", _e(getattr(finding, "operation"))))
        rows.append(("Advice", _e(getattr(finding, "message"))))

    elif hasattr(finding, "line_number"):
        rows.append(("Line", _e(getattr(finding, "line_number"))))
        rows.append(("Signal", "Potential N+1 query pattern"))

    return rows


def _render_accordion_card(finding: object, detector_title: str, index: int) -> str:
    """Render a finding that carries a code_snippet as an HTML <details> accordion."""
    severity = str(getattr(finding, "severity", "warning"))
    # Map "error" to "critical" for filter compatibility
    display_severity = "critical" if severity in ("critical", "error") else severity
    sev_css = f"severity-{severity}"

    message = _e(str(getattr(finding, "message", "")))
    file_path = _e(_finding_path(finding))
    line = _e(str(getattr(finding, "line", getattr(finding, "line_number", ""))))

    snippet: dict = getattr(finding, "code_snippet", {})
    start_line = snippet.get("start_line", "")
    end_line = snippet.get("end_line", "")
    lines: list[str] = snippet.get("lines", [])
    code_text = _e("\n".join(lines))

    if start_line and end_line and start_line != end_line:
        line_range = f"lines {start_line}–{end_line}"
    elif start_line:
        line_range = f"line {start_line}"
    else:
        line_range = ""

    return (
        f'<article class="fc" data-severity="{_e(display_severity)}">'
        f'<details class="issue-accordion">'
        f'<summary class="issue-summary">'
        f'<span class="issue-sev {sev_css}">{_e(severity.upper())}</span>'
        f'<span class="issue-msg">{message}</span>'
        f'<span class="issue-loc">{file_path}:{line}</span>'
        f'</summary>'
        f'<div class="issue-detail">'
        f'<div class="issue-file-path">{file_path} {_e(line_range)}</div>'
        f'<pre class="code-block"><code class="language-python">{code_text}</code></pre>'
        f'</div>'
        f'</details>'
        f'</article>'
    )


def _render_finding_card(finding: object, detector_title: str, index: int) -> str:
    if hasattr(finding, "code_snippet"):
        return _render_accordion_card(finding, detector_title, index)

    severity = str(getattr(finding, "severity", "warning"))
    severity_class = "sv-cr" if severity in ("critical", "error") else "sv-wa"
    display_severity = "critical" if severity in ("critical", "error") else severity
    path = _e(_finding_path(finding))
    title = _finding_title(finding)
    summary = _finding_summary(finding)

    rows_html = "".join(
        (
            '<div class="fc-row">'
            f'<span class="fc-lbl">{_e(label)}</span>'
            f'<span class="fc-val">{value}</span>'
            "</div>"
        )
        for label, value in _finding_rows(finding, detector_title)
    )

    return (
        f'<article class="fc" data-severity="{_e(display_severity)}">'
        f'<button class="fc-top" type="button" onclick="toggleCard(\'card-{index}\', this)">'
        f'<span class="sv {severity_class}">{_e(severity.upper())}</span>'
        f'<code class="fc-path">{path}</code>'
        f'<span class="fc-fn">{title}</span>'
        f'<span class="fc-chev" aria-hidden="true">▼</span>'
        "</button>"
        f'<div class="fc-det">{summary}</div>'
        f'<div class="fc-body" id="card-{index}"><div class="fc-rows">{rows_html}</div></div>'
        "</article>"
    )


def _render_group(
    attr: str,
    title: str,
    findings: list[object],
    skipped: bool,
    start_index: int,
) -> tuple[str, int]:
    counts_html = _detector_badges(findings)
    body: str
    card_index = start_index

    if skipped:
        body = '<div class="fg-note section-skipped">⊘ Skipped (--ignore flag)</div>'
    elif not findings:
        body = '<div class="fg-note section-clean">No issues found</div>'
    else:
        cards: list[str] = []
        for finding in findings:
            cards.append(_render_finding_card(finding, title, card_index))
            card_index += 1
        body = "".join(cards)

    html_block = (
        f'<section class="fg" data-section="{_e(_section_slug(attr))}">'
        f'<button class="fg-head" type="button" onclick="toggleGroup(\'group-{_e(_section_slug(attr))}\', this)">'
        '<span class="fg-left">'
        f'<span class="fg-name">{_e(title)}</span>'
        f"{counts_html}"
        "</span>"
        '<span class="fg-chev" aria-hidden="true">▼</span>'
        "</button>"
        f'<div class="fg-body" id="group-{_e(_section_slug(attr))}">{body}</div>'
        "</section>"
    )
    return html_block, card_index


def _render_breakdown(result: AnalysisResult) -> str:
    visible = [
        (detector_id, title, getattr(result, detector_id))
        for detector_id, title in _SECTIONS
        if detector_id not in result.skipped_detectors
    ]
    max_weight = max((_detector_weight(detector_id, findings) for detector_id, _, findings in visible), default=1.0)
    max_weight = max(max_weight, 1.0)

    rows: list[str] = []
    for detector_id, title, findings in visible:
        detector_weight = _detector_weight(detector_id, findings)
        label, impact_class = _impact_label_class(detector_weight)
        count = len(findings)
        count_text = f"{count} finding{'s' if count != 1 else ''}" if count else "Clean"
        row_class = (
            "r-cr"
            if any(getattr(f, "severity", "") == "critical" for f in findings)
            else "r-wa"
            if findings
            else "r-ok"
        )
        bar_class = (
            "b-cr"
            if row_class == "r-cr"
            else "b-wa"
            if row_class == "r-wa"
            else "b-ok"
        )
        width = 0 if detector_weight == 0 else max(6, round((detector_weight / max_weight) * 100))
        weight_text = f"{detector_weight:.1f}" if detector_weight else "—"

        rows.append(
            f'<div class="det-row {row_class}">'
            '<div class="d-info">'
            f'<span class="d-name">{_e(title)}</span>'
            f'<span class="d-cnt">{_e(count_text)}</span>'
            "</div>"
            f'<div class="d-bg"><div class="d-bar {bar_class}" style="--w:{width}%"></div></div>'
            f'<span class="d-wt">{_e(weight_text)}</span>'
            f'<span class="imp {impact_class}">{_e(label)}</span>'
            "</div>"
        )

    return (
        '<section class="s-wrap detector-section" data-reveal>'
        '<div class="s-eye">Detector Analysis</div>'
        '<div class="s-title">Score Breakdown</div>'
        f'<div class="det-wrap">{"".join(rows)}</div>'
        "</section>"
    )


def _render_detector_status_grid(result: AnalysisResult) -> str:
    cards: list[str] = []
    for detector_id, title in _SECTIONS:
        if detector_id in result.skipped_detectors:
            cards.append(
                '<div class="clean-row clean-skip">'
                '<span class="clean-dot clean-dot-skip"></span>'
                f"{_e(title)} — Skipped"
                "</div>"
            )
            continue
        if not getattr(result, detector_id):
            cards.append(
                '<div class="clean-row">'
                '<span class="clean-dot"></span>'
                f"{_e(title)} — Clean"
                "</div>"
            )

    if not cards:
        return ""

    return (
        '<section class="s-wrap" data-reveal>'
        '<div class="s-eye">Coverage</div>'
        '<div class="s-title">Detector Status</div>'
        f'<div class="clean-grid">{"".join(cards)}</div>'
        "</section>"
    )


def generate_html(result: AnalysisResult, project_path: str) -> str:
    """Return a self-contained HTML report string."""
    score = compute_score(result, project_path)
    grade = score_grade(score)
    label = score_label(score)
    color = _score_color(score)
    grade_card_class = _score_card_class(score)
    project_name = _project_name(project_path)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status_text = _score_status_text(score)
    status_class = _score_status_class(score)

    total_warnings = sum(
        sum(1 for finding in getattr(result, attr) if getattr(finding, "severity", "") == "warning")
        for attr, _ in _SECTIONS
    )
    total_criticals = sum(
        sum(
            1 for finding in getattr(result, attr)
            if getattr(finding, "severity", "") in ("critical", "error")
        )
        for attr, _ in _SECTIONS
    )
    clean_sections = sum(
        1
        for attr, _ in _SECTIONS
        if not getattr(result, attr) and attr not in result.skipped_detectors
    )
    total_findings = total_criticals + total_warnings

    insights_html = "".join(
        f'<div class="h-ind {("bad" if severity == "critical" else "warn" if severity == "warning" else "good")}">'
        f'<span class="i-d {("i-pulse" if severity == "critical" else "i-wa" if severity == "warning" else "i-ok")}"></span>'
        f"{text}"
        "</div>"
        for severity, text in _hero_insights(result)
    )

    breakdown_html = _render_breakdown(result)

    groups: list[str] = []
    card_index = 0
    for attr, title in _SECTIONS:
        group_html, card_index = _render_group(
            attr,
            title,
            getattr(result, attr),
            skipped=attr in result.skipped_detectors,
            start_index=card_index,
        )
        groups.append(group_html)
    groups_html = "\n".join(groups)

    detector_status_html = _render_detector_status_grid(result)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>django-arch-check · Architecture Intelligence</title>
  <style>
    :root {{
      --bg:#05070f; --s1:#090d1a; --s2:#0d1220; --s3:#111826;
      --fg:#e4e8f4; --mu:#464e68; --mu2:#606880;
      --br:#181f34; --br2:#1d2540;
      --critical:oklch(64% 0.26 22); --critical-dim:oklch(50% 0.20 22);
      --critical-bg:oklch(14% 0.09 22); --critical-b:oklch(28% 0.15 22);
      --warning:oklch(76% 0.18 52); --warning-dim:oklch(60% 0.14 52);
      --warning-bg:oklch(14% 0.07 52); --warning-b:oklch(30% 0.11 52);
      --clean:oklch(68% 0.18 162);
      --clean-bg:oklch(14% 0.07 162); --clean-b:oklch(28% 0.11 162);
      --accent:oklch(72% 0.20 202);
      --accent-bg:oklch(14% 0.07 202); --accent-b:oklch(26% 0.10 202);
      --impact-medium:oklch(70% 0.15 40);
      --body-grad-1:oklch(18% 0.04 240 / 0.7);
      --body-grad-2:oklch(16% 0.09 22 / 0.34);
      --body-grad-3:#05070f;
      --body-grad-4:#060913;
      --grid-line:oklch(30% 0.02 240 / 0.14);
      --scanline:rgba(2,4,10,.14);
      --nav-bg:oklch(5% 0.02 240 / .84);
      --hero-sub:#b1b9cf;
      --orbit-border:rgba(255,255,255,.07);
      --orbit-inner-border:rgba(255,255,255,.05);
      --orbit-grid:rgba(255,255,255,.035);
      --orbit-sheen:rgba(255,255,255,.03);
      --card-shadow:0 30px 70px rgba(0,0,0,.35);
      --card-shadow-soft:0 12px 36px rgba(0,0,0,.22);
      --mono:'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace;
      --sans:'Aptos','Segoe UI',Tahoma,sans-serif;
    }}

    body[data-theme="light"] {{
      --bg:#f3f6fb; --s1:#ffffff; --s2:#eef3fb; --s3:#e6edf7;
      --fg:#0f1728; --mu:#66748f; --mu2:#4e5d79;
      --br:#d5deeb; --br2:#c4d0e2;
      --critical-bg:oklch(95% 0.03 22); --critical-b:oklch(78% 0.11 22);
      --warning-bg:oklch(96% 0.03 52); --warning-b:oklch(82% 0.10 52);
      --clean-bg:oklch(96% 0.03 162); --clean-b:oklch(78% 0.09 162);
      --accent-bg:oklch(96% 0.03 202); --accent-b:oklch(77% 0.10 202);
      --body-grad-1:oklch(94% 0.03 240 / 0.95);
      --body-grad-2:oklch(95% 0.04 30 / 0.65);
      --body-grad-3:#f3f6fb;
      --body-grad-4:#fbfdff;
      --grid-line:oklch(80% 0.02 240 / 0.55);
      --scanline:rgba(130,150,180,.05);
      --nav-bg:rgba(246,249,253,.86);
      --hero-sub:#4c5d78;
      --orbit-border:rgba(112,132,170,.16);
      --orbit-inner-border:rgba(112,132,170,.12);
      --orbit-grid:rgba(112,132,170,.10);
      --orbit-sheen:rgba(255,255,255,.72);
      --card-shadow:0 22px 48px rgba(63,86,124,.12);
      --card-shadow-soft:0 10px 24px rgba(63,86,124,.10);
    }}

    *,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
    html {{ scroll-behavior:smooth; }}
    body {{
      font-family:var(--sans); font-size:14px; line-height:1.6; color:var(--fg);
      background:
        radial-gradient(circle at 15% 20%, var(--body-grad-1), transparent 28%),
        radial-gradient(circle at 82% 22%, var(--body-grad-2), transparent 24%),
        linear-gradient(180deg, var(--body-grad-4) 0%, var(--body-grad-3) 100%);
      -webkit-font-smoothing:antialiased; overflow-x:hidden;
    }}
    body::before {{
      content:''; position:fixed; inset:0; pointer-events:none; z-index:0;
      background-image:
        linear-gradient(var(--grid-line) 1px, transparent 1px),
        linear-gradient(90deg, var(--grid-line) 1px, transparent 1px);
      background-size:48px 48px;
      mask-image:linear-gradient(180deg, rgba(0,0,0,.4), rgba(0,0,0,.07));
      animation:gridMove 26s linear infinite;
    }}
    body::after {{
      content:''; position:fixed; inset:0; pointer-events:none; z-index:0;
      background:repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        var(--scanline) 2px,
        var(--scanline) 4px
      );
      opacity:.25;
    }}
    ::-webkit-scrollbar {{ width:6px; height:6px; }}
    ::-webkit-scrollbar-track {{ background:var(--bg); }}
    ::-webkit-scrollbar-thumb {{ background:var(--br2); border-radius:4px; }}

    .nav {{
      position:sticky; top:0; z-index:30; height:54px;
      background:var(--nav-bg);
      backdrop-filter:blur(22px) saturate(1.5);
      border-bottom:1px solid var(--br);
    }}
    .nav-in {{
      max-width:1180px; margin:0 auto; height:100%; padding:0 28px;
      display:flex; align-items:center; gap:14px; flex-wrap:wrap;
    }}
    .n-brand {{
      font-family:var(--mono); font-size:12px; font-weight:700; white-space:nowrap;
      display:flex; align-items:center; gap:8px;
    }}
    .n-ico {{ color:{color}; }}
    .n-div {{ width:1px; height:16px; background:var(--br2); flex-shrink:0; }}
    .n-score {{ display:flex; align-items:baseline; gap:5px; }}
    .n-num {{
      font-family:var(--mono); font-size:19px; font-weight:900; color:{color};
      letter-spacing:-.03em; line-height:1;
    }}
    .n-sep {{ font-size:12px; color:var(--mu); }}
    .n-grade {{
      font-size:12px; font-weight:800; color:{color};
      border:1.5px solid {color}; border-radius:4px; padding:1px 6px;
    }}
    .n-pills {{ display:flex; gap:6px; }}
    .pill {{
      font-family:var(--mono); font-size:10px; font-weight:700;
      padding:2px 8px; border-radius:4px; letter-spacing:.03em; text-transform:uppercase;
    }}
    .p-cr {{ background:var(--critical-bg); color:var(--critical); border:1px solid var(--critical-b); }}
    .p-wa {{ background:var(--warning-bg); color:var(--warning); border:1px solid var(--warning-b); }}
    .n-filters {{
      margin-left:auto; display:flex; gap:2px; background:var(--s3);
      border-radius:8px; padding:3px; border:1px solid var(--br);
    }}
    .theme-btn {{
      font-family:var(--sans); font-size:11px; font-weight:700;
      padding:4px 10px; border:1px solid var(--br); border-radius:8px; cursor:pointer;
      background:var(--s1); color:var(--fg); letter-spacing:.04em; text-transform:uppercase;
    }}
    .theme-btn:hover {{ background:var(--s2); }}
    .f-btn {{
      font-family:var(--sans); font-size:11px; font-weight:600;
      padding:4px 12px; border:none; border-radius:6px; cursor:pointer;
      background:transparent; color:var(--mu); letter-spacing:.05em; text-transform:uppercase;
    }}
    .f-btn:hover {{ color:var(--fg); }}
    .f-btn.active {{ background:var(--s2); color:var(--fg); }}

    .hero {{
      position:relative; z-index:1; min-height:78vh; display:flex; align-items:center;
      padding:76px 0 54px; overflow:hidden;
    }}
    .hero-shell {{
      max-width:1180px; margin:0 auto; width:100%; padding:0 44px;
      display:grid; grid-template-columns:minmax(0, 1.2fr) minmax(260px, .8fr); gap:36px;
      align-items:center;
    }}
    .h-content {{ max-width:720px; }}
    .h-eye {{
      font-family:var(--mono); font-size:11px; color:var(--mu2);
      letter-spacing:.12em; text-transform:uppercase; margin-bottom:28px;
    }}
    .h-score {{
      display:flex; align-items:flex-end; gap:18px; margin-bottom:18px;
    }}
    .h-num {{
      font-size:clamp(5rem, 17vw, 9.5rem); font-weight:900; line-height:.88;
      letter-spacing:-.05em; color:{color}; text-shadow:0 0 80px color-mix(in srgb, {color} 35%, transparent);
    }}
    .h-meta {{ display:flex; flex-direction:column; gap:6px; padding-bottom:12px; }}
    .h-denom {{ font-family:var(--mono); font-size:1.5rem; color:var(--mu); }}
    .h-grade {{ font-family:var(--mono); font-size:2.7rem; font-weight:900; color:{color}; line-height:1; }}
    .h-title {{
      font-size:clamp(2rem, 4vw, 3.4rem); font-weight:900; letter-spacing:-.04em;
      line-height:1.02; margin-bottom:14px;
    }}
    .h-sub {{
      max-width:62ch; font-size:15px; color:var(--hero-sub); margin-bottom:22px;
    }}
    .h-badge {{
      display:inline-flex; align-items:center; gap:10px; border-radius:6px;
      padding:8px 16px; font-family:var(--mono); font-size:12px; font-weight:700;
      letter-spacing:.10em; margin-bottom:28px;
    }}
    .status-critical {{ background:var(--critical-bg); border:1px solid var(--critical-b); color:var(--critical); }}
    .status-warning {{ background:var(--warning-bg); border:1px solid var(--warning-b); color:var(--warning); }}
    .status-clean {{ background:var(--clean-bg); border:1px solid var(--clean-b); color:var(--clean); }}
    .h-dot {{ width:8px; height:8px; border-radius:50%; background:currentColor; }}
    .h-inds {{ display:flex; flex-direction:column; gap:10px; margin-bottom:30px; }}
    .h-ind {{ display:flex; align-items:center; gap:10px; font-family:var(--mono); font-size:12px; letter-spacing:.04em; }}
    .h-ind.bad {{ color:var(--critical); }}
    .h-ind.warn {{ color:var(--warning); }}
    .h-ind.good {{ color:var(--clean); }}
    .i-d {{ width:6px; height:6px; border-radius:50%; flex-shrink:0; }}
    .i-pulse {{ background:var(--critical); box-shadow:0 0 0 0 color-mix(in srgb, var(--critical) 65%, transparent); animation:critPulse 1.3s ease-in-out infinite; }}
    .i-wa {{ background:var(--warning); }}
    .i-ok {{ background:var(--clean); }}
    .h-foot {{
      font-family:var(--mono); font-size:12px; color:var(--mu);
      display:flex; gap:12px; align-items:center; flex-wrap:wrap;
    }}
    .h-code {{
      color:var(--accent); background:var(--accent-bg); border:1px solid var(--accent-b);
      padding:1px 6px; border-radius:3px; font-family:inherit;
    }}
    .hero-side {{
      position:relative; min-height:420px; display:flex; align-items:center; justify-content:center;
    }}
    .hero-side::before, .hero-side::after {{
      content:''; position:absolute; border-radius:50%; filter:blur(2px);
    }}
    .hero-side::before {{
      width:280px; height:280px;
      background:radial-gradient(circle, color-mix(in srgb, {color} 24%, transparent) 0%, transparent 68%);
      box-shadow:
        0 0 0 1px color-mix(in srgb, {color} 28%, transparent),
        0 0 100px color-mix(in srgb, {color} 16%, transparent);
      animation:slowFloat 9s ease-in-out infinite;
    }}
    .hero-side::after {{
      width:390px; height:390px;
      border:1px solid color-mix(in srgb, {color} 30%, transparent);
      background:
        radial-gradient(circle at 50% 50%, color-mix(in srgb, {color} 10%, transparent), transparent 58%),
        repeating-radial-gradient(circle at center, transparent 0 24px, rgba(255,255,255,.03) 24px 25px);
      animation:slowFloat 12s ease-in-out infinite reverse;
    }}
    .hero-orbit {{
      position:absolute; inset:14% 8%;
      border:1px solid var(--orbit-border); border-radius:32px;
      background:
        linear-gradient(135deg, var(--orbit-sheen), transparent 38%),
        radial-gradient(circle at 65% 35%, color-mix(in srgb, {color} 12%, transparent), transparent 35%),
        var(--s1);
      box-shadow:inset 0 1px 0 var(--orbit-inner-border), var(--card-shadow);
      overflow:hidden;
    }}
    .hero-orbit::before {{
      content:''; position:absolute; inset:16px;
      border:1px solid var(--orbit-inner-border); border-radius:24px;
      background-image:
        linear-gradient(var(--orbit-grid) 1px, transparent 1px),
        linear-gradient(90deg, var(--orbit-grid) 1px, transparent 1px);
      background-size:28px 28px;
      mask-image:radial-gradient(circle, black 52%, transparent 92%);
    }}
    .orbit-score {{
      position:absolute; inset:0; display:flex; flex-direction:column;
      align-items:center; justify-content:center;
      font-family:var(--mono); text-align:center;
    }}
    .orbit-meter {{
      position:relative; width:200px; height:110px; flex-shrink:0;
    }}
    .orbit-meter svg {{ width:100%; height:100%; overflow:visible; }}
    .meter-track {{
      fill:none; stroke:var(--br2); stroke-width:10; stroke-linecap:round;
    }}
    .meter-fill {{
      fill:none; stroke:{color}; stroke-width:10; stroke-linecap:round;
      filter:drop-shadow(0 0 6px color-mix(in srgb, {color} 55%, transparent));
      transition:stroke-dashoffset 1.4s cubic-bezier(.2,.8,.2,1) .3s;
    }}
    .orbit-num {{
      position:absolute; bottom:0; left:50%; transform:translateX(-50%);
      font-size:3.2rem; font-weight:900; line-height:1; color:{color};
      letter-spacing:-.05em;
      text-shadow:0 0 28px color-mix(in srgb, {color} 45%, transparent);
    }}
    .orbit-score-meta {{
      margin-top:14px; display:flex; flex-direction:column; align-items:center; gap:6px;
    }}
    .orbit-score-meta span {{
      font-size:11px; letter-spacing:.1em; color:var(--mu2); text-transform:uppercase;
    }}
    .orbit-score-meta em {{
      font-style:normal; font-size:11px; font-weight:800; color:{color};
      padding:3px 10px; border-radius:999px;
      border:1px solid color-mix(in srgb, {color} 45%, transparent);
      background:color-mix(in srgb, {color} 12%, transparent);
    }}

    .container {{ position:relative; z-index:1; max-width:1140px; margin:0 auto; padding:10px 44px 100px; }}
    .s-wrap {{ margin-bottom:64px; }}
    .s-eye {{ font-family:var(--mono); font-size:11px; color:var(--mu2); letter-spacing:.12em; text-transform:uppercase; margin-bottom:6px; }}
    .s-title {{ font-size:1.8rem; font-weight:800; letter-spacing:-.03em; margin-bottom:24px; }}
    [data-reveal] {{ opacity:1; transform:none; transition:opacity .6s ease, transform .6s ease; }}
    body.js-enhanced [data-reveal]:not(.revealed) {{ opacity:0; transform:translateY(18px); }}
    [data-reveal].revealed {{ opacity:1; transform:none; }}

    .i-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
    .ic {{
      background:var(--s1); border:1px solid var(--br); border-radius:14px;
      padding:24px 22px; box-shadow:var(--card-shadow-soft);
    }}
    .ic.g-cr {{ border-color:var(--critical-b); }}
    .ic.g-wa {{ border-color:var(--warning-b); }}
    .ic.g-ok {{ border-color:var(--clean-b); }}
    .ic-num {{
      font-size:3.6rem; font-weight:900; line-height:1; letter-spacing:-.04em;
      font-variant-numeric:tabular-nums;
    }}
    .ic.g-cr .ic-num,.ic.g-cr .ic-sub {{ color:var(--critical); }}
    .ic.g-wa .ic-num,.ic.g-wa .ic-sub {{ color:var(--warning); }}
    .ic.g-ok .ic-num,.ic.g-ok .ic-sub {{ color:var(--clean); }}
    .ic-lbl {{ margin-top:8px; font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--mu); font-weight:600; }}
    .ic-sub {{ font-size:11px; margin-top:4px; }}

    .det-wrap {{ background:var(--s1); border:1px solid var(--br); border-radius:14px; overflow:hidden; }}
    .det-row {{
      display:grid; grid-template-columns:220px 1fr 76px 88px; align-items:center;
      gap:18px; padding:14px 24px; border-bottom:1px solid var(--br);
    }}
    .det-row:last-child {{ border-bottom:none; }}
    .det-row:hover {{ background:var(--s2); }}
    .d-info {{ display:flex; flex-direction:column; gap:2px; }}
    .d-name {{ font-size:13px; font-weight:600; }}
    .d-cnt {{ font-family:var(--mono); font-size:11px; color:var(--mu); }}
    .r-cr .d-name {{ color:var(--critical); }}
    .r-wa .d-name {{ color:var(--warning); }}
    .r-ok .d-name {{ color:var(--mu2); }}
    .d-bg {{ background:var(--s3); border-radius:999px; height:6px; overflow:hidden; }}
    .d-bar {{
      height:100%; border-radius:999px; width:var(--w,0%);
      transition:width 1.2s cubic-bezier(.2,.8,.2,1) .2s;
    }}
    .b-cr {{ background:linear-gradient(90deg,var(--critical-dim),var(--critical)); }}
    .b-wa {{ background:linear-gradient(90deg,var(--warning-dim),var(--warning)); }}
    .b-ok {{ background:linear-gradient(90deg,color-mix(in srgb, var(--clean) 35%, transparent),var(--clean)); }}
    .d-wt {{ font-family:var(--mono); font-size:12px; color:var(--mu); text-align:right; }}
    .imp {{
      font-family:var(--mono); font-size:10px; font-weight:700; padding:3px 8px;
      border-radius:4px; letter-spacing:.04em; text-align:center;
    }}
    .impact-high {{ background:var(--critical-bg); color:var(--critical); border:1px solid var(--critical-b); }}
    .impact-medium {{ background:oklch(14% 0.06 40); color:var(--impact-medium); border:1px solid oklch(30% 0.10 40); }}
    .impact-low {{ background:var(--warning-bg); color:var(--warning); border:1px solid var(--warning-b); }}
    .impact-none {{ background:var(--s3); color:var(--mu2); border:1px solid var(--br2); }}

    .fg {{
      background:var(--s1); border:1px solid var(--br); border-radius:12px;
      margin-bottom:10px; overflow:hidden;
    }}
    .fg.group-hidden {{ display:none; }}
    .fg-head {{
      width:100%; display:flex; align-items:center; justify-content:space-between;
      gap:14px; padding:14px 20px; border:none; background:var(--s2);
      color:inherit; cursor:pointer; text-align:left;
    }}
    .fg-head:hover {{ background:var(--s3); }}
    .fg-left {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
    .fg-name {{ font-size:14px; font-weight:700; }}
    .fg-chev {{ color:var(--mu); transition:transform .22s ease; }}
    .fg-head.collapsed .fg-chev {{ transform:rotate(-90deg); }}
    .fg-body.collapsed {{ display:none; }}
    .fg-note {{ padding:16px 20px; }}
    .section-clean,.section-skipped {{
      display:flex; align-items:center; gap:10px; font-size:13px; font-weight:600;
    }}
    .section-clean {{ color:var(--clean); }}
    .section-skipped {{ color:var(--mu2); }}
    .section-clean::before,.section-skipped::before {{
      content:''; width:7px; height:7px; border-radius:50%; flex-shrink:0;
    }}
    .section-clean::before {{ background:var(--clean); }}
    .section-skipped::before {{ background:var(--mu2); }}

    .badge {{
      display:inline-flex; align-items:center; padding:2px 7px; border-radius:4px;
      font-size:11px; font-weight:700; letter-spacing:.03em; text-transform:uppercase;
      font-variant-numeric:tabular-nums;
    }}
    .badge-critical {{ background:var(--critical-bg); color:var(--critical); border:1px solid var(--critical-b); }}
    .badge-warning {{ background:var(--warning-bg); color:var(--warning); border:1px solid var(--warning-b); }}

    .fc {{ border-top:1px solid var(--br); }}
    .fc:first-child {{ border-top:none; }}
    .fc.fc-hidden {{ display:none; }}
    .fc-top {{
      width:100%; display:flex; align-items:flex-start; gap:10px; flex-wrap:wrap;
      padding:13px 20px 6px; border:none; background:transparent; color:inherit;
      cursor:pointer; text-align:left;
    }}
    .fc:hover {{ background:var(--s2); }}
    .fc-chev {{ margin-left:auto; color:var(--mu); transition:transform .22s ease; }}
    .fc-top.open .fc-chev {{ transform:rotate(180deg); }}
    .sv {{
      font-family:var(--mono); font-size:10px; font-weight:700; padding:2px 7px;
      border-radius:4px; letter-spacing:.04em; flex-shrink:0; margin-top:2px;
    }}
    .sv-cr {{ background:var(--critical-bg); color:var(--critical); border:1px solid var(--critical-b); }}
    .sv-wa {{ background:var(--warning-bg); color:var(--warning); border:1px solid var(--warning-b); }}
    .fc-path {{
      font-family:var(--mono); font-size:11px; color:var(--accent);
      background:var(--accent-bg); border:1px solid var(--accent-b);
      padding:2px 6px; border-radius:4px; flex-shrink:0;
    }}
    .fc-fn {{ font-size:13px; font-weight:600; flex:1; min-width:0; }}
    .fc-det {{ padding:0 20px 12px 108px; font-size:13px; color:#b8bfd4; }}
    .fc-body {{ display:none; }}
    .fc-body.open {{ display:block; }}
    .fc-rows {{ padding:0 20px 16px 108px; display:flex; flex-direction:column; gap:7px; }}
    .fc-row {{ display:flex; gap:12px; align-items:flex-start; font-size:12px; }}
    .fc-lbl {{
      width:72px; flex-shrink:0; font-family:var(--mono); font-size:10px;
      font-weight:700; color:var(--mu); letter-spacing:.04em; padding-top:1px;
      text-transform:uppercase;
    }}
    .fc-val {{ color:#c8d0e3; }}

    .clean-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:8px; }}
    .clean-row {{
      display:flex; align-items:center; gap:10px; background:var(--s1); border:1px solid var(--clean-b);
      border-radius:8px; padding:11px 15px; font-family:var(--mono); font-size:11px; color:var(--clean);
    }}
    .clean-skip {{ color:var(--mu2); border-color:var(--br2); }}
    .clean-dot {{ width:6px; height:6px; border-radius:50%; background:var(--clean); flex-shrink:0; }}
    .clean-dot-skip {{ background:var(--mu2); }}

    footer {{
      margin-top:60px; padding-top:22px; border-top:1px solid var(--br);
      display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;
      font-family:var(--mono); font-size:11px; color:var(--mu);
    }}

    @keyframes gridMove {{
      from {{ background-position:0 0; }}
      to {{ background-position:48px 48px; }}
    }}
    @keyframes critPulse {{
      0%,100% {{ box-shadow:0 0 0 0 color-mix(in srgb, var(--critical) 65%, transparent); opacity:1; }}
      50% {{ box-shadow:0 0 0 6px transparent; opacity:.7; }}
    }}
    @keyframes slowFloat {{
      0%,100% {{ transform:translateY(0px) rotate(0deg); }}
      50% {{ transform:translateY(-12px) rotate(2deg); }}
    }}

    @media (max-width: 960px) {{
      .hero-shell {{ grid-template-columns:1fr; }}
      .i-grid {{ grid-template-columns:repeat(2,1fr); }}
      .det-row {{ grid-template-columns:160px 1fr 62px 78px; gap:10px; }}
      .hero-side {{ min-height:300px; }}
      .fc-det,.fc-rows {{ padding-left:20px; }}
    }}
    @media (max-width: 640px) {{
      .nav-in {{ padding:0 16px; }}
      .n-pills {{ display:none; }}
      .theme-btn {{ order:3; }}
      .hero-shell,.container {{ padding-left:20px; padding-right:20px; }}
      .i-grid {{ grid-template-columns:1fr 1fr; }}
      .det-row {{ grid-template-columns:1fr; }}
      .fc-top {{ padding-bottom:10px; }}
      .fc-det,.fc-rows {{ padding-left:20px; }}
      .n-filters {{ margin-left:0; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *,*::before,*::after {{ animation-duration:.01ms !important; transition-duration:.01ms !important; }}
    }}
    {_ACCORDION_CSS}
  </style>
</head>
<body>
  <nav class="nav">
    <div class="nav-in">
      <span class="n-brand"><span class="n-ico">◈</span>django-arch-check</span>
      <div class="n-div"></div>
      <div class="n-score">
        <span class="n-num">{score}</span>
        <span class="n-sep">/ 100</span>
        <span class="n-grade">{_e(grade)}</span>
      </div>
      <div class="n-pills">
        <span class="pill p-cr">{total_criticals} critical</span>
        <span class="pill p-wa">{total_warnings} warning</span>
      </div>
      <button class="theme-btn" id="theme-toggle" type="button" onclick="toggleTheme()" aria-label="Switch theme">Dark</button>
      <div class="n-filters">
        <button class="f-btn active" type="button" onclick="setFilter('all', this)">All</button>
        <button class="f-btn" type="button" onclick="setFilter('critical', this)">Critical</button>
        <button class="f-btn" type="button" onclick="setFilter('warning', this)">Warning</button>
      </div>
    </div>
  </nav>

  <section class="hero">
    <div class="hero-shell">
      <div class="h-content">
        <div class="h-eye">Architecture Intelligence Report · django-arch-check v{_e(__version__)}</div>
        <div class="h-score">
          <span class="h-num">{score}</span>
          <div class="h-meta">
            <span class="h-denom">/ 100</span>
            <span class="h-grade">{_e(grade)}</span>
          </div>
        </div>
        <div class="h-title">Architecture Report for <span style="color:{color}">{_e(project_name)}</span></div>
        <div class="h-sub">
          Weighted architectural health scoring across {len(_SECTIONS)} detectors.
          {total_findings} total finding(s), normalized by codebase size and detector risk.
        </div>
        <div class="h-badge {status_class}"><span class="h-dot"></span>{_e(status_text)}</div>
        <div class="h-inds">{insights_html}</div>
        <div class="h-foot">
          Project <code class="h-code">{_e(project_path)}</code>
          <span>·</span>
          <span>Generated {generated}</span>
        </div>
      </div>
      <div class="hero-side" aria-hidden="true">
        <div class="hero-orbit">
          <div class="orbit-score">
            <div class="orbit-meter">
              <svg viewBox="0 0 200 110" aria-hidden="true">
                <path class="meter-track"
                  d="M 10 100 A 90 90 0 0 1 190 100"/>
                <path class="meter-fill" id="meter-arc"
                  d="M 10 100 A 90 90 0 0 1 190 100"
                  stroke-dasharray="282.7"
                  stroke-dashoffset="282.7"/>
              </svg>
              <span class="orbit-num">{score}</span>
            </div>
            <div class="orbit-score-meta">
              <span>Health Score</span>
              <em>{_e(grade)} · {_e(label)}</em>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <main class="container">
    <section class="s-wrap" data-reveal>
      <div class="i-grid">
        <div class="ic {grade_card_class}"><div class="ic-num">{_e(grade)}</div><div class="ic-lbl">Health Grade</div><div class="ic-sub">Score {score} / 100</div></div>
        <div class="ic g-cr"><div class="ic-num">{total_criticals}</div><div class="ic-lbl">Critical</div><div class="ic-sub">Architecture violations</div></div>
        <div class="ic g-wa"><div class="ic-num">{total_warnings}</div><div class="ic-lbl">Warnings</div><div class="ic-sub">Requiring attention</div></div>
        <div class="ic g-ok"><div class="ic-num">{clean_sections}</div><div class="ic-lbl">Clean</div><div class="ic-sub">Detectors currently clean</div></div>
      </div>
    </section>

    {breakdown_html}

    <section class="s-wrap" data-reveal>
      <div class="s-eye">Intelligence Report</div>
      <div class="s-title">Architecture Findings</div>
      <div id="findings-root">{groups_html}</div>
    </section>

    {detector_status_html}

    <footer>
      <span>Score = 100 − density_penalty − absolute_penalty · weighted by detector risk</span>
      <span>django-arch-check v{_e(__version__)}</span>
    </footer>
  </main>

  <script>
    const THEME_KEY = 'django-arch-check-theme';

    function applyTheme(theme) {{
      const resolved = theme === 'light' ? 'light' : 'dark';
      document.body.dataset.theme = resolved;
      const btn = document.getElementById('theme-toggle');
      if (!btn) return;
      btn.textContent = resolved === 'light' ? 'Light' : 'Dark';
      btn.setAttribute(
        'aria-label',
        resolved === 'light' ? 'Switch to dark theme' : 'Switch to light theme'
      );
      btn.setAttribute(
        'title',
        resolved === 'light' ? 'Switch to dark theme' : 'Switch to light theme'
      );
    }}

    function toggleTheme() {{
      const next = document.body.dataset.theme === 'light' ? 'dark' : 'light';
      applyTheme(next);
      try {{
        localStorage.setItem(THEME_KEY, next);
      }} catch (error) {{
        // Ignore storage failures in locked-down browsers.
      }}
    }}

    function toggleGroup(id, btn) {{
      const body = document.getElementById(id);
      if (!body) return;
      body.classList.toggle('collapsed');
      btn.classList.toggle('collapsed');
    }}

    function toggleCard(id, btn) {{
      const body = document.getElementById(id);
      if (!body) return;
      body.classList.toggle('open');
      btn.classList.toggle('open');
    }}

    function setFilter(type, btn) {{
      document.querySelectorAll('.f-btn').forEach(el => el.classList.toggle('active', el === btn));
      document.querySelectorAll('.fg').forEach(group => {{
        const cards = group.querySelectorAll('.fc');
        if (!cards.length) {{
          group.classList.toggle('group-hidden', type !== 'all');
          return;
        }}
        let visible = 0;
        cards.forEach(card => {{
          const match = type === 'all' || card.dataset.severity === type;
          card.classList.toggle('fc-hidden', !match);
          if (match) visible++;
        }});
        group.classList.toggle('group-hidden', visible === 0);
      }});
    }}

    try {{
      applyTheme(localStorage.getItem(THEME_KEY) || 'dark');
    }} catch (error) {{
      applyTheme('dark');
    }}

    document.body.classList.add('js-enhanced');
    document.querySelectorAll('[data-reveal]').forEach((el) => {{
      if (el.getBoundingClientRect().top < window.innerHeight * 0.92) {{
        el.classList.add('revealed');
      }}
    }});

    const observer = new IntersectionObserver((entries) => {{
      entries.forEach((entry) => {{
        if (entry.isIntersecting) {{
          entry.target.classList.add('revealed');
        }}
      }});
    }}, {{ threshold: 0.12 }});

    document.querySelectorAll('[data-reveal]').forEach((el) => observer.observe(el));

    (function() {{
      var arc = document.getElementById('meter-arc');
      if (!arc) return;
      var score = {score};
      var total = 282.7;
      var offset = total - (score / 100) * total;
      // Defer so the CSS transition animates from the initial dashoffset
      requestAnimationFrame(function() {{
        requestAnimationFrame(function() {{
          arc.style.strokeDashoffset = offset;
        }});
      }});
    }})();
  </script>
</body>
</html>"""

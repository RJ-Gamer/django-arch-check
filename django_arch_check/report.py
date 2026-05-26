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
    weighted_score     = sum of all finding weights
    normalized_density = weighted_score / ln(file_count + 1)
    density_penalty    = min(65, round(normalized_density × 8))
    absolute_penalty   = min(15, round(weighted_score × 0.08))
    score              = max(0, 100 − density_penalty − absolute_penalty)

The logarithmic normalization ensures a fair comparison across codebase sizes:
a 5-file project with 1 critical should score lower than a 500-file project
with the same finding — because the finding represents a higher proportion
of the total surface area.

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
    "circular_imports":      {"critical": 10.0},
    "celery_tasks":          {"critical":  8.0, "warning": 3.0},
    "migration_safety":      {"warning":   6.0},
    "missing_service_layer": {"critical":  4.0, "warning": 2.0},
    "n_plus_one":            {"warning":   3.0},
    "direct_sql":            {"warning":   2.0},
    "god_apps":              {"critical":  3.0, "warning": 1.5},
    "fat_models":            {"critical":  2.0, "warning": 1.0},
}

# ---------------------------------------------------------------------------
# Section registry  (single source of truth for order + detector id → title)
# ---------------------------------------------------------------------------

_SECTIONS: list[tuple[str, str]] = [
    ("fat_models",            "Fat Models"),
    ("god_apps",              "God Apps"),
    ("circular_imports",      "Circular Imports"),
    ("missing_service_layer", "Missing Service Layer"),
    ("celery_tasks",          "Celery Tasks Without Retry"),
    ("direct_sql",            "Direct SQL"),
    ("n_plus_one",            "N+1 Query Risks"),
    ("migration_safety",      "Migration Safety"),
]

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

_DENSITY_FACTOR:        float = 8.0
_ABSOLUTE_FACTOR:       float = 0.08
_DENSITY_PENALTY_CAP:   int   = 65
_ABSOLUTE_PENALTY_CAP:  int   = 15
_DEFAULT_FILE_COUNT:    int   = 50   # fallback when project_path is not provided

_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", ".tox",
    ".venv", "venv", "env", ".env",
    "__pycache__", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "htmlcov", "dist", "build", ".eggs",
})

# ---------------------------------------------------------------------------
# File counting
# ---------------------------------------------------------------------------


def _count_python_files(project_path: str) -> int:
    """Count .py files in *project_path*, excluding tool and cache directories.

    Returns :data:`_DEFAULT_FILE_COUNT` when *project_path* is empty so that
    callers which do not have a path available (e.g. tests) still get a
    sensible denominator.
    """
    if not project_path:
        return _DEFAULT_FILE_COUNT
    count = 0
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        count += sum(1 for f in filenames if f.endswith(".py"))
    return max(1, count)


# ---------------------------------------------------------------------------
# Weighted score
# ---------------------------------------------------------------------------


def _compute_weighted_score(result: AnalysisResult) -> float:
    """Return the total weighted impact of all findings."""
    total = 0.0
    for detector_id, _ in _SECTIONS:
        findings = getattr(result, detector_id, [])
        weights = _DETECTOR_WEIGHTS.get(detector_id, {})
        for f in findings:
            sev = getattr(f, "severity", "warning")
            total += weights.get(sev, 1.0)
    return total


# ---------------------------------------------------------------------------
# Public score API
# ---------------------------------------------------------------------------


def compute_score(result: AnalysisResult, project_path: str = "") -> int:
    """Return a health score 0–100 using weighted, size-normalized formula."""
    weighted = _compute_weighted_score(result)
    if weighted == 0:
        return 100

    file_count = _count_python_files(project_path)
    normalized_density  = weighted / math.log(file_count + 1)
    density_penalty     = min(_DENSITY_PENALTY_CAP,  round(normalized_density * _DENSITY_FACTOR))
    absolute_penalty    = min(_ABSOLUTE_PENALTY_CAP, round(weighted * _ABSOLUTE_FACTOR))

    return max(0, 100 - density_penalty - absolute_penalty)


# ---------------------------------------------------------------------------
# Grade helpers  (public so cli.py can import them)
# ---------------------------------------------------------------------------


def score_grade(score: int) -> str:
    """Return letter grade A–F for *score*."""
    if score >= 90: return "A"
    if score >= 75: return "B"
    if score >= 60: return "C"
    if score >= 40: return "D"
    return "F"


def score_label(score: int) -> str:
    """Return a human-readable label for *score*."""
    if score >= 90: return "Excellent"
    if score >= 75: return "Good"
    if score >= 60: return "Needs Work"
    if score >= 40: return "Poor"
    return "Critical"


def _score_color(score: int) -> str:
    if score >= 75: return "var(--clean)"
    if score >= 60: return "var(--warning)"
    return "var(--critical)"


# ---------------------------------------------------------------------------
# Legacy _all_findings helper (used by older code paths, kept for compat)
# ---------------------------------------------------------------------------


def _all_findings(result: AnalysisResult) -> list[list[object]]:
    return [getattr(result, attr) for attr, _ in _SECTIONS]


# ---------------------------------------------------------------------------
# Finding → display row
# ---------------------------------------------------------------------------


def _finding_to_row(f: object) -> tuple[str, str]:
    """Return ``(severity, description)`` for any finding dataclass."""
    sev: str = getattr(f, "severity", "warning")

    if hasattr(f, "class_name") and hasattr(f, "method_count"):
        desc = (
            f"<code>{_e(getattr(f, 'file_path'))}</code> → "
            f"<strong>{_e(getattr(f, 'class_name'))}</strong> "
            f"({getattr(f, 'method_count')} methods)"
        )
    elif hasattr(f, "app_path") and hasattr(f, "percentage"):
        desc = (
            f"<code>{_e(getattr(f, 'app_path'))}</code> owns "
            f"{getattr(f, 'percentage')}% of total project code "
            f"({getattr(f, 'app_loc'):,} / {getattr(f, 'total_loc'):,} lines)"
        )
    elif hasattr(f, "cycle_display"):
        desc = (
            f"Circular import: "
            f"<strong>{_e(getattr(f, 'cycle_display'))}</strong>"
        )
    elif hasattr(f, "view_name") and hasattr(f, "orm_call_count"):
        detail = (
            f"contains {getattr(f, 'orm_call_count')} direct ORM calls"
            if sev == "critical"
            else "makes direct ORM calls"
        )
        desc = (
            f"<code>{_e(getattr(f, 'file_path'))}</code> → "
            f"<strong>{_e(getattr(f, 'view_name'))}()</strong> {detail}"
        )
    elif hasattr(f, "task_name"):
        detail = (
            "high-stakes task, no retry configured"
            if sev == "critical"
            else "no retry configured"
        )
        desc = (
            f"<code>{_e(getattr(f, 'file_path'))}</code> → "
            f"<strong>{_e(getattr(f, 'task_name'))}()</strong> — {detail}"
        )
    elif hasattr(f, "pattern") and hasattr(f, "line_number"):
        desc = (
            f"<code>{_e(getattr(f, 'file_path'))}:{getattr(f, 'line_number')}</code> "
            f"→ raw SQL detected: <code>{_e(getattr(f, 'pattern'))}</code>"
        )
    elif hasattr(f, "migration_name") and hasattr(f, "operation"):
        model  = getattr(f, "model_name", "")
        field  = getattr(f, "field_name", "")
        ctx    = (
            f"<code>{_e(model)}.{_e(field)}</code>"  if model and field else
            f"<code>{_e(model)}</code>"               if model           else ""
        )
        op = _e(getattr(f, "operation"))
        op_display = f"<strong>{op}</strong>({ctx})" if ctx else f"<strong>{op}</strong>"
        desc = (
            f"<code>{_e(getattr(f, 'file_path'))}</code> → {op_display}"
            f"<br><span style='color:var(--muted);font-size:12px'>"
            f"ℹ {_e(getattr(f, 'message'))}</span>"
        )
    elif hasattr(f, "line_number"):
        desc = (
            f"<code>{_e(getattr(f, 'file_path'))}:{getattr(f, 'line_number')}</code> "
            "→ ORM call inside loop — possible N+1 query risk"
        )
    else:
        desc = _e(str(f))

    return sev, desc


def _e(text: str) -> str:
    return html.escape(str(text))


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------


def _score_arc_offset(score: int) -> str:
    circumference = 2 * math.pi * 38
    offset = circumference * (1 - (score / 100))
    return f"{offset:.2f}"


def _badge(severity: str) -> str:
    return f'<span class="badge badge-{severity}">{severity.upper()}</span>'


def _count_severities(findings: list[object]) -> tuple[int, int]:
    critical = sum(1 for f in findings if getattr(f, "severity", "") == "critical")
    warning  = sum(1 for f in findings if getattr(f, "severity", "") == "warning")
    return critical, warning


def _section_slug(attr: str) -> str:
    return attr.replace("_", "-")


def _project_name(project_path: str) -> str:
    normalised = str(project_path).replace("\\", "/").rstrip("/")
    return normalised.split("/")[-1] if normalised else str(project_path)


def _severity_cell(severity: str) -> str:
    return (
        '<div class="sev-cell">'
        f'<div class="sev-dot {severity}"></div>'
        f"{_badge(severity)}"
        "</div>"
    )


def _render_section(
    attr: str,
    title: str,
    findings: list[object],
    skipped: bool = False,
) -> str:
    critical_count, warning_count = _count_severities(findings)
    summary_parts: list[str] = []
    if critical_count:
        summary_parts.append(
            f'<span class="badge badge-critical">{critical_count} critical</span>'
        )
    if warning_count:
        summary_parts.append(
            f'<span class="badge badge-warning">{warning_count} warning</span>'
        )

    header_counts = (
        f'<div class="section-counts">{"".join(summary_parts)}</div>'
        if summary_parts else ""
    )
    section_header = (
        '<div class="section-header">'
        f'<span class="section-name">{_e(title)}</span>'
        f"{header_counts}"
        "</div>"
    )

    if skipped:
        body = '<div class="section-skipped">⊘ Skipped (--ignore flag)</div>'
    elif not findings:
        body = '<div class="section-clean">No issues found</div>'
    else:
        rows = ""
        for f in findings:
            sev, desc = _finding_to_row(f)
            rows += (
                f'<tr class="finding-row" data-severity="{sev}">'
                f"<td>{_severity_cell(sev)}</td>"
                f"<td>{desc}</td>"
                "</tr>"
            )
        body = (
            '<table class="findings-table">'
            "<thead><tr><th>Severity</th><th>Finding</th></tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
        )

    return (
        f'<section class="section-card" data-section="{_e(_section_slug(attr))}">'
        f"{section_header}{body}</section>"
    )


# ---------------------------------------------------------------------------
# Score breakdown section
# ---------------------------------------------------------------------------


def _impact_label_class(detector_weight: float) -> tuple[str, str]:
    """Return (label, css_class) for a per-detector weighted score."""
    if detector_weight == 0:   return "None",   "impact-none"
    if detector_weight <= 3:   return "Low",    "impact-low"
    if detector_weight <= 8:   return "Medium", "impact-medium"
    return                            "High",   "impact-high"


def _render_breakdown(result: AnalysisResult) -> str:
    """Render the per-detector score breakdown table."""
    rows = ""
    for detector_id, title in _SECTIONS:
        if detector_id in result.skipped_detectors:
            continue
        findings    = getattr(result, detector_id, [])
        weights     = _DETECTOR_WEIGHTS.get(detector_id, {})
        det_weight  = sum(
            weights.get(getattr(f, "severity", "warning"), 1.0)
            for f in findings
        )
        count       = len(findings)
        label, cls  = _impact_label_class(det_weight)
        count_text  = (
            f"{count} finding{'s' if count != 1 else ''}"
            if count > 0 else "Clean"
        )
        weight_text = f"{det_weight:.1f}" if det_weight > 0 else "—"

        rows += (
            f"<tr>"
            f"<td>{_e(title)}</td>"
            f'<td class="bd-count">{count_text}</td>'
            f'<td class="bd-weight">{weight_text}</td>'
            f'<td><span class="impact-badge {cls}">{label}</span></td>'
            f"</tr>"
        )

    return (
        '<section class="section-card breakdown-card">'
        '<div class="section-header">'
        '<span class="section-name">Score Breakdown</span>'
        '<span class="section-breakdown-hint">weighted impact per detector</span>'
        "</div>"
        '<table class="breakdown-table">'
        "<thead><tr>"
        "<th>Detector</th><th>Findings</th>"
        "<th>Weight</th><th>Impact</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</section>"
    )


# ---------------------------------------------------------------------------
# Public HTML API
# ---------------------------------------------------------------------------


def generate_html(result: AnalysisResult, project_path: str) -> str:
    """Return a self-contained HTML report string."""
    sc          = compute_score(result, project_path)
    grade       = score_grade(sc)
    label       = score_label(sc)
    color       = _score_color(sc)
    arc_offset  = _score_arc_offset(sc)
    proj_name   = _project_name(project_path)
    generated   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_warnings = sum(
        sum(1 for f in getattr(result, attr) if getattr(f, "severity", "") == "warning")
        for attr, _ in _SECTIONS
    )
    total_criticals = sum(
        sum(1 for f in getattr(result, attr) if getattr(f, "severity", "") == "critical")
        for attr, _ in _SECTIONS
    )
    clean_sections = sum(
        1 for attr, _ in _SECTIONS
        if not getattr(result, attr) and attr not in result.skipped_detectors
    )

    sections_html   = "\n".join(
        _render_section(
            attr, title,
            getattr(result, attr),
            skipped=attr in result.skipped_detectors,
        )
        for attr, title in _SECTIONS
    )
    breakdown_html  = _render_breakdown(result)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>django-arch-check report</title>
  <style>
    :root {{
      --bg:             oklch(13% 0.014 240);
      --surface:        oklch(17% 0.013 240);
      --surface2:       oklch(21% 0.011 240);
      --fg:             oklch(93% 0.005 240);
      --muted:          oklch(54% 0.012 240);
      --border:         oklch(27% 0.011 240);
      --border2:        oklch(23% 0.010 240);

      --critical:       oklch(64% 0.22 25);
      --critical-dim:   oklch(50% 0.18 25);
      --critical-bg:    oklch(18% 0.07 25);
      --critical-bdr:   oklch(32% 0.13 25);

      --warning:        oklch(76% 0.14 55);
      --warning-dim:    oklch(62% 0.12 55);
      --warning-bg:     oklch(17% 0.05 55);
      --warning-bdr:    oklch(34% 0.10 55);

      --clean:          oklch(68% 0.18 160);
      --clean-bg:       oklch(17% 0.06 160);
      --clean-bdr:      oklch(30% 0.10 160);

      --impact-medium:  oklch(70% 0.15 40);

      --code-fg:        oklch(72% 0.10 200);
      --mono: 'JetBrains Mono', 'IBM Plex Mono', ui-monospace, Menlo, monospace;
      --sans: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', system-ui, sans-serif;
    }}

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      font-family: var(--sans);
      font-size: 14px;
      color: var(--fg);
      background:
        radial-gradient(circle at top left, oklch(19% 0.03 245 / 0.65), transparent 34%),
        radial-gradient(circle at top right, oklch(18% 0.04 25 / 0.30), transparent 28%),
        var(--bg);
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
      min-height: 100vh;
    }}
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: var(--bg); }}
    ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}

    /* ── Sticky bar ─────────────────────────────── */
    .sticky-bar {{
      position: sticky; top: 0; z-index: 100;
      background: oklch(13% 0.014 240 / 0.88);
      backdrop-filter: blur(14px) saturate(1.4);
      -webkit-backdrop-filter: blur(14px) saturate(1.4);
      border-bottom: 1px solid var(--border2);
      padding: 9px 28px;
      display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    }}
    .sticky-brand {{ font-family: var(--mono); font-size: 12px; color: var(--muted); letter-spacing: 0.02em; white-space: nowrap; }}
    .sticky-divider {{ width: 1px; height: 16px; background: var(--border); flex-shrink: 0; }}
    .sticky-score-group {{ display: flex; align-items: baseline; gap: 6px; }}
    .sticky-score-num {{
      font-size: 18px; font-weight: 800;
      color: {color};
      font-variant-numeric: tabular-nums; letter-spacing: -0.02em; line-height: 1;
    }}
    .sticky-score-denom {{ font-size: 12px; color: var(--muted); }}
    .sticky-grade {{
      font-size: 13px; font-weight: 800;
      color: {color};
      border: 1.5px solid {color};
      border-radius: 4px;
      padding: 0px 6px;
      line-height: 1.6;
    }}
    .sticky-pills {{ display: flex; gap: 6px; }}
    .sticky-pill {{
      font-size: 11px; font-weight: 700; padding: 2px 8px;
      border-radius: 4px; font-variant-numeric: tabular-nums; letter-spacing: 0.02em;
    }}
    .sticky-pill.critical {{ background: var(--critical-bg); color: var(--critical); border: 1px solid var(--critical-bdr); }}
    .sticky-pill.warning  {{ background: var(--warning-bg);  color: var(--warning);  border: 1px solid var(--warning-bdr);  }}
    .filter-group {{
      display: flex; gap: 2px; margin-left: auto;
      background: var(--surface2); border-radius: 8px; padding: 3px;
      border: 1px solid var(--border2);
    }}
    .filter-btn {{
      font-family: var(--sans); font-size: 11px; font-weight: 600;
      padding: 4px 14px; border: none; border-radius: 6px; cursor: pointer;
      background: transparent; color: var(--muted);
      letter-spacing: 0.05em; text-transform: uppercase;
      transition: color 0.12s, background 0.12s;
    }}
    .filter-btn:hover {{ color: var(--fg); }}
    .filter-btn.active {{ background: var(--surface); color: var(--fg); }}
    .filter-btn.active.f-critical {{ color: var(--critical); }}
    .filter-btn.active.f-warning  {{ color: var(--warning);  }}

    /* ── Layout ─────────────────────────────────── */
    .container {{ max-width: 1040px; margin: 0 auto; padding: 44px 28px 88px; }}

    /* ── Hero ───────────────────────────────────── */
    .hero {{
      display: grid; grid-template-columns: 1fr auto;
      align-items: center; gap: 32px;
      margin-bottom: 36px; padding-bottom: 36px;
      border-bottom: 1px solid var(--border2);
    }}
    .hero-eyebrow {{ font-family: var(--mono); font-size: 11px; color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 8px; }}
    .hero-title {{ font-size: 26px; font-weight: 700; letter-spacing: -0.025em; line-height: 1.2; }}
    .hero-title .dim {{ color: var(--muted); font-weight: 400; }}
    .hero-meta {{ display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 14px; }}
    .meta-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }}
    .meta-item code {{
      font-family: var(--mono); font-size: 11px; color: var(--code-fg);
      background: var(--surface2); border: 1px solid var(--border);
      padding: 1px 6px; border-radius: 4px;
    }}

    /* ── Score ring ─────────────────────────────── */
    .score-ring-wrap {{ display: flex; flex-direction: column; align-items: center; gap: 8px; flex-shrink: 0; }}
    .score-arc {{
      animation: ring-fill 1.1s cubic-bezier(0.4,0,0.2,1) 0.2s both;
      stroke: {color};
    }}
    @keyframes ring-fill {{
      from {{ stroke-dashoffset: 238.76; }}
      to   {{ stroke-dashoffset: {arc_offset}; }}
    }}
    .score-ring-grade {{
      font-size: 12px; font-weight: 800;
      color: {color};
      text-transform: uppercase; letter-spacing: 0.06em;
    }}
    .score-ring-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }}

    /* ── Stats row ──────────────────────────────── */
    .stats-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 32px; }}
    .stat-card {{ background: var(--surface); border: 1px solid var(--border2); border-radius: 10px; padding: 18px 22px; }}
    .stat-card.critical {{ border-color: var(--critical-bdr); }}
    .stat-card.warning  {{ border-color: var(--warning-bdr);  }}
    .stat-card.clean    {{ border-color: var(--clean-bdr);    }}
    .stat-card.grade    {{ border-color: {color};             }}
    .stat-num {{ font-size: 36px; font-weight: 800; letter-spacing: -0.04em; line-height: 1; font-variant-numeric: tabular-nums; }}
    .stat-card.critical .stat-num {{ color: var(--critical); }}
    .stat-card.warning  .stat-num {{ color: var(--warning);  }}
    .stat-card.clean    .stat-num {{ color: var(--clean);    }}
    .stat-card.grade    .stat-num {{ color: {color};         font-size: 32px; }}
    .stat-label {{ margin-top: 6px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); font-weight: 600; }}

    /* ── Section cards ──────────────────────────── */
    .section-card {{ background: var(--surface); border: 1px solid var(--border2); border-radius: 10px; margin-bottom: 10px; overflow: hidden; }}
    .section-header {{ display: flex; align-items: center; gap: 10px; padding: 13px 20px; background: var(--surface2); border-bottom: 1px solid var(--border2); }}
    .section-name {{ font-size: 13px; font-weight: 600; letter-spacing: -0.01em; }}
    .section-counts {{ display: flex; gap: 6px; margin-left: auto; flex-wrap: wrap; justify-content: flex-end; }}
    .section-breakdown-hint {{ font-size: 11px; color: var(--muted); margin-left: auto; }}

    .badge {{
      display: inline-flex; align-items: center;
      padding: 2px 7px; border-radius: 4px;
      font-size: 11px; font-weight: 700; letter-spacing: 0.03em;
      text-transform: uppercase; font-variant-numeric: tabular-nums;
    }}
    .badge-critical {{ background: var(--critical-bg); color: var(--critical); border: 1px solid var(--critical-bdr); }}
    .badge-warning  {{ background: var(--warning-bg);  color: var(--warning);  border: 1px solid var(--warning-bdr);  }}

    .section-clean, .section-skipped {{
      padding: 14px 20px; font-size: 13px; font-weight: 500;
      display: flex; align-items: center; gap: 10px;
    }}
    .section-clean   {{ color: var(--clean); }}
    .section-skipped {{ color: var(--muted); }}
    .section-clean::before, .section-skipped::before {{
      content: ''; display: block; width: 6px; height: 6px;
      border-radius: 50%; flex-shrink: 0;
    }}
    .section-clean::before   {{ background: var(--clean); }}
    .section-skipped::before {{ background: var(--muted); }}

    /* ── Findings table ─────────────────────────── */
    .findings-table {{ width: 100%; border-collapse: collapse; }}
    .findings-table thead th {{
      text-align: left; padding: 7px 20px;
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em;
      color: var(--muted); background: var(--surface2);
      border-bottom: 1px solid var(--border2); font-weight: 600;
    }}
    .findings-table thead th:first-child {{ width: 130px; }}
    .finding-row td {{ padding: 10px 20px; border-bottom: 1px solid var(--border2); font-size: 13px; vertical-align: middle; line-height: 1.5; }}
    .finding-row:last-child td {{ border-bottom: none; }}
    .finding-row {{ transition: background 0.1s; }}
    .finding-row:hover {{ background: var(--surface2); }}
    .finding-row td code {{ font-family: var(--mono); font-size: 11.5px; color: var(--code-fg); background: oklch(20% 0.015 200 / 0.6); border: 1px solid oklch(30% 0.012 200 / 0.5); padding: 1px 5px; border-radius: 3px; }}
    .finding-row td strong {{ color: var(--fg); font-weight: 600; }}

    .sev-cell {{ display: flex; align-items: center; gap: 7px; }}
    .sev-dot  {{ width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }}
    .sev-dot.critical {{ background: var(--critical); box-shadow: 0 0 5px var(--critical-dim); }}
    .sev-dot.warning  {{ background: var(--warning);  }}

    /* ── Breakdown table ────────────────────────── */
    .breakdown-table {{ width: 100%; border-collapse: collapse; }}
    .breakdown-table thead th {{
      text-align: left; padding: 7px 20px;
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em;
      color: var(--muted); background: var(--surface2);
      border-bottom: 1px solid var(--border2); font-weight: 600;
    }}
    .breakdown-table td {{
      padding: 9px 20px; border-bottom: 1px solid var(--border2);
      font-size: 13px; vertical-align: middle;
    }}
    .breakdown-table tr:last-child td {{ border-bottom: none; }}
    .breakdown-table tr:hover {{ background: var(--surface2); }}
    .bd-count  {{ color: var(--muted); font-size: 12px; }}
    .bd-weight {{ font-family: var(--mono); font-size: 12px; color: var(--code-fg); }}
    .impact-badge {{
      display: inline-flex; align-items: center; padding: 2px 8px;
      border-radius: 4px; font-size: 11px; font-weight: 700;
      letter-spacing: 0.04em; text-transform: uppercase;
    }}
    .impact-none   {{ background: var(--clean-bg);    color: var(--clean);          border: 1px solid var(--clean-bdr);    }}
    .impact-low    {{ background: var(--warning-bg);  color: var(--warning);        border: 1px solid var(--warning-bdr);  }}
    .impact-medium {{ background: oklch(17% 0.05 40); color: var(--impact-medium);  border: 1px solid oklch(34% 0.10 40);  }}
    .impact-high   {{ background: var(--critical-bg); color: var(--critical);       border: 1px solid var(--critical-bdr); }}

    /* ── Filter / visibility ────────────────────── */
    .finding-row.hidden       {{ display: none; }}
    .section-card.section-hidden {{ display: none; }}

    /* ── Footer ─────────────────────────────────── */
    footer {{
      margin-top: 52px; padding-top: 20px;
      border-top: 1px solid var(--border2);
      font-size: 12px; color: var(--muted);
      display: flex; justify-content: space-between; align-items: center;
      flex-wrap: wrap; gap: 8px; font-family: var(--mono);
    }}

    /* ── Responsive ─────────────────────────────── */
    @media (max-width: 640px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .stats-row {{ grid-template-columns: 1fr 1fr; }}
      .filter-group {{ margin-left: 0; }}
      .container {{ padding: 28px 16px 64px; }}
      .sticky-bar {{ padding: 9px 16px; }}
    }}
  </style>
</head>
<body>
  <div class="sticky-bar">
    <span class="sticky-brand">django-arch-check</span>
    <div class="sticky-divider"></div>
    <div class="sticky-score-group">
      <span class="sticky-score-num">{sc}</span>
      <span class="sticky-score-denom">/ 100</span>
      <span class="sticky-grade">{_e(grade)}</span>
    </div>
    <div class="sticky-pills">
      <span class="sticky-pill critical">{total_criticals} critical</span>
      <span class="sticky-pill warning">{total_warnings} warning</span>
    </div>
    <div class="filter-group">
      <button class="filter-btn active" data-filter="all"      onclick="setFilter('all',this)">All</button>
      <button class="filter-btn f-critical" data-filter="critical" onclick="setFilter('critical',this)">Critical</button>
      <button class="filter-btn f-warning"  data-filter="warning"  onclick="setFilter('warning',this)">Warning</button>
    </div>
  </div>

  <div class="container">
    <header class="hero">
      <div>
        <div class="hero-eyebrow">Architecture Report</div>
        <h1 class="hero-title">django-arch-check <span class="dim">/ {_e(proj_name)}</span></h1>
        <div class="hero-meta">
          <div class="meta-item">Project <code>{_e(project_path)}</code></div>
          <div class="meta-item">Generated {generated}</div>
          <div class="meta-item">v{_e(__version__)}</div>
        </div>
      </div>
      <div class="score-ring-wrap">
        <svg width="96" height="96" viewBox="0 0 96 96" aria-label="Health score {sc} out of 100">
          <circle cx="48" cy="48" r="38" fill="none" stroke="oklch(23% 0.011 240)" stroke-width="7"/>
          <circle cx="48" cy="48" r="38" fill="none" stroke-width="7"
            stroke-dasharray="238.76" stroke-dashoffset="{arc_offset}"
            stroke-linecap="round" transform="rotate(-90 48 48)" class="score-arc"/>
          <text x="48" y="44" text-anchor="middle" dominant-baseline="central"
            font-size="20" font-weight="800" fill="oklch(93% 0.005 240)"
            font-family="-apple-system,BlinkMacSystemFont,system-ui,sans-serif">{sc}</text>
          <text x="48" y="64" text-anchor="middle" dominant-baseline="central"
            font-size="13" font-weight="700"
            fill="{color}"
            font-family="-apple-system,BlinkMacSystemFont,system-ui,sans-serif">{_e(grade)}</text>
        </svg>
        <span class="score-ring-grade">{_e(grade)} · {_e(label)}</span>
        <span class="score-ring-label">Health Score</span>
      </div>
    </header>

    <div class="stats-row">
      <div class="stat-card grade">
        <div class="stat-num">{_e(grade)}</div>
        <div class="stat-label">{_e(label)}</div>
      </div>
      <div class="stat-card critical">
        <div class="stat-num">{total_criticals}</div>
        <div class="stat-label">Critical</div>
      </div>
      <div class="stat-card warning">
        <div class="stat-num">{total_warnings}</div>
        <div class="stat-label">Warnings</div>
      </div>
      <div class="stat-card clean">
        <div class="stat-num">{clean_sections}</div>
        <div class="stat-label">Sections Clean</div>
      </div>
    </div>

    {breakdown_html}

    {sections_html}

    <footer>
      <span>Score = 100 − density_penalty − absolute_penalty · weighted by detector risk</span>
      <span>django-arch-check v{_e(__version__)}</span>
    </footer>
  </div>

  <script>
    function setFilter(type, btn) {{
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('[data-section]').forEach(section => {{
        const rows = section.querySelectorAll('.finding-row');
        if (!rows.length) return;
        let visible = 0;
        rows.forEach(row => {{
          const match = type === 'all' || row.dataset.severity === type;
          row.classList.toggle('hidden', !match);
          if (match) visible++;
        }});
        section.classList.toggle('section-hidden', visible === 0);
      }});
    }}
  </script>
</body>
</html>"""
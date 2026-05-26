"""HTML report generator for django-arch-check.

This module is a pure function: it takes an :class:`AnalysisResult` and
returns a self-contained HTML string. All file I/O is the caller's
responsibility.

Score calculation
-----------------
Rate-based formula — scores finding *density*, not raw count:

    critical_rate = criticals / total_findings
    warning_rate  = warnings  / total_findings
    raw           = 100 - (critical_rate * 60) - (warning_rate * 40)
    penalty       = min(30, criticals * 2 + warnings * 0.5)
    score         = max(0, round(raw - penalty))

A project with only criticals scores lower than one with only warnings
at the same volume. Large codebases with known technical debt are not
automatically clamped to 0.
"""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone

from django_arch_check import __version__
from django_arch_check.analyzer import AnalysisResult

# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------

# Legacy per-finding penalty constants kept for the footer display only.
_WARNING_PENALTY = 5
_CRITICAL_PENALTY = 15


def compute_score(result: AnalysisResult) -> int:
    """Return a health score 0–100 using a rate-based formula."""
    criticals = 0
    warnings = 0
    for findings in _all_findings(result):
        for f in findings:
            sev = getattr(f, "severity", None)
            if sev == "critical":
                criticals += 1
            elif sev == "warning":
                warnings += 1

    total_findings = criticals + warnings
    if total_findings == 0:
        return 100

    critical_rate = criticals / total_findings
    warning_rate = warnings / total_findings
    raw = 100 - (critical_rate * 60) - (warning_rate * 40)
    absolute_penalty = min(30, (criticals * 2) + (warnings * 0.5))
    return max(0, round(raw - absolute_penalty))


def _all_findings(result: AnalysisResult) -> list[list[object]]:
    return [
        result.fat_models,
        result.god_apps,
        result.circular_imports,
        result.missing_service_layer,
        result.celery_tasks,
        result.direct_sql,
        result.n_plus_one,
        result.migration_safety,
    ]


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
        desc = f"Circular import: <strong>{_e(getattr(f, 'cycle_display'))}</strong>"
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
    elif hasattr(f, "line_number"):
        desc = (
            f"<code>{_e(getattr(f, 'file_path'))}:{getattr(f, 'line_number')}</code> "
            "→ ORM call inside loop — possible N+1 query risk"
        )
    elif hasattr(f, "migration_name") and hasattr(f, "operation"):
        model = getattr(f, "model_name", "")
        field = getattr(f, "field_name", "")
        context_parts = []
        if model:
            context_parts.append(f"model=<code>{_e(model)}</code>")
        if field:
            context_parts.append(f"field=<code>{_e(field)}</code>")
        context = ", ".join(context_parts)
        op_display = (
            f"<strong>{_e(getattr(f, 'operation'))}</strong>({context})"
            if context
            else f"<strong>{_e(getattr(f, 'operation'))}</strong>"
        )
        desc = (
            f"<code>{_e(getattr(f, 'file_path'))}</code> → {op_display}"
            f"<br><span style='color:var(--muted);font-size:12px'>"
            f"ℹ {_e(getattr(f, 'message'))}</span>"
        )
    else:
        desc = _e(str(f))

    return sev, desc


def _e(text: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(text))


# ---------------------------------------------------------------------------
# Section data
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

]


# ---------------------------------------------------------------------------
# HTML fragments
# ---------------------------------------------------------------------------


def _score_color(score: int) -> str:
    if score >= 80:
        return "var(--clean)"
    if score >= 50:
        return "var(--warning)"
    return "var(--critical)"


def _score_label(score: int) -> str:
    if score >= 80:
        return "Healthy"
    if score >= 50:
        return "Needs Attention"
    return "Critical"


def _badge(severity: str) -> str:
    return f'<span class="badge badge-{severity}">{severity.upper()}</span>'


def _count_severities(findings: list[object]) -> tuple[int, int]:
    critical = sum(1 for f in findings if getattr(f, "severity", "") == "critical")
    warning = sum(1 for f in findings if getattr(f, "severity", "") == "warning")
    return critical, warning


def _section_slug(attr: str) -> str:
    return attr.replace("_", "-")


def _project_name(project_path: str) -> str:
    normalised = str(project_path).replace("\\", "/").rstrip("/")
    return normalised.split("/")[-1] if normalised else str(project_path)


def _score_arc_offset(score: int) -> str:
    circumference = 2 * math.pi * 38
    offset = circumference * (1 - (score / 100))
    return f"{offset:.2f}"


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
        if summary_parts
        else ""
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
        f"{section_header}"
        f"{body}"
        "</section>"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_html(result: AnalysisResult, project_path: str) -> str:
    """Return a self-contained HTML report string."""
    score = compute_score(result)
    score_color = _score_color(score)
    score_label = _score_label(score)
    score_arc_offset = _score_arc_offset(score)
    project_name = _project_name(project_path)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_warnings = sum(
        sum(1 for f in getattr(result, attr) if getattr(f, "severity", "") == "warning")
        for attr, _ in _SECTIONS
    )
    total_criticals = sum(
        sum(
            1 for f in getattr(result, attr) if getattr(f, "severity", "") == "critical"
        )
        for attr, _ in _SECTIONS
    )
    clean_sections = sum(
        1
        for attr, _ in _SECTIONS
        if not getattr(result, attr) and attr not in result.skipped_detectors
    )

    sections_html = "\n".join(
        _render_section(
            attr,
            title,
            getattr(result, attr),
            skipped=attr in result.skipped_detectors,
        )
        for attr, title in _SECTIONS
    )

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
      -moz-osx-font-smoothing: grayscale;
      min-height: 100vh;
    }}
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: var(--bg); }}
    ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}

    .sticky-bar {{
      position: sticky;
      top: 0;
      z-index: 100;
      background: oklch(13% 0.014 240 / 0.88);
      backdrop-filter: blur(14px) saturate(1.4);
      -webkit-backdrop-filter: blur(14px) saturate(1.4);
      border-bottom: 1px solid var(--border2);
      padding: 9px 28px;
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
    }}
    .sticky-brand {{
      font-family: var(--mono);
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.02em;
      white-space: nowrap;
    }}
    .sticky-divider {{ width: 1px; height: 16px; background: var(--border); flex-shrink: 0; }}
    .sticky-score-group {{ display: flex; align-items: baseline; gap: 5px; }}
    .sticky-score-num {{
      font-size: 18px;
      font-weight: 800;
      color: {score_color};
      font-variant-numeric: tabular-nums;
      letter-spacing: -0.02em;
      line-height: 1;
    }}
    .sticky-score-denom {{ font-size: 12px; color: var(--muted); }}
    .sticky-pills {{ display: flex; gap: 6px; }}
    .sticky-pill {{
      font-size: 11px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 4px;
      font-variant-numeric: tabular-nums;
      letter-spacing: 0.02em;
    }}
    .sticky-pill.critical {{
      background: var(--critical-bg);
      color: var(--critical);
      border: 1px solid var(--critical-bdr);
    }}
    .sticky-pill.warning {{
      background: var(--warning-bg);
      color: var(--warning);
      border: 1px solid var(--warning-bdr);
    }}
    .filter-group {{
      display: flex;
      gap: 2px;
      margin-left: auto;
      background: var(--surface2);
      border-radius: 8px;
      padding: 3px;
      border: 1px solid var(--border2);
    }}
    .filter-btn {{
      font-family: var(--sans);
      font-size: 11px;
      font-weight: 600;
      padding: 4px 14px;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      background: transparent;
      color: var(--muted);
      letter-spacing: 0.05em;
      text-transform: uppercase;
      transition: color 0.12s, background 0.12s;
    }}
    .filter-btn:hover {{ color: var(--fg); }}
    .filter-btn.active {{ background: var(--surface); color: var(--fg); }}
    .filter-btn.active.f-critical {{ color: var(--critical); }}
    .filter-btn.active.f-warning {{ color: var(--warning); }}

    .container {{ max-width: 1040px; margin: 0 auto; padding: 44px 28px 88px; }}

    .hero {{
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: center;
      gap: 32px;
      margin-bottom: 36px;
      padding-bottom: 36px;
      border-bottom: 1px solid var(--border2);
    }}
    .hero-eyebrow {{
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}
    .hero-title {{
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.025em;
      line-height: 1.2;
      color: var(--fg);
    }}
    .hero-title .dim {{ color: var(--muted); font-weight: 400; }}
    .hero-meta {{ display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 14px; }}
    .meta-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }}
    .meta-item code {{
      font-family: var(--mono);
      font-size: 11px;
      color: var(--code-fg);
      background: var(--surface2);
      border: 1px solid var(--border);
      padding: 1px 6px;
      border-radius: 4px;
    }}

    .score-ring-wrap {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }}
    .score-arc {{
      animation: ring-fill 1.1s cubic-bezier(0.4,0,0.2,1) 0.2s both;
      stroke: {score_color};
    }}
    @keyframes ring-fill {{
      from {{ stroke-dashoffset: 238.76; }}
      to {{ stroke-dashoffset: {score_arc_offset}; }}
    }}
    .score-ring-label {{
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.1em;
      font-weight: 600;
    }}
    .score-ring-status {{
      font-size: 11px;
      font-weight: 700;
      color: {score_color};
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .stats-row {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 32px; }}
    .stat-card {{
      background: var(--surface);
      border: 1px solid var(--border2);
      border-radius: 10px;
      padding: 18px 22px;
    }}
    .stat-card.critical {{ border-color: var(--critical-bdr); }}
    .stat-card.warning {{ border-color: var(--warning-bdr); }}
    .stat-card.clean {{ border-color: var(--clean-bdr); }}
    .stat-num {{
      font-size: 36px;
      font-weight: 800;
      letter-spacing: -0.04em;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}
    .stat-card.critical .stat-num {{ color: var(--critical); }}
    .stat-card.warning .stat-num {{ color: var(--warning); }}
    .stat-card.clean .stat-num {{ color: var(--clean); }}
    .stat-label {{
      margin-top: 6px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--muted);
      font-weight: 600;
    }}

    .section-card {{
      background: var(--surface);
      border: 1px solid var(--border2);
      border-radius: 10px;
      margin-bottom: 10px;
      overflow: hidden;
      box-shadow: inset 0 1px 0 oklch(100% 0 0 / 0.02);
    }}
    .section-header {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 13px 20px;
      background: var(--surface2);
      border-bottom: 1px solid var(--border2);
    }}
    .section-name {{
      font-size: 13px;
      font-weight: 600;
      color: var(--fg);
      letter-spacing: -0.01em;
    }}
    .section-counts {{ display: flex; gap: 6px; margin-left: auto; flex-wrap: wrap; justify-content: flex-end; }}

    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 2px 7px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      font-variant-numeric: tabular-nums;
    }}
    .badge-critical {{
      background: var(--critical-bg);
      color: var(--critical);
      border: 1px solid var(--critical-bdr);
    }}
    .badge-warning {{
      background: var(--warning-bg);
      color: var(--warning);
      border: 1px solid var(--warning-bdr);
    }}

    .section-clean,
    .section-skipped {{
      padding: 14px 20px;
      font-size: 13px;
      font-weight: 500;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .section-clean {{ color: var(--clean); }}
    .section-skipped {{ color: var(--muted); }}
    .section-clean::before,
    .section-skipped::before {{
      content: '';
      display: block;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .section-clean::before {{ background: var(--clean); }}
    .section-skipped::before {{ background: var(--muted); }}

    .findings-table {{ width: 100%; border-collapse: collapse; }}
    .findings-table thead th {{
      text-align: left;
      padding: 7px 20px;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      background: var(--surface2);
      border-bottom: 1px solid var(--border2);
      font-weight: 600;
    }}
    .findings-table thead th:first-child {{ width: 130px; }}
    .finding-row td {{
      padding: 10px 20px;
      border-bottom: 1px solid var(--border2);
      font-size: 13px;
      vertical-align: middle;
      line-height: 1.5;
    }}
    .finding-row:last-child td {{ border-bottom: none; }}
    .finding-row {{ transition: background 0.1s; }}
    .finding-row:hover {{ background: var(--surface2); }}
    .finding-row td code {{
      font-family: var(--mono);
      font-size: 11.5px;
      color: var(--code-fg);
      background: oklch(20% 0.015 200 / 0.6);
      border: 1px solid oklch(30% 0.012 200 / 0.5);
      padding: 1px 5px;
      border-radius: 3px;
    }}
    .finding-row td strong {{ color: var(--fg); font-weight: 600; }}

    .sev-cell {{ display: flex; align-items: center; gap: 7px; }}
    .sev-dot {{ width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }}
    .sev-dot.critical {{ background: var(--critical); box-shadow: 0 0 5px var(--critical-dim); }}
    .sev-dot.warning {{ background: var(--warning); }}

    .finding-row.hidden {{ display: none; }}
    .section-card.section-hidden {{ display: none; }}

    footer {{
      margin-top: 52px;
      padding-top: 20px;
      border-top: 1px solid var(--border2);
      font-size: 12px;
      color: var(--muted);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      font-family: var(--mono);
    }}

    @media (max-width: 640px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .stats-row {{ grid-template-columns: 1fr 1fr; }}
      .stats-row .stat-card:last-child {{ grid-column: span 2; }}
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
      <span class="sticky-score-num">{score}</span>
      <span class="sticky-score-denom">/ 100</span>
    </div>
    <div class="sticky-pills">
      <span class="sticky-pill critical">{total_criticals} critical</span>
      <span class="sticky-pill warning">{total_warnings} warning</span>
    </div>
    <div class="filter-group">
      <button class="filter-btn active" data-filter="all" onclick="setFilter('all', this)">All</button>
      <button class="filter-btn f-critical" data-filter="critical" onclick="setFilter('critical', this)">Critical</button>
      <button class="filter-btn f-warning" data-filter="warning" onclick="setFilter('warning', this)">Warning</button>
    </div>
  </div>

  <div class="container">
    <header class="hero">
      <div>
        <div class="hero-eyebrow">Architecture Report</div>
        <h1 class="hero-title">django-arch-check <span class="dim">/ {_e(project_name)}</span></h1>
        <div class="hero-meta">
          <div class="meta-item">Project <code>{_e(project_path)}</code></div>
          <div class="meta-item">Generated {generated_at}</div>
          <div class="meta-item">v{_e(__version__)}</div>
        </div>
      </div>
      <div class="score-ring-wrap">
        <svg width="96" height="96" viewBox="0 0 96 96" aria-label="Health score {score} out of 100">
          <circle cx="48" cy="48" r="38" fill="none" stroke="oklch(23% 0.011 240)" stroke-width="7"/>
          <circle cx="48" cy="48" r="38" fill="none" stroke-width="7"
            stroke-dasharray="238.76" stroke-dashoffset="{score_arc_offset}"
            stroke-linecap="round" transform="rotate(-90 48 48)" class="score-arc"/>
          <text x="48" y="48" text-anchor="middle" dominant-baseline="central"
            font-size="22" font-weight="800" fill="oklch(93% 0.005 240)"
            font-family="-apple-system,BlinkMacSystemFont,system-ui,sans-serif">{score}</text>
        </svg>
        <span class="score-ring-label">Health Score</span>
        <span class="score-ring-status">{_e(score_label)}</span>
      </div>
    </header>

    <div class="stats-row">
      <div class="stat-card critical"><div class="stat-num">{total_criticals}</div><div class="stat-label">Critical</div></div>
      <div class="stat-card warning"><div class="stat-num">{total_warnings}</div><div class="stat-label">Warnings</div></div>
      <div class="stat-card clean"><div class="stat-num">{clean_sections}</div><div class="stat-label">Sections Clean</div></div>
    </div>

    {sections_html}

    <footer>
      <span>Health score based on finding severity and density</span>
      <span>django-arch-check v{_e(__version__)}</span>
    </footer>
  </div>

  <script>
    function setFilter(type, btn) {{
      document.querySelectorAll('.filter-btn').forEach(function(b) {{
        b.classList.remove('active');
      }});
      btn.classList.add('active');
      document.querySelectorAll('[data-section]').forEach(function(section) {{
        var rows = section.querySelectorAll('.finding-row');
        if (rows.length === 0) return;
        var visible = 0;
        rows.forEach(function(row) {{
          var match = type === 'all' || row.dataset.severity === type;
          row.classList.toggle('hidden', !match);
          if (match) visible++;
        }});
        section.classList.toggle('section-hidden', visible === 0);
      }});
    }}
  </script>
</body>
</html>"""

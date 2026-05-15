"""HTML report generator for django-arch-check.

This module is a pure function: it takes an :class:`AnalysisResult` and
returns a self-contained HTML string.  All file I/O is the caller's
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
at the same volume.  Large codebases with known technical debt are not
automatically clamped to 0.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from django_arch_check import __version__
from django_arch_check.analyzer import AnalysisResult

# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------

# Legacy per-finding penalty constants kept for the footer display only.
_WARNING_PENALTY  = 5
_CRITICAL_PENALTY = 15


def compute_score(result: AnalysisResult) -> int:
    """Return a health score 0–100 using a rate-based formula.

    The old per-finding deduction formula (100 - criticals*15 - warnings*5)
    does not scale: large mature codebases with many known findings always
    scored 0, making the metric meaningless.

    This formula scores based on finding *density* rather than raw count:

    1. ``critical_rate`` and ``warning_rate`` are each finding type's share
       of the total finding count.  A project with mostly criticals scores
       lower than one with mostly warnings, even at the same total.

    2. An ``absolute_penalty`` (capped at 30) adds a modest deduction
       proportional to raw counts so a project with 200 findings still
       scores lower than one with 5 — but not catastrophically lower.

    3. Score is rounded and clamped to [0, 100].
    """
    criticals = 0
    warnings  = 0
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
    warning_rate  = warnings  / total_findings

    raw = 100 - (critical_rate * 60) - (warning_rate * 40)

    # Small absolute penalty so raw finding counts still matter, capped at 30
    # so even very large projects can score above zero.
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
    ]


# ---------------------------------------------------------------------------
# Finding → display row
# ---------------------------------------------------------------------------


def _finding_to_row(f: object) -> tuple[str, str]:
    """Return ``(severity, description)`` for any finding dataclass."""
    sev: str = getattr(f, "severity", "warning")

    # Fat model
    if hasattr(f, "class_name") and hasattr(f, "method_count"):
        desc = (
            f"{_e(getattr(f, 'file_path'))} → "
            f"<strong>{_e(getattr(f, 'class_name'))}</strong> "
            f"({getattr(f, 'method_count')} methods)"
        )
    # God app
    elif hasattr(f, "app_path") and hasattr(f, "percentage"):
        desc = (
            f"<strong>{_e(getattr(f, 'app_path'))}</strong> owns "
            f"{getattr(f, 'percentage')}% of total project code "
            f"({getattr(f, 'app_loc'):,} / {getattr(f, 'total_loc'):,} lines)"
        )
    # Circular import
    elif hasattr(f, "cycle_display"):
        desc = f"Circular import: <strong>{_e(getattr(f, 'cycle_display'))}</strong>"
    # Missing service layer
    elif hasattr(f, "view_name") and hasattr(f, "line_count"):
        detail = (
            f"contains {getattr(f, 'line_count')} lines of business logic"
            if sev == "critical"
            else "makes direct ORM calls"
        )
        desc = (
            f"{_e(getattr(f, 'file_path'))} → "
            f"<strong>{_e(getattr(f, 'view_name'))}()</strong> {detail}"
        )
    # Celery task
    elif hasattr(f, "task_name"):
        detail = (
            "high-stakes task, no retry configured"
            if sev == "critical"
            else "no retry configured"
        )
        desc = (
            f"{_e(getattr(f, 'file_path'))} → "
            f"<strong>{_e(getattr(f, 'task_name'))}()</strong> — {detail}"
        )
    # Direct SQL
    elif hasattr(f, "pattern") and hasattr(f, "line_number"):
        desc = (
            f"{_e(getattr(f, 'file_path'))}:{getattr(f, 'line_number')} → "
            f"raw SQL detected: <code>{_e(getattr(f, 'pattern'))}</code>"
        )
    # N+1
    elif hasattr(f, "line_number"):
        desc = (
            f"{_e(getattr(f, 'file_path'))}:{getattr(f, 'line_number')} → "
            "ORM call inside loop — possible N+1 query risk"
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
    ("fat_models",           "Fat Models"),
    ("god_apps",             "God Apps"),
    ("circular_imports",     "Circular Imports"),
    ("missing_service_layer","Missing Service Layer"),
    ("celery_tasks",         "Celery Tasks Without Retry"),
    ("direct_sql",           "Direct SQL"),
    ("n_plus_one",           "N+1 Query Risks"),
]


# ---------------------------------------------------------------------------
# HTML fragments
# ---------------------------------------------------------------------------


def _score_color(score: int) -> str:
    if score >= 80:
        return "var(--green)"
    if score >= 50:
        return "var(--yellow)"
    return "var(--red)"


def _score_label(score: int) -> str:
    if score >= 80:
        return "Healthy"
    if score >= 50:
        return "Needs Attention"
    return "Critical"


def _badge(severity: str) -> str:
    return f'<span class="badge badge-{severity}">{severity.upper()}</span>'


def _render_section(title: str, findings: list[object]) -> str:
    if not findings:
        return f"""
    <section>
      <h2>{_e(title)}</h2>
      <p class="clean">✓ No issues found.</p>
    </section>"""

    rows = ""
    for f in findings:
        sev, desc = _finding_to_row(f)
        rows += f"""
        <tr class="row-{sev}">
          <td>{_badge(sev)}</td>
          <td>{desc}</td>
        </tr>"""

    count = len(findings)
    crit  = sum(1 for f in findings if getattr(f, "severity", "") == "critical")
    warn  = count - crit
    summary_parts = []
    if crit:
        summary_parts.append(f'<span class="badge badge-critical">{crit} critical</span>')
    if warn:
        summary_parts.append(f'<span class="badge badge-warning">{warn} warning</span>')

    return f"""
    <section>
      <h2>{_e(title)} <span class="section-count">{"&nbsp;".join(summary_parts)}</span></h2>
      <table>
        <thead><tr><th>Severity</th><th>Finding</th></tr></thead>
        <tbody>{rows}
        </tbody>
      </table>
    </section>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_html(result: AnalysisResult, project_path: str) -> str:
    """Return a self-contained HTML report string.

    Args:
        result:       Aggregated analysis results from all detectors.
        project_path: The analysed project path (used only for display).

    Returns:
        A complete HTML document as a string.
    """
    score        = compute_score(result)
    score_color  = _score_color(score)
    score_label  = _score_label(score)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_warnings  = sum(
        sum(1 for f in getattr(result, attr) if getattr(f, "severity", "") == "warning")
        for attr, _ in _SECTIONS
    )
    total_criticals = sum(
        sum(1 for f in getattr(result, attr) if getattr(f, "severity", "") == "critical")
        for attr, _ in _SECTIONS
    )

    sections_html = "\n".join(
        _render_section(title, getattr(result, attr))
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
      --red:        #e53e3e;
      --red-bg:     #fff5f5;
      --red-border: #fed7d7;
      --yellow:     #d69e2e;
      --yellow-bg:  #fffff0;
      --yel-border: #fefcbf;
      --green:      #38a169;
      --green-bg:   #f0fff4;
      --grn-border: #c6f6d5;
      --grey:       #718096;
      --text:       #1a202c;
      --border:     #e2e8f0;
      --bg:         #f7fafc;
      --white:      #ffffff;
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    }}

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: var(--font);
      font-size: 14px;
      color: var(--text);
      background: var(--bg);
      line-height: 1.6;
    }}

    /* ── Layout ── */
    .container {{ max-width: 960px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }}

    /* ── Header ── */
    header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1.5rem;
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 2px solid var(--border);
      flex-wrap: wrap;
    }}
    header h1 {{ font-size: 1.4rem; font-weight: 700; color: var(--text); }}
    header .meta {{ font-size: 0.8rem; color: var(--grey); margin-top: 0.25rem; }}
    header .meta code {{
      font-family: var(--mono);
      background: var(--border);
      padding: 1px 5px;
      border-radius: 3px;
    }}

    /* ── Score card ── */
    .score-card {{
      text-align: center;
      padding: 1rem 1.5rem;
      background: var(--white);
      border: 2px solid var(--border);
      border-radius: 12px;
      min-width: 140px;
    }}
    .score-number {{
      font-size: 2.8rem;
      font-weight: 800;
      line-height: 1;
      color: {score_color};
    }}
    .score-label {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--grey);
      margin-top: 0.3rem;
    }}

    /* ── Summary pills ── */
    .summary {{
      display: flex;
      gap: 1rem;
      margin-bottom: 2rem;
      flex-wrap: wrap;
    }}
    .pill {{
      padding: 0.4rem 0.9rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 600;
      border: 1px solid transparent;
    }}
    .pill-critical {{
      background: var(--red-bg);
      border-color: var(--red-border);
      color: var(--red);
    }}
    .pill-warning {{
      background: var(--yellow-bg);
      border-color: var(--yel-border);
      color: var(--yellow);
    }}
    .pill-clean {{
      background: var(--green-bg);
      border-color: var(--grn-border);
      color: var(--green);
    }}

    /* ── Sections ── */
    section {{
      background: var(--white);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 1.25rem;
      overflow: hidden;
    }}
    section h2 {{
      font-size: 0.9rem;
      font-weight: 600;
      padding: 0.75rem 1rem;
      background: var(--bg);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    .section-count {{ margin-left: auto; display: flex; gap: 0.4rem; }}
    .clean {{
      padding: 0.75rem 1rem;
      color: var(--green);
      font-size: 0.82rem;
      font-weight: 500;
    }}

    /* ── Table ── */
    table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
    th {{
      text-align: left;
      padding: 0.5rem 1rem;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--grey);
      background: var(--bg);
      border-bottom: 1px solid var(--border);
    }}
    th:first-child {{ width: 110px; }}
    td {{ padding: 0.55rem 1rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    td code {{ font-family: var(--mono); font-size: 0.8em; }}

    .row-critical {{ background: var(--red-bg); }}
    .row-warning  {{ background: var(--yellow-bg); }}

    /* ── Badges ── */
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}
    .badge-critical {{ background: var(--red-border);   color: var(--red);    }}
    .badge-warning  {{ background: var(--yel-border);   color: var(--yellow); }}
    .badge-warning  {{ background: var(--yel-border);   color: var(--yellow); }}

    /* ── Footer ── */
    footer {{
      margin-top: 3rem;
      padding-top: 1rem;
      border-top: 1px solid var(--border);
      font-size: 0.75rem;
      color: var(--grey);
      text-align: center;
    }}

    @media (max-width: 600px) {{
      header {{ flex-direction: column; }}
      .score-card {{ align-self: flex-start; }}
    }}
  </style>
</head>
<body>
  <div class="container">

    <header>
      <div>
        <h1>django-arch-check</h1>
        <div class="meta">
          Project: <code>{_e(project_path)}</code><br>
          Generated: {generated_at} &nbsp;·&nbsp; v{_e(__version__)}
        </div>
      </div>
      <div class="score-card">
        <div class="score-number">{score}</div>
        <div class="score-label">Health Score</div>
        <div class="score-label" style="color:{score_color}; font-weight:600;">{_e(score_label)}</div>
      </div>
    </header>

    <div class="summary">
      <span class="pill pill-critical">{total_criticals} critical</span>
      <span class="pill pill-warning">{total_warnings} warning</span>
      {'<span class="pill pill-clean">All checks passed</span>' if total_criticals + total_warnings == 0 else ''}
    </div>

    {sections_html}

    <footer>
      Score: 100 &minus; {_CRITICAL_PENALTY}&times;criticals &minus; {_WARNING_PENALTY}&times;warnings &nbsp;|&nbsp;
      Generated by <strong>django-arch-check</strong> v{_e(__version__)}
    </footer>

  </div>
</body>
</html>"""
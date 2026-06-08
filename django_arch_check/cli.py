"""CLI entry point for django-arch-check."""

from __future__ import annotations

import os
import sys
import time

import click

from django_arch_check import __version__
from django_arch_check.analyzer import (
    AnalysisResult,
    run_analysis,
    validate_ignored_detectors,
)
from django_arch_check.report import compute_score, generate_html, score_grade, score_label
from django_arch_check.serializers import generate_json, generate_sarif

# ---------------------------------------------------------------------------
# Severity styling
# ---------------------------------------------------------------------------

_SEVERITY_STYLE: dict[str, dict[str, object]] = {
    "critical": {"fg": "red", "bold": True},
    "warning": {"fg": "yellow", "bold": False},
}


def _severity_label(severity: str) -> str:
    """Return a fixed-width, coloured severity label."""
    label = f"[{severity.upper()}]"
    # Pad to 10 chars so WARNING and CRITICAL columns align.
    label = f"{label:<10}"
    return click.style(label, **_SEVERITY_STYLE.get(severity, {}))  # type: ignore[arg-type]


def _is_skipped(result: AnalysisResult, detector_name: str) -> bool:
    """Return True if the detector was skipped for this analysis run."""
    return detector_name in result.skipped_detectors


# ---------------------------------------------------------------------------
# Section printers
# ---------------------------------------------------------------------------


def _print_fat_models(result: AnalysisResult) -> None:
    """Print fat-model findings and their section summary."""
    findings = result.fat_models

    click.echo()
    click.echo(click.style("── Fat Models ──────────────────────────────", bold=True))

    if _is_skipped(result, "fat_models"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No fat models found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        click.echo(
            f"  {label} {f.file_path} → "
            + click.style(f.class_name, bold=True)
            + f" ({f.method_count} methods)"
        )

    count = len(findings)
    click.echo()
    click.echo(
        click.style(f"  Found {count} fat model(s).", fg="red" if count else "green")
    )


def _print_god_apps(result: AnalysisResult) -> None:
    """Print god-app findings and their section summary."""
    findings = result.god_apps

    click.echo()
    click.echo(click.style("── God Apps ────────────────────────────────", bold=True))

    if _is_skipped(result, "god_apps"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No god apps found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        click.echo(
            f"  {label} "
            + click.style(f.app_path, bold=True)
            + f" owns {f.percentage}% of total project code"
            + f" ({f.app_loc:,} / {f.total_loc:,} lines)"
        )

    count = len(findings)
    click.echo()
    click.echo(
        click.style(f"  Found {count} god app(s).", fg="red" if count else "green")
    )


def _print_circular_imports(result: AnalysisResult) -> None:
    """Print circular-import findings and their section summary."""
    findings = result.circular_imports

    click.echo()
    click.echo(click.style("── Circular Imports ────────────────────────", bold=True))

    if _is_skipped(result, "circular_imports"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No circular imports found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        click.echo(
            f"  {label} Circular import detected: "
            + click.style(f.cycle_display, bold=True)
        )

    count = len(findings)
    click.echo()
    click.echo(click.style(f"  Found {count} circular import(s).", fg="red"))


def _print_missing_service_layer(result: AnalysisResult) -> None:
    """Print missing-service-layer findings and their section summary."""
    findings = result.missing_service_layer

    click.echo()
    click.echo(click.style("── Missing Service Layer ────────────────────", bold=True))

    if _is_skipped(result, "missing_service_layer"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No missing service layer issues found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        call_label = "call" if f.orm_call_count == 1 else "calls"
        if f.severity == "critical":
            detail = f"contains {f.orm_call_count} direct ORM {call_label}"
        else:
            detail = f"makes {f.orm_call_count} direct ORM {call_label}"
        click.echo(
            f"  {label} {f.file_path} → "
            + click.style(f.view_name + "()", bold=True)
            + f" {detail}"
        )

    count = len(findings)
    click.echo()
    click.echo(
        click.style(
            f"  Found {count} missing service layer issue(s).",
            fg="red" if count else "green",
        )
    )


def _print_celery_tasks(result: AnalysisResult) -> None:
    """Print Celery task findings and their section summary."""
    findings = result.celery_tasks

    click.echo()
    click.echo(click.style("── Celery Tasks Without Retry ──────────────", bold=True))

    if _is_skipped(result, "celery_tasks"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No Celery tasks missing retry config.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        if f.severity == "critical":
            detail = "high-stakes task, no retry configured"
        else:
            detail = "no retry configured"
        click.echo(
            f"  {label} {f.file_path} → "
            + click.style(f.task_name + "()", bold=True)
            + f" — {detail}"
        )

    count = len(findings)
    click.echo()
    click.echo(
        click.style(
            f"  Found {count} Celery task(s) without retry.",
            fg="red" if count else "green",
        )
    )


def _print_direct_sql(result: AnalysisResult) -> None:
    """Print direct-SQL findings and their section summary."""
    findings = result.direct_sql

    click.echo()
    click.echo(click.style("── Direct SQL ──────────────────────────────", bold=True))

    if _is_skipped(result, "direct_sql"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No direct SQL usage found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        click.echo(
            f"  {label} {f.file_path}:{f.line_number} → raw SQL detected: "
            + click.style(f.pattern, bold=True)
        )

    count = len(findings)
    click.echo()
    click.echo(click.style(f"  Found {count} direct SQL usage(s).", fg="yellow"))


def _print_n1_serializer_risk(result: AnalysisResult) -> None:
    """Print N+1 serializer-risk findings and their section summary."""
    findings = result.n1_serializer_risk

    click.echo()
    click.echo(click.style("── N+1 Serializer Risk ─────────────────────", bold=True))

    if _is_skipped(result, "n1_serializer_risk"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No N+1 serializer risks found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        click.echo(
            f"  {label} {f.file}:{f.line} → "
            + click.style(f.message, bold=True)
        )

    count = len(findings)
    click.echo()
    click.echo(click.style(f"  Found {count} N+1 serializer risk(s).", fg="yellow"))


def _print_n_plus_one(result: AnalysisResult) -> None:
    """Print N+1 query-risk findings and their section summary."""
    findings = result.n_plus_one

    click.echo()
    click.echo(click.style("── N+1 Query Risks ─────────────────────────", bold=True))

    if _is_skipped(result, "n_plus_one"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No N+1 query risks found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        click.echo(
            f"  {label} {f.file_path}:{f.line_number} → "
            + click.style("ORM call inside loop", bold=True)
            + " — possible N+1 query risk"
        )

    count = len(findings)
    click.echo()
    click.echo(click.style(f"  Found {count} potential N+1 issue(s).", fg="yellow"))

def _print_migration_safety(result: AnalysisResult) -> None:
    """Print migration safety findings and their section summary."""
    findings = result.migration_safety

    click.echo()
    click.echo(click.style("── Migration Safety ────────────────────────", bold=True))

    if _is_skipped(result, "migration_safety"):
        click.echo(click.style("  ⊘ Skipped (--ignore flag)", fg="cyan"))
        return

    if not findings:
        click.echo(click.style("  No migration safety issues found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)

        # Build a compact context string for the operation
        if f.model_name and f.field_name:
            context = f"{f.model_name}.{f.field_name}"
        elif f.model_name:
            context = f.model_name
        else:
            context = ""

        op_display = f"{f.operation}({context})" if context else f.operation

        click.echo(
            f"  {label} {f.file_path} → "
            + click.style(op_display, bold=True)
        )
        # Advisory message indented under the finding line
        click.echo(f"{'':13}  ℹ  {f.message}")

    count = len(findings)
    click.echo()
    click.echo(
        click.style(f"  Found {count} migration safety issue(s).", fg="yellow")
    )

def _write_html_report(result: AnalysisResult, project_path: str) -> None:
    import os
    html_content = generate_html(result, project_path)
    out_path = os.path.join(project_path, "arch-report.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    sc = compute_score(result, project_path)
    grade = score_grade(sc)
    label = score_label(sc)
    click.echo(click.style(f"  Health score: {sc}/100  {grade} · {label}", bold=True))
    click.echo(click.style(f"  Report saved: {out_path}", fg="cyan"))

def _has_critical_findings(result: AnalysisResult) -> bool:
    """Return True if any detector emitted a critical or error finding."""
    from django_arch_check.report import _SECTIONS
    return any(
        getattr(f, "severity", "") in ("critical", "error")
        for attr, _ in _SECTIONS
        for f in getattr(result, attr, [])
    )


def _print_text_result(result: AnalysisResult) -> None:
    """Print all detector sections to stdout in text format."""
    _print_fat_models(result)
    _print_god_apps(result)
    _print_circular_imports(result)
    _print_missing_service_layer(result)
    _print_celery_tasks(result)
    _print_direct_sql(result)
    _print_n_plus_one(result)
    _print_migration_safety(result)
    _print_n1_serializer_risk(result)


def _finding_key(finding: object) -> str:
    """Return a stable string identity for a finding used to detect diffs."""
    parts = [
        getattr(finding, attr, "")
        for attr in ("file_path", "file", "class_name", "view_name", "task_name",
                     "cycle_display", "app_path", "pattern", "line_number",
                     "line", "operation", "migration_name", "message", "severity")
    ]
    return "|".join(str(p) for p in parts)


def _all_finding_keys(result: AnalysisResult) -> set[str]:
    from django_arch_check.report import _SECTIONS
    return {
        _finding_key(f)
        for attr, _ in _SECTIONS
        for f in getattr(result, attr, [])
    }


def _print_watch_diff(
    prev: AnalysisResult | None,
    curr: AnalysisResult,
    changed_files: list[str],
) -> None:
    """Print only what changed between two analysis runs."""
    from django_arch_check.report import _SECTIONS

    if changed_files:
        click.echo(
            click.style("  Changed: ", fg="cyan")
            + ", ".join(os.path.basename(p) for p in changed_files[:5])
            + (f" (+{len(changed_files) - 5} more)" if len(changed_files) > 5 else "")
        )

    if prev is None:
        _print_text_result(curr)
        return

    prev_keys = _all_finding_keys(prev)
    curr_keys = _all_finding_keys(curr)
    resolved = prev_keys - curr_keys
    new_keys = curr_keys - prev_keys

    if not resolved and not new_keys:
        click.echo(click.style("  No changes to findings.", fg="green"))
        return

    if resolved:
        click.echo(
            click.style(f"  ✔  {len(resolved)} finding(s) resolved.", fg="green")
        )
    if new_keys:
        click.echo(
            click.style(f"  ✖  {len(new_keys)} new finding(s):", fg="red", bold=True)
        )
        for attr, title in _SECTIONS:
            prev_attr_keys = {_finding_key(f) for f in getattr(prev, attr, [])}
            for f in getattr(curr, attr, []):
                if _finding_key(f) not in prev_attr_keys:
                    sev = getattr(f, "severity", "warning")
                    label = _severity_label(sev)
                    click.echo(f"    {label} [{title}] {_finding_key(f).split('|')[0]}")


def _run_watch(
    project_path: str,
    fat_model_threshold: int,
    god_app_threshold: int,
    ignored_detectors: tuple[str, ...],
    ignore_paths: tuple[str, ...],
) -> None:
    """Run analysis in watch mode, re-running on every .py file change."""
    from django_arch_check.watcher import _snapshot, _diff

    def _do_run(prev: AnalysisResult | None, changed: list[str]) -> AnalysisResult:
        now = time.strftime("%H:%M:%S")
        click.echo()
        click.echo(click.style("─" * 50, fg="cyan"))
        click.echo(
            click.style(f"[{now}] ", fg="cyan")
            + click.style(f"Analyzing: {project_path}", bold=True)
        )
        result = run_analysis(
            project_path,
            fat_model_threshold=fat_model_threshold,
            god_app_threshold=god_app_threshold,
            ignored_detectors=ignored_detectors,
            ignore_paths=ignore_paths,
        )
        sc = compute_score(result, project_path)
        grade = score_grade(sc)
        label = score_label(sc)
        color = "green" if sc >= 75 else "yellow" if sc >= 60 else "red"
        click.echo(
            click.style(f"  Score: {sc}/100  {grade} · {label}", fg=color, bold=True)
        )
        _print_watch_diff(prev, result, changed)
        return result

    click.echo(
        click.style("django-arch-check ", bold=True)
        + click.style(f"v{__version__}", fg="cyan")
        + click.style(" — watch mode", fg="cyan")
    )
    click.echo(click.style(f"  Watching: {project_path}", fg="cyan"))
    click.echo(click.style("  Press Ctrl+C to stop.", fg="cyan"))

    prev_result: AnalysisResult | None = None
    prev_result = _do_run(None, [])

    current_snapshot = _snapshot(project_path)

    try:
        while True:
            time.sleep(1)
            fresh_snapshot = _snapshot(project_path)
            changed = _diff(current_snapshot, fresh_snapshot)
            if changed:
                current_snapshot = fresh_snapshot
                # debounce: wait briefly for rapid multi-file saves
                time.sleep(0.3)
                fresh_snapshot = _snapshot(project_path)
                changed = _diff(current_snapshot, fresh_snapshot) or changed
                current_snapshot = fresh_snapshot
                prev_result = _do_run(prev_result, changed)
    except KeyboardInterrupt:
        click.echo()
        click.echo(click.style("  Watch stopped.", fg="cyan"))


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="django-arch-check")
def main() -> None:
    """Django Architectural Health Checker.

    Analyzes a Django project for structural and architectural issues.
    """


@main.command()
@click.argument(
    "project_path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True),
)
@click.option(
    "--fat-model-threshold",
    default=15,
    show_default=True,
    metavar="N",
    help="Flag models with >= N non-dunder methods.",
)
@click.option(
    "--god-app-threshold",
    default=30,
    show_default=True,
    metavar="PCT",
    help="Flag apps owning >= PCT% of total project LOC.",
)
@click.option(
    "--ignore",
    "ignored_detectors",
    multiple=True,
    metavar="DETECTOR",
    help="Ignore a detector by name. Repeatable.",
)
@click.option(
    "--ignore-path",
    "ignore_paths",
    multiple=True,
    metavar="PATH",
    help="Skip files whose path contains PATH. Repeatable.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "html", "json", "sarif"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format: text/html/json/sarif. HTML writes arch-report.html; the others use stdout.",
)
@click.option(
    "--watch",
    "watch_mode",
    is_flag=True,
    default=False,
    help="Re-run analysis automatically on every .py file change. Text format only.",
)
def analyze(
    project_path: str,
    fat_model_threshold: int,
    god_app_threshold: int,
    ignored_detectors: tuple[str, ...],
    ignore_paths: tuple[str, ...],
    output_format: str,
    watch_mode: bool,
) -> None:
    """Analyze a Django project at PROJECT_PATH for architectural issues."""
    try:
        ignored_detectors = validate_ignored_detectors(ignored_detectors)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if watch_mode and output_format != "text":
        raise click.ClickException("--watch is only supported with --format text.")

    if watch_mode:
        _run_watch(
            project_path=project_path,
            fat_model_threshold=fat_model_threshold,
            god_app_threshold=god_app_threshold,
            ignored_detectors=ignored_detectors,
            ignore_paths=ignore_paths,
        )
        return

    result = run_analysis(
        project_path,
        fat_model_threshold=fat_model_threshold,
        god_app_threshold=god_app_threshold,
        ignored_detectors=ignored_detectors,
        ignore_paths=ignore_paths,
    )

    if output_format in {"text", "html"}:
        click.echo(click.style(f"Analyzing: {project_path}", bold=True))

    if output_format == "html":
        _write_html_report(result, project_path)

    elif output_format == "json":
        click.echo(generate_json(result, project_path))

    elif output_format == "sarif":
        click.echo(generate_sarif(result, project_path))

    else:
        _print_text_result(result)

    # Exit non-zero if any critical findings exist across all detectors.
    if _has_critical_findings(result):
        sys.exit(1)

"""CLI entry point for django-arch-check."""

from __future__ import annotations

import sys

import click

from django_arch_check import __version__
from django_arch_check.analyzer import AnalysisResult, run_analysis
from django_arch_check.report import generate_html

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


# ---------------------------------------------------------------------------
# Section printers
# ---------------------------------------------------------------------------


def _print_fat_models(result: AnalysisResult) -> None:
    """Print fat-model findings and their section summary."""
    findings = result.fat_models

    click.echo()
    click.echo(click.style("── Fat Models ──────────────────────────────", bold=True))

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

    if not findings:
        click.echo(click.style("  No missing service layer issues found.", fg="green"))
        return

    for f in findings:
        label = _severity_label(f.severity)
        if f.severity == "critical":
            detail = f"contains {f.line_count} lines of business logic"
        else:
            detail = "makes direct ORM calls"
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


def _print_n_plus_one(result: AnalysisResult) -> None:
    """Print N+1 query-risk findings and their section summary."""
    findings = result.n_plus_one

    click.echo()
    click.echo(click.style("── N+1 Query Risks ─────────────────────────", bold=True))

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


def _write_html_report(result: AnalysisResult, project_path: str) -> None:
    """Generate arch-report.html and write it to the project root."""
    import os

    from django_arch_check.report import compute_score

    html_content = generate_html(result, project_path)
    out_path = os.path.join(project_path, "arch-report.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    score = compute_score(result)
    click.echo(click.style(f"  Health score: {score}/100", bold=True))
    click.echo(click.style(f"  Report saved: {out_path}", fg="cyan"))


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
    "--format",
    "output_format",
    type=click.Choice(["text", "html"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format: text (stdout) or html (arch-report.html).",
)
def analyze(
    project_path: str,
    fat_model_threshold: int,
    god_app_threshold: int,
    output_format: str,
) -> None:
    """Analyze a Django project at PROJECT_PATH for architectural issues."""
    click.echo(click.style(f"Analyzing: {project_path}", bold=True))

    result = run_analysis(
        project_path,
        fat_model_threshold=fat_model_threshold,
        god_app_threshold=god_app_threshold,
    )

    if output_format == "html":
        _write_html_report(result, project_path)
        return

    _print_fat_models(result)
    _print_god_apps(result)
    _print_circular_imports(result)
    _print_missing_service_layer(result)
    _print_celery_tasks(result)
    _print_direct_sql(result)
    _print_n_plus_one(result)

    # Exit non-zero if any critical findings exist across all detectors —
    # allows the tool to act as a CI gate.
    has_critical = (
        any(f.severity == "critical" for f in result.fat_models)
        or any(f.severity == "critical" for f in result.god_apps)
        or any(f.severity == "critical" for f in result.circular_imports)
        or any(f.severity == "critical" for f in result.missing_service_layer)
        or any(f.severity == "critical" for f in result.celery_tasks)
    )
    if has_critical:
        sys.exit(1)

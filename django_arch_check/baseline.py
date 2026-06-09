"""Baseline file support for django-arch-check.

A baseline lets teams snapshot current findings and only fail CI on
*new* regressions introduced after the snapshot was taken.

Workflow
--------
    # 1. Snapshot current state (run once, commit the file)
    django-arch-check baseline ./

    # 2. In CI: only fail on findings NOT in the baseline
    django-arch-check analyze --baseline ./

File format
-----------
    .arch-baseline.json — a JSON object written to the project root:

    {
      "version": 1,
      "created": "2026-06-08T12:00:00Z",
      "tool_version": "1.0.0",
      "finding_keys": ["key1", "key2", ...]
    }

Finding keys are the same stable strings produced by cli._finding_key().
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from django_arch_check import __version__
from django_arch_check.analyzer import AnalysisResult
from django_arch_check.report import _SECTIONS

BASELINE_FILENAME = ".arch-baseline.json"
_SCHEMA_VERSION = 1


def _finding_key(finding: object) -> str:
    """Stable string identity for a finding — mirrors cli._finding_key."""
    parts = [
        getattr(finding, attr, "")
        for attr in (
            "file_path", "file", "class_name", "view_name", "task_name",
            "cycle_display", "app_path", "pattern", "line_number",
            "line", "operation", "migration_name", "message", "severity",
        )
    ]
    return "|".join(str(p) for p in parts)


def _result_keys(result: AnalysisResult) -> set[str]:
    return {
        _finding_key(f)
        for attr, _ in _SECTIONS
        for f in getattr(result, attr, [])
    }


def write_baseline(result: AnalysisResult, project_path: str) -> Path:
    """Write a baseline file from *result* into *project_path*.

    Returns the path of the written file.
    """
    keys = sorted(_result_keys(result))
    payload = {
        "version": _SCHEMA_VERSION,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_version": __version__,
        "finding_keys": keys,
    }
    out = Path(project_path) / BASELINE_FILENAME
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def load_baseline(project_path: str) -> set[str] | None:
    """Load baseline finding keys from *project_path*.

    Returns None if no baseline file exists.
    Raises click.ClickException-friendly ValueError on corrupt files.
    """
    path = Path(project_path) / BASELINE_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("finding_keys", []))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"Could not read baseline file {path}: {exc}"
        ) from exc


def new_findings(result: AnalysisResult, baseline_keys: set[str]) -> AnalysisResult:
    """Return a copy of *result* containing only findings absent from the baseline."""
    kwargs: dict = {"skipped_detectors": result.skipped_detectors}
    for attr, _ in _SECTIONS:
        findings = getattr(result, attr, [])
        kwargs[attr] = [f for f in findings if _finding_key(f) not in baseline_keys]
    return AnalysisResult(**kwargs)

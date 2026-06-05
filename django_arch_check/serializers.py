"""Machine-readable serializers for django-arch-check results."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from django_arch_check import __version__
from django_arch_check.analyzer import AnalysisResult
from django_arch_check.report import compute_score

_HOMEPAGE: Final[str] = "https://github.com/RJ-Gamer/django-arch-check"

_DETECTORS: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "fat_models",
        "Fat Models",
        "Model classes with too many non-dunder methods.",
        "Flags Django model classes whose method count suggests business logic"
        " has accumulated in the model layer.",
    ),
    (
        "god_apps",
        "God Apps",
        "Apps that own a disproportionate share of project code.",
        "Flags Django apps whose percentage of total project LOC suggests"
        " excessive architectural centralization.",
    ),
    (
        "circular_imports",
        "Circular Imports",
        "Cycles in the intra-project import graph.",
        "Flags import cycles between project modules because they make module"
        " boundaries brittle and harder to reason about.",
    ),
    (
        "missing_service_layer",
        "Missing Service Layer",
        "Views with too many direct ORM calls.",
        "Flags view functions and methods that likely need service-layer"
        " extraction due to repeated direct ORM access.",
    ),
    (
        "celery_tasks",
        "Celery Tasks Without Retry",
        "Celery tasks missing retry configuration.",
        "Flags Celery tasks that have no retry policy, especially tasks whose"
        " names suggest high-stakes work like payments or notifications.",
    ),
    (
        "direct_sql",
        "Direct SQL",
        "Raw SQL usage that bypasses Django's ORM.",
        "Flags raw SQL patterns such as cursor.execute, connection.cursor,"
        " .raw, and .extra(select=...).",
    ),
    (
        "n_plus_one",
        "N+1 Query Risks",
        "Possible N+1 ORM queries inside loops and comprehensions.",
        "Flags likely N+1 query patterns when ORM calls occur inside loops"
        " without nearby select_related or prefetch_related usage.",
    ),
    (
        "migration_safety",
        "Migration Safety",
        "Migration operations that carry deployment or data-safety risk.",
        "Flags AddField without a default on NOT NULL columns, RemoveField, RenameField, "
        "RunPython without atomic=False, and RunSQL. Each finding is an advisory — not an "
        "error — and includes the risk, when it is safe to ignore, and a safer alternative.",
    ),
    (
        "n1_serializer_risk",
        "N+1 Serializer Risk",
        "Possible N+1 ORM access patterns in DRF serializers and viewsets.",
        "Flags SerializerMethodField ORM calls, nested serializers without matching "
        "prefetch/select coverage, serializer source= values that hit ORM-backed model "
        "@property methods, and bare viewset querysets paired with relational serializers.",
    ),
)


def generate_json(result: AnalysisResult, project_path: str) -> str:
    """Return a pretty-printed JSON report."""
    critical_count, warning_count = _summary_counts(result)
    payload: dict[str, Any] = {
        "tool": {
            "name": "django-arch-check",
            "version": __version__,
            "homepage": _HOMEPAGE,
        },
        "project_path": project_path,
        "generated_at": _generated_at(),
        "summary": {
            "health_score": compute_score(result, project_path),
            "critical_count": critical_count,
            "warning_count": warning_count,
            "total_findings": critical_count + warning_count,
            "skipped_detectors": list(result.skipped_detectors),
        },
        "detectors": [
            _detector_payload(detector_id, title, getattr(result, detector_id), result)
            for detector_id, title, _, _ in _DETECTORS
        ],
    }
    # Keep machine-readable output ASCII-safe so Windows cp1252 shells can
    # redirect it without choking on glyphs like "→" or "—".
    return json.dumps(payload, indent=2, ensure_ascii=True)


def generate_sarif(result: AnalysisResult, project_path: str) -> str:
    """Return a SARIF v2.1.0 report."""
    critical_count, warning_count = _summary_counts(result)
    rules = [
        {
            "id": detector_id,
            "name": title,
            "shortDescription": {"text": short_description},
            "help": {"text": help_text},
            "properties": {
                "tags": ["django", "architecture", detector_id],
            },
        }
        for detector_id, title, short_description, help_text in _DETECTORS
    ]

    results: list[dict[str, Any]] = []
    for rule_index, (detector_id, _, _, _) in enumerate(_DETECTORS):
        for finding in getattr(result, detector_id):
            results.append(_sarif_result(finding, detector_id, rule_index))

    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "django-arch-check",
                "version": __version__,
                "semanticVersion": __version__,
                "informationUri": _HOMEPAGE,
                "rules": rules,
            }
        },
        "results": results,
        "invocations": [{"executionSuccessful": True}],
        "properties": {
            "projectPath": project_path,
            "healthScore": compute_score(result),
            "criticalCount": critical_count,
            "warningCount": warning_count,
            "skippedDetectors": list(result.skipped_detectors),
        },
    }

    srcroot_uri = _srcroot_uri(project_path)
    if srcroot_uri is not None:
        run["originalUriBaseIds"] = {"%SRCROOT%": {"uri": srcroot_uri}}

    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [run],
    }
    return json.dumps(payload, indent=2, ensure_ascii=True)


def _detector_payload(
    detector_id: str,
    title: str,
    findings: list[object],
    result: AnalysisResult,
) -> dict[str, Any]:
    critical_count, warning_count = _count_by_severity(findings)
    return {
        "id": detector_id,
        "name": title,
        "skipped": detector_id in result.skipped_detectors,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "finding_count": len(findings),
        "findings": [_finding_payload(finding) for finding in findings],
    }


def _finding_payload(finding: object) -> dict[str, Any]:
    if is_dataclass(finding):
        payload: dict[str, Any] = dict(asdict(finding))
    else:
        payload = {"value": str(finding)}

    payload["message"] = _finding_message(finding)

    location = _finding_location(finding)
    if location is not None:
        payload["location"] = location

    return payload


def _sarif_result(
    finding: object,
    detector_id: str,
    rule_index: int,
) -> dict[str, Any]:
    severity = str(getattr(finding, "severity", "warning"))
    result: dict[str, Any] = {
        "ruleId": detector_id,
        "ruleIndex": rule_index,
        "level": _sarif_level(severity),
        "message": {"text": _finding_message(finding)},
        "properties": {
            "severity": severity,
            "detector": detector_id,
        },
    }

    location = _finding_location(finding)
    if location is not None:
        physical_location: dict[str, Any] = {
            "artifactLocation": {
                "uri": str(location["path"]),
                "uriBaseId": "%SRCROOT%",
            }
        }
        if "line" in location:
            physical_location["region"] = {"startLine": int(location["line"])}
        result["locations"] = [{"physicalLocation": physical_location}]

    return result


def _summary_counts(result: AnalysisResult) -> tuple[int, int]:
    critical_count = 0
    warning_count = 0
    for detector_id, _, _, _ in _DETECTORS:
        critical, warning = _count_by_severity(getattr(result, detector_id))
        critical_count += critical
        warning_count += warning
    return critical_count, warning_count


def _count_by_severity(findings: list[object]) -> tuple[int, int]:
    critical = sum(
        1 for finding in findings
        if getattr(finding, "severity", "") in ("critical", "error")
    )
    warning = sum(1 for finding in findings if getattr(finding, "severity", "") == "warning")
    return critical, warning


def _generated_at() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _srcroot_uri(project_path: str) -> str | None:
    try:
        uri = Path(project_path).resolve().as_uri()
    except (OSError, ValueError):
        return None
    return uri if uri.endswith("/") else uri + "/"


def _sarif_level(severity: str) -> str:
    return "error" if severity in ("critical", "error") else "warning"


def _finding_message(finding: object) -> str:
    severity = str(getattr(finding, "severity", "warning"))

    if hasattr(finding, "detector") and getattr(finding, "detector") == "N1SerializerRisk":
        return str(getattr(finding, "message", finding))

    if hasattr(finding, "class_name") and hasattr(finding, "method_count"):
        return (
            f"{getattr(finding, 'file_path')} → {getattr(finding, 'class_name')}"
            f" ({getattr(finding, 'method_count')} methods)"
        )

    if hasattr(finding, "app_path") and hasattr(finding, "percentage"):
        return (
            f"{getattr(finding, 'app_path')} owns {getattr(finding, 'percentage')}%"
            " of total project code"
            f" ({getattr(finding, 'app_loc'):,} / {getattr(finding, 'total_loc'):,}"
            " lines)"
        )

    if hasattr(finding, "cycle_display"):
        return f"Circular import detected: {getattr(finding, 'cycle_display')}"

    if hasattr(finding, "view_name") and hasattr(finding, "orm_call_count"):
        orm_call_count = int(getattr(finding, "orm_call_count"))
        call_label = "call" if orm_call_count == 1 else "calls"
        detail = (
            f"contains {orm_call_count} direct ORM {call_label}"
            if severity == "critical"
            else f"makes {orm_call_count} direct ORM {call_label}"
        )
        return (
            f"{getattr(finding, 'file_path')} → {getattr(finding, 'view_name')}()"
            f" {detail}"
        )

    if hasattr(finding, "task_name"):
        detail = (
            "high-stakes task, no retry configured"
            if severity == "critical"
            else "no retry configured"
        )
        return (
            f"{getattr(finding, 'file_path')} → {getattr(finding, 'task_name')}()"
            f" — {detail}"
        )

    if hasattr(finding, "pattern") and hasattr(finding, "line_number"):
        return (
            f"{getattr(finding, 'file_path')}:{getattr(finding, 'line_number')}"
            f" → raw SQL detected: {getattr(finding, 'pattern')}"
        )

    if hasattr(finding, "line_number"):
        return (
            f"{getattr(finding, 'file_path')}:{getattr(finding, 'line_number')}"
            " → ORM call inside loop — possible N+1 query risk"
        )
    
    if hasattr(finding, "migration_name") and hasattr(finding, "operation"):
        model = getattr(finding, "model_name", "")
        field = getattr(finding, "field_name", "")
        context_parts = []
        if model:
            context_parts.append(f"model={model!r}")
        if field:
            context_parts.append(f"field={field!r}")
        context = ", ".join(context_parts)
        op = getattr(finding, "operation")
        op_display = f"{op}({context})" if context else op
        return (
            f"{getattr(finding, 'file_path')} → {op_display}: "
            f"{getattr(finding, 'message')}"
        )

    return str(finding)


def _finding_location(finding: object) -> dict[str, int | str] | None:
    path: str | None = None
    if hasattr(finding, "file_path"):
        path = str(getattr(finding, "file_path"))
    elif hasattr(finding, "file"):
        path = str(getattr(finding, "file"))
    elif hasattr(finding, "app_path"):
        path = str(getattr(finding, "app_path")).rstrip("/\\")

    if not path:
        return None

    location: dict[str, int | str] = {"path": path.replace("\\", "/")}
    if hasattr(finding, "line_number"):
        location["line"] = int(getattr(finding, "line_number"))
    elif hasattr(finding, "line"):
        location["line"] = int(getattr(finding, "line"))
    return location

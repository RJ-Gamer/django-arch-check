"""Main analysis orchestrator.

This module is responsible for running all registered detectors against a
Django project and returning their aggregated results to the CLI layer.

To add a new detector:
    1. Implement it in ``detectors/<name>.py`` with a ``detect(project_path)``
       function that returns a list of finding dataclasses.
    2. Import the detector module and its Finding type below.
    3. Add a field to :class:`AnalysisResult` for the new findings list.
    4. Call the detector inside :func:`run_analysis` and populate the field.
    5. Add any new threshold parameters to :func:`run_analysis` and pass them
       through from the CLI layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from django_arch_check.detectors import (
    celery_tasks,
    circular_imports,
    direct_sql,
    fat_models,
    god_apps,
    missing_service_layer,
    n_plus_one,
    migration_safety,
)
from django_arch_check.detectors.celery_tasks import CeleryTaskFinding
from django_arch_check.detectors.circular_imports import CircularImportFinding
from django_arch_check.detectors.direct_sql import DirectSQLFinding
from django_arch_check.detectors.fat_models import FatModelFinding
from django_arch_check.detectors.god_apps import GodAppFinding
from django_arch_check.detectors.missing_service_layer import MissingServiceLayerFinding
from django_arch_check.detectors.n_plus_one import NPlusOneFinding

from django_arch_check.detectors.migration_safety import MigrationSafetyFinding 

VALID_DETECTORS: Final[tuple[str, ...]] = (
    "fat_models",
    "god_apps",
    "circular_imports",
    "missing_service_layer",
    "celery_tasks",
    "direct_sql",
    "n_plus_one",
    "migration_safety",
)


def validate_ignored_detectors(ignored_detectors: tuple[str, ...]) -> tuple[str, ...]:
    """Validate and de-duplicate ignored detector names."""
    unknown = [name for name in ignored_detectors if name not in VALID_DETECTORS]
    if unknown:
        valid = ", ".join(VALID_DETECTORS)
        raise ValueError(
            f"Unknown detector '{unknown[0]}'. Valid detectors are: {valid}"
        )
    return tuple(dict.fromkeys(ignored_detectors))


@dataclass
class AnalysisResult:
    """Aggregated output of all detectors for a single project run."""

    fat_models: list[FatModelFinding] = field(default_factory=list)
    god_apps: list[GodAppFinding] = field(default_factory=list)
    circular_imports: list[CircularImportFinding] = field(default_factory=list)
    missing_service_layer: list[MissingServiceLayerFinding] = field(default_factory=list)
    celery_tasks: list[CeleryTaskFinding] = field(default_factory=list)
    direct_sql: list[DirectSQLFinding] = field(default_factory=list)
    n_plus_one: list[NPlusOneFinding] = field(default_factory=list)
    migration_safety: list[MigrationSafetyFinding] = field(default_factory=list)  
    skipped_detectors: tuple[str, ...] = ()


def run_analysis(
    project_path: str,
    fat_model_threshold: int = 15,
    god_app_threshold: int = 30,
    ignored_detectors: tuple[str, ...] = (),
    ignore_paths: tuple[str, ...] = (),
) -> AnalysisResult:
    """Run all registered detectors against *project_path*.

    Args:
        project_path:        Root directory of the Django project.
        fat_model_threshold: Minimum method count to flag a model as fat.
        god_app_threshold:   Minimum LOC-% share to flag an app as a god app.
        ignored_detectors:   Detector names to skip entirely.
        ignore_paths:        File-path substrings to exclude from all detectors.

    Returns:
        An :class:`AnalysisResult` containing findings from every detector.
    """
    ignored_detectors = validate_ignored_detectors(ignored_detectors)
    ignored_set = set(ignored_detectors)
    ignore_paths = tuple(dict.fromkeys(ignore_paths))

    return AnalysisResult(
        fat_models=(
            []
            if "fat_models" in ignored_set
            else fat_models.detect(
                project_path,
                threshold=fat_model_threshold,
                ignore_paths=ignore_paths,
            )
        ),
        god_apps=(
            []
            if "god_apps" in ignored_set
            else god_apps.detect(
                project_path,
                threshold=god_app_threshold,
                ignore_paths=ignore_paths,
            )
        ),
        circular_imports=(
            []
            if "circular_imports" in ignored_set
            else circular_imports.detect(project_path, ignore_paths=ignore_paths)
        ),
        missing_service_layer=(
            []
            if "missing_service_layer" in ignored_set
            else missing_service_layer.detect(project_path, ignore_paths=ignore_paths)
        ),
        celery_tasks=(
            []
            if "celery_tasks" in ignored_set
            else celery_tasks.detect(project_path, ignore_paths=ignore_paths)
        ),
        direct_sql=(
            []
            if "direct_sql" in ignored_set
            else direct_sql.detect(project_path, ignore_paths=ignore_paths)
        ),
        n_plus_one=(
            []
            if "n_plus_one" in ignored_set
            else n_plus_one.detect(project_path, ignore_paths=ignore_paths)
        ),
        skipped_detectors=tuple(
            detector for detector in VALID_DETECTORS if detector in ignored_set
        ),
        migration_safety=(
            []
            if "migration_safety" in ignored_set
            else migration_safety.detect(project_path, ignore_paths=ignore_paths)
        ),
    )

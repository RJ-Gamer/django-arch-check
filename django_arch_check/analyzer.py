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

from django_arch_check.detectors import celery_tasks, circular_imports, direct_sql, fat_models, god_apps, missing_service_layer, n_plus_one
from django_arch_check.detectors.circular_imports import CircularImportFinding
from django_arch_check.detectors.fat_models import FatModelFinding
from django_arch_check.detectors.god_apps import GodAppFinding
from django_arch_check.detectors.celery_tasks import CeleryTaskFinding
from django_arch_check.detectors.direct_sql import DirectSQLFinding
from django_arch_check.detectors.n_plus_one import NPlusOneFinding
from django_arch_check.detectors.missing_service_layer import MissingServiceLayerFinding


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


def run_analysis(
    project_path: str,
    fat_model_threshold: int = 10,
    god_app_threshold: int = 30,
    service_layer_threshold: int = 10,
) -> AnalysisResult:
    """Run all registered detectors against *project_path*.

    Args:
        project_path:        Root directory of the Django project.
        fat_model_threshold: Minimum method count to flag a model as fat.
        god_app_threshold:        Minimum LOC-% share to flag an app as a god app.
        service_layer_threshold: Function body lines above which an ORM view is critical.

    Returns:
        An :class:`AnalysisResult` containing findings from every detector.
    """
    return AnalysisResult(
        fat_models=fat_models.detect(project_path, threshold=fat_model_threshold),
        god_apps=god_apps.detect(project_path, threshold=god_app_threshold),
        circular_imports=circular_imports.detect(project_path),
        missing_service_layer=missing_service_layer.detect(project_path, line_threshold=service_layer_threshold),
        celery_tasks=celery_tasks.detect(project_path),
        direct_sql=direct_sql.detect(project_path),
        n_plus_one=n_plus_one.detect(project_path),
    )
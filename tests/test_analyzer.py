"""Tests for analysis orchestration and detector skipping."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from django_arch_check import analyzer


def test_run_analysis_skips_ignored_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    skipped = Mock(return_value=["should-not-run"])
    called = Mock(return_value=[])

    monkeypatch.setattr(analyzer.fat_models, "detect", skipped)
    monkeypatch.setattr(analyzer.god_apps, "detect", called)
    monkeypatch.setattr(analyzer.circular_imports, "detect", called)
    monkeypatch.setattr(analyzer.missing_service_layer, "detect", called)
    monkeypatch.setattr(analyzer.celery_tasks, "detect", called)
    monkeypatch.setattr(analyzer.direct_sql, "detect", called)
    monkeypatch.setattr(analyzer.n_plus_one, "detect", called)

    result = analyzer.run_analysis("/project", ignored_detectors=("fat_models",))

    skipped.assert_not_called()
    assert called.call_count == 6
    assert result.skipped_detectors == ("fat_models",)


def test_validate_ignored_detectors_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown detector 'fat_modelz'"):
        analyzer.validate_ignored_detectors(("fat_modelz",))

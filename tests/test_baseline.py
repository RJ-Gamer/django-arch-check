"""Tests for baseline file support (baseline.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from django_arch_check.analyzer import AnalysisResult
from django_arch_check.baseline import (
    BASELINE_FILENAME,
    _finding_key,
    _result_keys,
    load_baseline,
    new_findings,
    write_baseline,
)
from django_arch_check.detectors.circular_imports import CircularImportFinding
from django_arch_check.detectors.fat_models import FatModelFinding


def _fat(name: str = "Order") -> FatModelFinding:
    return FatModelFinding(
        file_path="app/models.py", class_name=name,
        method_count=20, severity="critical",
    )


def _circ() -> CircularImportFinding:
    return CircularImportFinding(
        cycle_display="a.models -> b.models -> a.models", severity="critical"
    )


# ---------------------------------------------------------------------------
# _finding_key
# ---------------------------------------------------------------------------

def test_finding_key_is_stable() -> None:
    f = _fat()
    assert _finding_key(f) == _finding_key(f)


def test_finding_key_differs_for_different_findings() -> None:
    assert _finding_key(_fat("Order")) != _finding_key(_fat("Profile"))


# ---------------------------------------------------------------------------
# write_baseline / load_baseline
# ---------------------------------------------------------------------------

def test_write_baseline_creates_file(tmp_path: Path) -> None:
    result = AnalysisResult(fat_models=[_fat()])
    out = write_baseline(result, str(tmp_path))
    assert out == tmp_path / BASELINE_FILENAME
    assert out.is_file()


def test_write_baseline_contains_correct_keys(tmp_path: Path) -> None:
    result = AnalysisResult(fat_models=[_fat("Order"), _fat("Profile")])
    write_baseline(result, str(tmp_path))
    data = json.loads((tmp_path / BASELINE_FILENAME).read_text())
    assert data["version"] == 1
    assert len(data["finding_keys"]) == 2
    assert sorted(data["finding_keys"]) == data["finding_keys"]  # sorted


def test_write_baseline_empty_result(tmp_path: Path) -> None:
    write_baseline(AnalysisResult(), str(tmp_path))
    data = json.loads((tmp_path / BASELINE_FILENAME).read_text())
    assert data["finding_keys"] == []


def test_load_baseline_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_baseline(str(tmp_path)) is None


def test_load_baseline_returns_keys(tmp_path: Path) -> None:
    result = AnalysisResult(fat_models=[_fat()])
    write_baseline(result, str(tmp_path))
    keys = load_baseline(str(tmp_path))
    assert keys is not None
    assert len(keys) == 1


def test_load_baseline_raises_on_corrupt_file(tmp_path: Path) -> None:
    (tmp_path / BASELINE_FILENAME).write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Could not read baseline"):
        load_baseline(str(tmp_path))


def test_roundtrip_keys_match_result(tmp_path: Path) -> None:
    result = AnalysisResult(
        fat_models=[_fat("Order")],
        circular_imports=[_circ()],
    )
    write_baseline(result, str(tmp_path))
    keys = load_baseline(str(tmp_path))
    assert keys == _result_keys(result)


# ---------------------------------------------------------------------------
# new_findings
# ---------------------------------------------------------------------------

def test_new_findings_empty_baseline_returns_all(tmp_path: Path) -> None:
    result = AnalysisResult(fat_models=[_fat()])
    filtered = new_findings(result, set())
    assert len(filtered.fat_models) == 1


def test_new_findings_full_baseline_returns_none(tmp_path: Path) -> None:
    result = AnalysisResult(fat_models=[_fat()])
    keys = _result_keys(result)
    filtered = new_findings(result, keys)
    assert filtered.fat_models == []


def test_new_findings_partial_baseline(tmp_path: Path) -> None:
    order = _fat("Order")
    profile = _fat("Profile")
    result = AnalysisResult(fat_models=[order, profile])
    # Baseline only contains Order
    baseline = {_finding_key(order)}
    filtered = new_findings(result, baseline)
    assert len(filtered.fat_models) == 1
    assert filtered.fat_models[0].class_name == "Profile"


def test_new_findings_preserves_skipped_detectors() -> None:
    result = AnalysisResult(skipped_detectors=("direct_sql",))
    filtered = new_findings(result, set())
    assert filtered.skipped_detectors == ("direct_sql",)

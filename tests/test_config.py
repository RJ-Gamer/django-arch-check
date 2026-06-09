"""Tests for config file loading (config.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from django_arch_check.config import ArchConfig, load_config


def test_load_config_returns_defaults_when_no_file(tmp_path: Path) -> None:
    cfg = load_config(str(tmp_path))
    assert cfg == ArchConfig()
    assert cfg.fat_model_threshold == 15
    assert cfg.god_app_threshold == 30
    assert cfg.ignore == ()
    assert cfg.ignore_path == ()


def test_load_config_reads_pyproject_toml(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.django-arch-check]\n'
        'fat-model-threshold = 20\n'
        'god-app-threshold = 40\n'
        'ignore = ["direct_sql", "n_plus_one"]\n'
        'ignore-path = ["legacy/", "archive/"]\n',
        encoding="utf-8",
    )
    cfg = load_config(str(tmp_path))
    assert cfg.fat_model_threshold == 20
    assert cfg.god_app_threshold == 40
    assert cfg.ignore == ("direct_sql", "n_plus_one")
    assert cfg.ignore_path == ("legacy/", "archive/")


def test_load_config_reads_arch_check_toml(tmp_path: Path) -> None:
    (tmp_path / ".arch-check.toml").write_text(
        'fat-model-threshold = 25\n'
        'god-app-threshold = 50\n',
        encoding="utf-8",
    )
    cfg = load_config(str(tmp_path))
    assert cfg.fat_model_threshold == 25
    assert cfg.god_app_threshold == 50


def test_dotfile_takes_precedence_over_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.django-arch-check]\nfat-model-threshold = 10\n',
        encoding="utf-8",
    )
    (tmp_path / ".arch-check.toml").write_text(
        'fat-model-threshold = 99\n',
        encoding="utf-8",
    )
    cfg = load_config(str(tmp_path))
    assert cfg.fat_model_threshold == 99


def test_pyproject_without_tool_section_returns_defaults(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\n',
        encoding="utf-8",
    )
    cfg = load_config(str(tmp_path))
    assert cfg == ArchConfig()


def test_partial_config_fills_remaining_with_defaults(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.django-arch-check]\nfat-model-threshold = 5\n',
        encoding="utf-8",
    )
    cfg = load_config(str(tmp_path))
    assert cfg.fat_model_threshold == 5
    assert cfg.god_app_threshold == 30  # default
    assert cfg.ignore == ()


def test_empty_ignore_list(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.django-arch-check]\nignore = []\n',
        encoding="utf-8",
    )
    cfg = load_config(str(tmp_path))
    assert cfg.ignore == ()


def test_invalid_toml_raises_value_error(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.django-arch-check\nfat-model-threshold = 20\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Could not parse config file"):
        load_config(str(tmp_path))


def test_invalid_ignore_type_raises_value_error(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.django-arch-check]\nignore = "direct_sql"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected a list of strings"):
        load_config(str(tmp_path))

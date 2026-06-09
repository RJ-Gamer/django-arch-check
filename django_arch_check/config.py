"""Configuration file loader for django-arch-check."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            tomllib = None  # type: ignore[assignment]

_PYPROJECT = "pyproject.toml"
_DOTFILE = ".arch-check.toml"


@dataclass
class ArchConfig:
    """Resolved configuration from config file (if present)."""

    fat_model_threshold: int = 15
    god_app_threshold: int = 30
    ignore: tuple[str, ...] = field(default_factory=tuple)
    ignore_path: tuple[str, ...] = field(default_factory=tuple)


def load_config(project_path: str) -> ArchConfig:
    """Load config for *project_path*, returning defaults if none found."""
    root = Path(project_path)
    config_path, raw = _load_raw_config(root)
    if raw is None:
        return ArchConfig()

    return ArchConfig(
        fat_model_threshold=_read_int(
            raw,
            "fat-model-threshold",
            default=15,
            path=config_path,
        ),
        god_app_threshold=_read_int(
            raw,
            "god-app-threshold",
            default=30,
            path=config_path,
        ),
        ignore=_read_str_list(raw, "ignore", path=config_path),
        ignore_path=_read_str_list(raw, "ignore-path", path=config_path),
    )


def _load_raw_config(root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    """Return the config file path and raw config table, if one exists."""
    dotfile = root / _DOTFILE
    pyproject = root / _PYPROJECT

    if dotfile.is_file():
        return dotfile, _read_toml(dotfile)

    if not pyproject.is_file():
        return None, None

    data = _read_toml(pyproject)
    tool = data.get("tool")
    if tool is None:
        return None, None
    if not isinstance(tool, dict):
        raise ValueError(f"Invalid config file {pyproject}: [tool] must be a table.")

    config = tool.get("django-arch-check")
    if config is None:
        return None, None
    if not isinstance(config, dict):
        raise ValueError(
            f"Invalid config file {pyproject}: [tool.django-arch-check] must be a table."
        )
    return pyproject, config


def _read_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise ValueError(
            f"Cannot read config file {path}: tomllib is unavailable on this Python."
        )

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        raise ValueError(f"Could not parse config file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Invalid config file {path}: root document must be a table.")
    return data


def _read_int(
    raw: dict[str, Any],
    key: str,
    *,
    default: int,
    path: Path | None,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"Invalid config value for '{key}' in {path}: expected an integer."
        )
    return value


def _read_str_list(
    raw: dict[str, Any],
    key: str,
    *,
    path: Path | None,
) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(
            f"Invalid config value for '{key}' in {path}: expected a list of strings."
        )
    return tuple(value)

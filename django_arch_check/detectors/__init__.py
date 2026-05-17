"""Detector plugins for django-arch-check.

Each module in this package implements a specific architectural check.
All detectors will be discovered and invoked by the analyzer.
"""

from __future__ import annotations

import os
from collections.abc import MutableSequence, Sequence


def _normalise_path(path: str) -> str:
    """Return *path* with forward slashes for stable substring matching."""
    return path.replace("\\", "/")


def should_ignore_file(rel_path: str, ignore_paths: Sequence[str]) -> bool:
    """Return True if *rel_path* matches any user-supplied ignore substring."""
    normalised = _normalise_path(rel_path)
    return any(_normalise_path(ignored) in normalised for ignored in ignore_paths)


def should_ignore_dir(rel_path: str, ignore_paths: Sequence[str]) -> bool:
    """Return True if a directory path should be pruned from traversal."""
    normalised = _normalise_path(rel_path).strip("/")
    if not normalised or normalised == ".":
        return False
    return should_ignore_file(f"{normalised}/", ignore_paths)


def filter_dirnames(
    project_path: str,
    dirpath: str,
    dirnames: MutableSequence[str],
    skip_dirs: Sequence[str],
    ignore_paths: Sequence[str],
) -> None:
    """Prune skipped and ignored child directories from an ``os.walk`` step."""
    dirnames[:] = [
        dirname
        for dirname in dirnames
        if dirname not in skip_dirs
        and not should_ignore_dir(
            os.path.relpath(os.path.join(dirpath, dirname), project_path),
            ignore_paths,
        )
    ]

"""Shared pytest fixtures for django-arch-check tests."""

from __future__ import annotations

from pathlib import Path

import pytest


class ProjectBuilder:
    """Thin helper that writes files into a temp directory.

    Usage inside a test::

        def test_something(proj):
            proj.write("orders/__init__.py", "")
            proj.write("orders/models.py", "from django.db import models\\n...")
            findings = detect(proj.path)
    """

    def __init__(self, base: Path) -> None:
        self._base = base

    @property
    def path(self) -> str:
        """Absolute path to the project root as a plain string."""
        return str(self._base)

    def write(self, rel_path: str, content: str) -> "ProjectBuilder":
        """Write *content* to *rel_path* inside the project root.

        Creates parent directories automatically.
        Returns ``self`` so calls can be chained.
        """
        full = self._base / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return self


@pytest.fixture
def proj(tmp_path: Path) -> ProjectBuilder:
    """Return a fresh :class:`ProjectBuilder` rooted at a temporary directory."""
    return ProjectBuilder(tmp_path)

"""Tests for the file-change watcher (watcher.py)."""

from __future__ import annotations

import time
from pathlib import Path

from django_arch_check.watcher import _diff, _snapshot, watch


def test_snapshot_finds_py_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.txt").write_text("not python")
    snap = _snapshot(str(tmp_path))
    keys = [Path(k).name for k in snap]
    assert "a.py" in keys
    assert "b.txt" not in keys


def test_snapshot_skips_venv(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "lib.py").write_text("x = 1")
    (tmp_path / "real.py").write_text("x = 1")
    snap = _snapshot(str(tmp_path))
    keys = [Path(k).name for k in snap]
    assert "real.py" in keys
    assert "lib.py" not in keys


def test_snapshot_skips_pycache(tmp_path: Path) -> None:
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "mod.pyc").write_text("")
    (tmp_path / "mod.py").write_text("x = 1")
    snap = _snapshot(str(tmp_path))
    assert all("__pycache__" not in k for k in snap)


def test_diff_detects_new_file(tmp_path: Path) -> None:
    old: dict[str, float] = {}
    new = {str(tmp_path / "a.py"): 1.0}
    assert _diff(old, new) == [str(tmp_path / "a.py")]


def test_diff_detects_removed_file(tmp_path: Path) -> None:
    path = str(tmp_path / "a.py")
    old = {path: 1.0}
    new: dict[str, float] = {}
    assert _diff(old, new) == [path]


def test_diff_detects_modified_file(tmp_path: Path) -> None:
    path = str(tmp_path / "a.py")
    old = {path: 1.0}
    new = {path: 2.0}
    assert _diff(old, new) == [path]


def test_diff_no_change(tmp_path: Path) -> None:
    path = str(tmp_path / "a.py")
    snap = {path: 1.0}
    assert _diff(snap, snap) == []


def test_watch_fires_callback_on_change(tmp_path: Path) -> None:
    """Polling watcher fires callback after a file is modified."""
    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1")

    fired: list[bool] = []
    ticks = 0
    # Hard ceiling: stop after at most 30 poll cycles (~3s) regardless of
    # whether the callback fired, so the test never hangs.
    MAX_TICKS = 30

    def callback() -> None:
        fired.append(True)

    def stop() -> bool:
        nonlocal ticks
        ticks += 1
        return ticks >= MAX_TICKS or len(fired) >= 1

    import threading

    def _modify() -> None:
        time.sleep(0.2)
        py_file.write_text("x = 2")

    t = threading.Thread(target=_modify, daemon=True)
    t.start()

    watch(str(tmp_path), callback, poll_interval=0.1, debounce=0.1, stop=stop)
    t.join(timeout=3)

    assert len(fired) >= 1, "callback was never fired after file modification"


def test_watch_does_not_fire_without_change(tmp_path: Path) -> None:
    """Polling watcher does not fire if no files change."""
    (tmp_path / "mod.py").write_text("x = 1")

    fired: list[bool] = []
    ticks = 0

    def callback() -> None:
        fired.append(True)

    def stop() -> bool:
        nonlocal ticks
        ticks += 1
        return ticks >= 5  # stop after 5 poll cycles

    watch(str(tmp_path), callback, poll_interval=0.05, debounce=0.05, stop=stop)
    assert fired == []

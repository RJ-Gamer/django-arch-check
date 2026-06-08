"""File-change watcher for --watch mode.

Polls the project tree for .py file modifications and triggers a callback
whenever a change is detected. Uses watchdog when available, falls back to
pure-Python mtime polling so the feature works without any extra install.

Public API
----------
    watch(project_path, callback, poll_interval, debounce)

        project_path  – root directory to monitor
        callback      – called with no arguments after each debounced change
        poll_interval – seconds between mtime sweeps (polling fallback only)
        debounce      – seconds to wait after the last change before firing
"""

from __future__ import annotations

import os
import time
from typing import Callable

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", ".tox", ".venv", "venv", "env", ".env",
        "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache",
        ".pytest_cache", "htmlcov", "dist", "build", ".eggs",
    }
)


def _snapshot(project_path: str) -> dict[str, float]:
    """Return {filepath: mtime} for every .py file under *project_path*."""
    result: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            if name.endswith(".py"):
                full = os.path.join(dirpath, name)
                try:
                    result[full] = os.path.getmtime(full)
                except OSError:
                    pass
    return result


def _diff(old: dict[str, float], new: dict[str, float]) -> list[str]:
    """Return paths that were added, removed, or modified between snapshots."""
    changed: list[str] = []
    for path, mtime in new.items():
        if path not in old or old[path] != mtime:
            changed.append(path)
    for path in old:
        if path not in new:
            changed.append(path)
    return changed


def _watch_polling(
    project_path: str,
    callback: Callable[[], None],
    poll_interval: float,
    debounce: float,
    stop: Callable[[], bool],
) -> None:
    """Pure-Python polling watcher — no external dependencies."""
    current = _snapshot(project_path)
    pending_since: float | None = None

    while not stop():
        time.sleep(poll_interval)
        fresh = _snapshot(project_path)
        if _diff(current, fresh):
            current = fresh
            pending_since = time.monotonic()

        if pending_since is not None and (time.monotonic() - pending_since) >= debounce:
            pending_since = None
            callback()


def _watch_watchdog(
    project_path: str,
    callback: Callable[[], None],
    debounce: float,
    stop: Callable[[], bool],
) -> None:
    """watchdog-based watcher — lower latency than polling."""
    from watchdog.events import FileSystemEventHandler  # type: ignore[import]
    from watchdog.observers import Observer  # type: ignore[import]

    last_event: list[float] = [0.0]

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event: object) -> None:
            src = getattr(event, "src_path", "")
            if isinstance(src, str) and src.endswith(".py"):
                last_event[0] = time.monotonic()

    observer = Observer()
    observer.schedule(_Handler(), project_path, recursive=True)
    observer.start()
    try:
        fired_at: float = 0.0
        while not stop():
            time.sleep(0.1)
            t = last_event[0]
            if t > fired_at and (time.monotonic() - t) >= debounce:
                fired_at = time.monotonic()
                callback()
    finally:
        observer.stop()
        observer.join()


def watch(
    project_path: str,
    callback: Callable[[], None],
    poll_interval: float = 1.0,
    debounce: float = 0.5,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Watch *project_path* for .py changes and call *callback* on each.

    Blocks until *stop()* returns True (or KeyboardInterrupt).

    Args:
        project_path:  Root directory to monitor.
        callback:      Zero-argument callable fired after each debounced change.
        poll_interval: Seconds between mtime sweeps (polling fallback only).
        debounce:      Quiet period in seconds before firing after a change.
        stop:          Optional callable; watching stops when it returns True.
    """
    _stop: Callable[[], bool] = stop if stop is not None else (lambda: False)

    try:
        import watchdog  # noqa: F401  # type: ignore[import]
        _watch_watchdog(project_path, callback, debounce, _stop)
    except ImportError:
        _watch_polling(project_path, callback, poll_interval, debounce, _stop)

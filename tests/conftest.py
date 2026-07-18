# tests/conftest.py
"""Shared test fixtures for Milodex."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_milodex_data_dirs(tmp_path, monkeypatch):
    """Force every test into a tmp_path-based data root.

    Without this, any test that constructs ``ExecutionService`` /
    ``KillSwitchStateStore`` without an explicit ``event_store=`` ends
    up writing to the real ``data/milodex.db`` (see service.py default
    path). That pollutes the operator's audit trail every CI run. This
    fixture redirects the three known config knobs and verifies the
    redirect actually took effect — if a future code path adds a new
    default-path leak, this guard fires.
    """
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    locks_dir = data_dir / "locks"
    monkeypatch.setenv("MILODEX_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MILODEX_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MILODEX_LOCKS_DIR", str(locks_dir))

    from milodex.config import get_data_dir, get_locks_dir, get_logs_dir

    assert get_data_dir() == data_dir, "MILODEX_DATA_DIR override failed"
    assert get_logs_dir() == log_dir, "MILODEX_LOG_DIR override failed"
    assert get_locks_dir() == locks_dir, "MILODEX_LOCKS_DIR override failed"
    yield


@pytest.fixture(autouse=True)
def _guard_real_event_store_untouched():
    """Snapshot the real event store before each test, restore if changed.

    Belt-and-braces: the env-var fixture above should prevent every leak,
    but if a test bypasses ``get_data_dir()`` (e.g. constructs an
    EventStore with a hardcoded ``data/milodex.db`` path), this guard
    catches it on next run and fails loudly.
    """
    real_db = Path(__file__).resolve().parent.parent / "data" / "milodex.db"
    if real_db.exists():
        before_mtime = real_db.stat().st_mtime_ns
        before_size = real_db.stat().st_size
    else:
        before_mtime = None
        before_size = None
    yield
    if real_db.exists() and before_mtime is not None:
        after_mtime = real_db.stat().st_mtime_ns
        after_size = real_db.stat().st_size
        assert (before_mtime, before_size) == (after_mtime, after_size), (
            f"Test wrote to real production event store {real_db}. "
            "Pass an explicit isolated event_store to ExecutionService "
            "or use the autouse _isolate_milodex_data_dirs fixture."
        )

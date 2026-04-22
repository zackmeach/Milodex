"""Tests for the single-process advisory lock."""

from __future__ import annotations

import json
import os

import pytest

from milodex.core.advisory_lock import (
    AdvisoryLock,
    AdvisoryLockError,
    advisory_lock,
)


def test_advisory_lock_acquires_and_releases(tmp_path):
    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)

    holder = lock.acquire()

    assert lock.path.exists()
    assert holder.pid == os.getpid()
    assert holder.holder_name == "milodex"

    lock.release()
    assert not lock.path.exists()


def test_advisory_lock_blocks_second_acquire_from_same_process(tmp_path):
    first = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)
    second = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)

    first.acquire()
    try:
        with pytest.raises(AdvisoryLockError) as exc_info:
            second.acquire()
    finally:
        first.release()

    assert exc_info.value.holder is not None
    assert exc_info.value.holder.pid == os.getpid()
    assert "is held" in str(exc_info.value)


def test_advisory_lock_reclaims_stale_lockfile(tmp_path):
    lock_path = tmp_path / "milodex.runtime.lock"
    stale_pid = _pick_stale_pid()
    lock_path.write_text(
        json.dumps(
            {
                "pid": stale_pid,
                "hostname": "ghost",
                "holder_name": "milodex",
                "started_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)

    holder = lock.acquire()
    try:
        assert holder.pid == os.getpid()
    finally:
        lock.release()


def test_advisory_lock_context_manager_releases_on_exit(tmp_path):
    with advisory_lock("milodex.runtime", locks_dir=tmp_path) as holder:
        assert (tmp_path / "milodex.runtime.lock").exists()
        assert holder.pid == os.getpid()

    assert not (tmp_path / "milodex.runtime.lock").exists()


def test_advisory_lock_release_is_noop_when_not_held(tmp_path):
    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)

    # Should not raise even though acquire was never called.
    lock.release()
    assert not lock.path.exists()


def _pick_stale_pid() -> int:
    """Return a PID that almost certainly does not refer to a live process."""
    for candidate in (987654, 876543, 765432):
        try:
            os.kill(candidate, 0)
        except (ProcessLookupError, OSError):
            return candidate
    return 987654

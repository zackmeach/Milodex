"""Tests for the single-process advisory lock."""

from __future__ import annotations

import json
import os
import time

import pytest

from milodex.core.advisory_lock import (
    _STALE_LOCK_MAX_AGE_SECONDS,
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


def test_old_lockfile_with_live_recycled_pid_is_reclaimable_with_warning(tmp_path, caplog):
    """An ancient lock whose PID is now some unrelated live process is reclaimable.

    Recycled-PID hazard: the original Milodex holder died, the OS reused
    its PID for an unrelated long-lived process, and ``_process_exists``
    truthfully reports that PID alive — permanently blocking a legitimate
    restart. Fix: a lock older than the conservative age threshold is
    reclaimed (with a logged WARNING) even if its recorded PID is live,
    because no single trading session legitimately holds the lock that
    long. We record THIS process's own pid (guaranteed alive) and a very
    old mtime to stand in for the recycled-PID case.
    """
    lock_path = tmp_path / "milodex.runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),  # guaranteed-live, stands in for recycled
                "hostname": "ghost",
                "holder_name": "milodex",
                "started_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    old = time.time() - (_STALE_LOCK_MAX_AGE_SECONDS + 3600)
    os.utime(lock_path, (old, old))

    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)
    import logging

    with caplog.at_level(logging.WARNING, logger="milodex.core.advisory_lock"):
        holder = lock.acquire()
    try:
        assert holder.pid == os.getpid()
        assert any(
            "reclaim" in r.message.lower() or "stale" in r.message.lower() for r in caplog.records
        ), f"expected a stale-reclaim WARNING, got {caplog.records!r}"
    finally:
        lock.release()


def test_fresh_lockfile_with_live_owner_is_not_reclaimable(tmp_path):
    """A recently-written lock held by a live process must still block.

    The mtime fallback must NOT make a genuinely-held fresh lock
    stealable. Here the recorded PID is this live process and the lock
    file is brand new → acquisition must raise.
    """
    lock_path = tmp_path / "milodex.runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": "host",
                "holder_name": "milodex",
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    # mtime is "now" (just written); well within the freshness window.
    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)
    with pytest.raises(AdvisoryLockError) as exc_info:
        lock.acquire()
    assert exc_info.value.holder is not None
    assert exc_info.value.holder.pid == os.getpid()


def test_refreshed_lock_is_not_reclaimable_even_when_old_since_acquire(tmp_path):
    """A live holder that heartbeats keeps its lock unstealable indefinitely.

    Regression for the fix/event-store-integrity fail-open: the strategy
    runner's ``run()`` is an unbounded loop documented as safe to leave
    running all day/overnight, holding its per-strategy advisory lock for
    the whole lifetime. The age fallback alone would let a second
    invocation steal the lock from a still-working process once wall-clock
    since acquire exceeds the max age (no heartbeat → mtime never moves).
    ``refresh()`` makes the mtime a true liveness signal: a lock whose
    mtime is continuously refreshed must NOT be reclaimable no matter how
    long ago it was acquired. Simulate by acquiring, ageing the file far
    past the max age, calling ``refresh()``, then asserting a second
    acquire still blocks.
    """
    first = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)
    first.acquire()
    try:
        # Age the lock file well past the stale threshold (as if the
        # runner had been holding it for >12h of wall clock).
        old = time.time() - (_STALE_LOCK_MAX_AGE_SECONDS + 3600)
        os.utime(first.path, (old, old))

        # The live holder heartbeats: mtime moves back to "now".
        first.refresh()

        second = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)
        with pytest.raises(AdvisoryLockError) as exc_info:
            second.acquire()
        assert exc_info.value.holder is not None
        assert exc_info.value.holder.pid == os.getpid()
        assert "is held" in str(exc_info.value)
    finally:
        first.release()


def test_refresh_is_noop_when_lock_not_held(tmp_path):
    """``refresh()`` on an unheld lock must not raise or create the file."""
    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)

    lock.refresh()  # never acquired — must be a safe no-op

    assert not lock.path.exists()


def test_refresh_is_safe_to_call_repeatedly(tmp_path):
    """``refresh()`` must be idempotent and keep advancing the mtime."""
    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)
    lock.acquire()
    try:
        old = time.time() - (_STALE_LOCK_MAX_AGE_SECONDS + 3600)
        os.utime(lock.path, (old, old))
        for _ in range(3):
            lock.refresh()
        assert (time.time() - lock.path.stat().st_mtime) < _STALE_LOCK_MAX_AGE_SECONDS
    finally:
        lock.release()


def _pick_stale_pid() -> int:
    """Return a PID that almost certainly does not refer to a live process."""
    for candidate in (987654, 876543, 765432):
        try:
            os.kill(candidate, 0)
        except (ProcessLookupError, OSError):
            return candidate
    return 987654

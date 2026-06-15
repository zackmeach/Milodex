"""Tests for the single-process advisory lock."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.core.advisory_lock import (
    _STALE_LOCK_MAX_AGE_SECONDS,
    AdvisoryLock,
    AdvisoryLockError,
    LockHolder,
    advisory_lock,
    holder_is_live,
    live_lock_holder,
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


def test_acquire_blocking_returns_immediately_when_free(tmp_path):
    lock = AdvisoryLock("submit.paper", locks_dir=tmp_path)

    holder = lock.acquire_blocking(timeout_seconds=1.0)
    try:
        assert lock.path.exists()
        assert holder.pid == os.getpid()
    finally:
        lock.release()


def test_acquire_blocking_times_out_when_held(tmp_path):
    holder_lock = AdvisoryLock("submit.paper", locks_dir=tmp_path)
    waiter = AdvisoryLock("submit.paper", locks_dir=tmp_path)

    holder_lock.acquire()
    try:
        start = time.monotonic()
        with pytest.raises(AdvisoryLockError) as exc_info:
            waiter.acquire_blocking(timeout_seconds=0.2, poll_interval_seconds=0.02)
        elapsed = time.monotonic() - start
    finally:
        holder_lock.release()

    assert elapsed >= 0.2
    assert "within" in str(exc_info.value)
    # Fail-closed: the waiter never acquired, so no lock file leaks under it.
    assert not waiter._held


def test_acquire_blocking_succeeds_after_holder_releases(tmp_path):
    holder_lock = AdvisoryLock("submit.paper", locks_dir=tmp_path)
    waiter = AdvisoryLock("submit.paper", locks_dir=tmp_path)

    holder_lock.acquire()
    released = threading.Event()

    def _release_after_delay() -> None:
        time.sleep(0.15)
        holder_lock.release()
        released.set()

    releaser = threading.Thread(target=_release_after_delay)
    releaser.start()
    try:
        holder = waiter.acquire_blocking(timeout_seconds=3.0, poll_interval_seconds=0.02)
        assert released.is_set()
        assert holder.pid == os.getpid()
        assert waiter.path.exists()
    finally:
        waiter.release()
        releaser.join()


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


def test_current_holder_returns_none_when_no_lock_exists(tmp_path):
    """current_holder() returns None when no lock file is present."""
    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)

    assert lock.current_holder() is None


def test_current_holder_returns_holder_when_lock_is_held(tmp_path):
    """current_holder() returns the live holder when the lock file exists."""
    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)
    lock.acquire()
    try:
        holder = lock.current_holder()
        assert holder is not None
        assert holder.pid == os.getpid()
    finally:
        lock.release()


def test_current_holder_does_not_create_lock_file(tmp_path):
    """current_holder() on an unheld lock must not create or modify the lock file."""
    lock = AdvisoryLock("milodex.runtime", locks_dir=tmp_path)

    lock.current_holder()

    assert not lock.path.exists()


def _pick_stale_pid() -> int:
    """Return a PID that almost certainly does not refer to a live process."""
    for candidate in (987654, 876543, 765432):
        try:
            os.kill(candidate, 0)
        except (ProcessLookupError, OSError):
            return candidate
    return 987654


# ---------------------------------------------------------------------------
# Shared identity-verified liveness helper — holder_is_live / live_lock_holder
#
# This is the single shared answer to "is this advisory-lock holder a genuinely
# live process?" consolidated from three prior implementations (orphan
# reconcile, paper-runner control, bench peek). It must preserve the strongest
# pre-consolidation semantics: PID-existence AND process-start-time identity,
# with a loud degrade when start-time introspection is unavailable.
# ---------------------------------------------------------------------------


def _holder(pid: int, started_at: datetime) -> LockHolder:
    """Build a LockHolder with an arbitrary pid/started_at for liveness tests."""
    return LockHolder(
        pid=pid,
        hostname="test-host",
        holder_name="milodex test",
        started_at=started_at,
        path=Path("milodex.runtime.strategy.x.lock"),
    )


def test_holder_is_live_false_for_none() -> None:
    assert holder_is_live(None) is False


def test_holder_is_live_false_for_dead_pid() -> None:
    # pid=0 short-circuits _process_exists to False; no start-time probe needed.
    assert holder_is_live(_holder(0, datetime(2026, 1, 1, tzinfo=UTC))) is False


def test_holder_is_live_true_for_current_process() -> None:
    # This live process started before "now", so a lock recorded "now" passes
    # the start-time identity check (proc_start <= started_at + grace).
    assert holder_is_live(_holder(os.getpid(), datetime.now(tz=UTC))) is True


def test_holder_is_live_false_for_recycled_pid() -> None:
    # Live PID but a started_at anchored to the Unix epoch: the owning process
    # necessarily started *after* the lock was written — the recycled-PID
    # signature — so identity-verified liveness must classify it dead even
    # though bare PID-existence returns True.
    recycled = _holder(os.getpid(), datetime.fromtimestamp(0, tz=UTC))
    assert holder_is_live(recycled) is False


def test_holder_is_live_falls_back_to_pid_existence_without_start_time(monkeypatch, caplog) -> None:
    """When the platform cannot report a process start time, degrade to bare
    PID-existence — loudly. Matches the strongest pre-consolidation behavior."""
    import logging
    import sys

    # core/__init__ re-exports the ``advisory_lock`` context manager, which
    # shadows the submodule under attribute access — so BOTH ``import ... as``
    # and monkeypatch's dotted-path string form resolve to the *function*, not
    # the module. Patch the real module object from sys.modules, which is the
    # namespace ``holder_is_live`` looks ``_process_start_time`` up in at call time.
    monkeypatch.setattr(
        sys.modules["milodex.core.advisory_lock"], "_process_start_time", lambda pid: None
    )
    recycled = _holder(os.getpid(), datetime.fromtimestamp(0, tz=UTC))
    with caplog.at_level(logging.WARNING, logger="milodex.core.advisory_lock"):
        assert holder_is_live(recycled) is True
    assert any("introspection unavailable" in r.message for r in caplog.records), (
        f"expected a degraded-liveness WARNING, got {caplog.records!r}"
    )


def test_live_lock_holder_returns_none_when_no_lock(tmp_path) -> None:
    lock = AdvisoryLock("milodex.runtime.strategy.x", locks_dir=tmp_path)
    assert live_lock_holder(lock) is None


def test_live_lock_holder_returns_holder_when_live(tmp_path) -> None:
    lock = AdvisoryLock("milodex.runtime.strategy.x", locks_dir=tmp_path)
    lock.acquire()
    try:
        holder = live_lock_holder(lock)
        assert holder is not None
        assert holder.pid == os.getpid()
    finally:
        lock.release()


def test_live_lock_holder_returns_none_for_recycled_pid(tmp_path) -> None:
    # Plant a lock whose recorded started_at predates the live PID that owns it
    # (recycled-PID signature) and assert the shared helper reports it dead.
    lock_path = tmp_path / "milodex.runtime.strategy.x.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": "ghost",
                "holder_name": "milodex",
                "started_at": datetime.fromtimestamp(0, tz=UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    lock = AdvisoryLock("milodex.runtime.strategy.x", locks_dir=tmp_path)
    assert live_lock_holder(lock) is None

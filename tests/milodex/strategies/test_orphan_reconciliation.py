"""Tests for GUI-bootstrap orphaned strategy-run reconciliation."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from milodex.core.advisory_lock import AdvisoryLock
from milodex.core.event_store import EventStore, StrategyRunEvent
from milodex.strategies.orphan_reconciliation import reconcile_orphaned_runs_on_bootstrap
from milodex.strategies.paper_runner_control import runner_lock_name


def _open_run(store: EventStore, *, session_id: str, strategy_id: str) -> None:
    store.append_strategy_run(
        StrategyRunEvent(
            session_id=session_id,
            strategy_id=strategy_id,
            started_at=datetime(2026, 5, 18, tzinfo=UTC),
            ended_at=None,
            exit_reason=None,
            metadata={"mode": "paper"},
        )
    )


def _write_lock(
    locks_dir: Path,
    *,
    strategy_id: str,
    pid: int,
    started_at: datetime,
    holder_name: str = "milodex test",
    hostname: str = "test-host",
) -> Path:
    """Write a synthetic advisory-lock file (bypasses AdvisoryLock.acquire).

    Used to plant locks with arbitrary pid/started_at combinations that the
    real acquire path would never produce on a single host — specifically,
    the recycled-PID signature where the recorded ``started_at`` predates
    the process that currently owns the PID.
    """
    locks_dir.mkdir(parents=True, exist_ok=True)
    path = locks_dir / f"{runner_lock_name(strategy_id)}.lock"
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "hostname": hostname,
                "holder_name": holder_name,
                "started_at": started_at.isoformat(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def test_reconciles_open_run_with_no_live_runner(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    locks_dir = tmp_path / "locks"
    _open_run(store, session_id="s-1", strategy_id="strat.x.v1")
    now = datetime(2026, 5, 18, 20, 0, tzinfo=UTC)

    closed = reconcile_orphaned_runs_on_bootstrap(store, locks_dir, now=now)

    assert closed == ["strat.x.v1"]
    run = store.list_strategy_runs()[0]
    assert run.ended_at is not None
    assert run.exit_reason == "orphaned_no_live_runner"


def test_leaves_open_run_with_live_lock_holder(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    locks_dir = tmp_path / "locks"
    _open_run(store, session_id="s-1", strategy_id="strat.x.v1")
    lock = AdvisoryLock(runner_lock_name("strat.x.v1"), locks_dir=locks_dir)
    lock.acquire()
    try:
        now = datetime(2026, 5, 18, 20, 0, tzinfo=UTC)

        closed = reconcile_orphaned_runs_on_bootstrap(store, locks_dir, now=now)

        assert closed == []
        assert store.list_strategy_runs()[0].ended_at is None
    finally:
        lock.release()


def test_no_open_runs_returns_empty(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    locks_dir = tmp_path / "locks"
    now = datetime(2026, 5, 18, 20, 0, tzinfo=UTC)

    assert reconcile_orphaned_runs_on_bootstrap(store, locks_dir, now=now) == []


def test_reclassifies_recycled_pid_as_dead(tmp_path: Path) -> None:
    """A lock whose PID resolves to a live process but whose ``started_at`` predates
    that process is the recycled-PID signature (typical of a post-reboot phantom).

    A real surviving process must have been started before its own lock was
    written; a process that was assigned a recycled PID by the OS was started
    *after* the lock. Reconcile must treat the latter as dead and close the
    open run row, even though the bare PID-existence check returns True.
    """
    store = EventStore(tmp_path / "milodex.db")
    locks_dir = tmp_path / "locks"
    strategy_id = "strat.recycled.v1"
    _open_run(store, session_id="s-recycled", strategy_id=strategy_id)
    # Plant a lock whose ``started_at`` predates any possible process start
    # (Unix epoch). Combined with our live pytest PID, this is the recycled-
    # PID signature: a live process whose start time is necessarily later
    # than the lock's recorded ``started_at``. Anchoring to the epoch rather
    # than ``now - <delta>`` makes the test independent of pytest's own age,
    # so long-lived test workers / IDE daemons cannot accidentally pass the
    # "live process started before its own lock" check.
    _write_lock(
        locks_dir,
        strategy_id=strategy_id,
        pid=os.getpid(),
        started_at=datetime.fromtimestamp(0, tz=UTC),
    )
    now = datetime.now(tz=UTC)

    closed = reconcile_orphaned_runs_on_bootstrap(store, locks_dir, now=now)

    assert closed == [strategy_id]
    assert store.list_strategy_runs()[0].ended_at is not None
    assert store.list_strategy_runs()[0].exit_reason == "orphaned_no_live_runner"


def test_unlinks_stale_lock_after_closing_run(tmp_path: Path) -> None:
    """After reconcile closes an orphan run row, its stale advisory-lock file
    must be removed from disk.

    Without this, the runtime can show divergent state — the strategy_runs
    row is closed but the lock persists, blocking the next launch attempt
    until AdvisoryLock.acquire's own staleness fallback fires.
    """
    store = EventStore(tmp_path / "milodex.db")
    locks_dir = tmp_path / "locks"
    strategy_id = "strat.dead.v1"
    _open_run(store, session_id="s-dead", strategy_id=strategy_id)
    # pid=0 is the canonical "definitely-not-alive" value: _process_exists
    # short-circuits to False for any non-positive pid.
    lock_path = _write_lock(
        locks_dir,
        strategy_id=strategy_id,
        pid=0,
        started_at=datetime(2026, 5, 18, tzinfo=UTC),
    )
    assert lock_path.exists()
    now = datetime(2026, 5, 19, tzinfo=UTC)

    closed = reconcile_orphaned_runs_on_bootstrap(store, locks_dir, now=now)

    assert closed == [strategy_id]
    assert store.list_strategy_runs()[0].ended_at is not None
    assert not lock_path.exists(), "stale lock should be unlinked after reconcile"


def test_mixed_cohort_post_host_reset(tmp_path: Path) -> None:
    """End-to-end shape of the 2026-05-19 host-reset scenario.

    Six open strategy_runs rows; six lock files on disk. Of those:
      - three locks reference truly-dead PIDs (cleanly orphaned),
      - two reference recycled PIDs (live process, lock predates process),
      - one is a real held lock (live runner that survived).

    Reconcile must close the five non-live rows, unlink the five stale
    locks, and leave the one live row + lock untouched. This is the
    fixture that today's production defect failed against: the recycled
    pair stayed phantom and all six locks survived.
    """
    store = EventStore(tmp_path / "milodex.db")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)

    # Three truly-dead strategies (pid=0).
    dead_ids = [f"strat.dead{i}.v1" for i in range(3)]
    for i, sid in enumerate(dead_ids):
        _open_run(store, session_id=f"s-dead-{i}", strategy_id=sid)
        _write_lock(
            locks_dir,
            strategy_id=sid,
            pid=0,
            started_at=datetime(2026, 5, 18, tzinfo=UTC),
        )

    # Two recycled-PID strategies: live pytest PID, lock anchored to the
    # Unix epoch (predates any possible process start — see
    # test_reclassifies_recycled_pid_as_dead for the rationale).
    recycled_ids = [f"strat.recycled{i}.v1" for i in range(2)]
    for i, sid in enumerate(recycled_ids):
        _open_run(store, session_id=f"s-recycled-{i}", strategy_id=sid)
        _write_lock(
            locks_dir,
            strategy_id=sid,
            pid=os.getpid(),
            started_at=datetime.fromtimestamp(0, tz=UTC),
        )

    # One genuinely-live strategy: hold the lock via the real acquire path.
    live_id = "strat.zzz_live.v1"  # 'zzz' so sort order is predictable
    _open_run(store, session_id="s-live", strategy_id=live_id)
    live_lock = AdvisoryLock(runner_lock_name(live_id), locks_dir=locks_dir)
    live_lock.acquire()
    try:
        now = datetime.now(tz=UTC)

        closed = reconcile_orphaned_runs_on_bootstrap(store, locks_dir, now=now)

        # Five non-live strategies closed, in sorted order.
        assert closed == sorted([*dead_ids, *recycled_ids])

        # Their lock files are gone.
        for sid in [*dead_ids, *recycled_ids]:
            assert not (
                locks_dir / f"{runner_lock_name(sid)}.lock"
            ).exists(), f"stale lock for {sid} should be unlinked"

        # The live strategy's row is untouched and its lock is still on disk.
        live_run = next(
            r for r in store.list_strategy_runs() if r.strategy_id == live_id
        )
        assert live_run.ended_at is None
        assert (locks_dir / f"{runner_lock_name(live_id)}.lock").exists()

        # All non-live run rows are closed with the expected exit reason.
        for sid in [*dead_ids, *recycled_ids]:
            row = next(r for r in store.list_strategy_runs() if r.strategy_id == sid)
            assert row.ended_at is not None
            assert row.exit_reason == "orphaned_no_live_runner"
    finally:
        live_lock.release()

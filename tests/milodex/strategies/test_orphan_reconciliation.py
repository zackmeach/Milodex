"""Tests for GUI-bootstrap orphaned strategy-run reconciliation."""

from __future__ import annotations

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

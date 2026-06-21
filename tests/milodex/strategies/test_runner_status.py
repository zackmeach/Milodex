"""Tests for :mod:`milodex.strategies.runner_status`.

The runner-status module is the single GUI-free owner of "what is this
runner doing right now": the 4-state liveness resolver (moved from
``milodex.gui._event_queries``), the lock-mtime heartbeat label, and the
per-strategy status snapshot consumed by ``milodex strategy status``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from milodex.core.advisory_lock import AdvisoryLock
from milodex.core.event_store import EventStore, ExplanationEvent, StrategyRunEvent
from milodex.strategies.paper_runner_control import (
    controlled_stop_request_path,
    runner_lock_name,
)
from milodex.strategies.runner_status import (
    collect_runner_statuses,
    heartbeat_label,
    resolve_runner_liveness,
    runner_lock_holder,
)

STRATEGY_ID = "meanrev.daily.pullback_rsi2.test.v1"


def _write_minimal_strategy_config(
    config_dir: Path,
    *,
    strategy_id: str = STRATEGY_ID,
    variant: str = "test",
    bar_size: str = "1D",
) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"{strategy_id.replace('.', '_')}.yaml"
    path.write_text(
        f"""
strategy:
  id: "{strategy_id}"
  family: "meanrev"
  template: "daily.pullback_rsi2"
  variant: "{variant}"
  version: 1
  description: "Runner status test"
  enabled: true
  universe:
    - "SPY"
  parameters:
    rsi_lookback: 2
    rsi_entry_threshold: 10
    rsi_exit_threshold: 50
    ma_filter_length: 200
    stop_loss_pct: 0.05
    max_hold_days: 5
    max_concurrent_positions: 1
    sizing_rule: "equal_notional"
    per_position_notional_pct: 0.10
    ranking_enabled: false
    ranking_metric: "rsi_ascending"
    market_regime_symbol: ""
    market_regime_ma_length: 200
  tempo:
    bar_size: "{bar_size}"
    position_lifecycle: "same_session"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "paper"
  backtest:
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional: []
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _append_run(
    store: EventStore,
    *,
    strategy_id: str = STRATEGY_ID,
    session_id: str = "session-1",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    exit_reason: str | None = None,
) -> None:
    store.append_strategy_run(
        StrategyRunEvent(
            session_id=session_id,
            strategy_id=strategy_id,
            started_at=started_at or datetime.now(tz=UTC) - timedelta(hours=1),
            ended_at=ended_at,
            exit_reason=exit_reason,
            metadata={},
        )
    )


def _append_explanation(
    store: EventStore,
    *,
    session_id: str,
    recorded_at: datetime,
) -> None:
    store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type="submit",
            status="submitted",
            strategy_name=STRATEGY_ID,
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash=None,
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=None,
            latest_bar_close=None,
            account_equity=0.0,
            account_cash=0.0,
            account_portfolio_value=0.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id=session_id,
        )
    )


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "data" / "milodex.db")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "configs"
    _write_minimal_strategy_config(config_dir)
    return config_dir


@pytest.fixture
def locks_dir(tmp_path: Path) -> Path:
    locks = tmp_path / "locks"
    locks.mkdir()
    return locks


def _status_for(statuses: list[dict], strategy_id: str = STRATEGY_ID) -> dict:
    by_id = {s["strategy_id"]: s for s in statuses}
    assert strategy_id in by_id, f"no status entry for {strategy_id}: {by_id.keys()}"
    return by_id[strategy_id]


# ---------------------------------------------------------------------------
# collect_runner_statuses — liveness states
# ---------------------------------------------------------------------------


def test_running_state_with_live_lock(store, config_dir, locks_dir):
    _append_run(store)
    lock = AdvisoryLock(
        runner_lock_name(STRATEGY_ID),
        locks_dir=locks_dir,
        holder_name=f"milodex strategy run {STRATEGY_ID}",
    )
    lock.acquire()
    try:
        statuses = collect_runner_statuses(store, config_dir=config_dir, locks_dir=locks_dir)
    finally:
        lock.release()

    entry = _status_for(statuses)
    assert entry["state"] == "running"
    assert entry["session_id"] == "session-1"
    assert entry["holder_pid"] == os.getpid()
    assert entry["heartbeat"] == "on schedule"
    assert entry["stop_requested"] is False


def test_phantom_when_open_row_and_no_lock(store, config_dir, locks_dir):
    _append_run(store)

    statuses = collect_runner_statuses(store, config_dir=config_dir, locks_dir=locks_dir)

    entry = _status_for(statuses)
    assert entry["state"] == "phantom"
    assert entry["holder_pid"] is None
    assert entry["heartbeat"] == "no activity"
    assert "reap-orphans" in (entry["note"] or "")


def test_stopped_and_failed_states(store, config_dir, locks_dir):
    now = datetime.now(tz=UTC)
    _append_run(
        store,
        session_id="session-stopped",
        ended_at=now,
        exit_reason="controlled_stop",
    )
    other_id = "meanrev.daily.pullback_rsi2.other.v1"
    _write_minimal_strategy_config(config_dir, strategy_id=other_id, variant="other")
    _append_run(
        store,
        strategy_id=other_id,
        session_id="session-crashed",
        ended_at=now,
        exit_reason="crashed:RuntimeError('boom')",
    )

    statuses = collect_runner_statuses(store, config_dir=config_dir, locks_dir=locks_dir)

    assert _status_for(statuses)["state"] == "stopped"
    assert _status_for(statuses, other_id)["state"] == "failed"
    assert _status_for(statuses, other_id)["exit_reason"].startswith("crashed:")


def test_reaper_closed_session_classifies_failed(store, config_dir, locks_dir):
    """Both orphan closures are failures: 'orphan_recovered' (runner startup
    self-reconcile) and 'orphaned_no_live_runner' (GUI reaper)."""
    now = datetime.now(tz=UTC)
    _append_run(
        store,
        session_id="session-reaped",
        ended_at=now,
        exit_reason="orphaned_no_live_runner",
    )
    other_id = "meanrev.daily.pullback_rsi2.other.v1"
    _write_minimal_strategy_config(config_dir, strategy_id=other_id, variant="other")
    _append_run(
        store,
        strategy_id=other_id,
        session_id="session-self-reconciled",
        ended_at=now,
        exit_reason="orphan_recovered",
    )

    statuses = collect_runner_statuses(store, config_dir=config_dir, locks_dir=locks_dir)

    assert _status_for(statuses)["state"] == "failed"
    assert _status_for(statuses, other_id)["state"] == "failed"


def test_latest_session_wins(store, config_dir, locks_dir):
    old = datetime.now(tz=UTC) - timedelta(days=2)
    _append_run(
        store,
        session_id="session-old",
        started_at=old,
        ended_at=old + timedelta(hours=4),
        exit_reason="controlled_stop",
    )
    _append_run(store, session_id="session-new", ended_at=None)

    statuses = collect_runner_statuses(store, config_dir=config_dir, locks_dir=locks_dir)

    entry = _status_for(statuses)
    assert entry["session_id"] == "session-new"
    assert len(statuses) == 1


# ---------------------------------------------------------------------------
# collect_runner_statuses — last eval, stop sentinel, notes, filtering
# ---------------------------------------------------------------------------


def test_reports_last_eval_and_stop_request(store, config_dir, locks_dir):
    _append_run(store)
    first = datetime(2026, 6, 9, 20, 1, 0, tzinfo=UTC)
    second = datetime(2026, 6, 9, 20, 5, 0, tzinfo=UTC)
    _append_explanation(store, session_id="session-1", recorded_at=first)
    _append_explanation(store, session_id="session-1", recorded_at=second)
    sentinel = controlled_stop_request_path(locks_dir, STRATEGY_ID)
    sentinel.write_text(json.dumps({"mode": "controlled"}), encoding="utf-8")

    statuses = collect_runner_statuses(store, config_dir=config_dir, locks_dir=locks_dir)

    entry = _status_for(statuses)
    assert entry["last_eval_at"] is not None
    assert "20:05" in entry["last_eval_at"] or "T20:05" in entry["last_eval_at"]
    assert entry["stop_requested"] is True


def test_daily_running_idle_note(store, config_dir, locks_dir):
    _append_run(store)
    lock = AdvisoryLock(runner_lock_name(STRATEGY_ID), locks_dir=locks_dir)
    lock.acquire()
    try:
        statuses = collect_runner_statuses(store, config_dir=config_dir, locks_dir=locks_dir)
    finally:
        lock.release()

    entry = _status_for(statuses)
    assert entry["bar_size"] == "1D"
    assert "market close" in (entry["note"] or "")
    assert "by design" in (entry["note"] or "")


def test_intraday_running_has_no_idle_note(store, tmp_path, locks_dir):
    config_dir = tmp_path / "configs_intraday"
    _write_minimal_strategy_config(config_dir, bar_size="5Min")
    _append_run(store)
    lock = AdvisoryLock(runner_lock_name(STRATEGY_ID), locks_dir=locks_dir)
    lock.acquire()
    try:
        statuses = collect_runner_statuses(store, config_dir=config_dir, locks_dir=locks_dir)
    finally:
        lock.release()

    entry = _status_for(statuses)
    assert entry["bar_size"] == "5Min"
    assert entry["note"] is None


def test_filters_to_requested_strategy(store, config_dir, locks_dir):
    other_id = "meanrev.daily.pullback_rsi2.other.v1"
    _write_minimal_strategy_config(config_dir, strategy_id=other_id, variant="other")
    _append_run(store)
    _append_run(store, strategy_id=other_id, session_id="session-other")

    statuses = collect_runner_statuses(
        store, config_dir=config_dir, locks_dir=locks_dir, strategy_id=other_id
    )

    assert [s["strategy_id"] for s in statuses] == [other_id]


def test_never_ran_for_known_config(store, config_dir, locks_dir):
    statuses = collect_runner_statuses(
        store, config_dir=config_dir, locks_dir=locks_dir, strategy_id=STRATEGY_ID
    )

    entry = _status_for(statuses)
    assert entry["state"] == "never_ran"
    assert entry["session_id"] is None


def test_unknown_strategy_raises(store, config_dir, locks_dir):
    with pytest.raises(ValueError, match="not found"):
        collect_runner_statuses(
            store,
            config_dir=config_dir,
            locks_dir=locks_dir,
            strategy_id="nope.daily.missing.x.v1",
        )


# ---------------------------------------------------------------------------
# EventStore.latest_explanation_recorded_at
# ---------------------------------------------------------------------------


def test_latest_explanation_recorded_at(store):
    _append_run(store)
    assert store.latest_explanation_recorded_at("session-1") is None
    _append_explanation(
        store, session_id="session-1", recorded_at=datetime(2026, 6, 9, 20, 1, tzinfo=UTC)
    )
    _append_explanation(
        store, session_id="session-1", recorded_at=datetime(2026, 6, 9, 20, 6, tzinfo=UTC)
    )

    latest = store.latest_explanation_recorded_at("session-1")

    assert latest is not None
    assert "20:06" in latest
    assert store.latest_explanation_recorded_at("session-none") is None


# ---------------------------------------------------------------------------
# Moved helpers — direct behaviour + GUI re-export back-compat
# ---------------------------------------------------------------------------


def test_resolve_runner_liveness_states():
    assert resolve_runner_liveness(ended_at=None, exit_reason=None, lock_live=True) == "running"
    assert resolve_runner_liveness(ended_at=None, exit_reason=None, lock_live=False) == "phantom"
    assert (
        resolve_runner_liveness(
            ended_at="2026-06-09", exit_reason="controlled_stop", lock_live=False
        )
        == "stopped"
    )
    assert (
        resolve_runner_liveness(ended_at="2026-06-09", exit_reason="crashed: x", lock_live=False)
        == "failed"
    )


def test_heartbeat_label_classification():
    assert heartbeat_label(None, 60) == "no activity"
    assert heartbeat_label(30.0, 60) == "on schedule"
    assert heartbeat_label(121.0, 60) == "overdue by 2m"
    assert heartbeat_label(25.0, 10) == "overdue by 25s"


def test_runner_lock_holder_returns_none_without_lock(locks_dir):
    assert runner_lock_holder(STRATEGY_ID, locks_dir) is None


def test_gui_event_queries_reexports_runner_status():
    from milodex.gui import _event_queries
    from milodex.strategies import runner_status

    assert _event_queries.resolve_runner_liveness is runner_status.resolve_runner_liveness
    assert _event_queries.runner_lock_live is runner_status.runner_lock_live
    assert _event_queries.runner_lock_mtime_age is runner_status.runner_lock_mtime_age

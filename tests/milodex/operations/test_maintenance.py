"""Tests for event-store compaction (safe tier).

Prune only cascade-safe backtest explanations (backtest_run_id set, no linked
trade). Live rows, backtest trades, and backtest-with-trade explanations must
survive. See src/milodex/operations/maintenance.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from milodex.core.event_store import (
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    TradeEvent,
)
from milodex.operations.maintenance import plan_compaction, run_compaction

_TS = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)


def _seed_backtest_run(store: EventStore, run_id: str = "run-1") -> int:
    return store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id="s.v1",
            config_path="configs/s.yaml",
            config_hash="h",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 12, 31, tzinfo=UTC),
            started_at=_TS,
            status="completed",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={},
        )
    )


def _backtest_explanation(store: EventStore, run_db_id: int, *, with_trade: bool) -> int:
    eid = store.append_explanation(
        ExplanationEvent(
            recorded_at=_TS,
            decision_type="submit" if with_trade else "no_trade",
            status="submitted" if with_trade else "no_signal",
            strategy_name="s.v1",
            strategy_stage="backtest",
            strategy_config_path="configs/s.yaml",
            config_hash="h",
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="backtest_engine",
            market_open=True,
            latest_bar_timestamp=_TS,
            latest_bar_close=400.0,
            account_equity=10_000.0,
            account_cash=10_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="ok",
            reason_codes=[],
            risk_checks=[],
            context={},
            backtest_run_id=run_db_id,
        )
    )
    if with_trade:
        store.append_trade(
            TradeEvent(
                explanation_id=eid,
                recorded_at=_TS,
                status="filled",
                source="backtest",
                symbol="SPY",
                side="buy",
                quantity=1.0,
                order_type="market",
                time_in_force="day",
                estimated_unit_price=400.0,
                estimated_order_value=400.0,
                strategy_name="s.v1",
                strategy_stage="backtest",
                strategy_config_path="configs/s.yaml",
                submitted_by="backtest_engine",
                broker_order_id=None,
                broker_status=None,
                message=None,
                backtest_run_id=run_db_id,
            )
        )
    return eid


def _live_explanation(store: EventStore) -> int:
    return store.append_explanation(
        ExplanationEvent(
            recorded_at=_TS,
            decision_type="no_trade",
            status="no_signal",
            strategy_name="s.v1",
            strategy_stage="paper",
            strategy_config_path="configs/s.yaml",
            config_hash="h",
            symbol="SPY",
            side="buy",
            quantity=0.0,
            order_type="market",
            time_in_force="day",
            submitted_by="operator",
            market_open=True,
            latest_bar_timestamp=_TS,
            latest_bar_close=400.0,
            account_equity=10_000.0,
            account_cash=10_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="ok",
            reason_codes=[],
            risk_checks=[],
            context={},
        )
    )


def _explanations_count(store: EventStore) -> int:
    return len(store.list_explanations())


def test_plan_counts_only_cascade_safe_backtest_explanations(tmp_path):
    store = EventStore(tmp_path / "m.db")
    run = _seed_backtest_run(store)
    _backtest_explanation(store, run, with_trade=False)  # prunable
    _backtest_explanation(store, run, with_trade=False)  # prunable
    _backtest_explanation(store, run, with_trade=True)  # has trade -> NOT prunable
    _live_explanation(store)  # live -> NOT prunable
    assert plan_compaction(store).prunable_explanations == 2


def test_compaction_deletes_no_trade_backtest_explanations(tmp_path):
    store = EventStore(tmp_path / "m.db")
    run = _seed_backtest_run(store)
    _backtest_explanation(store, run, with_trade=False)
    _backtest_explanation(store, run, with_trade=False)
    before = _explanations_count(store)
    result = run_compaction(store, make_backup=False, vacuum=False)
    assert result.pruned_explanations == 2
    assert _explanations_count(store) == before - 2
    assert plan_compaction(store).prunable_explanations == 0


def test_compaction_never_touches_live_explanations(tmp_path):
    store = EventStore(tmp_path / "m.db")
    run = _seed_backtest_run(store)
    _backtest_explanation(store, run, with_trade=False)
    live = _live_explanation(store)
    run_compaction(store, make_backup=False, vacuum=False)
    ids = {e.id for e in store.list_explanations()}
    assert live in ids


def test_compaction_preserves_linked_backtest_trades(tmp_path):
    store = EventStore(tmp_path / "m.db")
    run = _seed_backtest_run(store)
    kept = _backtest_explanation(store, run, with_trade=True)
    _backtest_explanation(store, run, with_trade=False)
    run_compaction(store, make_backup=False, vacuum=False)
    ids = {e.id for e in store.list_explanations()}
    assert kept in ids  # explanation with a linked trade survives
    assert len(store.list_trades()) == 1  # the backtest trade survives


def test_plan_is_read_only(tmp_path):
    store = EventStore(tmp_path / "m.db")
    run = _seed_backtest_run(store)
    _backtest_explanation(store, run, with_trade=False)
    before = _explanations_count(store)
    plan_compaction(store)
    plan_compaction(store)
    assert _explanations_count(store) == before


def test_backup_created_before_delete(tmp_path):
    store = EventStore(tmp_path / "m.db")
    run = _seed_backtest_run(store)
    _backtest_explanation(store, run, with_trade=False)
    result = run_compaction(store, make_backup=True, vacuum=False)
    assert result.backup_path is not None
    assert result.backup_path.exists()


def test_vacuum_reduces_file_size(tmp_path):
    store = EventStore(tmp_path / "m.db")
    run = _seed_backtest_run(store)
    for _ in range(800):
        _backtest_explanation(store, run, with_trade=False)
    result = run_compaction(store, make_backup=False, vacuum=True)
    assert result.pruned_explanations == 800
    assert result.vacuumed is True
    assert result.db_size_after_bytes < result.db_size_before_bytes

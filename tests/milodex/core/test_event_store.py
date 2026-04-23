"""Tests for the SQLite-backed event store."""

from __future__ import annotations

from datetime import UTC, datetime

from milodex.core.event_store import (
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    KillSwitchEvent,
    StrategyRunEvent,
    TradeEvent,
)


def test_event_store_applies_initial_schema(tmp_path):
    db_path = tmp_path / "data" / "milodex.db"

    store = EventStore(db_path)

    assert db_path.exists()
    assert store.schema_version == 6
    assert {
        "_schema_version",
        "explanations",
        "trades",
        "kill_switch_events",
        "strategy_runs",
        "backtest_runs",
        "promotions",
        "portfolio_snapshots",
        "strategy_manifests",
    }.issubset(set(store.list_table_names()))


def test_event_store_round_trips_records(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    recorded_at = datetime(2026, 4, 21, 20, 0, tzinfo=UTC)

    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type="preview",
            status="preview",
            strategy_name="paper_momentum",
            strategy_stage="paper",
            strategy_config_path="configs/paper_momentum.yaml",
            config_hash=None,
            symbol="SPY",
            side="buy",
            quantity=5.0,
            order_type="market",
            time_in_force="day",
            submitted_by="operator",
            market_open=True,
            latest_bar_timestamp=recorded_at,
            latest_bar_close=100.0,
            account_equity=10_000.0,
            account_cash=8_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=50.0,
            risk_allowed=True,
            risk_summary="Allowed",
            reason_codes=[],
            risk_checks=[{"name": "kill_switch", "passed": True}],
            context={"source": "test"},
            session_id="session-1",
        )
    )
    trade_id = store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=recorded_at,
            status="preview",
            source="paper",
            symbol="SPY",
            side="buy",
            quantity=5.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=500.0,
            strategy_name="paper_momentum",
            strategy_stage="paper",
            strategy_config_path="configs/paper_momentum.yaml",
            submitted_by="operator",
            broker_order_id=None,
            broker_status=None,
            message="Preview complete.",
            session_id="session-1",
        )
    )
    store.append_kill_switch_event(
        KillSwitchEvent(
            event_type="activated",
            recorded_at=recorded_at,
            reason="Daily loss exceeded threshold.",
        )
    )
    store.append_strategy_run(
        StrategyRunEvent(
            session_id="session-1",
            strategy_id="regime_spy_shy_200dma_v1",
            started_at=recorded_at,
            ended_at=None,
            exit_reason=None,
            metadata={"mode": "paper"},
        )
    )

    explanations = store.list_explanations()
    trades = store.list_trades()
    kill_switch_events = store.list_kill_switch_events()
    strategy_runs = store.list_strategy_runs()

    assert len(explanations) == 1
    assert explanations[0].id == explanation_id
    assert explanations[0].risk_checks == [{"name": "kill_switch", "passed": True}]
    assert explanations[0].context == {"source": "test"}
    assert explanations[0].session_id == "session-1"

    assert len(trades) == 1
    assert trades[0].id == trade_id
    assert trades[0].explanation_id == explanation_id
    assert trades[0].status == "preview"
    assert trades[0].session_id == "session-1"

    assert len(kill_switch_events) == 1
    assert kill_switch_events[0].event_type == "activated"

    assert len(strategy_runs) == 1
    assert strategy_runs[0].strategy_id == "regime_spy_shy_200dma_v1"


def test_event_store_records_backtest_run_and_links_trades(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    start_date = datetime(2024, 1, 1, tzinfo=UTC)
    end_date = datetime(2024, 12, 31, tzinfo=UTC)
    started_at = datetime(2026, 4, 21, 20, 0, tzinfo=UTC)

    run_db_id = store.append_backtest_run(
        BacktestRunEvent(
            run_id="run-abc",
            strategy_id="meanrev.daily.pullback_rsi2.curated_largecap.v1",
            config_path="configs/meanrev_daily_rsi2pullback_v1.yaml",
            config_hash="hash-1",
            start_date=start_date,
            end_date=end_date,
            started_at=started_at,
            status="running",
            slippage_pct=0.002,
            commission_per_trade=0.0,
            metadata={"walk_forward_windows": 4},
        )
    )

    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=started_at,
            decision_type="backtest",
            status="submitted",
            strategy_name="meanrev.daily.pullback_rsi2.curated_largecap.v1",
            strategy_stage="backtest",
            strategy_config_path="configs/meanrev_daily_rsi2pullback_v1.yaml",
            config_hash="hash-1",
            symbol="AAPL",
            side="buy",
            quantity=10.0,
            order_type="market",
            time_in_force="day",
            submitted_by="backtest_engine",
            market_open=True,
            latest_bar_timestamp=started_at,
            latest_bar_close=150.0,
            account_equity=10_000.0,
            account_cash=7_500.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="Allowed",
            reason_codes=[],
            risk_checks=[],
            context={"source": "backtest"},
        )
    )
    trade_id = store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=started_at,
            status="submitted",
            source="backtest",
            symbol="AAPL",
            side="buy",
            quantity=10.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=150.0,
            estimated_order_value=1_500.0,
            strategy_name="meanrev.daily.pullback_rsi2.curated_largecap.v1",
            strategy_stage="backtest",
            strategy_config_path="configs/meanrev_daily_rsi2pullback_v1.yaml",
            submitted_by="backtest_engine",
            broker_order_id=None,
            broker_status=None,
            message="Backtest fill.",
            backtest_run_id=run_db_id,
        )
    )

    store.update_backtest_run_status(
        "run-abc",
        status="completed",
        ended_at=datetime(2026, 4, 21, 21, 0, tzinfo=UTC),
    )

    run = store.get_backtest_run("run-abc")
    assert run is not None
    assert run.id == run_db_id
    assert run.status == "completed"
    assert run.slippage_pct == 0.002
    assert run.metadata == {"walk_forward_windows": 4}
    assert run.ended_at is not None

    linked_trades = store.list_trades_for_backtest_run(run_db_id)
    assert [trade.id for trade in linked_trades] == [trade_id]
    assert linked_trades[0].source == "backtest"
    assert linked_trades[0].backtest_run_id == run_db_id

    paper_trades = [trade for trade in store.list_trades() if trade.source == "paper"]
    assert paper_trades == []

    listed = store.list_backtest_runs()
    assert len(listed) == 1
    assert listed[0].run_id == "run-abc"


def test_event_store_paper_trade_has_null_backtest_run_id(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 4, 21, 20, 0, tzinfo=UTC)

    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=now,
            decision_type="submit",
            status="submitted",
            strategy_name=None,
            strategy_stage=None,
            strategy_config_path=None,
            config_hash=None,
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="operator",
            market_open=True,
            latest_bar_timestamp=now,
            latest_bar_close=450.0,
            account_equity=10_000.0,
            account_cash=9_550.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="Allowed",
            reason_codes=[],
            risk_checks=[],
            context={},
        )
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=now,
            status="submitted",
            source="paper",
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=450.0,
            estimated_order_value=450.0,
            strategy_name=None,
            strategy_stage=None,
            strategy_config_path=None,
            submitted_by="operator",
            broker_order_id=None,
            broker_status=None,
            message="Preview complete.",
        )
    )

    trades = store.list_trades()
    assert len(trades) == 1
    assert trades[0].source == "paper"
    assert trades[0].backtest_run_id is None

"""Tests for the SQLite-backed event store."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from milodex.core.event_store import (
    MIN_COMPATIBLE_SCHEMA_VERSION,
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    KillSwitchEvent,
    PromotionEvent,
    StrategyManifestEvent,
    StrategyRunEvent,
    TradeEvent,
)


def test_event_store_rejects_below_minimum_schema_version(tmp_path, monkeypatch):
    """The guard refuses to open a store whose schema is older than the build expects.

    We can't easily fabricate a stale on-disk schema (migrations would
    re-apply and bump it back to head). Instead we raise the minimum
    above the current schema head and confirm the guard fires — same
    code path as a real stale-store scenario.
    """
    monkeypatch.setattr(
        "milodex.core.event_store.MIN_COMPATIBLE_SCHEMA_VERSION",
        99,
    )
    with pytest.raises(ValueError, match="below minimum compatible version 99"):
        EventStore(tmp_path / "stale.db")


def test_event_store_min_compatible_matches_current_schema():
    """If MIN_COMPATIBLE_SCHEMA_VERSION ever exceeds the head migration, the
    guard would refuse a freshly-built store. Lock that contract."""
    assert MIN_COMPATIBLE_SCHEMA_VERSION == 7


def test_event_store_applies_initial_schema(tmp_path):
    db_path = tmp_path / "data" / "milodex.db"

    store = EventStore(db_path)

    assert db_path.exists()
    assert store.schema_version == 7
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


def _seed_running_backtest_run(
    store: EventStore,
    *,
    run_id: str,
    strategy_id: str,
    started_at: datetime,
) -> int:
    return store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=strategy_id,
            config_path=f"configs/{strategy_id}.yaml",
            config_hash="hash-x",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 12, 31, tzinfo=UTC),
            started_at=started_at,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={"walk_forward": True, "windows_planned": 4},
        )
    )


def test_reconcile_orphan_backtest_runs_closes_running_rows_for_same_strategy(tmp_path):
    """A backtest_runs row left in ``status='running'`` with ``ended_at IS NULL``
    is the database-side fingerprint of a process that died mid-run (machine
    sleep, OOM, kill -9, parquet corruption — see PR #44 for the cache-side
    failure mode that produced yesterday's three orphans). The next backtest
    for the same strategy must close that row out so reports don't see a
    phantom session that lasts forever.
    """
    store = EventStore(tmp_path / "milodex.db")
    strategy_id = "momentum.daily.tsmom.curated_largecap.v1"
    started_at = datetime(2026, 5, 6, 16, 50, 23, tzinfo=UTC)
    _seed_running_backtest_run(
        store,
        run_id="orphan-run",
        strategy_id=strategy_id,
        started_at=started_at,
    )

    closed_at = datetime(2026, 5, 7, 13, 0, 0, tzinfo=UTC)
    reconciled = store.reconcile_orphan_backtest_runs(strategy_id=strategy_id, ended_at=closed_at)

    assert reconciled == 1
    run = store.get_backtest_run("orphan-run")
    assert run is not None
    assert run.status == "orphan_recovered"
    assert run.ended_at == closed_at


def test_reconcile_orphan_backtest_runs_leaves_other_strategies_untouched(tmp_path):
    """Per-strategy_id scope: an orphan for strategy Y is Y's next-startup
    responsibility, not strategy X's. Mirrors ``reconcile_orphan_strategy_runs``
    semantics from PR #44.
    """
    store = EventStore(tmp_path / "milodex.db")
    target_strategy = "momentum.daily.tsmom.curated_largecap.v1"
    other_strategy = "breakout.daily.donchian_20_10.sector_etfs.v1"
    started_at = datetime(2026, 5, 6, 16, 50, 23, tzinfo=UTC)
    _seed_running_backtest_run(
        store,
        run_id="orphan-other",
        strategy_id=other_strategy,
        started_at=started_at,
    )

    reconciled = store.reconcile_orphan_backtest_runs(
        strategy_id=target_strategy,
        ended_at=datetime(2026, 5, 7, 13, 0, 0, tzinfo=UTC),
    )

    assert reconciled == 0
    untouched = store.get_backtest_run("orphan-other")
    assert untouched is not None
    assert untouched.status == "running"
    assert untouched.ended_at is None


def test_reconcile_orphan_backtest_runs_does_not_touch_completed_runs(tmp_path):
    """A completed (or otherwise terminal-status) run is not an orphan even if
    ``ended_at`` were somehow null — and conversely, a row that already has a
    non-null ``ended_at`` is not in scope. Both halves of the WHERE clause
    must hold for a row to be reconciled, so terminal-state rows are safe.
    """
    store = EventStore(tmp_path / "milodex.db")
    strategy_id = "momentum.daily.tsmom.curated_largecap.v1"
    started_at = datetime(2026, 5, 6, 16, 50, 23, tzinfo=UTC)
    _seed_running_backtest_run(
        store,
        run_id="completed-run",
        strategy_id=strategy_id,
        started_at=started_at,
    )
    store.update_backtest_run_status(
        "completed-run",
        status="completed",
        ended_at=datetime(2026, 5, 6, 16, 55, 0, tzinfo=UTC),
    )

    reconciled = store.reconcile_orphan_backtest_runs(
        strategy_id=strategy_id,
        ended_at=datetime(2026, 5, 7, 13, 0, 0, tzinfo=UTC),
    )

    assert reconciled == 0
    completed = store.get_backtest_run("completed-run")
    assert completed is not None
    assert completed.status == "completed"
    # Original ended_at is preserved — reconcile didn't sweep this row.
    assert completed.ended_at == datetime(2026, 5, 6, 16, 55, 0, tzinfo=UTC)


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


def test_promotion_event_roundtrips_evidence_fields(tmp_path):
    store = EventStore(tmp_path / "data" / "milodex.db")
    recorded_at = datetime(2026, 4, 23, 18, 0, tzinfo=UTC)

    manifest_id = store.append_strategy_manifest(
        StrategyManifestEvent(
            strategy_id="regime.daily.sma200_rotation.cli_test.v1",
            stage="paper",
            config_hash="a" * 64,
            config_json={"strategy": {"id": "regime.daily.sma200_rotation.cli_test.v1"}},
            config_path="configs/test.yaml",
            frozen_at=recorded_at,
            frozen_by="operator",
        )
    )

    first_id = store.append_promotion(
        PromotionEvent(
            strategy_id="regime.daily.sma200_rotation.cli_test.v1",
            from_stage="backtest",
            to_stage="paper",
            promotion_type="lifecycle_exempt",
            approved_by="operator",
            recorded_at=recorded_at,
            manifest_id=manifest_id,
            evidence_json={
                "schema_version": 1,
                "recommendation": "promote",
                "known_risks": ["risk A"],
            },
        )
    )

    reversal_id = store.append_promotion(
        PromotionEvent(
            strategy_id="regime.daily.sma200_rotation.cli_test.v1",
            from_stage="paper",
            to_stage="backtest",
            promotion_type="demotion",
            approved_by="operator",
            recorded_at=recorded_at,
            reverses_event_id=first_id,
        )
    )

    fetched = store.get_promotion(first_id)
    assert fetched is not None
    assert fetched.manifest_id == manifest_id
    assert fetched.evidence_json == {
        "schema_version": 1,
        "recommendation": "promote",
        "known_risks": ["risk A"],
    }
    assert fetched.reverses_event_id is None

    reversal = store.get_promotion(reversal_id)
    assert reversal is not None
    assert reversal.reverses_event_id == first_id
    assert reversal.manifest_id is None
    assert reversal.evidence_json is None

"""Tests for the SQLite-backed event store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from milodex.core.event_store import (
    MIN_COMPATIBLE_SCHEMA_VERSION,
    BacktestEquitySnapshotEvent,
    BacktestRunEvent,
    EventStore,
    ExecutionAttemptEvent,
    ExplanationEvent,
    KillSwitchEvent,
    OrchestrationBatchEvent,
    OrchestrationJobEvent,
    PortfolioSnapshotEvent,
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
    assert MIN_COMPATIBLE_SCHEMA_VERSION == 12


def test_event_store_applies_initial_schema(tmp_path):
    db_path = tmp_path / "data" / "milodex.db"

    store = EventStore(db_path)

    assert db_path.exists()
    assert store.schema_version == 14
    assert {
        "_schema_version",
        "explanations",
        "trades",
        "kill_switch_events",
        "strategy_runs",
        "backtest_runs",
        "promotions",
        "portfolio_snapshots",
        "backtest_equity_snapshots",
        "portfolio_snapshots_quarantine",
        "strategy_manifests",
        "orchestration_batches",
        "orchestration_jobs",
        "risk_profile_changes",
        "reconciliation_runs",
        "reconciliation_adjustments",
    }.issubset(set(store.list_table_names()))


def test_orchestration_ledger_schema_has_adr_0040_tables_and_indexes(tmp_path):
    import sqlite3

    db_path = tmp_path / "milodex.db"
    store = EventStore(db_path)

    assert store.schema_version == 14
    with sqlite3.connect(db_path) as con:
        batch_columns = {row[1] for row in con.execute("PRAGMA table_info(orchestration_batches)")}
        job_columns = {row[1] for row in con.execute("PRAGMA table_info(orchestration_jobs)")}
        index_names = {row[1] for row in con.execute("PRAGMA index_list(orchestration_jobs)")} | {
            row[1] for row in con.execute("PRAGMA index_list(orchestration_batches)")
        }

    assert {
        "id",
        "batch_id",
        "action_type",
        "requested_by",
        "requested_at",
        "status",
        "metadata_json",
    }.issubset(batch_columns)
    assert {
        "id",
        "job_id",
        "batch_id",
        "strategy_id",
        "action_type",
        "requested_stage",
        "status",
        "queued_at",
        "started_at",
        "ended_at",
        "cancel_requested_at",
        "execution_ref_type",
        "execution_ref",
        "progress_current",
        "progress_total",
        "progress_label",
        "error_code",
        "error_message",
        "metadata_json",
    }.issubset(job_columns)
    assert {
        "idx_orchestration_batches_status_requested_at",
        "idx_orchestration_jobs_batch_id",
        "idx_orchestration_jobs_status",
        "idx_orchestration_jobs_execution_ref",
        "idx_orchestration_jobs_strategy_status",
    }.issubset(index_names)


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


def test_orchestration_batch_round_trips_metadata_and_status_update(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    requested_at = datetime(2026, 5, 10, 14, 0, tzinfo=UTC)

    db_id = store.create_orchestration_batch(
        OrchestrationBatchEvent(
            batch_id="batch-1",
            action_type="backtest_walk_forward",
            requested_by="operator",
            requested_at=requested_at,
            status="queued",
            metadata={"source": "kanban", "strategy_count": 2},
        )
    )
    store.update_orchestration_batch_status(
        "batch-1",
        status="running",
        metadata={"source": "kanban", "started_by": "worker-1"},
    )

    fetched = store.get_orchestration_batch("batch-1")
    listed = store.list_orchestration_batches()

    assert fetched is not None
    assert fetched.id == db_id
    assert fetched.batch_id == "batch-1"
    assert fetched.action_type == "backtest_walk_forward"
    assert fetched.requested_by == "operator"
    assert fetched.requested_at == requested_at
    assert fetched.status == "running"
    assert fetched.metadata == {"source": "kanban", "started_by": "worker-1"}
    assert listed == [fetched]


def test_orchestration_job_round_trips_metadata_progress_and_execution_ref(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    requested_at = datetime(2026, 5, 10, 14, 0, tzinfo=UTC)
    started_at = datetime(2026, 5, 10, 14, 1, tzinfo=UTC)

    store.create_orchestration_batch(
        OrchestrationBatchEvent(
            batch_id="batch-1",
            action_type="backtest_walk_forward",
            requested_by="operator",
            requested_at=requested_at,
            status="queued",
            metadata={},
        )
    )
    job_db_id = store.create_orchestration_job(
        OrchestrationJobEvent(
            job_id="job-1",
            batch_id="batch-1",
            strategy_id="momentum.daily.tsmom.curated_largecap.v1",
            action_type="backtest_walk_forward",
            requested_stage="backtest",
            status="queued",
            queued_at=requested_at,
            started_at=None,
            ended_at=None,
            cancel_requested_at=None,
            execution_ref_type=None,
            execution_ref=None,
            progress_current=0,
            progress_total=4,
            progress_label="queued",
            error_code=None,
            error_message=None,
            metadata={"symbols": ["SPY", "QQQ"], "window": {"years": 3}},
        )
    )

    store.update_orchestration_job_status(
        "job-1",
        status="running",
        started_at=started_at,
        execution_ref_type="backtest_run",
        execution_ref="run-abc",
        progress_current=1,
        progress_total=4,
        progress_label="walk-forward 1/4",
        metadata={"symbols": ["SPY", "QQQ"], "worker": "worker-1"},
    )

    fetched = store.get_orchestration_job("job-1")
    jobs_for_batch = store.list_orchestration_jobs(batch_id="batch-1")

    assert fetched is not None
    assert fetched.id == job_db_id
    assert fetched.job_id == "job-1"
    assert fetched.batch_id == "batch-1"
    assert fetched.strategy_id == "momentum.daily.tsmom.curated_largecap.v1"
    assert fetched.status == "running"
    assert fetched.started_at == started_at
    assert fetched.execution_ref_type == "backtest_run"
    assert fetched.execution_ref == "run-abc"
    assert fetched.progress_current == 1
    assert fetched.progress_total == 4
    assert fetched.progress_label == "walk-forward 1/4"
    assert fetched.metadata == {"symbols": ["SPY", "QQQ"], "worker": "worker-1"}
    assert jobs_for_batch == [fetched]


def test_request_orchestration_job_cancellation_sets_timestamp(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    queued_at = datetime(2026, 5, 10, 14, 0, tzinfo=UTC)
    cancel_requested_at = datetime(2026, 5, 10, 14, 5, tzinfo=UTC)

    store.create_orchestration_batch(
        OrchestrationBatchEvent(
            batch_id="batch-1",
            action_type="paper_session_start",
            requested_by="operator",
            requested_at=queued_at,
            status="queued",
            metadata={},
        )
    )
    store.create_orchestration_job(
        OrchestrationJobEvent(
            job_id="job-1",
            batch_id="batch-1",
            strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
            action_type="paper_session_start",
            requested_stage="paper",
            status="queued",
            queued_at=queued_at,
            started_at=None,
            ended_at=None,
            cancel_requested_at=None,
            execution_ref_type=None,
            execution_ref=None,
            progress_current=0,
            progress_total=None,
            progress_label="queued",
            error_code=None,
            error_message=None,
            metadata={},
        )
    )

    store.request_orchestration_job_cancellation(
        "job-1",
        cancel_requested_at=cancel_requested_at,
    )

    job = store.get_orchestration_job("job-1")
    assert job is not None
    assert job.status == "queued"
    assert job.cancel_requested_at == cancel_requested_at


def test_list_non_terminal_orchestration_jobs_filters_completed_failed_cancelled(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    queued_at = datetime(2026, 5, 10, 14, 0, tzinfo=UTC)
    store.create_orchestration_batch(
        OrchestrationBatchEvent(
            batch_id="batch-1",
            action_type="backtest_walk_forward",
            requested_by="operator",
            requested_at=queued_at,
            status="running",
            metadata={},
        )
    )

    for job_id, status in [
        ("queued-job", "queued"),
        ("running-job", "running"),
        ("blocked-job", "blocked"),
        ("orphan-recovered-job", "orphan_recovered"),
        ("completed-job", "completed"),
        ("failed-job", "failed"),
        ("cancelled-job", "cancelled"),
    ]:
        store.create_orchestration_job(
            OrchestrationJobEvent(
                job_id=job_id,
                batch_id="batch-1",
                strategy_id=f"strategy.{job_id}",
                action_type="backtest_walk_forward",
                requested_stage="backtest",
                status=status,
                queued_at=queued_at,
                started_at=None,
                ended_at=queued_at if status in {"completed", "failed", "cancelled"} else None,
                cancel_requested_at=None,
                execution_ref_type=None,
                execution_ref=None,
                progress_current=0,
                progress_total=4,
                progress_label=status,
                error_code="boom" if status == "failed" else None,
                error_message="failed" if status == "failed" else None,
                metadata={},
            )
        )

    active = store.list_non_terminal_orchestration_jobs()

    assert [job.job_id for job in active] == [
        "queued-job",
        "running-job",
    ]


def test_orchestration_ledger_writes_do_not_create_execution_rows(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    queued_at = datetime(2026, 5, 10, 14, 0, tzinfo=UTC)

    store.create_orchestration_batch(
        OrchestrationBatchEvent(
            batch_id="batch-1",
            action_type="backtest_walk_forward",
            requested_by="operator",
            requested_at=queued_at,
            status="queued",
            metadata={},
        )
    )
    store.create_orchestration_job(
        OrchestrationJobEvent(
            job_id="job-1",
            batch_id="batch-1",
            strategy_id="momentum.daily.tsmom.curated_largecap.v1",
            action_type="backtest_walk_forward",
            requested_stage="backtest",
            status="queued",
            queued_at=queued_at,
            started_at=None,
            ended_at=None,
            cancel_requested_at=None,
            execution_ref_type="backtest_run",
            execution_ref="not-created-by-ledger",
            progress_current=0,
            progress_total=4,
            progress_label="queued",
            error_code=None,
            error_message=None,
            metadata={},
        )
    )

    assert store.list_backtest_runs() == []
    assert store.list_strategy_runs() == []
    assert store.list_promotions() == []
    assert store.list_trades() == []
    assert store.list_explanations() == []


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
            backtest_run_id=run_db_id,
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


def test_finalize_backtest_run_writes_status_ended_at_and_metadata_together(tmp_path):
    """Terminal close-out is a single transaction (P1-03).

    Status, ``ended_at``, and ``metadata_json`` all land together — there is
    no intermediate commit where the row is 'completed' but the evidence
    metrics (which live only in ``metadata_json``) are missing.
    """
    store = EventStore(tmp_path / "milodex.db")
    started_at = datetime(2026, 6, 1, 14, 0, tzinfo=UTC)
    _seed_running_backtest_run(
        store,
        run_id="finalize-run",
        strategy_id="test.strat.v1",
        started_at=started_at,
    )
    ended_at = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)

    store.finalize_backtest_run(
        "finalize-run",
        status="completed",
        metadata={"initial_equity": 100_000.0, "trade_count": 7},
        ended_at=ended_at,
    )

    run = store.get_backtest_run("finalize-run")
    assert run is not None
    assert run.status == "completed"
    assert run.ended_at == ended_at
    assert run.metadata == {"initial_equity": 100_000.0, "trade_count": 7}


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


# ---------------------------------------------------------------------------
# Dual-ancestor enforcement (migration 008)
# ---------------------------------------------------------------------------


def _explanation_kwargs(**overrides) -> dict:
    """Build a minimal valid ExplanationEvent payload for ancestor tests."""
    recorded_at = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    base = {
        "recorded_at": recorded_at,
        "decision_type": "submit",
        "status": "submitted",
        "strategy_name": "regime.daily.sma200_rotation.spy_shy.v1",
        "strategy_stage": "paper",
        "strategy_config_path": "configs/regime.yaml",
        "config_hash": "abc",
        "symbol": "SPY",
        "side": "buy",
        "quantity": 1.0,
        "order_type": "market",
        "time_in_force": "day",
        "submitted_by": "strategy_runner",
        "market_open": True,
        "latest_bar_timestamp": recorded_at,
        "latest_bar_close": 400.0,
        "account_equity": 10_000.0,
        "account_cash": 10_000.0,
        "account_portfolio_value": 10_000.0,
        "account_daily_pnl": 0.0,
        "risk_allowed": True,
        "risk_summary": "Allowed",
        "reason_codes": [],
        "risk_checks": [],
        "context": {},
    }
    base.update(overrides)
    return base


def test_append_explanation_rejects_strategy_runner_with_no_ancestor(tmp_path):
    """A strategy_runner row with neither session_id nor backtest_run_id raises."""
    store = EventStore(tmp_path / "milodex.db")

    with pytest.raises(ValueError, match="must carry an ancestor"):
        store.append_explanation(
            ExplanationEvent(**_explanation_kwargs(submitted_by="strategy_runner"))
        )


def test_append_explanation_rejects_backtest_engine_with_no_ancestor(tmp_path):
    """A backtest_engine row with neither session_id nor backtest_run_id raises."""
    store = EventStore(tmp_path / "milodex.db")

    with pytest.raises(ValueError, match="must carry an ancestor"):
        store.append_explanation(
            ExplanationEvent(**_explanation_kwargs(submitted_by="backtest_engine"))
        )


def test_append_explanation_accepts_strategy_runner_with_session_id(tmp_path):
    """A paper-runner row with a session_id is accepted (the live path)."""
    store = EventStore(tmp_path / "milodex.db")

    explanation_id = store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(submitted_by="strategy_runner", session_id="paper-1")
        )
    )

    assert explanation_id > 0
    rows = store.list_explanations()
    assert len(rows) == 1
    assert rows[0].session_id == "paper-1"
    assert rows[0].backtest_run_id is None


def test_append_explanation_accepts_backtest_engine_with_backtest_run_id(tmp_path):
    """A backtest engine row with backtest_run_id is accepted (the new path)."""
    store = EventStore(tmp_path / "milodex.db")
    db_run_id = _seed_running_backtest_run(
        store,
        run_id="run-1",
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        started_at=datetime(2026, 5, 7, 14, 0, tzinfo=UTC),
    )

    explanation_id = store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(
                submitted_by="backtest_engine",
                backtest_run_id=db_run_id,
            )
        )
    )

    assert explanation_id > 0
    rows = store.list_explanations()
    assert len(rows) == 1
    assert rows[0].backtest_run_id == db_run_id
    assert rows[0].session_id is None


def test_append_explanation_allows_operator_without_ancestor(tmp_path):
    """System events (operator CLI, reconcile) bypass the ancestor check."""
    store = EventStore(tmp_path / "milodex.db")

    op_id = store.append_explanation(
        ExplanationEvent(**_explanation_kwargs(submitted_by="operator"))
    )
    rec_id = store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(submitted_by="reconcile", decision_type="reconcile_incident")
        )
    )

    assert op_id > 0
    assert rec_id > 0
    rows = store.list_explanations()
    assert len(rows) == 2
    assert all(r.session_id is None and r.backtest_run_id is None for r in rows)


def test_latest_reconcile_incident_hash_returns_none_when_empty(tmp_path):
    """No reconcile_incident rows -> None (startup idempotency check)."""
    store = EventStore(tmp_path / "milodex.db")
    assert store.latest_reconcile_incident_hash() is None


def test_latest_reconcile_incident_hash_returns_most_recent(tmp_path):
    """Returns the most recent reconcile_incident config_hash, ignoring other
    decision types and earlier incidents."""
    store = EventStore(tmp_path / "milodex.db")
    # An unrelated decision type must be ignored.
    store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(submitted_by="operator", config_hash="not-an-incident")
        )
    )
    # Two incidents; the most recently appended one wins.
    store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(
                submitted_by="reconcile",
                decision_type="reconcile_incident",
                config_hash="incident-old",
            )
        )
    )
    store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(
                submitted_by="reconcile",
                decision_type="reconcile_incident",
                config_hash="incident-new",
            )
        )
    )
    assert store.latest_reconcile_incident_hash() == "incident-new"


def test_append_explanation_rejects_invalid_backtest_run_id_via_fk(tmp_path):
    """SQLite FK rejects backtest_run_id that doesn't reference a real row."""
    import sqlite3

    store = EventStore(tmp_path / "milodex.db")

    with pytest.raises(sqlite3.IntegrityError):
        store.append_explanation(
            ExplanationEvent(
                **_explanation_kwargs(submitted_by="backtest_engine", backtest_run_id=999_999)
            )
        )


def test_migration_008_backfills_walk_forward_explanations(tmp_path):
    """Walk-forward orphan rows (session_id ``<run_id>:wN``) are linked to
    their parent ``backtest_runs`` id via the migration 008 backfill.

    Build a v7 schema by hand (no `backtest_run_id` column on explanations,
    `session_id` carrying the walk-forward suffix), then open the file with
    the live `EventStore`. Migration 008 fires automatically; the rows it
    finds get the canonical ancestor id.
    """
    import sqlite3

    db_path = tmp_path / "milodex.db"

    # Build v7 schema manually from migrations 001-007.
    migrations_dir = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "src"
        / "milodex"
        / "core"
        / "migrations"
    )
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL)")
    for sql_path in sorted(migrations_dir.glob("00[1-7]_*.sql")):
        con.executescript(sql_path.read_text(encoding="utf-8"))
    con.execute("DELETE FROM _schema_version")
    con.execute("INSERT INTO _schema_version(version) VALUES (7)")
    con.commit()

    # Seed two backtest_runs and three explanation rows referencing them via
    # session_id syntax (whole-period + two walk-forward windows).
    con.execute(
        """
        INSERT INTO backtest_runs (
            run_id, strategy_id, config_path, config_hash,
            start_date, end_date, started_at, status, slippage_pct,
            commission_per_trade, metadata_json
        ) VALUES (
            'run-A', 'strat.A', 'configs/a.yaml', 'hashA',
            '2024-01-01T00:00:00+00:00', '2024-12-31T00:00:00+00:00',
            '2026-05-07T14:00:00+00:00', 'completed', 0.001, 0.0, '{}'
        )
        """
    )
    run_a_id = con.execute("SELECT id FROM backtest_runs WHERE run_id='run-A'").fetchone()[0]

    con.execute(
        """
        INSERT INTO backtest_runs (
            run_id, strategy_id, config_path, config_hash,
            start_date, end_date, started_at, status, slippage_pct,
            commission_per_trade, metadata_json
        ) VALUES (
            'run-B', 'strat.B', 'configs/b.yaml', 'hashB',
            '2024-01-01T00:00:00+00:00', '2024-12-31T00:00:00+00:00',
            '2026-05-07T15:00:00+00:00', 'completed', 0.001, 0.0, '{}'
        )
        """
    )
    run_b_id = con.execute("SELECT id FROM backtest_runs WHERE run_id='run-B'").fetchone()[0]

    # Three pre-migration explanation rows, all from backtest_engine, with
    # session_ids in the three formats migration 008 backfills.
    explanation_template = (
        "INSERT INTO explanations ("
        "recorded_at, decision_type, status, strategy_name, strategy_stage, "
        "strategy_config_path, config_hash, symbol, side, quantity, "
        "order_type, time_in_force, submitted_by, market_open, "
        "latest_bar_timestamp, latest_bar_close, account_equity, "
        "account_cash, account_portfolio_value, account_daily_pnl, "
        "risk_allowed, risk_summary, reason_codes_json, risk_checks_json, "
        "context_json, session_id) VALUES (?, 'no_trade', 'no_signal', "
        "'strat.X', 'backtest', 'configs/x.yaml', 'h', 'SPY', 'hold', 0.0, "
        "'none', 'day', 'backtest_engine', 1, NULL, NULL, 10000.0, 10000.0, "
        "10000.0, 0.0, 1, 'allowed', '[]', '[]', '{}', ?)"
    )

    # Tier 2: walk-forward suffix matches run-A
    con.execute(explanation_template, ("2026-05-07T14:00:00+00:00", "run-A:w0"))
    con.execute(explanation_template, ("2026-05-07T14:01:00+00:00", "run-A:w1"))
    # Tier 3: whole-period (session_id == run_id) matches run-B
    con.execute(explanation_template, ("2026-05-07T15:00:00+00:00", "run-B"))
    # No-match orphan: session_id matches no run
    con.execute(
        explanation_template,
        ("2026-05-07T16:00:00+00:00", "ghost-session-no-such-run"),
    )

    con.commit()
    con.close()

    # Open with the live EventStore — this triggers migrations 008-012.
    store = EventStore(db_path)
    assert store.schema_version == 14

    rows = sorted(store.list_explanations(), key=lambda r: r.recorded_at)
    assert len(rows) == 4

    # Tier 2: walk-forward windows linked to run-A
    assert rows[0].session_id == "run-A:w0"
    assert rows[0].backtest_run_id == run_a_id
    assert rows[1].session_id == "run-A:w1"
    assert rows[1].backtest_run_id == run_a_id

    # Tier 3: whole-period linked to run-B
    assert rows[2].session_id == "run-B"
    assert rows[2].backtest_run_id == run_b_id

    # Unmatched session stays with NULL backtest_run_id (no false linkage)
    assert rows[3].session_id == "ghost-session-no-such-run"
    assert rows[3].backtest_run_id is None


def test_migration_008_backfills_via_trades_backtest_run_id(tmp_path):
    """Tier 1 backfill: an explanation paired with a trade row that already
    has ``trades.backtest_run_id`` should adopt that id even when the
    explanation's session_id is NULL or unparseable.
    """
    import sqlite3

    db_path = tmp_path / "milodex.db"
    migrations_dir = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "src"
        / "milodex"
        / "core"
        / "migrations"
    )

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL)")
    for sql_path in sorted(migrations_dir.glob("00[1-7]_*.sql")):
        con.executescript(sql_path.read_text(encoding="utf-8"))
    con.execute("DELETE FROM _schema_version")
    con.execute("INSERT INTO _schema_version(version) VALUES (7)")
    con.commit()

    con.execute(
        """
        INSERT INTO backtest_runs (
            run_id, strategy_id, config_path, config_hash,
            start_date, end_date, started_at, status, slippage_pct,
            commission_per_trade, metadata_json
        ) VALUES (
            'run-T', 'strat.T', 'configs/t.yaml', 'hT',
            '2024-01-01T00:00:00+00:00', '2024-12-31T00:00:00+00:00',
            '2026-05-07T14:00:00+00:00', 'completed', 0.001, 0.0, '{}'
        )
        """
    )
    run_t_id = con.execute("SELECT id FROM backtest_runs WHERE run_id='run-T'").fetchone()[0]

    cursor = con.execute(
        "INSERT INTO explanations ("
        "recorded_at, decision_type, status, strategy_name, strategy_stage, "
        "strategy_config_path, config_hash, symbol, side, quantity, "
        "order_type, time_in_force, submitted_by, market_open, "
        "latest_bar_timestamp, latest_bar_close, account_equity, "
        "account_cash, account_portfolio_value, account_daily_pnl, "
        "risk_allowed, risk_summary, reason_codes_json, risk_checks_json, "
        "context_json, session_id) VALUES ("
        "'2026-05-07T14:00:00+00:00', 'submit', 'submitted', 'strat.T', 'backtest', "
        "'configs/t.yaml', 'h', 'AAPL', 'buy', 1.0, 'market', 'day', "
        "'backtest_engine', 1, NULL, NULL, 10000.0, 10000.0, 10000.0, 0.0, 1, "
        "'ok', '[]', '[]', '{}', NULL)"
    )
    explanation_id = cursor.lastrowid
    con.execute(
        "INSERT INTO trades ("
        "explanation_id, recorded_at, status, source, symbol, side, quantity, "
        "order_type, time_in_force, estimated_unit_price, estimated_order_value, "
        "strategy_name, strategy_stage, strategy_config_path, submitted_by, "
        "broker_order_id, broker_status, message, session_id, backtest_run_id) "
        "VALUES (?, '2026-05-07T14:00:00+00:00', 'submitted', 'backtest', 'AAPL', "
        "'buy', 1.0, 'market', 'day', 100.0, 100.0, 'strat.T', 'backtest', "
        "'configs/t.yaml', 'backtest_engine', NULL, NULL, NULL, NULL, ?)",
        (explanation_id, run_t_id),
    )
    con.commit()
    con.close()

    store = EventStore(db_path)
    rows = store.list_explanations()
    assert len(rows) == 1
    # Tier 1 picked up the link from trades.backtest_run_id even though the
    # explanation's session_id is NULL.
    assert rows[0].session_id is None
    assert rows[0].backtest_run_id == run_t_id


def test_migration_008_is_idempotent(tmp_path):
    """Re-opening an already-migrated store does nothing destructive."""
    store = EventStore(tmp_path / "milodex.db")
    db_run_id = _seed_running_backtest_run(
        store,
        run_id="run-id",
        strategy_id="strat.x",
        started_at=datetime(2026, 5, 7, 14, 0, tzinfo=UTC),
    )
    store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(submitted_by="backtest_engine", backtest_run_id=db_run_id)
        )
    )

    # Re-open. Migrations should not run again (already at schema head).
    store2 = EventStore(tmp_path / "milodex.db")
    rows = store2.list_explanations()
    assert len(rows) == 1
    assert rows[0].backtest_run_id == db_run_id
    assert store2.schema_version == 14


# ─── BacktestEquitySnapshotEvent CRUD (ADR 0053, migration 010) ──────────────


def _make_backtest_equity_event(
    store: EventStore,
    *,
    session_id: str = "run-abc:w0",
    strategy_id: str = "test.strategy.v1",
    equity: float = 1234.56,
    backtest_run_id: int | None = None,
) -> BacktestEquitySnapshotEvent:
    ts = datetime(2025, 12, 15, tzinfo=UTC)
    return BacktestEquitySnapshotEvent(
        recorded_at=ts,
        session_id=session_id,
        strategy_id=strategy_id,
        equity=equity,
        cash=500.0,
        portfolio_value=equity,
        daily_pnl=None,
        positions=[],
        backtest_run_id=backtest_run_id,
    )


def test_append_backtest_equity_snapshot_returns_id(tmp_path):
    """append_backtest_equity_snapshot returns an autoincrement integer id."""
    store = EventStore(tmp_path / "milodex.db")
    event = _make_backtest_equity_event(store)
    row_id = store.append_backtest_equity_snapshot(event)
    assert isinstance(row_id, int)
    assert row_id >= 1


def test_append_backtest_equity_snapshot_round_trips(tmp_path):
    """Appended row is retrievable via list_backtest_equity_snapshots_for_strategy."""
    store = EventStore(tmp_path / "milodex.db")
    ts = datetime(2025, 12, 15, tzinfo=UTC)
    event = BacktestEquitySnapshotEvent(
        recorded_at=ts,
        session_id="run-abc:w0",
        strategy_id="test.strategy.v1",
        equity=2500.0,
        cash=800.0,
        portfolio_value=2500.0,
        daily_pnl=None,
        positions=[{"symbol": "SPY", "quantity": 5}],
        backtest_run_id=None,
    )
    store.append_backtest_equity_snapshot(event)

    rows = store.list_backtest_equity_snapshots_for_strategy("test.strategy.v1")
    assert len(rows) == 1
    r = rows[0]
    assert r.strategy_id == "test.strategy.v1"
    assert r.session_id == "run-abc:w0"
    assert r.equity == 2500.0
    assert r.cash == 800.0
    assert r.portfolio_value == 2500.0
    assert r.daily_pnl is None
    assert r.positions == [{"symbol": "SPY", "quantity": 5}]
    assert r.backtest_run_id is None


def test_list_backtest_equity_snapshots_filters_by_strategy(tmp_path):
    """list_backtest_equity_snapshots_for_strategy returns only matching strategy rows."""
    store = EventStore(tmp_path / "milodex.db")
    for i in range(3):
        store.append_backtest_equity_snapshot(
            _make_backtest_equity_event(store, strategy_id="strat.a", equity=1000.0 + i * 100)
        )
    for i in range(2):
        store.append_backtest_equity_snapshot(
            _make_backtest_equity_event(store, strategy_id="strat.b", equity=2000.0 + i * 100)
        )

    rows_a = store.list_backtest_equity_snapshots_for_strategy("strat.a")
    rows_b = store.list_backtest_equity_snapshots_for_strategy("strat.b")
    rows_c = store.list_backtest_equity_snapshots_for_strategy("strat.nonexistent")

    assert len(rows_a) == 3
    assert len(rows_b) == 2
    assert len(rows_c) == 0


def test_append_backtest_equity_snapshot_does_not_touch_portfolio_snapshots(tmp_path):
    """Writing backtest equity snapshots leaves portfolio_snapshots unchanged."""
    store = EventStore(tmp_path / "milodex.db")
    # Add a broker snapshot first
    broker_event = PortfolioSnapshotEvent(
        recorded_at=datetime(2026, 4, 27, 21, 0, tzinfo=UTC),
        session_id="broker-session-1",
        strategy_id="test.strategy.v1",
        equity=100000.0,
        cash=50000.0,
        portfolio_value=100000.0,
        daily_pnl=25.0,
        positions=[],
    )
    store.append_portfolio_snapshot(broker_event)

    # Add three backtest equity snapshots
    for i in range(3):
        store.append_backtest_equity_snapshot(
            _make_backtest_equity_event(store, session_id=f"run-abc:w{i}")
        )

    # portfolio_snapshots must still have exactly 1 row
    broker_rows = store.list_portfolio_snapshots_for_strategy("test.strategy.v1")
    assert len(broker_rows) == 1
    assert broker_rows[0].equity == 100000.0

    # backtest_equity_snapshots must have 3 rows
    bt_rows = store.list_backtest_equity_snapshots_for_strategy("test.strategy.v1")
    assert len(bt_rows) == 3


def test_get_latest_promotion_orders_by_recorded_at_not_id(tmp_path):
    """A backdated demotion inserted AFTER a newer promotion must not be
    returned as 'latest'. Mirrors the live pullback_rsi2 / audit_backfill case:
    id-order says backtest, wall-clock order says paper (correct)."""
    store = EventStore(tmp_path / "data" / "milodex.db")
    sid = "meanrev.daily.pullback_rsi2.curated_largecap.v1"

    # id=1 (lower id) but LATER wall-clock: the real paper promotion.
    store.append_promotion(
        PromotionEvent(
            strategy_id=sid,
            from_stage="backtest",
            to_stage="paper",
            promotion_type="statistical",
            approved_by="operator",
            recorded_at=datetime(2026, 5, 7, 13, 34, tzinfo=UTC),
        )
    )
    # id=2 (higher id) but EARLIER wall-clock: the backdated audit_backfill demotion.
    store.append_promotion(
        PromotionEvent(
            strategy_id=sid,
            from_stage="micro_live",
            to_stage="backtest",
            promotion_type="demotion",
            approved_by="audit_backfill",
            recorded_at=datetime(2026, 5, 6, 20, 0, tzinfo=UTC),
        )
    )

    latest = store.get_latest_promotion_for_strategy(sid)
    assert latest is not None
    assert latest.to_stage == "paper"  # wall-clock latest, NOT the higher-id demotion
    assert latest.promotion_type == "statistical"


def test_get_latest_promotion_tiebreaks_on_id_when_recorded_at_equal(tmp_path):
    """Equal recorded_at -> higher id wins (deterministic, insertion order)."""
    store = EventStore(tmp_path / "data" / "milodex.db")
    sid = "tie.daily.example.v1"
    ts = datetime(2026, 5, 7, 13, 34, tzinfo=UTC)
    store.append_promotion(
        PromotionEvent(
            strategy_id=sid,
            from_stage="backtest",
            to_stage="paper",
            promotion_type="statistical",
            approved_by="op",
            recorded_at=ts,
        )
    )
    store.append_promotion(
        PromotionEvent(
            strategy_id=sid,
            from_stage="paper",
            to_stage="backtest",
            promotion_type="demotion",
            approved_by="op",
            recorded_at=ts,
        )
    )
    latest = store.get_latest_promotion_for_strategy(sid)
    assert latest is not None
    assert latest.to_stage == "backtest"  # higher id breaks the tie


def _seed_trade(store: EventStore, *, symbol: str = "SPY", side: str = "buy") -> int:
    """Append an explanation + a paper trade; return the trade id."""
    explanation_id = store.append_explanation(
        ExplanationEvent(**_explanation_kwargs(submitted_by="operator", symbol=symbol, side=side))
    )
    return store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=datetime(2026, 5, 7, 14, 0, tzinfo=UTC),
            status="filled",
            source="paper",
            symbol=symbol,
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=400.0,
            estimated_order_value=400.0,
            strategy_name="regime.daily.sma200_rotation.spy_shy.v1",
            strategy_stage="paper",
            strategy_config_path="configs/regime.yaml",
            submitted_by="operator",
            broker_order_id=None,
            broker_status=None,
            message=None,
        )
    )


def test_iter_trades_matches_list_trades_in_order(tmp_path):
    """iter_trades yields the same trades, in the same id-ASC order, as list_trades."""
    store = EventStore(tmp_path / "milodex.db")
    _seed_trade(store, symbol="SPY", side="buy")
    _seed_trade(store, symbol="QQQ", side="sell")
    _seed_trade(store, symbol="IWM", side="buy")

    streamed = list(store.iter_trades())
    listed = store.list_trades()

    assert [t.id for t in streamed] == [t.id for t in listed]
    assert [(t.symbol, t.side) for t in streamed] == [
        ("SPY", "buy"),
        ("QQQ", "sell"),
        ("IWM", "buy"),
    ]


def test_iter_trades_empty_yields_nothing(tmp_path):
    """No trades -> iter_trades yields an empty sequence (not an error)."""
    store = EventStore(tmp_path / "milodex.db")
    assert list(store.iter_trades()) == []


def test_iter_trades_returns_lazy_generator(tmp_path):
    """iter_trades must stream (generator), not materialize the full table like
    list_trades — this is the property that fixes the unbounded-load OOM
    (docs/incidents/2026-05-29-runner-fleet-oom-freeze.md)."""
    import inspect

    store = EventStore(tmp_path / "milodex.db")
    _seed_trade(store)
    assert inspect.isgenerator(store.iter_trades())


# ---------------------------------------------------------------------------
# Bounded paper-count aggregates
#
# count_paper_trades / count_paper_rejections replace the full-table
# list_trades() + list_explanations() loads in promotion-evidence assembly
# (_derive_paper_counts). They MUST preserve the exact predicates of the
# comprehensions they replace:
#   trades:       source = 'paper'  AND strategy_name = ?   (NO stage filter)
#   explanations: strategy_name = ? AND strategy_stage = 'paper' AND risk_allowed = 0
# ---------------------------------------------------------------------------


def _append_paper_trade(
    store: EventStore,
    *,
    strategy_name: str,
    source: str = "paper",
    strategy_stage: str = "paper",
) -> None:
    explanation_id = store.append_explanation(
        ExplanationEvent(**_explanation_kwargs(submitted_by="operator"))
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=datetime(2026, 5, 7, 14, 0, tzinfo=UTC),
            status="filled",
            source=source,
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=400.0,
            estimated_order_value=400.0,
            strategy_name=strategy_name,
            strategy_stage=strategy_stage,
            strategy_config_path="configs/x.yaml",
            submitted_by="operator",
            broker_order_id=None,
            broker_status=None,
            message=None,
        )
    )


def test_count_paper_trades_filters_on_source_and_strategy_not_stage(tmp_path):
    """Counts trades where source='paper' and strategy matches; source and
    strategy are filtered, stage is NOT (matching the replaced comprehension)."""
    store = EventStore(tmp_path / "milodex.db")
    _append_paper_trade(store, strategy_name="alpha", strategy_stage="paper")
    _append_paper_trade(
        store, strategy_name="alpha", strategy_stage="micro_live"
    )  # stage not filtered
    _append_paper_trade(store, strategy_name="alpha", source="backtest")  # excluded: source
    _append_paper_trade(store, strategy_name="beta")  # excluded: strategy

    assert store.count_paper_trades("alpha") == 2


def test_count_paper_rejections_filters_strategy_stage_and_risk_allowed(tmp_path):
    """Counts paper-stage explanations for the strategy where risk_allowed is
    False; strategy, stage, and risk_allowed are all filtered."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(
                submitted_by="operator",
                strategy_name="alpha",
                strategy_stage="paper",
                risk_allowed=False,
            )
        )
    )
    store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(
                submitted_by="operator",
                strategy_name="alpha",
                strategy_stage="paper",
                risk_allowed=True,  # excluded: allowed, not a rejection
            )
        )
    )
    store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(
                submitted_by="operator",
                strategy_name="alpha",
                strategy_stage="backtest",  # excluded: not paper stage
                risk_allowed=False,
            )
        )
    )
    store.append_explanation(
        ExplanationEvent(
            **_explanation_kwargs(
                submitted_by="operator",
                strategy_name="beta",  # excluded: different strategy
                strategy_stage="paper",
                risk_allowed=False,
            )
        )
    )

    assert store.count_paper_rejections("alpha") == 1


# ---------------------------------------------------------------------------
# count_recent_submitted_orders (duplicate-order durable backstop)
#
# R-P1-4: backtest fills are stamped recorded_at = wall-clock now, so without
# a source='paper' predicate a concurrent backtest on the same symbol makes
# the runner's duplicate-order check see thousands of "recent submitted
# orders" and spuriously veto legitimate paper intents.
# ---------------------------------------------------------------------------


def _append_dedup_trade(
    store: EventStore,
    *,
    recorded_at: datetime,
    symbol: str = "SPY",
    side: str = "buy",
    status: str = "submitted",
    source: str = "paper",
    broker_order_id: str | None = None,
    strategy_name: str | None = "alpha",
) -> None:
    explanation_id = store.append_explanation(
        ExplanationEvent(**_explanation_kwargs(submitted_by="operator", symbol=symbol, side=side))
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=recorded_at,
            status=status,
            source=source,
            symbol=symbol,
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=400.0,
            estimated_order_value=400.0,
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path="configs/x.yaml",
            submitted_by="operator",
            broker_order_id=broker_order_id,
            broker_status=None,
            message=None,
        )
    )


def test_count_recent_submitted_orders_scoped_to_strategy_excludes_others(tmp_path):
    """PR5: scoping to a strategy counts only that strategy's rows."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    _append_dedup_trade(store, recorded_at=now, strategy_name="alpha")
    _append_dedup_trade(store, recorded_at=now, strategy_name="beta")
    since = now - timedelta(seconds=60)

    assert (
        store.count_recent_submitted_orders(
            symbol="SPY", side="buy", since=since, strategy_name="alpha"
        )
        == 1
    )
    # Unscoped (default) still counts account-wide — both rows.
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 2


def test_count_recent_submitted_orders_operator_scope_excludes_strategy_rows(tmp_path):
    """PR5: strategy_name=None scopes to operator-attributed (NULL) rows only."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    _append_dedup_trade(store, recorded_at=now, strategy_name="alpha")
    _append_dedup_trade(store, recorded_at=now, strategy_name=None)  # operator
    since = now - timedelta(seconds=60)

    assert (
        store.count_recent_submitted_orders(
            symbol="SPY", side="buy", since=since, strategy_name=None
        )
        == 1
    )


def test_count_recent_submitted_orders_excludes_backtest_rows(tmp_path):
    """Concurrent-backtest non-veto: backtest rows never count (R-P1-4)."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    _append_dedup_trade(store, recorded_at=now, source="backtest")
    _append_dedup_trade(store, recorded_at=now, source="backtest")

    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=now) == 0


def test_count_recent_submitted_orders_counts_paper_rows_in_window(tmp_path):
    """Paper submitted rows inside the window count; symbol/side filtered."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    _append_dedup_trade(store, recorded_at=now)
    _append_dedup_trade(store, recorded_at=now, side="sell")  # excluded: side
    _append_dedup_trade(store, recorded_at=now, symbol="QQQ")  # excluded: symbol
    _append_dedup_trade(store, recorded_at=now, status="blocked")  # excluded: status

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 1


def test_count_recent_submitted_orders_window_is_enforced_in_sql(tmp_path):
    """Rows older than ``since`` are excluded by the SQL time predicate."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    _append_dedup_trade(store, recorded_at=now - timedelta(seconds=120))  # outside
    _append_dedup_trade(store, recorded_at=now - timedelta(seconds=30))  # inside

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 1


def test_count_recent_submitted_orders_treats_naive_timestamps_as_utc(tmp_path):
    """Semantics preserved from the prior Python comparison: naive == UTC."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    naive_inside = datetime(2026, 5, 7, 13, 59, 30)  # 30 s before now, no tzinfo
    _append_dedup_trade(store, recorded_at=naive_inside)

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 1


# ---------------------------------------------------------------------------
# execution_attempts outbox (P1-02) — durable attempt row before broker submit,
# atomic explanation+trade, hardened duplicate-order count, stale-pending sweep
# ---------------------------------------------------------------------------


def _attempt_event(**overrides) -> ExecutionAttemptEvent:
    """Build a minimal valid ExecutionAttemptEvent for outbox tests."""
    base = {
        "client_order_id": "coid-1",
        "symbol": "SPY",
        "side": "buy",
        "quantity": 1.0,
        "order_type": "market",
        "created_at": datetime(2026, 5, 7, 14, 0, tzinfo=UTC),
        "status": "pending",
        "strategy_name": "alpha",
        "strategy_config_path": "configs/x.yaml",
        "session_id": "sess-1",
    }
    base.update(overrides)
    return ExecutionAttemptEvent(**base)


def test_execution_attempt_lifecycle_pending_to_submitted(tmp_path):
    """Happy path: pending row, then finalized 'submitted' with broker_order_id."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_execution_attempt(_attempt_event())

    [pending] = store.list_execution_attempts()
    assert pending.status == "pending"
    assert pending.broker_order_id is None
    assert pending.finalized_at is None

    finalized_at = datetime(2026, 5, 7, 14, 0, 2, tzinfo=UTC)
    store.finalize_execution_attempt(
        client_order_id="coid-1",
        status="submitted",
        finalized_at=finalized_at,
        broker_order_id="broker-1",
    )

    [attempt] = store.list_execution_attempts()
    assert attempt.status == "submitted"
    assert attempt.broker_order_id == "broker-1"
    assert attempt.finalized_at == finalized_at
    assert attempt.failure_detail is None


def test_finalize_execution_attempt_records_failure_detail(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_execution_attempt(_attempt_event())

    store.finalize_execution_attempt(
        client_order_id="coid-1",
        status="rejected",
        finalized_at=datetime(2026, 5, 7, 14, 0, 1, tzinfo=UTC),
        failure_detail="potential wash trade detected (40310000)",
    )

    [attempt] = store.list_execution_attempts()
    assert attempt.status == "rejected"
    assert "wash trade" in attempt.failure_detail


def test_finalize_execution_attempt_requires_pending_row(tmp_path):
    """Finalize is exactly-once: unknown ids and re-finalization both raise."""
    store = EventStore(tmp_path / "milodex.db")

    with pytest.raises(ValueError, match="No pending execution attempt"):
        store.finalize_execution_attempt(
            client_order_id="missing",
            status="submitted",
            finalized_at=datetime.now(tz=UTC),
        )

    store.append_execution_attempt(_attempt_event())
    store.finalize_execution_attempt(
        client_order_id="coid-1",
        status="submitted",
        finalized_at=datetime.now(tz=UTC),
        broker_order_id="broker-1",
    )
    with pytest.raises(ValueError, match="No pending execution attempt"):
        store.finalize_execution_attempt(
            client_order_id="coid-1",
            status="error",
            finalized_at=datetime.now(tz=UTC),
        )


def test_finalize_execution_attempt_rejects_invalid_status(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_execution_attempt(_attempt_event())

    with pytest.raises(ValueError, match="Invalid execution-attempt terminal status"):
        store.finalize_execution_attempt(
            client_order_id="coid-1",
            status="pending",
            finalized_at=datetime.now(tz=UTC),
        )


def _dedup_trade_event(*, explanation_id: int = 0, **overrides) -> TradeEvent:
    base = {
        "explanation_id": explanation_id,
        "recorded_at": datetime(2026, 5, 7, 14, 0, tzinfo=UTC),
        "status": "submitted",
        "source": "paper",
        "symbol": "SPY",
        "side": "buy",
        "quantity": 1.0,
        "order_type": "market",
        "time_in_force": "day",
        "estimated_unit_price": 400.0,
        "estimated_order_value": 400.0,
        "strategy_name": "alpha",
        "strategy_stage": "paper",
        "strategy_config_path": "configs/x.yaml",
        "submitted_by": "operator",
        "broker_order_id": None,
        "broker_status": None,
        "message": None,
    }
    base.update(overrides)
    return TradeEvent(**base)


def test_append_explanation_and_trade_links_and_persists_both(tmp_path):
    """The trade row carries the explanation id inserted in the same txn."""
    store = EventStore(tmp_path / "milodex.db")

    explanation_id, trade_id = store.append_explanation_and_trade(
        explanation=ExplanationEvent(**_explanation_kwargs(submitted_by="operator")),
        # explanation_id=0 placeholder — the method overrides it.
        trade=_dedup_trade_event(),
    )

    [trade] = store.list_trades()
    assert trade.id == trade_id
    assert trade.explanation_id == explanation_id
    [explanation] = store.list_explanations()
    assert explanation.id == explanation_id


def test_append_explanation_and_trade_is_atomic_on_trade_failure(tmp_path):
    """Failure between the two inserts persists NEITHER row (one transaction)."""
    store = EventStore(tmp_path / "milodex.db")

    with pytest.raises(Exception):  # noqa: B017, PT011 — sqlite bind error type is incidental
        store.append_explanation_and_trade(
            explanation=ExplanationEvent(**_explanation_kwargs(submitted_by="operator")),
            # Unbindable quantity makes the SECOND insert fail after the
            # explanation insert succeeded inside the shared transaction.
            trade=_dedup_trade_event(quantity=object()),
        )

    assert store.list_explanations() == []
    assert store.list_trades() == []


def test_append_explanation_and_trade_enforces_dual_ancestor_rule(tmp_path):
    """The atomic path applies the same migration-008 check as append_explanation."""
    store = EventStore(tmp_path / "milodex.db")

    with pytest.raises(ValueError, match="must carry an ancestor"):
        store.append_explanation_and_trade(
            explanation=ExplanationEvent(**_explanation_kwargs(submitted_by="strategy_runner")),
            trade=_dedup_trade_event(),
        )

    assert store.list_explanations() == []
    assert store.list_trades() == []


def test_count_recent_submitted_orders_counts_submitted_attempt_without_trade(tmp_path):
    """P1-02 recovery surface: broker success recorded on the attempt, but the
    trade row was lost (crash before the atomic write) — must still veto."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    store.append_execution_attempt(
        _attempt_event(status="submitted", broker_order_id="ghost-1", created_at=now)
    )

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 1


def test_count_recent_submitted_orders_counts_pending_attempt(tmp_path):
    """A pending attempt (in-flight or crashed mid-submit) counts — fail-safe."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    store.append_execution_attempt(_attempt_event(created_at=now))

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 1


def test_count_recent_submitted_orders_does_not_double_count_attempt_with_trade(tmp_path):
    """A fully-recorded submit (attempt + trade sharing broker_order_id) counts
    once: the trades subquery counts it, the attempts subquery excludes it."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    store.append_execution_attempt(
        _attempt_event(status="submitted", broker_order_id="b-1", created_at=now)
    )
    _append_dedup_trade(store, recorded_at=now, broker_order_id="b-1")

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 1


def test_count_recent_submitted_orders_ignores_rejected_attempts(tmp_path):
    """A broker rejection is a definitive no-order outcome — the only
    attempt state excluded from the dedup count."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    store.append_execution_attempt(
        _attempt_event(client_order_id="coid-r", status="rejected", created_at=now)
    )

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 0


def test_count_recent_submitted_orders_counts_error_attempts(tmp_path):
    """An 'error' attempt (unexpected broker exception, e.g. a timeout) can
    fire AFTER the order reached the broker — delivery is unknown, so the
    fail-safe is to veto."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    store.append_execution_attempt(
        _attempt_event(client_order_id="coid-e", status="error", created_at=now)
    )

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 1


def test_count_recent_submitted_orders_filters_attempts_by_symbol_side_window(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    store.append_execution_attempt(
        _attempt_event(client_order_id="coid-sym", symbol="QQQ", created_at=now)
    )
    store.append_execution_attempt(
        _attempt_event(client_order_id="coid-side", side="sell", created_at=now)
    )
    store.append_execution_attempt(
        _attempt_event(client_order_id="coid-old", created_at=now - timedelta(seconds=120))
    )

    since = now - timedelta(seconds=60)
    assert store.count_recent_submitted_orders(symbol="SPY", side="buy", since=since) == 0


def test_list_stale_pending_execution_attempts(tmp_path):
    """Old 'pending' rows are listed; fresh pending and finalized rows are not."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime.now(tz=UTC)
    store.append_execution_attempt(
        _attempt_event(client_order_id="coid-stale", created_at=now - timedelta(minutes=30))
    )
    store.append_execution_attempt(_attempt_event(client_order_id="coid-fresh", created_at=now))
    store.append_execution_attempt(
        _attempt_event(
            client_order_id="coid-done",
            status="submitted",
            broker_order_id="b-2",
            created_at=now - timedelta(minutes=30),
        )
    )

    stale = store.list_stale_pending_execution_attempts()
    assert [attempt.client_order_id for attempt in stale] == ["coid-stale"]


def test_get_latest_open_session_id_returns_none_when_no_open_run(tmp_path):
    """HR-13 item 14: returns None when no open run exists for the strategy."""
    store = EventStore(tmp_path / "milodex.db")
    result = store.get_latest_open_session_id("regime.daily.sma200_rotation.spy_shy.v1")
    assert result is None


def test_get_latest_open_session_id_returns_open_session(tmp_path):
    """HR-13 item 14: returns the session_id of the latest open run."""
    store = EventStore(tmp_path / "milodex.db")
    t0 = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    strategy_id = "regime.daily.sma200_rotation.spy_shy.v1"

    store.append_strategy_run(
        StrategyRunEvent(
            session_id="session-open",
            strategy_id=strategy_id,
            started_at=t0,
            ended_at=None,
            exit_reason=None,
            metadata={},
        )
    )
    result = store.get_latest_open_session_id(strategy_id)
    assert result == "session-open"


def test_get_latest_open_session_id_ignores_closed_runs(tmp_path):
    """HR-13 item 14: closed runs are excluded; None returned when all runs are closed."""
    store = EventStore(tmp_path / "milodex.db")
    t0 = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    strategy_id = "regime.daily.sma200_rotation.spy_shy.v1"

    store.append_strategy_run(
        StrategyRunEvent(
            session_id="session-closed",
            strategy_id=strategy_id,
            started_at=t0,
            ended_at=None,
            exit_reason=None,
            metadata={},
        )
    )
    store.update_strategy_run_end(
        session_id="session-closed",
        ended_at=t0,
        exit_reason="controlled_stop",
    )
    result = store.get_latest_open_session_id(strategy_id)
    assert result is None


def test_get_latest_open_session_id_returns_latest_of_multiple_open(tmp_path):
    """HR-13 item 14: when multiple open runs exist (e.g. post-crash orphans), return the latest."""
    store = EventStore(tmp_path / "milodex.db")
    t0 = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    strategy_id = "regime.daily.sma200_rotation.spy_shy.v1"

    # Two open runs; the second (session-b) was inserted later → higher id.
    store.append_strategy_run(
        StrategyRunEvent(
            session_id="session-a",
            strategy_id=strategy_id,
            started_at=t0,
            ended_at=None,
            exit_reason=None,
            metadata={},
        )
    )
    store.append_strategy_run(
        StrategyRunEvent(
            session_id="session-b",
            strategy_id=strategy_id,
            started_at=t0,
            ended_at=None,
            exit_reason=None,
            metadata={},
        )
    )
    result = store.get_latest_open_session_id(strategy_id)
    assert result == "session-b"

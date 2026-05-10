"""Tests for the SQLite-backed event store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.core.event_store import (
    MIN_COMPATIBLE_SCHEMA_VERSION,
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    KillSwitchEvent,
    OrchestrationBatchEvent,
    OrchestrationJobEvent,
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
    assert MIN_COMPATIBLE_SCHEMA_VERSION == 9


def test_event_store_applies_initial_schema(tmp_path):
    db_path = tmp_path / "data" / "milodex.db"

    store = EventStore(db_path)

    assert db_path.exists()
    assert store.schema_version == 9
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
        "orchestration_batches",
        "orchestration_jobs",
    }.issubset(set(store.list_table_names()))


def test_orchestration_ledger_schema_has_adr_0040_tables_and_indexes(tmp_path):
    import sqlite3

    db_path = tmp_path / "milodex.db"
    store = EventStore(db_path)

    assert store.schema_version == 9
    with sqlite3.connect(db_path) as con:
        batch_columns = {
            row[1] for row in con.execute("PRAGMA table_info(orchestration_batches)")
        }
        job_columns = {
            row[1] for row in con.execute("PRAGMA table_info(orchestration_jobs)")
        }
        index_names = {
            row[1] for row in con.execute("PRAGMA index_list(orchestration_jobs)")
        } | {
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

    # Open with the live EventStore — this triggers migration 008.
    store = EventStore(db_path)
    assert store.schema_version == 9

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
    assert store2.schema_version == 9

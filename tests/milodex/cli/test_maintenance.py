"""CLI integration tests for ``milodex maintenance compact``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from milodex.cli.formatter import JSON_SCHEMA_VERSION
from milodex.cli.main import main as cli_entrypoint
from milodex.core.advisory_lock import AdvisoryLock
from milodex.core.event_store import BacktestRunEvent, EventStore, ExplanationEvent

_TS = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)


def _no_broker():
    raise AssertionError("maintenance must not need a broker")


def _refuse_data_provider():
    raise AssertionError("maintenance must not need a data provider")


def _run(argv: list[str], tmp_path: Path) -> tuple[int, StringIO, StringIO]:
    out, err = StringIO(), StringIO()
    code = cli_entrypoint(
        argv,
        event_store_factory=lambda: EventStore(tmp_path / "milodex.db"),
        broker_factory=_no_broker,
        data_provider_factory=_refuse_data_provider,
        locks_dir=tmp_path / "locks",
        stdout=out,
        stderr=err,
    )
    return code, out, err


def _seed_prunable_backtest_explanation(tmp_path: Path, n: int = 2) -> None:
    store = EventStore(tmp_path / "milodex.db")
    run = store.append_backtest_run(
        BacktestRunEvent(
            run_id="r",
            strategy_id="s",
            config_path="c",
            config_hash="h",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 12, 31, tzinfo=UTC),
            started_at=_TS,
            status="completed",
            slippage_pct=0.0,
            commission_per_trade=0.0,
            metadata={},
        )
    )
    for _ in range(n):
        store.append_explanation(
            ExplanationEvent(
                recorded_at=_TS,
                decision_type="no_trade",
                status="no_signal",
                strategy_name="s",
                strategy_stage="backtest",
                strategy_config_path="c",
                config_hash="h",
                symbol="SPY",
                side="buy",
                quantity=0.0,
                order_type="market",
                time_in_force="day",
                submitted_by="backtest_engine",
                market_open=True,
                latest_bar_timestamp=None,
                latest_bar_close=None,
                account_equity=0.0,
                account_cash=0.0,
                account_portfolio_value=0.0,
                account_daily_pnl=0.0,
                risk_allowed=True,
                risk_summary="ok",
                reason_codes=[],
                risk_checks=[],
                context={},
                backtest_run_id=run,
            )
        )


def _prunable_count(tmp_path: Path) -> int:
    return EventStore(tmp_path / "milodex.db").count_prunable_backtest_explanations()


def test_compact_dry_run_reports_counts_without_apply(tmp_path: Path) -> None:
    _seed_prunable_backtest_explanation(tmp_path, n=3)
    code, out, _ = _run(["maintenance", "compact", "--json"], tmp_path)
    assert code == 0
    data = json.loads(out.getvalue())["data"]
    assert data["applied"] is False
    assert data["prunable_explanations"] == 3
    assert _prunable_count(tmp_path) == 3  # nothing pruned


def test_compact_apply_prunes_and_vacuums(tmp_path: Path) -> None:
    _seed_prunable_backtest_explanation(tmp_path, n=3)
    code, out, _ = _run(["maintenance", "compact", "--apply", "--no-backup", "--json"], tmp_path)
    assert code == 0
    data = json.loads(out.getvalue())["data"]
    assert data["applied"] is True
    assert data["pruned_explanations"] == 3
    assert data["vacuumed"] is True
    assert _prunable_count(tmp_path) == 0


def test_compact_refuses_under_advisory_lock(tmp_path: Path) -> None:
    _seed_prunable_backtest_explanation(tmp_path, n=1)
    holder = AdvisoryLock(
        "milodex.runtime", locks_dir=tmp_path / "locks", holder_name="other_process"
    )
    holder.acquire()
    try:
        code, _, err = _run(["maintenance", "compact", "--apply", "--json"], tmp_path)
        assert code == 1
        assert json.loads(err.getvalue())["errors"][0]["code"] == "advisory_lock_held"
    finally:
        holder.release()


def test_compact_json_contract(tmp_path: Path) -> None:
    _seed_prunable_backtest_explanation(tmp_path, n=1)
    code, out, _ = _run(["maintenance", "compact", "--json"], tmp_path)
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["schema_version"] == JSON_SCHEMA_VERSION
    assert payload["command"] == "maintenance.compact"
    assert "prunable_explanations" in payload["data"]

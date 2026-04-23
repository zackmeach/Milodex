"""CLI integration tests for ``milodex analytics``.

Covers:
  * ``--strategy`` resolves to the latest backtest run for that strategy.
  * positional run_id and ``--strategy`` are mutually exclusive.
  * ``export`` happy path produces CSVs named after the resolved run id.
  * ``metrics`` / ``trades`` / ``compare`` happy paths via ``cli_entrypoint``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import (
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    TradeEvent,
)


def _refuse_broker():
    raise AssertionError("broker should not be needed by analytics")


def _refuse_data_provider():
    raise AssertionError("data provider should not be needed for these commands")


def _run(argv: list[str], tmp_path: Path) -> tuple[int, StringIO, StringIO]:
    out = StringIO()
    err = StringIO()
    exit_code = cli_entrypoint(
        argv,
        event_store_factory=lambda: EventStore(tmp_path / "milodex.db"),
        broker_factory=_refuse_broker,
        data_provider_factory=_refuse_data_provider,
        stdout=out,
        stderr=err,
    )
    return exit_code, out, err


def _append_explanation(store: EventStore, *, strategy: str, when: datetime) -> int:
    return store.append_explanation(
        ExplanationEvent(
            recorded_at=when,
            decision_type="preview",
            status="preview",
            strategy_name=strategy,
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash="fp-test",
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="test",
            market_open=True,
            latest_bar_timestamp=when,
            latest_bar_close=100.0,
            account_equity=100_000.0,
            account_cash=100_000.0,
            account_portfolio_value=100_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="ok",
            reason_codes=[],
            risk_checks=[],
            context={},
        )
    )


def _append_trade(
    store: EventStore,
    *,
    explanation_id: int,
    when: datetime,
    side: str,
    price: float,
    backtest_run_id: int,
    strategy: str,
) -> None:
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=when,
            status="submitted",
            source="backtest",
            symbol="SPY",
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=price,
            estimated_order_value=price,
            strategy_name=strategy,
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="test",
            broker_order_id=None,
            broker_status=None,
            message=None,
            backtest_run_id=backtest_run_id,
        )
    )


def _seed_run(
    store: EventStore,
    *,
    run_id: str,
    strategy_id: str,
    trade_pairs: int = 3,
    start_offset_days: int = 0,
) -> int:
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=start_offset_days)
    end = start + timedelta(days=60)
    equity_curve = [
        ((start + timedelta(days=i)).date().isoformat(), 100_000.0 + i * 10.0) for i in range(60)
    ]
    db_id = store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=strategy_id,
            config_path="configs/test.yaml",
            config_hash="fp-test",
            start_date=start,
            end_date=end,
            started_at=start,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={"initial_equity": 100_000.0, "equity_curve": equity_curve},
        )
    )
    store.update_backtest_run_status(run_id, status="completed", ended_at=end)
    for i in range(trade_pairs):
        buy_at = start + timedelta(days=i * 2)
        sell_at = start + timedelta(days=i * 2 + 1)
        buy_exp = _append_explanation(store, strategy=strategy_id, when=buy_at)
        _append_trade(
            store,
            explanation_id=buy_exp,
            when=buy_at,
            side="buy",
            price=100.0 + i,
            backtest_run_id=db_id,
            strategy=strategy_id,
        )
        sell_exp = _append_explanation(store, strategy=strategy_id, when=sell_at)
        _append_trade(
            store,
            explanation_id=sell_exp,
            when=sell_at,
            side="sell",
            price=101.0 + i,
            backtest_run_id=db_id,
            strategy=strategy_id,
        )
    return db_id


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def test_analytics_metrics_happy_path(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _seed_run(store, run_id="bt-1", strategy_id="regime.v1")

    exit_code, out, _ = _run(["analytics", "metrics", "bt-1", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "success"
    assert payload["data"]["strategy"]["run_id"] == "bt-1"
    assert "profit_factor" in payload["data"]["strategy"]
    assert "max_drawdown_duration_days" in payload["data"]["strategy"]


def test_analytics_metrics_strategy_shortcut_resolves_latest(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _seed_run(store, run_id="bt-older", strategy_id="regime.v1")
    _seed_run(store, run_id="bt-newer", strategy_id="regime.v1", start_offset_days=90)

    exit_code, out, _ = _run(
        ["analytics", "metrics", "--strategy", "regime.v1", "--json"], tmp_path
    )
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["data"]["strategy"]["run_id"] == "bt-newer"


def test_analytics_metrics_strategy_with_no_runs_errors(tmp_path: Path) -> None:
    EventStore(tmp_path / "milodex.db")

    exit_code, _out, err = _run(
        ["analytics", "metrics", "--strategy", "nonexistent.v1", "--json"], tmp_path
    )
    assert exit_code != 0
    payload = json.loads(err.getvalue())
    assert payload["status"] == "error"
    assert "No backtest runs found" in payload["errors"][0]["message"]


def test_analytics_metrics_runid_and_strategy_mutually_exclusive(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _seed_run(store, run_id="bt-1", strategy_id="regime.v1")

    exit_code, _out, err = _run(
        ["analytics", "metrics", "bt-1", "--strategy", "regime.v1", "--json"],
        tmp_path,
    )
    assert exit_code != 0
    payload = json.loads(err.getvalue())
    assert payload["status"] == "error"
    assert "not both" in payload["errors"][0]["message"]


def test_analytics_metrics_requires_run_or_strategy(tmp_path: Path) -> None:
    EventStore(tmp_path / "milodex.db")

    exit_code, _out, err = _run(["analytics", "metrics", "--json"], tmp_path)
    assert exit_code != 0
    payload = json.loads(err.getvalue())
    assert "required" in payload["errors"][0]["message"].lower()


# ---------------------------------------------------------------------------
# trades
# ---------------------------------------------------------------------------


def test_analytics_trades_strategy_shortcut(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _seed_run(store, run_id="bt-1", strategy_id="regime.v1", trade_pairs=2)

    exit_code, out, _ = _run(["analytics", "trades", "--strategy", "regime.v1", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["data"]["run_id"] == "bt-1"
    assert payload["data"]["total"] == 4  # 2 pairs × 2 sides


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def test_analytics_compare_strategy_a_and_b(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _seed_run(store, run_id="bt-A", strategy_id="regime.v1")
    _seed_run(store, run_id="bt-B", strategy_id="mean_reversion.v1")

    exit_code, out, _ = _run(
        [
            "analytics",
            "compare",
            "--strategy-a",
            "regime.v1",
            "--strategy-b",
            "mean_reversion.v1",
            "--json",
        ],
        tmp_path,
    )
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["data"]["run_a"]["run_id"] == "bt-A"
    assert payload["data"]["run_b"]["run_id"] == "bt-B"


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_analytics_export_with_strategy_shortcut(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _seed_run(store, run_id="bt-export", strategy_id="regime.v1", trade_pairs=2)

    output_dir = tmp_path / "export"
    exit_code, _out, _err = _run(
        [
            "analytics",
            "export",
            "--strategy",
            "regime.v1",
            "--output",
            str(output_dir),
        ],
        tmp_path,
    )
    assert exit_code == 0
    assert (output_dir / "bt-export_trades.csv").exists()
    assert (output_dir / "bt-export_equity.csv").exists()

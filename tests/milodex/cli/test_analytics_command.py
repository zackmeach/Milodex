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

import pytest

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
    # Whole-period runs (the default fixture) carry the "whole_period" label.
    assert payload["data"]["strategy"]["result_type"] == "whole_period"


def _seed_walk_forward_run(
    store: EventStore,
    *,
    run_id: str,
    strategy_id: str,
    total_return_pct: float,
    sharpe: float,
    max_drawdown_pct: float,
    trading_days: int,
    trade_pairs: int = 3,
) -> int:
    """Seed a walk-forward backtest_runs row with no equity_curve (windows reset)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=60)
    db_id = store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=strategy_id,
            config_path="configs/test.yaml",
            config_hash="fp-test-wf",
            start_date=start,
            end_date=end,
            started_at=start,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={
                "initial_equity": 100_000.0,
                "walk_forward": True,
                "oos_aggregate": {
                    "total_return_pct": total_return_pct,
                    "sharpe": sharpe,
                    "max_drawdown_pct": max_drawdown_pct,
                    "trading_days": trading_days,
                },
            },
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


def test_analytics_metrics_walk_forward_uses_oos_aggregate(tmp_path: Path) -> None:
    """Walk-forward run reports OOS-aggregate return / sharpe / drawdown / trading_days.

    Closes the §7 finding surfaced 2026-04-26: previously, walk-forward runs
    reported total_return_pct=0, sharpe=null, trading_days=0 because each
    OOS window resets equity. Now the metrics surface reads
    metadata["oos_aggregate"] and tags result_type="walk_forward".
    """
    store = EventStore(tmp_path / "milodex.db")
    _seed_walk_forward_run(
        store,
        run_id="bt-wf-1",
        strategy_id="meanrev.v1",
        total_return_pct=4.34,
        sharpe=0.327,
        max_drawdown_pct=6.41,
        trading_days=892,
    )

    exit_code, out, _ = _run(["analytics", "metrics", "bt-wf-1", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    strategy = payload["data"]["strategy"]
    assert strategy["result_type"] == "walk_forward"
    assert strategy["total_return_pct"] == pytest.approx(4.34)
    assert strategy["sharpe_ratio"] == pytest.approx(0.327)
    assert strategy["max_drawdown_pct"] == pytest.approx(6.41)
    assert strategy["trading_days"] == 892
    # CAGR is derived consistently from the OOS-aggregate inputs, not zero.
    assert strategy["cagr_pct"] is not None
    assert strategy["cagr_pct"] > 0
    # Trade-ledger metrics (computed from the seeded buys/sells) are still meaningful.
    assert strategy["trade_count"] > 0


def test_analytics_metrics_walk_forward_handles_null_oos_sharpe(tmp_path: Path) -> None:
    """A walk-forward run whose OOS-aggregate sharpe is null (the regime strategy:
    trade_count 0 / near-flat OOS) must not crash `analytics metrics` with
    float(None) (D-1). Sharpe renders as null, not a TypeError."""
    store = EventStore(tmp_path / "milodex.db")
    _seed_walk_forward_run(
        store,
        run_id="bt-wf-null",
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        total_return_pct=0.0,
        sharpe=None,
        max_drawdown_pct=0.0,
        trading_days=0,
    )

    exit_code, out, _ = _run(["analytics", "metrics", "bt-wf-null", "--json"], tmp_path)
    assert exit_code == 0
    strategy = json.loads(out.getvalue())["data"]["strategy"]
    assert strategy["result_type"] == "walk_forward"
    assert strategy["sharpe_ratio"] is None


@pytest.mark.parametrize(
    "argv",
    [
        ["analytics", "trades", "bt-x", "--limit", "-1"],
        ["analytics", "trades", "bt-x", "--limit", "0"],
        ["analytics", "list", "--limit", "-5"],
        ["analytics", "list", "--limit", "0"],
    ],
)
def test_analytics_rejects_non_positive_limit(tmp_path: Path, argv: list[str]) -> None:
    """A non-positive --limit fails loudly instead of silently dropping rows via a
    negative/empty slice (A-7)."""
    exit_code, _out, err = _run(argv, tmp_path)
    assert exit_code == 1
    assert "limit" in err.getvalue().lower()


def test_analytics_list_accepts_limit_one(tmp_path: Path) -> None:
    """Boundary: limit == 1 is accepted (the guard rejects < 1, not <= 1)."""
    exit_code, _out, _err = _run(["analytics", "list", "--limit", "1"], tmp_path)
    assert exit_code == 0


def test_analytics_metrics_walk_forward_human_lines_label_oos(tmp_path: Path) -> None:
    """Human output flags walk-forward results so an operator can't misread them."""
    store = EventStore(tmp_path / "milodex.db")
    _seed_walk_forward_run(
        store,
        run_id="bt-wf-2",
        strategy_id="meanrev.v1",
        total_return_pct=2.0,
        sharpe=0.1,
        max_drawdown_pct=1.0,
        trading_days=200,
    )
    exit_code, out, _ = _run(["analytics", "metrics", "bt-wf-2"], tmp_path)
    assert exit_code == 0
    text = out.getvalue()
    assert "walk-forward" in text.lower()


def test_analytics_metrics_walk_forward_labels_each_oos_derived_metric(tmp_path: Path) -> None:
    """Per-metric labels distinguish OOS-aggregate values from equity-curve values.

    Closes P-1 (PHASE2_PLANNING.md) option (a): each OOS-aggregate metric in
    the walk-forward report gets a per-line label so an operator reading
    "Total return: +2.00%" cannot mistake it for whole-period equity-curve
    return when it is in fact the OOS-aggregate stitched across windows.
    """
    store = EventStore(tmp_path / "milodex.db")
    _seed_walk_forward_run(
        store,
        run_id="bt-wf-pm",
        strategy_id="meanrev.v1",
        total_return_pct=4.34,
        sharpe=0.327,
        max_drawdown_pct=6.41,
        trading_days=752,
    )
    exit_code, out, _ = _run(["analytics", "metrics", "bt-wf-pm"], tmp_path)
    assert exit_code == 0
    text = out.getvalue()

    for line_prefix in ("Trading days:", "Total return:", "Max drawdown:", "Sharpe:"):
        line = next((line for line in text.splitlines() if line_prefix in line), None)
        assert line is not None, f"missing line for {line_prefix!r}"
        assert "OOS" in line or "walk-forward" in line.lower(), (
            f"line {line!r} (prefix {line_prefix!r}) must carry an OOS / walk-forward "
            f"label so an operator cannot misread it as whole-period equity-curve output"
        )


def test_analytics_metrics_walk_forward_clears_sortino_from_broken_equity_curve(
    tmp_path: Path,
) -> None:
    """Sortino is derived from the equity curve; for walk-forward it must be n/a.

    Each OOS window resets equity, so the running equity curve is fragmented
    and Sortino computed from it is meaningless. Surface it as `None`/`n/a`
    rather than letting the broken-curve number leak into the report.
    """
    store = EventStore(tmp_path / "milodex.db")
    _seed_walk_forward_run(
        store,
        run_id="bt-wf-sortino",
        strategy_id="meanrev.v1",
        total_return_pct=4.34,
        sharpe=0.327,
        max_drawdown_pct=6.41,
        trading_days=752,
    )
    exit_code, out, _ = _run(["analytics", "metrics", "bt-wf-sortino", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    strategy = payload["data"]["strategy"]

    assert strategy["result_type"] == "walk_forward"
    assert strategy["sortino_ratio"] is None, (
        "walk-forward must clear sortino_ratio (equity curve is fragmented "
        "across OOS windows; the broken-curve value is meaningless)"
    )


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
    # R-ANA-004: report export to CSV via the analytics CLI command.
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


def test_analytics_export_json_format(tmp_path: Path) -> None:
    # R-ANA-004: report export to JSON via the analytics CLI command.
    store = EventStore(tmp_path / "milodex.db")
    _seed_run(store, run_id="bt-json", strategy_id="regime.v1", trade_pairs=2)

    output_dir = tmp_path / "export-json"
    exit_code, _out, _err = _run(
        [
            "analytics",
            "export",
            "bt-json",
            "--output",
            str(output_dir),
            "--format",
            "json",
        ],
        tmp_path,
    )
    assert exit_code == 0
    trades_path = output_dir / "bt-json_trades.json"
    equity_path = output_dir / "bt-json_equity.json"
    assert trades_path.exists()
    assert equity_path.exists()

    trades_payload = json.loads(trades_path.read_text(encoding="utf-8"))
    assert isinstance(trades_payload, list)
    assert len(trades_payload) == 4  # 2 pairs × 2 sides
    assert {"recorded_at", "symbol", "side", "quantity"} <= set(trades_payload[0].keys())

    equity_payload = json.loads(equity_path.read_text(encoding="utf-8"))
    assert isinstance(equity_payload, list)
    assert equity_payload and {"date", "portfolio_value"} <= set(equity_payload[0].keys())


def test_analytics_export_markdown_format(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _seed_run(store, run_id="bt-md", strategy_id="regime.v1", trade_pairs=2)

    output_dir = tmp_path / "export-md"
    exit_code, _out, _err = _run(
        [
            "analytics",
            "export",
            "bt-md",
            "--output",
            str(output_dir),
            "--format",
            "md",
        ],
        tmp_path,
    )
    assert exit_code == 0
    report_path = output_dir / "bt-md_report.md"
    assert report_path.exists()

    body = report_path.read_text(encoding="utf-8")
    assert "## Metrics" in body
    assert "## Trades" in body
    assert "## Equity Curve" in body
    assert "| metric | value | confidence |" in body
    assert "| date | symbol | side | qty | price |" in body
    assert "| date | value |" in body

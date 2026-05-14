"""Tests for the backtest CLI command's uncertainty labeling.

Covers R-CLI-014 (statistical-insufficiency labeling below 30 trades)
and R-PRM-004 (regime-family exemption — operational evidence basis).
"""

from __future__ import annotations

import argparse
from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from milodex.backtesting.engine import BacktestResult
from milodex.cli.commands import backtest as backtest_command
from milodex.cli.commands.backtest import _build_backtest_result
from milodex.data.models import BarSet
from milodex.risk import RiskPolicy


def _result(strategy_id: str, trade_count: int) -> BacktestResult:
    return BacktestResult(
        run_id="run-1",
        strategy_id=strategy_id,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 3, 31),
        initial_equity=10_000.0,
        final_equity=10_250.0,
        total_return_pct=2.5,
        trade_count=trade_count,
        buy_count=trade_count // 2,
        sell_count=trade_count - trade_count // 2,
        slippage_pct=0.001,
        commission_per_trade=0.0,
        trading_days=60,
    )


def test_statistical_strategy_below_threshold_flagged_insufficient():
    result = _build_backtest_result(
        _result("meanrev.daily.rsi2pullback.v1", trade_count=12),
    )
    assert result.data["uncertainty_label"] == "insufficient evidence"
    assert "12 < 30" in result.data["uncertainty_reason"]
    assert any("insufficient evidence" in ln for ln in result.human_lines)


def test_statistical_strategy_at_or_above_threshold_not_flagged():
    result = _build_backtest_result(
        _result("meanrev.daily.rsi2pullback.v1", trade_count=30),
    )
    assert "uncertainty_label" not in result.data
    assert not any("insufficient" in ln for ln in result.human_lines)


def test_regime_strategy_gets_operational_evidence_basis():
    result = _build_backtest_result(
        _result("regime.daily.sma200_rotation.spy_shy.v1", trade_count=2),
    )
    assert result.data["evidence_basis"] == "operational"
    assert "uncertainty_label" not in result.data
    assert any("operational" in ln for ln in result.human_lines)


def test_regime_strategy_exempt_even_at_high_trade_count():
    """Regime never gets a statistical label, regardless of trade count."""
    result = _build_backtest_result(
        _result("regime.daily.sma200_rotation.spy_shy.v1", trade_count=500),
    )
    assert result.data["evidence_basis"] == "operational"
    assert "uncertainty_label" not in result.data


def test_backtest_cli_defaults_to_bypass_risk_policy():
    captured = {}
    ctx = MagicMock()

    def get_engine(strategy_id, **kwargs):
        captured["strategy_id"] = strategy_id
        captured["kwargs"] = kwargs
        engine = MagicMock()
        engine.run.return_value = _result("meanrev.daily.rsi2pullback.v1", trade_count=30)
        return engine

    ctx.get_backtest_engine = get_engine
    args = _args(risk_policy="bypass")

    result = backtest_command.run(args, ctx)

    assert captured["kwargs"]["risk_policy"] is RiskPolicy.BYPASS
    assert result.data["risk_policy"] == "bypass"
    assert any("Risk policy:   bypass" in line for line in result.human_lines)


def test_backtest_cli_passes_enforce_risk_policy():
    captured = {}
    ctx = MagicMock()

    def get_engine(_strategy_id, **kwargs):
        captured["kwargs"] = kwargs
        engine = MagicMock()
        engine.run.return_value = _result("meanrev.daily.rsi2pullback.v1", trade_count=30)
        engine.run.return_value.risk_policy = RiskPolicy.ENFORCE
        return engine

    ctx.get_backtest_engine = get_engine
    args = _args(risk_policy="enforce")

    result = backtest_command.run(args, ctx)

    assert captured["kwargs"]["risk_policy"] is RiskPolicy.ENFORCE
    assert result.data["risk_policy"] == "enforce"
    assert any("Risk policy:   enforce" in line for line in result.human_lines)


def test_backtest_parser_rejects_invalid_risk_policy():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    backtest_command.register(subparsers)

    try:
        parser.parse_args(
            [
                "backtest",
                "meanrev.daily.rsi2pullback.v1",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
                "--risk-policy",
                "definitely-not-a-policy",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - argparse must exit for invalid choices
        raise AssertionError("invalid --risk-policy value should be rejected")


def test_walk_forward_cli_reuses_prefetched_bars(monkeypatch):
    start = date(2024, 1, 2)
    bars = {
        "SPY": BarSet(
            pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp(start, tz="UTC") + pd.Timedelta(days=i),
                        "open": 100.0,
                        "high": 100.0,
                        "low": 100.0,
                        "close": 100.0,
                        "volume": 1000,
                        "vwap": 100.0,
                    }
                    for i in range(10)
                ]
            )
        )
    }
    engine = MagicMock()
    engine._loaded.config.backtest = {"walk_forward_windows": 1}
    engine.prefetch_bars.return_value = bars
    ctx = MagicMock()
    ctx.get_backtest_engine.return_value = engine
    captured = {}

    def fake_run_walk_forward(*_args, **kwargs):
        captured["all_bars"] = kwargs.get("all_bars")
        from milodex.backtesting.walk_forward_runner import (
            WalkForwardResult,
            WalkForwardStability,
        )

        return WalkForwardResult(
            run_id="wf-1",
            strategy_id="meanrev.daily.rsi2pullback.v1",
            start_date=start,
            end_date=date(2024, 1, 11),
            initial_equity=10_000.0,
            train_days=5,
            test_days=5,
            step_days=5,
            windows=[],
            oos_trade_count=0,
            oos_trading_days=0,
            oos_total_return_pct=0.0,
            oos_sharpe=None,
            oos_max_drawdown_pct=0.0,
            oos_equity_curve=[],
            stability=WalkForwardStability(None, None, None, 0, 0, False),
            risk_policy=RiskPolicy.BYPASS,
        )

    monkeypatch.setattr(backtest_command, "run_walk_forward", fake_run_walk_forward)
    args = _args(risk_policy="bypass")
    args.walk_forward = True

    backtest_command.run(args, ctx)

    assert captured["all_bars"] is bars


def _args(*, risk_policy: str) -> argparse.Namespace:
    return argparse.Namespace(
        strategy_id="meanrev.daily.rsi2pullback.v1",
        start="2024-01-01",
        end="2024-01-31",
        slippage=None,
        initial_equity=10_000.0,
        walk_forward=False,
        run_id=None,
        risk_policy=risk_policy,
    )

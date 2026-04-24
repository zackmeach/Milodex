"""Tests for the backtest CLI command's uncertainty labeling.

Covers R-CLI-014 (statistical-insufficiency labeling below 30 trades)
and R-PRM-004 (regime-family exemption — operational evidence basis).
"""

from __future__ import annotations

from datetime import date

from milodex.backtesting.engine import BacktestResult
from milodex.cli.commands.backtest import _build_backtest_result


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

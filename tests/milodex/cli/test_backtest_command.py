"""Tests for the backtest CLI command's uncertainty labeling.

Covers R-CLI-014 (statistical-insufficiency labeling below 30 trades)
and R-PRM-004 (regime-family exemption — operational evidence basis).
"""

from __future__ import annotations

import argparse
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

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


def test_statistical_strategy_uses_configured_trade_floor_for_uncertainty():
    result = _build_backtest_result(
        _result("momentum.daily.dual_absolute.gem_weekly.v1", trade_count=20),
        min_trade_count=20,
    )

    assert "uncertainty_label" not in result.data
    assert not any("insufficient" in ln for ln in result.human_lines)


def test_statistical_strategy_configured_trade_floor_appears_in_uncertainty_reason():
    result = _build_backtest_result(
        _result("momentum.daily.dual_absolute.gem_weekly.v1", trade_count=19),
        min_trade_count=20,
    )

    assert result.data["uncertainty_label"] == "insufficient evidence"
    assert "19 < 20" in result.data["uncertainty_reason"]


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


def _engine_with_backtest_config(backtest: dict) -> SimpleNamespace:
    return SimpleNamespace(_loaded=SimpleNamespace(config=SimpleNamespace(backtest=backtest)))


def test_min_trade_count_from_engine_treats_null_config_as_default():
    """A config with `min_trades_required: null` (the regime R-PRM-004 exemption)
    must not crash plain backtest with int(None); it falls back to the statistical
    default (A-5)."""
    engine = _engine_with_backtest_config({"min_trades_required": None})
    assert backtest_command._min_trade_count_from_engine(engine) == 30


def test_min_trade_count_from_engine_uses_configured_value():
    """A real integer floor (including 0) is preserved, not collapsed to the default."""
    assert (
        backtest_command._min_trade_count_from_engine(
            _engine_with_backtest_config({"min_trades_required": 20})
        )
        == 20
    )
    assert (
        backtest_command._min_trade_count_from_engine(
            _engine_with_backtest_config({"min_trades_required": 0})
        )
        == 0
    )


@pytest.mark.parametrize("bad", [-0.5, -0.001, 50.0, float("nan")])
def test_backtest_rejects_invalid_slippage(bad):
    """Negative / implausibly-large / nan slippage is rejected BEFORE engine
    construction, so no return-inflating backtest_runs row is ever persisted (A-4)."""
    ctx = MagicMock()
    args = _args(risk_policy="bypass")
    args.slippage = bad

    with pytest.raises(ValueError, match="slippage"):
        backtest_command.run(args, ctx)

    ctx.get_backtest_engine.assert_not_called()


@pytest.mark.parametrize("good", [0.0, 0.002])
def test_backtest_accepts_valid_slippage(good):
    """The lower bound is inclusive of 0.0 and accepts normal fractional slippage."""
    captured = {}

    def get_engine(strategy_id, **kwargs):
        captured["kwargs"] = kwargs
        engine = MagicMock()
        engine.run.return_value = _result("meanrev.daily.rsi2pullback.v1", trade_count=30)
        return engine

    ctx = MagicMock()
    ctx.get_backtest_engine = get_engine
    args = _args(risk_policy="bypass")
    args.slippage = good

    backtest_command.run(args, ctx)

    assert captured["kwargs"]["slippage_pct"] == good


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


def test_backtest_output_exposes_skipped_count():
    backtest_result = _result("meanrev.daily.rsi2pullback.v1", trade_count=30)
    backtest_result.skipped_count = 3

    result = _build_backtest_result(backtest_result)

    assert result.data["skipped_count"] == 3
    assert any("Skipped orders: 3" in line for line in result.human_lines)


def test_backtest_output_exposes_data_quality_report():
    backtest_result = _result("meanrev.daily.rsi2pullback.v1", trade_count=30)
    backtest_result.data_quality = {
        "status": "pass_with_warnings",
        "blocker_count": 0,
        "warning_count": 2,
        "issues": [],
    }

    result = _build_backtest_result(backtest_result)

    assert result.data["data_quality"]["status"] == "pass_with_warnings"
    assert any(
        "Data quality:  pass with warnings (2 warning(s))" in line for line in result.human_lines
    )


def _clamp_issue(symbol: str, first_bar_date: str) -> dict:
    return {
        "code": "requested_window_starts_after_requested_start",
        "severity": "warning",
        "symbol": symbol,
        "message": f"{symbol} starts materially after the requested backtest window.",
        "context": {
            "requested_start": "2020-01-01",
            "first_bar_date": first_bar_date,
            "tolerance_days": 7,
        },
    }


def test_clamped_window_surfaced_legibly_in_human_output():
    """A clamped window must name actual-vs-requested start, not hide in a warning count."""
    backtest_result = _result("momentum.daily.tsmom.curated_largecap.v1", trade_count=0)
    backtest_result.data_quality = {
        "status": "pass_with_warnings",
        "blocker_count": 0,
        "warning_count": 2,
        "issues": [_clamp_issue("AAPL", "2020-07-27"), _clamp_issue("SPY", "2020-08-03")],
    }

    result = _build_backtest_result(backtest_result)

    clamp_lines = [ln for ln in result.human_lines if ln.startswith("Window clamp:")]
    assert len(clamp_lines) == 1
    assert "2020-07-27" in clamp_lines[0]  # earliest actual start across affected symbols
    assert "2020-01-01" in clamp_lines[0]  # requested start
    assert "2 symbol(s)" in clamp_lines[0]


def test_no_window_clamp_line_when_window_not_clamped():
    backtest_result = _result("momentum.daily.tsmom.curated_largecap.v1", trade_count=40)
    backtest_result.data_quality = {
        "status": "pass_with_warnings",
        "blocker_count": 0,
        "warning_count": 1,
        "issues": [
            {
                "code": "requested_window_coverage_below_98pct",
                "severity": "warning",
                "symbol": "AAPL",
                "message": "AAPL has 97.0% requested-window bar coverage.",
                "context": {"coverage_pct": 97.0},
            }
        ],
    }

    result = _build_backtest_result(backtest_result)

    assert not any(ln.startswith("Window clamp:") for ln in result.human_lines)


def test_backtest_absent_data_quality_reports_not_recorded():
    """A legacy run with no scanner output must not render as a clean pass (P2-21)."""
    backtest_result = _result("meanrev.daily.rsi2pullback.v1", trade_count=30)
    backtest_result.data_quality = {}  # engine default; legacy runs predate the scanner

    result = _build_backtest_result(backtest_result)

    assert result.data["data_quality"]["status"] == "not_recorded"
    assert result.data["data_quality"]["blocker_count"] is None
    assert any("Data quality:  not recorded" in line for line in result.human_lines)
    assert not any("Data quality:  pass" in line for line in result.human_lines)


def test_backtest_none_data_quality_reports_not_recorded():
    backtest_result = _result("meanrev.daily.rsi2pullback.v1", trade_count=30)
    backtest_result.data_quality = None

    result = _build_backtest_result(backtest_result)

    assert result.data["data_quality"]["status"] == "not_recorded"
    assert any("Data quality:  not recorded" in line for line in result.human_lines)


def test_data_quality_label_does_not_default_missing_status_to_pass():
    # A payload without a status key must not be labeled "pass".
    assert backtest_command._data_quality_label({"warning_count": 0}) == "not recorded"


def test_backtest_output_exposes_run_manifest():
    backtest_result = _result("meanrev.daily.rsi2pullback.v1", trade_count=30)
    backtest_result.run_manifest = {
        "schema_version": 1,
        "strategy": {"config_hash": "abc123"},
        "code": {"commit": "deadbeef", "dirty": False, "available": True},
    }

    result = _build_backtest_result(backtest_result)

    assert result.data["run_manifest"]["schema_version"] == 1
    assert result.data["run_manifest"]["strategy"]["config_hash"] == "abc123"


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
    # ``walk_forward_windows`` is now a public property on ``BacktestEngine``
    # consumed by ``derive_walk_forward_spans``; set it directly on the mock.
    engine.walk_forward_windows = 1
    engine.prefetch_bars.return_value = bars
    # ``derive_walk_forward_spans`` reads ``engine.bar_size`` (a property on
    # the real BacktestEngine) and passes it through
    # ``timeframe_from_bar_size`` to derive the prefetch timeframe.  Setting
    # the attribute directly on the MagicMock short-circuits the @property
    # and gives the lookup a real string instead of another MagicMock (which
    # would raise KeyError in _BAR_SIZE_TO_TIMEFRAME).
    engine.bar_size = "1D"
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
            oos_skipped_count=2,
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


def test_walk_forward_output_exposes_skipped_count(monkeypatch):
    from milodex.backtesting.walk_forward_runner import (
        WalkForwardResult,
        WalkForwardStability,
    )

    result = backtest_command._build_walk_forward_result(
        WalkForwardResult(
            run_id="wf-1",
            strategy_id="meanrev.daily.rsi2pullback.v1",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_equity=10_000.0,
            train_days=10,
            test_days=5,
            step_days=5,
            windows=[],
            oos_trade_count=4,
            oos_skipped_count=2,
            oos_trading_days=10,
            oos_total_return_pct=1.0,
            oos_sharpe=None,
            oos_max_drawdown_pct=0.0,
            oos_equity_curve=[],
            stability=WalkForwardStability(None, None, None, 0, 0, False),
            risk_policy=RiskPolicy.BYPASS,
        )
    )

    assert result.data["oos_aggregate"]["skipped_count"] == 2
    assert any("Skipped:     2" in line for line in result.human_lines)


def test_walk_forward_output_exposes_data_quality_report():
    from milodex.backtesting.walk_forward_runner import (
        WalkForwardResult,
        WalkForwardStability,
    )

    result = backtest_command._build_walk_forward_result(
        WalkForwardResult(
            run_id="wf-1",
            strategy_id="meanrev.daily.rsi2pullback.v1",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_equity=10_000.0,
            train_days=10,
            test_days=5,
            step_days=5,
            windows=[],
            oos_trade_count=30,
            oos_skipped_count=0,
            oos_trading_days=10,
            oos_total_return_pct=1.0,
            oos_sharpe=None,
            oos_max_drawdown_pct=0.0,
            oos_equity_curve=[],
            stability=WalkForwardStability(None, None, None, 0, 0, False),
            risk_policy=RiskPolicy.BYPASS,
            data_quality={
                "status": "pass",
                "blocker_count": 0,
                "warning_count": 0,
                "issues": [],
            },
        )
    )

    assert result.data["data_quality"]["status"] == "pass"
    assert any("Data quality:  pass" in line for line in result.human_lines)


def test_walk_forward_output_exposes_run_manifest():
    from milodex.backtesting.walk_forward_runner import (
        WalkForwardResult,
        WalkForwardStability,
    )

    result = backtest_command._build_walk_forward_result(
        WalkForwardResult(
            run_id="wf-1",
            strategy_id="meanrev.daily.rsi2pullback.v1",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_equity=10_000.0,
            train_days=10,
            test_days=5,
            step_days=5,
            windows=[],
            oos_trade_count=30,
            oos_skipped_count=0,
            oos_trading_days=10,
            oos_total_return_pct=1.0,
            oos_sharpe=None,
            oos_max_drawdown_pct=0.0,
            oos_equity_curve=[],
            stability=WalkForwardStability(None, None, None, 0, 0, False),
            risk_policy=RiskPolicy.BYPASS,
            run_manifest={"schema_version": 1, "strategy": {"config_hash": "wf-hash"}},
        )
    )

    assert result.data["run_manifest"]["strategy"]["config_hash"] == "wf-hash"


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

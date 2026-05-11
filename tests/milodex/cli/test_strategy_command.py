"""Tests for the stage-compatibility guard in `milodex strategy run`."""

from __future__ import annotations

import importlib
import textwrap
from io import StringIO

import pytest

from milodex.broker.models import AccountInfo, OrderSide, OrderType, TimeInForce
from milodex.cli.main import main as cli_entrypoint
from milodex.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from milodex.risk.models import RiskCheckResult, RiskDecision

cli_main_module = importlib.import_module("milodex.cli.main")

_STRATEGY_ID = "test.daily.example.stub.v1"

_YAML_TEMPLATE = textwrap.dedent(
    """\
    strategy:
      id: "{strategy_id}"
      family: "test"
      template: "daily.example"
      variant: "stub"
      version: 1
      description: "Stub strategy for CLI stage-guard tests."
      enabled: true
      universe:
        - "SPY"
      parameters: {{}}
      tempo:
        bar_size: "1D"
        min_hold_days: 1
        max_hold_days: 5
      risk:
        max_position_pct: 0.10
        max_positions: 1
        daily_loss_cap_pct: 0.02
        stop_loss_pct: 0.05
      stage: "{stage}"
      backtest:
        commission_per_trade: 0.00
        min_trades_required: 30
      disable_conditions_additional: []
    """
)


def _write_config(tmp_path, stage: str) -> None:
    """Write a minimal strategy YAML with the given stage into tmp_path."""
    (tmp_path / "stub_strategy.yaml").write_text(
        _YAML_TEMPLATE.format(strategy_id=_STRATEGY_ID, stage=stage),
        encoding="utf-8",
    )


class StubRunner:
    def __init__(self) -> None:
        self.run_calls = 0
        self.session_id = "test-session"

    def run(self) -> None:
        self.run_calls += 1

    def set_on_cycle_result(self, callback) -> None:
        pass


class TestStageCompatibilityGuard:
    """paper mode × stage combinations."""

    def test_paper_mode_paper_stage_passes_guard(self, monkeypatch, tmp_path):
        """paper mode + paper-stage config must proceed past the guard."""
        monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "paper")
        _write_config(tmp_path, stage="paper")
        runner = StubRunner()
        stdout = StringIO()

        exit_code = cli_entrypoint(
            ["strategy", "run", _STRATEGY_ID],
            strategy_runner_factory=lambda _sid: runner,
            config_dir=tmp_path,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0, "paper-stage strategy in paper mode must be allowed"
        assert runner.run_calls == 1

    def test_paper_mode_backtest_stage_raises_clear_error(self, monkeypatch, tmp_path):
        """paper mode + backtest-stage config must be refused with a clear error."""
        monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "paper")
        _write_config(tmp_path, stage="backtest")
        runner = StubRunner()
        stderr = StringIO()

        exit_code = cli_entrypoint(
            ["strategy", "run", _STRATEGY_ID],
            strategy_runner_factory=lambda _sid: runner,
            config_dir=tmp_path,
            stdout=StringIO(),
            stderr=stderr,
        )

        assert exit_code == 1, "backtest-stage strategy in paper mode must be refused"
        assert runner.run_calls == 0, "runner.run() must not be called when guard fires"
        error_text = stderr.getvalue()
        assert _STRATEGY_ID in error_text, "error message must name the strategy_id"
        assert "backtest" in error_text, "error message must name the configured stage"
        assert "paper" in error_text, "error message must name the active trading mode"

    def test_paper_mode_micro_live_stage_raises_error(self, monkeypatch, tmp_path):
        """micro_live-stage strategy must also be refused in paper mode."""
        monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "paper")
        _write_config(tmp_path, stage="micro_live")
        runner = StubRunner()
        stderr = StringIO()

        exit_code = cli_entrypoint(
            ["strategy", "run", _STRATEGY_ID],
            strategy_runner_factory=lambda _sid: runner,
            config_dir=tmp_path,
            stdout=StringIO(),
            stderr=stderr,
        )

        assert exit_code == 1
        assert runner.run_calls == 0
        error_text = stderr.getvalue()
        assert _STRATEGY_ID in error_text
        assert "micro_live" in error_text


class TestCheckStageCompatibilityUnit:
    """Unit tests for the _check_stage_compatibility helper."""

    def test_matching_stage_does_not_raise(self):
        from milodex.cli.commands.strategy import _check_stage_compatibility

        _check_stage_compatibility(_STRATEGY_ID, "paper", "paper")  # must not raise

    def test_mismatched_stage_raises_value_error(self):
        from milodex.cli.commands.strategy import _check_stage_compatibility

        with pytest.raises(ValueError, match=_STRATEGY_ID):
            _check_stage_compatibility(_STRATEGY_ID, "backtest", "paper")

    def test_error_message_includes_all_three_fields(self):
        from milodex.cli.commands.strategy import _check_stage_compatibility

        with pytest.raises(ValueError) as exc_info:
            _check_stage_compatibility("strat.foo.v1", "backtest", "paper")

        msg = str(exc_info.value)
        assert "strat.foo.v1" in msg
        assert "backtest" in msg
        assert "paper" in msg

    def test_unrecognized_mode_raises_with_actionable_message(self):
        """An unrecognized trading_mode must raise ValueError naming the bad mode
        and listing the recognized modes -- never silently produce an empty set."""
        from milodex.cli.commands.strategy import _check_stage_compatibility

        with pytest.raises(ValueError) as exc_info:
            _check_stage_compatibility(_STRATEGY_ID, "paper", "typo_mode")

        msg = str(exc_info.value)
        assert "typo_mode" in msg, "error must name the unrecognized mode"
        assert "paper" in msg, "error must list recognized modes"
        assert "TRADING_MODE" in msg, "error must point to the env var"


def test_format_decision_line_includes_reason_codes_for_blocked_submits():
    from milodex.cli.commands.strategy import _format_decision_line

    result = ExecutionResult(
        status=ExecutionStatus.BLOCKED,
        execution_request=ExecutionRequest(
            symbol="XLK",
            side=OrderSide.BUY,
            quantity=113.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            estimated_unit_price=177.0,
            estimated_order_value=20_001.0,
        ),
        risk_decision=RiskDecision(
            allowed=False,
            summary="Blocked by risk checks",
            checks=[
                RiskCheckResult(
                    "order_value",
                    False,
                    "Estimated order value exceeded.",
                    "max_order_value_exceeded",
                ),
                RiskCheckResult(
                    "single_position",
                    False,
                    "Projected position value exceeded.",
                    "max_single_position_exceeded",
                ),
            ],
            reason_codes=["max_order_value_exceeded", "max_single_position_exceeded"],
        ),
        account=AccountInfo(
            equity=100_000.0,
            cash=100_000.0,
            buying_power=100_000.0,
            portfolio_value=100_000.0,
            daily_pnl=0.0,
        ),
        market_open=True,
        latest_bar=None,
    )

    line = _format_decision_line(result)

    assert "max_order_value_exceeded" in line
    assert "max_single_position_exceeded" in line

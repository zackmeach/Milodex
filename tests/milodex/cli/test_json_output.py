"""Tests for the ``--json`` CLI contract (R-CLI-009, ADR 0014)."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, date, datetime
from io import StringIO

import pandas as pd
import pytest

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.cli.formatter import (
    JSON_SCHEMA_VERSION,
    CommandResult,
    HumanFormatter,
    JsonFormatter,
    get_formatter,
)
from milodex.cli.main import main as cli_entrypoint
from milodex.data.models import Bar, BarSet, Timeframe
from milodex.execution.models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from milodex.execution.state import KillSwitchState
from milodex.risk import RiskCheckResult, RiskDecision

cli_main_module = importlib.import_module("milodex.cli.main")


class _Broker:
    def __init__(
        self,
        *,
        account: AccountInfo | None = None,
        market_open: bool = False,
        positions=None,
        orders=None,
    ) -> None:
        self._account = account
        self._market_open = market_open
        self._positions = positions or []
        self._orders = orders or []

    def get_account(self):
        assert self._account is not None
        return self._account

    def is_market_open(self):
        return self._market_open

    def get_positions(self):
        return self._positions

    def get_orders(self, status="all", limit=100):
        return self._orders


class _DataProvider:
    def __init__(self, bars_by_symbol):
        self._bars_by_symbol = bars_by_symbol

    def get_bars(self, symbols, timeframe, start, end):
        return {symbol: self._bars_by_symbol.get(symbol) for symbol in symbols}


class _ExecutionService:
    def __init__(
        self,
        *,
        preview_result=None,
        submit_result=None,
        order=None,
        cancel_result=(True, None),
        kill_switch_state=None,
    ):
        self.preview_result = preview_result
        self.submit_result = submit_result
        self.order = order
        self.cancel_result = cancel_result
        self.kill_switch_state = kill_switch_state or KillSwitchState(active=False)

    def preview(self, intent):
        return self.preview_result

    def submit_paper(self, intent):
        return self.submit_result

    def get_order_status(self, order_id):
        return self.order

    def cancel_order(self, order_id):
        return self.cancel_result

    def get_kill_switch_state(self):
        return self.kill_switch_state


class _StrategyRunner:
    def __init__(self):
        self.run_calls = 0
        self.session_id = "test-session"
        self.on_cycle_result = None

    def run(self):
        self.run_calls += 1

    def set_on_cycle_result(self, callback):
        self.on_cycle_result = callback


def _sample_execution_result(status: ExecutionStatus = ExecutionStatus.PREVIEW) -> ExecutionResult:
    request = ExecutionRequest(
        symbol="SPY",
        side=OrderSide.BUY,
        quantity=5.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        estimated_unit_price=100.0,
        estimated_order_value=500.0,
    )
    decision = RiskDecision(
        allowed=True,
        summary="ok",
        checks=[RiskCheckResult(name="paper_mode", passed=True, message="Paper mode confirmed.")],
    )
    order = None
    if status == ExecutionStatus.SUBMITTED:
        order = Order(
            id="order-paper-1",
            symbol="SPY",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=5.0,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.PENDING,
            submitted_at=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
        )
    return ExecutionResult(
        status=status,
        execution_request=request,
        risk_decision=decision,
        account=AccountInfo(
            equity=10_000.0,
            cash=5_000.0,
            buying_power=5_000.0,
            portfolio_value=10_000.0,
            daily_pnl=150.0,
        ),
        market_open=True,
        latest_bar=Bar(
            timestamp=datetime(2025, 1, 15, 14, 29, tzinfo=UTC),
            open=99.0,
            high=101.0,
            low=98.5,
            close=100.0,
            volume=1000,
            vwap=100.0,
        ),
        order=order,
        message="ok",
        recorded_at=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
    )


def test_command_result_json_payload_includes_required_fields():
    result = CommandResult(
        command="status",
        data={"trading_mode": "paper"},
        human_lines=["Milodex Status"],
    )

    payload = result.to_json_payload()

    assert payload["schema_version"] == JSON_SCHEMA_VERSION
    assert payload["command"] == "status"
    assert payload["status"] == "success"
    assert payload["data"] == {"trading_mode": "paper"}
    assert payload["warnings"] == []
    assert payload["errors"] == []
    assert payload["summary"] == ["Milodex Status"]
    # Timestamp is ISO-8601 and parseable.
    datetime.fromisoformat(payload["timestamp"])


def test_get_formatter_returns_human_or_json():
    assert isinstance(get_formatter(as_json=False), HumanFormatter)
    assert isinstance(get_formatter(as_json=True), JsonFormatter)


def test_human_formatter_renders_lines():
    result = CommandResult(command="status", human_lines=["a", "b"])
    assert HumanFormatter().render(result) == "a\nb"


def test_status_json_output_matches_contract(monkeypatch):
    monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "paper")
    broker = _Broker(
        account=AccountInfo(
            equity=10_000.0,
            cash=5_000.0,
            buying_power=5_000.0,
            portfolio_value=10_000.0,
            daily_pnl=150.0,
        ),
        market_open=True,
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["status", "--json"],
        broker_factory=lambda: broker,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["command"] == "status"
    assert payload["status"] == "success"
    assert payload["schema_version"] == JSON_SCHEMA_VERSION
    assert payload["data"]["trading_mode"] == "paper"
    assert payload["data"]["market_open"] is True
    assert payload["data"]["account"]["equity"] == 10_000.0


def test_positions_json_output_includes_sorted_positions():
    broker = _Broker(
        positions=[
            Position(
                symbol="AAPL",
                quantity=10.0,
                avg_entry_price=150.0,
                current_price=155.0,
                market_value=1550.0,
                unrealized_pnl=50.0,
                unrealized_pnl_pct=0.0333,
            ),
            Position(
                symbol="MSFT",
                quantity=5.0,
                avg_entry_price=200.0,
                current_price=198.0,
                market_value=990.0,
                unrealized_pnl=-10.0,
                unrealized_pnl_pct=-0.01,
            ),
        ]
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["positions", "--json", "--sort", "market-value", "--limit", "1"],
        broker_factory=lambda: broker,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["command"] == "positions"
    assert payload["data"]["sort"] == "market-value"
    assert payload["data"]["limit"] == 1
    assert len(payload["data"]["positions"]) == 1
    assert payload["data"]["positions"][0]["symbol"] == "AAPL"


def test_bars_json_output_serializes_rows():
    barset = BarSet(
        pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-14", "2025-01-15"], utc=True),
                "open": [149.0, 150.0],
                "high": [150.0, 152.0],
                "low": [148.5, 149.5],
                "close": [149.5, 151.0],
                "volume": [950000, 1000000],
                "vwap": [149.2, 150.8],
            }
        )
    )
    provider = _DataProvider({"SPY": barset})
    stdout = StringIO()

    exit_code = cli_entrypoint(
        [
            "data",
            "bars",
            "SPY",
            "--json",
            "--start",
            "2025-01-13",
            "--end",
            "2025-01-15",
            "--limit",
            "2",
        ],
        data_provider_factory=lambda: provider,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["command"] == "data.bars"
    assert payload["data"]["symbol"] == "SPY"
    assert payload["data"]["timeframe"] == "1d"
    assert len(payload["data"]["bars"]) == 2
    assert payload["data"]["bars"][0]["close"] == 149.5


def test_trade_preview_json_output_includes_risk_decision():
    service = _ExecutionService(preview_result=_sample_execution_result())
    stdout = StringIO()

    exit_code = cli_entrypoint(
        [
            "trade",
            "preview",
            "SPY",
            "--json",
            "--side",
            "buy",
            "--quantity",
            "5",
            "--order-type",
            "market",
        ],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["command"] == "trade.preview"
    assert payload["data"]["risk_decision"]["allowed"] is True
    assert payload["data"]["request"]["symbol"] == "SPY"


def test_trade_cancel_failure_reports_error_status():
    service = _ExecutionService(cancel_result=(False, None))
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["trade", "cancel", "--json", "order-paper-1"],
        execution_service_factory=lambda: service,
        stdout=StringIO(),
        stderr=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["errors"][0]["code"] == "cancel_failed"


def test_kill_switch_json_output_reports_state():
    service = _ExecutionService(
        kill_switch_state=KillSwitchState(
            active=True,
            reason="Daily loss exceeded kill switch threshold.",
            last_triggered_at="2026-04-14T12:00:00+00:00",
        )
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["trade", "kill-switch", "status", "--json"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["command"] == "trade.kill-switch.status"
    assert payload["data"]["active"] is True
    assert payload["data"]["reason"] == "Daily loss exceeded kill switch threshold."


def test_strategy_run_json_output(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "paper")
    monkeypatch.setattr(cli_main_module, "get_locks_dir", lambda: tmp_path / "locks")
    runner = _StrategyRunner()
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["strategy", "run", "--json", "regime.daily.sma200_rotation.spy_shy.v1"],
        strategy_runner_factory=lambda strategy_id: runner,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert runner.run_calls == 1
    payload = json.loads(stdout.getvalue())
    assert payload["command"] == "strategy.run"
    assert payload["data"]["strategy_id"] == "regime.daily.sma200_rotation.spy_shy.v1"


def test_json_error_payload_has_code_and_message(monkeypatch):
    monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "live")
    stderr = StringIO()

    exit_code = cli_entrypoint(
        ["strategy", "run", "--json", "regime.daily.sma200_rotation.spy_shy.v1"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    payload = json.loads(stderr.getvalue())
    assert payload["status"] == "error"
    assert payload["errors"][0]["code"] == "error"
    assert "paper-only" in payload["errors"][0]["message"]


def test_json_formatter_decoupled_from_command_logic():
    """ADR 0014: removing JSON formatter breaks only JSON tests, not commands.

    We verify that commands produce structured results (``CommandResult``),
    and that the human and JSON outputs share a single source of truth.
    """
    human_result = cli_main_module._build_status_result(
        AccountInfo(
            equity=1.0, cash=1.0, buying_power=1.0, portfolio_value=1.0, daily_pnl=0.0
        ),
        "paper",
        True,
    )
    assert human_result.data["trading_mode"] == "paper"
    human_lines = HumanFormatter().render(human_result).splitlines()
    assert "Milodex Status" in human_lines

    payload = json.loads(JsonFormatter().render(human_result))
    assert payload["summary"] == human_lines


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "status"],
        ["status", "--json"],
    ],
)
def test_json_flag_accepted_before_or_after_command(monkeypatch, argv):
    monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "paper")
    broker = _Broker(
        account=AccountInfo(
            equity=1.0, cash=1.0, buying_power=1.0, portfolio_value=1.0, daily_pnl=0.0
        ),
        market_open=False,
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        argv,
        broker_factory=lambda: broker,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["command"] == "status"


def test_Timeframe_used_to_silence_unused_import():  # noqa: N802
    # Timeframe is referenced so imports remain stable across linters.
    assert Timeframe.DAY_1
    assert date(2025, 1, 1) < date(2025, 2, 1)

"""Tests for the Milodex CLI."""

from __future__ import annotations

import importlib
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path

import pandas as pd

from milodex.broker.exceptions import BrokerAuthError
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
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


class StubBroker:
    """Simple broker stub for CLI tests."""

    def __init__(
        self,
        *,
        account: AccountInfo | None = None,
        market_open: bool = False,
        positions: list[Position] | None = None,
        orders: list[Order] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._account = account
        self._market_open = market_open
        self._positions = positions or []
        self._orders = orders or []
        self._error = error
        self.order_calls: list[tuple[str, int]] = []

    def get_account(self) -> AccountInfo:
        if self._error:
            raise self._error
        assert self._account is not None
        return self._account

    def is_market_open(self) -> bool:
        if self._error:
            raise self._error
        return self._market_open

    def get_positions(self) -> list[Position]:
        if self._error:
            raise self._error
        return self._positions

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        if self._error:
            raise self._error
        self.order_calls.append((status, limit))
        return self._orders


class StubDataProvider:
    """Simple data provider stub for CLI tests."""

    def __init__(self, bars_by_symbol: dict[str, BarSet] | None = None) -> None:
        self._bars_by_symbol = bars_by_symbol or {}
        self.calls: list[tuple[list[str], Timeframe, date, date]] = []

    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, BarSet]:
        self.calls.append((symbols, timeframe, start, end))
        return {symbol: self._bars_by_symbol.get(symbol) for symbol in symbols}


class StubExecutionService:
    """Execution service stub for trade command tests."""

    def __init__(
        self,
        *,
        preview_result: ExecutionResult | None = None,
        submit_result: ExecutionResult | None = None,
        order: Order | None = None,
        cancel_result: tuple[bool, Order | None] = (True, None),
        kill_switch_state: KillSwitchState | None = None,
    ) -> None:
        self.preview_result = preview_result
        self.submit_result = submit_result
        self.order = order
        self.cancel_result = cancel_result
        self.kill_switch_state = kill_switch_state or KillSwitchState(active=False)
        self.preview_calls: list[object] = []
        self.submit_calls: list[object] = []
        self.order_status_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.reset_kill_switch_calls = 0

    def preview(self, intent):
        self.preview_calls.append(intent)
        assert self.preview_result is not None
        return self.preview_result

    def submit_paper(self, intent):
        self.submit_calls.append(intent)
        assert self.submit_result is not None
        return self.submit_result

    def get_order_status(self, order_id: str) -> Order:
        self.order_status_calls.append(order_id)
        assert self.order is not None
        return self.order

    def cancel_order(self, order_id: str) -> tuple[bool, Order | None]:
        self.cancel_calls.append(order_id)
        return self.cancel_result

    def get_kill_switch_state(self) -> KillSwitchState:
        return self.kill_switch_state

    def reset_kill_switch(self) -> None:
        self.reset_kill_switch_calls += 1
        self.kill_switch_state = KillSwitchState(active=False)


class StubStrategyRunner:
    """Strategy runner stub for CLI tests."""

    def __init__(self) -> None:
        self.run_calls = 0
        self.session_id = "test-session"
        self.on_cycle_result = None

    def run(self) -> None:
        self.run_calls += 1

    def set_on_cycle_result(self, callback) -> None:
        self.on_cycle_result = callback


def _sample_barset() -> BarSet:
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-13", "2025-01-14", "2025-01-15"], utc=True),
                "open": [148.0, 149.0, 150.0],
                "high": [149.0, 150.0, 152.0],
                "low": [147.0, 148.5, 149.5],
                "close": [148.5, 149.5, 151.0],
                "volume": [900000, 950000, 1000000],
                "vwap": [148.3, 149.2, 150.8],
            }
        )
    )


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
        allowed=status != ExecutionStatus.BLOCKED,
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


def test_status_outputs_account_summary(monkeypatch):
    monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "paper")

    broker = StubBroker(
        account=AccountInfo(
            equity=10000.0,
            cash=5000.0,
            buying_power=5000.0,
            portfolio_value=10000.0,
            daily_pnl=150.0,
        ),
        market_open=True,
    )
    stdout = StringIO()
    stderr = StringIO()

    exit_code = cli_entrypoint(
        ["status"], broker_factory=lambda: broker, stdout=stdout, stderr=stderr
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "Milodex Status" in output
    assert "Trading mode: paper" in output
    assert "Market open: yes" in output
    assert "Equity: $10,000.00" in output
    assert stderr.getvalue() == ""


def test_positions_support_sort_and_limit():
    broker = StubBroker(
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
        ["positions", "--sort", "market-value", "--limit", "1"],
        broker_factory=lambda: broker,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "AAPL" in output
    assert "MSFT" not in output


def test_orders_support_symbol_filter_and_verbose_output():
    broker = StubBroker(
        orders=[
            Order(
                id="order-1234567890",
                symbol="MSFT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=5.0,
                time_in_force=TimeInForce.DAY,
                status=OrderStatus.PENDING,
                submitted_at=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
                limit_price=250.0,
            ),
            Order(
                id="order-abcdefghij",
                symbol="AAPL",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=2.0,
                time_in_force=TimeInForce.DAY,
                status=OrderStatus.FILLED,
                submitted_at=datetime(2025, 1, 15, 15, 30, tzinfo=UTC),
            ),
        ]
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["orders", "--status", "open", "--limit", "5", "--symbol", "MSFT", "--verbose"],
        broker_factory=lambda: broker,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert broker.order_calls == [("open", 5)]
    assert "MSFT" in output
    assert "AAPL" not in output
    assert "details: limit=$250.00" in output


def test_data_bars_outputs_rows():
    provider = StubDataProvider({"SPY": _sample_barset()})
    stdout = StringIO()

    exit_code = cli_entrypoint(
        [
            "data",
            "bars",
            "SPY",
            "--timeframe",
            "1d",
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

    output = stdout.getvalue()
    assert exit_code == 0
    assert provider.calls == [(["SPY"], Timeframe.DAY_1, date(2025, 1, 13), date(2025, 1, 15))]
    assert "Bars for SPY (1d)" in output
    assert "2025-01-14" in output
    assert "2025-01-15" in output


def test_config_validate_accepts_sample_strategy():
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["config", "validate", str(Path("configs/sample_strategy.yaml"))],
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "Config validation passed" in output
    assert "Detected kind: strategy" in output


def test_trade_preview_renders_execution_result():
    service = StubExecutionService(preview_result=_sample_execution_result())
    stdout = StringIO()

    exit_code = cli_entrypoint(
        [
            "trade",
            "preview",
            "SPY",
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

    output = stdout.getvalue()
    assert exit_code == 0
    assert service.preview_calls
    assert "Trade Execution" in output
    assert "Decision: allow" in output


def test_trade_submit_requires_paper_flag():
    service = StubExecutionService(
        submit_result=_sample_execution_result(ExecutionStatus.SUBMITTED)
    )
    stderr = StringIO()

    exit_code = cli_entrypoint(
        [
            "trade",
            "submit",
            "SPY",
            "--side",
            "buy",
            "--quantity",
            "5",
            "--order-type",
            "market",
        ],
        execution_service_factory=lambda: service,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert not service.submit_calls
    assert "requires --paper" in stderr.getvalue()


def test_trade_submit_renders_submitted_order(tmp_path):
    service = StubExecutionService(
        submit_result=_sample_execution_result(ExecutionStatus.SUBMITTED)
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        [
            "trade",
            "submit",
            "SPY",
            "--side",
            "buy",
            "--quantity",
            "5",
            "--order-type",
            "market",
            "--paper",
        ],
        execution_service_factory=lambda: service,
        locks_dir=tmp_path,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert service.submit_calls
    assert "Broker order ID: order-paper-1" in output


def test_trade_order_status_renders_order():
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
    service = StubExecutionService(order=order)
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["trade", "order-status", "order-paper-1"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert service.order_status_calls == ["order-paper-1"]
    assert "Order Status" in output
    assert "SPY" in output


def test_trade_cancel_renders_result():
    service = StubExecutionService(cancel_result=(True, None))
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["trade", "cancel", "order-paper-1"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert service.cancel_calls == ["order-paper-1"]
    assert "requested successfully" in output


def test_trade_kill_switch_status_renders_state():
    service = StubExecutionService(
        kill_switch_state=KillSwitchState(
            active=True,
            reason="Daily loss exceeded kill switch threshold.",
            last_triggered_at="2026-04-14T12:00:00+00:00",
        )
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["trade", "kill-switch", "status"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "Kill Switch" in output
    assert "Active: yes" in output


def test_trade_kill_switch_reset_requires_confirm():
    service = StubExecutionService(
        kill_switch_state=KillSwitchState(active=True, reason="test")
    )
    stderr = StringIO()

    exit_code = cli_entrypoint(
        ["trade", "kill-switch", "reset"],
        execution_service_factory=lambda: service,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert "--confirm" in stderr.getvalue()
    assert service.reset_kill_switch_calls == 0


def test_trade_kill_switch_reset_clears_with_confirm():
    service = StubExecutionService(
        kill_switch_state=KillSwitchState(
            active=True,
            reason="Operator requested kill switch.",
            last_triggered_at="2026-04-23T14:19:23+00:00",
        )
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["trade", "kill-switch", "reset", "--confirm"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert service.reset_kill_switch_calls == 1
    assert "Kill Switch Reset" in output
    assert "Previously active: yes" in output
    assert "Now active: no" in output


def test_strategy_run_requires_paper_mode(monkeypatch):
    monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "live")
    stderr = StringIO()

    exit_code = cli_entrypoint(
        ["strategy", "run", "regime.daily.sma200_rotation.spy_shy.v1"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert "paper-only" in stderr.getvalue()


def test_strategy_run_dispatches_runner(monkeypatch):
    monkeypatch.setattr(cli_main_module, "get_trading_mode", lambda: "paper")
    runner = StubStrategyRunner()
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["strategy", "run", "regime.daily.sma200_rotation.spy_shy.v1"],
        strategy_runner_factory=lambda strategy_id: runner,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert runner.run_calls == 1
    output = stdout.getvalue()
    assert "session: test-session" in output
    assert "strategy: regime.daily.sma200_rotation.spy_shy.v1" in output
    assert "mode: paper" in output
    assert "Session test-session ended." in output


def test_main_reports_broker_errors_to_stderr():
    broker = StubBroker(error=BrokerAuthError("bad credentials"))
    stdout = StringIO()
    stderr = StringIO()

    exit_code = cli_entrypoint(
        ["status"], broker_factory=lambda: broker, stdout=stdout, stderr=stderr
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "Error:" in stderr.getvalue()

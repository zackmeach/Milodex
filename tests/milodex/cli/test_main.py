"""Tests for the Milodex CLI."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from io import StringIO

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


def test_positions_outputs_table():
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
            )
        ]
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["positions"], broker_factory=lambda: broker, stdout=stdout, stderr=StringIO()
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "Open Positions" in output
    assert "AAPL" in output
    assert "$1,550.00" in output
    assert "3.33%" in output


def test_positions_empty_state():
    broker = StubBroker()
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["positions"], broker_factory=lambda: broker, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "No open positions." in stdout.getvalue()


def test_orders_outputs_table_and_uses_filters():
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
            )
        ]
    )
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["orders", "--status", "open", "--limit", "5"],
        broker_factory=lambda: broker,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert broker.order_calls == [("open", 5)]
    assert "Recent Orders" in output
    assert "MSFT" in output
    assert "limit" in output


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

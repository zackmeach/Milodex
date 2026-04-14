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
from milodex.data.models import BarSet, Timeframe

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

    def get_latest_bar(self, symbol: str):  # pragma: no cover - unused in CLI tests
        raise NotImplementedError

    def get_tradeable_assets(self):  # pragma: no cover - unused in CLI tests
        raise NotImplementedError


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


def test_config_validate_rejects_missing_required_keys(tmp_path):
    config_path = tmp_path / "bad_strategy.yaml"
    config_path.write_text("strategy:\n  name: example\n", encoding="utf-8")
    stderr = StringIO()

    exit_code = cli_entrypoint(
        ["config", "validate", str(config_path), "--kind", "strategy"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert "missing required key" in stderr.getvalue()


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

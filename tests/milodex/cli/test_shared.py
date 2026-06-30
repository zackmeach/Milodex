"""Contract pins for ``milodex.cli._shared``.

These tests freeze the machine-readable JSON shape of the CLI serializers
(``*_to_dict``), the small formatters, ``parse_iso_date``, ``error_result``,
and ``command_name_from_args``. Full key-set assertions (``set(d) == {...}``)
are deliberate so that adding or removing a serialized field fails the test
until the contract change is acknowledged here.
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

import pytest

from milodex.analytics.metrics import PerformanceMetrics
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.cli._shared import (
    account_to_dict,
    command_name_from_args,
    error_result,
    format_money,
    format_pct,
    order_to_dict,
    parse_iso_date,
    performance_metrics_to_dict,
    position_to_dict,
)
from milodex.cli.formatter import CommandResult


def _account() -> AccountInfo:
    return AccountInfo(
        equity=1000.0,
        cash=250.0,
        buying_power=2000.0,
        portfolio_value=1000.0,
        daily_pnl=-12.5,
    )


def _position() -> Position:
    return Position(
        symbol="SPY",
        quantity=3.0,
        avg_entry_price=400.0,
        current_price=410.0,
        market_value=1230.0,
        unrealized_pnl=30.0,
        unrealized_pnl_pct=0.025,
    )


def _order(*, filled: bool) -> Order:
    return Order(
        id="ord-1",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=2.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.FILLED if filled else OrderStatus.PENDING,
        submitted_at=datetime(2026, 6, 21, 14, 30, tzinfo=UTC),
        limit_price=None,
        stop_price=None,
        filled_quantity=2.0 if filled else None,
        filled_avg_price=405.0 if filled else None,
        filled_at=datetime(2026, 6, 21, 14, 31, tzinfo=UTC) if filled else None,
    )


def _metrics() -> PerformanceMetrics:
    return PerformanceMetrics(
        run_id="run-1",
        strategy_id="momentum.atr.spy.v1",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 6, 30),
        initial_equity=1000.0,
        final_equity=1100.0,
        total_return_pct=10.0,
        cagr_pct=21.0,
        max_drawdown_pct=-5.0,
        max_drawdown_duration_days=12,
        sharpe_ratio=1.2,
        sortino_ratio=1.5,
        trade_count=40,
        buy_count=20,
        sell_count=20,
        win_rate_pct=55.0,
        avg_hold_days=2.5,
        winning_trades=22,
        losing_trades=18,
        avg_win_usd=15.0,
        avg_loss_usd=-10.0,
        profit_factor=1.8,
        trading_days=120,
        confidence_label="high",
        result_type="whole_period",
    )


def test_account_to_dict_full_contract() -> None:
    d = account_to_dict(_account())
    assert set(d.keys()) == {
        "equity",
        "cash",
        "buying_power",
        "portfolio_value",
        "daily_pnl",
    }
    assert all(isinstance(v, float) for v in d.values())
    assert d["equity"] == 1000.0
    assert d["daily_pnl"] == -12.5


def test_position_to_dict_full_contract() -> None:
    d = position_to_dict(_position())
    assert set(d.keys()) == {
        "symbol",
        "quantity",
        "avg_entry_price",
        "current_price",
        "market_value",
        "unrealized_pnl",
        "unrealized_pnl_pct",
    }
    assert d["symbol"] == "SPY"
    assert isinstance(d["symbol"], str)
    for key in (
        "quantity",
        "avg_entry_price",
        "current_price",
        "market_value",
        "unrealized_pnl",
        "unrealized_pnl_pct",
    ):
        assert isinstance(d[key], float)


def test_order_to_dict_full_contract_unfilled() -> None:
    d = order_to_dict(_order(filled=False))
    assert set(d.keys()) == {
        "id",
        "symbol",
        "side",
        "order_type",
        "quantity",
        "time_in_force",
        "status",
        "submitted_at",
        "limit_price",
        "stop_price",
        "filled_quantity",
        "filled_avg_price",
        "filled_at",
    }
    # Enum -> .value coercion (plain strings, not Enum members).
    assert d["side"] == "buy"
    assert d["order_type"] == "market"
    assert d["status"] == "pending"
    assert d["time_in_force"] == "day"
    for key in ("side", "order_type", "status", "time_in_force"):
        assert isinstance(d[key], str)
    # submitted_at is always an isoformat string.
    assert d["submitted_at"] == "2026-06-21T14:30:00+00:00"
    # Nullable fields are None on an unfilled order.
    assert d["filled_at"] is None
    assert d["filled_quantity"] is None
    assert d["filled_avg_price"] is None
    assert d["limit_price"] is None
    assert d["stop_price"] is None


def test_order_to_dict_filled_at_isoformat() -> None:
    d = order_to_dict(_order(filled=True))
    assert d["status"] == "filled"
    assert d["filled_at"] == "2026-06-21T14:31:00+00:00"
    assert isinstance(d["filled_at"], str)
    assert d["filled_quantity"] == 2.0
    assert d["filled_avg_price"] == 405.0


def test_performance_metrics_to_dict_full_contract() -> None:
    d = performance_metrics_to_dict(_metrics())
    assert set(d.keys()) == {
        "run_id",
        "strategy_id",
        "start_date",
        "end_date",
        "initial_equity",
        "final_equity",
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "max_drawdown_duration_days",
        "sharpe_ratio",
        "sortino_ratio",
        "trade_count",
        "buy_count",
        "sell_count",
        "win_rate_pct",
        "avg_hold_days",
        "winning_trades",
        "losing_trades",
        "avg_win_usd",
        "avg_loss_usd",
        "profit_factor",
        "trading_days",
        "confidence_label",
        "result_type",
    }
    # Dates serialize as isoformat strings.
    assert d["start_date"] == "2025-01-01"
    assert d["end_date"] == "2025-06-30"
    assert d["run_id"] == "run-1"
    assert d["result_type"] == "whole_period"


def test_format_money() -> None:
    assert format_money(1234.56) == "$1,234.56"
    assert format_money(-1234.56) == "$-1,234.56"
    assert format_money(0) == "$0.00"
    assert format_money(1000000) == "$1,000,000.00"


def test_format_pct() -> None:
    assert format_pct(0.1234) == "12.34%"
    assert format_pct(0) == "0.00%"
    assert format_pct(-0.05) == "-5.00%"
    assert format_pct(12.5) == "1,250.00%"


def test_parse_iso_date_valid() -> None:
    assert parse_iso_date("2026-06-21") == date(2026, 6, 21)


def test_parse_iso_date_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match=r"Invalid date 'not-a-date'\. Use YYYY-MM-DD format\."):
        parse_iso_date("not-a-date")


def test_error_result_shape() -> None:
    result = error_result("trade.submit", "boom", code="bad_input")
    assert isinstance(result, CommandResult)
    assert result.command == "trade.submit"
    assert result.status == "error"
    assert result.data == {}
    assert result.human_lines == ["Error: boom"]
    assert result.errors == [{"code": "bad_input", "message": "boom"}]


def test_error_result_defaults_code_and_passes_data() -> None:
    result = error_result("status", "nope", data={"k": "v"})
    assert result.errors == [{"code": "error", "message": "nope"}]
    assert result.data == {"k": "v"}


def test_command_name_from_args_dotted() -> None:
    args = argparse.Namespace(command="trade", trade_command="submit")
    assert command_name_from_args(args) == "trade.submit"


def test_command_name_from_args_no_subcommand() -> None:
    args = argparse.Namespace(command="status")
    assert command_name_from_args(args) == "status"


def test_command_name_from_args_no_command_falls_back() -> None:
    assert command_name_from_args(argparse.Namespace()) == "milodex"


def test_command_name_from_args_kill_switch() -> None:
    args = argparse.Namespace(command="kill-switch", kill_switch_command="reset")
    assert command_name_from_args(args) == "kill-switch.reset"

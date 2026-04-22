"""Shared helpers for CLI command modules.

Holds argparse flag helpers, small formatters, result builders used across
multiple command modules, and the ``CommandContext`` DTO that bundles the
factories each command needs. Anything used by exactly one command lives in
that command's module instead.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from milodex.broker import OrderSide, OrderType, TimeInForce
from milodex.broker.models import AccountInfo, Order, Position
from milodex.cli.formatter import CommandResult
from milodex.data import Timeframe

if TYPE_CHECKING:
    from milodex.backtesting.engine import BacktestEngine
    from milodex.broker.alpaca_client import AlpacaBrokerClient
    from milodex.core.event_store import EventStore
    from milodex.data.alpaca_provider import AlpacaDataProvider
    from milodex.execution import ExecutionService
    from milodex.strategies.runner import StrategyRunner


TIMEFRAME_CHOICES = {
    "1m": Timeframe.MINUTE_1,
    "5m": Timeframe.MINUTE_5,
    "15m": Timeframe.MINUTE_15,
    "1h": Timeframe.HOUR_1,
    "1d": Timeframe.DAY_1,
}

ORDER_TYPE_CHOICES = {
    "market": OrderType.MARKET,
    "limit": OrderType.LIMIT,
    "stop": OrderType.STOP,
    "stop_limit": OrderType.STOP_LIMIT,
}

SIDE_CHOICES = {
    "buy": OrderSide.BUY,
    "sell": OrderSide.SELL,
}

TIF_CHOICES = {
    "day": TimeInForce.DAY,
    "gtc": TimeInForce.GTC,
}


@dataclass
class CommandContext:
    get_execution_service: Callable[[], ExecutionService]
    get_strategy_runner: Callable[[str], StrategyRunner]
    get_backtest_engine: Callable[..., BacktestEngine]
    get_event_store: Callable[[], EventStore]
    broker_factory: Callable[[], AlpacaBrokerClient]
    data_provider_factory: Callable[[], AlpacaDataProvider]
    get_trading_mode: Callable[[], str]
    config_dir: Path
    locks_dir: Path


def add_global_flags(parser: argparse.ArgumentParser) -> None:
    """Attach global flags (``--json``) to ``parser``.

    The flag is repeated on every subparser so it can appear either before
    or after the command name, per R-CLI-009. ``argparse.SUPPRESS`` is used
    as the default so a subparser does not overwrite a value set by the
    root parser when the flag was passed before the subcommand name.
    """
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit the result as a single JSON object (R-CLI-009).",
    )


def add_trade_arguments(parser: argparse.ArgumentParser, *, require_paper_flag: bool) -> None:
    parser.add_argument("symbol", help="Ticker symbol.")
    parser.add_argument("--side", required=True, choices=tuple(SIDE_CHOICES), help="Order side.")
    parser.add_argument("--quantity", required=True, type=float, help="Order quantity.")
    parser.add_argument(
        "--order-type",
        required=True,
        choices=tuple(ORDER_TYPE_CHOICES),
        help="Order type.",
    )
    parser.add_argument(
        "--time-in-force",
        choices=tuple(TIF_CHOICES),
        default="day",
        help="Time in force.",
    )
    parser.add_argument("--limit-price", type=float, help="Limit price when required.")
    parser.add_argument("--stop-price", type=float, help="Stop price when required.")
    parser.add_argument("--strategy-config", help="Optional path to a strategy config YAML file.")
    if require_paper_flag:
        parser.add_argument(
            "--paper",
            action="store_true",
            help="Required safety flag for paper trade submission.",
        )


def format_money(value: float) -> str:
    return f"${value:,.2f}"


def format_pct(value: float) -> str:
    return f"{value * 100:,.2f}%"


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD format.") from exc


def account_to_dict(account: AccountInfo) -> dict[str, Any]:
    return {
        "equity": account.equity,
        "cash": account.cash,
        "buying_power": account.buying_power,
        "portfolio_value": account.portfolio_value,
        "daily_pnl": account.daily_pnl,
    }


def position_to_dict(position: Position) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "avg_entry_price": position.avg_entry_price,
        "current_price": position.current_price,
        "market_value": position.market_value,
        "unrealized_pnl": position.unrealized_pnl,
        "unrealized_pnl_pct": position.unrealized_pnl_pct,
    }


def order_to_dict(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "symbol": order.symbol,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "quantity": order.quantity,
        "time_in_force": order.time_in_force.value,
        "status": order.status.value,
        "submitted_at": order.submitted_at.isoformat(),
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
        "filled_quantity": order.filled_quantity,
        "filled_avg_price": order.filled_avg_price,
        "filled_at": order.filled_at.isoformat() if order.filled_at is not None else None,
    }


def error_result(command: str, message: str, code: str = "error") -> CommandResult:
    return CommandResult(
        command=command,
        status="error",
        human_lines=[f"Error: {message}"],
        errors=[{"code": code, "message": message}],
    )


def command_name_from_args(args: argparse.Namespace) -> str:
    parts = [getattr(args, "command", None) or "milodex"]
    for attr in (
        "data_command",
        "config_command",
        "trade_command",
        "strategy_command",
        "analytics_command",
    ):
        value = getattr(args, attr, None)
        if value:
            parts.append(value)
    if getattr(args, "kill_switch_command", None):
        parts.append(args.kill_switch_command)
    return ".".join(parts)

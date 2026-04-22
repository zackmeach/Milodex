"""CLI entrypoint for operator workflows."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TextIO

import pandas as pd

from milodex.analytics.benchmark import compute_benchmark
from milodex.analytics.metrics import PerformanceMetrics, compute_metrics
from milodex.backtesting.engine import BacktestEngine, BacktestResult
from milodex.backtesting.walk_forward import WalkForwardSplitter
from milodex.broker import BrokerError, OrderSide, OrderType, TimeInForce
from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.models import AccountInfo, Order, Position
from milodex.cli.config_validation import validate_config_file
from milodex.cli.formatter import CommandResult, get_formatter
from milodex.config import get_data_dir, get_locks_dir, get_logs_dir, get_trading_mode
from milodex.core.advisory_lock import AdvisoryLock, AdvisoryLockError
from milodex.core.event_store import EventStore
from milodex.data import BarSet, Timeframe
from milodex.data.alpaca_provider import AlpacaDataProvider
from milodex.execution import ExecutionService, TradeIntent
from milodex.execution.models import ExecutionResult
from milodex.execution.state import KillSwitchStateStore
from milodex.strategies.loader import StrategyLoader
from milodex.strategies.runner import StrategyRunner

_TIMEFRAME_CHOICES = {
    "1m": Timeframe.MINUTE_1,
    "5m": Timeframe.MINUTE_5,
    "15m": Timeframe.MINUTE_15,
    "1h": Timeframe.HOUR_1,
    "1d": Timeframe.DAY_1,
}

_ORDER_TYPE_CHOICES = {
    "market": OrderType.MARKET,
    "limit": OrderType.LIMIT,
    "stop": OrderType.STOP,
    "stop_limit": OrderType.STOP_LIMIT,
}

_SIDE_CHOICES = {
    "buy": OrderSide.BUY,
    "sell": OrderSide.SELL,
}

_TIF_CHOICES = {
    "day": TimeInForce.DAY,
    "gtc": TimeInForce.GTC,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the root CLI parser."""
    parser = argparse.ArgumentParser(prog="milodex", description="Milodex operator CLI.")
    _add_global_flags(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser(
        "status",
        help="Show trading mode, market status, and account summary.",
    )
    _add_global_flags(status_parser)

    positions_parser = subparsers.add_parser("positions", help="List open positions.")
    _add_global_flags(positions_parser)
    positions_parser.add_argument(
        "--sort",
        choices=("symbol", "market-value", "unrealized-pnl"),
        default="symbol",
        help="Sort positions before display.",
    )
    positions_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of positions to display.",
    )

    orders_parser = subparsers.add_parser("orders", help="List recent orders.")
    _add_global_flags(orders_parser)
    orders_parser.add_argument(
        "--status",
        choices=("all", "open", "closed"),
        default="all",
        help="Filter recent orders by status.",
    )
    orders_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of orders to display.",
    )
    orders_parser.add_argument(
        "--symbol",
        help="Filter orders to a specific symbol after retrieval.",
    )
    orders_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show limit/stop/fill details when available.",
    )

    data_parser = subparsers.add_parser("data", help="Inspect market data.")
    _add_global_flags(data_parser)
    data_subparsers = data_parser.add_subparsers(dest="data_command", required=True)
    bars_parser = data_subparsers.add_parser("bars", help="Fetch bars for a symbol.")
    _add_global_flags(bars_parser)
    bars_parser.add_argument("symbol", help="Ticker symbol to fetch.")
    bars_parser.add_argument(
        "--timeframe",
        choices=tuple(_TIMEFRAME_CHOICES),
        default="1d",
        help="Timeframe to request.",
    )
    bars_parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD format.")
    bars_parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD format.")
    bars_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of bars to display from the requested range.",
    )

    config_parser = subparsers.add_parser("config", help="Validate Milodex config files.")
    _add_global_flags(config_parser)
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    validate_parser = config_subparsers.add_parser("validate", help="Validate a YAML config file.")
    _add_global_flags(validate_parser)
    validate_parser.add_argument("path", help="Path to the YAML config file.")
    validate_parser.add_argument(
        "--kind",
        choices=("strategy", "risk"),
        help="Optional config kind override.",
    )

    trade_parser = subparsers.add_parser("trade", help="Preview and submit paper trades.")
    _add_global_flags(trade_parser)
    trade_subparsers = trade_parser.add_subparsers(dest="trade_command", required=True)

    preview_parser = trade_subparsers.add_parser(
        "preview",
        help="Preview a trade through risk checks.",
    )
    _add_global_flags(preview_parser)
    _add_trade_arguments(preview_parser, require_paper_flag=False)

    submit_parser = trade_subparsers.add_parser(
        "submit",
        help="Submit a paper trade after risk checks.",
    )
    _add_global_flags(submit_parser)
    _add_trade_arguments(submit_parser, require_paper_flag=True)

    order_status_parser = trade_subparsers.add_parser(
        "order-status",
        help="Fetch current broker status for an order.",
    )
    _add_global_flags(order_status_parser)
    order_status_parser.add_argument("order_id", help="Broker order ID.")

    cancel_parser = trade_subparsers.add_parser("cancel", help="Cancel an existing order.")
    _add_global_flags(cancel_parser)
    cancel_parser.add_argument("order_id", help="Broker order ID.")

    kill_switch_parser = trade_subparsers.add_parser(
        "kill-switch",
        help="Inspect kill switch state.",
    )
    _add_global_flags(kill_switch_parser)
    kill_switch_subparsers = kill_switch_parser.add_subparsers(
        dest="kill_switch_command",
        required=True,
    )
    kill_switch_status_parser = kill_switch_subparsers.add_parser(
        "status", help="Show current kill switch state."
    )
    _add_global_flags(kill_switch_status_parser)

    strategy_parser = subparsers.add_parser("strategy", help="Run configured strategies.")
    _add_global_flags(strategy_parser)
    strategy_subparsers = strategy_parser.add_subparsers(dest="strategy_command", required=True)
    strategy_run_parser = strategy_subparsers.add_parser(
        "run",
        help="Run one strategy as a foreground paper-trading session.",
    )
    _add_global_flags(strategy_run_parser)
    strategy_run_parser.add_argument(
        "strategy_id", help="Strategy identifier from the YAML config."
    )

    backtest_parser = subparsers.add_parser(
        "backtest",
        help="Run a historical backtest for a strategy.",
    )
    _add_global_flags(backtest_parser)
    backtest_parser.add_argument("strategy_id", help="Strategy identifier from the YAML config.")
    backtest_parser.add_argument("--start", required=True, help="Backtest start date YYYY-MM-DD.")
    backtest_parser.add_argument("--end", required=True, help="Backtest end date YYYY-MM-DD.")
    backtest_parser.add_argument(
        "--slippage",
        type=float,
        default=None,
        help="Per-trade slippage as a fraction (overrides strategy config).",
    )
    backtest_parser.add_argument(
        "--initial-equity",
        type=float,
        default=100_000.0,
        help="Starting simulated account equity in USD.",
    )
    backtest_parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run walk-forward validation windows in addition to the full window.",
    )
    backtest_parser.add_argument(
        "--run-id",
        default=None,
        help="Explicit run ID (UUID); auto-generated if omitted.",
    )

    analytics_parser = subparsers.add_parser(
        "analytics",
        help="Query and export backtest results.",
    )
    _add_global_flags(analytics_parser)
    analytics_subparsers = analytics_parser.add_subparsers(
        dest="analytics_command", required=True
    )

    analytics_metrics_parser = analytics_subparsers.add_parser(
        "metrics",
        help="Show performance metrics for a backtest run.",
    )
    _add_global_flags(analytics_metrics_parser)
    analytics_metrics_parser.add_argument("run_id", help="Backtest run ID.")
    analytics_metrics_parser.add_argument(
        "--compare-spy",
        action="store_true",
        help="Include SPY buy-and-hold benchmark comparison.",
    )

    analytics_trades_parser = analytics_subparsers.add_parser(
        "trades",
        help="List trades for a backtest run.",
    )
    _add_global_flags(analytics_trades_parser)
    analytics_trades_parser.add_argument("run_id", help="Backtest run ID.")
    analytics_trades_parser.add_argument(
        "--limit", type=int, default=50, help="Maximum number of trades to show."
    )

    analytics_compare_parser = analytics_subparsers.add_parser(
        "compare",
        help="Side-by-side metrics comparison for two backtest runs.",
    )
    _add_global_flags(analytics_compare_parser)
    analytics_compare_parser.add_argument("run_id_a", help="First backtest run ID.")
    analytics_compare_parser.add_argument("run_id_b", help="Second backtest run ID.")

    analytics_export_parser = analytics_subparsers.add_parser(
        "export",
        help="Export equity curve and trades for a backtest run as CSV.",
    )
    _add_global_flags(analytics_export_parser)
    analytics_export_parser.add_argument("run_id", help="Backtest run ID.")
    analytics_export_parser.add_argument(
        "--output",
        required=True,
        help="Output directory path for exported CSV files.",
    )

    analytics_list_parser = analytics_subparsers.add_parser(
        "list",
        help="List all backtest runs recorded in the event store.",
    )
    _add_global_flags(analytics_list_parser)
    analytics_list_parser.add_argument(
        "--limit", type=int, default=20, help="Maximum number of runs to show."
    )

    promote_parser = subparsers.add_parser(
        "promote",
        help="Advance a strategy to the next stage after passing gate checks.",
    )
    _add_global_flags(promote_parser)
    promote_parser.add_argument(
        "strategy_id",
        help="Strategy identifier from the YAML config.",
    )
    promote_parser.add_argument(
        "--to",
        required=True,
        dest="to_stage",
        choices=("paper", "micro_live", "live"),
        help="Target stage to promote to.",
    )
    promote_parser.add_argument(
        "--run-id",
        default=None,
        help="Backtest run ID (UUID) to use as promotion evidence.",
    )
    promote_parser.add_argument(
        "--lifecycle-exempt",
        action="store_true",
        help="Bypass statistical thresholds (for lifecycle-proof / regime strategies).",
    )
    promote_parser.add_argument(
        "--approved-by",
        default="operator",
        help="Name or identifier of the operator approving the promotion.",
    )
    promote_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required safety flag when promoting to 'live'.",
    )
    promote_parser.add_argument(
        "--notes",
        default=None,
        help="Optional free-form notes recorded with the promotion event.",
    )

    return parser


def _add_global_flags(parser: argparse.ArgumentParser) -> None:
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


def _add_trade_arguments(parser: argparse.ArgumentParser, *, require_paper_flag: bool) -> None:
    parser.add_argument("symbol", help="Ticker symbol.")
    parser.add_argument("--side", required=True, choices=tuple(_SIDE_CHOICES), help="Order side.")
    parser.add_argument("--quantity", required=True, type=float, help="Order quantity.")
    parser.add_argument(
        "--order-type",
        required=True,
        choices=tuple(_ORDER_TYPE_CHOICES),
        help="Order type.",
    )
    parser.add_argument(
        "--time-in-force",
        choices=tuple(_TIF_CHOICES),
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


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_pct(value: float) -> str:
    return f"{value * 100:,.2f}%"


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD format.") from exc


def _account_to_dict(account: AccountInfo) -> dict[str, Any]:
    return {
        "equity": account.equity,
        "cash": account.cash,
        "buying_power": account.buying_power,
        "portfolio_value": account.portfolio_value,
        "daily_pnl": account.daily_pnl,
    }


def _position_to_dict(position: Position) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "avg_entry_price": position.avg_entry_price,
        "current_price": position.current_price,
        "market_value": position.market_value,
        "unrealized_pnl": position.unrealized_pnl,
        "unrealized_pnl_pct": position.unrealized_pnl_pct,
    }


def _order_to_dict(order: Order) -> dict[str, Any]:
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


def _build_status_result(
    account: AccountInfo, trading_mode: str, market_open: bool
) -> CommandResult:
    lines = [
        "Milodex Status",
        f"Trading mode: {trading_mode}",
        f"Market open: {'yes' if market_open else 'no'}",
        f"Equity: {_format_money(account.equity)}",
        f"Cash: {_format_money(account.cash)}",
        f"Buying power: {_format_money(account.buying_power)}",
        f"Portfolio value: {_format_money(account.portfolio_value)}",
        f"Daily P&L: {_format_money(account.daily_pnl)}",
    ]
    data = {
        "trading_mode": trading_mode,
        "market_open": market_open,
        "account": _account_to_dict(account),
    }
    return CommandResult(command="status", data=data, human_lines=lines)


def _build_positions_result(
    positions: list[Position], *, sort_key: str, limit: int
) -> CommandResult:
    if limit < 1:
        raise ValueError("--limit must be at least 1.")
    if not positions:
        return CommandResult(
            command="positions",
            data={"positions": [], "sort": sort_key, "limit": limit},
            human_lines=["Open Positions", "No open positions."],
        )

    sorted_positions = _sort_positions(list(positions), sort_key)[:limit]
    lines = [
        "Open Positions",
        "SYMBOL  QTY       AVG ENTRY    CURRENT      MKT VALUE    UNRLZD P&L   UNRLZD P&L %",
    ]
    for position in sorted_positions:
        lines.append(
            f"{position.symbol:<6}  "
            f"{position.quantity:>8.2f}  "
            f"{_format_money(position.avg_entry_price):>11}  "
            f"{_format_money(position.current_price):>11}  "
            f"{_format_money(position.market_value):>11}  "
            f"{_format_money(position.unrealized_pnl):>11}  "
            f"{_format_pct(position.unrealized_pnl_pct):>12}"
        )
    data = {
        "positions": [_position_to_dict(p) for p in sorted_positions],
        "sort": sort_key,
        "limit": limit,
    }
    return CommandResult(command="positions", data=data, human_lines=lines)


def _sort_positions(positions: list[Position], sort_key: str) -> list[Position]:
    if sort_key == "market-value":
        return sorted(positions, key=lambda position: position.market_value, reverse=True)
    if sort_key == "unrealized-pnl":
        return sorted(positions, key=lambda position: position.unrealized_pnl, reverse=True)
    return sorted(positions, key=lambda position: position.symbol)


def _build_orders_result(
    orders: list[Order], *, symbol: str | None, verbose: bool
) -> CommandResult:
    filtered_orders = list(orders)
    if symbol:
        filtered_orders = [
            order for order in filtered_orders if order.symbol.upper() == symbol.upper()
        ]
    if not filtered_orders:
        return CommandResult(
            command="orders",
            data={"orders": [], "symbol_filter": symbol, "verbose": verbose},
            human_lines=["Recent Orders", "No matching orders."],
        )

    lines = [
        "Recent Orders",
        "ID            SYMBOL  SIDE  TYPE        STATUS             QTY       SUBMITTED",
    ]
    for order in filtered_orders:
        lines.append(
            f"{order.id[:12]:<12}  "
            f"{order.symbol:<6}  "
            f"{order.side.value.upper():<4}  "
            f"{order.order_type.value:<10}  "
            f"{order.status.value:<17}  "
            f"{order.quantity:>8.2f}  "
            f"{order.submitted_at.isoformat()}"
        )
        if verbose:
            details = []
            if order.limit_price is not None:
                details.append(f"limit={_format_money(order.limit_price)}")
            if order.stop_price is not None:
                details.append(f"stop={_format_money(order.stop_price)}")
            if order.filled_quantity is not None:
                details.append(f"filled_qty={order.filled_quantity:.2f}")
            if order.filled_avg_price is not None:
                details.append(f"filled_avg={_format_money(order.filled_avg_price)}")
            if details:
                lines.append(f"  details: {', '.join(details)}")
    data = {
        "orders": [_order_to_dict(o) for o in filtered_orders],
        "symbol_filter": symbol,
        "verbose": verbose,
    }
    return CommandResult(command="orders", data=data, human_lines=lines)


def _build_bars_result(
    symbol: str, timeframe_label: str, barset: BarSet, *, limit: int
) -> CommandResult:
    if limit < 1:
        raise ValueError("--limit must be at least 1.")
    dataframe = barset.to_dataframe()
    if dataframe.empty:
        return CommandResult(
            command="data.bars",
            data={"symbol": symbol.upper(), "timeframe": timeframe_label, "bars": []},
            human_lines=[f"Bars for {symbol.upper()} ({timeframe_label})", "No bars returned."],
        )

    display_df = dataframe.tail(limit).copy()
    display_df["timestamp"] = pd.to_datetime(display_df["timestamp"], utc=True).dt.strftime(
        "%Y-%m-%d %H:%M:%S%z"
    )

    lines = [
        f"Bars for {symbol.upper()} ({timeframe_label})",
        "TIMESTAMP                 OPEN      HIGH       LOW     CLOSE    VOLUME      VWAP",
    ]
    bars_data: list[dict[str, Any]] = []
    for row in display_df.itertuples(index=False):
        vwap_value = float(row.vwap) if pd.notna(row.vwap) else None
        vwap = f"{vwap_value:>8.2f}" if vwap_value is not None else " " * 8
        lines.append(
            f"{row.timestamp:<24}  "
            f"{float(row.open):>8.2f}  "
            f"{float(row.high):>8.2f}  "
            f"{float(row.low):>8.2f}  "
            f"{float(row.close):>8.2f}  "
            f"{int(row.volume):>8}  "
            f"{vwap}"
        )
        bars_data.append(
            {
                "timestamp": row.timestamp,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": int(row.volume),
                "vwap": vwap_value,
            }
        )
    data = {"symbol": symbol.upper(), "timeframe": timeframe_label, "bars": bars_data}
    return CommandResult(command="data.bars", data=data, human_lines=lines)


def _empty_barset() -> BarSet:
    return BarSet(
        pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])
    )


def _build_trade_intent(args: argparse.Namespace) -> TradeIntent:
    return TradeIntent(
        symbol=args.symbol,
        side=_SIDE_CHOICES[args.side],
        quantity=args.quantity,
        order_type=_ORDER_TYPE_CHOICES[args.order_type],
        time_in_force=_TIF_CHOICES[args.time_in_force],
        limit_price=args.limit_price,
        stop_price=args.stop_price,
        strategy_config_path=Path(args.strategy_config) if args.strategy_config else None,
    )


def _execution_result_to_dict(result: ExecutionResult) -> dict[str, Any]:
    request = result.execution_request
    decision = result.risk_decision
    payload: dict[str, Any] = {
        "status": result.status.value,
        "trading_mode": get_trading_mode(),
        "market_open": result.market_open,
        "request": {
            "symbol": request.symbol,
            "side": request.side.value,
            "order_type": request.order_type.value,
            "quantity": request.quantity,
            "time_in_force": request.time_in_force.value,
            "estimated_unit_price": request.estimated_unit_price,
            "estimated_order_value": request.estimated_order_value,
            "strategy_name": request.strategy_name,
            "strategy_stage": request.strategy_stage,
        },
        "risk_decision": {
            "allowed": decision.allowed,
            "summary": decision.summary,
            "checks": [
                {"name": check.name, "passed": check.passed, "message": check.message}
                for check in decision.checks
            ],
        },
        "order": _order_to_dict(result.order) if result.order is not None else None,
        "latest_bar": (
            {
                "timestamp": result.latest_bar.timestamp.isoformat(),
                "close": result.latest_bar.close,
            }
            if result.latest_bar is not None
            else None
        ),
        "message": result.message,
    }
    return payload


def _build_execution_result(
    result: ExecutionResult, *, command: str
) -> CommandResult:
    request = result.execution_request
    lines = [
        "Trade Execution",
        f"Status: {result.status.value}",
        f"Symbol: {request.symbol}",
        f"Side: {request.side.value}",
        f"Order type: {request.order_type.value}",
        f"Quantity: {request.quantity:.2f}",
        f"Time in force: {request.time_in_force.value}",
        f"Estimated unit price: {_format_money(request.estimated_unit_price)}",
        f"Estimated order value: {_format_money(request.estimated_order_value)}",
        f"Trading mode: {get_trading_mode()}",
        f"Market open: {'yes' if result.market_open else 'no'}",
    ]
    if request.strategy_name:
        lines.append(f"Strategy: {request.strategy_name} ({request.strategy_stage})")
    if result.latest_bar is not None:
        lines.append(
            "Latest bar: "
            f"{result.latest_bar.timestamp.isoformat()} "
            f"close={_format_money(result.latest_bar.close)}"
        )
    lines.append("Risk checks:")
    for check in result.risk_decision.checks:
        prefix = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{prefix}] {check.name}: {check.message}")
    lines.append(f"Decision: {'allow' if result.risk_decision.allowed else 'block'}")
    if result.order is not None:
        lines.append(f"Broker order ID: {result.order.id}")
        lines.append(f"Broker status: {result.order.status.value}")
    if result.message:
        lines.append(f"Message: {result.message}")
    return CommandResult(
        command=command,
        data=_execution_result_to_dict(result),
        human_lines=lines,
    )


def _build_order_status_result(order: Order) -> CommandResult:
    lines = [
        "Order Status",
        f"ID: {order.id}",
        f"Symbol: {order.symbol}",
        f"Side: {order.side.value}",
        f"Type: {order.order_type.value}",
        f"Status: {order.status.value}",
        f"Quantity: {order.quantity:.2f}",
        f"Submitted: {order.submitted_at.isoformat()}",
    ]
    if order.limit_price is not None:
        lines.append(f"Limit price: {_format_money(order.limit_price)}")
    if order.stop_price is not None:
        lines.append(f"Stop price: {_format_money(order.stop_price)}")
    if order.filled_quantity is not None:
        lines.append(f"Filled quantity: {order.filled_quantity:.2f}")
    if order.filled_avg_price is not None:
        lines.append(f"Filled average price: {_format_money(order.filled_avg_price)}")
    if order.filled_at is not None:
        lines.append(f"Filled at: {order.filled_at.isoformat()}")
    return CommandResult(
        command="trade.order-status",
        data={"order": _order_to_dict(order)},
        human_lines=lines,
    )


def _build_cancel_result(
    cancelled: bool, order: Order | None, order_id: str
) -> CommandResult:
    if cancelled:
        lines = [f"Order cancel requested successfully: {order_id}"]
        if order is not None:
            lines.append(f"Latest status: {order.status.value}")
    else:
        lines = [f"Order could not be cancelled: {order_id}"]
    data = {
        "order_id": order_id,
        "cancelled": cancelled,
        "order": _order_to_dict(order) if order is not None else None,
    }
    return CommandResult(
        command="trade.cancel",
        status="success" if cancelled else "error",
        data=data,
        human_lines=lines,
        errors=[]
        if cancelled
        else [{"code": "cancel_failed", "message": f"Order could not be cancelled: {order_id}"}],
    )


def _build_kill_switch_result(
    active: bool, reason: str | None, triggered_at: str | None
) -> CommandResult:
    lines = [
        "Kill Switch",
        f"Active: {'yes' if active else 'no'}",
    ]
    if reason:
        lines.append(f"Reason: {reason}")
    if triggered_at:
        lines.append(f"Last triggered: {triggered_at}")
    return CommandResult(
        command="trade.kill-switch.status",
        data={
            "active": active,
            "reason": reason,
            "last_triggered_at": triggered_at,
        },
        human_lines=lines,
    )


def _build_validate_result(path: Path, kind: str | None) -> CommandResult:
    lines = validate_config_file(path, kind=kind)
    detected_kind: str | None = None
    for line in lines:
        if line.startswith("Detected kind:"):
            detected_kind = line.split(":", 1)[1].strip()
            break
    return CommandResult(
        command="config.validate",
        data={"path": str(path), "kind": detected_kind or kind, "messages": list(lines)},
        human_lines=lines,
    )


def _build_backtest_result(
    result: BacktestResult, *, walk_forward_windows: list | None
) -> CommandResult:
    trade_summary = (
        f"{result.trade_count} ({result.buy_count} buys, {result.sell_count} sells)"
    )
    lines = [
        "Backtest Result",
        f"Strategy:       {result.strategy_id}",
        f"Run ID:         {result.run_id}",
        f"Period:         {result.start_date} to {result.end_date}",
        f"Trading days:   {result.trading_days}",
        f"Initial equity: {_format_money(result.initial_equity)}",
        f"Final equity:   {_format_money(result.final_equity)}",
        f"Total return:   {result.total_return_pct:+.2f}%",
        f"Trades:         {trade_summary}",
        f"Slippage:       {result.slippage_pct * 100:.2f}%",
        f"Commission:     {_format_money(result.commission_per_trade)}/trade",
    ]
    data: dict[str, Any] = {
        "run_id": result.run_id,
        "strategy_id": result.strategy_id,
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "trading_days": result.trading_days,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "total_return_pct": result.total_return_pct,
        "trade_count": result.trade_count,
        "buy_count": result.buy_count,
        "sell_count": result.sell_count,
        "slippage_pct": result.slippage_pct,
        "commission_per_trade": result.commission_per_trade,
    }
    if walk_forward_windows:
        lines.append(f"Walk-forward windows: {len(walk_forward_windows)}")
        data["walk_forward_windows"] = walk_forward_windows
    return CommandResult(command="backtest", data=data, human_lines=lines)


def _metrics_to_dict(m: PerformanceMetrics) -> dict[str, Any]:
    return {
        "run_id": m.run_id,
        "strategy_id": m.strategy_id,
        "start_date": m.start_date.isoformat(),
        "end_date": m.end_date.isoformat(),
        "initial_equity": m.initial_equity,
        "final_equity": m.final_equity,
        "total_return_pct": m.total_return_pct,
        "cagr_pct": m.cagr_pct,
        "max_drawdown_pct": m.max_drawdown_pct,
        "sharpe_ratio": m.sharpe_ratio,
        "sortino_ratio": m.sortino_ratio,
        "trade_count": m.trade_count,
        "buy_count": m.buy_count,
        "sell_count": m.sell_count,
        "win_rate_pct": m.win_rate_pct,
        "avg_hold_days": m.avg_hold_days,
        "winning_trades": m.winning_trades,
        "losing_trades": m.losing_trades,
        "trading_days": m.trading_days,
        "confidence_label": m.confidence_label,
    }


def _build_metrics_lines(m: PerformanceMetrics, label: str = "Strategy") -> list[str]:
    lines = [
        f"  {label}:",
        f"    Strategy ID:    {m.strategy_id}",
        f"    Run ID:         {m.run_id}",
        f"    Period:         {m.start_date} to {m.end_date}",
        f"    Trading days:   {m.trading_days}",
        f"    Total return:   {m.total_return_pct:+.2f}%",
        f"    CAGR:           {m.cagr_pct:+.2f}%" if m.cagr_pct is not None else "    CAGR:           n/a",  # noqa: E501
        f"    Max drawdown:   {m.max_drawdown_pct:.2f}%",
        (
            f"    Sharpe:         {m.sharpe_ratio:.2f}"
            if m.sharpe_ratio is not None
            else "    Sharpe:         n/a"
        ),
        (
            f"    Sortino:        {m.sortino_ratio:.2f}"
            if m.sortino_ratio is not None
            else "    Sortino:        n/a"
        ),
        (
            f"    Win rate:       {m.win_rate_pct:.1f}%"
            if m.win_rate_pct is not None
            else "    Win rate:       n/a"
        ),
        (
            f"    Avg hold:       {m.avg_hold_days:.1f}d"
            if m.avg_hold_days is not None
            else "    Avg hold:       n/a"
        ),
        (
            f"    Trades:         {m.trade_count} "
            f"({m.buy_count}B/{m.sell_count}S, "
            f"{m.winning_trades}W/{m.losing_trades}L)"
        ),
        f"    Confidence:     {m.confidence_label}",
    ]
    return lines


def _build_analytics_metrics_result(
    strategy_metrics: PerformanceMetrics,
    benchmark_metrics: PerformanceMetrics | None,
) -> CommandResult:
    lines = ["Performance Metrics"]
    lines.extend(_build_metrics_lines(strategy_metrics, label="Strategy"))
    if benchmark_metrics is not None:
        lines.append("")
        lines.extend(_build_metrics_lines(benchmark_metrics, label="SPY Benchmark"))
    data: dict[str, Any] = {
        "strategy": _metrics_to_dict(strategy_metrics),
        "benchmark": _metrics_to_dict(benchmark_metrics) if benchmark_metrics else None,
    }
    return CommandResult(command="analytics.metrics", data=data, human_lines=lines)


def _build_analytics_trades_result(
    run_id: str,
    trades: list,
    *,
    limit: int,
) -> CommandResult:
    if not trades:
        return CommandResult(
            command="analytics.trades",
            data={"run_id": run_id, "trades": [], "total": 0},
            human_lines=[f"Trades for run {run_id}", "No trades found."],
        )
    shown = trades[:limit]
    lines = [
        f"Trades for run {run_id} (showing {len(shown)} of {len(trades)})",
        "DATE        SYMBOL  SIDE  QTY        FILL PRICE",
    ]
    trades_data = []
    for t in shown:
        lines.append(
            f"{str(t.recorded_at)[:10]}  "
            f"{t.symbol:<6}  "
            f"{t.side:<4}  "
            f"{t.quantity:>9.2f}  "
            f"{_format_money(t.estimated_unit_price)}"
        )
        trades_data.append(
            {
                "recorded_at": t.recorded_at.isoformat(),
                "symbol": t.symbol,
                "side": t.side,
                "quantity": t.quantity,
                "estimated_unit_price": t.estimated_unit_price,
                "estimated_order_value": t.estimated_order_value,
                "status": t.status,
            }
        )
    return CommandResult(
        command="analytics.trades",
        data={"run_id": run_id, "trades": trades_data, "total": len(trades)},
        human_lines=lines,
    )


def _build_analytics_compare_result(
    metrics_a: PerformanceMetrics,
    metrics_b: PerformanceMetrics,
) -> CommandResult:
    lines = ["Backtest Comparison"]
    lines.extend(_build_metrics_lines(metrics_a, label=f"Run A ({metrics_a.run_id[:8]}…)"))
    lines.append("")
    lines.extend(_build_metrics_lines(metrics_b, label=f"Run B ({metrics_b.run_id[:8]}…)"))
    return CommandResult(
        command="analytics.compare",
        data={"run_a": _metrics_to_dict(metrics_a), "run_b": _metrics_to_dict(metrics_b)},
        human_lines=lines,
    )


def _build_analytics_export_result(run_id: str, output_dir: Path) -> CommandResult:
    return CommandResult(
        command="analytics.export",
        data={"run_id": run_id, "output_dir": str(output_dir)},
        human_lines=[
            f"Exported backtest data for run {run_id}",
            f"Output directory: {output_dir}",
        ],
    )


def _build_analytics_list_result(runs: list, *, limit: int) -> CommandResult:
    shown = runs[:limit]
    if not shown:
        return CommandResult(
            command="analytics.list",
            data={"runs": [], "total": 0},
            human_lines=["Backtest Runs", "No backtest runs found."],
        )
    lines = [
        f"Backtest Runs (showing {len(shown)} of {len(runs)})",
        "RUN ID                                STATUS     STRATEGY"
        "                              START       END         TRADES",
    ]
    runs_data = []
    for run in shown:
        meta = run.metadata or {}
        trade_count = meta.get("trade_count", "?")
        lines.append(
            f"{run.run_id:<36}  "
            f"{run.status:<10} "
            f"{run.strategy_id[:36]:<36}  "
            f"{str(run.start_date)[:10]}  "
            f"{str(run.end_date)[:10]}  "
            f"{trade_count}"
        )
        runs_data.append(
            {
                "run_id": run.run_id,
                "strategy_id": run.strategy_id,
                "status": run.status,
                "start_date": str(run.start_date)[:10],
                "end_date": str(run.end_date)[:10],
                "slippage_pct": run.slippage_pct,
            }
        )
    return CommandResult(
        command="analytics.list",
        data={"runs": runs_data, "total": len(runs)},
        human_lines=lines,
    )


def _update_stage_in_config(path: Path, from_stage: str, to_stage: str) -> None:
    """Replace the ``stage:`` value in a strategy YAML config in-place.

    Uses simple text replacement so all comments and formatting are preserved.
    Raises ``ValueError`` if the expected string is not found.
    """
    content = path.read_text(encoding="utf-8")
    old = f'stage: "{from_stage}"'
    new = f'stage: "{to_stage}"'
    if old not in content:
        msg = (
            f"Could not find 'stage: \"{from_stage}\"' in {path}. "
            "Was the config modified externally?"
        )
        raise ValueError(msg)
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def _build_promotion_result(
    *,
    strategy_id: str,
    from_stage: str,
    to_stage: str,
    gate_result: Any,
    promoted: bool,
) -> CommandResult:
    data: dict[str, Any] = {
        "strategy_id": strategy_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "promoted": promoted,
        "promotion_type": gate_result.promotion_type,
        "gate_failures": list(gate_result.failures),
        "metrics": {
            "sharpe_ratio": gate_result.sharpe_ratio,
            "max_drawdown_pct": gate_result.max_drawdown_pct,
            "trade_count": gate_result.trade_count,
        },
    }
    if promoted:
        lines: list[str] = [
            "Strategy Promotion",
            f"Strategy:       {strategy_id}",
            f"Stage:          {from_stage} -> {to_stage}",
            f"Type:           {gate_result.promotion_type}",
            "Gate checks:    all passed",
            "Result:         promotion recorded, config updated.",
        ]
        return CommandResult(command="promote", data=data, human_lines=lines)

    lines = [
        "Strategy Promotion — BLOCKED",
        f"Strategy:       {strategy_id}",
        f"Stage:          {from_stage} -> {to_stage}",
        "Gate check failures:",
    ]
    for failure in gate_result.failures:
        lines.append(f"  - {failure}")
    return CommandResult(
        command="promote",
        status="error",
        data=data,
        human_lines=lines,
        errors=[{"code": "gate_check_failed", "message": f} for f in gate_result.failures],
    )


def _error_result(command: str, message: str, code: str = "error") -> CommandResult:
    return CommandResult(
        command=command,
        status="error",
        human_lines=[f"Error: {message}"],
        errors=[{"code": code, "message": message}],
    )


def _command_name_from_args(args: argparse.Namespace) -> str:
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


def main(
    argv: list[str] | None = None,
    *,
    broker_factory=AlpacaBrokerClient,
    data_provider_factory=AlpacaDataProvider,
    execution_service_factory=None,
    strategy_runner_factory=None,
    backtest_engine_factory=None,
    event_store_factory=None,
    config_dir: Path = Path("configs"),
    locks_dir: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a process exit code."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    _locks_dir = locks_dir if locks_dir is not None else get_locks_dir()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    as_json = bool(getattr(args, "json_output", False))
    formatter = get_formatter(as_json=as_json)
    command_name = _command_name_from_args(args)

    def get_execution_service() -> ExecutionService:
        if execution_service_factory is not None:
            return execution_service_factory()
        return ExecutionService(
            broker_client=broker_factory(),
            data_provider=data_provider_factory(),
        )

    def get_strategy_runner(strategy_id: str) -> StrategyRunner:
        if strategy_runner_factory is not None:
            return strategy_runner_factory(strategy_id)
        broker = broker_factory()
        data_provider = data_provider_factory()
        event_store = get_event_store()
        kill_switch_store = KillSwitchStateStore(
            event_store=event_store,
            legacy_path=get_logs_dir() / "kill_switch_state.json",
        )
        execution_service = ExecutionService(
            broker_client=broker,
            data_provider=data_provider,
            kill_switch_store=kill_switch_store,
            event_store=event_store,
        )
        return StrategyRunner(
            strategy_id=strategy_id,
            config_dir=Path("configs"),
            broker_client=broker,
            data_provider=data_provider,
            execution_service=execution_service,
            event_store=event_store,
        )

    def get_event_store() -> EventStore:
        if event_store_factory is not None:
            return event_store_factory()
        return EventStore(get_data_dir() / "milodex.db")

    def get_backtest_engine(strategy_id: str, **kwargs) -> BacktestEngine:
        if backtest_engine_factory is not None:
            return backtest_engine_factory(strategy_id, **kwargs)
        loader = StrategyLoader()
        config_path = _resolve_strategy_config(strategy_id, config_dir)
        loaded = loader.load(config_path)
        event_store = get_event_store()
        data_provider = data_provider_factory()
        return BacktestEngine(
            loaded=loaded,
            data_provider=data_provider,
            event_store=event_store,
            **kwargs,
        )

    try:
        result = _dispatch_command(
            args,
            get_execution_service=get_execution_service,
            get_strategy_runner=get_strategy_runner,
            get_backtest_engine=get_backtest_engine,
            get_event_store=get_event_store,
            broker_factory=broker_factory,
            data_provider_factory=data_provider_factory,
            config_dir=config_dir,
            locks_dir=_locks_dir,
        )
    except AdvisoryLockError as exc:
        result = _error_result(command_name, str(exc), code="advisory_lock_held")
        print(formatter.render(result), file=stderr)
        return 1
    except (BrokerError, ValueError) as exc:
        result = _error_result(command_name, str(exc), code="error")
        print(formatter.render(result), file=stderr)
        return 1

    rendered = formatter.render(result)
    if rendered:
        stream = stdout if result.status == "success" else stderr
        print(rendered, file=stream)
    return 0 if result.status == "success" else 1


def _resolve_strategy_config(strategy_id: str, config_dir: Path = Path("configs")) -> Path:
    """Locate the YAML file whose strategy.id matches ``strategy_id``."""
    from milodex.strategies.loader import load_strategy_config

    for path in sorted(config_dir.glob("*.yaml")):
        try:
            config = load_strategy_config(path)
        except ValueError:
            continue
        if config.strategy_id == strategy_id:
            return path
    msg = f"Strategy config not found for strategy id: {strategy_id}"
    raise ValueError(msg)


def _dispatch_command(
    args: argparse.Namespace,
    *,
    get_execution_service,
    get_strategy_runner,
    get_backtest_engine,
    get_event_store,
    broker_factory,
    data_provider_factory,
    config_dir: Path = Path("configs"),
    locks_dir: Path | None = None,
) -> CommandResult:
    """Run the selected command and return its structured result."""
    if args.command == "status":
        broker = broker_factory()
        return _build_status_result(
            broker.get_account(), get_trading_mode(), broker.is_market_open()
        )
    if args.command == "positions":
        broker = broker_factory()
        return _build_positions_result(
            broker.get_positions(), sort_key=args.sort, limit=args.limit
        )
    if args.command == "orders":
        broker = broker_factory()
        return _build_orders_result(
            broker.get_orders(status=args.status, limit=args.limit),
            symbol=args.symbol,
            verbose=args.verbose,
        )
    if args.command == "data" and args.data_command == "bars":
        provider = data_provider_factory()
        symbol = args.symbol.upper()
        start = _parse_iso_date(args.start)
        end = _parse_iso_date(args.end)
        if end < start:
            raise ValueError("--end must be on or after --start.")
        timeframe = _TIMEFRAME_CHOICES[args.timeframe]
        bars_by_symbol = provider.get_bars([symbol], timeframe, start, end)
        barset = bars_by_symbol.get(symbol) or _empty_barset()
        return _build_bars_result(symbol, args.timeframe, barset, limit=args.limit)
    if args.command == "config" and args.config_command == "validate":
        return _build_validate_result(Path(args.path), args.kind)
    if args.command == "trade":
        service = get_execution_service()
        if args.trade_command == "preview":
            return _build_execution_result(
                service.preview(_build_trade_intent(args)), command="trade.preview"
            )
        if args.trade_command == "submit":
            if not args.paper:
                raise ValueError("trade submit requires --paper.")
            with AdvisoryLock(
                "milodex.runtime",
                locks_dir=locks_dir or get_locks_dir(),
                holder_name="milodex trade submit",
            ):
                return _build_execution_result(
                    service.submit_paper(_build_trade_intent(args)),
                    command="trade.submit",
                )
        if args.trade_command == "order-status":
            return _build_order_status_result(service.get_order_status(args.order_id))
        if args.trade_command == "cancel":
            cancelled, order = service.cancel_order(args.order_id)
            return _build_cancel_result(cancelled, order, args.order_id)
        if args.trade_command == "kill-switch" and args.kill_switch_command == "status":
            state = service.get_kill_switch_state()
            return _build_kill_switch_result(
                state.active, state.reason, state.last_triggered_at
            )
        raise ValueError(f"Unsupported trade command: {args.trade_command}")
    if args.command == "strategy" and args.strategy_command == "run":
        if get_trading_mode() != "paper":
            raise ValueError("strategy run is paper-only in Phase 1.")
        with AdvisoryLock(
            "milodex.runtime",
            locks_dir=locks_dir or get_locks_dir(),
            holder_name=f"milodex strategy run {args.strategy_id}",
        ):
            runner = get_strategy_runner(args.strategy_id)
            runner.run()
            return CommandResult(
                command="strategy.run",
                data={"strategy_id": args.strategy_id, "trading_mode": get_trading_mode()},
                human_lines=[f"Running strategy: {args.strategy_id}"],
            )

    if args.command == "backtest":
        start = _parse_iso_date(args.start)
        end = _parse_iso_date(args.end)
        if end < start:
            raise ValueError("--end must be on or after --start.")
        engine_kwargs: dict[str, Any] = {"initial_equity": args.initial_equity}
        if args.slippage is not None:
            engine_kwargs["slippage_pct"] = args.slippage
        if args.run_id is not None:
            run_id_kwarg = args.run_id
        else:
            run_id_kwarg = None

        engine = get_backtest_engine(args.strategy_id, **engine_kwargs)
        backtest_result = engine.run(start, end, run_id=run_id_kwarg)

        walk_windows = None
        if args.walk_forward:
            loaded = engine._loaded  # noqa: SLF001
            from milodex.backtesting.engine import _trading_days_in_range

            all_bars = data_provider_factory().get_bars(
                symbols=list(loaded.context.universe),
                timeframe=_TIMEFRAME_CHOICES["1d"],
                start=start,
                end=end,
            )
            trading_days = _trading_days_in_range(all_bars, start, end)
            wf_config = loaded.config.backtest
            wf_windows_count = int(wf_config.get("walk_forward_windows", 4))
            total_days = len(trading_days)
            if total_days >= 2:
                test_days = max(1, total_days // (wf_windows_count + 1))
                train_days = total_days - wf_windows_count * test_days
                if train_days >= 1:
                    splitter = WalkForwardSplitter()
                    walk_windows = [
                        {
                            "train_start": ts.isoformat(),
                            "train_end": te.isoformat(),
                            "test_start": vs.isoformat(),
                            "test_end": ve.isoformat(),
                        }
                        for ts, te, vs, ve in splitter.split(
                            trading_days,
                            train_days=train_days,
                            test_days=test_days,
                            step_days=test_days,
                        )
                    ]

        return _build_backtest_result(backtest_result, walk_forward_windows=walk_windows)

    if args.command == "analytics":
        event_store = get_event_store()

        if args.analytics_command == "list":
            all_runs = event_store.list_backtest_runs()
            return _build_analytics_list_result(all_runs, limit=args.limit)

        if args.analytics_command == "metrics":
            run = event_store.get_backtest_run(args.run_id)
            if run is None:
                raise ValueError(f"Backtest run not found: {args.run_id}")
            if run.id is None:
                raise ValueError(f"Backtest run has no DB id: {args.run_id}")
            raw_trades = event_store.list_trades_for_backtest_run(run.id)
            equity_curve = _equity_curve_from_trades(raw_trades, run.metadata or {})
            trades_dicts = [
                {
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "estimated_unit_price": t.estimated_unit_price,
                    "recorded_at": t.recorded_at.isoformat(),
                }
                for t in raw_trades
            ]
            strategy_metrics = compute_metrics(
                run_id=run.run_id,
                strategy_id=run.strategy_id,
                start_date=run.start_date.date() if run.start_date else date.today(),
                end_date=run.end_date.date() if run.end_date else date.today(),
                initial_equity=run.metadata.get("initial_equity", 100_000.0),
                equity_curve=equity_curve,
                trades=trades_dicts,
            )
            benchmark_metrics = None
            if args.compare_spy:
                provider = data_provider_factory()
                benchmark_metrics = compute_benchmark(
                    start_date=strategy_metrics.start_date,
                    end_date=strategy_metrics.end_date,
                    initial_equity=strategy_metrics.initial_equity,
                    data_provider=provider,
                )
            return _build_analytics_metrics_result(strategy_metrics, benchmark_metrics)

        if args.analytics_command == "trades":
            run = event_store.get_backtest_run(args.run_id)
            if run is None:
                raise ValueError(f"Backtest run not found: {args.run_id}")
            if run.id is None:
                raise ValueError(f"Backtest run has no DB id: {args.run_id}")
            trades = event_store.list_trades_for_backtest_run(run.id)
            return _build_analytics_trades_result(args.run_id, trades, limit=args.limit)

        if args.analytics_command == "compare":
            run_a = event_store.get_backtest_run(args.run_id_a)
            run_b = event_store.get_backtest_run(args.run_id_b)
            if run_a is None:
                raise ValueError(f"Backtest run not found: {args.run_id_a}")
            if run_b is None:
                raise ValueError(f"Backtest run not found: {args.run_id_b}")
            metrics_a = _metrics_for_run(run_a, event_store)
            metrics_b = _metrics_for_run(run_b, event_store)
            return _build_analytics_compare_result(metrics_a, metrics_b)

        if args.analytics_command == "export":
            run = event_store.get_backtest_run(args.run_id)
            if run is None:
                raise ValueError(f"Backtest run not found: {args.run_id}")
            if run.id is None:
                raise ValueError(f"Backtest run has no DB id: {args.run_id}")
            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)
            trades = event_store.list_trades_for_backtest_run(run.id)
            equity_curve = _equity_curve_from_trades(trades, run.metadata or {})
            _export_trades_csv(trades, output_dir / f"{args.run_id}_trades.csv")
            _export_equity_curve_csv(equity_curve, output_dir / f"{args.run_id}_equity.csv")
            return _build_analytics_export_result(args.run_id, output_dir)

        raise ValueError(f"Unsupported analytics command: {args.analytics_command}")

    if args.command == "promote":
        from milodex.core.event_store import PromotionEvent
        from milodex.strategies.loader import load_strategy_config
        from milodex.strategies.promotion import check_gate, validate_stage_transition

        config_path = _resolve_strategy_config(args.strategy_id, config_dir)
        config = load_strategy_config(config_path)
        from_stage = config.stage
        to_stage = args.to_stage

        validate_stage_transition(from_stage, to_stage)

        if to_stage == "live" and not args.confirm:
            raise ValueError(
                "Promoting to 'live' requires --confirm. "
                "This action will be recorded and is irreversible."
            )

        sharpe_ratio = None
        max_drawdown_pct = None
        trade_count = None
        if args.run_id is not None:
            event_store = get_event_store()
            run = event_store.get_backtest_run(args.run_id)
            if run is None:
                raise ValueError(f"Backtest run not found: {args.run_id}")
            metrics = _metrics_for_run(run, event_store)
            sharpe_ratio = metrics.sharpe_ratio
            max_drawdown_pct = metrics.max_drawdown_pct
            trade_count = metrics.trade_count

        gate_result = check_gate(
            lifecycle_exempt=args.lifecycle_exempt,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
        )

        if not gate_result.allowed:
            return _build_promotion_result(
                strategy_id=args.strategy_id,
                from_stage=from_stage,
                to_stage=to_stage,
                gate_result=gate_result,
                promoted=False,
            )

        event_store = get_event_store()
        event_store.append_promotion(
            PromotionEvent(
                strategy_id=args.strategy_id,
                from_stage=from_stage,
                to_stage=to_stage,
                promotion_type=gate_result.promotion_type,
                approved_by=args.approved_by,
                recorded_at=datetime.now(UTC),
                backtest_run_id=args.run_id,
                sharpe_ratio=gate_result.sharpe_ratio,
                max_drawdown_pct=gate_result.max_drawdown_pct,
                trade_count=gate_result.trade_count,
                notes=args.notes,
            )
        )
        _update_stage_in_config(config_path, from_stage, to_stage)
        return _build_promotion_result(
            strategy_id=args.strategy_id,
            from_stage=from_stage,
            to_stage=to_stage,
            gate_result=gate_result,
            promoted=True,
        )

    raise ValueError(f"Unsupported command: {args.command}")


def _equity_curve_from_trades(
    trades: list,
    metadata: dict[str, Any],
) -> list[tuple[date, float]]:
    """Reconstruct equity curve from metadata or estimate from trades.

    The engine stores the equity curve in ``backtest_runs.metadata_json`` as
    a list of ``[iso_date, value]`` pairs.  Fall back to an empty curve when
    the metadata key is absent (e.g. runs created before this field existed).
    """
    raw = metadata.get("equity_curve", [])
    if raw:
        result = []
        for item in raw:
            try:
                d = date.fromisoformat(str(item[0]))
                v = float(item[1])
                result.append((d, v))
            except (ValueError, IndexError, TypeError):
                continue
        if result:
            return result

    # Fallback: no stored equity curve — return empty
    return []


def _metrics_for_run(run, event_store: EventStore) -> PerformanceMetrics:
    """Build PerformanceMetrics for a BacktestRunEvent from the event store."""
    if run.id is None:
        raise ValueError(f"Backtest run has no DB id: {run.run_id}")
    raw_trades = event_store.list_trades_for_backtest_run(run.id)
    equity_curve = _equity_curve_from_trades(raw_trades, run.metadata or {})
    trades_dicts = [
        {
            "symbol": t.symbol,
            "side": t.side,
            "quantity": t.quantity,
            "estimated_unit_price": t.estimated_unit_price,
            "recorded_at": t.recorded_at.isoformat(),
        }
        for t in raw_trades
    ]
    return compute_metrics(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        start_date=run.start_date.date() if run.start_date else date.today(),
        end_date=run.end_date.date() if run.end_date else date.today(),
        initial_equity=run.metadata.get("initial_equity", 100_000.0) if run.metadata else 100_000.0,
        equity_curve=equity_curve,
        trades=trades_dicts,
    )


def _export_trades_csv(trades: list, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "recorded_at", "symbol", "side", "quantity",
                "estimated_unit_price", "estimated_order_value", "status",
            ],
        )
        writer.writeheader()
        for t in trades:
            writer.writerow(
                {
                    "recorded_at": t.recorded_at.isoformat(),
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "estimated_unit_price": t.estimated_unit_price,
                    "estimated_order_value": t.estimated_order_value,
                    "status": t.status,
                }
            )


def _export_equity_curve_csv(
    equity_curve: list[tuple[date, float]], path: Path
) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["date", "portfolio_value"])
        writer.writeheader()
        for d, v in equity_curve:
            writer.writerow({"date": d.isoformat(), "portfolio_value": v})


if __name__ == "__main__":
    raise SystemExit(main())

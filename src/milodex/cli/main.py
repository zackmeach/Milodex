"""CLI entrypoint for operator workflows."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import TextIO

import pandas as pd

from milodex.broker import BrokerError, OrderSide, OrderType, TimeInForce
from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.models import AccountInfo, Order, Position
from milodex.cli.config_validation import validate_config_file
from milodex.config import get_trading_mode
from milodex.data import BarSet, Timeframe
from milodex.data.alpaca_provider import AlpacaDataProvider
from milodex.execution import ExecutionService, TradeIntent
from milodex.execution.models import ExecutionResult

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
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show trading mode, market status, and account summary.")

    positions_parser = subparsers.add_parser("positions", help="List open positions.")
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
    data_subparsers = data_parser.add_subparsers(dest="data_command", required=True)
    bars_parser = data_subparsers.add_parser("bars", help="Fetch bars for a symbol.")
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
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    validate_parser = config_subparsers.add_parser("validate", help="Validate a YAML config file.")
    validate_parser.add_argument("path", help="Path to the YAML config file.")
    validate_parser.add_argument(
        "--kind",
        choices=("strategy", "risk"),
        help="Optional config kind override.",
    )

    trade_parser = subparsers.add_parser("trade", help="Preview and submit paper trades.")
    trade_subparsers = trade_parser.add_subparsers(dest="trade_command", required=True)

    preview_parser = trade_subparsers.add_parser(
        "preview",
        help="Preview a trade through risk checks.",
    )
    _add_trade_arguments(preview_parser, require_paper_flag=False)

    submit_parser = trade_subparsers.add_parser(
        "submit",
        help="Submit a paper trade after risk checks.",
    )
    _add_trade_arguments(submit_parser, require_paper_flag=True)

    order_status_parser = trade_subparsers.add_parser(
        "order-status",
        help="Fetch current broker status for an order.",
    )
    order_status_parser.add_argument("order_id", help="Broker order ID.")

    cancel_parser = trade_subparsers.add_parser("cancel", help="Cancel an existing order.")
    cancel_parser.add_argument("order_id", help="Broker order ID.")

    kill_switch_parser = trade_subparsers.add_parser(
        "kill-switch",
        help="Inspect kill switch state.",
    )
    kill_switch_subparsers = kill_switch_parser.add_subparsers(
        dest="kill_switch_command",
        required=True,
    )
    kill_switch_subparsers.add_parser("status", help="Show current kill switch state.")

    return parser


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


def _write_lines(stream: TextIO, lines: list[str]) -> None:
    for line in lines:
        print(line, file=stream)


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD format.") from exc


def _render_status(account: AccountInfo, trading_mode: str, market_open: bool) -> list[str]:
    return [
        "Milodex Status",
        f"Trading mode: {trading_mode}",
        f"Market open: {'yes' if market_open else 'no'}",
        f"Equity: {_format_money(account.equity)}",
        f"Cash: {_format_money(account.cash)}",
        f"Buying power: {_format_money(account.buying_power)}",
        f"Portfolio value: {_format_money(account.portfolio_value)}",
        f"Daily P&L: {_format_money(account.daily_pnl)}",
    ]


def _render_positions(positions: list[Position], *, sort_key: str, limit: int) -> list[str]:
    if not positions:
        return ["Open Positions", "No open positions."]
    if limit < 1:
        raise ValueError("--limit must be at least 1.")

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
    return lines


def _sort_positions(positions: list[Position], sort_key: str) -> list[Position]:
    if sort_key == "market-value":
        return sorted(positions, key=lambda position: position.market_value, reverse=True)
    if sort_key == "unrealized-pnl":
        return sorted(positions, key=lambda position: position.unrealized_pnl, reverse=True)
    return sorted(positions, key=lambda position: position.symbol)


def _render_orders(orders: list[Order], *, symbol: str | None, verbose: bool) -> list[str]:
    filtered_orders = list(orders)
    if symbol:
        filtered_orders = [
            order for order in filtered_orders if order.symbol.upper() == symbol.upper()
        ]
    if not filtered_orders:
        return ["Recent Orders", "No matching orders."]

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
    return lines


def _render_bars(symbol: str, timeframe_label: str, barset: BarSet, *, limit: int) -> list[str]:
    if limit < 1:
        raise ValueError("--limit must be at least 1.")
    dataframe = barset.to_dataframe()
    if dataframe.empty:
        return [f"Bars for {symbol.upper()} ({timeframe_label})", "No bars returned."]

    display_df = dataframe.tail(limit).copy()
    display_df["timestamp"] = pd.to_datetime(display_df["timestamp"], utc=True).dt.strftime(
        "%Y-%m-%d %H:%M:%S%z"
    )

    lines = [
        f"Bars for {symbol.upper()} ({timeframe_label})",
        "TIMESTAMP                 OPEN      HIGH       LOW     CLOSE    VOLUME      VWAP",
    ]
    for row in display_df.itertuples(index=False):
        vwap = f"{float(row.vwap):>8.2f}" if pd.notna(row.vwap) else " " * 8
        lines.append(
            f"{row.timestamp:<24}  "
            f"{float(row.open):>8.2f}  "
            f"{float(row.high):>8.2f}  "
            f"{float(row.low):>8.2f}  "
            f"{float(row.close):>8.2f}  "
            f"{int(row.volume):>8}  "
            f"{vwap}"
        )
    return lines


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


def _render_execution_result(result: ExecutionResult) -> list[str]:
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
    return lines


def _render_order_status(order: Order) -> list[str]:
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
    return lines


def _render_cancel_result(cancelled: bool, order: Order | None, order_id: str) -> list[str]:
    if cancelled:
        lines = [f"Order cancel requested successfully: {order_id}"]
        if order is not None:
            lines.append(f"Latest status: {order.status.value}")
        return lines
    return [f"Order could not be cancelled: {order_id}"]


def _render_kill_switch_status(
    active: bool,
    reason: str | None,
    triggered_at: str | None,
) -> list[str]:
    lines = [
        "Kill Switch",
        f"Active: {'yes' if active else 'no'}",
    ]
    if reason:
        lines.append(f"Reason: {reason}")
    if triggered_at:
        lines.append(f"Last triggered: {triggered_at}")
    return lines


def main(
    argv: list[str] | None = None,
    *,
    broker_factory=AlpacaBrokerClient,
    data_provider_factory=AlpacaDataProvider,
    execution_service_factory=None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a process exit code."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    def get_execution_service() -> ExecutionService:
        if execution_service_factory is not None:
            return execution_service_factory()
        return ExecutionService(
            broker_client=broker_factory(),
            data_provider=data_provider_factory(),
        )

    try:
        if args.command == "status":
            broker = broker_factory()
            lines = _render_status(
                broker.get_account(),
                get_trading_mode(),
                broker.is_market_open(),
            )
        elif args.command == "positions":
            broker = broker_factory()
            lines = _render_positions(broker.get_positions(), sort_key=args.sort, limit=args.limit)
        elif args.command == "orders":
            broker = broker_factory()
            lines = _render_orders(
                broker.get_orders(status=args.status, limit=args.limit),
                symbol=args.symbol,
                verbose=args.verbose,
            )
        elif args.command == "data" and args.data_command == "bars":
            provider = data_provider_factory()
            symbol = args.symbol.upper()
            start = _parse_iso_date(args.start)
            end = _parse_iso_date(args.end)
            if end < start:
                raise ValueError("--end must be on or after --start.")
            timeframe = _TIMEFRAME_CHOICES[args.timeframe]
            bars_by_symbol = provider.get_bars([symbol], timeframe, start, end)
            barset = bars_by_symbol.get(symbol) or _empty_barset()
            lines = _render_bars(
                symbol,
                args.timeframe,
                barset,
                limit=args.limit,
            )
        elif args.command == "config" and args.config_command == "validate":
            lines = validate_config_file(Path(args.path), kind=args.kind)
        elif args.command == "trade":
            service = get_execution_service()
            if args.trade_command == "preview":
                lines = _render_execution_result(service.preview(_build_trade_intent(args)))
            elif args.trade_command == "submit":
                if not args.paper:
                    raise ValueError("trade submit requires --paper.")
                lines = _render_execution_result(service.submit_paper(_build_trade_intent(args)))
            elif args.trade_command == "order-status":
                lines = _render_order_status(service.get_order_status(args.order_id))
            elif args.trade_command == "cancel":
                cancelled, order = service.cancel_order(args.order_id)
                lines = _render_cancel_result(cancelled, order, args.order_id)
            elif args.trade_command == "kill-switch" and args.kill_switch_command == "status":
                state = service.get_kill_switch_state()
                lines = _render_kill_switch_status(
                    state.active,
                    state.reason,
                    state.last_triggered_at,
                )
            else:
                parser.error(f"Unsupported trade command: {args.trade_command}")
                return 2
        else:
            parser.error(f"Unsupported command: {args.command}")
            return 2
    except (BrokerError, ValueError) as exc:
        print(f"Error: {exc}", file=stderr)
        return 1

    _write_lines(stdout, lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

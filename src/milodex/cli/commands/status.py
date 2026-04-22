"""status, positions, orders commands."""

from __future__ import annotations

import argparse

from milodex.broker.models import AccountInfo, Order, Position
from milodex.cli._shared import (
    CommandContext,
    account_to_dict,
    add_global_flags,
    format_money,
    format_pct,
    order_to_dict,
    position_to_dict,
)
from milodex.cli.formatter import CommandResult


def register(subparsers: argparse._SubParsersAction) -> None:
    status_parser = subparsers.add_parser(
        "status",
        help="Show trading mode, market status, and account summary.",
    )
    add_global_flags(status_parser)

    positions_parser = subparsers.add_parser("positions", help="List open positions.")
    add_global_flags(positions_parser)
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
    add_global_flags(orders_parser)
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


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.command == "status":
        broker = ctx.broker_factory()
        return _build_status_result(
            broker.get_account(), ctx.get_trading_mode(), broker.is_market_open()
        )
    if args.command == "positions":
        broker = ctx.broker_factory()
        return _build_positions_result(broker.get_positions(), sort_key=args.sort, limit=args.limit)
    if args.command == "orders":
        broker = ctx.broker_factory()
        return _build_orders_result(
            broker.get_orders(status=args.status, limit=args.limit),
            symbol=args.symbol,
            verbose=args.verbose,
        )
    raise ValueError(f"Unsupported command: {args.command}")


def _build_status_result(
    account: AccountInfo, trading_mode: str, market_open: bool
) -> CommandResult:
    lines = [
        "Milodex Status",
        f"Trading mode: {trading_mode}",
        f"Market open: {'yes' if market_open else 'no'}",
        f"Equity: {format_money(account.equity)}",
        f"Cash: {format_money(account.cash)}",
        f"Buying power: {format_money(account.buying_power)}",
        f"Portfolio value: {format_money(account.portfolio_value)}",
        f"Daily P&L: {format_money(account.daily_pnl)}",
    ]
    data = {
        "trading_mode": trading_mode,
        "market_open": market_open,
        "account": account_to_dict(account),
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
            f"{format_money(position.avg_entry_price):>11}  "
            f"{format_money(position.current_price):>11}  "
            f"{format_money(position.market_value):>11}  "
            f"{format_money(position.unrealized_pnl):>11}  "
            f"{format_pct(position.unrealized_pnl_pct):>12}"
        )
    data = {
        "positions": [position_to_dict(p) for p in sorted_positions],
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
                details.append(f"limit={format_money(order.limit_price)}")
            if order.stop_price is not None:
                details.append(f"stop={format_money(order.stop_price)}")
            if order.filled_quantity is not None:
                details.append(f"filled_qty={order.filled_quantity:.2f}")
            if order.filled_avg_price is not None:
                details.append(f"filled_avg={format_money(order.filled_avg_price)}")
            if details:
                lines.append(f"  details: {', '.join(details)}")
    data = {
        "orders": [order_to_dict(o) for o in filtered_orders],
        "symbol_filter": symbol,
        "verbose": verbose,
    }
    return CommandResult(command="orders", data=data, human_lines=lines)

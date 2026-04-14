"""Minimal CLI entrypoint for read-only operator workflows."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from milodex.broker import BrokerClient, BrokerError
from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.models import AccountInfo, Order, Position
from milodex.config import get_trading_mode


def build_parser() -> argparse.ArgumentParser:
    """Build the root CLI parser."""
    parser = argparse.ArgumentParser(
        prog="milodex",
        description="Milodex operator CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show trading mode, market status, and account summary.")
    subparsers.add_parser("positions", help="List open positions.")

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

    return parser


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_pct(value: float) -> str:
    return f"{value * 100:,.2f}%"


def _write_lines(stream: TextIO, lines: Sequence[str]) -> None:
    for line in lines:
        print(line, file=stream)


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


def _render_positions(positions: Sequence[Position]) -> list[str]:
    if not positions:
        return ["Open Positions", "No open positions."]

    lines = [
        "Open Positions",
        "SYMBOL  QTY       AVG ENTRY    CURRENT      MKT VALUE    UNRLZD P&L   UNRLZD P&L %",
    ]
    for position in positions:
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


def _render_orders(orders: Sequence[Order]) -> list[str]:
    if not orders:
        return ["Recent Orders", "No matching orders."]

    lines = [
        "Recent Orders",
        "ID            SYMBOL  SIDE  TYPE        STATUS             QTY       SUBMITTED",
    ]
    for order in orders:
        lines.append(
            f"{order.id[:12]:<12}  "
            f"{order.symbol:<6}  "
            f"{order.side.value.upper():<4}  "
            f"{order.order_type.value:<10}  "
            f"{order.status.value:<17}  "
            f"{order.quantity:>8.2f}  "
            f"{order.submitted_at.isoformat()}"
        )
    return lines


def main(
    argv: Sequence[str] | None = None,
    *,
    broker_factory: Callable[[], BrokerClient] = AlpacaBrokerClient,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a process exit code."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        broker = broker_factory()

        if args.command == "status":
            lines = _render_status(
                broker.get_account(),
                get_trading_mode(),
                broker.is_market_open(),
            )
        elif args.command == "positions":
            lines = _render_positions(broker.get_positions())
        elif args.command == "orders":
            lines = _render_orders(broker.get_orders(status=args.status, limit=args.limit))
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

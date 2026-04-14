"""CLI entrypoint for read-only operator workflows."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import TextIO

import pandas as pd

from milodex.broker import BrokerClient, BrokerError
from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.models import AccountInfo, Order, Position
from milodex.cli.config_validation import validate_config_file
from milodex.config import get_trading_mode
from milodex.data import BarSet, DataProvider, Timeframe
from milodex.data.alpaca_provider import AlpacaDataProvider

_TIMEFRAME_CHOICES = {
    "1m": Timeframe.MINUTE_1,
    "5m": Timeframe.MINUTE_5,
    "15m": Timeframe.MINUTE_15,
    "1h": Timeframe.HOUR_1,
    "1d": Timeframe.DAY_1,
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
    bars_parser.add_argument(
        "--start",
        required=True,
        help="Start date in YYYY-MM-DD format.",
    )
    bars_parser.add_argument(
        "--end",
        required=True,
        help="End date in YYYY-MM-DD format.",
    )
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

    return parser


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_pct(value: float) -> str:
    return f"{value * 100:,.2f}%"


def _write_lines(stream: TextIO, lines: Sequence[str]) -> None:
    for line in lines:
        print(line, file=stream)


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        msg = f"Invalid date '{value}'. Use YYYY-MM-DD format."
        raise ValueError(msg) from exc


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


def _render_positions(
    positions: Sequence[Position],
    *,
    sort_key: str,
    limit: int,
) -> list[str]:
    if not positions:
        return ["Open Positions", "No open positions."]

    if limit < 1:
        msg = "--limit must be at least 1."
        raise ValueError(msg)

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


def _render_orders(
    orders: Sequence[Order],
    *,
    symbol: str | None,
    verbose: bool,
) -> list[str]:
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
            details: list[str] = []
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


def _render_bars(
    symbol: str,
    timeframe_label: str,
    barset: BarSet,
    *,
    limit: int,
) -> list[str]:
    if limit < 1:
        msg = "--limit must be at least 1."
        raise ValueError(msg)

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


def _handle_config_validate(path: str, kind: str | None) -> list[str]:
    return validate_config_file(Path(path), kind=kind)


def _empty_barset() -> BarSet:
    return BarSet(
        pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    broker_factory: Callable[[], BrokerClient] = AlpacaBrokerClient,
    data_provider_factory: Callable[[], DataProvider] = AlpacaDataProvider,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a process exit code."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

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
            lines = _render_positions(
                broker.get_positions(),
                sort_key=args.sort,
                limit=args.limit,
            )
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
                msg = "--end must be on or after --start."
                raise ValueError(msg)
            timeframe = _TIMEFRAME_CHOICES[args.timeframe]
            bars_by_symbol = provider.get_bars([symbol], timeframe, start, end)
            barset = bars_by_symbol.get(symbol) or _empty_barset()
            lines = _render_bars(symbol, args.timeframe, barset, limit=args.limit)
        elif args.command == "config" and args.config_command == "validate":
            lines = _handle_config_validate(args.path, args.kind)
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

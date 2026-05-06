"""data bars and data fetch-universe commands."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from milodex.cli._shared import (
    TIMEFRAME_CHOICES,
    CommandContext,
    add_global_flags,
    parse_iso_date,
)
from milodex.cli.formatter import CommandResult
from milodex.data import BarSet
from milodex.strategies.loader import resolve_universe_ref


def register(subparsers: argparse._SubParsersAction) -> None:
    data_parser = subparsers.add_parser("data", help="Inspect market data.")
    add_global_flags(data_parser)
    data_subparsers = data_parser.add_subparsers(dest="data_command", required=True)

    bars_parser = data_subparsers.add_parser("bars", help="Fetch bars for a symbol.")
    add_global_flags(bars_parser)
    bars_parser.add_argument("symbol", help="Ticker symbol to fetch.")
    bars_parser.add_argument(
        "--timeframe",
        choices=tuple(TIMEFRAME_CHOICES),
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

    fu_parser = data_subparsers.add_parser(
        "fetch-universe",
        help="Pre-fill the parquet cache for every symbol in a universe.",
    )
    add_global_flags(fu_parser)
    fu_parser.add_argument(
        "--universe-ref",
        required=True,
        help="Universe id string, e.g. universe.sp100_liquid.v1",
    )
    fu_parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD format.")
    fu_parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD format.")
    fu_parser.add_argument(
        "--timeframe",
        choices=tuple(TIMEFRAME_CHOICES),
        default="1d",
        help="Timeframe to request (default: 1d).",
    )
    fu_parser.add_argument(
        "--config-dir",
        default="configs",
        help="Directory containing universe manifests (default: configs).",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.data_command == "fetch-universe":
        return _run_fetch_universe(args, ctx)
    if args.data_command != "bars":
        raise ValueError(f"Unsupported data command: {args.data_command}")
    provider = ctx.data_provider_factory()
    symbol = args.symbol.upper()
    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start.")
    timeframe = TIMEFRAME_CHOICES[args.timeframe]
    bars_by_symbol = provider.get_bars([symbol], timeframe, start, end)
    barset = bars_by_symbol.get(symbol) or _empty_barset()
    return _build_bars_result(symbol, args.timeframe, barset, limit=args.limit)


def _empty_barset() -> BarSet:
    return BarSet(
        pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])
    )


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


def _run_fetch_universe(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    config_path = Path(args.config_dir) / "_dummy.yaml"
    try:
        symbols = resolve_universe_ref(args.universe_ref, config_path)
    except ValueError as exc:
        return CommandResult(
            command="data.fetch-universe",
            status="error",
            human_lines=[f"Error: {exc}"],
            errors=[{"code": "universe_ref_not_found", "message": str(exc)}],
        )

    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start.")

    timeframe = TIMEFRAME_CHOICES[args.timeframe]
    provider = ctx.data_provider_factory()

    print(
        f"Fetching {len(symbols)} symbols from {start} to {end} ({args.timeframe})...",
        flush=True,
    )

    bars_by_symbol = provider.get_bars(list(symbols), timeframe, start, end)

    return _build_fetch_universe_result(args.universe_ref, args.timeframe, symbols, bars_by_symbol)


def _build_fetch_universe_result(
    universe_ref: str,
    timeframe_label: str,
    symbols: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
) -> CommandResult:
    total = len(symbols)
    with_data = {s for s in symbols if bars_by_symbol.get(s) is not None}
    hit = len(with_data)
    missing = sorted(set(symbols) - with_data)
    coverage_pct = (hit / total * 100) if total > 0 else 0.0

    lines: list[str] = [
        f"fetch-universe: {universe_ref} ({timeframe_label})",
        f"  Total symbols requested : {total}",
        f"  Symbols with data       : {hit}",
        f"  Coverage                : {hit}/{total} ({coverage_pct:.1f}%)",
    ]

    missing_cap = 10
    if missing:
        truncated = missing[:missing_cap]
        suffix = " ..." if len(missing) > missing_cap else ""
        lines.append(f"  Missing                 : {', '.join(truncated)}{suffix}")
    else:
        lines.append("  Missing                 : none")

    data: dict[str, Any] = {
        "universe_ref": universe_ref,
        "timeframe": timeframe_label,
        "total_requested": total,
        "symbols_with_data": hit,
        "coverage_pct": round(coverage_pct, 1),
        "missing": missing,
    }
    return CommandResult(command="data.fetch-universe", data=data, human_lines=lines)

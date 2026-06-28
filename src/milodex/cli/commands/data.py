"""data bars, data fetch-universe, and data warmup-tape commands."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
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
from milodex.data import BarSet, Timeframe
from milodex.strategies.instrument_eligibility import InstrumentEligibilityError
from milodex.strategies.loader import resolve_universe_ref

#: Intraday timeframes valid for the readiness report, mapped to bar minutes.
#: 1d is intentionally absent — readiness is a per-session intraday check.
_READINESS_TIMEFRAME_MINUTES = {
    Timeframe.MINUTE_1: 1,
    Timeframe.MINUTE_5: 5,
    Timeframe.MINUTE_15: 15,
    Timeframe.HOUR_1: 60,
}

_DATE_RANGE_TOLERANCE = timedelta(days=7)


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
    fu_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Force a full-range refetch+merge before reading back, healing "
            "interior cache gaps that get_bars assumes are complete (e.g. a "
            "missing year between an old backtest warm and a later runner tail)."
        ),
    )

    wt_parser = data_subparsers.add_parser(
        "warmup-tape",
        help="Fetch recent VIX history from Yahoo Finance and write it to the market cache.",
    )
    add_global_flags(wt_parser)
    wt_parser.add_argument(
        "--lookback-days",
        type=int,
        default=365,
        help="Number of calendar days of history to fetch (default: 365).",
    )

    rd_parser = data_subparsers.add_parser(
        "readiness",
        help="Intraday data-readiness report for a universe (per-session completeness).",
    )
    add_global_flags(rd_parser)
    rd_parser.add_argument("--universe-ref", required=True, help="Universe id string.")
    rd_parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD format.")
    rd_parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD format.")
    rd_parser.add_argument(
        "--timeframe",
        choices=tuple(TIMEFRAME_CHOICES),
        default="5m",
        help="Intraday timeframe (default: 5m). Daily (1d) is not valid for readiness.",
    )
    rd_parser.add_argument(
        "--config-dir",
        default="configs",
        help="Directory containing universe manifests (default: configs).",
    )
    rd_parser.add_argument(
        "--cross-check-reference",
        action="store_true",
        help=(
            "Cross-check IEX session ranges against a free consolidated daily "
            "reference (Yahoo) to flag inward price bias. Makes live network calls."
        ),
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.data_command == "fetch-universe":
        return _run_fetch_universe(args, ctx)
    if args.data_command == "warmup-tape":
        return _run_warmup_tape(args)
    if args.data_command == "readiness":
        return _run_readiness(args, ctx)
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

    mode = "force-backfilling" if getattr(args, "force", False) else "Fetching"
    print(
        f"{mode} {len(symbols)} symbols from {start} to {end} ({args.timeframe})...",
        flush=True,
    )

    if getattr(args, "force", False):
        provider.backfill_range(list(symbols), timeframe, start, end)
    bars_by_symbol = provider.get_bars(list(symbols), timeframe, start, end)

    return _build_fetch_universe_result(
        args.universe_ref,
        args.timeframe,
        symbols,
        bars_by_symbol,
        requested_start=start,
        requested_end=end,
    )


def _build_fetch_universe_result(
    universe_ref: str,
    timeframe_label: str,
    symbols: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    *,
    requested_start: date | None = None,
    requested_end: date | None = None,
) -> CommandResult:
    total = len(symbols)
    with_data = {s for s in symbols if bars_by_symbol.get(s) is not None}
    hit = len(with_data)
    missing = sorted(set(symbols) - with_data)
    coverage_pct = (hit / total * 100) if total > 0 else 0.0
    date_range_warnings = _date_range_warnings(
        symbols=symbols,
        bars_by_symbol=bars_by_symbol,
        requested_start=requested_start,
        requested_end=requested_end,
    )
    warned_symbols = {item["symbol"] for item in date_range_warnings}
    symbols_with_full_date_range = max(0, hit - len(warned_symbols))
    date_range_coverage_pct = (symbols_with_full_date_range / total * 100) if total > 0 else 0.0

    lines: list[str] = [
        f"fetch-universe: {universe_ref} ({timeframe_label})",
        f"  Total symbols requested : {total}",
        f"  Symbols with data       : {hit}",
        f"  Coverage                : {hit}/{total} ({coverage_pct:.1f}%)",
        (
            "  Date range coverage     : "
            f"{symbols_with_full_date_range}/{total} ({date_range_coverage_pct:.1f}%)"
        ),
    ]

    missing_cap = 10
    if missing:
        truncated = missing[:missing_cap]
        suffix = " ..." if len(missing) > missing_cap else ""
        lines.append(f"  Missing                 : {', '.join(truncated)}{suffix}")
    else:
        lines.append("  Missing                 : none")

    warning_cap = 10
    if date_range_warnings:
        lines.append(f"  Date range warnings     : {len(date_range_warnings)}")
        for warning in date_range_warnings[:warning_cap]:
            first = warning["first_bar_date"] or "none"
            last = warning["last_bar_date"] or "none"
            lines.append(
                f"    {warning['symbol']}: {warning['issue']} (first={first}, last={last})"
            )
        if len(date_range_warnings) > warning_cap:
            lines.append("    ...")
    else:
        lines.append("  Date range warnings     : none")

    data: dict[str, Any] = {
        "universe_ref": universe_ref,
        "timeframe": timeframe_label,
        "requested_start": requested_start.isoformat() if requested_start else None,
        "requested_end": requested_end.isoformat() if requested_end else None,
        "total_requested": total,
        "symbols_with_data": hit,
        "coverage_pct": round(coverage_pct, 1),
        "missing": missing,
        "symbols_with_full_date_range": symbols_with_full_date_range,
        "date_range_coverage_pct": round(date_range_coverage_pct, 1),
        "date_range_warnings": date_range_warnings,
    }
    return CommandResult(command="data.fetch-universe", data=data, human_lines=lines)


def _date_range_warnings(
    *,
    symbols: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    requested_start: date | None,
    requested_end: date | None,
) -> list[dict[str, str | None]]:
    if requested_start is None or requested_end is None:
        return []

    warnings: list[dict[str, str | None]] = []
    for symbol in symbols:
        barset = bars_by_symbol.get(symbol)
        if barset is None:
            continue
        dataframe = barset.to_dataframe()
        if dataframe.empty:
            continue
        timestamps = pd.to_datetime(dataframe["timestamp"], utc=True, errors="coerce").dropna()
        if timestamps.empty:
            continue
        first_bar_date = timestamps.min().date()
        last_bar_date = timestamps.max().date()
        common = {
            "symbol": symbol,
            "first_bar_date": first_bar_date.isoformat(),
            "last_bar_date": last_bar_date.isoformat(),
        }
        if first_bar_date - requested_start > _DATE_RANGE_TOLERANCE:
            warnings.append({**common, "issue": "starts_after_requested_window"})
        if requested_end - last_bar_date > _DATE_RANGE_TOLERANCE:
            warnings.append({**common, "issue": "ends_before_requested_window"})
    return warnings


def _run_readiness(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    from milodex.data.intraday_readiness import scan_intraday_readiness

    config_path = Path(args.config_dir) / "_dummy.yaml"
    try:
        symbols = resolve_universe_ref(args.universe_ref, config_path)
    except InstrumentEligibilityError as exc:
        return CommandResult(
            command="data.readiness",
            status="error",
            human_lines=[f"Error: {exc}"],
            errors=[{"code": "universe_contains_forbidden_instrument", "message": str(exc)}],
        )
    except ValueError as exc:
        return CommandResult(
            command="data.readiness",
            status="error",
            human_lines=[f"Error: {exc}"],
            errors=[{"code": "universe_ref_not_found", "message": str(exc)}],
        )

    timeframe = TIMEFRAME_CHOICES[args.timeframe]
    minutes = _READINESS_TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        return CommandResult(
            command="data.readiness",
            status="error",
            human_lines=[
                f"Error: {args.timeframe} is not an intraday timeframe (use 1m/5m/15m/1h)."
            ],
            errors=[
                {
                    "code": "invalid_timeframe",
                    "message": "readiness requires an intraday timeframe (1m/5m/15m/1h)",
                }
            ],
        )

    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start.")

    provider = ctx.data_provider_factory()
    bars_by_symbol = provider.get_bars(list(symbols), timeframe, start, end)

    reference_daily = None
    if getattr(args, "cross_check_reference", False):
        from milodex.data.consolidated_reference import fetch_daily_ohlc

        reference_daily = {s: fetch_daily_ohlc(s, start, end) for s in symbols}

    report = scan_intraday_readiness(
        bars_by_symbol,
        timeframe_minutes=minutes,
        requested_start=start,
        requested_end=end,
        reference_daily_by_symbol=reference_daily,
    )
    return _build_readiness_result(args.universe_ref, args.timeframe, report)


def _build_readiness_result(universe_ref: str, timeframe_label: str, report: Any) -> CommandResult:
    data = report.to_dict()
    lines = [
        f"data readiness: {universe_ref} ({timeframe_label})",
        f"  Status        : {data['status']}",
        f"  Feed label    : {data['feed_label']}",
        f"  Symbols       : {len(data['scanned_symbols'])}",
        f"  Warnings      : {data['warning_count']}",
    ]
    cap = 10
    per_symbol = data["per_symbol"]
    for sr in per_symbol[:cap]:
        lines.append(
            f"    {sr['symbol']}: {sr['observed_bars']}/{sr['expected_bars']} bars "
            f"({sr['coverage_pct']}%), {sr['sessions_observed']} sessions"
        )
    if len(per_symbol) > cap:
        lines.append("    ...")
    issue_codes = data["issue_codes"]
    if issue_codes:
        from collections import Counter

        counts = Counter(issue_codes)
        summary = ", ".join(f"{c}×{n}" for c, n in counts.most_common(cap))
        lines.append(f"  Issue codes   : {summary}")
    return CommandResult(command="data.readiness", data=data, human_lines=lines)


def _run_warmup_tape(args: argparse.Namespace) -> CommandResult:
    """Fetch VIX history from Yahoo Finance and write it to the market cache."""
    from milodex.data.tape_cache_warmup import get_vix_cache_state, warmup_vix_cache

    lookback_days: int = args.lookback_days
    success = warmup_vix_cache(lookback_days=lookback_days)

    state = get_vix_cache_state()

    if success:
        lines = [
            "data warmup-tape: VIX cache refreshed",
            f"  rows         : {state['row_count']}",
            f"  latest date  : {state['latest_date']}",
        ]
        return CommandResult(
            command="data.warmup-tape",
            data={"success": True, **state},
            human_lines=lines,
        )
    else:
        lines = [
            "data warmup-tape: VIX cache refresh FAILED — check logs for details.",
            "  The tape will show '—' for VIX until a successful warmup.",
        ]
        return CommandResult(
            command="data.warmup-tape",
            status="error",
            human_lines=lines,
            errors=[
                {
                    "code": "vix_fetch_failed",
                    "message": "Yahoo Finance fetch returned no data",
                }
            ],
        )

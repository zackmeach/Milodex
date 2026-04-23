"""backtest command."""

from __future__ import annotations

import argparse
from typing import Any

from milodex.backtesting.engine import BacktestResult
from milodex.backtesting.walk_forward import WalkForwardSplitter
from milodex.cli._shared import (
    TIMEFRAME_CHOICES,
    CommandContext,
    add_global_flags,
    format_money,
    parse_iso_date,
)
from milodex.cli.formatter import CommandResult


def register(subparsers: argparse._SubParsersAction) -> None:
    backtest_parser = subparsers.add_parser(
        "backtest",
        help="Run a historical backtest for a strategy.",
    )
    add_global_flags(backtest_parser)
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


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start.")
    engine_kwargs: dict[str, Any] = {"initial_equity": args.initial_equity}
    if args.slippage is not None:
        engine_kwargs["slippage_pct"] = args.slippage

    engine = ctx.get_backtest_engine(args.strategy_id, **engine_kwargs)
    backtest_result = engine.run(start, end, run_id=args.run_id)

    walk_windows = None
    if args.walk_forward:
        loaded = engine._loaded  # noqa: SLF001
        from milodex.backtesting.engine import _trading_days_in_range

        all_bars = ctx.data_provider_factory().get_bars(
            symbols=list(loaded.context.universe),
            timeframe=TIMEFRAME_CHOICES["1d"],
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


def _build_backtest_result(
    result: BacktestResult, *, walk_forward_windows: list | None
) -> CommandResult:
    trade_summary = f"{result.trade_count} ({result.buy_count} buys, {result.sell_count} sells)"
    lines = [
        "Backtest Result",
        f"Strategy:       {result.strategy_id}",
        f"Run ID:         {result.run_id}",
        f"Period:         {result.start_date} to {result.end_date}",
        f"Trading days:   {result.trading_days}",
        f"Initial equity: {format_money(result.initial_equity)}",
        f"Final equity:   {format_money(result.final_equity)}",
        f"Total return:   {result.total_return_pct:+.2f}%",
        f"Trades:         {trade_summary}",
        f"Slippage:       {result.slippage_pct * 100:.2f}%",
        f"Commission:     {format_money(result.commission_per_trade)}/trade",
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

    _attach_uncertainty_label(result, data, lines)
    return CommandResult(command="backtest", data=data, human_lines=lines)


# Statistical minimum before a backtest is considered evidence-bearing
# per ROADMAP promotion thresholds. Regime-family strategies are
# exempt (R-PRM-004) because rotation can't produce trade counts at
# this scale over reasonable windows.
_STATISTICAL_MIN_TRADES = 30


def _strategy_family(strategy_id: str) -> str:
    """Return the first dot-segment of a strategy_id — the family.

    Example: ``regime.daily.sma200_rotation.spy_shy.v1`` → ``regime``.
    """
    return strategy_id.split(".", 1)[0] if strategy_id else ""


def _attach_uncertainty_label(
    result: BacktestResult, data: dict[str, Any], lines: list[str]
) -> None:
    """Annotate output with R-CLI-014 confidence labels, R-PRM-004-aware.

    Regime-family runs get ``evidence_basis='operational'`` — the
    lifecycle-proof platform check, not a statistical one. Non-regime
    runs below the 30-trade minimum are labeled ``'insufficient
    evidence'`` per R-CLI-014.
    """
    family = _strategy_family(result.strategy_id)
    if family == "regime":
        data["evidence_basis"] = "operational"
        lines.append("Evidence basis: operational (regime strategy, R-PRM-004)")
        return
    if result.trade_count < _STATISTICAL_MIN_TRADES:
        reason = f"trade count {result.trade_count} < {_STATISTICAL_MIN_TRADES} statistical minimum"
        data["uncertainty_label"] = "insufficient evidence"
        data["uncertainty_reason"] = reason
        lines.append(f"Confidence:     insufficient evidence ({reason})")

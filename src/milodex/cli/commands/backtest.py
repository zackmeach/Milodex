"""backtest command.

Two paths:

- Without ``--walk-forward``: single whole-period backtest. Metrics reflect
  the full ``[start, end]`` window and are reported as-is.
- With ``--walk-forward``: runs the walk-forward orchestrator which simulates
  each OOS test window independently and reports OOS-aggregate metrics (the
  ones the promotion gate looks at). Per-window results and stability
  diagnostics are attached so a reviewer can see whether the aggregate signal
  comes from the whole history or leans on a single lucky window.
"""

from __future__ import annotations

import argparse
from typing import Any

from milodex.backtesting.engine import BacktestResult
from milodex.backtesting.walk_forward_runner import (
    WalkForwardResult,
    compute_window_spans,
    run_walk_forward,
)
from milodex.cli._shared import (
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
        help=(
            "Run walk-forward validation: simulate each OOS test window "
            "independently and report OOS-aggregate metrics."
        ),
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

    if args.walk_forward:
        return _run_walk_forward(engine, start, end, args)

    backtest_result = engine.run(start, end, run_id=args.run_id)
    return _build_backtest_result(backtest_result)


def _run_walk_forward(engine, start, end, args) -> CommandResult:
    from milodex.backtesting.engine import _trading_days_in_range

    loaded = engine._loaded  # noqa: SLF001
    wf_windows_count = int(loaded.config.backtest.get("walk_forward_windows", 4))

    all_bars = engine.prefetch_bars(start, end)
    total_days = len(_trading_days_in_range(all_bars, start, end))
    train_days, test_days, step_days = compute_window_spans(total_days, wf_windows_count)

    result = run_walk_forward(
        engine,
        start_date=start,
        end_date=end,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        initial_equity=args.initial_equity,
        run_id=args.run_id,
    )
    return _build_walk_forward_result(result)


def _build_backtest_result(result: BacktestResult) -> CommandResult:
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
    _attach_uncertainty_label(
        family=_strategy_family(result.strategy_id),
        trade_count=result.trade_count,
        data=data,
        lines=lines,
    )
    return CommandResult(command="backtest", data=data, human_lines=lines)


def _build_walk_forward_result(result: WalkForwardResult) -> CommandResult:
    stability = result.stability
    lines = [
        "Backtest Result (walk-forward)",
        f"Strategy:       {result.strategy_id}",
        f"Run ID:         {result.run_id}",
        f"Period:         {result.start_date} to {result.end_date}",
        f"Windows:        {len(result.windows)} "
        f"(train={result.train_days}d, test={result.test_days}d, step={result.step_days}d)",
        f"Initial equity: {format_money(result.initial_equity)}",
        "",
        "OOS aggregate (metrics the promotion gate evaluates):",
        f"  Trading days: {result.oos_trading_days}",
        f"  Trades:       {result.oos_trade_count}",
        f"  Total return: {result.oos_total_return_pct:+.2f}%",
        f"  Sharpe:       {_fmt_optional(result.oos_sharpe, '.2f')}",
        f"  Max drawdown: {result.oos_max_drawdown_pct:.2f}%",
        "",
        "Stability across windows:",
        f"  Sharpe min/max/std: "
        f"{_fmt_optional(stability.sharpe_min, '.2f')} / "
        f"{_fmt_optional(stability.sharpe_max, '.2f')} / "
        f"{_fmt_optional(stability.sharpe_std, '.2f')}",
        f"  Positive windows: {stability.windows_positive} / {len(result.windows)}",
        f"  Negative windows: {stability.windows_negative} / {len(result.windows)}",
        f"  Single-window dependency: {'YES' if stability.single_window_dependency else 'no'}",
    ]
    if result.windows:
        lines.append("")
        lines.append("Per-window OOS results:")
        for window in result.windows:
            lines.append(
                f"  #{window.index}  "
                f"{window.test_start} -> {window.test_end}  "
                f"trades={window.trade_count}  "
                f"return={window.total_return_pct:+.2f}%  "
                f"sharpe={_fmt_optional(window.sharpe, '.2f')}  "
                f"maxDD={window.max_drawdown_pct:.2f}%"
            )

    data: dict[str, Any] = {
        "run_id": result.run_id,
        "strategy_id": result.strategy_id,
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "walk_forward": True,
        "train_days": result.train_days,
        "test_days": result.test_days,
        "step_days": result.step_days,
        "initial_equity": result.initial_equity,
        "oos_aggregate": {
            "trading_days": result.oos_trading_days,
            "trade_count": result.oos_trade_count,
            "total_return_pct": result.oos_total_return_pct,
            "sharpe": result.oos_sharpe,
            "max_drawdown_pct": result.oos_max_drawdown_pct,
        },
        "stability": {
            "sharpe_min": stability.sharpe_min,
            "sharpe_max": stability.sharpe_max,
            "sharpe_std": stability.sharpe_std,
            "windows_positive": stability.windows_positive,
            "windows_negative": stability.windows_negative,
            "single_window_dependency": stability.single_window_dependency,
        },
        "windows": [
            {
                "index": w.index,
                "train_start": w.train_start.isoformat(),
                "train_end": w.train_end.isoformat(),
                "test_start": w.test_start.isoformat(),
                "test_end": w.test_end.isoformat(),
                "trading_days": w.trading_days,
                "trade_count": w.trade_count,
                "total_return_pct": w.total_return_pct,
                "sharpe": w.sharpe,
                "max_drawdown_pct": w.max_drawdown_pct,
            }
            for w in result.windows
        ],
    }
    _attach_uncertainty_label(
        family=_strategy_family(result.strategy_id),
        trade_count=result.oos_trade_count,
        data=data,
        lines=lines,
    )
    if stability.single_window_dependency:
        lines.append(
            "WARNING: aggregate return depends on a single window — "
            "dropping the best-returning window flips the sign. Treat as fragile."
        )
    return CommandResult(command="backtest", data=data, human_lines=lines)


def _fmt_optional(value: float | None, spec: str) -> str:
    if value is None:
        return "n/a"
    return format(value, spec)


# Statistical minimum before a backtest is considered evidence-bearing
# per ROADMAP promotion thresholds. Regime-family strategies are
# exempt (R-PRM-004) because rotation can't produce trade counts at
# this scale over reasonable windows.
_STATISTICAL_MIN_TRADES = 30


def _strategy_family(strategy_id: str) -> str:
    return strategy_id.split(".", 1)[0] if strategy_id else ""


def _attach_uncertainty_label(
    *, family: str, trade_count: int, data: dict[str, Any], lines: list[str]
) -> None:
    if family == "regime":
        data["evidence_basis"] = "operational"
        lines.append("Evidence basis: operational (regime strategy, R-PRM-004)")
        return
    if trade_count < _STATISTICAL_MIN_TRADES:
        reason = f"trade count {trade_count} < {_STATISTICAL_MIN_TRADES} statistical minimum"
        data["uncertainty_label"] = "insufficient evidence"
        data["uncertainty_reason"] = reason
        lines.append(f"Confidence:     insufficient evidence ({reason})")

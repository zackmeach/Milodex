"""analytics metrics, trades, compare, export, list commands."""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path
from typing import Any

from milodex.analytics.benchmark import compute_benchmark
from milodex.analytics.metrics import PerformanceMetrics, compute_metrics
from milodex.cli._shared import (
    CommandContext,
    add_global_flags,
    format_money,
    performance_metrics_to_dict,
)
from milodex.cli.formatter import CommandResult
from milodex.core.event_store import EventStore


def register(subparsers: argparse._SubParsersAction) -> None:
    analytics_parser = subparsers.add_parser(
        "analytics",
        help="Query and export backtest results.",
    )
    add_global_flags(analytics_parser)
    analytics_subparsers = analytics_parser.add_subparsers(dest="analytics_command", required=True)

    metrics_parser = analytics_subparsers.add_parser(
        "metrics",
        help="Show performance metrics for a backtest run.",
    )
    add_global_flags(metrics_parser)
    metrics_parser.add_argument(
        "run_id",
        nargs="?",
        help="Backtest run ID. Omit and pass --strategy to use the latest run.",
    )
    metrics_parser.add_argument(
        "--strategy",
        dest="strategy_id",
        help="Resolve to the latest backtest run for this strategy.",
    )
    metrics_parser.add_argument(
        "--compare-spy",
        action="store_true",
        help="Include SPY buy-and-hold benchmark comparison.",
    )

    trades_parser = analytics_subparsers.add_parser(
        "trades",
        help="List trades for a backtest run.",
    )
    add_global_flags(trades_parser)
    trades_parser.add_argument(
        "run_id",
        nargs="?",
        help="Backtest run ID. Omit and pass --strategy to use the latest run.",
    )
    trades_parser.add_argument(
        "--strategy",
        dest="strategy_id",
        help="Resolve to the latest backtest run for this strategy.",
    )
    trades_parser.add_argument(
        "--limit", type=int, default=50, help="Maximum number of trades to show."
    )

    compare_parser = analytics_subparsers.add_parser(
        "compare",
        help="Side-by-side metrics comparison for two backtest runs.",
    )
    add_global_flags(compare_parser)
    compare_parser.add_argument("run_id_a", nargs="?", help="First backtest run ID.")
    compare_parser.add_argument("run_id_b", nargs="?", help="Second backtest run ID.")
    compare_parser.add_argument(
        "--strategy-a",
        dest="strategy_a",
        help="Latest-run shortcut for the left side of the comparison.",
    )
    compare_parser.add_argument(
        "--strategy-b",
        dest="strategy_b",
        help="Latest-run shortcut for the right side of the comparison.",
    )

    export_parser = analytics_subparsers.add_parser(
        "export",
        help="Export equity curve and trades for a backtest run as CSV.",
    )
    add_global_flags(export_parser)
    export_parser.add_argument(
        "run_id",
        nargs="?",
        help="Backtest run ID. Omit and pass --strategy to use the latest run.",
    )
    export_parser.add_argument(
        "--strategy",
        dest="strategy_id",
        help="Resolve to the latest backtest run for this strategy.",
    )
    export_parser.add_argument(
        "--output",
        required=True,
        help="Output directory path for exported CSV files.",
    )

    list_parser = analytics_subparsers.add_parser(
        "list",
        help="List all backtest runs recorded in the event store.",
    )
    add_global_flags(list_parser)
    list_parser.add_argument(
        "--limit", type=int, default=20, help="Maximum number of runs to show."
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    event_store = ctx.get_event_store()

    if args.analytics_command == "list":
        all_runs = event_store.list_backtest_runs()
        return _build_analytics_list_result(all_runs, limit=args.limit)

    if args.analytics_command == "metrics":
        run_id = _resolve_run_arg(
            event_store,
            run_id=args.run_id,
            strategy_id=getattr(args, "strategy_id", None),
        )
        run_ = event_store.get_backtest_run(run_id)
        if run_ is None:
            raise ValueError(f"Backtest run not found: {run_id}")
        if run_.id is None:
            raise ValueError(f"Backtest run has no DB id: {run_id}")
        raw_trades = event_store.list_trades_for_backtest_run(run_.id)
        equity_curve = equity_curve_from_trades(raw_trades, run_.metadata or {})
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
            run_id=run_.run_id,
            strategy_id=run_.strategy_id,
            start_date=run_.start_date.date() if run_.start_date else date.today(),
            end_date=run_.end_date.date() if run_.end_date else date.today(),
            initial_equity=run_.metadata.get("initial_equity", 100_000.0),
            equity_curve=equity_curve,
            trades=trades_dicts,
        )
        benchmark_metrics = None
        if args.compare_spy:
            provider = ctx.data_provider_factory()
            benchmark_metrics = compute_benchmark(
                start_date=strategy_metrics.start_date,
                end_date=strategy_metrics.end_date,
                initial_equity=strategy_metrics.initial_equity,
                data_provider=provider,
            )
        return _build_analytics_metrics_result(strategy_metrics, benchmark_metrics)

    if args.analytics_command == "trades":
        run_id = _resolve_run_arg(
            event_store,
            run_id=args.run_id,
            strategy_id=getattr(args, "strategy_id", None),
        )
        run_ = event_store.get_backtest_run(run_id)
        if run_ is None:
            raise ValueError(f"Backtest run not found: {run_id}")
        if run_.id is None:
            raise ValueError(f"Backtest run has no DB id: {run_id}")
        trades = event_store.list_trades_for_backtest_run(run_.id)
        return _build_analytics_trades_result(run_id, trades, limit=args.limit)

    if args.analytics_command == "compare":
        run_id_a = _resolve_run_arg(
            event_store,
            run_id=args.run_id_a,
            strategy_id=getattr(args, "strategy_a", None),
            flag_label="strategy-a",
        )
        run_id_b = _resolve_run_arg(
            event_store,
            run_id=args.run_id_b,
            strategy_id=getattr(args, "strategy_b", None),
            flag_label="strategy-b",
        )
        run_a = event_store.get_backtest_run(run_id_a)
        run_b = event_store.get_backtest_run(run_id_b)
        if run_a is None:
            raise ValueError(f"Backtest run not found: {run_id_a}")
        if run_b is None:
            raise ValueError(f"Backtest run not found: {run_id_b}")
        metrics_a = metrics_for_run(run_a, event_store)
        metrics_b = metrics_for_run(run_b, event_store)
        return _build_analytics_compare_result(metrics_a, metrics_b)

    if args.analytics_command == "export":
        run_id = _resolve_run_arg(
            event_store,
            run_id=args.run_id,
            strategy_id=getattr(args, "strategy_id", None),
        )
        run_ = event_store.get_backtest_run(run_id)
        if run_ is None:
            raise ValueError(f"Backtest run not found: {run_id}")
        if run_.id is None:
            raise ValueError(f"Backtest run has no DB id: {run_id}")
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        trades = event_store.list_trades_for_backtest_run(run_.id)
        equity_curve = equity_curve_from_trades(trades, run_.metadata or {})
        _export_trades_csv(trades, output_dir / f"{run_id}_trades.csv")
        _export_equity_curve_csv(equity_curve, output_dir / f"{run_id}_equity.csv")
        return _build_analytics_export_result(run_id, output_dir)

    raise ValueError(f"Unsupported analytics command: {args.analytics_command}")


def _resolve_run_arg(
    event_store: EventStore,
    *,
    run_id: str | None,
    strategy_id: str | None,
    flag_label: str = "strategy",
) -> str:
    """Resolve a command's run_id from either the positional arg or --strategy.

    ``run_id`` and ``strategy_id`` are mutually exclusive. Exactly one must be
    provided. When ``strategy_id`` is supplied, the event store is queried for
    the most-recent backtest run for that strategy.
    """
    if run_id and strategy_id:
        raise ValueError(f"Specify either a run_id positional or --{flag_label}, not both.")
    if not run_id and not strategy_id:
        raise ValueError(f"A run_id or --{flag_label} is required.")
    if run_id:
        return run_id
    return _latest_run_id_for_strategy(event_store, strategy_id)


def _latest_run_id_for_strategy(event_store: EventStore, strategy_id: str) -> str:
    runs = [r for r in event_store.list_backtest_runs() if r.strategy_id == strategy_id]
    if not runs:
        raise ValueError(
            f"No backtest runs found for strategy '{strategy_id}'. "
            f"Run 'milodex backtest {strategy_id}' first or use --run-id explicitly."
        )
    # list_backtest_runs returns ascending by id → last is most recent.
    return runs[-1].run_id


def equity_curve_from_trades(
    trades: list,
    metadata: dict[str, Any],
) -> list[tuple[date, float]]:
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
    return []


def metrics_for_run(run_, event_store: EventStore) -> PerformanceMetrics:
    """Build PerformanceMetrics for a BacktestRunEvent from the event store."""
    if run_.id is None:
        raise ValueError(f"Backtest run has no DB id: {run_.run_id}")
    raw_trades = event_store.list_trades_for_backtest_run(run_.id)
    equity_curve = equity_curve_from_trades(raw_trades, run_.metadata or {})
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
        run_id=run_.run_id,
        strategy_id=run_.strategy_id,
        start_date=run_.start_date.date() if run_.start_date else date.today(),
        end_date=run_.end_date.date() if run_.end_date else date.today(),
        initial_equity=run_.metadata.get("initial_equity", 100_000.0)
        if run_.metadata
        else 100_000.0,
        equity_curve=equity_curve,
        trades=trades_dicts,
    )


def _build_metrics_lines(m: PerformanceMetrics, label: str = "Strategy") -> list[str]:
    lines = [
        f"  {label}:",
        f"    Strategy ID:    {m.strategy_id}",
        f"    Run ID:         {m.run_id}",
        f"    Period:         {m.start_date} to {m.end_date}",
        f"    Trading days:   {m.trading_days}",
        f"    Total return:   {m.total_return_pct:+.2f}%",
        f"    CAGR:           {m.cagr_pct:+.2f}%"
        if m.cagr_pct is not None
        else "    CAGR:           n/a",  # noqa: E501
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
        "strategy": performance_metrics_to_dict(strategy_metrics),
        "benchmark": performance_metrics_to_dict(benchmark_metrics) if benchmark_metrics else None,
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
            f"{format_money(t.estimated_unit_price)}"
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
        data={
            "run_a": performance_metrics_to_dict(metrics_a),
            "run_b": performance_metrics_to_dict(metrics_b),
        },
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
    for run_ in shown:
        meta = run_.metadata or {}
        trade_count = meta.get("trade_count", "?")
        lines.append(
            f"{run_.run_id:<36}  "
            f"{run_.status:<10} "
            f"{run_.strategy_id[:36]:<36}  "
            f"{str(run_.start_date)[:10]}  "
            f"{str(run_.end_date)[:10]}  "
            f"{trade_count}"
        )
        runs_data.append(
            {
                "run_id": run_.run_id,
                "strategy_id": run_.strategy_id,
                "status": run_.status,
                "start_date": str(run_.start_date)[:10],
                "end_date": str(run_.end_date)[:10],
                "slippage_pct": run_.slippage_pct,
            }
        )
    return CommandResult(
        command="analytics.list",
        data={"runs": runs_data, "total": len(runs)},
        human_lines=lines,
    )


def _export_trades_csv(trades: list, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "recorded_at",
                "symbol",
                "side",
                "quantity",
                "estimated_unit_price",
                "estimated_order_value",
                "status",
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


def _export_equity_curve_csv(equity_curve: list[tuple[date, float]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["date", "portfolio_value"])
        writer.writeheader()
        for d, v in equity_curve:
            writer.writerow({"date": d.isoformat(), "portfolio_value": v})

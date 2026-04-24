"""Batch walk-forward evaluator.

Runs :func:`run_walk_forward` across a list of strategies and returns a
comparable ranking table. Used by ``milodex research screen`` to screen a
bank of backtested candidates side-by-side instead of invoking the single-
strategy walk-forward N times by hand.

Scope: this is a *screening* tool. It evaluates and ranks; it never freezes,
promotes, or advances any strategy. The ``gate_allowed`` column is advisory
— the operator still runs ``milodex promotion promote`` for any candidate
that looks promising.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from milodex.backtesting.engine import _trading_days_in_range
from milodex.backtesting.walk_forward_runner import (
    compute_window_spans,
    run_walk_forward,
)
from milodex.promotion.state_machine import check_gate

if TYPE_CHECKING:
    from collections.abc import Sequence

    from milodex.cli._shared import CommandContext
    from milodex.data.models import BarSet


@dataclass(frozen=True)
class BatchRow:
    """One strategy's outcome inside a batch screen."""

    strategy_id: str
    family: str
    trade_count: int
    oos_sharpe: float | None
    oos_max_drawdown_pct: float
    oos_total_return_pct: float
    single_window_dependency: bool
    gate_allowed: bool
    gate_promotion_type: str
    gate_failures: tuple[str, ...]
    run_id: str | None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "family": self.family,
            "trade_count": self.trade_count,
            "oos_sharpe": self.oos_sharpe,
            "oos_max_drawdown_pct": self.oos_max_drawdown_pct,
            "oos_total_return_pct": self.oos_total_return_pct,
            "single_window_dependency": self.single_window_dependency,
            "gate_allowed": self.gate_allowed,
            "gate_promotion_type": self.gate_promotion_type,
            "gate_failures": list(self.gate_failures),
            "run_id": self.run_id,
            "error": self.error,
        }


@dataclass(frozen=True)
class BatchResult:
    """Full batch outcome — rows sorted in screening order."""

    start_date: date
    end_date: date
    rows: tuple[BatchRow, ...]


def run_batch(
    *,
    strategy_ids: Sequence[str],
    start_date: date,
    end_date: date,
    ctx: CommandContext,
    fail_fast: bool = False,
    initial_equity: float = 100_000.0,
) -> BatchResult:
    """Run walk-forward screening over ``strategy_ids``.

    Bars are cached in memory across strategies keyed on
    ``(universe, warmup_start, end_date)`` so two variants over the same
    universe share one data-provider call per batch invocation. (Disk-level
    caching via ``ParquetCache`` still applies across invocations.)

    On per-strategy error: if ``fail_fast`` is set, re-raise immediately; else
    record the exception string on the row and move on. This matters for
    screening — one malformed config should not mask results for the other
    twelve candidates.
    """
    if end_date < start_date:
        msg = "end_date must be on or after start_date"
        raise ValueError(msg)

    bar_cache: dict[tuple, dict[str, BarSet]] = {}
    rows: list[BatchRow] = []
    for strategy_id in strategy_ids:
        try:
            row = _screen_one(
                strategy_id=strategy_id,
                start_date=start_date,
                end_date=end_date,
                ctx=ctx,
                initial_equity=initial_equity,
                bar_cache=bar_cache,
            )
        except Exception as exc:
            if fail_fast:
                raise
            rows.append(_error_row(strategy_id, exc))
            continue
        rows.append(row)

    rows_sorted = _rank_rows(rows)
    return BatchResult(start_date=start_date, end_date=end_date, rows=tuple(rows_sorted))


def _screen_one(
    *,
    strategy_id: str,
    start_date: date,
    end_date: date,
    ctx: CommandContext,
    initial_equity: float,
    bar_cache: dict[tuple, dict[str, BarSet]],
) -> BatchRow:
    engine = ctx.get_backtest_engine(strategy_id, initial_equity=initial_equity)
    loaded = engine._loaded  # noqa: SLF001
    family = loaded.context.family
    universe = tuple(sorted(loaded.context.universe))
    warmup_days = engine._warmup_calendar_days()  # noqa: SLF001
    warmup_start = start_date - timedelta(days=warmup_days)
    cache_key = (universe, warmup_start, end_date)

    if cache_key in bar_cache:
        all_bars = bar_cache[cache_key]
    else:
        all_bars = engine.prefetch_bars(start_date, end_date)
        bar_cache[cache_key] = all_bars

    total_days = len(_trading_days_in_range(all_bars, start_date, end_date))
    wf_windows = int(loaded.config.backtest.get("walk_forward_windows", 4))
    train_days, test_days, step_days = compute_window_spans(total_days, wf_windows)

    result = run_walk_forward(
        engine,
        start_date=start_date,
        end_date=end_date,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        initial_equity=initial_equity,
        all_bars=all_bars,
    )

    gate = check_gate(
        lifecycle_exempt=(family == "regime"),
        sharpe_ratio=result.oos_sharpe,
        max_drawdown_pct=result.oos_max_drawdown_pct,
        trade_count=result.oos_trade_count,
    )

    return BatchRow(
        strategy_id=strategy_id,
        family=family,
        trade_count=result.oos_trade_count,
        oos_sharpe=result.oos_sharpe,
        oos_max_drawdown_pct=result.oos_max_drawdown_pct,
        oos_total_return_pct=result.oos_total_return_pct,
        single_window_dependency=result.stability.single_window_dependency,
        gate_allowed=gate.allowed,
        gate_promotion_type=gate.promotion_type,
        gate_failures=tuple(gate.failures),
        run_id=result.run_id,
    )


def _error_row(strategy_id: str, exc: BaseException) -> BatchRow:
    return BatchRow(
        strategy_id=strategy_id,
        family="",
        trade_count=0,
        oos_sharpe=None,
        oos_max_drawdown_pct=0.0,
        oos_total_return_pct=0.0,
        single_window_dependency=False,
        gate_allowed=False,
        gate_promotion_type="error",
        gate_failures=(str(exc),),
        run_id=None,
        error=str(exc),
    )


def _rank_rows(rows: list[BatchRow]) -> list[BatchRow]:
    """Sort by (gate_allowed desc, sharpe desc with None last, return desc).

    Gate-passing rows float to the top; within each tier, higher Sharpe first;
    rows with no Sharpe (empty windows, errors) fall to the bottom.
    """

    def key(row: BatchRow) -> tuple:
        gate_rank = 0 if row.gate_allowed else 1
        sharpe_missing = row.oos_sharpe is None
        sharpe_sort = -(row.oos_sharpe if row.oos_sharpe is not None else 0.0)
        return (gate_rank, sharpe_missing, sharpe_sort, -row.oos_total_return_pct)

    return sorted(rows, key=key)


__all__ = ["BatchResult", "BatchRow", "run_batch"]

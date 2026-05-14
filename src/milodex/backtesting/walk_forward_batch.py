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

import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from milodex.backtesting.engine import _trading_days_in_range
from milodex.backtesting.walk_forward_runner import (
    compute_window_spans,
    run_walk_forward,
)
from milodex.promotion.state_machine import check_gate
from milodex.strategies.loader import resolve_universe_survivorship_corrected

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
    oos_equity_curve: tuple[tuple[date, float], ...] = ()
    error: str | None = None
    # Whether the strategy's universe is point-in-time corrected for
    # survivorship bias. False by default — universes built from a present-day
    # ticker list applied retroactively are silently optimistic. Reported in
    # the screen output so the operator knows which Sharpes are credibility-
    # corrected and which are not. See docs/RISK_POLICY.md "Known Backtest
    # Limitations and Biases".
    survivorship_corrected: bool = False

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
            "oos_equity_curve": [
                {"date": point_date.isoformat(), "equity": equity}
                for point_date, equity in self.oos_equity_curve
            ],
            "error": self.error,
            "survivorship_corrected": self.survivorship_corrected,
        }


@dataclass(frozen=True)
class BatchResult:
    """Full batch outcome — rows sorted in screening order."""

    start_date: date
    end_date: date
    rows: tuple[BatchRow, ...]
    correlation_matrix: dict[str, dict[str, float | None]] = field(default_factory=dict)


def run_batch(
    *,
    strategy_ids: Sequence[str],
    start_date: date,
    end_date: date,
    ctx: CommandContext,
    fail_fast: bool = False,
    initial_equity: float = 100_000.0,
    parallel: int = 1,
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

    ``parallel``: when > 1, dispatch ``_screen_one`` invocations across a
    ``ProcessPoolExecutor`` of that many workers. Each child process opens
    its own ``EventStore``; SQLite WAL mode (set in
    ``EventStore._connect``) makes concurrent reads + serialized writes
    safe across processes. The in-memory bar cache is sequential-only —
    parallel mode forgoes it; the disk-level Parquet cache still applies.
    Idempotent: results match sequential mode strategy-for-strategy.
    """
    if end_date < start_date:
        msg = "end_date must be on or after start_date"
        raise ValueError(msg)

    if parallel <= 1:
        return _run_batch_sequential(
            strategy_ids=strategy_ids,
            start_date=start_date,
            end_date=end_date,
            ctx=ctx,
            fail_fast=fail_fast,
            initial_equity=initial_equity,
        )
    return _run_batch_parallel(
        strategy_ids=strategy_ids,
        start_date=start_date,
        end_date=end_date,
        ctx=ctx,
        fail_fast=fail_fast,
        initial_equity=initial_equity,
        parallel=parallel,
    )


def _run_batch_sequential(
    *,
    strategy_ids: Sequence[str],
    start_date: date,
    end_date: date,
    ctx: CommandContext,
    fail_fast: bool,
    initial_equity: float,
) -> BatchResult:
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
    return BatchResult(
        start_date=start_date,
        end_date=end_date,
        rows=tuple(rows_sorted),
        correlation_matrix=_compute_correlation_matrix(rows_sorted),
    )


def _run_batch_parallel(
    *,
    strategy_ids: Sequence[str],
    start_date: date,
    end_date: date,
    ctx: CommandContext,
    fail_fast: bool,
    initial_equity: float,
    parallel: int,
) -> BatchResult:
    """Parallel dispatch of ``_screen_one`` across ``parallel`` workers.

    Each child process builds its own ``EventStore`` against the parent's
    SQLite path (WAL mode allows concurrent reads + serialized writes) and
    its own data provider. The parent's ``ctx`` typically holds factory
    closures that cannot cross the process boundary on Windows spawn — we
    instead pass a small dataclass recipe (event-store path, config dir)
    that the child uses to rebuild a minimal context shim.
    """
    recipe = _build_worker_recipe(ctx)
    if recipe is None:
        # Caller passed a ctx whose factories cannot be reconstructed in a
        # worker (e.g., test ctx with closures over tempfiles). Fall back
        # to sequential — the parallel flag is best-effort.
        return _run_batch_sequential(
            strategy_ids=strategy_ids,
            start_date=start_date,
            end_date=end_date,
            ctx=ctx,
            fail_fast=fail_fast,
            initial_equity=initial_equity,
        )

    rows_by_id: dict[str, BatchRow] = {}
    with ProcessPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(
                _worker_screen_one,
                strategy_id,
                start_date,
                end_date,
                initial_equity,
                recipe,
            ): strategy_id
            for strategy_id in strategy_ids
        }
        for future in as_completed(futures):
            strategy_id = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                if fail_fast:
                    # Cancellation is best-effort: queued futures are skipped,
                    # but already-running child processes continue to completion
                    # before the executor context exits. The exception is
                    # re-raised after the executor finishes shutting down.
                    for other in futures:
                        other.cancel()
                    raise
                rows_by_id[strategy_id] = _error_row(strategy_id, exc)
                continue
            rows_by_id[strategy_id] = row

    # Preserve input order so downstream consumers see deterministic
    # ordering before _rank_rows sorts by gate/sharpe.
    rows = [rows_by_id[sid] for sid in strategy_ids]
    rows_sorted = _rank_rows(rows)
    return BatchResult(
        start_date=start_date,
        end_date=end_date,
        rows=tuple(rows_sorted),
        correlation_matrix=_compute_correlation_matrix(rows_sorted),
    )


# ---------------------------------------------------------------------------
# Parallel worker plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _WorkerRecipe:
    """Self-contained recipe a worker uses to rebuild a CommandContext.

    The parent's ``ctx`` is built around closures over CLI-time state that
    will not survive Windows ``spawn``. This recipe carries only stdlib-
    primitive fields — paths — so it crosses the process boundary cleanly.
    The worker uses these fields to reconstruct a minimal ``ctx`` shim with
    just the surface ``_screen_one`` reads.
    """

    config_dir_path: str
    event_store_path: str | None


def _build_worker_recipe(ctx: CommandContext) -> _WorkerRecipe | None:
    """Best-effort extraction of a worker recipe from ``ctx``.

    Returns ``None`` when the context cannot be reconstructed in a child
    process — the caller falls back to sequential mode in that case. The
    extraction reads only public attributes; any attribute it cannot
    serialize (e.g., a closure over a tempfile factory) signals the
    fallback.
    """
    try:
        config_dir = ctx.config_dir
    except Exception:
        return None
    if config_dir is None:
        return None

    event_store_path: str | None = None
    try:
        event_store = ctx.get_event_store()
        # EventStore exposes its sqlite path as ``_path``; relying on that
        # is acceptable here — the worker only needs a path string and the
        # EventStore constructor accepts one.
        path_attr = getattr(event_store, "_path", None)
        if path_attr is not None:
            event_store_path = str(path_attr)
    except Exception:
        return None

    if event_store_path is None:
        # Without a durable path, child processes cannot share the audit
        # trail. Fall back to sequential.
        return None

    return _WorkerRecipe(
        config_dir_path=str(config_dir),
        event_store_path=event_store_path,
    )


def _worker_screen_one(
    strategy_id: str,
    start_date: date,
    end_date: date,
    initial_equity: float,
    recipe: _WorkerRecipe,
) -> BatchRow:
    """Module-level worker entry point — must be importable in the child.

    Each child process builds its own minimal ``CommandContext`` shim from
    the recipe, then delegates to the same ``_screen_one`` used by the
    sequential path. The shim's ``get_backtest_engine`` constructs a fresh
    ``BacktestEngine`` against a fresh ``EventStore`` so SQLite connections
    are not shared across processes (the source of stray
    ``ProgrammingError`` in multi-process SQLite use).
    """
    # Lazy imports keep this module importable in the parent without
    # eagerly resolving heavy CLI / data-provider chains.
    from pathlib import Path

    from milodex.backtesting.engine import BacktestEngine
    from milodex.core.event_store import EventStore
    from milodex.data.alpaca_provider import AlpacaDataProvider
    from milodex.strategies.loader import StrategyLoader

    config_dir = Path(recipe.config_dir_path)
    event_store = EventStore(Path(recipe.event_store_path))

    # Build a tiny context shim with just the surface ``_screen_one`` reads.
    class _ChildCtx:
        pass

    child_ctx = _ChildCtx()
    child_ctx.config_dir = config_dir

    def _resolve_strategy_config_path(sid: str) -> Path:
        for candidate in config_dir.glob("*.yaml"):
            try:
                from milodex.strategies.loader import load_strategy_config

                config = load_strategy_config(candidate)
            except Exception:
                continue
            if config.strategy_id == sid:
                return candidate
        msg = f"Strategy id {sid!r} not found under {config_dir}."
        raise ValueError(msg)

    def _build_engine(sid: str, **kwargs: Any) -> BacktestEngine:
        loader = StrategyLoader()
        config_path = _resolve_strategy_config_path(sid)
        loaded = loader.load(config_path)
        # IMPORTANT: --parallel requires Alpaca credentials in the environment.
        # Worker subprocesses spawn fresh and cannot inherit the parent's
        # configured data provider (closures don't pickle), so this codepath
        # always uses AlpacaDataProvider regardless of how the parent was wired.
        # Operators running --parallel in test environments without ALPACA_API_KEY
        # / ALPACA_SECRET_KEY will see authentication errors per worker.
        data_provider = AlpacaDataProvider()
        return BacktestEngine(
            loaded=loaded,
            data_provider=data_provider,
            event_store=event_store,
            **kwargs,
        )

    child_ctx.get_backtest_engine = _build_engine

    return _screen_one(
        strategy_id=strategy_id,
        start_date=start_date,
        end_date=end_date,
        ctx=child_ctx,  # type: ignore[arg-type]
        initial_equity=initial_equity,
        bar_cache={},
    )


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
    universe_ref = loaded.context.universe_ref
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

    # Stamp the run record so research-screen runs are distinguishable from
    # single-strategy ``milodex backtest`` runs in the event store.
    persisted = engine._event_store.get_backtest_run(result.run_id)  # noqa: SLF001
    if persisted is not None:
        merged_metadata = {**persisted.metadata, "source": "research_screen"}
        engine._event_store.update_backtest_run_metadata(  # noqa: SLF001
            result.run_id, metadata=merged_metadata
        )

    gate = check_gate(
        lifecycle_exempt=(family == "regime"),
        to_stage="paper",
        sharpe_ratio=result.oos_sharpe,
        max_drawdown_pct=result.oos_max_drawdown_pct,
        trade_count=result.oos_trade_count,
    )

    # Look up the universe's declared survivorship-correction status. A
    # missing universe_ref (strategies that inline their universe instead of
    # referencing a manifest) defaults to False — same conservative answer as
    # an undeclared field.
    survivorship_corrected = False
    if universe_ref is not None:
        survivorship_corrected = resolve_universe_survivorship_corrected(
            universe_ref, loaded.config.path
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
        oos_equity_curve=tuple(result.oos_equity_curve),
        survivorship_corrected=survivorship_corrected,
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
        oos_equity_curve=(),
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


def _compute_correlation_matrix(rows: list[BatchRow]) -> dict[str, dict[str, float | None]]:
    """Return pairwise Pearson correlations over aligned OOS daily returns."""
    returns_by_strategy = {
        row.strategy_id: _daily_return_series(row.oos_equity_curve) for row in rows
    }
    matrix: dict[str, dict[str, float | None]] = {}
    for left in rows:
        matrix[left.strategy_id] = {}
        for right in rows:
            if left.strategy_id == right.strategy_id:
                matrix[left.strategy_id][right.strategy_id] = 1.0
                continue
            left_returns = returns_by_strategy[left.strategy_id]
            right_returns = returns_by_strategy[right.strategy_id]
            dates = sorted(set(left_returns) & set(right_returns))
            if len(dates) < 2:
                matrix[left.strategy_id][right.strategy_id] = None
                continue
            matrix[left.strategy_id][right.strategy_id] = _pearson(
                [left_returns[point_date] for point_date in dates],
                [right_returns[point_date] for point_date in dates],
            )
    return matrix


def _daily_return_series(equity_curve: tuple[tuple[date, float], ...]) -> dict[date, float]:
    returns: dict[date, float] = {}
    for (prev_date, prev_equity), (current_date, current_equity) in zip(
        equity_curve, equity_curve[1:], strict=False
    ):
        _ = prev_date
        if prev_equity == 0:
            continue
        returns[current_date] = (current_equity / prev_equity) - 1.0
    return returns


def _pearson(left: list[float], right: list[float]) -> float | None:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_var = sum((x - left_mean) ** 2 for x in left)
    right_var = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_var * right_var)
    if denominator == 0:
        return None
    return numerator / denominator


__all__ = ["BatchResult", "BatchRow", "run_batch"]

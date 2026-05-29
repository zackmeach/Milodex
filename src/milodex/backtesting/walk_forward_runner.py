"""Walk-forward orchestrator: runs per-OOS-window simulations and aggregates.

The splitter in :mod:`milodex.backtesting.walk_forward` only produces window
boundaries. This module is what actually *runs* walk-forward — it invokes the
backtest engine once per OOS test window, never on the train window, because
Milodex strategies use fixed published parameters rather than fitted ones.
"Train" here is warmup data the strategy's indicators can see; the
simulation itself only advances equity through the test window.

OOS-aggregate metrics (the numbers the promotion gate should look at) are
computed by concatenating per-window daily returns into a single stream and
stitching a geometric cumulative equity curve across windows. This is what
distinguishes honest walk-forward from whole-period backtest metrics wearing
walk-forward clothing.

Why a separate module rather than a method on ``BacktestEngine``:

- Bar prefetch, splitter, and simulation loop are three distinct concerns.
  Keeping aggregation out of the engine means the engine stays a one-shot
  simulator — easier to reason about and easier to test in isolation.
- The orchestrator owns per-window bookkeeping, while parent-run lifecycle
  flows through the engine's public backtesting lifecycle surface.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from milodex.analytics.metrics import (
    daily_returns_from_equity,
    max_drawdown_from_equity,
    sharpe_from_daily_returns,
)
from milodex.backtesting.walk_forward import WalkForwardSplitter
from milodex.data.bar_quality import DataQualityError
from milodex.risk import RiskPolicy

if TYPE_CHECKING:
    from milodex.backtesting.engine import BacktestEngine


@dataclass(frozen=True)
class WalkForwardWindow:
    """Per-window outcome inside a walk-forward run."""

    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    trading_days: int
    trade_count: int
    initial_equity: float
    final_equity: float
    total_return_pct: float
    sharpe: float | None
    max_drawdown_pct: float
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    round_trip_count: int = 0
    skipped_count: int = 0


@dataclass(frozen=True)
class WalkForwardStability:
    """Robustness diagnostics across OOS windows.

    These answer "is the aggregate signal coming from the whole history or
    from one lucky window?" — the fragility check :doc:`VISION.md` demands.
    """

    sharpe_min: float | None
    sharpe_max: float | None
    sharpe_std: float | None
    windows_positive: int
    windows_negative: int
    single_window_dependency: bool


@dataclass(frozen=True)
class WalkForwardResult:
    """Full walk-forward outcome: per-window detail + OOS aggregate."""

    run_id: str
    strategy_id: str
    start_date: date
    end_date: date
    initial_equity: float
    train_days: int
    test_days: int
    step_days: int
    windows: list[WalkForwardWindow]
    oos_trade_count: int
    oos_skipped_count: int
    oos_trading_days: int
    oos_total_return_pct: float
    oos_sharpe: float | None
    oos_max_drawdown_pct: float
    oos_equity_curve: list[tuple[date, float]]
    stability: WalkForwardStability
    db_id: int | None = None
    oos_round_trip_count: int = 0
    risk_policy: RiskPolicy = RiskPolicy.BYPASS
    data_quality: dict = field(default_factory=dict)
    run_manifest: dict = field(default_factory=dict)


def compute_window_spans(
    total_trading_days: int, walk_forward_windows: int
) -> tuple[int, int, int]:
    """(train_days, test_days, step_days) given a trading-day budget.

    Splits the budget into ``walk_forward_windows`` equal-length OOS test
    slices; whatever remains is the training prelude. ``step_days`` equals
    ``test_days`` so the OOS windows tile without overlap. Shared by the
    single-run CLI and the batch runner so they can't drift apart on how a
    walk-forward is framed for the same config.
    """
    if total_trading_days < 2:
        msg = f"Not enough trading days for walk-forward: {total_trading_days}."
        raise ValueError(msg)
    test_days = max(1, total_trading_days // (walk_forward_windows + 1))
    train_days = total_trading_days - walk_forward_windows * test_days
    if train_days < 1:
        msg = (
            f"Walk-forward window math yields train_days={train_days}. "
            f"Widen the date range or reduce walk_forward_windows."
        )
        raise ValueError(msg)
    return train_days, test_days, test_days


def derive_walk_forward_spans(
    engine: BacktestEngine,
    start: date,
    end: date,
) -> tuple[dict, int, int, int]:
    """Prefetch bars and derive walk-forward window spans from the engine config.

    Single shared implementation used by both the CLI backtest command and the
    Bench command facade so the two paths cannot drift apart on how they frame
    the walk-forward split for the same strategy config.

    Returns ``(all_bars, train_days, test_days, step_days)`` where *all_bars*
    is the prefetched bar dict that callers pass on to :func:`run_walk_forward`
    (avoiding a second fetch).  Window count is read via the public
    :attr:`BacktestEngine.walk_forward_windows` property so this function
    requires no access to private engine attributes.

    Args:
        engine: A fully-configured :class:`~milodex.backtesting.engine.BacktestEngine`.
        start: Backtest start date (inclusive).
        end: Backtest end date (inclusive).

    Returns:
        A 4-tuple ``(all_bars, train_days, test_days, step_days)``.

    Raises:
        ValueError: If there are fewer than 2 trading days in the range, or
            if the window math yields a non-positive ``train_days``.
    """
    from milodex.backtesting.engine import _trading_days_in_range
    from milodex.data.timeframes import timeframe_from_bar_size

    all_bars = engine.prefetch_bars(start, end, timeframe=timeframe_from_bar_size(engine.bar_size))
    total_days = len(_trading_days_in_range(all_bars, start, end))
    window_count = engine.walk_forward_windows
    train_days, test_days, step_days = compute_window_spans(total_days, window_count)
    return all_bars, train_days, test_days, step_days


def run_walk_forward(
    engine: BacktestEngine,
    *,
    start_date: date,
    end_date: date,
    train_days: int,
    test_days: int,
    step_days: int,
    initial_equity: float | None = None,
    run_id: str | None = None,
    all_bars: dict | None = None,
) -> WalkForwardResult:
    """Run walk-forward validation and return per-window + OOS-aggregate metrics.

    The caller owns choosing ``train_days`` / ``test_days`` / ``step_days``;
    this function does not default them because the right sizing is
    strategy-dependent (intraday strategies want shorter windows, swing
    strategies want longer) and making the default implicit invites silent
    drift between strategy configs and validation runs.

    Equity resets to ``initial_equity`` at the start of every OOS window.
    That isolation is the point — it prevents a windfall in one window from
    compounding through later windows and inflating apparent stability.

    When ``all_bars`` is passed in, the prefetch step is skipped — used by the
    batch runner so multiple strategies over the same universe can share one
    fetch per invocation.
    """
    if end_date < start_date:
        msg = "end_date must be on or after start_date"
        raise ValueError(msg)

    eq_start = initial_equity if initial_equity is not None else engine.initial_equity

    if all_bars is None:
        from milodex.data.timeframes import timeframe_from_bar_size

        all_bars = engine.prefetch_bars(
            start_date, end_date, timeframe=timeframe_from_bar_size(engine.bar_size)
        )
    from milodex.backtesting.engine import _trading_days_in_range

    trading_days = _trading_days_in_range(all_bars, start_date, end_date)
    if len(trading_days) < train_days + test_days:
        msg = (
            f"Not enough trading days for walk-forward: have {len(trading_days)}, "
            f"need at least train_days + test_days = {train_days + test_days}."
        )
        raise ValueError(msg)

    splitter = WalkForwardSplitter()
    window_dates = list(
        splitter.split(
            trading_days,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
        )
    )
    if not window_dates:
        msg = "Walk-forward produced zero windows."
        raise ValueError(msg)

    run_handle = engine.start_walk_forward_parent_run(
        run_id=run_id,
        start_date=start_date,
        end_date=end_date,
        windows_planned=len(window_dates),
    )
    effective_run_id = run_handle.run_id
    db_run_id = run_handle.db_id

    windows: list[WalkForwardWindow] = []
    try:
        data_quality = engine.scan_backtest_data_quality(all_bars, start_date, end_date)
        for index, (tr_start, tr_end, te_start, te_end) in enumerate(window_dates):
            window_trading_days = [d for d in trading_days if te_start <= d <= te_end]
            if not window_trading_days:
                continue
            output = engine.simulate_window(
                all_bars=all_bars,
                trading_days=window_trading_days,
                db_run_id=db_run_id,
                session_id=f"{effective_run_id}:w{index}",
                initial_equity=eq_start,
            )
            window_returns = daily_returns_from_equity(output.equity_curve)
            window_sharpe = sharpe_from_daily_returns(window_returns)
            window_dd, _ = max_drawdown_from_equity(output.equity_curve)
            total_return_pct = (
                (output.final_equity - eq_start) / eq_start * 100.0 if eq_start > 0 else 0.0
            )
            windows.append(
                WalkForwardWindow(
                    index=index,
                    train_start=tr_start,
                    train_end=tr_end,
                    test_start=te_start,
                    test_end=te_end,
                    trading_days=len(window_trading_days),
                    trade_count=output.trade_count,
                    skipped_count=output.skipped_count,
                    initial_equity=eq_start,
                    final_equity=output.final_equity,
                    total_return_pct=total_return_pct,
                    sharpe=window_sharpe,
                    max_drawdown_pct=window_dd * 100.0,
                    equity_curve=list(output.equity_curve),
                    round_trip_count=output.round_trip_count,
                )
            )
    except DataQualityError as exc:
        data_quality = exc.report.to_dict()
        engine.update_backtest_run_metadata(
            effective_run_id,
            metadata=engine.backtest_run_metadata_with_manifest(
                effective_run_id,
                start_date=start_date,
                end_date=end_date,
                initial_equity=eq_start,
                data_quality=data_quality,
            ),
        )
        engine.mark_backtest_run_failed(effective_run_id)
        raise
    except Exception:
        engine.mark_backtest_run_failed(effective_run_id)
        raise

    aggregate = _aggregate_oos(windows, eq_start)
    stability = _compute_stability(windows)
    run_manifest = engine.build_backtest_run_manifest(
        start_date=start_date,
        end_date=end_date,
        initial_equity=eq_start,
        data_quality=data_quality,
    )

    engine.complete_walk_forward_run(
        effective_run_id,
        metadata={
            "walk_forward": True,
            "train_days": train_days,
            "test_days": test_days,
            "step_days": step_days,
            "initial_equity": eq_start,
            "windows": [_window_to_dict(w) for w in windows],
            "oos_aggregate": {
                "trade_count": aggregate.trade_count,
                "skipped_count": aggregate.skipped_count,
                "round_trip_count": aggregate.round_trip_count,
                "trading_days": aggregate.trading_days,
                "total_return_pct": aggregate.total_return_pct,
                "sharpe": aggregate.sharpe,
                "max_drawdown_pct": aggregate.max_drawdown_pct,
                "equity_curve": [[d.isoformat(), v] for d, v in aggregate.equity_curve],
            },
            "stability": {
                "sharpe_min": stability.sharpe_min,
                "sharpe_max": stability.sharpe_max,
                "sharpe_std": stability.sharpe_std,
                "windows_positive": stability.windows_positive,
                "windows_negative": stability.windows_negative,
                "single_window_dependency": stability.single_window_dependency,
            },
            "risk_policy": engine.risk_policy.value,
            "data_quality": data_quality,
            "run_manifest": run_manifest,
        },
    )

    return WalkForwardResult(
        run_id=effective_run_id,
        strategy_id=engine.strategy_id,
        start_date=start_date,
        end_date=end_date,
        initial_equity=eq_start,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        windows=windows,
        oos_trade_count=aggregate.trade_count,
        oos_skipped_count=aggregate.skipped_count,
        oos_trading_days=aggregate.trading_days,
        oos_total_return_pct=aggregate.total_return_pct,
        oos_sharpe=aggregate.sharpe,
        oos_max_drawdown_pct=aggregate.max_drawdown_pct,
        oos_equity_curve=aggregate.equity_curve,
        stability=stability,
        db_id=db_run_id,
        oos_round_trip_count=aggregate.round_trip_count,
        risk_policy=engine.risk_policy,
        data_quality=data_quality,
        run_manifest=run_manifest,
    )


@dataclass(frozen=True)
class _OosAggregate:
    trade_count: int
    skipped_count: int
    round_trip_count: int
    trading_days: int
    total_return_pct: float
    sharpe: float | None
    max_drawdown_pct: float
    equity_curve: list[tuple[date, float]]


def _aggregate_oos(windows: list[WalkForwardWindow], initial_equity: float) -> _OosAggregate:
    """Concatenate per-window daily returns and stitch a cumulative equity curve.

    Each window is an independent $initial_equity -> $final_equity run. Daily
    returns are the pct-change within a window; concatenating them gives a
    single continuous return stream even though equity was reset between
    windows. Cumulative equity is then built by compounding that stream from
    the starting ``initial_equity``.
    """
    if not windows:
        return _OosAggregate(
            trade_count=0,
            skipped_count=0,
            round_trip_count=0,
            trading_days=0,
            total_return_pct=0.0,
            sharpe=None,
            max_drawdown_pct=0.0,
            equity_curve=[],
        )
    all_returns: list[float] = []
    stitched: list[tuple[date, float]] = []
    running_equity = initial_equity
    for window in windows:
        if not window.equity_curve:
            continue
        # One return per equity-curve step, kept index-aligned with
        # ``equity_curve[1:]``. ``daily_returns_from_equity`` drops the step
        # when the prior day's equity is <= 0, which would shorten the series
        # and silently misalign dates -> returns; emit 0.0 for those steps so
        # every curve transition has exactly one paired return.
        steps = window.equity_curve[1:]
        window_returns = [
            (curr - prev) / prev if prev > 0 else 0.0
            for (_, prev), (_, curr) in zip(window.equity_curve[:-1], steps, strict=True)
        ]
        if len(window_returns) != len(steps):
            raise AssertionError(
                "OOS aggregation misaligned: "
                f"{len(window_returns)} returns vs {len(steps)} equity steps "
                f"in window index={window.index}"
            )
        if not stitched:
            stitched.append((window.equity_curve[0][0], running_equity))
        for (day, _), r in zip(steps, window_returns, strict=True):
            running_equity *= 1.0 + r
            stitched.append((day, running_equity))
            all_returns.append(r)
    trade_count = sum(w.trade_count for w in windows)
    skipped_count = sum(w.skipped_count for w in windows)
    round_trip_count = sum(w.round_trip_count for w in windows)
    trading_days = sum(w.trading_days for w in windows)
    total_return_pct = (running_equity - initial_equity) / initial_equity * 100.0
    sharpe = sharpe_from_daily_returns(all_returns)
    max_dd, _ = max_drawdown_from_equity(stitched)
    return _OosAggregate(
        trade_count=trade_count,
        skipped_count=skipped_count,
        round_trip_count=round_trip_count,
        trading_days=trading_days,
        total_return_pct=total_return_pct,
        sharpe=sharpe,
        max_drawdown_pct=max_dd * 100.0,
        equity_curve=stitched,
    )


def _compute_stability(windows: list[WalkForwardWindow]) -> WalkForwardStability:
    """Summary statistics + single-window-dependency flag across windows."""
    sharpes = [w.sharpe for w in windows if w.sharpe is not None]
    sharpe_min = min(sharpes) if sharpes else None
    sharpe_max = max(sharpes) if sharpes else None
    sharpe_std = statistics.pstdev(sharpes) if len(sharpes) >= 2 else None
    windows_positive = sum(1 for w in windows if w.total_return_pct > 0)
    windows_negative = sum(1 for w in windows if w.total_return_pct < 0)
    dependency = _single_window_dependency(windows)
    return WalkForwardStability(
        sharpe_min=sharpe_min,
        sharpe_max=sharpe_max,
        sharpe_std=sharpe_std,
        windows_positive=windows_positive,
        windows_negative=windows_negative,
        single_window_dependency=dependency,
    )


def _single_window_dependency(windows: list[WalkForwardWindow]) -> bool:
    """True if dropping the best-return window flips aggregate return negative.

    Non-flag result: aggregate was already non-positive (strategy has no edge
    regardless of any single window), or there are fewer than 2 windows (the
    check is ill-defined). In both cases, other metrics already tell the story.
    """
    if len(windows) < 2:
        return False
    aggregate_pct = _compound(w.total_return_pct for w in windows)
    if aggregate_pct <= 0.0:
        return False
    best_idx = max(range(len(windows)), key=lambda i: windows[i].total_return_pct)
    without_best = _compound(w.total_return_pct for i, w in enumerate(windows) if i != best_idx)
    return without_best <= 0.0


def _compound(returns_pct) -> float:
    """Geometric compounding of percentage returns (e.g. 5.0 means +5%)."""
    factor = 1.0
    for r in returns_pct:
        factor *= 1.0 + r / 100.0
    return (factor - 1.0) * 100.0


def _window_to_dict(window: WalkForwardWindow) -> dict:
    return {
        "index": window.index,
        "train_start": window.train_start.isoformat(),
        "train_end": window.train_end.isoformat(),
        "test_start": window.test_start.isoformat(),
        "test_end": window.test_end.isoformat(),
        "trading_days": window.trading_days,
        "trade_count": window.trade_count,
        "skipped_count": window.skipped_count,
        "initial_equity": window.initial_equity,
        "final_equity": window.final_equity,
        "total_return_pct": window.total_return_pct,
        "sharpe": window.sharpe,
        "max_drawdown_pct": window.max_drawdown_pct,
    }


__all__ = [
    "WalkForwardWindow",
    "WalkForwardStability",
    "WalkForwardResult",
    "compute_window_spans",
    "run_walk_forward",
]

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
- The orchestrator owns the parent ``BacktestRunEvent`` lifecycle and all
  per-window bookkeeping, so the engine doesn't need to know about windows.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from milodex.analytics.metrics import (
    daily_returns_from_equity,
    max_drawdown_from_equity,
    sharpe_from_daily_returns,
)
from milodex.backtesting.walk_forward import WalkForwardSplitter
from milodex.core.event_store import BacktestRunEvent

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
    oos_trading_days: int
    oos_total_return_pct: float
    oos_sharpe: float | None
    oos_max_drawdown_pct: float
    oos_equity_curve: list[tuple[date, float]]
    stability: WalkForwardStability
    db_id: int | None = None


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
    """
    if end_date < start_date:
        msg = "end_date must be on or after start_date"
        raise ValueError(msg)

    eq_start = initial_equity if initial_equity is not None else engine._initial_equity  # noqa: SLF001

    all_bars = engine.prefetch_bars(start_date, end_date)
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

    effective_run_id = run_id or str(uuid.uuid4())
    started_at = datetime.now(tz=UTC)
    db_run_id = engine._event_store.append_backtest_run(  # noqa: SLF001
        BacktestRunEvent(
            run_id=effective_run_id,
            strategy_id=engine._loaded.config.strategy_id,  # noqa: SLF001
            config_path=str(engine._loaded.config.path),  # noqa: SLF001
            config_hash=engine._loaded.context.config_hash,  # noqa: SLF001
            start_date=_date_to_dt(start_date),
            end_date=_date_to_dt(end_date),
            started_at=started_at,
            status="running",
            slippage_pct=engine._slippage_pct,  # noqa: SLF001
            commission_per_trade=engine._commission,  # noqa: SLF001
            metadata={"walk_forward": True, "windows_planned": len(window_dates)},
        )
    )

    windows: list[WalkForwardWindow] = []
    try:
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
                    initial_equity=eq_start,
                    final_equity=output.final_equity,
                    total_return_pct=total_return_pct,
                    sharpe=window_sharpe,
                    max_drawdown_pct=window_dd * 100.0,
                    equity_curve=list(output.equity_curve),
                )
            )
    except Exception:
        engine._event_store.update_backtest_run_status(  # noqa: SLF001
            effective_run_id, status="failed", ended_at=datetime.now(tz=UTC)
        )
        raise

    aggregate = _aggregate_oos(windows, eq_start)
    stability = _compute_stability(windows)

    engine._event_store.update_backtest_run_status(  # noqa: SLF001
        effective_run_id, status="completed", ended_at=datetime.now(tz=UTC)
    )
    engine._event_store.update_backtest_run_metadata(  # noqa: SLF001
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
        },
    )

    return WalkForwardResult(
        run_id=effective_run_id,
        strategy_id=engine._loaded.config.strategy_id,  # noqa: SLF001
        start_date=start_date,
        end_date=end_date,
        initial_equity=eq_start,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        windows=windows,
        oos_trade_count=aggregate.trade_count,
        oos_trading_days=aggregate.trading_days,
        oos_total_return_pct=aggregate.total_return_pct,
        oos_sharpe=aggregate.sharpe,
        oos_max_drawdown_pct=aggregate.max_drawdown_pct,
        oos_equity_curve=aggregate.equity_curve,
        stability=stability,
        db_id=db_run_id,
    )


@dataclass(frozen=True)
class _OosAggregate:
    trade_count: int
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
        window_returns = daily_returns_from_equity(window.equity_curve)
        if not stitched:
            stitched.append((window.equity_curve[0][0], running_equity))
        for (day, _), r in zip(window.equity_curve[1:], window_returns, strict=False):
            running_equity *= 1.0 + r
            stitched.append((day, running_equity))
            all_returns.append(r)
    trade_count = sum(w.trade_count for w in windows)
    trading_days = sum(w.trading_days for w in windows)
    total_return_pct = (running_equity - initial_equity) / initial_equity * 100.0
    sharpe = sharpe_from_daily_returns(all_returns)
    max_dd, _ = max_drawdown_from_equity(stitched)
    return _OosAggregate(
        trade_count=trade_count,
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
        "initial_equity": window.initial_equity,
        "final_equity": window.final_equity,
        "total_return_pct": window.total_return_pct,
        "sharpe": window.sharpe,
        "max_drawdown_pct": window.max_drawdown_pct,
    }


def _date_to_dt(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


__all__ = [
    "WalkForwardWindow",
    "WalkForwardStability",
    "WalkForwardResult",
    "run_walk_forward",
]

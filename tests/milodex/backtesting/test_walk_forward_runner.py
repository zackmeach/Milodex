"""Tests for the walk-forward orchestrator.

The splitter math is covered in ``test_walk_forward.py``; these tests focus
on the orchestration layer: that each OOS window is simulated independently
with a fresh equity, that OOS-aggregate metrics stitch per-window returns
into a continuous stream, and that stability diagnostics correctly flag
single-window dependence.
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.backtesting.walk_forward_runner import (
    WalkForwardWindow,
    _aggregate_oos,
    _compute_stability,
    _single_window_dependency,
    run_walk_forward,
)
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.strategies.base import DecisionReasoning, StrategyDecision

# ---------------------------------------------------------------------------
# Shared helpers — mirror test_engine.py conventions so the fixtures compose.
# ---------------------------------------------------------------------------


def _decision(intents: list) -> StrategyDecision:
    return StrategyDecision(
        intents=list(intents),
        reasoning=DecisionReasoning(rule="no_signal", narrative="stub"),
    )


def _make_barset(closes: list[float], start: date) -> BarSet:
    rows = []
    d = start
    for close in closes:
        rows.append(
            {
                "timestamp": pd.Timestamp(d, tz="UTC"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
                "vwap": close,
            }
        )
        d += timedelta(days=1)
    return BarSet(pd.DataFrame(rows))


def _make_loaded_strategy(universe: tuple[str, ...]):
    from milodex.strategies.base import StrategyContext

    tmp_dir = Path(tempfile.mkdtemp())
    yaml_path = tmp_dir / "wf.yaml"
    yaml_path.write_text("strategy:\n  id: x\n", encoding="utf-8")

    config = MagicMock()
    config.strategy_id = "meanrev.daily.test.v.v1"
    config.family = "meanrev"
    config.stage = "backtest"
    config.path = yaml_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.universe = universe

    context = StrategyContext(
        strategy_id=config.strategy_id,
        family="meanrev",
        template="daily.test",
        variant="v",
        version=1,
        config_hash="hash",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path=str(yaml_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = _decision([])

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def _make_event_store() -> EventStore:
    return EventStore(Path(tempfile.mktemp(suffix=".db")))


def _make_engine(universe=("SPY",), bars_start=date(2024, 1, 2), bar_count=30):
    loaded = _make_loaded_strategy(universe)
    closes = [100.0 + i for i in range(bar_count)]
    barset = _make_barset(closes, start=bars_start)
    provider = MagicMock()
    provider.get_bars.return_value = {universe[0]: barset}
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    return engine, store, bars_start


# ---------------------------------------------------------------------------
# run_walk_forward — orchestration behaviour
# ---------------------------------------------------------------------------


def test_walk_forward_raises_when_range_too_short():
    engine, _, bars_start = _make_engine(bar_count=5)
    with pytest.raises(ValueError, match="Not enough trading days"):
        run_walk_forward(
            engine,
            start_date=bars_start,
            end_date=bars_start + timedelta(days=4),
            train_days=10,
            test_days=5,
            step_days=5,
        )


def test_walk_forward_raises_on_inverted_dates():
    engine, _, bars_start = _make_engine(bar_count=30)
    with pytest.raises(ValueError, match="end_date"):
        run_walk_forward(
            engine,
            start_date=bars_start + timedelta(days=10),
            end_date=bars_start,
            train_days=5,
            test_days=3,
            step_days=3,
        )


def test_walk_forward_creates_one_parent_run_not_one_per_window():
    """All per-window trades must land under a single BacktestRunEvent.

    This is the promotion-gate contract: evaluation hangs off one run_id.
    If we accidentally created a run per window, the gate could only see one.
    """
    engine, store, bars_start = _make_engine(bar_count=30)
    result = run_walk_forward(
        engine,
        start_date=bars_start,
        end_date=bars_start + timedelta(days=29),
        train_days=10,
        test_days=5,
        step_days=5,
    )
    runs = store.list_backtest_runs()
    # exactly one run on disk; its id matches the result
    assert len(runs) == 1
    assert runs[0].run_id == result.run_id
    assert runs[0].metadata["walk_forward"] is True


def test_walk_forward_metadata_contains_oos_aggregate_and_stability():
    engine, store, bars_start = _make_engine(bar_count=30)
    result = run_walk_forward(
        engine,
        start_date=bars_start,
        end_date=bars_start + timedelta(days=29),
        train_days=10,
        test_days=5,
        step_days=5,
    )
    stored = store.get_backtest_run(result.run_id)
    assert stored is not None
    md = stored.metadata
    assert "oos_aggregate" in md
    assert "stability" in md
    assert "windows" in md
    assert len(md["windows"]) == len(result.windows)


def test_walk_forward_per_window_initial_equity_resets():
    """Each window must start at the caller-specified initial_equity.

    If initial_equity leaked between windows, a big drawdown in window 1
    would shrink window 2's capital base — masking window 2's real return.
    """
    engine, _, bars_start = _make_engine(bar_count=30)
    result = run_walk_forward(
        engine,
        start_date=bars_start,
        end_date=bars_start + timedelta(days=29),
        train_days=10,
        test_days=5,
        step_days=5,
        initial_equity=50_000.0,
    )
    assert result.initial_equity == 50_000.0
    assert all(w.initial_equity == 50_000.0 for w in result.windows)


# ---------------------------------------------------------------------------
# _aggregate_oos — stitching math
# ---------------------------------------------------------------------------


def _window(
    returns_pct: list[float],
    *,
    index: int = 0,
    initial: float = 100.0,
    trade_count: int = 0,
) -> WalkForwardWindow:
    """Build a WalkForwardWindow whose equity curve realises ``returns_pct``."""
    equity_curve: list[tuple[date, float]] = []
    equity = initial
    d = date(2024, 1, 1)
    equity_curve.append((d, equity))
    for r in returns_pct:
        d = d + timedelta(days=1)
        equity *= 1.0 + r / 100.0
        equity_curve.append((d, equity))
    return WalkForwardWindow(
        index=index,
        train_start=date(2023, 1, 1),
        train_end=date(2023, 12, 31),
        test_start=date(2024, 1, 1),
        test_end=d,
        trading_days=len(returns_pct) + 1,
        trade_count=trade_count,
        initial_equity=initial,
        final_equity=equity,
        total_return_pct=(equity - initial) / initial * 100.0,
        sharpe=None,
        max_drawdown_pct=0.0,
        equity_curve=equity_curve,
    )


def test_aggregate_oos_compounds_windows():
    """Two windows each +10% must aggregate to +21% (geometric).

    This is the single statistically-honest way to combine per-window returns.
    Arithmetic averaging (would give +10%) is wrong because it ignores that
    window-2 capital is 1.10× window-1 capital under continuous deployment.
    """
    windows = [_window([10.0], index=0), _window([10.0], index=1)]
    aggregate = _aggregate_oos(windows, initial_equity=100.0)
    assert aggregate.total_return_pct == pytest.approx(21.0, rel=1e-6)


def test_aggregate_oos_handles_empty_windows():
    aggregate = _aggregate_oos([], initial_equity=100.0)
    assert aggregate.trade_count == 0
    assert aggregate.trading_days == 0
    assert aggregate.total_return_pct == 0.0
    assert aggregate.sharpe is None
    assert aggregate.max_drawdown_pct == 0.0
    assert aggregate.equity_curve == []


def test_aggregate_oos_sharpe_is_on_concatenated_returns():
    """Sharpe must be computed on the stitched daily-return stream, not averaged.

    If it were averaged across per-window Sharpes it would double-count
    volatility; the correct measure treats the strategy's deployed return
    stream as one series.
    """
    windows = [
        _window([1.0, -1.0, 1.0, -1.0], index=0),
        _window([1.0, -1.0, 1.0, -1.0], index=1),
    ]
    aggregate = _aggregate_oos(windows, initial_equity=100.0)
    # Returns are roughly mean-zero so Sharpe should be near zero.
    assert aggregate.sharpe is not None
    assert abs(aggregate.sharpe) < 1.0


# ---------------------------------------------------------------------------
# Stability diagnostics
# ---------------------------------------------------------------------------


def test_single_window_dependency_flips_on_dropping_best():
    """Aggregate +return that goes negative when the best window is removed.

    Classic fragility pattern: three losing windows, one big winner. The
    aggregate looks fine; remove the winner and the strategy is a loser.
    """
    windows = [
        _window([20.0], index=0),
        _window([-2.0], index=1),
        _window([-3.0], index=2),
        _window([-2.0], index=3),
    ]
    assert _single_window_dependency(windows) is True


def test_single_window_dependency_false_when_robust():
    """Aggregate stays positive even after the best window is removed."""
    windows = [
        _window([5.0], index=0),
        _window([4.0], index=1),
        _window([3.0], index=2),
    ]
    assert _single_window_dependency(windows) is False


def test_single_window_dependency_false_when_already_negative():
    """Can't be 'single-window-dependent' if the aggregate is already negative.

    The check is about protecting *apparent* edge from being spurious; if
    there's no apparent edge, there's nothing to protect.
    """
    windows = [
        _window([-5.0], index=0),
        _window([-4.0], index=1),
    ]
    assert _single_window_dependency(windows) is False


def test_single_window_dependency_false_with_fewer_than_two_windows():
    """The check is ill-defined for a single window."""
    assert _single_window_dependency([_window([10.0], index=0)]) is False
    assert _single_window_dependency([]) is False


def test_compute_stability_counts_positive_and_negative_windows():
    windows = [
        _window([5.0], index=0),
        _window([-2.0], index=1),
        _window([0.0], index=2),
        _window([3.0], index=3),
    ]
    stability = _compute_stability(windows)
    assert stability.windows_positive == 2
    assert stability.windows_negative == 1

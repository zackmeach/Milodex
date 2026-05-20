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
    derive_walk_forward_spans,
    run_walk_forward,
)
from milodex.core.event_store import EventStore
from milodex.data.bar_quality import DataQualityError
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
    config.tempo = {"bar_size": "1D"}
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
    strategy.max_lookback_periods.return_value = 0

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


def test_walk_forward_explanations_are_parented_to_backtest_run():
    """Every walk-forward explanation row must carry backtest_run_id.

    Closes the orphan-evaluation gap (migration 008): walk-forward used to
    write explanations with session_id ``<run_id>:wN`` and no explicit
    backtest_run_id link, so analytics joining only on session_id
    ↔ strategy_runs lost them. The runner now passes the parent
    backtest_run.id through to record_no_action/_record_execution; this
    test pins that contract end-to-end.
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

    parent = store.get_backtest_run(result.run_id)
    assert parent is not None
    explanations = [e for e in store.list_explanations() if e.submitted_by == "backtest_engine"]
    assert explanations, "walk-forward should have produced at least one explanation"
    assert all(e.backtest_run_id == parent.id for e in explanations), (
        "every walk-forward explanation must point at the parent backtest_run.id "
        "(not just the synthetic walk-forward session_id)"
    )


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
    assert md["oos_aggregate"]["skipped_count"] == result.oos_skipped_count
    assert all("skipped_count" in window for window in md["windows"])


def test_walk_forward_metadata_contains_data_quality_report():
    engine, store, bars_start = _make_engine(universe=("SPY", "QQQ"), bar_count=30)
    qqq_rows = [
        {
            "timestamp": pd.Timestamp(bars_start + timedelta(days=i), tz="UTC"),
            "open": 100.0 + i,
            "high": 100.0 + i,
            "low": 100.0 + i,
            "close": 100.0 + i,
            "volume": 1000,
            "vwap": 100.0 + i,
        }
        for i in range(29)
    ]
    engine._data_provider.get_bars.return_value["QQQ"] = BarSet(pd.DataFrame(qqq_rows))  # noqa: SLF001

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
    assert result.data_quality["status"] == "pass_with_warnings"
    assert stored.metadata["data_quality"] == result.data_quality
    assert stored.metadata["run_manifest"] == result.run_manifest
    assert result.run_manifest["data"]["quality"]["status"] == "pass_with_warnings"


def test_walk_forward_data_quality_blocker_marks_parent_run_failed():
    engine, store, bars_start = _make_engine(bar_count=30)
    bad_rows = engine._data_provider.get_bars.return_value["SPY"].to_dataframe()  # noqa: SLF001
    bad_rows.loc[bad_rows.index[0], "low"] = 999.0
    engine._data_provider.get_bars.return_value["SPY"] = BarSet(bad_rows)  # noqa: SLF001

    with pytest.raises(DataQualityError):
        run_walk_forward(
            engine,
            start_date=bars_start,
            end_date=bars_start + timedelta(days=29),
            train_days=10,
            test_days=5,
            step_days=5,
            run_id="wf-bad-quality",
        )

    stored = store.get_backtest_run("wf-bad-quality")
    assert stored is not None
    assert stored.status == "failed"
    assert stored.metadata["walk_forward"] is True
    assert stored.metadata["windows_planned"] == 4
    assert stored.metadata["risk_policy"] == "bypass"
    assert stored.metadata["data_quality"]["status"] == "fail"
    assert stored.metadata["run_manifest"]["data"]["quality"]["status"] == "fail"


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
    skipped_count: int = 0,
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
        skipped_count=skipped_count,
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
    assert aggregate.skipped_count == 0
    assert aggregate.trading_days == 0
    assert aggregate.total_return_pct == 0.0
    assert aggregate.sharpe is None
    assert aggregate.max_drawdown_pct == 0.0
    assert aggregate.equity_curve == []


def test_aggregate_oos_sums_skipped_counts():
    windows = [
        _window([1.0], index=0, trade_count=2, skipped_count=3),
        _window([1.0], index=1, trade_count=4, skipped_count=5),
    ]

    aggregate = _aggregate_oos(windows, initial_equity=100.0)

    assert aggregate.trade_count == 6
    assert aggregate.skipped_count == 8


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


def test_aggregate_oos_aligns_dates_to_returns_with_nonpositive_prior_day():
    """A non-positive-prior equity day must not misalign dates to returns.

    ``daily_returns_from_equity`` only emits a return when the prior day's
    equity is > 0, so an equity curve with a wiped-out (0.0) day produces
    fewer returns than ``equity_curve[1:]`` has elements. The old
    ``zip(..., strict=False)`` silently truncated, pairing later dates with
    the wrong returns and corrupting the stitched OOS curve — and therefore
    ``oos_sharpe`` / ``oos_max_drawdown_pct`` that the promotion capital gate
    consumes. The stitched curve must stay one equity point per curve step
    with the correct date attached to each step.
    """
    d = date(2024, 1, 1)
    # Day 1 wipes equity to 0.0, day 2 recovers to 5.0, day 3 to 6.0.
    equity_curve = [
        (d, 100.0),
        (d + timedelta(days=1), 0.0),
        (d + timedelta(days=2), 5.0),
        (d + timedelta(days=3), 6.0),
    ]
    window = WalkForwardWindow(
        index=0,
        train_start=date(2023, 1, 1),
        train_end=date(2023, 12, 31),
        test_start=d,
        test_end=d + timedelta(days=3),
        trading_days=4,
        trade_count=0,
        skipped_count=0,
        initial_equity=100.0,
        final_equity=6.0,
        total_return_pct=-94.0,
        sharpe=None,
        max_drawdown_pct=0.0,
        equity_curve=equity_curve,
    )

    aggregate = _aggregate_oos([window], initial_equity=100.0)

    # One stitched point per equity-curve step (seed + 3 transitions),
    # each carrying its own curve date — no truncation, no shifted dates.
    assert len(aggregate.equity_curve) == len(equity_curve)
    assert [day for day, _ in aggregate.equity_curve] == [day for day, _ in equity_curve]
    # The final stitched date must be the curve's last date (not dropped).
    assert aggregate.equity_curve[-1][0] == d + timedelta(days=3)


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


# ---------------------------------------------------------------------------
# Portfolio snapshot per window (R-XC-016 closure for analytics/snapshots.py)
# ---------------------------------------------------------------------------


def test_walk_forward_records_one_snapshot_per_window():
    """Each OOS window produces its own backtest_equity_snapshots row (ADR 0053).

    The walk-forward orchestrator calls `engine.simulate_window` per window
    with a window-scoped session_id (`{run_id}:w{index}`). The engine writes
    one snapshot per simulation to backtest_equity_snapshots (not
    portfolio_snapshots — ADR 0053 split), so N windows ⇒ N snapshot rows,
    each queryable independently by session_id.
    """
    import sqlite3

    engine, store, bars_start = _make_engine(bar_count=30)
    result = run_walk_forward(
        engine,
        start_date=bars_start,
        end_date=bars_start + timedelta(days=29),
        train_days=10,
        test_days=5,
        step_days=5,
    )

    assert len(result.windows) > 0, "fixture must produce at least one OOS window"

    # ADR 0053: walk-forward snapshots are in backtest_equity_snapshots, not portfolio_snapshots
    with sqlite3.connect(store._path) as con:
        con.row_factory = sqlite3.Row
        for window in result.windows:
            session_id = f"{result.run_id}:w{window.index}"
            rows = con.execute(
                "SELECT equity FROM backtest_equity_snapshots WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            assert len(rows) == 1, (
                f"window {window.index}: expected exactly one backtest_equity_snapshots row at "
                f"session_id={session_id!r}, found {len(rows)}"
            )
            # Snapshot equity matches the window's reported final_equity.
            assert rows[0]["equity"] == pytest.approx(window.final_equity)

            # portfolio_snapshots must NOT contain this session_id
            broker_rows = store.list_portfolio_snapshots_for_session(session_id)
            assert broker_rows == [], (
                f"portfolio_snapshots must not contain walk-forward session"
                f" {session_id!r} (ADR 0053)"
            )


# ---------------------------------------------------------------------------
# derive_walk_forward_spans — shared helper (refactor/bench-facade-layering)
# ---------------------------------------------------------------------------


class _FakeDeriveEngine:
    """Minimal engine stub for derive_walk_forward_spans tests.

    Uses the public ``walk_forward_windows`` attribute instead of
    ``_loaded.config.backtest``, which is what the real ``BacktestEngine``
    property surfaces.
    """

    def __init__(self, *, bars: dict, walk_forward_windows: int = 4) -> None:
        self._bars = bars
        self.walk_forward_windows = walk_forward_windows
        # Minimal ``_loaded`` stub so derive_walk_forward_spans can read
        # ``tempo["bar_size"]`` without hitting a real engine.
        self._loaded = type("_L", (), {
            "config": type("_C", (), {
                "tempo": {"bar_size": "1D"}
            })()
        })()

    def prefetch_bars(self, start: date, end: date, *, timeframe=None) -> dict:  # noqa: ARG002
        return self._bars


def _bars_for_days(n: int, start: date = date(2024, 1, 2)) -> dict:
    """Build a single-symbol BarSet with *n* calendar days of data."""
    rows = [
        {
            "timestamp": pd.Timestamp(start + timedelta(days=i), tz="UTC"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000,
            "vwap": 100.0,
        }
        for i in range(n)
    ]
    return {"SPY": BarSet(pd.DataFrame(rows))}


def test_derive_walk_forward_spans_returns_bars_and_window_tuple():
    """derive_walk_forward_spans returns (all_bars, train, test, step)."""
    start = date(2024, 1, 2)
    end = date(2024, 1, 11)  # 10 calendar days
    bars = _bars_for_days(10, start)
    engine = _FakeDeriveEngine(bars=bars, walk_forward_windows=2)

    all_bars, train_days, test_days, step_days = derive_walk_forward_spans(engine, start, end)

    # bars should be the same object returned by prefetch_bars
    assert all_bars is bars
    # With 10 trading days and 2 windows: test_days = 10 // 3 = 3,
    # train_days = 10 - 2*3 = 4, step_days = test_days = 3.
    assert test_days == step_days, "step_days must equal test_days (non-overlapping)"
    assert train_days >= 1
    assert train_days + 2 * test_days <= 10


def test_derive_walk_forward_spans_uses_public_walk_forward_windows():
    """The helper reads engine.walk_forward_windows, not engine._loaded."""
    start = date(2024, 1, 2)
    bars = _bars_for_days(20, start)
    end = start + timedelta(days=19)

    engine_2 = _FakeDeriveEngine(bars=bars, walk_forward_windows=2)
    engine_4 = _FakeDeriveEngine(bars=bars, walk_forward_windows=4)

    _, train_2, test_2, _ = derive_walk_forward_spans(engine_2, start, end)
    _, train_4, test_4, _ = derive_walk_forward_spans(engine_4, start, end)

    # More windows → smaller test slices → larger training prelude
    assert test_4 < test_2 or train_4 > train_2, (
        "different walk_forward_windows must produce different span tuples"
    )


def test_derive_walk_forward_spans_raises_on_insufficient_days():
    """Too few trading days propagates the ValueError from compute_window_spans."""
    bars = _bars_for_days(1, date(2024, 1, 2))
    engine = _FakeDeriveEngine(bars=bars, walk_forward_windows=4)

    with pytest.raises(ValueError, match="Not enough trading days"):
        derive_walk_forward_spans(engine, date(2024, 1, 2), date(2024, 1, 2))


def test_derive_walk_forward_spans_identical_output_to_manual_derivation():
    """derive_walk_forward_spans produces the same result as the manual derivation
    that the two call sites previously duplicated independently."""
    from milodex.backtesting.engine import _trading_days_in_range
    from milodex.backtesting.walk_forward_runner import compute_window_spans

    start = date(2024, 1, 2)
    end = date(2024, 1, 11)
    n_windows = 3
    bars = _bars_for_days(10, start)
    engine = _FakeDeriveEngine(bars=bars, walk_forward_windows=n_windows)

    all_bars, train, test, step = derive_walk_forward_spans(engine, start, end)

    # Replicate the pre-refactor manual derivation
    manual_all_bars = engine.prefetch_bars(start, end)
    total_days = len(_trading_days_in_range(manual_all_bars, start, end))
    manual_train, manual_test, manual_step = compute_window_spans(total_days, n_windows)

    assert (train, test, step) == (manual_train, manual_test, manual_step)
    assert all_bars is manual_all_bars

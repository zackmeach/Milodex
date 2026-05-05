"""Tests for the mean-reversion IBS low-close strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.meanrev_ibs_lowclose import MeanrevIbsLowcloseStrategy

# ---------------------------------------------------------------------------
# Entry-side coverage
# ---------------------------------------------------------------------------


def test_ibs_selects_lowest_ibs_above_ma_and_sizes_with_equity() -> None:
    """Happy path: across a four-symbol universe, the strategy ranks
    candidates by IBS-ascending, takes the top-N within capacity, and sizes
    each position from equity * per_position_notional_pct.
    """
    strategy = MeanrevIbsLowcloseStrategy()
    universe = ("SPY", "QQQ", "IWM", "DIA")

    spy = _trend_up_with_low_close(base=400.0, low_close_ibs=0.10)
    qqq = _trend_up_with_low_close(base=350.0, low_close_ibs=0.05)
    iwm = _trend_up_with_low_close(base=200.0, low_close_ibs=0.18)
    dia = _trend_up_with_low_close(base=300.0, low_close_ibs=0.50)  # not low — rejected

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={
            "SPY": _ohlc_barset(spy),
            "QQQ": _ohlc_barset(qqq),
            "IWM": _ohlc_barset(iwm),
            "DIA": _ohlc_barset(dia),
        },
    )

    intents = strategy.evaluate(_ohlc_barset(spy), context).intents

    # QQQ has the lowest IBS (0.05), then SPY (0.10). Capacity=2 so DIA and
    # IWM lose; DIA fails the entry threshold outright.
    intent_tuples = [(intent.side.value, intent.symbol, intent.quantity) for intent in intents]
    spy_close = spy[-1][3]
    qqq_close = qqq[-1][3]
    assert intent_tuples == [
        ("buy", "QQQ", _expected_shares(10_000.0, 0.20, qqq_close)),
        ("buy", "SPY", _expected_shares(10_000.0, 0.20, spy_close)),
    ]
    for intent in intents:
        assert intent.order_type == OrderType.MARKET


def test_ibs_skips_symbols_above_entry_threshold() -> None:
    """A symbol whose latest IBS is at or above ibs_entry_threshold must
    not produce an entry intent — the threshold is the only oversold gate.
    """
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.50)

    context = _context(
        universe=("SPY",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_ibs_skips_symbols_below_ma_filter() -> None:
    """A symbol whose latest close is below its long-term MA must be
    rejected even if IBS is low. The MA filter is the family's bullish-
    regime gate; below the MA is a different distribution than above it.
    """
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _trend_down_with_low_close(base=400.0, low_close_ibs=0.10)

    context = _context(
        universe=("SPY",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.intents == []
    rejections = {
        entry["symbol"]: entry["reason"] for entry in decision.reasoning.rejected_alternatives
    }
    assert "SPY" in rejections
    assert "SMA" in rejections["SPY"] or "not above" in rejections["SPY"]


def test_ibs_rejects_zero_range_bar() -> None:
    """Degenerate bars where High == Low (limit-locked or untraded) yield
    an undefined IBS. The strategy refuses rather than treating it as 0
    or 0.5 — a 0-range bar is not honest oversold evidence.
    """
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.10)
    # Replace the last bar with a degenerate 0-range bar (still above the MA).
    last_open, _last_high, _last_low, last_close = bars[-1]
    bars[-1] = (last_close, last_close, last_close, last_close)

    context = _context(
        universe=("SPY",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.intents == []
    rejections = {
        entry["symbol"]: entry["reason"] for entry in decision.reasoning.rejected_alternatives
    }
    assert "SPY" in rejections
    assert "IBS undefined" in rejections["SPY"]
    _ = last_open  # appease the linter; used to clarify the destructure


def test_ibs_ranking_disabled_keeps_universe_order() -> None:
    """When ranking is disabled, qualifying candidates are taken in the
    order they appear in the universe tuple, not by IBS.
    """
    strategy = MeanrevIbsLowcloseStrategy()
    universe = ("SPY", "QQQ", "IWM")
    bars = {
        "SPY": _ohlc_barset(_trend_up_with_low_close(base=400.0, low_close_ibs=0.18)),
        "QQQ": _ohlc_barset(_trend_up_with_low_close(base=350.0, low_close_ibs=0.05)),
        "IWM": _ohlc_barset(_trend_up_with_low_close(base=200.0, low_close_ibs=0.10)),
    }

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=False,
        bars_by_symbol=bars,
    )

    intents = strategy.evaluate(_ohlc_barset([(1, 1, 1, 1)]), context).intents
    assert [intent.symbol for intent in intents] == ["SPY", "QQQ"]


# ---------------------------------------------------------------------------
# Exit-side coverage — one test per exit rule
# ---------------------------------------------------------------------------


def test_ibs_exits_when_close_above_prior_day_high() -> None:
    """Signal exit: today's close > yesterday's high → snapback complete,
    sell at next open.
    """
    strategy = MeanrevIbsLowcloseStrategy()
    # Build a series where the final bar's close clears the prior bar's high.
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.50, length=210)
    # Replace last two bars with a low day followed by a strong close above
    # that low day's high.
    bars[-2] = (399.0, 401.0, 397.0, 397.5)  # prior_high = 401.0
    bars[-1] = (400.0, 403.0, 399.0, 402.5)  # close 402.5 > 401.0 → exit

    context = _context(
        universe=("SPY",),
        positions={"SPY": 5.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
        entry_state={"SPY": {"entry_price": 400.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert [(i.side.value, i.symbol, i.quantity) for i in decision.intents] == [
        ("sell", "SPY", 5.0),
    ]
    assert decision.reasoning.rule == "meanrev.ibs_exit"


def test_ibs_exits_on_max_hold_days() -> None:
    """Time stop: held_days >= max_hold_days fires regardless of signal."""
    strategy = MeanrevIbsLowcloseStrategy()
    # Bars that wouldn't trigger any other exit (close below prior high,
    # close above stop_loss line).
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.50, length=210)
    bars[-1] = (400.0, 401.0, 399.0, 399.5)  # close not above prior high

    context = _context(
        universe=("SPY",),
        positions={"SPY": 5.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
        entry_state={"SPY": {"entry_price": 400.0, "held_days": 3}},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert [(i.side.value, i.symbol, i.quantity) for i in decision.intents] == [
        ("sell", "SPY", 5.0),
    ]
    assert decision.reasoning.rule == "meanrev.max_hold"


def test_ibs_exits_on_stop_loss_breach() -> None:
    """Loss stop: close <= entry * (1 - stop_loss_pct) fires before any
    other exit (most-specific-first precedence).
    """
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.50, length=210)
    # Entry at 400; stop_loss_pct=0.05 (default in _context). Need close
    # at or below 380. Set last bar close to 379.
    bars[-1] = (382.0, 383.0, 378.0, 379.0)

    context = _context(
        universe=("SPY",),
        positions={"SPY": 5.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
        entry_state={"SPY": {"entry_price": 400.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert [(i.side.value, i.symbol, i.quantity) for i in decision.intents] == [
        ("sell", "SPY", 5.0),
    ]
    assert decision.reasoning.rule == "meanrev.stop_loss"


def test_ibs_stop_loss_dominates_signal_exit() -> None:
    """When both stop_loss and signal-exit fire on the same bar, the more
    specific stop_loss rule names the explanation (precedence).
    """
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.50, length=210)
    # Engineer a bar where both exits would fire: close clears prior high
    # AND breaches the stop_loss line. Possible only if prior_high < stop
    # threshold — set up that way.
    bars[-2] = (200.0, 201.0, 199.0, 200.0)  # prior_high = 201.0
    # entry=400, stop=0.05 → stop line = 380. close=379 breaches stop AND
    # exceeds prior_high=201 → both rules fire. Stop must win.
    bars[-1] = (382.0, 383.0, 378.0, 379.0)

    context = _context(
        universe=("SPY",),
        positions={"SPY": 5.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
        entry_state={"SPY": {"entry_price": 400.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.reasoning.rule == "meanrev.stop_loss"


# ---------------------------------------------------------------------------
# Universe scoping & no-signal coverage
# ---------------------------------------------------------------------------


def test_ibs_ignores_positions_outside_declared_universe() -> None:
    """A position on a symbol outside the IBS universe belongs to another
    strategy. IBS must not emit an exit for it. Same regression as the
    RSI(2) template's out-of-universe test.
    """
    strategy = MeanrevIbsLowcloseStrategy()
    spy_bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.50, length=210)
    aapl_bars = _trend_up_with_low_close(base=170.0, low_close_ibs=0.50, length=210)
    # AAPL bars contrived so a signal exit *would* fire if IBS treated it
    # as its own — close above prior high.
    aapl_bars[-2] = (168.0, 170.0, 166.0, 167.0)
    aapl_bars[-1] = (169.0, 172.0, 168.0, 171.0)

    context = _context(
        universe=("SPY",),
        positions={"AAPL": 10.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(spy_bars), "AAPL": _ohlc_barset(aapl_bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(spy_bars), context)
    sells = [i for i in decision.intents if i.side == OrderSide.SELL]
    assert sells == []


def test_ibs_returns_no_signal_reasoning_when_nothing_qualifies() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.80, length=210)

    context = _context(
        universe=("SPY",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_ibs_capacity_full_emits_no_signal_reasoning() -> None:
    """When every position slot is already filled, no entry candidates are
    even considered, and the cycle reports no_signal with capacity context.
    """
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.10, length=210)
    flat = _trend_up_with_low_close(base=400.0, low_close_ibs=0.50, length=210)

    context = _context(
        universe=("SPY", "QQQ"),
        positions={"SPY": 1.0, "QQQ": 1.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(flat), "QQQ": _ohlc_barset(bars)},
        entry_state={
            "SPY": {"entry_price": 400.0, "held_days": 0},
            "QQQ": {"entry_price": 400.0, "held_days": 0},
        },
    )

    decision = strategy.evaluate(_ohlc_barset(flat), context)
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert "capacity" in decision.reasoning.narrative


# ---------------------------------------------------------------------------
# Reasoning + parameter validation
# ---------------------------------------------------------------------------


def test_ibs_entry_reasoning_captures_ranking_and_rejections() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    universe = ("SPY", "QQQ", "DIA")
    bars = {
        "SPY": _ohlc_barset(_trend_up_with_low_close(base=400.0, low_close_ibs=0.10)),
        "QQQ": _ohlc_barset(_trend_up_with_low_close(base=350.0, low_close_ibs=0.05)),
        "DIA": _ohlc_barset(_trend_up_with_low_close(base=300.0, low_close_ibs=0.50)),
    }

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol=bars,
    )

    decision = strategy.evaluate(_ohlc_barset([(1, 1, 1, 1)]), context)

    assert decision.reasoning.rule == "meanrev.ibs_entry"
    assert decision.reasoning.ranking is not None
    rejections = {
        entry["symbol"]: entry["reason"] for entry in decision.reasoning.rejected_alternatives
    }
    assert "DIA" in rejections
    assert "IBS" in rejections["DIA"] or "entry threshold" in rejections["DIA"]
    assert "ibs_entry_threshold" in decision.reasoning.threshold


def test_ibs_rejects_invalid_parameters() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _trend_up_with_low_close(base=400.0, low_close_ibs=0.10)

    context = _context(
        universe=("SPY",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"SPY": _ohlc_barset(bars)},
        override_parameters={"ibs_entry_threshold": 1.5},
    )

    with pytest.raises(ValueError, match="ibs_entry_threshold must be in"):
        strategy.evaluate(_ohlc_barset(bars), context)


def test_default_strategy_loader_resolves_ibs_strategy() -> None:
    """The IBS strategy is registered in the default loader's registry and
    its config validates end-to-end.
    """
    loader = StrategyLoader()

    loaded = loader.load(Path("configs/meanrev_daily_ibs_lowclose_v1.yaml"))

    assert isinstance(loaded.strategy, MeanrevIbsLowcloseStrategy)
    assert loaded.context.strategy_id == "meanrev.daily.ibs_lowclose.index_etfs.v1"
    assert loaded.context.universe_ref == "universe.index_etfs.v1"
    assert "SPY" in loaded.context.universe
    assert "QQQ" in loaded.context.universe
    assert "IWM" in loaded.context.universe
    assert "DIA" in loaded.context.universe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(
    *,
    universe: tuple[str, ...],
    positions: dict[str, float],
    equity: float,
    max_concurrent_positions: int,
    ranking_enabled: bool,
    bars_by_symbol: dict[str, BarSet],
    entry_state: dict[str, dict[str, object]] | None = None,
    override_parameters: dict[str, object] | None = None,
) -> StrategyContext:
    parameters: dict[str, object] = {
        "ibs_entry_threshold": 0.20,
        "ma_filter_length": 200,
        "stop_loss_pct": 0.05,
        "max_hold_days": 3,
        "max_concurrent_positions": max_concurrent_positions,
        "sizing_rule": "equal_notional",
        "per_position_notional_pct": 0.20,
        "ranking_enabled": ranking_enabled,
        "ranking_metric": "ibs_ascending",
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="meanrev.daily.ibs_lowclose.index_etfs.v1",
        family="meanrev",
        template="daily.ibs_lowclose",
        variant="index_etfs",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=universe,
        universe_ref="universe.index_etfs.v1",
        disable_conditions=(),
        config_path="configs/meanrev_daily_ibs_lowclose_v1.yaml",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _ohlc_barset(rows: list[tuple[float, float, float, float]]) -> BarSet:
    """Build a BarSet from (open, high, low, close) tuples. Volume + vwap
    are filled with deterministic placeholders since IBS doesn't read them.
    """
    timestamps = pd.date_range("2025-01-01", periods=len(rows), freq="D", tz=UTC)
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "volume": [1_000_000] * len(rows),
            "vwap": [row[3] for row in rows],
        }
    )
    return BarSet(df)


def _trend_up_with_low_close(
    *,
    base: float,
    low_close_ibs: float,
    length: int = 210,
) -> list[tuple[float, float, float, float]]:
    """Build an OHLC series that ramps upward (close > 200-SMA) and ends with
    a single bar whose IBS = low_close_ibs.

    The bar's range is fixed at ~1% of base so range > 0 always holds. The
    SMA is computed over closes, so the ramp's per-bar +0.5 ensures the
    final close sits well above the 200-SMA mean for any low_close_ibs.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length - 1):
        c = base + idx * 0.5
        rows.append((c - 0.1, c + 0.1, c - 0.2, c))
    last_close_baseline = rows[-1][3]
    high = last_close_baseline + 1.0
    low = last_close_baseline - 1.0
    band = high - low
    close = low + low_close_ibs * band
    rows.append((last_close_baseline, high, low, close))
    return rows


def _trend_down_with_low_close(
    *,
    base: float,
    low_close_ibs: float,
    length: int = 210,
) -> list[tuple[float, float, float, float]]:
    """Mirror of _trend_up_with_low_close: ramps downward so the final close
    is below the long-term SMA. Used to confirm the MA filter rejects an
    otherwise-low-IBS bar.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length - 1):
        c = base - idx * 0.5
        rows.append((c + 0.1, c + 0.2, c - 0.1, c))
    last_close_baseline = rows[-1][3]
    high = last_close_baseline + 1.0
    low = last_close_baseline - 1.0
    band = high - low
    close = low + low_close_ibs * band
    rows.append((last_close_baseline, high, low, close))
    return rows


def _expected_shares(equity: float, notional_pct: float, unit_price: float) -> float:
    import math

    return float(max(0, math.floor((equity * notional_pct) / unit_price)))

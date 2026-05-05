"""Tests for the cross-sectional momentum rotation strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.momentum_xsec_rotation import MomentumXsecRotationStrategy

# ---------------------------------------------------------------------------
# Cadence — non-rebalance bar suppresses ranking
# ---------------------------------------------------------------------------


def test_xsec_returns_no_signal_on_non_rebalance_bar() -> None:
    """The cadence is the load-bearing invariant: ranking + entries fire only
    on bars whose weekday equals rebalance_weekday. On other days the
    strategy is silent (entries-wise), even if a top-ranked symbol exists.
    """
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK", "XLF")
    bars = {
        "XLK": _make_bars(_descending_then_strong_uptrend(base=100.0), end_weekday=2),  # Wed
        "XLF": _make_bars(_descending_then_strong_uptrend(base=50.0), end_weekday=2),
    }

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert "non-rebalance" in decision.reasoning.narrative


def test_xsec_emits_entries_on_friday_rebalance() -> None:
    """Happy path: on Friday close, the top-`target_positions` symbols by
    trailing ranking_lookback return are entered.
    """
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK", "XLF", "XLU")
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),  # winner
        "XLF": _make_bars(_strong_uptrend(base=50.0, total_return=0.15), end_weekday=4),  # 2nd
        "XLU": _make_bars(_strong_uptrend(base=80.0, total_return=0.05), end_weekday=4),  # 3rd
    }

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
        # No regime filter, no MA filter — keep the test focused on ranking.
        override_parameters={
            "market_regime_symbol": "",
            "ma_filter_length": 0,
        },
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    intents = decision.intents
    assert len(intents) == 2
    assert {i.symbol for i in intents} == {"XLK", "XLF"}
    for intent in intents:
        assert intent.side == OrderSide.BUY
        assert intent.order_type == OrderType.MARKET
    assert decision.reasoning.rule == "momentum.xsec_entry"


# ---------------------------------------------------------------------------
# Rank-based exits
# ---------------------------------------------------------------------------


def test_xsec_rank_exit_when_holding_drops_outside_top_buffer() -> None:
    """A held symbol that falls outside `exit_outside_top_n` on the rebalance
    bar must be sold.
    """
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK", "XLF", "XLU", "XLE")
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),
        "XLF": _make_bars(_strong_uptrend(base=50.0, total_return=0.20), end_weekday=4),
        "XLU": _make_bars(_strong_uptrend(base=80.0, total_return=0.10), end_weekday=4),
        "XLE": _make_bars(_strong_uptrend(base=60.0, total_return=0.02), end_weekday=4),  # rank 4
    }

    context = _context(
        universe=universe,
        positions={"XLE": 5.0},  # ranked 4 → outside top_3 buffer → exit
        equity=10_000.0,
        bars_by_symbol=bars,
        # entry_state present so daily stops can read entry_price
        entry_state={"XLE": {"entry_price": 60.0, "held_days": 2}},
        override_parameters={
            "market_regime_symbol": "",
            "ma_filter_length": 0,
        },
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    sells = [i for i in decision.intents if i.side == OrderSide.SELL]
    assert sells == [i for i in decision.intents if i.symbol == "XLE" and i.side == OrderSide.SELL]
    assert len(sells) == 1
    assert sells[0].quantity == 5.0


def test_xsec_keeps_holding_within_top_buffer() -> None:
    """A held symbol whose rank stays within `exit_outside_top_n` is not
    rebalanced out — the hysteresis buffer is the whole point.
    """
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK", "XLF", "XLU")
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),
        "XLF": _make_bars(_strong_uptrend(base=50.0, total_return=0.20), end_weekday=4),
        "XLU": _make_bars(_strong_uptrend(base=80.0, total_return=0.10), end_weekday=4),  # rank 3
    }

    context = _context(
        universe=universe,
        positions={"XLU": 3.0},  # rank 3, exit_outside_top_n=3 → keep
        equity=10_000.0,
        bars_by_symbol=bars,
        entry_state={"XLU": {"entry_price": 80.0, "held_days": 1}},
        override_parameters={
            "market_regime_symbol": "",
            "ma_filter_length": 0,
        },
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    sells = [i for i in decision.intents if i.side == OrderSide.SELL]
    assert "XLU" not in {i.symbol for i in sells}


# ---------------------------------------------------------------------------
# Daily stops fire even on non-rebalance bars
# ---------------------------------------------------------------------------


def test_xsec_stop_loss_fires_on_non_rebalance_bar() -> None:
    """A close-based stop_loss breach must fire on any bar (rebalance or
    not). The strategy commits to a one-week hold but escapes on a hard
    loss without waiting for Friday.
    """
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK",)
    # Stop_loss_pct 0.07 default; entry 100 → stop line 93. Final close 90.
    closes = _ramp_then_breakdown(base=100.0, final_close=90.0)
    bars = {"XLK": _make_bars(closes, end_weekday=2)}  # Wednesday — not Friday

    context = _context(
        universe=universe,
        positions={"XLK": 5.0},
        equity=10_000.0,
        bars_by_symbol=bars,
        entry_state={"XLK": {"entry_price": 100.0, "held_days": 2}},
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    assert decision.reasoning.rule == "momentum.stop_loss"
    sells = [i for i in decision.intents if i.side == OrderSide.SELL]
    assert sells[0].symbol == "XLK"
    assert sells[0].quantity == 5.0


def test_xsec_max_hold_fires_on_non_rebalance_bar() -> None:
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK",)
    closes = [100.0 + idx * 0.1 for idx in range(80)]
    bars = {"XLK": _make_bars(closes, end_weekday=2)}

    context = _context(
        universe=universe,
        positions={"XLK": 5.0},
        equity=10_000.0,
        bars_by_symbol=bars,
        entry_state={"XLK": {"entry_price": 90.0, "held_days": 5}},  # max_hold_days default 5
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    assert decision.reasoning.rule == "momentum.max_hold"


# ---------------------------------------------------------------------------
# Regime filter
# ---------------------------------------------------------------------------


def test_xsec_regime_filter_suppresses_entries_in_bear_market() -> None:
    """When SPY is below its regime MA on the rebalance bar, no entries are
    emitted. Existing-holding rank exits still happen — the regime filter
    is risk-off, not silent.
    """
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK", "XLF")
    spy = _make_bars(_descending_then_flat(start=500.0, length=250), end_weekday=4)
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),
        "XLF": _make_bars(_strong_uptrend(base=50.0, total_return=0.20), end_weekday=4),
        "SPY": spy,
    }

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
        # Default config has SPY regime filter on
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    buys = [i for i in decision.intents if i.side == OrderSide.BUY]
    assert buys == []


# ---------------------------------------------------------------------------
# Universe scoping & no-signal coverage
# ---------------------------------------------------------------------------


def test_xsec_ignores_positions_outside_declared_universe() -> None:
    """A position on a symbol the xsec strategy never bought is not its
    business. Don't issue rank-based or stop-based exits for it.
    """
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK",)
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),
        # AAPL bars set up so a stop_loss WOULD trigger if it were a held xsec position
        "AAPL": _make_bars(_ramp_then_breakdown(base=170.0, final_close=145.0), end_weekday=4),
    }

    context = _context(
        universe=universe,
        positions={"AAPL": 10.0},
        equity=10_000.0,
        bars_by_symbol=bars,
        entry_state={"AAPL": {"entry_price": 170.0, "held_days": 1}},
        override_parameters={"market_regime_symbol": "", "ma_filter_length": 0},
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    sells = [i for i in decision.intents if i.side == OrderSide.SELL]
    assert "AAPL" not in {i.symbol for i in sells}


def test_xsec_returns_no_signal_when_capacity_full_and_top_held() -> None:
    """If the top `target_positions` are already held and no rank-exits or
    stops fire, the cycle is a no-op and emits no_signal with reasoning.
    """
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK", "XLF", "XLU")
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),
        "XLF": _make_bars(_strong_uptrend(base=50.0, total_return=0.20), end_weekday=4),
        "XLU": _make_bars(_strong_uptrend(base=80.0, total_return=0.10), end_weekday=4),
    }

    context = _context(
        universe=universe,
        positions={"XLK": 5.0, "XLF": 5.0},  # top 2 already held
        equity=10_000.0,
        bars_by_symbol=bars,
        entry_state={
            "XLK": {"entry_price": 100.0, "held_days": 1},
            "XLF": {"entry_price": 50.0, "held_days": 1},
        },
        override_parameters={"market_regime_symbol": "", "ma_filter_length": 0},
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    assert [i for i in decision.intents if i.side == OrderSide.BUY] == []


# ---------------------------------------------------------------------------
# Reasoning + parameter validation + loader
# ---------------------------------------------------------------------------


def test_xsec_entry_reasoning_captures_full_ranking() -> None:
    strategy = MomentumXsecRotationStrategy()
    universe = ("XLK", "XLF", "XLU")
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),
        "XLF": _make_bars(_strong_uptrend(base=50.0, total_return=0.20), end_weekday=4),
        "XLU": _make_bars(_strong_uptrend(base=80.0, total_return=0.10), end_weekday=4),
    }

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
        override_parameters={"market_regime_symbol": "", "ma_filter_length": 0},
    )

    decision = strategy.evaluate(_first_barset(bars), context)
    assert decision.reasoning.rule == "momentum.xsec_entry"
    assert decision.reasoning.ranking is not None
    ranks = {entry["symbol"]: entry["rank"] for entry in decision.reasoning.ranking}
    assert ranks["XLK"] == 1
    assert ranks["XLF"] == 2
    assert ranks["XLU"] == 3


def test_xsec_rejects_invalid_exit_buffer_below_target() -> None:
    """exit_outside_top_n < target_positions makes the strategy unstable —
    a holding at exactly its target rank would immediately exit.
    """
    strategy = MomentumXsecRotationStrategy()
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),
    }

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
        override_parameters={"target_positions": 2, "exit_outside_top_n": 1},
    )

    with pytest.raises(ValueError, match="exit_outside_top_n must be"):
        strategy.evaluate(_first_barset(bars), context)


def test_xsec_rejects_invalid_rebalance_weekday() -> None:
    strategy = MomentumXsecRotationStrategy()
    bars = {
        "XLK": _make_bars(_strong_uptrend(base=100.0, total_return=0.30), end_weekday=4),
    }

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
        override_parameters={"rebalance_weekday": 7},
    )

    with pytest.raises(ValueError, match="rebalance_weekday must be"):
        strategy.evaluate(_first_barset(bars), context)


def test_default_strategy_loader_resolves_xsec_strategy() -> None:
    loader = StrategyLoader()

    loaded = loader.load(Path("configs/momentum_daily_xsec_rotation_sector_etfs_v1.yaml"))

    assert isinstance(loaded.strategy, MomentumXsecRotationStrategy)
    assert loaded.context.strategy_id == "momentum.daily.xsec_rotation.sector_etfs.v1"
    assert loaded.context.universe_ref == "universe.sector_etfs_spdr.v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(
    *,
    universe: tuple[str, ...],
    positions: dict[str, float],
    equity: float,
    bars_by_symbol: dict[str, BarSet],
    entry_state: dict[str, dict[str, object]] | None = None,
    override_parameters: dict[str, object] | None = None,
) -> StrategyContext:
    parameters: dict[str, object] = {
        "ranking_lookback": 63,
        "target_positions": 2,
        "exit_outside_top_n": 3,
        "rebalance_weekday": 4,
        "ma_filter_length": 0,
        "stop_loss_pct": 0.07,
        "max_hold_days": 5,
        "max_concurrent_positions": 3,
        "sizing_rule": "equal_notional",
        "per_position_notional_pct": 0.45,
        "ranking_enabled": True,
        "ranking_metric": "xsec_return_descending",
        "market_regime_symbol": "SPY",
        "market_regime_ma_length": 200,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="momentum.daily.xsec_rotation.sector_etfs.v1",
        family="momentum",
        template="daily.xsec_rotation",
        variant="sector_etfs",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=universe,
        universe_ref="universe.sector_etfs_spdr.v1",
        disable_conditions=(),
        config_path="configs/momentum_daily_xsec_rotation_sector_etfs_v1.yaml",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _make_bars(closes: list[float], *, end_weekday: int) -> BarSet:
    """Build a BarSet whose final bar's timestamp lands on the given weekday.

    Cross-sectional cadence is keyed off the latest bar's weekday, so tests
    must control it precisely. We pick a known anchor date (a Monday) and
    advance day-by-day so the LAST bar lands on the requested weekday.

    Phase 1's daily-bar guarantee allows weekend bars to be elided in real
    data; for tests we use contiguous calendar days and align the
    end-weekday by choosing the start date.
    """
    n = len(closes)
    # 2024-01-01 was a Monday (weekday=0). To land bar n-1 on `end_weekday`,
    # start so that (start_weekday + n - 1) % 7 == end_weekday.
    base_monday = pd.Timestamp("2024-01-01", tz=UTC)
    target_offset = (end_weekday - (n - 1)) % 7
    start = base_monday + pd.Timedelta(days=target_offset)
    timestamps = pd.date_range(start, periods=n, freq="D", tz=UTC)
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
            "vwap": closes,
        }
    )
    return BarSet(df)


def _first_barset(bars_by_symbol: dict[str, BarSet]) -> BarSet:
    return next(iter(bars_by_symbol.values()))


def _strong_uptrend(*, base: float, total_return: float, length: int = 80) -> list[float]:
    """Linear ramp from base to base*(1+total_return) over `length` bars."""
    if length < 2:
        return [base]
    final = base * (1.0 + total_return)
    step = (final - base) / (length - 1)
    return [base + idx * step for idx in range(length)]


def _descending_then_strong_uptrend(*, base: float, length: int = 80) -> list[float]:
    """First half declines, second half ramps up. Final return is positive
    over the trailing 63 days, so this still ranks. Used in cadence tests
    that don't care about exact magnitudes.
    """
    half = length // 2
    descending = [base * (1.0 + 0.001 * (half - idx)) for idx in range(half)]
    ascending = [descending[-1] * (1.0 + 0.005 * (idx + 1)) for idx in range(length - half)]
    return descending + ascending


def _descending_then_flat(*, start: float, length: int) -> list[float]:
    """Long descending series — used for SPY in the regime filter test (close
    well below 200-DMA at the end of the series).
    """
    return [start * (1.0 - idx * 0.002) for idx in range(length)]


def _ramp_then_breakdown(*, base: float, final_close: float, length: int = 80) -> list[float]:
    """Ramp upward, then a final close at `final_close`. Used for stop-loss
    tests where the entry_state's entry_price is referenced and the stop
    arithmetic must clear the threshold.
    """
    ramp = [base + idx * 0.05 for idx in range(length - 1)]
    ramp.append(final_close)
    return ramp

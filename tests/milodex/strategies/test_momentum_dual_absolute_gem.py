"""Tests for the dual-momentum (GEM) single-asset rotation strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.momentum_dual_absolute_gem import MomentumDualAbsoluteGemStrategy

# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------


def test_gem_returns_no_signal_on_non_rebalance_bar() -> None:
    """Like the xsec template, GEM is single-asset weekly. No turnover off-rebalance."""
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.10, efa=0.05, agg=0.02, shy=0.01, end_weekday=2)  # Wed

    context = _context(positions={}, equity=10_000.0, bars_by_symbol=bars)
    decision = strategy.evaluate(_first_barset(bars), context)
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert "non-rebalance" in decision.reasoning.narrative


# ---------------------------------------------------------------------------
# Rotation rule — the load-bearing test set
# ---------------------------------------------------------------------------


def test_gem_picks_top_risk_on_when_it_beats_risk_off() -> None:
    """Relative momentum picks the highest-return risk-on; absolute momentum
    confirms (top risk-on > risk-off return) so we hold it.
    """
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.20, efa=0.05, agg=0.02, shy=0.01, end_weekday=4)

    context = _context(positions={}, equity=10_000.0, bars_by_symbol=bars)
    decision = strategy.evaluate(_first_barset(bars), context)

    buys = [i for i in decision.intents if i.side == OrderSide.BUY]
    assert len(buys) == 1
    assert buys[0].symbol == "SPY"
    assert decision.reasoning.rule == "momentum.dual_absolute_rotation"


def test_gem_falls_back_to_risk_off_when_top_risk_on_underperforms() -> None:
    """Absolute momentum: if no risk-on candidate beats risk-off's return,
    target = risk_off (SHY). This is GEM's drawdown-avoidance edge.
    """
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=-0.05, efa=-0.03, agg=-0.10, shy=0.01, end_weekday=4)

    context = _context(positions={}, equity=10_000.0, bars_by_symbol=bars)
    decision = strategy.evaluate(_first_barset(bars), context)

    buys = [i for i in decision.intents if i.side == OrderSide.BUY]
    assert len(buys) == 1
    assert buys[0].symbol == "SHY"


def test_gem_holds_target_when_already_held() -> None:
    """If the current holding equals the rotation target on this rebalance,
    no orders are emitted. Reasoning rule says hold, not rotation.
    """
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.20, efa=0.05, agg=0.02, shy=0.01, end_weekday=4)

    context = _context(
        positions={"SPY": 5.0},
        equity=10_000.0,
        bars_by_symbol=bars,
        entry_state={"SPY": {"entry_price": 400.0, "held_days": 5}},
    )
    decision = strategy.evaluate(_first_barset(bars), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "momentum.dual_absolute_hold"


def test_gem_rotates_from_one_risk_on_to_another() -> None:
    """When the top risk-on changes (e.g. EFA overtakes SPY), the strategy
    sells the current holding and buys the new target on the same rebalance.
    """
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.05, efa=0.20, agg=0.02, shy=0.01, end_weekday=4)

    context = _context(
        positions={"SPY": 5.0},
        equity=10_000.0,
        bars_by_symbol=bars,
        entry_state={"SPY": {"entry_price": 400.0, "held_days": 5}},
    )
    decision = strategy.evaluate(_first_barset(bars), context)

    sells = [i for i in decision.intents if i.side == OrderSide.SELL]
    buys = [i for i in decision.intents if i.side == OrderSide.BUY]
    assert [s.symbol for s in sells] == ["SPY"]
    assert [b.symbol for b in buys] == ["EFA"]


def test_gem_rotates_from_risk_off_to_risk_on_when_market_recovers() -> None:
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.20, efa=0.05, agg=0.02, shy=0.01, end_weekday=4)

    context = _context(
        positions={"SHY": 50.0},
        equity=10_000.0,
        bars_by_symbol=bars,
        entry_state={"SHY": {"entry_price": 80.0, "held_days": 5}},
    )
    decision = strategy.evaluate(_first_barset(bars), context)

    sells = [i for i in decision.intents if i.side == OrderSide.SELL]
    buys = [i for i in decision.intents if i.side == OrderSide.BUY]
    assert [s.symbol for s in sells] == ["SHY"]
    assert [b.symbol for b in buys] == ["SPY"]


# ---------------------------------------------------------------------------
# Sizing + invariant tests
# ---------------------------------------------------------------------------


def test_gem_sizes_with_allocation_pct() -> None:
    """Single-asset full-allocation sizing: floor(equity * allocation_pct / price)."""
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.20, efa=0.05, agg=0.02, shy=0.01, end_weekday=4)

    context = _context(positions={}, equity=10_000.0, bars_by_symbol=bars)
    decision = strategy.evaluate(_first_barset(bars), context)

    buy = next(i for i in decision.intents if i.side == OrderSide.BUY)
    assert buy.symbol == "SPY"
    assert buy.order_type == OrderType.MARKET
    assert buy.quantity > 0


def test_gem_validates_risk_off_symbol_must_be_in_universe() -> None:
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.20, efa=0.05, agg=0.02, shy=0.01, end_weekday=4)

    context = _context(
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
        override_parameters={"risk_off_symbol": "TLT"},  # not in universe
    )

    with pytest.raises(ValueError, match="risk_off_symbol"):
        strategy.evaluate(_first_barset(bars), context)


def test_gem_no_signal_when_lookback_exceeds_history() -> None:
    """Insufficient history → can't compute trailing returns → no_signal."""
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.20, efa=0.05, agg=0.02, shy=0.01, end_weekday=4, length=20)

    context = _context(
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
        override_parameters={"momentum_lookback": 126},
    )
    decision = strategy.evaluate(_first_barset(bars), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_gem_rejects_invalid_rebalance_weekday() -> None:
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.20, efa=0.05, agg=0.02, shy=0.01, end_weekday=4)

    context = _context(
        positions={},
        equity=10_000.0,
        bars_by_symbol=bars,
        override_parameters={"rebalance_weekday": 9},
    )

    with pytest.raises(ValueError, match="rebalance_weekday"):
        strategy.evaluate(_first_barset(bars), context)


def test_gem_emits_full_ranking_in_reasoning() -> None:
    strategy = MomentumDualAbsoluteGemStrategy()
    bars = _gem_bars(spy=0.20, efa=0.05, agg=0.02, shy=0.01, end_weekday=4)

    context = _context(positions={}, equity=10_000.0, bars_by_symbol=bars)
    decision = strategy.evaluate(_first_barset(bars), context)

    assert decision.reasoning.ranking is not None
    symbols_in_ranking = [entry["symbol"] for entry in decision.reasoning.ranking]
    assert set(symbols_in_ranking) == {"SPY", "EFA", "AGG", "SHY"}


def test_default_strategy_loader_resolves_gem_strategy() -> None:
    loader = StrategyLoader()

    loaded = loader.load(Path("configs/momentum_daily_dual_absolute_gem_weekly_v1.yaml"))

    assert isinstance(loaded.strategy, MomentumDualAbsoluteGemStrategy)
    assert loaded.context.strategy_id == "momentum.daily.dual_absolute.gem_weekly.v1"
    assert loaded.context.universe_ref == "universe.gem_quartet.v1"
    assert "SPY" in loaded.context.universe
    assert "SHY" in loaded.context.universe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(
    *,
    positions: dict[str, float],
    equity: float,
    bars_by_symbol: dict[str, BarSet],
    entry_state: dict[str, dict[str, object]] | None = None,
    override_parameters: dict[str, object] | None = None,
) -> StrategyContext:
    parameters: dict[str, object] = {
        "risk_off_symbol": "SHY",
        "momentum_lookback": 126,
        "rebalance_weekday": 4,
        "allocation_pct": 0.95,
        "sizing_rule": "single_asset_full_allocation",
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="momentum.daily.dual_absolute.gem_weekly.v1",
        family="momentum",
        template="daily.dual_absolute",
        variant="gem_weekly",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=("SPY", "EFA", "AGG", "SHY"),
        universe_ref="universe.gem_quartet.v1",
        disable_conditions=(),
        config_path="configs/momentum_daily_dual_absolute_gem_weekly_v1.yaml",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _first_barset(bars_by_symbol: dict[str, BarSet]) -> BarSet:
    return next(iter(bars_by_symbol.values()))


def _gem_bars(
    *,
    spy: float,
    efa: float,
    agg: float,
    shy: float,
    end_weekday: int,
    length: int = 200,
) -> dict[str, BarSet]:
    """Build a four-symbol GEM-quartet bar set where each symbol's trailing
    return over `momentum_lookback=126` bars equals approximately the
    requested value.

    Construction: linear ramp from base to base*(1+r) over `length` bars.
    Trailing 126-day return is computed from close[-1] / close[-1-126], so
    we set base such that close[-1-126] sits at the linear-interpolated
    value matching the requested return.
    """
    return {
        "SPY": _ramp(base=400.0, total_return=spy, length=length, end_weekday=end_weekday),
        "EFA": _ramp(base=70.0, total_return=efa, length=length, end_weekday=end_weekday),
        "AGG": _ramp(base=100.0, total_return=agg, length=length, end_weekday=end_weekday),
        "SHY": _ramp(base=80.0, total_return=shy, length=length, end_weekday=end_weekday),
    }


def _ramp(*, base: float, total_return: float, length: int, end_weekday: int) -> BarSet:
    """Linear ramp over `length` bars whose return over the trailing 126
    bars equals approximately `total_return`. To make this exact, we
    construct closes such that close[-1] / close[-127] - 1 == total_return.
    """
    n = length
    if n < 2:
        closes = [base]
    else:
        # Choose closes such that the trailing-126 ratio is exactly 1+total_return.
        # Easiest: closes[0..n-128] = base; closes[n-127] = base; ramp to
        # closes[n-1] = base * (1 + total_return).
        closes = [base] * max(0, n - 127)
        ramp_n = min(127, n)
        if ramp_n > 1:
            for idx in range(ramp_n):
                closes.append(base * (1.0 + total_return * idx / (ramp_n - 1)))
        else:
            closes.append(base * (1.0 + total_return))
        closes = closes[:n]
    base_monday = pd.Timestamp("2024-01-01", tz=UTC)
    target_offset = (end_weekday - (n - 1)) % 7
    start = base_monday + pd.Timedelta(days=target_offset)
    timestamps = pd.date_range(start, periods=n, freq="D", tz=UTC)
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
            "vwap": closes,
        }
    )
    return BarSet(df)

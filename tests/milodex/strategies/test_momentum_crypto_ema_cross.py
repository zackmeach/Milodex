"""Unit tests for the BTC/USD EMA-cross momentum crypto canary.

Long-only, single-position, 24/7 (no session helper). Small EMA periods keep
fixtures tiny and deterministic; the shipped config uses 12/48.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.momentum_crypto_ema_cross import MomentumCryptoEmaCrossStrategy

SYMBOL = "BTC/USD"


def test_no_signal_before_warmup() -> None:
    strategy = MomentumCryptoEmaCrossStrategy()
    bars = _bars([100.0, 101.0, 102.0])  # len 3 <= slow_ema_period (4)
    decision = strategy.evaluate(bars, _context(positions={}, equity=100_000.0, bars=bars))
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_buy_when_fast_above_slow_and_flat() -> None:
    strategy = MomentumCryptoEmaCrossStrategy()
    bars = _bars([100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0])  # rising -> fast>slow
    decision = strategy.evaluate(bars, _context(positions={}, equity=100_000.0, bars=bars))
    assert len(decision.intents) == 1
    assert decision.intents[0].side is OrderSide.BUY
    assert decision.intents[0].symbol == SYMBOL
    assert decision.reasoning.rule == "momentum.crypto.ema_cross.entry"


def test_buy_quantity_is_fractional() -> None:
    strategy = MomentumCryptoEmaCrossStrategy()
    # latest close 160 -> 10% of 100k / 160 = 62.5 units; pick a price giving <1 unit.
    bars = _bars([40_000.0, 44_000.0, 48_000.0, 52_000.0, 56_000.0, 60_000.0, 64_000.0])
    decision = strategy.evaluate(bars, _context(positions={}, equity=100_000.0, bars=bars))
    qty = decision.intents[0].quantity
    assert 0 < qty < 1  # 10_000 / 64_000 ≈ 0.15625, NOT floored to 0
    assert isinstance(qty, float)


def test_no_duplicate_buy_when_already_long() -> None:
    strategy = MomentumCryptoEmaCrossStrategy()
    bars = _bars([100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0])
    decision = strategy.evaluate(
        bars,
        _context(
            positions={SYMBOL: 0.2},
            equity=100_000.0,
            bars=bars,
            entry_state={SYMBOL: {"entry_price": 100.0, "held_days": 0}},
        ),
    )
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_sell_when_fast_below_slow_and_long() -> None:
    strategy = MomentumCryptoEmaCrossStrategy()
    bars = _bars([160.0, 150.0, 140.0, 130.0, 120.0, 110.0, 100.0])  # falling -> fast<slow
    decision = strategy.evaluate(
        bars,
        _context(
            positions={SYMBOL: 0.2},
            equity=100_000.0,
            bars=bars,
            # entry at the latest close so the stop (5% below) does NOT fire —
            # isolates the trend-invalidation exit from the stop-loss exit.
            entry_state={SYMBOL: {"entry_price": 100.0, "held_days": 0}},
        ),
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].side is OrderSide.SELL
    assert decision.intents[0].quantity == 0.2  # exits the full position
    assert decision.reasoning.rule == "momentum.crypto.ema_cross.cross_down"


def test_stop_loss_takes_priority_over_trend() -> None:
    strategy = MomentumCryptoEmaCrossStrategy()
    # Rising path (fast>slow, no cross-down) but latest close is below the stop.
    bars = _bars([80.0, 82.0, 84.0, 86.0, 88.0, 90.0, 94.0])
    decision = strategy.evaluate(
        bars,
        _context(
            positions={SYMBOL: 0.2},
            equity=100_000.0,
            bars=bars,
            entry_state={SYMBOL: {"entry_price": 100.0, "held_days": 0}},  # 94 <= 100*0.95
        ),
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].side is OrderSide.SELL
    assert decision.reasoning.rule == "momentum.crypto.ema_cross.stop_loss"


def test_never_shorts_when_flat() -> None:
    strategy = MomentumCryptoEmaCrossStrategy()
    bars = _bars([160.0, 150.0, 140.0, 130.0, 120.0, 110.0, 100.0])  # fast<slow but flat
    decision = strategy.evaluate(bars, _context(positions={}, equity=100_000.0, bars=bars))
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(
    *,
    positions: dict[str, float],
    equity: float,
    bars: BarSet,
    entry_state: dict[str, dict[str, Any]] | None = None,
    override_parameters: dict[str, Any] | None = None,
) -> StrategyContext:
    parameters: dict[str, Any] = {
        "fast_ema_period": 2,
        "slow_ema_period": 4,
        "stop_loss_pct": 0.05,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)
    return StrategyContext(
        strategy_id="momentum.crypto.ema_cross.btc_usd_1h.v1",
        family="momentum",
        template="crypto.ema_cross",
        variant="btc_usd_1h",
        version=1,
        config_hash="test_hash",
        parameters=parameters,
        universe=(SYMBOL,),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol={SYMBOL: bars},
        entry_state=entry_state or {},
    )


def _bars(closes: list[float]) -> BarSet:
    """Build a continuous hourly UTC BarSet from a close series."""
    start = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    records: list[dict[str, Any]] = []
    for i, c in enumerate(closes):
        records.append(
            {
                "timestamp": start + pd.Timedelta(hours=i),
                "open": float(c),
                "high": float(c) + 1.0,
                "low": float(c) - 1.0,
                "close": float(c),
                "volume": 10.0,
                "vwap": float(c),
            }
        )
    return BarSet(pd.DataFrame(records))

"""Unit tests for the BTC/USD RSI(2) mean-reversion crypto canary.

Long-only, single-position, 24/7 (no session helper). Max-hold is day-granular
(``held_days``) because the engine's held-days accounting is per-outer-day by
design; small RSI periods keep fixtures deterministic. Shipped config uses
RSI(2) on 30-minute bars.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.meanrev_crypto_rsi2 import MeanrevCryptoRsi2Strategy

SYMBOL = "BTC/USD"


def test_no_signal_before_warmup() -> None:
    strategy = MeanrevCryptoRsi2Strategy()
    bars = _bars([100.0, 99.0])  # len == lookback -> RSI undefined
    decision = strategy.evaluate(bars, _context(positions={}, equity=100_000.0, bars=bars))
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_buy_when_oversold_and_flat() -> None:
    strategy = MeanrevCryptoRsi2Strategy()
    bars = _bars([100.0, 98.0, 96.0, 94.0, 92.0])  # falling -> RSI2 ~ 0 (oversold)
    decision = strategy.evaluate(bars, _context(positions={}, equity=100_000.0, bars=bars))
    assert len(decision.intents) == 1
    assert decision.intents[0].side is OrderSide.BUY
    assert decision.intents[0].symbol == SYMBOL
    assert decision.reasoning.rule == "meanrev.crypto.rsi2.entry"


def test_buy_quantity_is_fractional() -> None:
    strategy = MeanrevCryptoRsi2Strategy()
    bars = _bars([60_000.0, 58_000.0, 56_000.0, 54_000.0, 52_000.0])  # oversold, ~$52k
    decision = strategy.evaluate(bars, _context(positions={}, equity=100_000.0, bars=bars))
    qty = decision.intents[0].quantity
    assert 0 < qty < 1  # 10_000 / 52_000 ≈ 0.19, NOT floored to 0
    assert isinstance(qty, float)


def test_no_duplicate_buy_when_already_long() -> None:
    strategy = MeanrevCryptoRsi2Strategy()
    bars = _bars([100.0, 98.0, 96.0, 94.0, 92.0])  # still oversold
    decision = strategy.evaluate(
        bars,
        _context(
            positions={SYMBOL: 0.2},
            equity=100_000.0,
            bars=bars,
            entry_state={SYMBOL: {"entry_price": 92.0, "held_days": 0}},
        ),
    )
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_sell_when_rsi_normalizes() -> None:
    strategy = MeanrevCryptoRsi2Strategy()
    bars = _bars([92.0, 94.0, 96.0, 100.0, 104.0])  # rising -> RSI2 ~ 100 (>= exit 60)
    decision = strategy.evaluate(
        bars,
        _context(
            positions={SYMBOL: 0.2},
            equity=100_000.0,
            bars=bars,
            entry_state={SYMBOL: {"entry_price": 90.0, "held_days": 0}},  # 104 > stop, not stopped
        ),
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].side is OrderSide.SELL
    assert decision.reasoning.rule == "meanrev.crypto.rsi2.rsi_exit"


def test_sell_when_max_hold_expires() -> None:
    strategy = MeanrevCryptoRsi2Strategy()
    bars = _bars([100.0, 100.0, 100.0, 100.0, 100.0])  # flat -> RSI 50 (< exit, no stop)
    decision = strategy.evaluate(
        bars,
        _context(
            positions={SYMBOL: 0.2},
            equity=100_000.0,
            bars=bars,
            entry_state={SYMBOL: {"entry_price": 100.0, "held_days": 2}},  # >= max_hold_days (2)
        ),
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].side is OrderSide.SELL
    assert decision.reasoning.rule == "meanrev.crypto.rsi2.max_hold"


def test_stop_loss_takes_priority() -> None:
    strategy = MeanrevCryptoRsi2Strategy()
    bars = _bars([100.0, 98.0, 96.0, 94.0, 90.0])  # oversold AND below stop
    decision = strategy.evaluate(
        bars,
        _context(
            positions={SYMBOL: 0.2},
            equity=100_000.0,
            bars=bars,
            entry_state={SYMBOL: {"entry_price": 100.0, "held_days": 0}},  # 90 <= 100*0.95
        ),
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].side is OrderSide.SELL
    assert decision.reasoning.rule == "meanrev.crypto.rsi2.stop_loss"


def test_never_shorts_when_flat_and_overbought() -> None:
    strategy = MeanrevCryptoRsi2Strategy()
    bars = _bars([92.0, 94.0, 96.0, 100.0, 104.0])  # overbought, flat
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
        "rsi_lookback": 2,
        "rsi_entry_threshold": 10.0,
        "rsi_exit_threshold": 60.0,
        "stop_loss_pct": 0.05,
        "max_hold_days": 2,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)
    return StrategyContext(
        strategy_id="meanrev.crypto.rsi2.btc_usd_30m.v1",
        family="meanrev",
        template="crypto.rsi2",
        variant="btc_usd_30m",
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
    """Build a continuous 30-minute UTC BarSet from a close series."""
    start = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    records: list[dict[str, Any]] = []
    for i, c in enumerate(closes):
        records.append(
            {
                "timestamp": start + pd.Timedelta(minutes=30 * i),
                "open": float(c),
                "high": float(c) + 1.0,
                "low": float(c) - 1.0,
                "close": float(c),
                "volume": 10.0,
                "vwap": float(c),
            }
        )
    return BarSet(pd.DataFrame(records))

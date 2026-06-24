"""Parity safety net for the indicator-consolidation refactor.

Pins the EXACT numeric output of each indicator function that the
``feat/indicator-consolidation`` work deduplicates against fixed, deterministic
input series. The expected literals were produced by running the *original*
(pre-consolidation) functions and pasting the values back here. These tests
MUST pass before the consolidation and STILL pass bit-for-bit after it.

Equality is exact ``==`` throughout: the consolidation reroutes each scalar
local to ``series_helper(...).iloc[-1]`` and the ATR locals to a single shared
``atr`` with identical arithmetic, so the computation order is unchanged. (If a
future change forced a different reduction order, switch the affected asserts to
``pytest.approx(expected, rel=1e-12, abs=1e-12)`` and note it here.)
"""

from __future__ import annotations

import pandas as pd

# Post-consolidation the breakout ATR/EMA locals are gone; the surviving
# symbols (shared ``atr``/``ema_value`` plus the re-export aliases the strategies
# now expose) preserve identical behavior, which is exactly what these pinned
# literals continue to assert. ``shared_atr`` is the single canonical ATR that
# both breakout strategies now call; ``atr_channel_ema`` is the scalar EMA the
# atr_channel strategy now calls.
from milodex.strategies._indicators import atr as shared_atr
from milodex.strategies._indicators import ema_value as atr_channel_ema
from milodex.strategies.breakout_donchian import _atr as donchian_atr
from milodex.strategies.meanrev_rsi2_intraday import _wilder_rsi_series as intraday_rsi_series
from milodex.strategies.meanrev_rsi2_pullback import _wilder_rsi as pullback_rsi_scalar

# A deterministic 40-bar close path: enough history for lookback 14, with both
# up and down moves so the RSI/ATR are not degenerate.
CLOSES = pd.Series(
    [
        100.0, 101.0, 102.0, 101.5, 103.0, 104.0, 103.5, 105.0, 106.0, 105.0,
        107.0, 108.0, 107.5, 109.0, 110.0, 108.0, 109.5, 111.0, 112.0, 110.0,
        111.5, 113.0, 114.0, 112.0, 113.5, 115.0, 116.0, 114.0, 115.5, 117.0,
        118.0, 116.0, 117.5, 119.0, 120.0, 118.0, 119.5, 121.0, 122.0, 120.0,
    ]
)
HIGHS = CLOSES + 1.0
LOWS = CLOSES - 1.0
FRAME = pd.DataFrame({"high": HIGHS, "low": LOWS, "close": CLOSES})


# ---------------------------------------------------------------------------
# 1. meanrev_rsi2_intraday._wilder_rsi_series  (SERIES)
# ---------------------------------------------------------------------------


def test_intraday_rsi_series_parity() -> None:
    rsi = intraday_rsi_series(CLOSES, 2)
    assert len(rsi) == len(CLOSES)
    # First ``lookback`` entries are undefined.
    assert pd.isna(rsi.iloc[0])
    assert pd.isna(rsi.iloc[1])
    # index 2 is pure gains -> RSI 100.
    assert float(rsi.iloc[2]) == 100.0
    # Last three values pinned exactly.
    assert float(rsi.iloc[-3]) == 82.60869584053732
    assert float(rsi.iloc[-2]) == 89.47368438004
    assert float(rsi.iloc[-1]) == 34.693877445012944


def test_intraday_rsi_series_short_all_nan() -> None:
    # n <= lookback -> all-NaN series of the input length.
    rsi = intraday_rsi_series(pd.Series([1.0, 2.0]), 5)
    assert len(rsi) == 2
    assert bool(rsi.isna().all())


# ---------------------------------------------------------------------------
# 2. meanrev_rsi2_pullback._wilder_rsi  (SCALAR -> float | None)
# ---------------------------------------------------------------------------


def test_pullback_rsi_scalar_parity() -> None:
    assert pullback_rsi_scalar(CLOSES, 2) == 34.693877445012944
    assert pullback_rsi_scalar(CLOSES, 14) == 64.41414615597408


def test_pullback_rsi_scalar_insufficient_history_none() -> None:
    assert pullback_rsi_scalar(pd.Series([1.0, 2.0]), 5) is None


def test_pullback_rsi_scalar_all_gains_is_100() -> None:
    assert pullback_rsi_scalar(pd.Series([10.0, 11.0, 12.0, 13.0, 14.0]), 2) == 100.0


def test_pullback_rsi_scalar_flat_is_50() -> None:
    assert pullback_rsi_scalar(pd.Series([5.0, 5.0, 5.0, 5.0, 5.0]), 2) == 50.0


# ---------------------------------------------------------------------------
# 3. breakout_atr_channel._ema  (SCALAR -> float, last EMA value)
# ---------------------------------------------------------------------------


def test_atr_channel_ema_scalar_parity() -> None:
    assert atr_channel_ema(CLOSES, 5) == 120.18461622955053
    assert atr_channel_ema(CLOSES, 20) == 116.62807338179533


# ---------------------------------------------------------------------------
# 4. atr_channel ATR (formerly frame-shaped local; now the call site extracts
#    high/low/close series and calls the shared ``atr``). Same pinned literals.
# ---------------------------------------------------------------------------


def _atr_channel_atr(frame: pd.DataFrame, lookback: int) -> float | None:
    """Reproduce the atr_channel call site: extract series, call shared ATR."""
    return shared_atr(frame["high"], frame["low"], frame["close"], lookback)


def test_atr_channel_atr_parity() -> None:
    assert _atr_channel_atr(FRAME, 14) == 2.5
    assert _atr_channel_atr(FRAME, 5) == 2.6


def test_atr_channel_atr_boundary_and_none() -> None:
    # len == lookback + 1 -> one full window.
    fb = FRAME.iloc[:6].reset_index(drop=True)
    assert _atr_channel_atr(fb, 5) == 2.1
    # len == lookback -> too short -> None.
    assert _atr_channel_atr(FRAME.iloc[:5], 5) is None


# ---------------------------------------------------------------------------
# 5. breakout_donchian._atr  (SERIES-shaped -> float | None)
# ---------------------------------------------------------------------------


def test_donchian_atr_parity() -> None:
    assert donchian_atr(HIGHS, LOWS, CLOSES, 14) == 2.5
    assert donchian_atr(HIGHS, LOWS, CLOSES, 5) == 2.6


def test_donchian_atr_boundary_and_none() -> None:
    assert donchian_atr(HIGHS.iloc[:6], LOWS.iloc[:6], CLOSES.iloc[:6], 5) == 2.1
    assert donchian_atr(HIGHS.iloc[:5], LOWS.iloc[:5], CLOSES.iloc[:5], 5) is None


# ---------------------------------------------------------------------------
# Cross-check: both breakout ATR call patterns agree on shared inputs (they
# consolidate to one canonical ``atr``), and the scalar RSI equals the series'
# last value.
# ---------------------------------------------------------------------------


def test_atr_call_sites_agree_on_shared_input() -> None:
    for lookback in (5, 14):
        assert _atr_channel_atr(FRAME, lookback) == donchian_atr(HIGHS, LOWS, CLOSES, lookback)


def test_scalar_rsi_equals_series_last() -> None:
    for lookback in (2, 5, 14):
        series_last = float(intraday_rsi_series(CLOSES, lookback).iloc[-1])
        assert pullback_rsi_scalar(CLOSES, lookback) == series_last

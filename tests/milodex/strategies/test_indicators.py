"""Tests for shared strategy indicator helpers (EMA, Wilder RSI)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from milodex.strategies._indicators import ema_series, wilder_rsi_series


def test_ema_of_constant_series_is_constant() -> None:
    closes = pd.Series([100.0] * 10)
    ema = ema_series(closes, span=4)
    assert ema.iloc[-1] == 100.0


def test_ema_matches_adjust_false_recursion() -> None:
    """EMA(span=2, adjust=False) of [1,2,3]: 1 -> 1.6667 -> 2.5556."""
    closes = pd.Series([1.0, 2.0, 3.0])
    ema = ema_series(closes, span=2)
    assert ema.iloc[0] == 1.0
    assert np.isclose(ema.iloc[1], 1.6666667)
    assert np.isclose(ema.iloc[2], 2.5555556)


def test_rsi_all_nan_before_warmup() -> None:
    closes = pd.Series([10.0, 11.0])  # len == lookback
    rsi = wilder_rsi_series(closes, lookback=2)
    assert rsi.isna().all()


def test_rsi_is_100_for_monotonic_increasing() -> None:
    closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    rsi = wilder_rsi_series(closes, lookback=2)
    assert rsi.iloc[-1] == 100.0


def test_rsi_is_0_for_monotonic_decreasing() -> None:
    closes = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0])
    rsi = wilder_rsi_series(closes, lookback=2)
    assert rsi.iloc[-1] == 0.0


def test_rsi_low_at_trough_high_after_recovery() -> None:
    """A V-shaped path prints oversold RSI at the trough, overbought after recovery."""
    closes = pd.Series([100.0, 98.0, 96.0, 94.0, 92.0, 96.0, 100.0])
    rsi = wilder_rsi_series(closes, lookback=2)
    trough_rsi = float(rsi.iloc[4])  # bottom of the V
    recovered_rsi = float(rsi.iloc[-1])
    assert trough_rsi < 20.0
    assert recovered_rsi > 60.0

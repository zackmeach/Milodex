"""Pure cross-sectional feature kit shared by the non-rule deciders.

These re-express features the existing *rule* families already compute —
trailing momentum return (``momentum`` family), Wilder RSI (``meanrev``
family), distance-from-moving-average, and realized volatility — as small
pure functions over a daily close-price series. **No new data source:** every
feature is derived from the cached OHLCV bars the rule strategies already
consume.

The kit is shared by the scored-linear decider and the tree-bucketed decider
so that the *decision paradigm* (continuous weighted score vs. discrete
bucketed traversal) is the only thing that differs between them — the inputs
are identical. This is the seam proof's "hold every other axis fixed" rule
applied at the feature level.

Determinism: each function is a pure transform of its input series; given the
same closes it returns the same value. ``cross_sectional_zscore`` sorts only
implicitly through dict iteration order, which the callers control with a
stable symbol ordering.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

from milodex.strategies._indicators import wilder_rsi as _wilder_rsi


def trailing_return(closes: pd.Series, lookback: int) -> float | None:
    """Return the simple trailing return over ``lookback`` bars, or ``None``.

    ``None`` when there is insufficient history or the reference price is
    non-positive (a degenerate series we refuse to divide by).
    """
    if lookback < 1 or len(closes) < lookback + 1:
        return None
    reference = float(closes.iloc[-1 - lookback])
    if reference <= 0:
        return None
    return float(closes.iloc[-1]) / reference - 1.0


def wilder_rsi(closes: pd.Series, lookback: int) -> float | None:
    """Return Wilder-smoothed RSI with period ``lookback``, or ``None``.

    Delegates to the shared :func:`milodex.strategies._indicators.wilder_rsi`
    so the deciders and the ``meanrev`` family compute one identical RSI. The
    extra ``lookback < 1`` guard preserves this kit's stricter contract (the
    shared helper assumes ``lookback >= 1``).
    """
    if lookback < 1:
        return None
    return _wilder_rsi(closes, lookback)


def ma_distance(closes: pd.Series, length: int) -> float | None:
    """Return ``close / SMA(length) - 1`` (fractional distance), or ``None``."""
    if length < 1 or len(closes) < length:
        return None
    sma = float(closes.tail(length).mean())
    if sma <= 0:
        return None
    return float(closes.iloc[-1]) / sma - 1.0


def realized_vol(closes: pd.Series, lookback: int) -> float | None:
    """Return the realized volatility (population std of simple daily returns)
    over the trailing ``lookback`` bars, or ``None`` when history is short."""
    if lookback < 2 or len(closes) < lookback + 1:
        return None
    returns = closes.pct_change().dropna()
    if len(returns) < lookback:
        return None
    window = returns.tail(lookback)
    return float(window.std(ddof=0))


def cross_sectional_zscore(values: Mapping[str, float]) -> dict[str, float]:
    """Return the population z-score of each value across the cross section.

    ``{symbol: (value - mean) / std}``. When the cross-sectional standard
    deviation is zero (every symbol identical) every z-score is ``0.0`` — a
    neutral contribution rather than a divide-by-zero. This is what turns a
    raw feature into a *comparable* component of a linear score.
    """
    if not values:
        return {}
    xs = list(values.values())
    n = len(xs)
    mean = sum(xs) / n
    variance = sum((x - mean) ** 2 for x in xs) / n
    std = math.sqrt(variance)
    if std == 0.0:
        return {key: 0.0 for key in values}
    return {key: (value - mean) / std for key, value in values.items()}

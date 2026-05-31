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

    Identical formula to the ``meanrev`` family's RSI (see
    ``meanrev_rsi2_pullback._wilder_rsi``) — re-stated here so the deciders
    do not import a sibling strategy's private helper.
    """
    if lookback < 1 or len(closes) <= lookback:
        return None

    deltas = closes.diff().dropna()
    if len(deltas) < lookback:
        return None

    gains = deltas.clip(lower=0.0)
    losses = (-deltas).clip(lower=0.0)

    avg_gain = float(gains.iloc[:lookback].mean())
    avg_loss = float(losses.iloc[:lookback].mean())
    for gain, loss in zip(gains.iloc[lookback:], losses.iloc[lookback:], strict=False):
        avg_gain = (avg_gain * (lookback - 1) + float(gain)) / lookback
        avg_loss = (avg_loss * (lookback - 1) + float(loss)) / lookback

    if avg_loss == 0.0:
        return 50.0 if avg_gain == 0.0 else 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


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

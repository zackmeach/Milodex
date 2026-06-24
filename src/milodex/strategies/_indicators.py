"""Shared, session-agnostic price indicators for strategies.

Unlike :mod:`milodex.strategies._session_intraday` (which encodes US equity
cash-session concepts), these helpers operate on a plain close series with no
calendar assumptions — suitable for 24/7 markets (crypto) as well as equities.

``wilder_rsi_series`` reproduces the Wilder-smoothed RSI used by the existing
``meanrev_rsi2_*`` strategies (seed = simple mean of the first ``lookback``
gains/losses, then the Wilder recursion). The equity strategies now consume
these shared helpers too (the previously-deferred cleanup is done): the scalar
``wilder_rsi`` / ``ema_value`` accessors return the last series value with the
same insufficient-history semantics the old per-strategy locals had, and
``atr`` is the single Average True Range used by both breakout strategies.
"""

from __future__ import annotations

import pandas as pd


def ema_series(closes: pd.Series, span: int) -> pd.Series:
    """Return the exponential moving average of ``closes`` (``adjust=False``).

    ``adjust=False`` gives the standard recursive EMA used for trend signals:
    ``ema[0] = closes[0]`` and ``ema[i] = ema[i-1] + alpha*(closes[i]-ema[i-1])``
    with ``alpha = 2/(span+1)``. Aligned index-for-index with ``closes``.
    """
    return closes.astype(float).ewm(span=span, adjust=False).mean()


def wilder_rsi_series(closes: pd.Series, lookback: int) -> pd.Series:
    """Return a Wilder-smoothed RSI series aligned to ``closes``.

    Seed is the simple mean of the first ``lookback`` gains/losses (indices
    ``1..lookback``; index 0's delta is undefined), then the Wilder recursion.
    Entries before enough history exist are ``NaN``. Vectorised into a single
    O(n) pass.
    """
    closes = closes.astype(float)
    n = len(closes)
    result = pd.Series([float("nan")] * n, index=closes.index, dtype=float)
    if n <= lookback:
        return result

    deltas = closes.diff()
    gains = deltas.clip(lower=0.0).to_numpy()
    losses = (-deltas).clip(lower=0.0).to_numpy()

    avg_gain = float(gains[1 : lookback + 1].mean())
    avg_loss = float(losses[1 : lookback + 1].mean())
    result.iloc[lookback] = _rsi_from(avg_gain, avg_loss)
    for i in range(lookback + 1, n):
        avg_gain = (avg_gain * (lookback - 1) + float(gains[i])) / lookback
        avg_loss = (avg_loss * (lookback - 1) + float(losses[i])) / lookback
        result.iloc[i] = _rsi_from(avg_gain, avg_loss)
    return result


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 50.0 if avg_gain == 0.0 else 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def ema_value(closes: pd.Series, span: int) -> float:
    """Return the last EMA value (scalar accessor over :func:`ema_series`)."""
    return float(ema_series(closes, span).iloc[-1])


def wilder_rsi(closes: pd.Series, lookback: int) -> float | None:
    """Return the latest Wilder RSI value, or ``None`` on insufficient history.

    Thin scalar accessor over :func:`wilder_rsi_series`: returns the final
    series value, or ``None`` when the series has no defined value yet (i.e.
    ``len(closes) <= lookback``). Bit-for-bit equal to the per-strategy scalar
    locals it replaces.
    """
    if len(closes) <= lookback:
        return None
    last = wilder_rsi_series(closes, lookback).iloc[-1]
    return None if pd.isna(last) else float(last)


def atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, lookback: int) -> float | None:
    """Return Average True Range over ``lookback`` bars (simple-MA flavour).

    True Range = max(high - low, |high - prior_close|, |low - prior_close|).
    The first TR is undefined (no prior close) and dropped; the window is the
    last ``lookback`` defined TRs. Returns ``None`` when the history is too
    short to form one full window. Wilder-smoothed ATR would be a new family
    version (ADR 0015), not a parameter knob.
    """
    highs = highs.astype(float)
    lows = lows.astype(float)
    closes = closes.astype(float)
    if len(closes) < lookback + 1:
        return None
    prior_close = closes.shift(1)
    tr = pd.concat(
        [
            (highs - lows),
            (highs - prior_close).abs(),
            (lows - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr = tr.dropna()
    if len(tr) < lookback:
        return None
    return float(tr.tail(lookback).mean())

"""Shared, session-agnostic price indicators for strategies.

Unlike :mod:`milodex.strategies._session_intraday` (which encodes US equity
cash-session concepts), these helpers operate on a plain close series with no
calendar assumptions — suitable for 24/7 markets (crypto) as well as equities.

``wilder_rsi_series`` reproduces the Wilder-smoothed RSI used by the existing
``meanrev_rsi2_*`` strategies (seed = simple mean of the first ``lookback``
gains/losses, then the Wilder recursion). It is duplicated here rather than
imported from a strategy module so the new crypto canaries do not depend on a
sibling strategy's private function; adopting this shared helper in the
existing equity strategies is a deliberate, out-of-scope future cleanup.
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

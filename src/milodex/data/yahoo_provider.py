"""Yahoo Finance data provider — narrow-purpose VIX fetcher.

This module exists solely to fetch ^VIX history from Yahoo Finance.  Alpaca's
free tier does not supply VIX (it is a CBOE volatility index, not an equity),
so a dedicated Yahoo path is the only viable free option.

**Scope is intentionally minimal.**  Do not extend this module to fetch arbitrary
symbols from Yahoo — the Alpaca provider is the canonical bar source for
equities.  This module is a surgical supplement, not a second general provider.

Usage::

    from datetime import date
    from milodex.data.yahoo_provider import fetch_vix_history

    df = fetch_vix_history(start=date(2024, 1, 1), end=date.today())
    # df has columns: timestamp, open, high, low, close, volume, vwap

On any network or parse error the function returns an empty DataFrame and logs a
WARNING.  This keeps a Yahoo outage from breaking the broader cache refresh.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import yfinance

_logger = logging.getLogger(__name__)

# Yahoo Finance ticker for CBOE VIX
_VIX_TICKER = "^VIX"


def fetch_vix_history(start: date, end: date) -> pd.DataFrame:
    """Fetch daily VIX bars from Yahoo Finance for [start, end].

    Returns a DataFrame with columns:
        timestamp  — datetime64[ns, UTC]
        open       — float64
        high       — float64
        low        — float64
        close      — float64
        volume     — int64
        vwap       — float64 (always NaN; VIX has no volume-weighted price)

    The returned schema matches the ParquetCache / BarSet contract so the result
    can be passed directly to ``ParquetCache.write`` or ``ParquetCache.merge``.

    On any exception (network error, parse error, empty result from Yahoo) the
    function logs a WARNING and returns an empty DataFrame with the same schema.
    Callers should check ``df.empty`` before trusting the result.

    Args:
        start: First date to include (inclusive).
        end:   Last date to include (inclusive).

    Returns:
        DataFrame matching the ParquetCache bar schema, possibly empty.
    """
    empty = _empty_vix_df()
    try:
        ticker = yfinance.Ticker(_VIX_TICKER)
        # auto_adjust=False keeps raw OHLC; interval="1d" for daily bars.
        # We add 1 day to `end` because yfinance treats the end as exclusive.
        raw = ticker.history(
            start=start.isoformat(),
            end=(
                date(end.year, end.month, end.day)
                .__add__(timedelta(days=1))
                .isoformat()
            ),
            interval="1d",
            auto_adjust=False,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("fetch_vix_history: failed to fetch from Yahoo Finance: %s", exc)
        return empty

    if raw is None or raw.empty:
        _logger.warning(
            "fetch_vix_history: Yahoo returned no data for %s between %s and %s",
            _VIX_TICKER,
            start,
            end,
        )
        return empty

    try:
        return _reshape(raw)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("fetch_vix_history: failed to reshape Yahoo data: %s", exc)
        return empty


def _empty_vix_df() -> pd.DataFrame:
    """Return an empty DataFrame with the canonical bar schema."""
    return pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"]
    ).astype(
        {
            "timestamp": "datetime64[ns, UTC]",
            "open": "float64",
            "high": "float64",
            "low": "float64",
            "close": "float64",
            "volume": "int64",
            "vwap": "float64",
        }
    )


def _reshape(raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape a yfinance history DataFrame into the ParquetCache bar schema.

    yfinance returns a DatetimeIndex with columns Open/High/Low/Close/Volume
    (title-case).  This function normalises column names, ensures UTC timestamps,
    adds a vwap column (NaN — VIX has no VWAP), and returns a copy with a plain
    RangeIndex.

    Raises:
        KeyError: if expected columns are missing from ``raw``.
        ValueError: if the timestamp column cannot be coerced to UTC datetime.
    """
    df = raw.copy()

    # The index is the date/timestamp; move it to a regular column FIRST so
    # the index name (e.g. "Date", "Datetime") lands in df.columns before we
    # normalise to lower-case.  If we lowercased before reset_index, the index
    # name would retain its original case (e.g. "Date") and our lower-case
    # candidate search would miss it.
    df = df.reset_index()

    # Normalise column names to lower-case to cover both title-case ("Open")
    # and lower-case ("open") yfinance variants.
    df.columns = [c.lower() for c in df.columns]

    # Find the timestamp column — yfinance names it "date" or "datetime".
    # Do NOT include "index" here: a plain RangeIndex reset produces a
    # numeric "index" column, which is not a valid timestamp source.
    ts_col = None
    for candidate in ("date", "datetime"):
        if candidate in df.columns:
            ts_col = candidate
            break
    if ts_col is None:
        raise KeyError(
            f"fetch_vix_history._reshape: could not find timestamp column in {list(df.columns)}"
        )

    # Coerce to UTC-aware datetime.
    ts = pd.to_datetime(df[ts_col], utc=True)

    result = pd.DataFrame(
        {
            "timestamp": ts,
            "open": pd.to_numeric(df["open"], errors="coerce").astype("float64"),
            "high": pd.to_numeric(df["high"], errors="coerce").astype("float64"),
            "low": pd.to_numeric(df["low"], errors="coerce").astype("float64"),
            "close": pd.to_numeric(df["close"], errors="coerce").astype("float64"),
            "volume": pd.to_numeric(df.get("volume", 0), errors="coerce")
            .fillna(0)
            .astype("int64"),
            "vwap": float("nan"),
        }
    )

    # Drop rows where close is NaN (yfinance sometimes returns extra blank rows).
    result = result.dropna(subset=["close"]).reset_index(drop=True)
    return result

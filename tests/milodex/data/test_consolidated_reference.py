"""IEX price-fidelity gate: consolidated daily cross-check."""

from __future__ import annotations

from datetime import date

import pandas as pd

from milodex.data.consolidated_reference import (
    cross_check_session_extremes,
    fetch_daily_ohlc,
)
from milodex.data.models import BarSet


def _intraday_session(day: str, *, high: float, low: float, close: float, n: int = 78) -> BarSet:
    """A regular 5-min session whose overall high/low/last-close are set."""
    start = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    rows = []
    for i in range(n):
        ts = (start + pd.Timedelta(minutes=5 * i)).tz_convert("UTC")
        bar_high = high if i == 5 else (low + high) / 2
        bar_low = low if i == 7 else (low + high) / 2
        bar_close = close if i == n - 1 else (low + high) / 2
        rows.append(
            {
                "timestamp": ts,
                "open": (low + high) / 2,
                "high": bar_high,
                "low": bar_low,
                "close": bar_close,
                "volume": 1000.0,
                "vwap": (low + high) / 2,
            }
        )
    return BarSet(pd.DataFrame(rows))


def _daily_ref(day: str, *, high: float, low: float, close: float) -> pd.DataFrame:
    # Daily bars are date-labelled at midnight UTC; the bar's calendar date IS its
    # session date (no ET conversion — a daily bar is not a point-in-time).
    return pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(day, tz="UTC"),
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000_000,
                "vwap": float("nan"),
            }
        ]
    )


def test_inward_bias_flagged():
    # IEX normalized range (1/100 = 0.01) is a quarter of consolidated (4/100 = 0.04).
    intraday = {"SPY": _intraday_session("2025-06-17", high=100.5, low=99.5, close=100.0)}
    reference = {"SPY": _daily_ref("2025-06-17", high=102.0, low=98.0, close=100.0)}
    issues = cross_check_session_extremes(intraday, reference, min_sessions=1)
    assert any(i.code == "iex_inward_price_bias" for i in issues)


def test_matching_range_no_flag():
    intraday = {"SPY": _intraday_session("2025-06-17", high=102.0, low=98.0, close=100.0)}
    reference = {"SPY": _daily_ref("2025-06-17", high=102.0, low=98.0, close=100.0)}
    issues = cross_check_session_extremes(intraday, reference, min_sessions=1)
    assert not issues


def test_adjustment_offset_does_not_false_positive():
    # The original blocker: Alpaca-adjusted intraday vs Yahoo-raw daily differ by a
    # ~level factor. Same SHAPE (normalized range), different price level => NO flag.
    intraday = {"SPY": _intraday_session("2025-06-17", high=100.5, low=99.5, close=100.0)}
    reference = {"SPY": _daily_ref("2025-06-17", high=150.75, low=149.25, close=150.0)}
    issues = cross_check_session_extremes(intraday, reference, min_sessions=1)
    assert not issues  # ratio 1.0 despite a 1.5x level offset


def test_min_sessions_suppresses_short_window():
    intraday = {"SPY": _intraday_session("2025-06-17", high=100.5, low=99.5, close=100.0)}
    reference = {"SPY": _daily_ref("2025-06-17", high=102.0, low=98.0, close=100.0)}
    # Only 1 overlapping session but min_sessions=5 -> no flag (documented inertness).
    issues = cross_check_session_extremes(intraday, reference, min_sessions=5)
    assert not issues


def test_no_reference_no_flag():
    intraday = {"SPY": _intraday_session("2025-06-17", high=100.5, low=99.5, close=100.0)}
    issues = cross_check_session_extremes(intraday, {}, min_sessions=1)
    assert not issues


def test_fetch_daily_ohlc_reshapes_monkeypatched_yfinance(monkeypatch):
    import yfinance

    class _StubTicker:
        def __init__(self, symbol):
            self._symbol = symbol

        def history(self, **kwargs):
            # Real yfinance daily history names its index "Date".
            idx = pd.DatetimeIndex(pd.to_datetime(["2025-06-16", "2025-06-17"]), name="Date")
            return pd.DataFrame(
                {
                    "Open": [100.0, 101.0],
                    "High": [102.0, 103.0],
                    "Low": [99.0, 100.0],
                    "Close": [101.0, 102.0],
                    "Volume": [1_000_000, 1_100_000],
                },
                index=idx,
            )

    monkeypatch.setattr(yfinance, "Ticker", _StubTicker)
    df = fetch_daily_ohlc("SPY", date(2025, 6, 16), date(2025, 6, 17))
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "vwap"]
    assert len(df) == 2
    assert df["high"].tolist() == [102.0, 103.0]


def test_fetch_daily_ohlc_empty_on_error(monkeypatch):
    import yfinance

    def _boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr(yfinance, "Ticker", _boom)
    df = fetch_daily_ohlc("SPY", date(2025, 6, 16), date(2025, 6, 17))
    assert df.empty

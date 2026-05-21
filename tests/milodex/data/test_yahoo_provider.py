"""Unit tests for milodex.data.yahoo_provider.

All tests use MagicMock to patch yfinance.Ticker — no live network calls.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from milodex.data.yahoo_provider import _empty_vix_df, _reshape, fetch_vix_history

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_yfinance_df(
    dates: list[str],
    closes: list[float],
) -> pd.DataFrame:
    """Build a minimal yfinance-shaped DataFrame for testing _reshape / fetch_vix_history.

    yfinance returns a DatetimeIndex named "Date" (tz-aware or tz-naive) with
    title-case columns Open/High/Low/Close/Volume.  We set the index name to
    "Date" to match real yfinance output so _reshape can find the timestamp col.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(dates, utc=True), name="Date")
    n = len(dates)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [0] * n,
            "Dividends": [0.0] * n,
            "Stock Splits": [0.0] * n,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# _reshape tests
# ---------------------------------------------------------------------------


class TestReshape:
    def test_returns_expected_columns(self):
        raw = _make_yfinance_df(["2025-01-15", "2025-01-16"], [18.5, 19.0])
        result = _reshape(raw)
        expected = ["timestamp", "open", "high", "low", "close", "volume", "vwap"]
        assert list(result.columns) == expected

    def test_timestamp_is_utc(self):
        raw = _make_yfinance_df(["2025-01-15"], [18.5])
        result = _reshape(raw)
        # Accept both ns and us resolution — newer pandas (2.x+) uses us by default
        assert result["timestamp"].dtype.tz is not None, "timestamp must be tz-aware"
        assert str(result["timestamp"].dtype.tz) == "UTC"

    def test_close_values_preserved(self):
        raw = _make_yfinance_df(["2025-01-15", "2025-01-16"], [18.5, 19.0])
        result = _reshape(raw)
        assert list(result["close"]) == pytest.approx([18.5, 19.0])

    def test_volume_is_int64(self):
        raw = _make_yfinance_df(["2025-01-15"], [18.5])
        result = _reshape(raw)
        assert result["volume"].dtype == "int64"

    def test_vwap_is_nan(self):
        """VIX has no volume-weighted price — vwap is always NaN."""
        raw = _make_yfinance_df(["2025-01-15"], [18.5])
        result = _reshape(raw)
        assert pd.isna(result["vwap"].iloc[0])

    def test_drops_nan_close_rows(self):
        raw = _make_yfinance_df(["2025-01-15", "2025-01-16"], [18.5, 19.0])
        # Inject a NaN close
        raw.loc[raw.index[1], "Close"] = float("nan")
        result = _reshape(raw)
        assert len(result) == 1
        assert result["close"].iloc[0] == pytest.approx(18.5)

    def test_handles_lowercase_columns(self):
        """yfinance sometimes returns lower-case column names."""
        raw = _make_yfinance_df(["2025-01-15"], [18.5])
        raw.columns = [c.lower() for c in raw.columns]
        result = _reshape(raw)
        assert "close" in result.columns
        assert result["close"].iloc[0] == pytest.approx(18.5)

    def test_raises_on_missing_timestamp_column(self):
        # DataFrame with a plain integer RangeIndex (no date/datetime column) and
        # all OHLCV columns present.  After reset_index() the index becomes a
        # numeric column named "index" — none of our timestamp candidates match.
        raw = pd.DataFrame(
            {
                "Open": [18.5],
                "High": [19.5],
                "Low": [17.5],
                "Close": [18.5],
                "Volume": [0],
            }
        )
        with pytest.raises(KeyError, match="timestamp column"):
            _reshape(raw)

    def test_row_count_matches_input(self):
        raw = _make_yfinance_df(
            ["2025-01-13", "2025-01-14", "2025-01-15"],
            [17.0, 18.0, 19.0],
        )
        result = _reshape(raw)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# fetch_vix_history tests
# ---------------------------------------------------------------------------


class TestFetchVixHistory:
    def _patch_ticker(self, df: pd.DataFrame | None = None, *, raise_exc: Exception | None = None):
        """Return a context-manager that patches yfinance.Ticker."""
        mock_ticker = MagicMock()
        if raise_exc is not None:
            mock_ticker.history.side_effect = raise_exc
        else:
            mock_ticker.history.return_value = df if df is not None else pd.DataFrame()
        return patch("milodex.data.yahoo_provider.yfinance.Ticker", return_value=mock_ticker)

    def test_returns_dataframe_on_success(self):
        raw = _make_yfinance_df(["2025-01-15", "2025-01-16"], [18.5, 19.0])
        with self._patch_ticker(raw):
            result = fetch_vix_history(date(2025, 1, 15), date(2025, 1, 16))
        assert not result.empty
        assert list(result.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "vwap",
        ]

    def test_returns_empty_on_network_error(self):
        with self._patch_ticker(raise_exc=ConnectionError("no network")):
            result = fetch_vix_history(date(2025, 1, 15), date(2025, 1, 16))
        assert result.empty

    def test_returns_empty_when_yahoo_returns_empty_df(self):
        with self._patch_ticker(pd.DataFrame()):
            result = fetch_vix_history(date(2025, 1, 15), date(2025, 1, 16))
        assert result.empty

    def test_returns_empty_on_none_from_ticker(self):
        with self._patch_ticker(None):
            result = fetch_vix_history(date(2025, 1, 15), date(2025, 1, 16))
        assert result.empty

    def test_passes_correct_ticker_symbol(self):
        raw = _make_yfinance_df(["2025-01-15"], [18.5])
        mock_ticker_cls = MagicMock()
        mock_ticker_cls.return_value.history.return_value = raw
        with patch("milodex.data.yahoo_provider.yfinance.Ticker", mock_ticker_cls):
            fetch_vix_history(date(2025, 1, 15), date(2025, 1, 15))
        mock_ticker_cls.assert_called_once_with("^VIX")

    def test_end_date_inclusive(self):
        """yfinance end is exclusive; fetch_vix_history adds 1 day internally."""
        raw = _make_yfinance_df(["2025-01-15"], [18.5])
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = raw
        with patch("milodex.data.yahoo_provider.yfinance.Ticker", return_value=mock_ticker):
            fetch_vix_history(date(2025, 1, 15), date(2025, 1, 15))
        _, kwargs = mock_ticker.history.call_args
        # end should be 2025-01-16 (exclusive), not 2025-01-15
        assert kwargs["end"] == "2025-01-16"

    def test_close_values_round_trip(self):
        raw = _make_yfinance_df(
            ["2025-01-13", "2025-01-14", "2025-01-15"],
            [17.0, 18.0, 19.0],
        )
        with self._patch_ticker(raw):
            result = fetch_vix_history(date(2025, 1, 13), date(2025, 1, 15))
        assert list(result["close"]) == pytest.approx([17.0, 18.0, 19.0])

    def test_returns_empty_dataframe_schema_on_error(self):
        """Empty result must still have the correct column names."""
        with self._patch_ticker(raise_exc=RuntimeError("boom")):
            result = fetch_vix_history(date(2025, 1, 15), date(2025, 1, 16))
        assert result.empty
        assert list(result.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "vwap",
        ]


# ---------------------------------------------------------------------------
# _empty_vix_df shape
# ---------------------------------------------------------------------------


class TestEmptyVixDf:
    def test_column_names(self):
        df = _empty_vix_df()
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "vwap"]

    def test_is_empty(self):
        assert _empty_vix_df().empty

    def test_timestamp_dtype(self):
        df = _empty_vix_df()
        # Accept both ns and us resolution — newer pandas uses us
        assert df["timestamp"].dtype.tz is not None, "timestamp must be tz-aware"
        assert str(df["timestamp"].dtype.tz) == "UTC"

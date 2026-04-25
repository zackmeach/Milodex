"""Tests for ParquetCache."""

from datetime import date

import pandas as pd
import pytest

from milodex.data.cache import ParquetCache
from milodex.data.models import Timeframe


@pytest.fixture()
def cache_dir(tmp_path):
    return tmp_path / "market_cache"


@pytest.fixture()
def cache(cache_dir):
    return ParquetCache(cache_dir)


@pytest.fixture()
def sample_df():
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-13", "2025-01-14", "2025-01-15"], utc=True),
            "open": [148.0, 149.0, 150.0],
            "high": [149.0, 150.0, 152.0],
            "low": [147.0, 148.5, 149.5],
            "close": [148.5, 149.5, 151.0],
            "volume": [900000, 950000, 1000000],
            "vwap": [148.3, 149.2, 150.8],
        }
    )


class TestParquetCache:
    def test_creates_directory_on_init(self, cache, cache_dir):
        assert cache_dir.exists()

    def test_read_returns_none_for_empty_cache(self, cache):
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert result is None

    def test_write_and_read_roundtrip(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert result is not None
        assert len(result) == 3
        assert list(result.columns) == list(sample_df.columns)

    def test_get_cached_range_returns_none_for_empty(self, cache):
        result = cache.get_cached_range("AAPL", Timeframe.DAY_1)
        assert result is None

    def test_get_cached_range_returns_min_max_dates(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        start, end = cache.get_cached_range("AAPL", Timeframe.DAY_1)
        assert start == date(2025, 1, 13)
        assert end == date(2025, 1, 15)

    def test_merge_appends_new_data(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        new_data = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-16", "2025-01-17"], utc=True),
                "open": [151.0, 152.0],
                "high": [153.0, 154.0],
                "low": [150.5, 151.5],
                "close": [152.0, 153.0],
                "volume": [1100000, 1200000],
                "vwap": [151.5, 152.5],
            }
        )
        cache.merge("AAPL", Timeframe.DAY_1, new_data)
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert len(result) == 5

    def test_merge_deduplicates_by_timestamp(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        overlap = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-15", "2025-01-16"], utc=True),
                "open": [150.0, 151.0],
                "high": [152.0, 153.0],
                "low": [149.5, 150.5],
                "close": [151.0, 152.0],
                "volume": [1000000, 1100000],
                "vwap": [150.8, 151.5],
            }
        )
        cache.merge("AAPL", Timeframe.DAY_1, overlap)
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert len(result) == 4  # 3 original + 1 new, not 5

    def test_merge_into_empty_cache(self, cache, sample_df):
        cache.merge("AAPL", Timeframe.DAY_1, sample_df)
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert len(result) == 3

    def test_different_timeframes_are_separate(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        assert cache.read("AAPL", Timeframe.HOUR_1) is None

    def test_different_symbols_are_separate(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        assert cache.read("SPY", Timeframe.DAY_1) is None

    def test_merge_fills_gap_in_middle(self, cache):
        """Cache has Jan 13-15 and Jan 20-22. Fill Jan 16-19."""
        early = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-13", "2025-01-14", "2025-01-15"], utc=True),
                "open": [148.0, 149.0, 150.0],
                "high": [149.0, 150.0, 152.0],
                "low": [147.0, 148.5, 149.5],
                "close": [148.5, 149.5, 151.0],
                "volume": [900000, 950000, 1000000],
                "vwap": [148.3, 149.2, 150.8],
            }
        )
        late = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-20", "2025-01-21", "2025-01-22"], utc=True),
                "open": [153.0, 154.0, 155.0],
                "high": [154.0, 155.0, 156.0],
                "low": [152.0, 153.0, 154.0],
                "close": [153.5, 154.5, 155.5],
                "volume": [1100000, 1200000, 1300000],
                "vwap": [153.2, 154.2, 155.2],
            }
        )
        cache.write("AAPL", Timeframe.DAY_1, pd.concat([early, late]))
        middle = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-16", "2025-01-17"], utc=True),
                "open": [151.0, 152.0],
                "high": [152.0, 153.0],
                "low": [150.0, 151.0],
                "close": [151.5, 152.5],
                "volume": [1050000, 1100000],
                "vwap": [151.2, 152.2],
            }
        )
        cache.merge("AAPL", Timeframe.DAY_1, middle)
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert len(result) == 8  # 3 + 2 + 3
        timestamps = pd.to_datetime(result["timestamp"])
        assert timestamps.is_monotonic_increasing


def test_read_logs_cache_miss_when_file_absent(cache, caplog):
    with caplog.at_level("INFO", logger="milodex.data.cache"):
        result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is None
    assert any("cache_miss" in r.message and "AAPL" in r.message for r in caplog.records)


def test_read_logs_cache_hit_when_file_present(cache, sample_df, caplog):
    cache.write("AAPL", Timeframe.DAY_1, sample_df)
    with caplog.at_level("INFO", logger="milodex.data.cache"):
        result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None
    assert any("cache_hit" in r.message and "rows=3" in r.message for r in caplog.records)

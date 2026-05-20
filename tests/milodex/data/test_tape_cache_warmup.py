"""Integration-style tests for milodex.data.tape_cache_warmup.

Verifies that the VIX ingest flow (warmup_vix_cache) correctly writes VIX data
to the ParquetCache and handles failure paths gracefully.

All Yahoo Finance and Alpaca calls are mocked — no live network in any test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from milodex.data.cache import ParquetCache
from milodex.data.models import Timeframe
from milodex.data.tape_cache_warmup import get_vix_cache_state, warmup_vix_cache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vix_df(dates: list[str], closes: list[float]) -> pd.DataFrame:
    """Build a minimal VIX DataFrame matching the ParquetCache bar schema."""
    ts = pd.to_datetime(dates, utc=True)
    n = len(dates)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [0] * n,
            "vwap": [float("nan")] * n,
        }
    )


# ---------------------------------------------------------------------------
# warmup_vix_cache tests
# ---------------------------------------------------------------------------


class TestWarmupVixCache:
    def test_writes_vix_parquet_to_cache(self, tmp_path: Path):
        """On a successful Yahoo fetch the VIX parquet must be written."""
        vix_df = _make_vix_df(["2025-01-15", "2025-01-16"], [18.5, 19.0])

        with patch("milodex.data.tape_cache_warmup.fetch_vix_history", return_value=vix_df):
            result = warmup_vix_cache(cache_dir=tmp_path, cache_version="v3")

        assert result is True
        cache = ParquetCache(tmp_path, version="v3")
        stored = cache.read("VIX", Timeframe.DAY_1)
        assert stored is not None
        assert not stored.empty
        assert len(stored) == 2

    def test_returns_false_when_yahoo_returns_empty(self, tmp_path: Path):
        empty_df = pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"]
        )
        with patch("milodex.data.tape_cache_warmup.fetch_vix_history", return_value=empty_df):
            result = warmup_vix_cache(cache_dir=tmp_path, cache_version="v3")

        assert result is False

    def test_returns_false_on_cache_write_failure(self, tmp_path: Path):
        vix_df = _make_vix_df(["2025-01-15"], [18.5])

        with patch("milodex.data.tape_cache_warmup.fetch_vix_history", return_value=vix_df):
            with patch(
                "milodex.data.tape_cache_warmup.ParquetCache.merge",
                side_effect=OSError("disk full"),
            ):
                result = warmup_vix_cache(cache_dir=tmp_path, cache_version="v3")

        assert result is False

    def test_merges_with_existing_vix_data(self, tmp_path: Path):
        """Repeated warmup should merge, not overwrite, keeping both old and new rows."""
        cache = ParquetCache(tmp_path, version="v3")
        old_df = _make_vix_df(["2025-01-13", "2025-01-14"], [17.0, 18.0])
        cache.write("VIX", Timeframe.DAY_1, old_df)

        new_df = _make_vix_df(["2025-01-15", "2025-01-16"], [19.0, 20.0])
        with patch("milodex.data.tape_cache_warmup.fetch_vix_history", return_value=new_df):
            result = warmup_vix_cache(cache_dir=tmp_path, cache_version="v3")

        assert result is True
        stored = cache.read("VIX", Timeframe.DAY_1)
        assert stored is not None
        assert len(stored) == 4

    def test_vix_parquet_schema_matches_barset_contract(self, tmp_path: Path):
        """Written VIX parquet must have all required ParquetCache columns."""
        vix_df = _make_vix_df(["2025-01-15"], [18.5])
        with patch("milodex.data.tape_cache_warmup.fetch_vix_history", return_value=vix_df):
            warmup_vix_cache(cache_dir=tmp_path, cache_version="v3")

        cache = ParquetCache(tmp_path, version="v3")
        stored = cache.read("VIX", Timeframe.DAY_1)
        assert stored is not None
        for col in ("timestamp", "open", "high", "low", "close", "volume", "vwap"):
            assert col in stored.columns, f"missing column: {col}"

    def test_respects_lookback_days_parameter(self, tmp_path: Path):
        """fetch_vix_history must be called with the correct date window."""
        vix_df = _make_vix_df(["2025-01-15"], [18.5])

        with patch(
            "milodex.data.tape_cache_warmup.fetch_vix_history", return_value=vix_df
        ) as mock_fetch:
            with patch(
                "milodex.data.tape_cache_warmup.datetime"
            ) as mock_dt:
                mock_dt.now.return_value = datetime(2025, 1, 20, tzinfo=UTC)
                mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                warmup_vix_cache(cache_dir=tmp_path, cache_version="v3", lookback_days=30)

        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args
        # Either positional or keyword args — check the end date is close to today
        end_arg = call_kwargs.kwargs.get("end") or call_kwargs.args[1]
        assert end_arg == date(2025, 1, 20)

    def test_uses_cache_version_parameter(self, tmp_path: Path):
        """The supplied cache_version must be used when constructing ParquetCache."""
        vix_df = _make_vix_df(["2025-01-15"], [18.5])

        with patch("milodex.data.tape_cache_warmup.fetch_vix_history", return_value=vix_df):
            warmup_vix_cache(cache_dir=tmp_path, cache_version="v99")

        # File should exist under the v99 version directory
        vix_path = tmp_path / "v99" / "1Day" / "VIX.parquet"
        assert vix_path.exists()


# ---------------------------------------------------------------------------
# get_vix_cache_state tests
# ---------------------------------------------------------------------------


class TestGetVixCacheState:
    def test_returns_not_exists_when_no_parquet(self, tmp_path: Path):
        state = get_vix_cache_state(cache_dir=tmp_path, cache_version="v3")
        assert state["exists"] is False
        assert state["row_count"] == 0
        assert state["latest_date"] is None

    def test_returns_correct_state_when_parquet_exists(self, tmp_path: Path):
        cache = ParquetCache(tmp_path, version="v3")
        df = _make_vix_df(["2025-01-13", "2025-01-14", "2025-01-15"], [17.0, 18.0, 19.0])
        cache.write("VIX", Timeframe.DAY_1, df)

        state = get_vix_cache_state(cache_dir=tmp_path, cache_version="v3")
        assert state["exists"] is True
        assert state["row_count"] == 3
        assert state["latest_date"] == "2025-01-15"

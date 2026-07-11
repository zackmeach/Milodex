"""Tests for ParquetCache."""

import multiprocessing
import os
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from milodex.data.cache import ParquetCache
from milodex.data.models import Timeframe


# Top-level so multiprocessing can serialize it on Windows.
def _writer_worker(args):
    cache_dir, value = args
    cache = ParquetCache(cache_dir)
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-15"], utc=True),
            "open": [value],
            "high": [value],
            "low": [value],
            "close": [value],
            "volume": [1000000],
            "vwap": [value],
        }
    )
    cache.write("AAPL", Timeframe.DAY_1, df)
    return value


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

    def test_version_segment_in_path(self, tmp_path, sample_df):
        """Cache file must land at {cache_dir}/{version}/{timeframe}/{symbol}.parquet."""
        versioned_cache = ParquetCache(tmp_path / "market_cache", version="v2")
        versioned_cache.write("AAPL", Timeframe.DAY_1, sample_df)
        expected = tmp_path / "market_cache" / "v2" / "1Day" / "AAPL.parquet"
        assert expected.exists(), f"Expected parquet at {expected}"

    def test_different_versions_are_isolated(self, tmp_path, sample_df):
        """v1 and v2 caches must not share files."""
        cache_v1 = ParquetCache(tmp_path / "market_cache", version="v1")
        cache_v2 = ParquetCache(tmp_path / "market_cache", version="v2")
        cache_v1.write("AAPL", Timeframe.DAY_1, sample_df)
        # v2 sees a cache miss even though v1 has data
        assert cache_v2.read("AAPL", Timeframe.DAY_1) is None

    def test_v2_v3_versions_isolated(self, tmp_path, sample_df):
        """v2 (split-only) and v3 (split + dividend) caches must not share files.

        Bumping the cache version is the only mechanism that prevents an
        Adjustment.SPLIT parquet from being silently consumed by a code path
        that assumes Adjustment.ALL data. Any change to the on-disk bar format
        (or the upstream request that produced it) requires a fresh version
        segment.
        """
        cache_v2 = ParquetCache(tmp_path / "market_cache", version="v2")
        cache_v3 = ParquetCache(tmp_path / "market_cache", version="v3")
        cache_v2.write("AAPL", Timeframe.DAY_1, sample_df)
        # v3 sees a cache miss even though v2 has data
        assert cache_v3.read("AAPL", Timeframe.DAY_1) is None

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


def test_read_treats_zero_byte_parquet_as_cache_miss(cache, cache_dir, caplog):
    # A 0-byte parquet file results from a write interrupted before any bytes
    # land on disk (process death mid-write, OS crash, OOM). Without this
    # guard, the next read crashes pyarrow with "Parquet file size is 0 bytes"
    # and takes the runner down. Treating the corrupt file as a cache miss
    # lets the upstream fetcher re-acquire the data from the source.
    target = cache_dir / "v1" / "1Day" / "AAPL.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")
    with caplog.at_level("WARNING", logger="milodex.data.cache"):
        result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is None
    assert any("cache_corrupt" in r.message and "AAPL" in r.message for r in caplog.records)


def test_write_is_atomic_when_underlying_serializer_corrupts_target(
    cache, cache_dir, sample_df, monkeypatch
):
    # Failure mode reproduced from a real runner crash: the parquet serializer
    # opens the destination in 'wb' mode (truncating it to 0 bytes), starts
    # writing, then the process dies before completing. Without atomic write,
    # the destination is left at 0 bytes and the next read crashes pyarrow
    # with "Parquet file size is 0 bytes" — exactly the production failure.
    # The write-to-temp + atomic rename pattern keeps the destination intact.
    cache.write("AAPL", Timeframe.DAY_1, sample_df)
    target = cache_dir / "v1" / "1Day" / "AAPL.parquet"
    original_bytes = target.read_bytes()
    assert len(original_bytes) > 0

    real_to_parquet = pd.DataFrame.to_parquet

    def _truncate_then_raise(self, path, *args, **kwargs):
        # Simulate a serializer that opened+truncated the file, then died.
        with open(path, "wb"):
            pass
        raise OSError("simulated mid-write interruption")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _truncate_then_raise)

    new_df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-02-01"], utc=True),
            "open": [200.0],
            "high": [201.0],
            "low": [199.0],
            "close": [200.5],
            "volume": [1000000],
            "vwap": [200.2],
        }
    )
    with pytest.raises(OSError):
        cache.write("AAPL", Timeframe.DAY_1, new_df)

    # Restore so the post-write assertion path can read the file normally.
    monkeypatch.setattr(pd.DataFrame, "to_parquet", real_to_parquet)

    # Destination is unchanged — the failed write never touched it.
    assert target.exists()
    assert target.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# Unique-tmp-name tests (PR-A: fix concurrent-writer race)
# ---------------------------------------------------------------------------


def test_write_uses_unique_tmp_path_per_call(cache, cache_dir, sample_df, monkeypatch):
    """Each call to write() must use a distinct tmp filename.

    Two concurrent writers that share the same symbol both land their tmp files
    in the same directory. If the tmp name is deterministic, the second open()
    truncates the first writer's temp — corrupting its in-flight write.
    """
    captured_paths: list = []
    real_to_parquet = pd.DataFrame.to_parquet

    def _capture_path(self, path, *args, **kwargs):
        captured_paths.append(path)
        real_to_parquet(self, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _capture_path)

    cache.write("AAPL", Timeframe.DAY_1, sample_df)
    cache.write("AAPL", Timeframe.DAY_1, sample_df)

    assert len(captured_paths) == 2, "expected two to_parquet calls"
    assert captured_paths[0] != captured_paths[1], "tmp paths must differ between calls"

    expected_parent = cache_dir / "v1" / "1Day"
    for p in captured_paths:
        assert str(p).startswith(str(expected_parent)), f"unexpected parent for {p}"
        assert str(p).startswith(str(expected_parent / "AAPL.parquet.")), (
            f"tmp path {p!r} does not start with AAPL.parquet."
        )


def test_concurrent_writers_do_not_collide(tmp_path):
    """Four processes writing the same symbol concurrently must all succeed.

    With a deterministic tmp name, two writers race to open() the same path;
    the loser silently truncates the winner's temp file, producing a corrupt
    rename target.  The unique-per-writer tmp name eliminates that race.
    """
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    args = [(cache_dir, v) for v in [1.0, 2.0, 3.0, 4.0]]
    with multiprocessing.Pool(4) as pool:
        results = pool.map(_writer_worker, args)
    assert sorted(results) == [1.0, 2.0, 3.0, 4.0]

    final = ParquetCache(cache_dir).read("AAPL", Timeframe.DAY_1)
    assert final is not None and len(final) == 1
    assert float(final["close"].iloc[0]) in {1.0, 2.0, 3.0, 4.0}


# ---------------------------------------------------------------------------
# _replace_with_retry tests (writer-vs-writer rename collision fix)
# ---------------------------------------------------------------------------


def test_replace_with_retry_succeeds_after_transient_permission_error(cache, cache_dir, sample_df):
    """A single transient PermissionError on os.replace is retried and the
    write succeeds — the destination file is created with correct content.
    """
    real_replace = os.replace
    call_count = 0

    def _fail_once(src, dst):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise PermissionError("[WinError 5] Access is denied (simulated)")
        real_replace(src, dst)

    with patch("milodex.data.cache.os.replace", side_effect=_fail_once):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)

    assert call_count == 2, f"expected os.replace to be called twice, got {call_count}"

    result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None
    assert len(result) == len(sample_df)


def test_replace_with_retry_gives_up_after_max_attempts(cache, cache_dir, sample_df):
    """When os.replace always raises PermissionError, _replace_with_retry
    exhausts max_attempts (default 4) calls then re-raises.

    The BaseException cleanup path must have run — no .tmp* files remain.
    """
    call_count = 0

    def _always_fail(src, dst):
        nonlocal call_count
        call_count += 1
        raise PermissionError("[WinError 5] Access is denied (simulated)")

    with patch("milodex.data.cache.os.replace", side_effect=_always_fail):
        with patch("milodex.data.cache.time.sleep"):  # don't actually sleep in tests
            with pytest.raises(PermissionError):
                cache.write("AAPL", Timeframe.DAY_1, sample_df)

    assert call_count == 4, f"expected exactly 4 os.replace calls (max_attempts), got {call_count}"

    # BaseException cleanup must have removed the tmp file.
    tmp_files = list(cache_dir.rglob("*.tmp*"))
    assert tmp_files == [], f"orphan tmp files found after exhausted retry: {tmp_files}"


def test_concurrent_failure_leaves_no_orphan_tmp_for_other_writer(
    cache, cache_dir, sample_df, monkeypatch
):
    """A failed write's BaseException-catch unlinks its own tmp; a subsequent
    successful write must find zero leftover ``.tmp*`` files and produce a
    valid destination.
    """
    real_to_parquet = pd.DataFrame.to_parquet
    call_count = 0

    def _fail_first_call(self, path, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Write 0 bytes to the tmp path then raise — simulates a mid-write crash.
            with open(path, "wb"):
                pass
            raise OSError("simulated first-write failure")
        real_to_parquet(self, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _fail_first_call)

    with pytest.raises(OSError):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)

    # Second call uses the real to_parquet (call_count == 2 path above).
    cache.write("AAPL", Timeframe.DAY_1, sample_df)

    result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None and len(result) == 3

    # No orphan tmp files in the whole cache tree.
    tmp_files = list(cache_dir.rglob("*.tmp*"))
    assert tmp_files == [], f"orphan tmp files found: {tmp_files}"


# ---------------------------------------------------------------------------
# _read_parquet_with_retry tests (reader-vs-writer rename collision fix).
# A sibling runner reading a symbol's parquet while another runner is mid
# os.replace onto it gets PermissionError(13) on Windows; the read must
# survive the transient collision the same way the write side already does.
# ---------------------------------------------------------------------------


def test_read_with_retry_succeeds_after_transient_permission_error(cache, sample_df):
    """A single transient PermissionError on the parquet read is retried and
    the cached frame is returned intact (reader-vs-writer-rename race)."""
    cache.write("AAPL", Timeframe.DAY_1, sample_df)

    real_read = pd.read_parquet
    call_count = 0

    def _fail_once(path, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise PermissionError("[Errno 13] Permission denied (simulated)")
        return real_read(path, *args, **kwargs)

    with patch("milodex.data.cache.pd.read_parquet", side_effect=_fail_once):
        result = cache.read("AAPL", Timeframe.DAY_1)

    assert call_count == 2, f"expected pd.read_parquet to be called twice, got {call_count}"
    assert result is not None
    assert len(result) == len(sample_df)


def test_write_raises_on_slash_symbol(cache, sample_df):
    """A symbol containing '/' (e.g. 'BTC/USD') has no filesystem-safe cache
    key today — writing must fail loudly instead of nesting a stray
    directory and dying inside pyarrow with an unhelpful OSError."""
    with pytest.raises(ValueError, match="path separator"):
        cache.write("BTC/USD", Timeframe.DAY_1, sample_df)


def test_read_raises_on_slash_symbol(cache):
    """Same guard on the read path — ``_path`` backs every cache method."""
    with pytest.raises(ValueError, match="path separator"):
        cache.read("BTC/USD", Timeframe.DAY_1)


def test_read_with_retry_gives_up_after_max_attempts(cache, sample_df):
    """When the parquet read always raises PermissionError, the read retry
    exhausts max_attempts (default 4) then re-raises rather than crashing the
    whole runner process on the first transient collision."""
    cache.write("AAPL", Timeframe.DAY_1, sample_df)

    call_count = 0

    def _always_fail(path, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise PermissionError("[Errno 13] Permission denied (simulated)")

    with patch("milodex.data.cache.pd.read_parquet", side_effect=_always_fail):
        with patch("milodex.data.cache.time.sleep"):  # don't actually sleep in tests
            with pytest.raises(PermissionError):
                cache.read("AAPL", Timeframe.DAY_1)

    assert call_count == 4, f"expected exactly 4 read attempts (max_attempts), got {call_count}"

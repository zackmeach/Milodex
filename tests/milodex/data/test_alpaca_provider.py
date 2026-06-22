# tests/milodex/data/test_alpaca_provider.py
"""Tests for AlpacaDataProvider.

All tests mock the Alpaca SDK -- no real API calls.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.requests import Adjustment

from milodex.data.alpaca_provider import AlpacaDataProvider
from milodex.data.models import Bar, BarSet, Timeframe


def _make_429_api_error() -> APIError:
    """Construct an APIError that reports status_code == 429."""
    http_error = MagicMock(spec=requests.exceptions.HTTPError)
    http_error.response = MagicMock()
    http_error.response.status_code = 429
    return APIError('{"code": 429, "message": "too many requests"}', http_error)


def _make_api_error_with_none_response() -> APIError:
    """Construct an APIError whose status_code property raises AttributeError.

    This happens when http_error.response is None -- the SDK property body does
    ``http_error.response.status_code`` without a None-check, so ``None.status_code``
    raises AttributeError.
    """
    http_error = MagicMock(spec=requests.exceptions.HTTPError)
    http_error.response = None  # triggers AttributeError in APIError.status_code
    return APIError('{"code": 0, "message": "unknown"}', http_error)


def _make_bar(timestamp: datetime, close: float = 151.0) -> MagicMock:
    """Create a mock Alpaca bar object."""
    bar = MagicMock()
    bar.timestamp = timestamp
    bar.open = close - 1.0
    bar.high = close + 1.0
    bar.low = close - 1.5
    bar.close = close
    bar.volume = 1_000_000
    bar.vwap = close
    return bar


@pytest.fixture()
def mock_alpaca_bar():
    """Create a mock Alpaca bar object."""
    return _make_bar(datetime(2025, 1, 15, 5, 0, tzinfo=UTC), close=151.0)


@pytest.fixture()
def provider(tmp_path):
    """Create an AlpacaDataProvider with mocked credentials and cache."""
    with patch("milodex.data.alpaca_provider.get_alpaca_credentials") as mock_creds:
        mock_creds.return_value = ("test-key", "test-secret")
        with patch("milodex.data.alpaca_provider.get_cache_dir") as mock_cache:
            mock_cache.return_value = tmp_path / "market_cache"
            with patch("milodex.data.alpaca_provider.get_trading_mode", return_value="paper"):
                with patch("milodex.data.alpaca_provider.StockHistoricalDataClient"):
                    with patch("milodex.data.alpaca_provider.TradingClient"):
                        yield AlpacaDataProvider()


class TestGetBars:
    def test_returns_dict_of_barsets(self, provider, mock_alpaca_bar):
        """R-DAT-002: AlpacaDataProvider.get_bars returns a non-empty BarSet per symbol."""
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert "AAPL" in result
        assert isinstance(result["AAPL"], BarSet)
        assert len(result["AAPL"]) == 1

    def test_returns_empty_barset_for_unknown_symbol(self, provider):
        provider._client.get_stock_bars.return_value = MagicMock(data={})
        result = provider.get_bars(
            symbols=["ZZZZZ"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert "ZZZZZ" in result
        assert len(result["ZZZZZ"]) == 0

    def test_requests_iex_feed_for_stock_bars(self, provider, mock_alpaca_bar):
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})

        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        request = provider._client.get_stock_bars.call_args.args[0]
        assert request.feed == DataFeed.IEX

    def test_stock_bars_request_uses_all_adjustment(self, provider, mock_alpaca_bar):
        """StockBarsRequest must carry Adjustment.ALL (split AND dividend adjusted).

        Two failure modes this guards against:
        1. Splits: raw bars contain ~75% one-day drops on split dates
           (AAPL 2020-08-31, NVDA 2021-07-20, TSLA 2020/2022, AMZN 2022-06-06,
           GOOGL 2022-07-18) which are interpreted as real crashes by strategies.
        2. Dividends: raw or split-only bars omit dividend reinvestment, dropping
           1.5-3% of total return per year for long-only equity strategies and
           making backtests systematically pessimistic vs. real-world buy-and-hold.

        Adjustment.ALL is the most-inclusive setting and the only one that produces
        bars matching published total-return benchmarks (e.g., Yahoo Finance SPY).
        """
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})

        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        request = provider._client.get_stock_bars.call_args.args[0]
        assert request.adjustment == Adjustment.ALL


class TestBackfillRange:
    """backfill_range force-fetches the full range and heals an interior cache
    gap that get_bars' recent-window scan (today - 60d) assumes is complete."""

    def test_heals_interior_gap(self, provider):
        d0102 = datetime(2025, 1, 2, 5, 0, tzinfo=UTC)
        d0103 = datetime(2025, 1, 3, 5, 0, tzinfo=UTC)  # weekday — the gap
        d0106 = datetime(2025, 1, 6, 5, 0, tzinfo=UTC)

        # Seed the cache with a gap at 2025-01-03 (fetch only 01-02 and 01-06).
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"SPY": [_make_bar(d0102), _make_bar(d0106)]}
        )
        provider.get_bars(["SPY"], Timeframe.DAY_1, date(2025, 1, 2), date(2025, 1, 6))
        seeded = provider._cache.read("SPY", Timeframe.DAY_1)
        seeded_dates = set(pd.to_datetime(seeded["timestamp"]).dt.date)
        assert date(2025, 1, 3) not in seeded_dates  # gap confirmed present

        # Force a full-range backfill; the source now returns the missing 01-03.
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"SPY": [_make_bar(d0102), _make_bar(d0103), _make_bar(d0106)]}
        )
        counts = provider.backfill_range(
            ["SPY"], Timeframe.DAY_1, date(2025, 1, 1), date(2025, 1, 7)
        )

        healed = provider._cache.read("SPY", Timeframe.DAY_1)
        healed_dates = set(pd.to_datetime(healed["timestamp"]).dt.date)
        assert date(2025, 1, 3) in healed_dates  # gap healed
        assert counts["SPY"] == 3

    def test_fetches_full_range_with_iex_all_adjustment(self, provider):
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"SPY": [_make_bar(datetime(2025, 2, 3, 5, 0, tzinfo=UTC))]}
        )
        provider.backfill_range(["SPY"], Timeframe.DAY_1, date(2025, 1, 1), date(2025, 3, 1))
        request = provider._client.get_stock_bars.call_args.args[0]
        assert request.feed == DataFeed.IEX
        assert request.adjustment == Adjustment.ALL
        # Full requested range — no cache-coverage short-circuit.
        assert request.start.date() == date(2025, 1, 1)
        assert request.end.date() == date(2025, 3, 1)

    def test_empty_symbols_makes_no_call(self, provider):
        result = provider.backfill_range([], Timeframe.DAY_1, date(2025, 1, 1), date(2025, 2, 1))
        assert result == {}
        provider._client.get_stock_bars.assert_not_called()


class TestGetBarsBatching:
    """Verify that get_bars collapses N symbols into ONE API call when they share
    the same missing date range (the common case for a cold cache or daily
    incremental update)."""

    def test_single_api_call_for_multiple_symbols_cold_cache(self, provider):
        """N symbols with no cache -> 1 batched request, not N individual requests."""
        symbols = ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "WMT"]
        ts = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        response_data = {sym: [_make_bar(ts)] for sym in symbols}
        provider._client.get_stock_bars.return_value = MagicMock(data=response_data)

        result = provider.get_bars(
            symbols=symbols,
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        # All 10 symbols share the same missing range -> exactly 1 API call
        assert provider._client.get_stock_bars.call_count == 1
        for sym in symbols:
            assert sym in result

    def test_batched_request_symbol_or_symbols_is_list(self, provider):
        """When multiple symbols are batched, symbol_or_symbols must be a list."""
        symbols = ["AAPL", "MSFT"]
        ts = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        response_data = {sym: [_make_bar(ts)] for sym in symbols}
        provider._client.get_stock_bars.return_value = MagicMock(data=response_data)

        provider.get_bars(
            symbols=symbols,
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        request = provider._client.get_stock_bars.call_args.args[0]
        assert isinstance(request.symbol_or_symbols, list)
        assert set(request.symbol_or_symbols) == set(symbols)

    def test_single_symbol_uses_string_not_list(self, provider, mock_alpaca_bar):
        """Single-symbol requests use a plain string, not a 1-element list."""
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})

        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        request = provider._client.get_stock_bars.call_args.args[0]
        assert request.symbol_or_symbols == "AAPL"

    def test_per_symbol_cache_written_correctly_after_batch(self, provider):
        """After a batched fetch, each symbol has correct data and caches independently."""
        symbols = ["AAPL", "MSFT"]
        ts = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        response_data = {
            "AAPL": [_make_bar(ts, close=150.0)],
            "MSFT": [_make_bar(ts, close=300.0)],
        }
        provider._client.get_stock_bars.return_value = MagicMock(data=response_data)

        result = provider.get_bars(
            symbols=symbols,
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        # Both symbols have correct data in their BarSets
        assert len(result["AAPL"]) == 1
        assert len(result["MSFT"]) == 1
        assert float(result["AAPL"].to_dataframe()["close"].iloc[0]) == pytest.approx(150.0)
        assert float(result["MSFT"].to_dataframe()["close"].iloc[0]) == pytest.approx(300.0)

        # Cache is readable per-symbol on a second call (no API hit)
        provider._client.get_stock_bars.reset_mock()
        result2 = provider.get_bars(
            symbols=symbols,
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert provider._client.get_stock_bars.call_count == 0
        assert float(result2["AAPL"].to_dataframe()["close"].iloc[0]) == pytest.approx(150.0)
        assert float(result2["MSFT"].to_dataframe()["close"].iloc[0]) == pytest.approx(300.0)

    def test_symbol_absent_from_response_gets_empty_barset(self, provider):
        """A symbol with no data in the Alpaca response gets an empty BarSet; others are ok."""
        ts = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        # AAPL returns data; UNKNOWN returns nothing (absent from response.data)
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [_make_bar(ts)]})

        result = provider.get_bars(
            symbols=["AAPL", "UNKNOWN"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        assert provider._client.get_stock_bars.call_count == 1  # still one batched call
        assert len(result["AAPL"]) == 1
        assert "UNKNOWN" in result
        assert len(result["UNKNOWN"]) == 0

    def test_no_sleep_between_batched_calls(self, provider, mock_alpaca_bar):
        """No time.sleep calls occur on a successful single-batch fetch (0.35s sleep removed)."""
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})

        sleep_calls: list[float] = []
        with patch("time.sleep", side_effect=sleep_calls.append):
            provider.get_bars(
                symbols=["AAPL"],
                timeframe=Timeframe.DAY_1,
                start=date(2025, 1, 15),
                end=date(2025, 1, 15),
            )

        # No sleeps at all on a successful single-call fetch
        assert sleep_calls == []

    def test_two_distinct_missing_ranges_produce_two_api_calls(self, provider):
        """Symbols needing different date ranges each get their own batched call.

        Setup: AAPL has cache covering 2024-06-01 to 2025-01-14; MSFT has no cache.
        Request range: 2024-06-01 to 2025-01-15.
        - AAPL only needs the 1-day tail: (2025-01-15, 2025-01-15)
        - MSFT needs the full range: (2024-06-01, 2025-01-15)
        These are different date ranges -> 2 batched API calls.
        """
        # Populate AAPL cache with data from 2024-06-01 to 2025-01-14
        aapl_bars = [
            _make_bar(datetime(2024, 6, 3, 5, 0, tzinfo=UTC)),  # Mon
            _make_bar(datetime(2025, 1, 14, 5, 0, tzinfo=UTC)),  # Tue
        ]
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": aapl_bars})
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2024, 6, 1),
            end=date(2025, 1, 14),
        )
        provider._client.get_stock_bars.reset_mock()

        # Now request AAPL + MSFT from 2024-06-01 to 2025-01-15.
        # AAPL cache covers 2024-06-01..2025-01-14; it only needs the +1 day tail.
        # MSFT has no cache; it needs the full range.
        # These are two different ranges -> 2 API calls.
        provider._client.get_stock_bars.return_value = MagicMock(
            data={
                "AAPL": [_make_bar(datetime(2025, 1, 15, 5, 0, tzinfo=UTC))],
                "MSFT": [
                    _make_bar(datetime(2024, 6, 3, 5, 0, tzinfo=UTC)),
                    _make_bar(datetime(2025, 1, 15, 5, 0, tzinfo=UTC)),
                ],
            }
        )
        provider.get_bars(
            symbols=["AAPL", "MSFT"],
            timeframe=Timeframe.DAY_1,
            start=date(2024, 6, 1),
            end=date(2025, 1, 15),
        )

        # AAPL needs (2025-01-15, 2025-01-15); MSFT needs (2024-06-01, 2025-01-15) -> 2 calls
        assert provider._client.get_stock_bars.call_count >= 2


class TestGetLatestBar:
    def test_returns_bar(self, provider, mock_alpaca_bar):
        provider._client.get_stock_latest_bar.return_value = {"AAPL": mock_alpaca_bar}
        result = provider.get_latest_bar("AAPL")
        assert isinstance(result, Bar)
        assert result.close == 151.0
        assert result.vwap == 151.0

    def test_requests_iex_feed_for_latest_bar(self, provider, mock_alpaca_bar):
        provider._client.get_stock_latest_bar.return_value = {"AAPL": mock_alpaca_bar}

        provider.get_latest_bar("AAPL")

        request = provider._client.get_stock_latest_bar.call_args.args[0]
        assert request.feed == DataFeed.IEX


class TestGetBarsCaching:
    def test_cache_hit_avoids_api_call(self, provider, mock_alpaca_bar):
        """When cache fully covers the request and end < today, no API call.

        R-DAT-003: a second identical fetch is served from the Parquet cache.
        """
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        call_count_after_first = provider._client.get_stock_bars.call_count

        # Second call should use cache (end date is in the past)
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert provider._client.get_stock_bars.call_count == call_count_after_first
        assert "AAPL" in result

    def test_today_always_refetched(self, provider, mock_alpaca_bar):
        """Bars for today should always hit the API even if cached."""
        today = datetime.now(tz=UTC).date()
        mock_alpaca_bar.timestamp = datetime(today.year, today.month, today.day, 14, 30, tzinfo=UTC)
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})
        # First call
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=today,
            end=today,
        )
        first_count = provider._client.get_stock_bars.call_count

        # Second call -- today should still hit API
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=today,
            end=today,
        )
        assert provider._client.get_stock_bars.call_count > first_count

    def test_today_refetch_never_requests_start_after_end(self, provider, mock_alpaca_bar):
        """When today is already cached, refetch logic must not generate an invalid range."""
        today = datetime.now(tz=UTC).date()
        mock_alpaca_bar.timestamp = datetime(today.year, today.month, today.day, 14, 30, tzinfo=UTC)
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})

        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=today,
            end=today,
        )
        first_count = provider._client.get_stock_bars.call_count

        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=today,
            end=today,
        )

        request = provider._client.get_stock_bars.call_args.args[0]
        assert request.start.date() <= request.end.date()
        assert provider._client.get_stock_bars.call_count == first_count + 1

    def test_stale_cache_behind_today_fetches_full_tail(self, provider):
        """A daily cache that has fallen days behind today must re-fetch the
        whole gap (cache_end+1 .. today), not just (today, today).

        The today-only range returns empty from the live feed, so the cache
        would otherwise stay pinned at its last-warmed bar -- the 2026-05-28
        silent per-universe staleness bug (largecap stuck at 05-18 while ETFs
        were current). The discriminating assertion is the requested START:
        buggy code requests start == today; the fix requests start == cache_end+1.
        """
        today = datetime.now(tz=UTC).date()
        cache_end = today - timedelta(days=10)

        # 1) Seed the cache so its last bar is `cache_end` (end < today -> cached).
        provider._client.get_stock_bars.return_value = MagicMock(
            data={
                "AAPL": [
                    _make_bar(
                        datetime(cache_end.year, cache_end.month, cache_end.day, 5, 0, tzinfo=UTC)
                    )
                ]
            }
        )
        provider.get_bars(
            symbols=["AAPL"], timeframe=Timeframe.DAY_1, start=cache_end, end=cache_end
        )

        # 2) Request through today. Mock a fresh tail (a today-dated bar).
        provider._client.get_stock_bars.return_value = MagicMock(
            data={
                "AAPL": [
                    _make_bar(
                        datetime(today.year, today.month, today.day, 5, 0, tzinfo=UTC), close=160.0
                    )
                ]
            }
        )
        result = provider.get_bars(
            symbols=["AAPL"], timeframe=Timeframe.DAY_1, start=cache_end, end=today
        )

        request = provider._client.get_stock_bars.call_args.args[0]
        # Discriminator: the fetch must START at cache_end+1 to fill the gap,
        # not at `today` (which returns empty live and never heals the cache).
        assert request.start.date() == cache_end + timedelta(days=1), (
            f"expected gap-fill starting {cache_end + timedelta(days=1)}, "
            f"got {request.start.date()}"
        )
        assert request.end.date() == today
        # Corroborating: the cache heals -- latest bar is now today.
        assert result["AAPL"].latest().timestamp.date() == today


class TestGetTradeableAssets:
    def test_returns_list_of_symbols(self, provider):
        asset1 = MagicMock()
        asset1.symbol = "AAPL"
        asset1.tradable = True
        asset1.status = "active"

        asset2 = MagicMock()
        asset2.symbol = "GOOG"
        asset2.tradable = True
        asset2.status = "active"

        # Untradeable asset should be filtered out
        asset3 = MagicMock()
        asset3.symbol = "DELISTED"
        asset3.tradable = False
        asset3.status = "inactive"

        provider._trading_client = MagicMock()
        provider._trading_client.get_all_assets.return_value = [asset1, asset2, asset3]

        result = provider.get_tradeable_assets()
        assert "AAPL" in result
        assert "GOOG" in result
        assert "DELISTED" not in result


class TestRetryOn429:
    """Tests for _call_with_retry_on_429 via the public get_bars / get_latest_bar surface."""

    def test_get_bars_retries_on_429_then_succeeds(self, provider, mock_alpaca_bar):
        """429 errors trigger retry; eventual success returns data normally."""
        err429 = _make_429_api_error()
        provider._client.get_stock_bars.side_effect = [
            err429,
            err429,
            MagicMock(data={"AAPL": [mock_alpaca_bar]}),
        ]

        with patch("time.sleep"):
            result = provider.get_bars(
                symbols=["AAPL"],
                timeframe=Timeframe.DAY_1,
                start=date(2025, 1, 15),
                end=date(2025, 1, 15),
            )

        assert "AAPL" in result
        assert len(result["AAPL"]) == 1
        assert provider._client.get_stock_bars.call_count == 3

    def test_get_bars_does_not_retry_on_non_429_errors(self, provider):
        """Non-429 HTTP errors bubble up immediately without retry."""
        err500 = requests.exceptions.HTTPError(response=MagicMock(status_code=500))
        provider._client.get_stock_bars.side_effect = err500

        with patch("time.sleep"):
            with pytest.raises(requests.exceptions.HTTPError):
                provider.get_bars(
                    symbols=["AAPL"],
                    timeframe=Timeframe.DAY_1,
                    start=date(2025, 1, 15),
                    end=date(2025, 1, 15),
                )

        assert provider._client.get_stock_bars.call_count == 1

    def test_get_bars_gives_up_after_max_attempts(self, provider):
        """After max_attempts (default 5), the last 429 is re-raised."""
        err429 = _make_429_api_error()
        # Always fail with 429
        provider._client.get_stock_bars.side_effect = err429

        with patch("time.sleep"):
            with pytest.raises(APIError) as exc_info:
                provider.get_bars(
                    symbols=["AAPL"],
                    timeframe=Timeframe.DAY_1,
                    start=date(2025, 1, 15),
                    end=date(2025, 1, 15),
                )

        assert exc_info.value is err429
        assert provider._client.get_stock_bars.call_count == 5

    def test_retry_uses_exponential_backoff_with_jitter(self, provider, mock_alpaca_bar):
        """Backoff sleeps follow base_delay * 2^attempt + jitter, capped at max_delay."""
        err429 = _make_429_api_error()
        provider._client.get_stock_bars.side_effect = [
            err429,
            err429,
            err429,
            err429,
            MagicMock(data={"AAPL": [mock_alpaca_bar]}),
        ]

        sleep_calls: list[float] = []

        def record_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        fixed_jitter = 0.5

        with patch("time.sleep", side_effect=record_sleep):
            with patch("random.uniform", return_value=fixed_jitter):
                provider.get_bars(
                    symbols=["AAPL"],
                    timeframe=Timeframe.DAY_1,
                    start=date(2025, 1, 15),
                    end=date(2025, 1, 15),
                )

        # The per-symbol 0.35s sleep has been removed; all recorded sleeps are backoff.
        # Expected: base_delay=1.0, jitter=0.5
        # attempt 0: min(1.0 * 2^0 + 0.5, 60) = 1.5
        # attempt 1: min(1.0 * 2^1 + 0.5, 60) = 2.5
        # attempt 2: min(1.0 * 2^2 + 0.5, 60) = 4.5
        # attempt 3: min(1.0 * 2^3 + 0.5, 60) = 8.5
        assert sleep_calls == pytest.approx([1.5, 2.5, 4.5, 8.5], rel=1e-6)
        assert all(s <= 60.0 for s in sleep_calls)

    def test_handles_apierror_with_response_none(self, provider):
        """APIError whose status_code raises AttributeError (response=None) is re-raised.

        The guard uses getattr(exc, "status_code", None) which catches AttributeError
        raised by the SDK property when http_error.response is None. The error is NOT
        retried (we cannot determine it was 429), so it propagates on the first attempt.
        """
        err = _make_api_error_with_none_response()
        provider._client.get_stock_bars.side_effect = err

        with patch("time.sleep"):
            with pytest.raises(APIError) as exc_info:
                provider.get_bars(
                    symbols=["AAPL"],
                    timeframe=Timeframe.DAY_1,
                    start=date(2025, 1, 15),
                    end=date(2025, 1, 15),
                )

        # No retry -- should propagate immediately on attempt 0.
        assert exc_info.value is err
        assert provider._client.get_stock_bars.call_count == 1


def test_timeframe_map_covers_every_timeframe_member() -> None:
    """Every Timeframe enum member must have an Alpaca TimeFrame mapping.

    A partial map is a latent KeyError: adding a Timeframe member without a
    mapping entry breaks any get_bars call for that timeframe.

    R-DAT-005: covers MINUTE_1, MINUTE_5, MINUTE_15, HOUR_1, DAY_1 mapping to Alpaca.
    """
    from milodex.data.alpaca_provider import _TIMEFRAME_MAP

    missing = [tf for tf in Timeframe if tf not in _TIMEFRAME_MAP]
    assert not missing, f"Timeframe members missing from _TIMEFRAME_MAP: {missing}"

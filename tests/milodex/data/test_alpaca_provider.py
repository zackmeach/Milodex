# tests/milodex/data/test_alpaca_provider.py
"""Tests for AlpacaDataProvider.

All tests mock the Alpaca SDK -- no real API calls.
"""

import logging
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
from milodex.data.provider import DataConnectivityError


def _make_tls_eof_error() -> requests.exceptions.SSLError:
    """The exact class/shape that killed four daily runners on 2026-07-23.

    urllib3 surfaces a mid-read TLS teardown as ``MaxRetryError`` caused by
    ``SSLEOFError``; requests re-wraps it in ``requests.exceptions.SSLError``
    (a ``ConnectionError`` subclass).
    """
    return requests.exceptions.SSLError(
        "HTTPSConnectionPool(host='data.alpaca.markets', port=443): Max retries "
        "exceeded with url: /v2/stocks/bars (Caused by SSLError(SSLEOFError(8, "
        "'[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol')))"
    )


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
            with patch("milodex.data.alpaca_provider.StockHistoricalDataClient"):
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


class TestGetBarsTransientConnectivity:
    """The bars fetch survives TLS/connection teardown (live defect 2026-07-23).

    Four daily runners crashed at the identical second when their post-close
    close-eval bars fetch hit ``SSLEOFError``: ``get_bars`` wrapped its fetch
    in the 429-only retry helper, so the raw ``requests.exceptions.SSLError``
    bubbled out of the data layer unclassified. Bars reads are idempotent —
    they now ride ``call_with_retry_on_transient``, and transient exhaustion
    is translated to ``DataConnectivityError`` (the data-plane analogue of
    ``BrokerConnectionError``) so the runner's poll loop can classify it.
    """

    def test_get_bars_retries_tls_eof_then_succeeds(self, provider, mock_alpaca_bar):
        err = _make_tls_eof_error()
        ok = MagicMock(data={"AAPL": [mock_alpaca_bar]})
        provider._client.get_stock_bars.side_effect = [err, ok]

        with patch("time.sleep"):
            result = provider.get_bars(
                symbols=["AAPL"],
                timeframe=Timeframe.DAY_1,
                start=date(2025, 1, 15),
                end=date(2025, 1, 15),
            )

        assert len(result["AAPL"]) == 1
        assert provider._client.get_stock_bars.call_count == 2

    def test_get_bars_transient_exhaustion_raises_data_connectivity_error(self, provider):
        err = _make_tls_eof_error()
        provider._client.get_stock_bars.side_effect = err

        with patch("time.sleep"):
            with pytest.raises(DataConnectivityError) as exc_info:
                provider.get_bars(
                    symbols=["AAPL"],
                    timeframe=Timeframe.DAY_1,
                    start=date(2025, 1, 15),
                    end=date(2025, 1, 15),
                )

        assert exc_info.value.__cause__ is err

    def test_get_bars_non_transient_error_propagates_unchanged(self, provider):
        """A non-transient, non-429 error is neither retried nor translated."""
        http_error = MagicMock(spec=requests.exceptions.HTTPError)
        http_error.response = MagicMock()
        http_error.response.status_code = 422
        err = APIError('{"code": 422, "message": "unprocessable"}', http_error)
        provider._client.get_stock_bars.side_effect = err

        with patch("time.sleep"):
            with pytest.raises(APIError) as exc_info:
                provider.get_bars(
                    symbols=["AAPL"],
                    timeframe=Timeframe.DAY_1,
                    start=date(2025, 1, 15),
                    end=date(2025, 1, 15),
                )

        assert exc_info.value is err
        assert provider._client.get_stock_bars.call_count == 1

    def test_get_latest_bar_transient_exhaustion_raises_data_connectivity_error(self, provider):
        err = _make_tls_eof_error()
        provider._client.get_stock_latest_bar.side_effect = err

        with patch("time.sleep"):
            with pytest.raises(DataConnectivityError) as exc_info:
                provider.get_latest_bar("AAPL")

        assert exc_info.value.__cause__ is err


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

        # Cache is readable per-symbol on a second call. Each symbol's warm read
        # fires exactly one adjustment-epoch probe (the fixed mock returns the
        # same closes, so both probes match and no full refetch happens).
        provider._client.get_stock_bars.reset_mock()
        result2 = provider.get_bars(
            symbols=symbols,
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert provider._client.get_stock_bars.call_count == 2  # one probe per symbol
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
    def test_cache_hit_probes_adjustment_then_serves_cache(self, provider, mock_alpaca_bar):
        """When cache fully covers the request and end < today, the warm read
        probes the earliest cached bar for a stale adjustment epoch (one tiny
        call) and, on a match, serves from cache WITHOUT refetching the full range.

        R-DAT-003 + bug-sweep #1 (corporate-action staleness guard): the old
        contract (zero API calls on a hit) is replaced by 'exactly one probe
        call, and no full refetch on a match' (a divergence-heal would be +2).
        """
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        call_count_after_first = provider._client.get_stock_bars.call_count

        # Second call: probe matches (same close) -> serve cache, no full refetch.
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert provider._client.get_stock_bars.call_count == call_count_after_first + 1
        assert "AAPL" in result
        assert len(result["AAPL"]) == 1

    def test_adjustment_probe_match_serves_cache_without_full_refetch(self, provider):
        """On a match, the probe is a SINGLE-DAY request for the EARLIEST cached
        bar (not the recent tail) and the full multi-day range is not refetched —
        the served bars are the untouched cached values."""
        ts13 = datetime(2025, 1, 13, 5, 0, tzinfo=UTC)
        ts14 = datetime(2025, 1, 14, 5, 0, tzinfo=UTC)
        ts15 = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts13, 100.0), _make_bar(ts14, 101.0), _make_bar(ts15, 102.0)]}
        )
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 15),
        )

        provider._client.get_stock_bars.reset_mock()
        # Probe returns the same earliest close (100) -> match.
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts13, 100.0)]}
        )
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 15),
        )

        assert provider._client.get_stock_bars.call_count == 1  # probe only, no full refetch
        req = provider._client.get_stock_bars.call_args.args[0]
        assert req.start.date() == date(2025, 1, 13)  # earliest cached bar...
        assert req.end.date() == date(2025, 1, 13)  # ...single day, not the full range
        assert result["AAPL"].to_dataframe()["close"].tolist() == pytest.approx(
            [100.0, 101.0, 102.0]
        )

    def test_stale_adjustment_epoch_heals_on_divergence(self, provider):
        """A corporate action after caching re-scales the whole adjusted series.
        The warm-read probe detects the earliest bar's close has drifted and heals
        by REPLACING the whole cached range with freshly-adjusted bars."""
        ts13 = datetime(2025, 1, 13, 5, 0, tzinfo=UTC)
        ts14 = datetime(2025, 1, 14, 5, 0, tzinfo=UTC)
        ts15 = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        # Cold fetch caches three bars at close=100 (the old adjustment epoch).
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts13, 100.0), _make_bar(ts14, 100.0), _make_bar(ts15, 100.0)]}
        )
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 15),
        )

        # Warm read: a 2:1 split re-scales the series to close=50. Probe (earliest,
        # single day) returns 50; the heal refetch (Jan 13-15) returns 50 for all.
        provider._client.get_stock_bars.reset_mock()
        provider._client.get_stock_bars.side_effect = [
            MagicMock(data={"AAPL": [_make_bar(ts13, 50.0)]}),  # probe
            MagicMock(
                data={"AAPL": [_make_bar(ts13, 50.0), _make_bar(ts14, 50.0), _make_bar(ts15, 50.0)]}
            ),  # heal refetch of the full range
        ]
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 15),
        )
        assert provider._client.get_stock_bars.call_count == 2  # probe + full refetch
        assert result["AAPL"].to_dataframe()["close"].tolist() == pytest.approx([50.0, 50.0, 50.0])

        # The on-disk cache was REPLACED: a subsequent (now-matching) read sees 50.
        provider._client.get_stock_bars.side_effect = None
        provider._client.get_stock_bars.reset_mock()
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts13, 50.0)]}
        )
        result2 = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 15),
        )
        assert result2["AAPL"].to_dataframe()["close"].tolist() == pytest.approx([50.0, 50.0, 50.0])
        assert provider._client.get_stock_bars.call_count == 1  # just the matching probe

    def test_adjustment_probe_error_serves_cache_fail_safe(self, provider):
        """If the probe call errors, serve the cache unchanged (status quo before
        the guard) — the probe must never become a new failure mode."""
        ts15 = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts15, 100.0)]}
        )
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        # Warm read: probe raises a non-429 error -> caught -> serve stale cache.
        provider._client.get_stock_bars.reset_mock()
        provider._client.get_stock_bars.side_effect = RuntimeError("probe boom")
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert "AAPL" in result
        assert result["AAPL"].to_dataframe()["close"].tolist() == pytest.approx([100.0])

    def test_adjustment_probe_malformed_response_serves_cache_fail_safe(self, provider):
        """A malformed probe RESPONSE (not a network error) — e.g. a bar whose
        close can't be parsed — must also fail safe and serve the cache. Guards
        the whole-probe fail-safe (parse errors are wrapped too, not just the
        network call)."""
        ts15 = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts15, 100.0)]}
        )
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        # Warm read: probe returns a bar with the right timestamp but an
        # unparseable close -> float() raises inside the probe -> serve cache.
        bad_bar = MagicMock()
        bad_bar.timestamp = ts15
        bad_bar.close = "not-a-number"
        provider._client.get_stock_bars.reset_mock()
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [bad_bar]})
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert result["AAPL"].to_dataframe()["close"].tolist() == pytest.approx([100.0])

    def test_heal_cache_write_error_serves_stale_cache_fail_safe(self, provider, caplog):
        """SWP-01: if the divergence heal's cache.write raises (e.g. a Windows
        parquet file-lock IOError), the heal must fail safe — serve the stale
        cached frame, log a WARN, and never raise out of the warm read. The
        widened fail-safe now spans parse + write, not just the refetch call."""
        ts13 = datetime(2025, 1, 13, 5, 0, tzinfo=UTC)
        ts14 = datetime(2025, 1, 14, 5, 0, tzinfo=UTC)
        ts15 = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        # Seed three cached bars at close=100 (the old adjustment epoch).
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts13, 100.0), _make_bar(ts14, 100.0), _make_bar(ts15, 100.0)]}
        )
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 15),
        )

        # Warm read: probe diverges (50 vs 100) -> heal; but cache.write raises.
        provider._client.get_stock_bars.reset_mock()
        provider._client.get_stock_bars.side_effect = [
            MagicMock(data={"AAPL": [_make_bar(ts13, 50.0)]}),  # probe -> diverge
            MagicMock(
                data={"AAPL": [_make_bar(ts13, 50.0), _make_bar(ts14, 50.0), _make_bar(ts15, 50.0)]}
            ),  # heal refetch of the full range
        ]
        with patch.object(provider._cache, "write", side_effect=OSError("parquet locked")):
            with caplog.at_level(logging.WARNING):
                result = provider.get_bars(
                    symbols=["AAPL"],
                    timeframe=Timeframe.DAY_1,
                    start=date(2025, 1, 13),
                    end=date(2025, 1, 15),
                )

        # No exception; the stale cached frame (100s) is served unchanged.
        assert result["AAPL"].to_dataframe()["close"].tolist() == pytest.approx(
            [100.0, 100.0, 100.0]
        )
        assert "adjustment_heal_error" in caplog.text

    def test_heal_empty_refetch_warns_and_serves_stale_cache(self, provider, caplog):
        """SWP-05: a divergence heal whose refetch comes back EMPTY (a non-
        converging heal) must log a WARN (adjustment_heal_empty) and serve the
        stale cache — not silently return None after the probe's 'healing' INFO."""
        ts13 = datetime(2025, 1, 13, 5, 0, tzinfo=UTC)
        ts14 = datetime(2025, 1, 14, 5, 0, tzinfo=UTC)
        ts15 = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts13, 100.0), _make_bar(ts14, 100.0), _make_bar(ts15, 100.0)]}
        )
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 15),
        )

        # Warm read: probe diverges (50) -> heal; the heal refetch returns nothing.
        provider._client.get_stock_bars.reset_mock()
        provider._client.get_stock_bars.side_effect = [
            MagicMock(data={"AAPL": [_make_bar(ts13, 50.0)]}),  # probe -> diverge
            MagicMock(data={"AAPL": []}),  # heal refetch empty
        ]
        with caplog.at_level(logging.WARNING):
            result = provider.get_bars(
                symbols=["AAPL"],
                timeframe=Timeframe.DAY_1,
                start=date(2025, 1, 13),
                end=date(2025, 1, 15),
            )

        assert provider._client.get_stock_bars.call_count == 2  # probe + empty heal refetch
        assert result["AAPL"].to_dataframe()["close"].tolist() == pytest.approx(
            [100.0, 100.0, 100.0]
        )
        assert "adjustment_heal_empty" in caplog.text

    def test_backfill_range_readjusts_within_window_only(self, provider):
        """backfill_range overwrites cached bars IN its window with freshly-
        adjusted values (merge keep=last), but leaves bars OUTSIDE the window on
        their old adjustment epoch — the documented re-adjustment scope."""
        ts13 = datetime(2025, 1, 13, 5, 0, tzinfo=UTC)
        ts14 = datetime(2025, 1, 14, 5, 0, tzinfo=UTC)
        ts15 = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        # Seed three cached bars at close=100.
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts13, 100.0), _make_bar(ts14, 100.0), _make_bar(ts15, 100.0)]}
        )
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 15),
        )

        # Backfill only [Jan 14, Jan 15] with re-adjusted close=90.
        provider._client.get_stock_bars.reset_mock()
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [_make_bar(ts14, 90.0), _make_bar(ts15, 90.0)]}
        )
        provider.backfill_range(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 14),
            end=date(2025, 1, 15),
        )

        cached = provider._cache.read("AAPL", Timeframe.DAY_1).sort_values("timestamp")
        closes = cached["close"].tolist()
        # Jan 13 (outside window) keeps old epoch; Jan 14-15 (in window) re-adjusted.
        assert closes == pytest.approx([100.0, 90.0, 90.0])

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


class TestGetLatestBarTransientRetry:
    """get_latest_bar is on the live trade path (drain fresh-price / cap pricing)
    and is an idempotent read, so a transient connection failure must be retried
    rather than killing the runner's poll loop (call_with_retry_on_transient)."""

    def test_retries_on_connection_error_then_succeeds(self, provider, mock_alpaca_bar):
        """First call raises ConnectionError; the retry succeeds and returns the bar."""
        provider._client.get_stock_latest_bar.side_effect = [
            requests.exceptions.ConnectionError("connection reset by peer"),
            {"AAPL": mock_alpaca_bar},
        ]

        with patch("time.sleep"):
            result = provider.get_latest_bar("AAPL")

        assert isinstance(result, Bar)
        assert result.close == 151.0
        assert provider._client.get_stock_latest_bar.call_count == 2

    def test_retries_on_read_timeout_then_succeeds(self, provider, mock_alpaca_bar):
        """A transient ReadTimeout is retried, not propagated."""
        provider._client.get_stock_latest_bar.side_effect = [
            requests.exceptions.ReadTimeout("read timed out"),
            {"AAPL": mock_alpaca_bar},
        ]

        with patch("time.sleep"):
            result = provider.get_latest_bar("AAPL")

        assert result.close == 151.0
        assert provider._client.get_stock_latest_bar.call_count == 2


class TestCacheWritePersistenceFailSoft:
    """The poll path's cache PERSISTENCE step is fail-soft; everything else stays loud.

    2026-07-23 live defect: five co-running SPY intraday runners held
    SPY.parquet open (pd.read_parquet handles) so long that the writer's
    rename-retry budget exhausted and the PermissionError crashed two runner
    sessions. The fetched bars were already in memory and re-fetchable next
    poll, so losing the session over a failed cache WRITE is strictly worse
    than serving the in-memory merge and retrying persistence next poll.

    Boundary contract pinned here:
    - CacheWriteContentionError from merge  -> fail soft (serve in-memory merge,
      one warning, cache untouched)
    - upstream fetch errors                 -> propagate unchanged
    - merge data-integrity errors           -> propagate unchanged
    """

    @staticmethod
    def _daily_bars(days: list[int], closes: list[float]) -> list[MagicMock]:
        return [
            _make_bar(datetime(2025, 1, day, 5, 0, tzinfo=UTC), close=close)
            for day, close in zip(days, closes, strict=True)
        ]

    def test_serves_fetched_bars_when_cache_write_contended_cold_cache(self, provider, caplog):
        """Cold cache + unpersistable write: get_bars still returns the fetched
        bars and logs exactly one contention warning for the symbol."""
        bars = self._daily_bars([13, 14, 15], [150.0, 151.0, 152.0])
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": bars})

        with patch(
            "milodex.data.cache.os.replace",
            side_effect=PermissionError("[WinError 5] Access is denied (simulated)"),
        ):
            with patch("milodex.data.cache.time.sleep"):
                with caplog.at_level(logging.WARNING, logger="milodex.data.alpaca_provider"):
                    result = provider.get_bars(
                        symbols=["AAPL"],
                        timeframe=Timeframe.DAY_1,
                        start=date(2025, 1, 13),
                        end=date(2025, 1, 15),
                    )

        barset = result["AAPL"]
        assert len(barset) == 3, "fetched bars must be served despite the failed persistence"

        warnings = [r for r in caplog.records if "cache_write_contention" in r.getMessage()]
        assert len(warnings) == 1, f"expected exactly one contention warning, got {len(warnings)}"

        # Persistence genuinely failed — nothing on disk.
        assert provider._cache.read("AAPL", Timeframe.DAY_1) is None

    def test_serves_in_memory_merge_of_cache_and_fetch_on_contention(self, provider, caplog):
        """Warm cache + new tail + unpersistable write: get_bars serves the
        in-memory merge (cached history + fresh tail) while the on-disk cache
        stays on its previous state for the next poll to heal."""
        seed = self._daily_bars([13, 14], [150.0, 151.0])
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": seed})
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 13),
            end=date(2025, 1, 14),
        )
        assert len(provider._cache.read("AAPL", Timeframe.DAY_1)) == 2

        tail = self._daily_bars([15], [152.0])
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": tail})

        with patch(
            "milodex.data.cache.os.replace",
            side_effect=PermissionError("[WinError 5] Access is denied (simulated)"),
        ):
            with patch("milodex.data.cache.time.sleep"):
                with caplog.at_level(logging.WARNING, logger="milodex.data.alpaca_provider"):
                    result = provider.get_bars(
                        symbols=["AAPL"],
                        timeframe=Timeframe.DAY_1,
                        start=date(2025, 1, 13),
                        end=date(2025, 1, 15),
                    )

        barset = result["AAPL"]
        assert len(barset) == 3, "must serve cached history merged with the fresh tail"
        assert float(barset.to_dataframe()["close"].iloc[-1]) == 152.0

        # On-disk cache unchanged — the next poll re-fetches the tail and
        # re-persists.
        assert len(provider._cache.read("AAPL", Timeframe.DAY_1)) == 2

    def test_fetch_errors_still_propagate(self, provider):
        """The fail-soft boundary covers ONLY cache persistence: an upstream
        fetch failure propagates out of get_bars exactly as before."""
        err500 = requests.exceptions.HTTPError(response=MagicMock(status_code=500))
        provider._client.get_stock_bars.side_effect = err500

        with pytest.raises(requests.exceptions.HTTPError):
            provider.get_bars(
                symbols=["AAPL"],
                timeframe=Timeframe.DAY_1,
                start=date(2025, 1, 13),
                end=date(2025, 1, 15),
            )

    def test_merge_data_integrity_errors_still_propagate(self, provider):
        """A schema/dtype ValueError from merge is a data-integrity failure,
        not a persistence hiccup — it must stay loud (silent NaN/dtype
        corruption feeding the promotion gate is worse than a dead runner)."""
        bars = self._daily_bars([13], [150.0])
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": bars})

        with patch.object(
            provider._cache, "merge", side_effect=ValueError("schema drift (simulated)")
        ):
            with pytest.raises(ValueError, match="schema drift"):
                provider.get_bars(
                    symbols=["AAPL"],
                    timeframe=Timeframe.DAY_1,
                    start=date(2025, 1, 13),
                    end=date(2025, 1, 15),
                )


def test_timeframe_map_covers_every_timeframe_member() -> None:
    """Every Timeframe enum member must have an Alpaca TimeFrame mapping.

    A partial map is a latent KeyError: adding a Timeframe member without a
    mapping entry breaks any get_bars call for that timeframe.

    R-DAT-005: covers MINUTE_1, MINUTE_5, MINUTE_15, HOUR_1, DAY_1 mapping to Alpaca.
    """
    from milodex.data.alpaca_provider import _TIMEFRAME_MAP

    missing = [tf for tf in Timeframe if tf not in _TIMEFRAME_MAP]
    assert not missing, f"Timeframe members missing from _TIMEFRAME_MAP: {missing}"

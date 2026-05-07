# tests/milodex/data/test_alpaca_provider.py
"""Tests for AlpacaDataProvider.

All tests mock the Alpaca SDK — no real API calls.
"""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

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

    This happens when http_error.response is None — the SDK property body does
    ``http_error.response.status_code`` without a None-check, so ``None.status_code``
    raises AttributeError.
    """
    http_error = MagicMock(spec=requests.exceptions.HTTPError)
    http_error.response = None  # triggers AttributeError in APIError.status_code
    return APIError('{"code": 0, "message": "unknown"}', http_error)


@pytest.fixture()
def mock_alpaca_bar():
    """Create a mock Alpaca bar object."""
    bar = MagicMock()
    bar.timestamp = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
    bar.open = 150.0
    bar.high = 152.0
    bar.low = 149.5
    bar.close = 151.0
    bar.volume = 1000000
    bar.vwap = 150.8
    return bar


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


class TestGetLatestBar:
    def test_returns_bar(self, provider, mock_alpaca_bar):
        provider._client.get_stock_latest_bar.return_value = {"AAPL": mock_alpaca_bar}
        result = provider.get_latest_bar("AAPL")
        assert isinstance(result, Bar)
        assert result.close == 151.0
        assert result.vwap == 150.8

    def test_requests_iex_feed_for_latest_bar(self, provider, mock_alpaca_bar):
        provider._client.get_stock_latest_bar.return_value = {"AAPL": mock_alpaca_bar}

        provider.get_latest_bar("AAPL")

        request = provider._client.get_stock_latest_bar.call_args.args[0]
        assert request.feed == DataFeed.IEX


class TestGetBarsCaching:
    def test_cache_hit_avoids_api_call(self, provider, mock_alpaca_bar):
        """When cache fully covers the request and end < today, no API call."""
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

        # Second call — today should still hit API
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

        # Filter out the 0.35 intra-runner pacing sleeps; keep only backoff sleeps
        # (backoff delays are > 0.35 for all 4 retries with base_delay=1.0)
        backoff_sleeps = [s for s in sleep_calls if s > 0.35]

        # Expected: base_delay=1.0, jitter=0.5
        # attempt 0: min(1.0 * 2^0 + 0.5, 60) = 1.5
        # attempt 1: min(1.0 * 2^1 + 0.5, 60) = 2.5
        # attempt 2: min(1.0 * 2^2 + 0.5, 60) = 4.5
        # attempt 3: min(1.0 * 2^3 + 0.5, 60) = 8.5
        assert backoff_sleeps == pytest.approx([1.5, 2.5, 4.5, 8.5], rel=1e-6)
        assert all(s <= 60.0 for s in backoff_sleeps)

    def test_handles_apierror_with_response_none(self, provider):
        """APIError whose status_code raises AttributeError (response=None) is re-raised.

        The guard uses getattr(exc, "status_code", None) which catches AttributeError
        raised by the SDK property when http_error.response is None. The error is NOT
        retried (we can't determine it was 429), so it propagates on the first attempt.
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

        # No retry — should propagate immediately on attempt 0.
        assert exc_info.value is err
        assert provider._client.get_stock_bars.call_count == 1

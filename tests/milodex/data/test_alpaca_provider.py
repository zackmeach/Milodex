# tests/milodex/data/test_alpaca_provider.py
"""Tests for AlpacaDataProvider.

All tests mock the Alpaca SDK — no real API calls.
"""

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest
from alpaca.data.enums import DataFeed
from alpaca.data.requests import Adjustment

from milodex.data.alpaca_provider import AlpacaDataProvider
from milodex.data.models import Bar, BarSet, Timeframe


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

    def test_stock_bars_request_uses_split_adjustment(self, provider, mock_alpaca_bar):
        """StockBarsRequest must carry Adjustment.SPLIT.

        Without this, raw bars contain ~75% one-day drops on split dates
        (AAPL 2020-08-31, NVDA 2021-07-20, TSLA 2020/2022, AMZN 2022-06-06,
        GOOGL 2022-07-18) which are interpreted as real crashes by strategies.
        """
        provider._client.get_stock_bars.return_value = MagicMock(data={"AAPL": [mock_alpaca_bar]})

        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )

        request = provider._client.get_stock_bars.call_args.args[0]
        assert request.adjustment == Adjustment.SPLIT

    def test_split_adjustment_returns_adjusted_close_values(self, provider):
        """Regression: provider returns bars as-received from Alpaca (no transform).

        With Adjustment.SPLIT set on the request, Alpaca returns pre-adjusted
        prices. This test verifies the provider passes those values through
        unchanged — a split-adjusted AAPL close near $125 (Aug 2020) should
        appear as ~125, not ~500 (the raw pre-split price).
        """
        adjusted_bar = MagicMock()
        # AAPL 2020-08-28: split-adjusted close ~$124, raw pre-split ~$496
        adjusted_bar.timestamp = datetime(2020, 8, 28, 20, 0, tzinfo=UTC)
        adjusted_bar.open = 124.0
        adjusted_bar.high = 125.18
        adjusted_bar.low = 123.83
        adjusted_bar.close = 124.37
        adjusted_bar.volume = 338054800
        adjusted_bar.vwap = 124.5

        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [adjusted_bar]}
        )

        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2020, 8, 28),
            end=date(2020, 8, 28),
        )

        bars = result["AAPL"]
        assert len(bars) == 1
        row = bars.to_dataframe().iloc[0]
        # Split-adjusted price is ~$124; raw pre-split price would be ~$496.
        # Assert we got the adjusted value back (i.e., provider did not transform it).
        assert row["close"] == pytest.approx(124.37)
        assert row["close"] < 200, (
            "Close should be split-adjusted (~$124), not raw pre-split (~$496)"
        )


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

# src/milodex/data/alpaca_provider.py
"""Alpaca implementation of DataProvider.

This is the ONLY file in the data layer that imports alpaca-py.
All Alpaca-specific types are translated to milodex models before
being returned to callers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestBarRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient

from milodex.config import get_alpaca_credentials, get_cache_dir, get_trading_mode
from milodex.data.cache import ParquetCache
from milodex.data.models import Bar, BarSet, Timeframe
from milodex.data.provider import DataProvider

# Map our Timeframe enum to Alpaca's TimeFrame objects
_TIMEFRAME_MAP: dict[Timeframe, TimeFrame] = {
    Timeframe.MINUTE_1: TimeFrame(1, TimeFrameUnit.Minute),
    Timeframe.MINUTE_5: TimeFrame(5, TimeFrameUnit.Minute),
    Timeframe.MINUTE_15: TimeFrame(15, TimeFrameUnit.Minute),
    Timeframe.HOUR_1: TimeFrame(1, TimeFrameUnit.Hour),
    Timeframe.DAY_1: TimeFrame(1, TimeFrameUnit.Day),
}


class AlpacaDataProvider(DataProvider):
    """Market data provider backed by Alpaca's API.

    Uses StockHistoricalDataClient for bar data and TradingClient
    for asset discovery. Caches fetched data locally as Parquet files.
    """

    def __init__(self) -> None:
        api_key, secret_key = get_alpaca_credentials()
        self._client = StockHistoricalDataClient(api_key, secret_key)
        paper = get_trading_mode() == "paper"
        self._trading_client = TradingClient(api_key, secret_key, paper=paper)
        self._cache = ParquetCache(get_cache_dir())

    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, BarSet]:
        """Fetch OHLCV bars, using cache where available.

        Cache strategy:
        - If cache fully covers the range and end < today, use cache only.
        - Otherwise, identify missing date ranges and fetch only those.
        - Today's date is always re-fetched (market may still be open).
        - Merge fetched data into cache for future use.
        """
        alpaca_tf = _TIMEFRAME_MAP[timeframe]
        today = datetime.now(tz=UTC).date()
        result: dict[str, BarSet] = {}

        for symbol in symbols:
            cached_df = self._cache.read(symbol, timeframe)
            cached_range = self._cache.get_cached_range(symbol, timeframe)

            # Full cache hit: range covered and not requesting today
            if (
                cached_range is not None
                and cached_df is not None
                and cached_range[0] <= start
                and cached_range[1] >= end
                and end < today
            ):
                ts = pd.to_datetime(cached_df["timestamp"])
                mask = (ts.dt.date >= start) & (ts.dt.date <= end)
                result[symbol] = BarSet(cached_df[mask].reset_index(drop=True))
                continue

            # Determine what ranges to fetch from Alpaca
            ranges_to_fetch: list[tuple[date, date]] = []
            if cached_range is None or cached_df is None:
                # No cache — fetch everything
                ranges_to_fetch.append((start, end))
            else:
                cache_start, cache_end = cached_range
                cached_dates = set(pd.to_datetime(cached_df["timestamp"]).dt.date)
                # Before cache start
                if start < cache_start:
                    ranges_to_fetch.append((start, min(end, cache_start)))
                # After cache end (or today needs re-fetch)
                if end > cache_end or end >= today:
                    fetch_from = max(start, cache_end)
                    ranges_to_fetch.append((fetch_from, end))
                # Gaps in the middle: check for missing dates in range
                if start >= cache_start and end <= cache_end:
                    check = max(start, cache_start)
                    gap_start = None
                    while check <= min(end, cache_end):
                        if check not in cached_dates:
                            if gap_start is None:
                                gap_start = check
                        elif gap_start is not None:
                            ranges_to_fetch.append((gap_start, check))
                            gap_start = None
                        check += timedelta(days=1)
                    if gap_start is not None:
                        ranges_to_fetch.append((gap_start, check))

            # Fetch each missing range from Alpaca
            all_new_dfs: list[pd.DataFrame] = []
            for fetch_start, fetch_end in ranges_to_fetch:
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=alpaca_tf,
                    start=datetime(
                        fetch_start.year,
                        fetch_start.month,
                        fetch_start.day,
                        tzinfo=UTC,
                    ),
                    end=datetime(
                        fetch_end.year,
                        fetch_end.month,
                        fetch_end.day,
                        23,
                        59,
                        59,
                        tzinfo=UTC,
                    ),
                )
                response = self._client.get_stock_bars(request)
                bars_data = response.data.get(symbol, [])
                if bars_data:
                    df = pd.DataFrame(
                        [
                            {
                                "timestamp": b.timestamp,
                                "open": float(b.open),
                                "high": float(b.high),
                                "low": float(b.low),
                                "close": float(b.close),
                                "volume": int(b.volume),
                                "vwap": float(b.vwap) if b.vwap else None,
                            }
                            for b in bars_data
                        ]
                    )
                    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                    all_new_dfs.append(df)

            # Merge new data into cache
            if all_new_dfs:
                new_data = pd.concat(all_new_dfs, ignore_index=True)
                self._cache.merge(symbol, timeframe, new_data)

            # Read full cache and slice to requested range
            full_cache = self._cache.read(symbol, timeframe)
            if full_cache is not None and not full_cache.empty:
                ts = pd.to_datetime(full_cache["timestamp"])
                mask = (ts.dt.date >= start) & (ts.dt.date <= end)
                result[symbol] = BarSet(full_cache[mask].reset_index(drop=True))
            else:
                result[symbol] = BarSet(
                    pd.DataFrame(
                        columns=[
                            "timestamp",
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                            "vwap",
                        ]
                    )
                )

        return result

    def get_latest_bar(self, symbol: str) -> Bar:
        """Fetch the most recent bar from Alpaca."""
        response = self._client.get_stock_latest_bar(
            StockLatestBarRequest(symbol_or_symbols=symbol)
        )
        alpaca_bar = response[symbol]
        return Bar(
            timestamp=alpaca_bar.timestamp,
            open=float(alpaca_bar.open),
            high=float(alpaca_bar.high),
            low=float(alpaca_bar.low),
            close=float(alpaca_bar.close),
            volume=int(alpaca_bar.volume),
            vwap=float(alpaca_bar.vwap) if alpaca_bar.vwap else None,
        )

    def get_tradeable_assets(self) -> list[str]:
        """Return all tradeable symbols from Alpaca."""
        assets = self._trading_client.get_all_assets()
        return [
            a.symbol
            for a in assets
            if a.tradable
            and str(a.status.value if hasattr(a.status, "value") else a.status) == "active"
        ]

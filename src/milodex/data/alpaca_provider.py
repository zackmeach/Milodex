# src/milodex/data/alpaca_provider.py
"""Alpaca implementation of DataProvider.

This is the ONLY file in the data layer that imports alpaca-py.
All Alpaca-specific types are translated to milodex models before
being returned to callers.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import Adjustment, StockBarsRequest, StockLatestBarRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient

from milodex.config import get_alpaca_credentials, get_cache_dir, get_trading_mode
from milodex.core._alpaca_retry import call_with_retry_on_429
from milodex.data.cache import ParquetCache
from milodex.data.models import Bar, BarSet, Timeframe
from milodex.data.provider import DataProvider

_logger = logging.getLogger(__name__)


# Cache schema version -- increment when the on-disk bar format changes in a way
# that makes existing parquet files incompatible or incorrect.
# v1 -> v2 (2026-05-05): switched StockBarsRequest to Adjustment.SPLIT so all
#   cached bars are now split-adjusted. Old raw-bar parquets must be discarded.
# v2 -> v3 (2026-05-06): switched to Adjustment.ALL so cached bars are now split-
#   AND dividend-adjusted. Without this, long-only equity backtests are
#   systematically pessimistic by ~1.5-3% per year (missing dividend
#   reinvestment). Old split-only parquets are silently incompatible with code
#   paths that assume Adjustment.ALL data, so v2 files must not be reused.
CACHE_VERSION = "v3"

# Map our Timeframe enum to Alpaca's TimeFrame objects
_TIMEFRAME_MAP: dict[Timeframe, TimeFrame] = {
    Timeframe.MINUTE_1: TimeFrame(1, TimeFrameUnit.Minute),
    Timeframe.MINUTE_5: TimeFrame(5, TimeFrameUnit.Minute),
    Timeframe.MINUTE_15: TimeFrame(15, TimeFrameUnit.Minute),
    Timeframe.MINUTE_30: TimeFrame(30, TimeFrameUnit.Minute),
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
        self._cache = ParquetCache(get_cache_dir(), version=CACHE_VERSION)

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

        Batching strategy:
        Symbols that share the same missing date range are grouped into a single
        multi-symbol StockBarsRequest, dramatically reducing API call volume for
        large universes (e.g. S&P-100: ~1 call instead of ~98 for a cold cache).
        Different missing ranges each get their own batched request; per-symbol
        cache layout is unchanged.
        """
        alpaca_tf = _TIMEFRAME_MAP[timeframe]
        today = datetime.now(tz=UTC).date()
        result: dict[str, BarSet] = {}

        # -- Phase 1: per-symbol cache check -----------------------------------
        # Symbols with a full cache hit go directly to `result`.
        # Symbols with any missing range are collected in `symbol_ranges`.
        # symbol -> list of (fetch_start, fetch_end) tuples it needs
        symbol_ranges: dict[str, list[tuple[date, date]]] = {}

        for symbol in symbols:
            # Fix #4: read parquet once; derive range from the in-memory frame
            # rather than calling get_cached_range() which re-reads the file.
            cached_df = self._cache.read(symbol, timeframe)
            if cached_df is not None and not cached_df.empty:
                _ts = pd.to_datetime(cached_df["timestamp"])
                cached_range: tuple[date, date] | None = (
                    _ts.min().date(),
                    _ts.max().date(),
                )
            else:
                cached_range = None

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
                # No cache -- fetch everything
                ranges_to_fetch.append((start, end))
            else:
                cache_start, cache_end = cached_range
                cached_dates = set(pd.to_datetime(cached_df["timestamp"]).dt.date)
                # Before cache start
                if start < cache_start:
                    ranges_to_fetch.append((start, min(end, cache_start)))
                # After cache end (or today needs re-fetch). Fetch from the day
                # after the cache ends through `end` so a cache that has fallen
                # behind today fills its whole stale tail -- not just (today,
                # today), which returns empty from the live daily feed and would
                # leave the cache pinned (2026-05-28 silent-staleness bug). The
                # min(..., today) preserves the always-refetch-today intent when
                # the cache already reaches today (cache_end + 1 > today).
                if end >= today:
                    fetch_from = max(start, min(cache_end + timedelta(days=1), today))
                    if fetch_from <= end:
                        ranges_to_fetch.append((fetch_from, end))
                elif end > cache_end:
                    fetch_from = max(start, cache_end + timedelta(days=1))
                    if fetch_from <= end:
                        ranges_to_fetch.append((fetch_from, end))
                # Gaps in the middle: check for missing dates in range.
                # Only scan the recent window -- historical data from initial
                # backtest fetches is assumed complete. Skip weekend-only gaps
                # that produce empty API responses and are never written to cache.
                recent_gap_start = max(start, cache_start, today - timedelta(days=60))
                if start >= cache_start and end <= cache_end:
                    check = recent_gap_start
                    gap_start = None
                    while check <= min(end, cache_end):
                        if check not in cached_dates:
                            if gap_start is None:
                                gap_start = check
                        elif gap_start is not None:
                            if _range_has_weekday(gap_start, check - timedelta(days=1)):
                                ranges_to_fetch.append((gap_start, check))
                            gap_start = None
                        check += timedelta(days=1)
                    if gap_start is not None:
                        gap_end = min(end, cache_end)
                        if _range_has_weekday(gap_start, gap_end):
                            ranges_to_fetch.append((gap_start, gap_end))

            symbol_ranges[symbol] = ranges_to_fetch

        # -- Phase 2: batch API calls by date range ----------------------------
        # Group symbols by the date range they need so we can send one batched
        # StockBarsRequest per (fetch_start, fetch_end) tuple instead of one
        # request per symbol.  Symbols that share identical missing ranges -- the
        # common case for a cold cache or a daily incremental update -- collapse
        # into a single call.
        #
        # range_key -> list[symbol]
        range_to_symbols: dict[tuple[date, date], list[str]] = {}
        for symbol, ranges in symbol_ranges.items():
            for r in ranges:
                range_to_symbols.setdefault(r, []).append(symbol)

        # Accumulate per-symbol DataFrames across all batch responses.
        # symbol -> list[pd.DataFrame]
        fetched: dict[str, list[pd.DataFrame]] = {s: [] for s in symbol_ranges}

        for (fetch_start, fetch_end), batch_symbols in range_to_symbols.items():
            symbol_or_symbols: str | list[str] = (
                batch_symbols[0] if len(batch_symbols) == 1 else batch_symbols
            )
            request = StockBarsRequest(
                symbol_or_symbols=symbol_or_symbols,
                timeframe=alpaca_tf,
                feed=DataFeed.IEX,
                adjustment=Adjustment.ALL,
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
            response = call_with_retry_on_429(lambda: self._client.get_stock_bars(request))

            # Split the batched response back into per-symbol DataFrames.
            # Alpaca returns an empty list (not an error) for symbols with no
            # data in the requested range, so missing symbols are handled
            # gracefully: fetched[sym] stays [] and the symbol gets an empty
            # BarSet in Phase 3.
            for sym in batch_symbols:
                bars_data = response.data.get(sym, [])
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
                    fetched[sym].append(df)

        # -- Phase 3: merge into cache, read back, slice to requested range ----
        for symbol in symbol_ranges:
            sym_dfs = fetched[symbol]
            if sym_dfs:
                new_data = pd.concat(sym_dfs, ignore_index=True)
                self._cache.merge(symbol, timeframe, new_data)

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

    def backfill_range(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, int]:
        """Force-fetch ``[start, end]`` from the source and merge into the cache.

        Unlike :meth:`get_bars`, this does NOT short-circuit on a cache hit and
        does NOT apply the recent-window gap heuristic — it fetches the entire
        requested range and merges it. Use it to heal an interior cache gap that
        is older than the ``get_bars`` recent-scan window (``today - 60d``): e.g.
        a missing year between an old backtest warm (ending 2024-12-31) and a
        later live-runner tail (starting 2026-03-09), which ``get_bars`` assumes
        is complete and never refetches. Merge is additive and de-duplicating,
        so overlapping an existing range is safe.

        Returns ``{symbol: bars_fetched}``.

        ponytail: the fetch/parse/merge below mirrors get_bars Phase 2/3 by
        design — duplicated, not shared, so the hot-path get_bars stays
        byte-identical. DRY into a helper only if a third caller appears.
        """
        if not symbols:
            return {}
        alpaca_tf = _TIMEFRAME_MAP[timeframe]
        request = StockBarsRequest(
            symbol_or_symbols=symbols if len(symbols) > 1 else symbols[0],
            timeframe=alpaca_tf,
            feed=DataFeed.IEX,
            adjustment=Adjustment.ALL,
            start=datetime(start.year, start.month, start.day, tzinfo=UTC),
            end=datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC),
        )
        response = call_with_retry_on_429(lambda: self._client.get_stock_bars(request))
        fetched_counts: dict[str, int] = {}
        for sym in symbols:
            bars_data = response.data.get(sym, [])
            fetched_counts[sym] = len(bars_data)
            if not bars_data:
                continue
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
            self._cache.merge(sym, timeframe, df)
        return fetched_counts

    def get_latest_bar(self, symbol: str) -> Bar:
        """Fetch the most recent bar from Alpaca."""
        response = call_with_retry_on_429(
            lambda: self._client.get_stock_latest_bar(
                StockLatestBarRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
            )
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


def _range_has_weekday(start: date, end: date) -> bool:
    """Return True if [start, end] contains at least one weekday (Mon-Fri).

    Used to skip fetching weekend-only gap ranges that never contain market
    data and are never written back to cache.
    """
    check = start
    while check <= end:
        if check.weekday() < 5:
            return True
        check += timedelta(days=1)
    return False

"""Simulated data provider for backtest execution.

Wraps historical bar data so that :meth:`get_latest_bar` returns the
bar for the *simulation* day being replayed, not today's live bar.
This is the data-layer counterpart to
:class:`milodex.broker.simulated.SimulatedBroker`: together they let the
backtest engine hand intents to the shared
:class:`milodex.execution.service.ExecutionService` without a parallel
execution path.

Pattern: the engine prefetches all bars for the backtest window, then
on each simulation day calls :meth:`set_simulation_day` to advance the
"now" marker. ``get_latest_bar`` reads whatever bar was current on
that day.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from milodex.data.models import Bar, BarSet, Timeframe
from milodex.data.provider import DataProvider


class SimulatedDataProvider(DataProvider):
    """Replays prefetched bars, advancing a simulation-day pointer."""

    def __init__(self, all_bars: dict[str, BarSet]) -> None:
        self._all_bars = {sym.upper(): bs for sym, bs in all_bars.items()}
        self._current_day: date | None = None

    def set_simulation_day(self, day: date) -> None:
        """Advance the "now" marker to the given day."""
        self._current_day = day

    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,  # noqa: ARG002
        start: date,
        end: date,
    ) -> dict[str, BarSet]:
        result: dict[str, BarSet] = {}
        for sym in symbols:
            barset = self._all_bars.get(sym.upper())
            if barset is None:
                continue
            df = barset.to_dataframe()
            if df.empty:
                continue
            timestamps = pd.to_datetime(df["timestamp"], utc=True)
            mask = (timestamps.dt.date >= start) & (timestamps.dt.date <= end)
            sliced = df.loc[mask]
            if not sliced.empty:
                result[sym.upper()] = BarSet(sliced.reset_index(drop=True))
        return result

    def get_latest_bar(self, symbol: str) -> Bar:
        normalized = symbol.upper()
        barset = self._all_bars.get(normalized)
        if barset is None:
            msg = f"No bars available for {normalized} in simulated data provider."
            raise ValueError(msg)

        df = barset.to_dataframe()
        if df.empty:
            msg = f"No bars available for {normalized} in simulated data provider."
            raise ValueError(msg)

        timestamps = pd.to_datetime(df["timestamp"], utc=True)
        if self._current_day is not None:
            mask = timestamps.dt.date <= self._current_day
            df = df.loc[mask]
            if df.empty:
                msg = f"No bars on or before {self._current_day} for {normalized}."
                raise ValueError(msg)
            timestamps = pd.to_datetime(df["timestamp"], utc=True)

        row = df.iloc[-1]
        ts = timestamps.iloc[-1]
        if not isinstance(ts, datetime):
            ts = ts.to_pydatetime()
        return Bar(
            timestamp=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
            vwap=float(row["vwap"]) if pd.notna(row.get("vwap")) else None,
        )

    def get_tradeable_assets(self) -> list[str]:
        return list(self._all_bars.keys())

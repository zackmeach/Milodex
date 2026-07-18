"""Tests for SimulatedDataProvider."""

from __future__ import annotations

from datetime import UTC, date

import pandas as pd
import pytest

from milodex.data.models import BarSet, Timeframe
from milodex.data.simulated import SimulatedDataProvider


def build_barset(start: str, closes: list[float]) -> BarSet:
    timestamps = pd.date_range(start, periods=len(closes), freq="D", tz=UTC)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * len(closes),
                "vwap": closes,
            }
        )
    )


def test_get_latest_bar_without_day_marker_returns_last_bar():
    provider = SimulatedDataProvider({"SPY": build_barset("2024-01-01", [100.0, 101.0, 102.0])})
    bar = provider.get_latest_bar("SPY")
    assert bar.close == 102.0


def test_get_latest_bar_respects_simulation_day():
    provider = SimulatedDataProvider(
        {"SPY": build_barset("2024-01-01", [100.0, 101.0, 102.0, 103.0])}
    )
    provider.set_simulation_day(date(2024, 1, 2))
    bar = provider.get_latest_bar("SPY")
    assert bar.close == 101.0
    assert bar.timestamp.date() == date(2024, 1, 2)


def test_get_latest_bar_symbol_normalized_case_insensitive():
    provider = SimulatedDataProvider({"SPY": build_barset("2024-01-01", [100.0])})
    assert provider.get_latest_bar("spy").close == 100.0


def test_get_latest_bar_unknown_symbol_raises():
    provider = SimulatedDataProvider({"SPY": build_barset("2024-01-01", [100.0])})
    with pytest.raises(ValueError, match="No bars available"):
        provider.get_latest_bar("AAPL")


def test_get_latest_bar_before_any_data_raises():
    provider = SimulatedDataProvider({"SPY": build_barset("2024-01-05", [100.0, 101.0])})
    provider.set_simulation_day(date(2024, 1, 1))
    with pytest.raises(ValueError, match="No bars on or before"):
        provider.get_latest_bar("SPY")


def test_get_bars_filters_by_date_range():
    provider = SimulatedDataProvider(
        {"SPY": build_barset("2024-01-01", [100.0, 101.0, 102.0, 103.0, 104.0])}
    )
    result = provider.get_bars(
        symbols=["SPY"],
        timeframe=Timeframe.DAY_1,
        start=date(2024, 1, 2),
        end=date(2024, 1, 4),
    )
    df = result["SPY"].to_dataframe()
    assert len(df) == 3
    assert df["close"].tolist() == [101.0, 102.0, 103.0]


def test_get_bars_missing_symbol_absent_from_result():
    provider = SimulatedDataProvider({"SPY": build_barset("2024-01-01", [100.0])})
    result = provider.get_bars(
        symbols=["SPY", "AAPL"],
        timeframe=Timeframe.DAY_1,
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
    )
    assert "AAPL" not in result
    assert "SPY" in result


def test_frozen_snapshot_unaffected_by_later_source_mutation() -> None:
    """BarSet defensively copies its source frame, so later source mutation can't alter output.

    A backtest run replays a prefetched snapshot. BarSet copies its input frame on
    construction, so a later "vendor correction" to the original frame must NOT change
    replayed output. This guards the in-memory copy only — it is NOT the run-manifest
    snapshot-versioning the staleness-policy requirement asks for. If BarSet stopped
    copying, this test fails.
    """
    source = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-02", periods=3, freq="D", tz=UTC),
            "open": [100.0, 101.0, 102.0],
            "high": [100.0, 101.0, 102.0],
            "low": [100.0, 101.0, 102.0],
            "close": [100.0, 101.0, 102.0],
            "volume": [1_000_000] * 3,
            "vwap": [100.0, 101.0, 102.0],
        }
    )
    provider = SimulatedDataProvider({"SPY": BarSet(source)})

    # Vendor correction: mutate the ORIGINAL frame after the snapshot was taken.
    source.loc[:, "close"] = [999.0, 999.0, 999.0]

    result = provider.get_bars(
        symbols=["SPY"],
        timeframe=Timeframe.DAY_1,
        start=date(2024, 1, 2),
        end=date(2024, 1, 4),
    )
    assert result["SPY"].to_dataframe()["close"].tolist() == [100.0, 101.0, 102.0]

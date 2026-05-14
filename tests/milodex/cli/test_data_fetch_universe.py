"""Tests for `milodex data fetch-universe`."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from milodex.cli.commands.data import _build_fetch_universe_result, _run_fetch_universe
from milodex.data.models import BarSet, Timeframe

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_UNIVERSE_YAML = """\
universe:
  id: "universe.test_fake.v1"
  version: 1
  slippage_pct: 0.0005
  description: "Fake universe for tests."
  etfs:
    - "SPY"
    - "QQQ"
  stocks:
    - "AAPL"
    - "MSFT"
    - "GOOG"
"""


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "universe_test_fake_v1.yaml").write_text(_UNIVERSE_YAML, encoding="utf-8")
    return tmp_path


def _make_barset(
    timestamps: list[str] | None = None,
) -> BarSet:
    timestamps = timestamps or ["2023-01-03", "2023-12-29"]
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": pd.to_datetime(timestamps, utc=True),
                "open": [100.0] * len(timestamps),
                "high": [105.0] * len(timestamps),
                "low": [99.0] * len(timestamps),
                "close": [103.0] * len(timestamps),
                "volume": [500_000] * len(timestamps),
                "vwap": [102.5] * len(timestamps),
            }
        )
    )


class StubDataProvider:
    """Returns a pre-configured bars_by_symbol mapping."""

    def __init__(self, bars_by_symbol: dict[str, BarSet | None]) -> None:
        self._bars = bars_by_symbol
        self.calls: list[tuple[list[str], Timeframe, date, date]] = []

    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, BarSet | None]:
        self.calls.append((symbols, timeframe, start, end))
        return {s: self._bars.get(s) for s in symbols}


def _make_args(
    config_dir: Path,
    *,
    universe_ref: str = "universe.test_fake.v1",
    start: str = "2023-01-01",
    end: str = "2023-12-31",
    timeframe: str = "1d",
) -> argparse.Namespace:
    return argparse.Namespace(
        data_command="fetch-universe",
        universe_ref=universe_ref,
        start=start,
        end=end,
        timeframe=timeframe,
        config_dir=str(config_dir),
        json_output=False,
    )


class _FakeCtx:
    def __init__(self, provider: StubDataProvider) -> None:
        self._provider = provider

    def data_provider_factory(self) -> StubDataProvider:
        return self._provider


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fetch_universe_resolves_and_calls_provider_with_full_symbol_list(
    config_dir: Path,
) -> None:
    symbols = ("AAPL", "GOOG", "MSFT", "QQQ", "SPY")  # sorted, de-duped
    provider = StubDataProvider({s: _make_barset() for s in symbols})
    ctx = _FakeCtx(provider)
    args = _make_args(config_dir)

    result = _run_fetch_universe(args, ctx)

    assert result.status == "success"
    assert len(provider.calls) == 1
    called_symbols, tf, start, end = provider.calls[0]
    # All 5 universe members must be included
    assert sorted(called_symbols) == list(symbols)
    assert tf == Timeframe.DAY_1
    assert start == date(2023, 1, 1)
    assert end == date(2023, 12, 31)
    assert result.data["total_requested"] == 5
    assert result.data["symbols_with_data"] == 5
    assert result.data["requested_start"] == "2023-01-01"
    assert result.data["requested_end"] == "2023-12-31"


def test_fetch_universe_reports_coverage_when_provider_returns_partial_data(
    config_dir: Path,
) -> None:
    # Provider only returns data for 3 of the 5 symbols
    partial_bars: dict[str, BarSet | None] = {
        "AAPL": _make_barset(),
        "GOOG": None,
        "MSFT": _make_barset(),
        "QQQ": None,
        "SPY": _make_barset(),
    }
    provider = StubDataProvider(partial_bars)
    ctx = _FakeCtx(provider)
    args = _make_args(config_dir)

    result = _run_fetch_universe(args, ctx)

    assert result.status == "success"
    assert result.data["total_requested"] == 5
    assert result.data["symbols_with_data"] == 3
    assert result.data["coverage_pct"] == 60.0
    assert result.data["symbols_with_full_date_range"] == 3
    assert result.data["date_range_coverage_pct"] == 60.0
    assert result.data["date_range_warnings"] == []
    missing = result.data["missing"]
    assert sorted(missing) == ["GOOG", "QQQ"]
    # Human lines surface the missing symbols
    missing_line = next(ln for ln in result.human_lines if "Missing" in ln)
    assert "GOOG" in missing_line
    assert "QQQ" in missing_line


def test_fetch_universe_reports_100_pct_when_complete(config_dir: Path) -> None:
    symbols = ("AAPL", "GOOG", "MSFT", "QQQ", "SPY")
    provider = StubDataProvider({s: _make_barset() for s in symbols})
    ctx = _FakeCtx(provider)
    args = _make_args(config_dir)

    result = _run_fetch_universe(args, ctx)

    assert result.data["coverage_pct"] == 100.0
    assert result.data["missing"] == []
    assert result.data["symbols_with_full_date_range"] == 5
    assert result.data["date_range_coverage_pct"] == 100.0
    assert result.data["date_range_warnings"] == []
    none_line = next(ln for ln in result.human_lines if "Missing" in ln)
    assert "none" in none_line
    warning_line = next(ln for ln in result.human_lines if "Date range warnings" in ln)
    assert "none" in warning_line


def test_fetch_universe_warns_when_data_starts_after_requested_window(
    config_dir: Path,
) -> None:
    symbols = ("AAPL", "GOOG", "MSFT", "QQQ", "SPY")
    provider = StubDataProvider(
        {
            **{s: _make_barset() for s in symbols if s != "AAPL"},
            "AAPL": _make_barset(["2023-02-01", "2023-12-29"]),
        }
    )
    ctx = _FakeCtx(provider)
    args = _make_args(config_dir)

    result = _run_fetch_universe(args, ctx)

    assert result.data["symbols_with_full_date_range"] == 4
    assert result.data["date_range_coverage_pct"] == 80.0
    assert result.data["date_range_warnings"] == [
        {
            "symbol": "AAPL",
            "first_bar_date": "2023-02-01",
            "last_bar_date": "2023-12-29",
            "issue": "starts_after_requested_window",
        }
    ]
    assert any("AAPL: starts_after_requested_window" in ln for ln in result.human_lines)


def test_fetch_universe_warns_when_data_ends_before_requested_window(
    config_dir: Path,
) -> None:
    symbols = ("AAPL", "GOOG", "MSFT", "QQQ", "SPY")
    provider = StubDataProvider(
        {
            **{s: _make_barset() for s in symbols if s != "SPY"},
            "SPY": _make_barset(["2023-01-03", "2023-10-01"]),
        }
    )
    ctx = _FakeCtx(provider)
    args = _make_args(config_dir)

    result = _run_fetch_universe(args, ctx)

    assert result.data["symbols_with_full_date_range"] == 4
    assert result.data["date_range_coverage_pct"] == 80.0
    assert result.data["date_range_warnings"] == [
        {
            "symbol": "SPY",
            "first_bar_date": "2023-01-03",
            "last_bar_date": "2023-10-01",
            "issue": "ends_before_requested_window",
        }
    ]
    assert any("SPY: ends_before_requested_window" in ln for ln in result.human_lines)


def test_fetch_universe_handles_missing_universe_ref_with_clear_error(
    config_dir: Path,
) -> None:
    provider = StubDataProvider({})
    ctx = _FakeCtx(provider)
    args = _make_args(config_dir, universe_ref="universe.does_not_exist.v99")

    result = _run_fetch_universe(args, ctx)

    assert result.status == "error"
    assert len(result.errors) == 1
    assert result.errors[0]["code"] == "universe_ref_not_found"
    assert "does_not_exist" in result.errors[0]["message"]
    # Provider should not have been called
    assert provider.calls == []


# ---------------------------------------------------------------------------
# Unit tests for _build_fetch_universe_result (truncation path)
# ---------------------------------------------------------------------------


def test_missing_list_truncated_at_10() -> None:
    symbols = tuple(f"SYM{i:02d}" for i in range(15))
    # None of the symbols have data
    bars: dict[str, BarSet | None] = {s: None for s in symbols}
    result = _build_fetch_universe_result("universe.x.v1", "1d", symbols, bars)

    assert result.data["symbols_with_data"] == 0
    assert len(result.data["missing"]) == 15  # full list in data
    missing_line = next(ln for ln in result.human_lines if "Missing" in ln)
    assert "..." in missing_line
    # At most 10 symbols shown
    shown = missing_line.split(":")[1]
    shown_count = shown.count(",") + 1 if shown.strip() else 0
    # The truncated display shows ≤ 10 symbols + " ..."
    assert "SYM14" not in missing_line or shown_count <= 11  # 10 names + possible ellipsis entry


def test_date_range_warning_list_truncated_at_10() -> None:
    symbols = tuple(f"SYM{i:02d}" for i in range(15))
    bars = {s: _make_barset(["2023-02-01", "2023-12-29"]) for s in symbols}

    result = _build_fetch_universe_result(
        "universe.x.v1",
        "1d",
        symbols,
        bars,
        requested_start=date(2023, 1, 1),
        requested_end=date(2023, 12, 31),
    )

    assert len(result.data["date_range_warnings"]) == 15
    assert any("Date range warnings     : 15" in ln for ln in result.human_lines)
    assert any(ln.strip() == "..." for ln in result.human_lines)
    assert not any("SYM14: starts_after_requested_window" in ln for ln in result.human_lines)

"""Tests for `milodex data readiness`."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pytest

from milodex.cli.commands.data import _run_readiness
from milodex.data.models import BarSet, Timeframe

_UNIVERSE_YAML = """\
universe:
  id: "universe.test_intraday.v1"
  version: 1
  slippage_pct: 0.0005
  description: "Fake single-symbol universe for readiness tests."
  etfs:
    - "SPY"
  stocks: []
"""

_FORBIDDEN_YAML = """\
universe:
  id: "universe.test_bad.v1"
  version: 1
  slippage_pct: 0.0005
  description: "Fake universe with a forbidden ETP."
  etfs:
    - "SPY"
    - "SQQQ"
  stocks: []
"""


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "universe_test_intraday_v1.yaml").write_text(_UNIVERSE_YAML, encoding="utf-8")
    (tmp_path / "universe_test_bad_v1.yaml").write_text(_FORBIDDEN_YAML, encoding="utf-8")
    return tmp_path


def _session_5min(day: str = "2025-06-17", n: int = 78) -> BarSet:
    start = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    rows = [
        {
            "timestamp": (start + pd.Timedelta(minutes=5 * i)).tz_convert("UTC"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
            "vwap": 100.2,
        }
        for i in range(n)
    ]
    return BarSet(pd.DataFrame(rows))


class _StubProvider:
    def __init__(self, bars: dict[str, BarSet]) -> None:
        self._bars = bars

    def get_bars(self, symbols, timeframe, start, end):
        return {s: self._bars[s] for s in symbols if s in self._bars}


class _FakeCtx:
    def __init__(self, provider: _StubProvider) -> None:
        self._provider = provider

    def data_provider_factory(self) -> _StubProvider:
        return self._provider


def _args(
    config_dir: Path,
    *,
    timeframe: str = "5m",
    universe_ref: str = "universe.test_intraday.v1",
    start: str = "2025-06-17",
    end: str = "2025-06-17",
    feed_label: str = "fallback",
):
    return argparse.Namespace(
        data_command="readiness",
        universe_ref=universe_ref,
        start=start,
        end=end,
        timeframe=timeframe,
        config_dir=str(config_dir),
        feed_label=feed_label,
        json_output=False,
    )


def test_readiness_rejects_daily_timeframe(config_dir: Path) -> None:
    ctx = _FakeCtx(_StubProvider({"SPY": _session_5min()}))
    result = _run_readiness(_args(config_dir, timeframe="1d"), ctx)
    assert result.status == "error"
    assert result.errors[0]["code"] == "invalid_timeframe"


def test_readiness_clean_session_passes(config_dir: Path) -> None:
    ctx = _FakeCtx(_StubProvider({"SPY": _session_5min()}))
    result = _run_readiness(_args(config_dir), ctx)
    assert result.status == "success"
    assert result.data["status"] == "pass"
    assert result.data["timeframe_minutes"] == 5
    assert result.data["feed_label"] == "fallback"


def test_readiness_surfaces_warnings(config_dir: Path) -> None:
    # 40 of 78 bars -> coverage warning.
    ctx = _FakeCtx(_StubProvider({"SPY": _session_5min(n=40)}))
    result = _run_readiness(_args(config_dir), ctx)
    assert result.data["status"] == "pass_with_warnings"
    assert "intraday_session_coverage_below_threshold" in result.data["issue_codes"]


def test_readiness_missing_universe_ref(config_dir: Path) -> None:
    ctx = _FakeCtx(_StubProvider({}))
    result = _run_readiness(_args(config_dir, universe_ref="universe.nope.v9"), ctx)
    assert result.status == "error"
    assert result.errors[0]["code"] == "universe_ref_not_found"


def test_readiness_forbidden_universe_distinct_error(config_dir: Path) -> None:
    ctx = _FakeCtx(_StubProvider({"SPY": _session_5min()}))
    result = _run_readiness(_args(config_dir, universe_ref="universe.test_bad.v1"), ctx)
    assert result.status == "error"
    assert result.errors[0]["code"] == "universe_contains_forbidden_instrument"
    assert "SQQQ" in result.errors[0]["message"]


def test_readiness_passes_correct_timeframe_to_provider(config_dir: Path) -> None:
    captured = {}

    class _CaptureProvider:
        def get_bars(self, symbols, timeframe, start, end):
            captured["tf"] = timeframe
            return {"SPY": _session_5min()}

    result = _run_readiness(_args(config_dir, timeframe="15m"), _FakeCtx(_CaptureProvider()))
    assert captured["tf"] == Timeframe.MINUTE_15
    assert result.data["timeframe_minutes"] == 15

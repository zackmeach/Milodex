"""Direct fail-closed branch coverage for ``StrategyRunner._fresh_pricing_bar``
(runner.py, ADR 0057 §2).

The drain fetches a confirmably-current fresh bar via ``get_latest_bar`` to size
entries and price the exposure cap. ``_fresh_pricing_bar`` is the gatekeeper: it
returns ``None`` (fail closed) whenever the fresh price cannot be confirmed, and
the bar only when every guard passes. The four fail-closed branches and the
happy path are exercised here directly (a focused unit, distinct from the
full-cycle drain tests in test_runner_drain_fresh_price.py).
"""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path

from milodex.data.models import Bar
from tests.milodex.strategies.test_runner_queued_intent_drain import _build_open_runner


def _locked_bar(runner, symbol: str = "SPY") -> Bar:
    """The stub provider's latest daily bar == the locked-in session bar."""
    return runner._data_provider._bars_by_symbol[symbol].latest()


def _fresh(locked: Bar, *, close: float, minutes: int = 1, timestamp=None) -> Bar:
    ts = timestamp
    if ts is None:
        ts = locked.timestamp.to_pydatetime() + timedelta(minutes=minutes)
    return Bar(
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        vwap=close,
    )


def test_fresh_pricing_bar_returns_bar_on_happy_path(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A current-session bar strictly newer than the locked bar with a finite,
    positive close is returned verbatim."""
    runner, _broker, _provider, _event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    locked = _locked_bar(runner)
    fresh = _fresh(locked, close=11.0)
    runner._data_provider.get_latest_bar = lambda _sym: fresh

    assert runner._fresh_pricing_bar("SPY", locked) is fresh


def test_fresh_pricing_bar_returns_none_when_get_latest_bar_raises(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Provider outage (get_latest_bar raises) fails closed -> None (caught here so
    an EXIT routes through the fail-closed branch, not the generic handler)."""
    runner, _broker, _provider, _event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    locked = _locked_bar(runner)

    def boom(_sym: str) -> Bar:
        raise RuntimeError("provider unreachable")

    runner._data_provider.get_latest_bar = boom

    assert runner._fresh_pricing_bar("SPY", locked) is None


def test_fresh_pricing_bar_returns_none_for_non_current_session_bar(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A stale provider returning a PRIOR-session bar (date before _now's date) is
    rejected even if strictly newer than the locked bar by timestamp."""
    runner, _broker, _provider, _event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    locked = _locked_bar(runner)
    # Pin the clock a full day ahead so the fresh bar's date is a PRIOR session.
    pinned = runner._now() + timedelta(days=1)
    runner._now = lambda: pinned
    fresh = _fresh(locked, close=11.0)  # timestamp is locked + 1 min -> prior day vs pinned
    runner._data_provider.get_latest_bar = lambda _sym: fresh

    assert runner._fresh_pricing_bar("SPY", locked) is None


def test_fresh_pricing_bar_returns_none_when_not_strictly_newer(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A provider echoing the locked bar (same timestamp) carries no new price and
    is rejected: the fresh bar must be STRICTLY newer than the locked bar."""
    runner, _broker, _provider, _event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    locked = _locked_bar(runner)
    echo = _fresh(locked, close=11.0, timestamp=locked.timestamp.to_pydatetime())
    runner._data_provider.get_latest_bar = lambda _sym: echo

    assert runner._fresh_pricing_bar("SPY", locked) is None


def test_fresh_pricing_bar_returns_none_for_nonfinite_close(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A NaN close fails closed (NaN > 0 is False)."""
    runner, _broker, _provider, _event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    locked = _locked_bar(runner)
    fresh = _fresh(locked, close=math.nan)
    runner._data_provider.get_latest_bar = lambda _sym: fresh

    assert runner._fresh_pricing_bar("SPY", locked) is None


def test_fresh_pricing_bar_returns_none_for_nonpositive_close(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A zero/negative close fails closed."""
    runner, _broker, _provider, _event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    locked = _locked_bar(runner)
    fresh = _fresh(locked, close=0.0)
    runner._data_provider.get_latest_bar = lambda _sym: fresh

    assert runner._fresh_pricing_bar("SPY", locked) is None

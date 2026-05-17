"""Tests for :class:`milodex.gui.market_tape_state.MarketTapeState`.

Structure mirrors test_performance_state.py:
- Pure-logic helpers tested without Qt.
- Qt lifecycle tests gated behind ``_skip_no_qt``.

Missing-cache vs missing-symbol behaviour (documented choice):
- A completely absent cache dir (or no vN version dir) → dataStatus='error'.
  Rationale: a missing cache is a configuration/infrastructure problem, not a
  per-symbol data gap.  All-None rows for every symbol with status 'ready' would
  silently mask the problem.
- A symbol absent from an otherwise valid cache → that symbol's row has
  close=None / pctChange=None / asOf=None, dataStatus='ready'.
  Rationale: one missing ticker is a data-gap, not a system fault; the other
  symbols should still render.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# PySide6 availability gate
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QCoreApplication, QThreadPool  # noqa: F401
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping Qt-aware MarketTapeState tests",
)

SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "VIX")

# ---------------------------------------------------------------------------
# Pure helper tests — no Qt required
# ---------------------------------------------------------------------------


def test_pct_change_normal() -> None:
    from milodex.gui.market_tape_state import _pct_change

    result = _pct_change(105.0, 100.0)
    assert result is not None
    assert abs(result - 0.05) < 1e-9


def test_pct_change_loss() -> None:
    from milodex.gui.market_tape_state import _pct_change

    result = _pct_change(90.0, 100.0)
    assert result is not None
    assert abs(result - (-0.10)) < 1e-9


def test_pct_change_prior_zero_returns_none() -> None:
    from milodex.gui.market_tape_state import _pct_change

    assert _pct_change(100.0, 0) is None
    assert _pct_change(100.0, 0.0) is None


def test_pct_change_prior_none_returns_none() -> None:
    from milodex.gui.market_tape_state import _pct_change

    assert _pct_change(100.0, None) is None


def test_pct_change_latest_none_returns_none() -> None:
    from milodex.gui.market_tape_state import _pct_change

    assert _pct_change(None, 100.0) is None


def test_pct_change_both_none_returns_none() -> None:
    from milodex.gui.market_tape_state import _pct_change

    assert _pct_change(None, None) is None


# ---------------------------------------------------------------------------
# Cache read helper tests — no Qt required
# ---------------------------------------------------------------------------


def _write_bars(cache_dir: Path, symbol: str, version: str, rows: list[dict]) -> None:
    """Write bars for a symbol via ParquetCache."""
    from milodex.data.cache import ParquetCache
    from milodex.data.models import Timeframe

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    cache = ParquetCache(cache_dir, version=version)
    cache.write(symbol, Timeframe.DAY_1, df)


def _make_bar_rows(symbol: str, *, closes: list[float], base_dt: datetime) -> list[dict]:
    """Generate minimal bar rows (timestamp + OHLCV + vwap)."""
    rows = []
    for i, close in enumerate(closes):
        ts = base_dt + timedelta(days=i)
        rows.append(
            {
                "timestamp": ts.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000_000,
                "vwap": close,
            }
        )
    return rows


def test_read_tape_present_symbols(tmp_path) -> None:
    """_read_tape returns correct close/pctChange/asOf for seeded symbols."""
    from milodex.gui.market_tape_state import _read_tape

    cache_dir = tmp_path / "market_cache"
    base_dt = datetime(2026, 5, 10, 0, 0, 0, tzinfo=UTC)

    # Seed SPY: two bars, close 500 → 525  (pctChange = 0.05)
    spy_bars = _make_bar_rows("SPY", closes=[500.0, 525.0], base_dt=base_dt)
    _write_bars(cache_dir, "SPY", "v1", spy_bars)
    # Seed QQQ: two bars, close 400 → 380  (pctChange = -0.05)
    qqq_bars = _make_bar_rows("QQQ", closes=[400.0, 380.0], base_dt=base_dt)
    _write_bars(cache_dir, "QQQ", "v1", qqq_bars)

    rows = _read_tape(cache_dir)
    assert isinstance(rows, list)
    assert len(rows) == len(SYMBOLS)

    spy = next(r for r in rows if r["symbol"] == "SPY")
    assert spy["close"] == pytest.approx(525.0)
    assert spy["pctChange"] == pytest.approx(0.05)
    assert spy["asOf"] is not None

    qqq = next(r for r in rows if r["symbol"] == "QQQ")
    assert qqq["close"] == pytest.approx(380.0)
    assert qqq["pctChange"] == pytest.approx(-0.05)

    # Symbols not seeded → no-data shape
    for sym in ("IWM", "TLT", "VIX"):
        entry = next(r for r in rows if r["symbol"] == sym)
        assert entry["close"] is None
        assert entry["pctChange"] is None
        assert entry["asOf"] is None


def test_read_tape_single_bar_no_pct_change(tmp_path) -> None:
    """A symbol with only one bar has close populated but pctChange=None."""
    from milodex.gui.market_tape_state import _read_tape

    cache_dir = tmp_path / "market_cache"
    base_dt = datetime(2026, 5, 14, 0, 0, 0, tzinfo=UTC)
    _write_bars(cache_dir, "SPY", "v1", _make_bar_rows("SPY", closes=[500.0], base_dt=base_dt))

    rows = _read_tape(cache_dir)
    spy = next(r for r in rows if r["symbol"] == "SPY")
    assert spy["close"] == pytest.approx(500.0)
    assert spy["pctChange"] is None
    assert spy["asOf"] is not None


def test_read_tape_symbol_order(tmp_path) -> None:
    """Rows are returned in the canonical SYMBOLS order."""
    from milodex.gui.market_tape_state import _read_tape

    cache_dir = tmp_path / "market_cache"
    base_dt = datetime(2026, 5, 14, 0, 0, 0, tzinfo=UTC)
    spy_bars = _make_bar_rows("SPY", closes=[500.0, 510.0], base_dt=base_dt)
    _write_bars(cache_dir, "SPY", "v1", spy_bars)

    rows = _read_tape(cache_dir)
    assert [r["symbol"] for r in rows] == list(SYMBOLS)


def test_read_tape_missing_cache_dir_raises_or_errors(tmp_path) -> None:
    """_read_tape raises an exception when the cache_dir does not exist."""
    from milodex.gui.market_tape_state import _read_tape

    missing = tmp_path / "nonexistent_cache"
    with pytest.raises(Exception):  # noqa: B017
        _read_tape(missing)


def test_read_tape_no_version_dir_raises(tmp_path) -> None:
    """_read_tape raises when cache_dir exists but contains no vN dirs."""
    from milodex.gui.market_tape_state import _read_tape

    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    (cache_dir / "1Day").mkdir()  # not a vN dir

    with pytest.raises(Exception):  # noqa: B017
        _read_tape(cache_dir)


# ---------------------------------------------------------------------------
# Qt-aware fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication."""
    if not _PYSIDE6_AVAILABLE:
        return None

    import os
    import sys

    from PySide6.QtGui import QGuiApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


def _make_state(cache_dir: Path | None = None, refresh_interval_ms: int = 99_999_999):
    from milodex.gui.market_tape_state import MarketTapeState

    return MarketTapeState(cache_dir=cache_dir, refresh_interval_ms=refresh_interval_ms)


def _wait_for_pool(state) -> None:
    state._thread_pool.waitForDone(2000)  # noqa: SLF001
    QCoreApplication.processEvents()
    QCoreApplication.processEvents()


# ---------------------------------------------------------------------------
# Qt lifecycle tests
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_initial_state_is_loading(qapp, tmp_path) -> None:
    """Before any refresh, dataStatus is 'loading'."""
    _ = qapp
    state = _make_state(tmp_path / "cache")

    assert state.dataStatus == "loading"
    assert state.rows == []
    assert state.lastRefreshedAt == ""
    assert state.dataErrorMessage == ""


@_skip_no_qt
def test_refresh_populates_rows(qapp, tmp_path) -> None:
    """After a successful refresh, rows is a list of symbol entries."""
    _ = qapp
    cache_dir = tmp_path / "market_cache"
    base_dt = datetime(2026, 5, 10, 0, 0, 0, tzinfo=UTC)
    spy_bars = _make_bar_rows("SPY", closes=[500.0, 525.0], base_dt=base_dt)
    _write_bars(cache_dir, "SPY", "v1", spy_bars)

    state = _make_state(cache_dir)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    rows = state.rows
    assert len(rows) == len(SYMBOLS)
    symbols_in_rows = [r["symbol"] for r in rows]
    assert symbols_in_rows == list(SYMBOLS)

    spy = next(r for r in rows if r["symbol"] == "SPY")
    assert spy["close"] == pytest.approx(525.0)
    assert spy["pctChange"] == pytest.approx(0.05)
    assert spy["asOf"] is not None

    state.stop()


@_skip_no_qt
def test_missing_cache_dir_sets_error_status(qapp, tmp_path) -> None:
    """Pointing at a missing cache dir sets dataStatus='error'."""
    _ = qapp
    state = _make_state(tmp_path / "does_not_exist")

    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "error"
    assert state.dataErrorMessage != ""
    assert state.rows == []

    state.stop()


@_skip_no_qt
def test_error_after_success_preserves_last_known(qapp, tmp_path) -> None:
    """After a successful refresh, a subsequent failure leaves last-known data intact."""
    _ = qapp
    cache_dir = tmp_path / "market_cache"
    base_dt = datetime(2026, 5, 10, 0, 0, 0, tzinfo=UTC)
    spy_bars = _make_bar_rows("SPY", closes=[500.0, 525.0], base_dt=base_dt)
    _write_bars(cache_dir, "SPY", "v1", spy_bars)

    state = _make_state(cache_dir)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    first_rows = list(state.rows)

    # Force error by pointing at missing cache
    state._cache_dir = tmp_path / "gone"  # noqa: SLF001
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "error"
    assert state.rows == first_rows  # last-known preserved

    state.stop()


@_skip_no_qt
def test_concurrent_kick_drops_when_in_flight(qapp, tmp_path) -> None:
    """A second _kick_refresh while one is in-flight is a no-op."""
    _ = qapp
    state = _make_state(tmp_path / "cache")

    state._refresh_in_flight = True  # noqa: SLF001
    pool_before = state._thread_pool.activeThreadCount()  # noqa: SLF001

    state._kick_refresh()  # noqa: SLF001
    assert state._thread_pool.activeThreadCount() == pool_before  # noqa: SLF001

    state._refresh_in_flight = False  # noqa: SLF001
    state.stop()


@_skip_no_qt
def test_stop_drains_in_flight_worker(qapp, tmp_path) -> None:
    """stop() must wait for in-flight workers before returning."""
    from milodex.gui.market_tape_state import _MarketTapeRefreshRunnable

    cache_dir = tmp_path / "market_cache"
    base_dt = datetime(2026, 5, 10, 0, 0, 0, tzinfo=UTC)
    spy_bars = _make_bar_rows("SPY", closes=[500.0, 525.0], base_dt=base_dt)
    _write_bars(cache_dir, "SPY", "v1", spy_bars)

    state = _make_state(cache_dir)

    release = threading.Event()
    worker_ran = threading.Event()

    original_run = _MarketTapeRefreshRunnable.run

    def slow_run(self):
        worker_ran.set()
        release.wait(timeout=5.0)
        original_run(self)

    _MarketTapeRefreshRunnable.run = slow_run

    try:
        state._kick_refresh()  # noqa: SLF001
        assert worker_ran.wait(timeout=3.0), "Worker did not start within 3s"

        # Schedule unblock *after* stop() starts so stop() must actually wait.
        threading.Timer(0.5, release.set).start()

        t0 = time.monotonic()
        state.stop()
        elapsed = time.monotonic() - t0

        assert state._thread_pool.activeThreadCount() == 0  # noqa: SLF001
        assert elapsed >= 0.4, f"stop() returned too fast ({elapsed:.2f}s) — drain not exercised"
        assert elapsed < 2.0, f"stop() took {elapsed:.2f}s — expected < 2s (hit timeout?)"
    finally:
        _MarketTapeRefreshRunnable.run = original_run

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
    """_read_tape raises RuntimeError when the cache_dir does not exist.

    The raised message must be path-free (no absolute filesystem path
    in the operator-facing string).
    """
    from milodex.gui.market_tape_state import _read_tape

    missing = tmp_path / "nonexistent_cache"
    with pytest.raises(RuntimeError, match="Market data unavailable"):
        _read_tape(missing)


def test_read_tape_missing_cache_dir_message_has_no_path(tmp_path) -> None:
    """The raised error message must not expose an absolute filesystem path."""
    from milodex.gui.market_tape_state import _read_tape

    missing = tmp_path / "nonexistent_cache"
    try:
        _read_tape(missing)
    except RuntimeError as exc:
        msg = str(exc)
        assert str(missing) not in msg, (
            f"Absolute path found in operator-facing error message: {msg!r}"
        )
    else:
        pytest.fail("_read_tape should have raised RuntimeError")


def test_read_tape_no_version_dir_raises(tmp_path) -> None:
    """_read_tape raises RuntimeError when cache_dir exists but contains no vN dirs.

    The raised message must be path-free.
    """
    from milodex.gui.market_tape_state import _read_tape

    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    (cache_dir / "1Day").mkdir()  # not a vN dir

    with pytest.raises(RuntimeError, match="Market data unavailable"):
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


# Lifecycle scaffold tests (missing-cache error, error-after-success preservation,
# in-flight drop, stop-drains-worker) were removed in PR C of RM-007 — those
# contracts are now covered ONCE in tests/milodex/gui/test_polling_lifecycle.py.

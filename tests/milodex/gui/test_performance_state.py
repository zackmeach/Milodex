"""Tests for :class:`milodex.gui.performance_state.PerformanceState`.

Mirrors the StrategyBankState test harness:

- Pure-logic helpers are tested without Qt.
- Full QObject lifecycle tests require a ``QGuiApplication`` and real (tmp-path)
  SQLite DB + Parquet cache.  Gated behind ``_skip_no_qt``.
- Tests drive the refresh cycle directly via ``_kick_refresh()``; the timer
  interval is set to 99 999 999 ms so it never fires in CI.
- Fixture DB schema matches ``portfolio_snapshots`` exactly.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# PySide6 availability
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QCoreApplication, QThreadPool  # noqa: F401
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping Qt-aware PerformanceState tests",
)

# ---------------------------------------------------------------------------
# Pure-logic helper tests — no Qt required
# ---------------------------------------------------------------------------


def test_period_return_normal() -> None:
    from milodex.gui.performance_state import _period_return

    result = _period_return([100.0, 110.0, 120.0])
    assert abs(result - 0.20) < 1e-9


def test_period_return_loss() -> None:
    from milodex.gui.performance_state import _period_return

    result = _period_return([100.0, 90.0])
    assert abs(result - (-0.10)) < 1e-9


def test_period_return_single_element() -> None:
    from milodex.gui.performance_state import _period_return

    assert _period_return([100.0]) is None


def test_period_return_empty() -> None:
    from milodex.gui.performance_state import _period_return

    assert _period_return([]) is None


def test_period_return_zero_start() -> None:
    from milodex.gui.performance_state import _period_return

    assert _period_return([0.0, 100.0]) is None


def test_max_drawdown_no_drawdown() -> None:
    from milodex.gui.performance_state import _max_drawdown

    result = _max_drawdown([100.0, 110.0, 120.0])
    assert result == 0.0


def test_max_drawdown_with_drawdown() -> None:
    from milodex.gui.performance_state import _max_drawdown

    # peak=120, trough=90: (90/120)-1 = -0.25
    result = _max_drawdown([100.0, 120.0, 90.0, 115.0])
    assert abs(result - (-0.25)) < 1e-9


def test_max_drawdown_empty() -> None:
    from milodex.gui.performance_state import _max_drawdown

    assert _max_drawdown([]) is None


def test_max_drawdown_returns_nonpositive() -> None:
    from milodex.gui.performance_state import _max_drawdown

    result = _max_drawdown([100.0, 80.0, 60.0])
    assert result is not None
    assert result <= 0.0


def test_is_stale_none_newest_is_not_stale() -> None:
    """Empty table (newest_iso=None) is NOT stale — it is 'no data'.

    Stale requires: a snapshot exists AND it is older than the threshold.
    An empty series is a separate state; conflating it with stale would render
    the wrong UI treatment (stale warning vs. honest 'no data yet').
    """
    from milodex.gui.performance_state import _is_stale

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    assert _is_stale(None, now) is False


def test_is_stale_exactly_two_days_fresh() -> None:
    """Exactly 2 days old is within threshold — fresh."""
    from milodex.gui.performance_state import _is_stale

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    newest_iso = (now - timedelta(days=2)).isoformat()
    assert _is_stale(newest_iso, now) is False


def test_is_stale_three_days_stale() -> None:
    """3 days old exceeds threshold — stale."""
    from milodex.gui.performance_state import _is_stale

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    newest_iso = (now - timedelta(days=3)).isoformat()
    assert _is_stale(newest_iso, now) is True


def test_is_stale_recent() -> None:
    from milodex.gui.performance_state import _is_stale

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    newest_iso = (now - timedelta(hours=6)).isoformat()
    assert _is_stale(newest_iso, now) is False


def test_empty_snapshot_not_stale() -> None:
    """(a) Empty snapshots → NOT stale. An empty series has no snapshot; it
    must not be reported as stale data — it is simply no data yet."""
    from milodex.gui.performance_state import _is_stale

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    # newest_iso=None means the portfolio_snapshots table returned no rows.
    assert _is_stale(None, now) is False, "empty series must NOT be flagged stale"


def test_snapshot_present_but_old_is_stale() -> None:
    """(b) Snapshot exists but is older than threshold → stale.

    This is the genuine stale case: we had data and it aged out.
    The existing stale path must be preserved intact.
    """
    from milodex.gui.performance_state import _is_stale

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    old_iso = (now - timedelta(days=5)).isoformat()  # 5 days > 2-day threshold
    assert _is_stale(old_iso, now) is True, "old snapshot must be flagged stale"


def test_fresh_snapshot_not_stale() -> None:
    """(c) Fresh snapshot (within threshold) → NOT stale."""
    from milodex.gui.performance_state import _is_stale

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    recent_iso = (now - timedelta(hours=4)).isoformat()
    assert _is_stale(recent_iso, now) is False, "recent snapshot must NOT be flagged stale"


def test_is_stale_tz_naive_input_does_not_raise() -> None:
    """A tz-naive ISO string from an upstream regression must not crash _is_stale.

    Without the tzinfo guard, ``datetime.now(tz=UTC) - naive_dt`` raises
    TypeError.  The fix clamps to UTC so the result degrades to a wrong-by-
    hours stale flag rather than a hard crash that kills every refresh cycle.
    """
    from milodex.gui.performance_state import _is_stale

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    # Naive ISO (no +00:00 suffix) — simulates a broken upstream snapshot
    naive_iso = "2026-05-16T06:00:00"
    # Must not raise; result value is acceptable as long as it's a bool
    result = _is_stale(naive_iso, now)
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Fixture DB helpers
# ---------------------------------------------------------------------------


def _create_fixture_db(path: Path) -> None:
    """Create a minimal SQLite DB with portfolio_snapshots (exact production schema)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            session_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            equity REAL NOT NULL,
            cash REAL NOT NULL,
            portfolio_value REAL NOT NULL,
            daily_pnl REAL NOT NULL,
            positions_json TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


_SNAPSHOT_COUNTER = 0


def _seed_snapshot(
    db: Path,
    recorded_at: str,
    equity: float,
    *,
    session_id: str = "sess-001",
    strategy_id: str = "default",
    cash: float = 0.0,
    portfolio_value: float | None = None,
    daily_pnl: float = 0.0,
    positions_json: str = "{}",
) -> None:
    """Insert one portfolio_snapshots row."""
    if portfolio_value is None:
        portfolio_value = equity
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO portfolio_snapshots
            (recorded_at, session_id, strategy_id, equity, cash,
             portfolio_value, daily_pnl, positions_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recorded_at,
            session_id,
            strategy_id,
            equity,
            cash,
            portfolio_value,
            daily_pnl,
            positions_json,
        ),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Pure _query_performance tests — no Qt required
# ---------------------------------------------------------------------------


def test_query_performance_slices_week_return(tmp_path) -> None:
    """Week slice return is computed correctly from seeded snapshots."""
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
    # 5 days ago → within Week window (7 days)
    five_days_ago = (now - timedelta(days=5)).isoformat()
    two_days_ago = (now - timedelta(days=2)).isoformat()
    yesterday = (now - timedelta(days=1)).isoformat()

    _seed_snapshot(db, five_days_ago, 100_000.0)
    _seed_snapshot(db, two_days_ago, 105_000.0)
    _seed_snapshot(db, yesterday, 110_000.0)

    result = _query_performance(db, now)

    by_slice = result["by_slice"]
    week = by_slice["Week"]
    # return = (110000 / 100000) - 1 = 0.10
    assert week["return"] is not None
    assert abs(week["return"] - 0.10) < 1e-9
    assert week["drawdown"] is not None
    assert week["drawdown"] <= 0.0


def test_query_performance_slices_today_is_placeholder(tmp_path) -> None:
    """Today slice is always placeholder zeros, regardless of DB contents."""
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
    _seed_snapshot(db, (now - timedelta(hours=2)).isoformat(), 100_000.0)

    result = _query_performance(db, now)
    today = result["by_slice"]["Today"]
    assert today["return"] is None
    assert today["drawdown"] is None


def test_query_performance_all_paper_uses_earliest_snapshot(tmp_path) -> None:
    """All-Paper window starts from the earliest snapshot."""
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
    six_months_ago = (now - timedelta(days=180)).isoformat()
    three_months_ago = (now - timedelta(days=90)).isoformat()
    yesterday = (now - timedelta(days=1)).isoformat()

    _seed_snapshot(db, six_months_ago, 80_000.0)
    _seed_snapshot(db, three_months_ago, 90_000.0)
    _seed_snapshot(db, yesterday, 100_000.0)

    result = _query_performance(db, now)
    all_paper = result["by_slice"]["All-Paper"]
    # return = (100000 / 80000) - 1 = 0.25
    assert all_paper["return"] is not None
    assert abs(all_paper["return"] - 0.25) < 1e-9


def test_query_performance_ytd_window(tmp_path) -> None:
    """YTD window starts from Jan 1 of the current year."""
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
    # Jan 2 — within YTD
    jan2 = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC).isoformat()
    # Dec 31 last year — outside YTD
    dec31 = datetime(2025, 12, 31, 0, 0, 0, tzinfo=UTC).isoformat()
    yesterday = (now - timedelta(days=1)).isoformat()

    _seed_snapshot(db, dec31, 90_000.0)   # outside YTD
    _seed_snapshot(db, jan2, 100_000.0)   # first in YTD
    _seed_snapshot(db, yesterday, 110_000.0)

    result = _query_performance(db, now)
    ytd = result["by_slice"]["YTD"]
    # return = (110000 / 100000) - 1 = 0.10 (dec31 excluded)
    assert ytd["return"] is not None
    assert abs(ytd["return"] - 0.10) < 1e-9


def test_query_performance_empty_db(tmp_path) -> None:
    """Empty portfolio_snapshots returns None returns/drawdowns and empty sparkline."""
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
    result = _query_performance(db, now)

    by_slice = result["by_slice"]
    for slice_name in ("Week", "Month", "YTD", "All-Paper"):
        assert by_slice[slice_name]["return"] is None, f"{slice_name} should be None"
    assert result["sparkline"] == []
    assert result["newest_recorded_at"] is None


def test_query_performance_duplicate_timestamp_takes_latest_id(tmp_path) -> None:
    """When two rows share a timestamp, the one with the highest id wins."""
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
    ts = (now - timedelta(days=3)).isoformat()

    # Same timestamp, different equity — higher id wins
    _seed_snapshot(db, ts, 100_000.0)  # id=1
    _seed_snapshot(db, ts, 105_000.0)  # id=2 — should win
    _seed_snapshot(db, (now - timedelta(days=1)).isoformat(), 110_000.0)

    result = _query_performance(db, now)
    week = result["by_slice"]["Week"]
    # Should use 105000 as start (highest id for that ts), not 100000
    assert week["return"] is not None
    expected_return = (110_000.0 / 105_000.0) - 1.0
    assert abs(week["return"] - expected_return) < 1e-9


def test_query_performance_missing_db_raises(tmp_path) -> None:
    """_query_performance raises when the DB path does not exist."""
    from milodex.gui.performance_state import _query_performance

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
    with pytest.raises(Exception):  # noqa: B017
        _query_performance(tmp_path / "nonexistent.db", now)


# ---------------------------------------------------------------------------
# Read-only connection test — no Qt required
# ---------------------------------------------------------------------------


def test_read_only_connection_blocks_writes(tmp_path) -> None:
    """Connecting with file:...?mode=ro raises OperationalError on write attempt."""
    db = tmp_path / "readonly_test.db"
    # Create the DB via normal connection
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.commit()
    conn.close()

    # Now open read-only and attempt DDL
    ro_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_conn.execute("CREATE TABLE x(a)")
    ro_conn.close()


# ---------------------------------------------------------------------------
# SPY benchmark tests — no Qt required
# ---------------------------------------------------------------------------


def _write_spy_parquet(cache_dir: Path, version: str, rows: list[dict]) -> None:
    """Write a tiny SPY parquet via ParquetCache for test isolation."""
    from milodex.data.cache import ParquetCache
    from milodex.data.models import Timeframe

    df = pd.DataFrame(rows)
    # timestamp must be tz-aware UTC for the schema
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    cache = ParquetCache(cache_dir, version=version)
    cache.write("SPY", Timeframe.DAY_1, df)


def test_latest_cache_version_picks_highest(tmp_path) -> None:
    """_latest_cache_version returns the highest vN dir among v2, v3, v10."""
    from milodex.gui._market_cache import _latest_cache_version

    (tmp_path / "v2").mkdir()
    (tmp_path / "v3").mkdir()
    (tmp_path / "v10").mkdir()
    (tmp_path / "1Day").mkdir()  # should be ignored — not vN

    result = _latest_cache_version(tmp_path)
    assert result == "v10"


def test_latest_cache_version_single_dir(tmp_path) -> None:
    from milodex.gui._market_cache import _latest_cache_version

    (tmp_path / "v2").mkdir()
    assert _latest_cache_version(tmp_path) == "v2"


def test_latest_cache_version_no_versioned_dirs_returns_none(tmp_path) -> None:
    from milodex.gui._market_cache import _latest_cache_version

    (tmp_path / "1Day").mkdir()
    assert _latest_cache_version(tmp_path) is None


def test_spy_benchmark_computed_per_slice(tmp_path) -> None:
    """SPY benchmark return and excess are computed correctly over a slice window."""
    from milodex.gui.performance_state import _query_performance

    cache_dir = tmp_path / "market_cache"
    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)

    # Seed portfolio: 10% Week return
    five_days_ago = (now - timedelta(days=5)).isoformat()
    yesterday = (now - timedelta(days=1)).isoformat()
    _seed_snapshot(db, five_days_ago, 100_000.0)
    _seed_snapshot(db, yesterday, 110_000.0)

    # Seed SPY: 5% over same window
    _write_spy_parquet(
        cache_dir,
        "v3",
        [
            {
                "timestamp": (now - timedelta(days=5)).isoformat(),
                "open": 500.0,
                "high": 505.0,
                "low": 498.0,
                "close": 500.0,
                "volume": 1_000_000,
                "vwap": 501.0,
            },
            {
                "timestamp": (now - timedelta(days=1)).isoformat(),
                "open": 520.0,
                "high": 525.0,
                "low": 518.0,
                "close": 525.0,
                "volume": 1_000_000,
                "vwap": 522.0,
            },
        ],
    )

    result = _query_performance(db, now, cache_dir=cache_dir)

    bbs = result["benchmark_by_slice"]
    week_bm = bbs["Week"]
    # SPY return = (525 / 500) - 1 = 0.05
    assert week_bm["spyReturn"] is not None
    assert abs(week_bm["spyReturn"] - 0.05) < 1e-9
    # excess = 0.10 - 0.05 = 0.05
    assert week_bm["excess"] is not None
    assert abs(week_bm["excess"] - 0.05) < 1e-9


def test_spy_benchmark_missing_cache_returns_none(tmp_path) -> None:
    """When SPY parquet is missing, benchmark returns are None."""
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
    _seed_snapshot(db, (now - timedelta(days=5)).isoformat(), 100_000.0)
    _seed_snapshot(db, (now - timedelta(days=1)).isoformat(), 110_000.0)

    # cache_dir with no SPY parquet
    cache_dir = tmp_path / "empty_cache"
    cache_dir.mkdir()

    result = _query_performance(db, now, cache_dir=cache_dir)
    for slice_name in ("Week", "Month", "YTD", "All-Paper"):
        bm = result["benchmark_by_slice"][slice_name]
        assert bm["spyReturn"] is None
        assert bm["excess"] is None


# ---------------------------------------------------------------------------
# Qt-aware fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication so QObject + QTimer + QThreadPool work."""
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


def _make_state(
    db_path: Path,
    cache_dir: Path | None = None,
    refresh_interval_ms: int = 99_999_999,
):
    """Construct a PerformanceState with a long interval so timers never fire."""
    from milodex.gui.performance_state import PerformanceState

    return PerformanceState(
        db_path=db_path,
        cache_dir=cache_dir,
        refresh_interval_ms=refresh_interval_ms,
    )


def _wait_for_pool(state) -> None:
    """Block until the state's thread pool drains, then process Qt events."""
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
    db = tmp_path / "perf.db"
    _create_fixture_db(db)
    state = _make_state(db)

    assert state.dataStatus == "loading"
    assert state.bySlice == {}
    assert state.benchmarkBySlice == {}
    assert state.sparkline == []
    assert state.isStale is False
    assert state.lastRefreshedAt == ""
    assert state.dataErrorMessage == ""


@_skip_no_qt
def test_refresh_populates_by_slice(qapp, tmp_path) -> None:
    """After a successful refresh, bySlice is populated."""
    _ = qapp
    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime.now(tz=UTC)
    five_days_ago = (now - timedelta(days=5)).isoformat()
    yesterday = (now - timedelta(days=1)).isoformat()
    _seed_snapshot(db, five_days_ago, 100_000.0)
    _seed_snapshot(db, yesterday, 110_000.0)

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    by_slice = state.bySlice
    assert "Week" in by_slice
    assert "Month" in by_slice
    assert "YTD" in by_slice
    assert "All-Paper" in by_slice
    assert "Today" in by_slice

    week = by_slice["Week"]
    assert "return" in week
    assert "drawdown" in week

    state.stop()


@_skip_no_qt
def test_missing_db_sets_error_status(qapp, tmp_path) -> None:
    """Pointing at a non-existent DB sets dataStatus='error'."""
    _ = qapp
    db = tmp_path / "does_not_exist.db"
    state = _make_state(db)

    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "error"
    assert state.dataErrorMessage != ""
    assert state.bySlice == {}

    state.stop()


@_skip_no_qt
def test_error_after_success_preserves_last_known(qapp, tmp_path) -> None:
    """After a successful refresh, a failure leaves the last-known data intact."""
    _ = qapp
    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    now = datetime.now(tz=UTC)
    _seed_snapshot(db, (now - timedelta(days=3)).isoformat(), 100_000.0)
    _seed_snapshot(db, (now - timedelta(days=1)).isoformat(), 110_000.0)

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    first_by_slice = state.bySlice

    # Force error by pointing at missing DB
    state._db_path = tmp_path / "gone.db"  # noqa: SLF001
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "error"
    # Last-known data preserved
    assert state.bySlice == first_by_slice

    state.stop()


@_skip_no_qt
def test_concurrent_kick_drops_when_in_flight(qapp, tmp_path) -> None:
    """A second _kick_refresh while one is in flight is a no-op."""
    _ = qapp
    db = tmp_path / "perf.db"
    _create_fixture_db(db)
    state = _make_state(db)

    state._refresh_in_flight = True  # noqa: SLF001 — simulate in-flight
    pool_before = state._thread_pool.activeThreadCount()  # noqa: SLF001

    state._kick_refresh()  # noqa: SLF001
    assert state._thread_pool.activeThreadCount() == pool_before  # noqa: SLF001

    state._refresh_in_flight = False  # noqa: SLF001
    state.stop()


@_skip_no_qt
def test_stop_drains_in_flight_worker(qapp, tmp_path) -> None:
    """stop() must wait for in-flight workers before returning."""
    import threading
    import time

    from milodex.gui.performance_state import _PerfRefreshRunnable

    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    state = _make_state(db)

    release = threading.Event()
    worker_ran = threading.Event()

    original_run = _PerfRefreshRunnable.run

    def slow_run(self):
        worker_ran.set()
        release.wait(timeout=5.0)
        original_run(self)

    _PerfRefreshRunnable.run = slow_run

    try:
        state._kick_refresh()  # noqa: SLF001
        assert worker_ran.wait(timeout=3.0), "Worker did not start within 3s"

        # Worker is now genuinely in-flight (blocking on release).  Schedule
        # the unblock *after* stop() starts so that stop() must actually wait.
        threading.Timer(0.5, release.set).start()

        t0 = time.monotonic()
        state.stop()
        elapsed = time.monotonic() - t0

        assert state._thread_pool.activeThreadCount() == 0  # noqa: SLF001
        assert elapsed >= 0.4, f"stop() returned too fast ({elapsed:.2f}s) — drain not exercised"
        assert elapsed < 2.0, f"stop() took {elapsed:.2f}s — expected < 2s (hit timeout?)"
    finally:
        _PerfRefreshRunnable.run = original_run


@_skip_no_qt
def test_stale_flag_exposed_on_state(qapp, tmp_path) -> None:
    """isStale is True (and hasSnapshot is True) when newest snapshot is older than 2 days."""
    _ = qapp
    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    # Seed a snapshot 5 days old — should be stale
    five_days_ago = (datetime.now(tz=UTC) - timedelta(days=5)).isoformat()
    _seed_snapshot(db, five_days_ago, 100_000.0)

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    assert state.hasSnapshot is True, "snapshot exists — hasSnapshot must be True"
    assert state.isStale is True, "old snapshot must be flagged stale"
    assert state.staleAsOf != ""

    state.stop()


@_skip_no_qt
def test_fresh_data_not_stale(qapp, tmp_path) -> None:
    """isStale is False (and hasSnapshot is True) when newest snapshot is within 2 days."""
    _ = qapp
    db = tmp_path / "perf.db"
    _create_fixture_db(db)

    yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    _seed_snapshot(db, yesterday, 100_000.0)

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    assert state.hasSnapshot is True, "snapshot exists — hasSnapshot must be True"
    assert state.isStale is False, "fresh snapshot must NOT be flagged stale"

    state.stop()


@_skip_no_qt
def test_empty_db_has_no_snapshot_not_stale(qapp, tmp_path) -> None:
    """Empty portfolio_snapshots → hasSnapshot=False, isStale=False.

    The empty-table case must NOT be treated as stale; it is 'no data yet.'
    Verifies the three-way distinction: empty ≠ stale ≠ fresh.
    """
    _ = qapp
    db = tmp_path / "perf.db"
    _create_fixture_db(db)
    # No rows inserted — empty table

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready", "empty DB is a valid ready state, not an error"
    assert state.hasSnapshot is False, "no rows → hasSnapshot must be False"
    assert state.isStale is False, "no rows → must NOT be flagged stale"
    assert state.staleAsOf == "", "no rows → staleAsOf must be empty"

    state.stop()


# ---------------------------------------------------------------------------
# ALL-PAPER invariant tests (ADR 0053 — migration 010 correctness guard)
#
# These tests use a real EventStore (migration 010 applied) with the pre-010
# mixed scenario: :w backtest rows, whole-period backtest rows, and a stray
# anomaly row alongside real broker rows. Post-migration, _SQL_ALL_PAPER only
# sees the 3 broker-only rows, so the return and drawdown are realistic.
# ---------------------------------------------------------------------------


def _seed_all_paper_scenario(db_path: Path) -> None:
    """Seed a migration-010 DB with the pre-010 mixed table scenario.

    After migration 010 runs:
    - :w rows → backtest_equity_snapshots (not visible to _SQL_ALL_PAPER)
    - whole-period backtest rows → backtest_equity_snapshots
    - broker rows → portfolio_snapshots (only these are visible)

    We insert rows BEFORE calling EventStore so the migration applies the
    split. Rows are inserted directly via sqlite3 into an already-migrated
    DB (the EventStore constructor applies migration 010 first).
    """
    import json

    from milodex.core.event_store import EventStore, PortfolioSnapshotEvent, BacktestEquitySnapshotEvent
    from datetime import UTC

    # Open EventStore — this applies migration 010 to the fresh DB
    store = EventStore(db_path)

    # 3 broker-only snapshot rows (realistic equity ~$100k)
    for i, equity in enumerate([100_200.0, 100_500.0, 100_800.0]):
        store.append_portfolio_snapshot(
            PortfolioSnapshotEvent(
                recorded_at=datetime(2026, 4, 27 + i, 21, 0, 0, tzinfo=UTC),
                session_id=f"broker-sess-{i}",
                strategy_id="test.strat.v1",
                equity=equity,
                cash=50_000.0,
                portfolio_value=equity,
                daily_pnl=float(i * 50),
                positions=[],
            )
        )

    # 5 :w walk-forward backtest rows (simulated equity, unrealistic ranges)
    for i, equity in enumerate([1_000.0, 1_100.0, 900.0, 1_050.0, 1_200.0]):
        store.append_backtest_equity_snapshot(
            BacktestEquitySnapshotEvent(
                recorded_at=datetime(2025, 12, i + 10, tzinfo=UTC),
                session_id=f"run-abc:w{i}",
                strategy_id="test.strat.v1",
                equity=equity,
                cash=500.0,
                portfolio_value=equity,
                daily_pnl=None,
                positions=[],
            )
        )

    # 1 stray anomaly (whole-period backtest, high equity)
    store.append_backtest_equity_snapshot(
        BacktestEquitySnapshotEvent(
            recorded_at=datetime(2024, 12, 31, tzinfo=UTC),
            session_id="stray-session-anomaly",
            strategy_id="test.strat.v1",
            equity=149_315.0,
            cash=10_000.0,
            portfolio_value=149_315.0,
            daily_pnl=None,
            positions=[],
        )
    )


def test_all_paper_return_is_realistic_post_migration(tmp_path: Path) -> None:
    """ALL-PAPER return must be realistic after migration 010.

    Pre-010, the +9865% bug arose because earliest row was a backtest
    starting equity (~$1015) and latest was broker equity (~$101k).
    Post-010, portfolio_snapshots only contains broker rows. With three
    broker rows ranging $100200→$100800, the realistic return is ~0.6%.

    Guard: return must be between -50% and +100% (sanity bounds for a
    paper account that's been running a few weeks/months).
    """
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "milodex.db"
    _seed_all_paper_scenario(db)

    now = datetime(2026, 5, 20, 18, 0, 0, tzinfo=UTC)
    result = _query_performance(db, now)

    all_paper = result["by_slice"]["All-Paper"]
    ret = all_paper["return"]

    assert ret is not None, "ALL-PAPER return must not be None with broker snapshot rows present"
    assert ret > -0.50, f"ALL-PAPER return {ret:.2%} is below realistic lower bound (-50%)"
    assert ret < 1.00, f"ALL-PAPER return {ret:.2%} is above realistic upper bound (+100%)"


def test_all_paper_drawdown_is_realistic_post_migration(tmp_path: Path) -> None:
    """ALL-PAPER drawdown must be realistic after migration 010.

    Pre-010, the drawdown was -98.99% because the equity series spanned
    from ~$1015 to ~$101k with a backtest low in between. Post-010, only
    broker rows are in portfolio_snapshots, so drawdown reflects the
    actual broker account trajectory.

    Guard: drawdown must be between -50% and 0% (sanity bounds).
    """
    from milodex.gui.performance_state import _query_performance

    db = tmp_path / "milodex.db"
    _seed_all_paper_scenario(db)

    now = datetime(2026, 5, 20, 18, 0, 0, tzinfo=UTC)
    result = _query_performance(db, now)

    all_paper = result["by_slice"]["All-Paper"]
    dd = all_paper["drawdown"]

    assert dd is not None, "ALL-PAPER drawdown must not be None with broker snapshot rows present"
    assert dd >= -0.50, f"ALL-PAPER drawdown {dd:.2%} is below realistic lower bound (-50%)"
    assert dd <= 0.0, f"ALL-PAPER drawdown {dd:.2%} must be non-positive"

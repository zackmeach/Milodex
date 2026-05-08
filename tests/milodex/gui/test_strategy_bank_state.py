"""Tests for :class:`milodex.gui.strategy_bank_state.StrategyBankState`.

Mirrors :mod:`tests.milodex.gui.test_operational_state` in structure and
conventions:

- Pure-logic helpers (``_compute_gate_failures``, ``_query_bank``) are tested
  without Qt.
- Full QObject lifecycle tests require a ``QGuiApplication`` and a real
  (tmp-path) SQLite DB.  They are gated behind ``_skip_no_qt`` when PySide6
  is absent.
- Tests drive the refresh cycle directly via ``_kick_refresh()`` rather than
  sleeping for QTimer ticks — keeps the suite fast and deterministic.
- The fixture DB uses a minimal schema (just the tables the queries need) so
  tests are hermetic.  ``data/milodex.db`` is never touched.

Fixture DB schema: the two tables the SQL queries need — ``promotions`` and
``backtest_runs``.  Column set matches migrations 003 and 004 exactly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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
    reason="PySide6 not installed — skipping Qt-aware StrategyBankState tests",
)

# ---------------------------------------------------------------------------
# Pure-logic helpers — no Qt required
# ---------------------------------------------------------------------------


def test_compute_gate_failures_all_pass() -> None:
    """No failures when all three metrics clear their thresholds."""
    from milodex.gui.strategy_bank_state import _compute_gate_failures

    assert _compute_gate_failures(sharpe=0.6, max_dd=10.0, trade_count=50) == []


def test_compute_gate_failures_sharpe_only() -> None:
    """Only [S] when Sharpe fails but DD and trades pass."""
    from milodex.gui.strategy_bank_state import _compute_gate_failures

    result = _compute_gate_failures(sharpe=0.2, max_dd=10.0, trade_count=50)
    assert result == ["S"]


def test_compute_gate_failures_drawdown_only() -> None:
    """Only [D] when MaxDD fails but Sharpe and trades pass."""
    from milodex.gui.strategy_bank_state import _compute_gate_failures

    result = _compute_gate_failures(sharpe=0.8, max_dd=20.0, trade_count=50)
    assert result == ["D"]


def test_compute_gate_failures_trades_only() -> None:
    """Only [N] when trade count fails but Sharpe and DD pass."""
    from milodex.gui.strategy_bank_state import _compute_gate_failures

    result = _compute_gate_failures(sharpe=0.8, max_dd=10.0, trade_count=15)
    assert result == ["N"]


def test_compute_gate_failures_all_fail() -> None:
    """All three codes when all three gates fail."""
    from milodex.gui.strategy_bank_state import _compute_gate_failures

    result = _compute_gate_failures(sharpe=0.1, max_dd=20.0, trade_count=5)
    assert result == ["S", "D", "N"]


@pytest.mark.parametrize(
    ("sharpe", "max_dd", "trade_count", "expected"),
    [
        # dual_absolute scenario: passes Sharpe, fails D and N
        (0.83, 17.88, 20, ["D", "N"]),
        # seasonality scenario: fails only Sharpe
        (-0.27, 11.59, 40, ["S"]),
        # 52w_high scenario: fails Sharpe and DD
        (0.16, 16.44, 769, ["S", "D"]),
        # At-threshold values — gate uses sharpe < 0.5 (strict), dd >= 15.0 (strict), trades < 30
        # sharpe=0.5 is NOT < 0.5, so Sharpe passes; dd=15.0 IS >= 15.0, so DD fails.
        (0.5, 15.0, 30, ["D"]),
        (0.51, 14.99, 30, []),  # strictly passing all three
        # None metrics → all fail
        (None, None, None, ["S", "D", "N"]),
    ],
)
def test_gate_failure_codes_computed_correctly(sharpe, max_dd, trade_count, expected) -> None:
    """Gate-failure codes are derived correctly across the realistic metric space."""
    from milodex.gui.strategy_bank_state import _compute_gate_failures

    result = _compute_gate_failures(sharpe, max_dd, trade_count)
    assert result == expected


# ---------------------------------------------------------------------------
# Fixture DB helpers
# ---------------------------------------------------------------------------


def _create_fixture_db(path: Path) -> None:
    """Create a minimal SQLite DB with the two tables the queries need."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            from_stage TEXT NOT NULL,
            to_stage TEXT NOT NULL,
            promotion_type TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            backtest_run_id TEXT,
            sharpe_ratio REAL,
            max_drawdown_pct REAL,
            trade_count INTEGER,
            notes TEXT
        );

        CREATE TABLE backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            strategy_id TEXT NOT NULL,
            config_path TEXT,
            config_hash TEXT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL,
            slippage_pct REAL,
            commission_per_trade REAL,
            metadata_json TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _seed_paper_row(
    path: Path,
    strategy_id: str,
    promotion_type: str = "statistical",
    sharpe: float = 0.70,
    max_dd: float = 5.0,
    trade_count: int = 100,
    recorded_at: str = "2026-05-07T00:00:00+00:00",
) -> None:
    """Insert one paper-stage promotion record."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        INSERT INTO promotions
            (recorded_at, strategy_id, from_stage, to_stage, promotion_type,
             approved_by, backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count)
        VALUES (?, ?, 'backtest', 'paper', ?, 'test', NULL, ?, ?, ?)
        """,
        (recorded_at, strategy_id, promotion_type, sharpe, max_dd, trade_count),
    )
    conn.commit()
    conn.close()


def _seed_blocked_row(
    path: Path,
    strategy_id: str,
    run_id: str,
    sharpe: float = 0.2,
    max_dd: float = 10.0,
    trade_count: int = 50,
    started_at: str = "2026-05-07T00:00:00+00:00",
) -> None:
    """Insert one completed backtest_run record for a blocked strategy."""
    metadata = {
        "oos_aggregate": {
            "sharpe": sharpe,
            "max_drawdown_pct": max_dd,
            "trade_count": trade_count,
        }
    }
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, strategy_id, start_date, end_date, started_at, status, metadata_json)
        VALUES (?, ?, '2020-01-01', '2024-12-31', ?, 'completed', ?)
        """,
        (run_id, strategy_id, started_at, json.dumps(metadata)),
    )
    conn.commit()
    conn.close()


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


def _make_state(db_path: Path, refresh_interval_ms: int = 99_999_999):
    """Construct a StrategyBankState with a long interval so timers never fire.

    Tests drive _kick_refresh() explicitly.
    """
    from milodex.gui.strategy_bank_state import StrategyBankState

    return StrategyBankState(db_path=db_path, refresh_interval_ms=refresh_interval_ms)


def _wait_for_pool(state) -> None:
    """Block until the state's thread pool drains, then process Qt events."""
    state._thread_pool.waitForDone(2000)  # noqa: SLF001
    QCoreApplication.processEvents()
    QCoreApplication.processEvents()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_initial_state_is_loading_with_empty_lists(qapp, tmp_path) -> None:
    """Before any refresh, dataStatus is 'loading' and both lists are empty."""
    _ = qapp
    db = tmp_path / "test.db"
    _create_fixture_db(db)
    state = _make_state(db)

    assert state.dataStatus == "loading"
    assert state.paperStrategies == []
    assert state.blockedStrategies == []
    assert state.lastRefreshedAt == ""
    assert state.dataErrorMessage == ""


@_skip_no_qt
def test_refresh_populates_paper_and_blocked_lists(qapp, tmp_path) -> None:
    """After a successful refresh, paper and blocked lists reflect DB contents."""
    _ = qapp
    db = tmp_path / "test.db"
    _create_fixture_db(db)
    _seed_paper_row(db, "breakout.daily.atr_channel.sector_etfs.v1", sharpe=0.64)
    _seed_blocked_row(db, "seasonality.daily.turn_of_month.spy.v1", "run-001", sharpe=-0.27)

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    assert len(state.paperStrategies) == 1
    assert state.paperStrategies[0]["strategyId"] == "breakout.daily.atr_channel.sector_etfs.v1"
    assert state.paperStrategies[0]["promotionType"] == "statistical"
    assert abs(state.paperStrategies[0]["sharpeRatio"] - 0.64) < 1e-9

    assert len(state.blockedStrategies) == 1
    assert state.blockedStrategies[0]["strategyId"] == "seasonality.daily.turn_of_month.spy.v1"
    assert state.blockedStrategies[0]["gateFailures"] == ["S"]  # Sharpe -0.27 fails only Sharpe

    state.stop()


@_skip_no_qt
def test_db_unavailable_sets_error_status_preserves_last_known(qapp, tmp_path) -> None:
    """Pointing at a non-existent DB sets dataStatus='error', message non-empty, lists empty."""
    _ = qapp
    db = tmp_path / "does_not_exist.db"
    state = _make_state(db)

    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "error"
    assert state.dataErrorMessage != ""
    # Lists were never populated — stay empty (not cleared, just never set)
    assert state.paperStrategies == []
    assert state.blockedStrategies == []

    state.stop()


@_skip_no_qt
def test_db_error_after_success_preserves_last_known(qapp, tmp_path) -> None:
    """After a successful refresh, a subsequent failure leaves old data intact."""
    _ = qapp
    db = tmp_path / "test.db"
    _create_fixture_db(db)
    _seed_paper_row(db, "momentum.daily.tsmom.curated_largecap.v1", sharpe=0.88)

    state = _make_state(db)

    # First refresh: succeeds
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)
    assert state.dataStatus == "ready"
    assert len(state.paperStrategies) == 1

    # Point the state at a missing DB to force failure on next refresh
    state._db_path = tmp_path / "gone.db"  # noqa: SLF001
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "error"
    # Last-known paper data is preserved
    assert len(state.paperStrategies) == 1
    assert state.paperStrategies[0]["strategyId"] == "momentum.daily.tsmom.curated_largecap.v1"

    state.stop()


@_skip_no_qt
def test_concurrent_refresh_kicks_drop_when_in_flight(qapp, tmp_path) -> None:
    """A second _kick_refresh while one is in flight is a no-op (no pile-up)."""
    _ = qapp
    db = tmp_path / "test.db"
    _create_fixture_db(db)
    state = _make_state(db)

    state._refresh_in_flight = True  # noqa: SLF001 — simulate in-flight
    pool_queue_before = state._thread_pool.activeThreadCount()  # noqa: SLF001

    state._kick_refresh()  # noqa: SLF001
    # No new task was submitted; active count unchanged (still 0 because
    # pool isn't actually running anything — we just set the flag).
    assert state._thread_pool.activeThreadCount() == pool_queue_before  # noqa: SLF001

    state._refresh_in_flight = False  # noqa: SLF001
    state.stop()


@_skip_no_qt
def test_stop_drains_in_flight_worker(qapp, tmp_path) -> None:
    """stop() must wait for in-flight DB workers before returning.

    Mirrors the equivalent OperationalState test.  A slow DB operation is
    simulated by monkeypatching the connection factory inside the runnable.
    We use a threading.Event to block the worker, verify it started, then
    release it and check stop() completes.
    """
    import threading
    import time

    from milodex.gui.strategy_bank_state import _BankRefreshRunnable

    db = tmp_path / "test.db"
    _create_fixture_db(db)

    state = _make_state(db)

    release = threading.Event()
    worker_ran = threading.Event()

    original_run = _BankRefreshRunnable.run

    def slow_run(self):
        worker_ran.set()
        release.wait(timeout=5.0)
        original_run(self)

    _BankRefreshRunnable.run = slow_run

    try:
        state._kick_refresh()  # noqa: SLF001
        assert worker_ran.wait(timeout=3.0), "Worker did not start within 3s"

        release.set()
        t0 = time.monotonic()
        state.stop()
        elapsed = time.monotonic() - t0

        assert state._thread_pool.activeThreadCount() == 0  # noqa: SLF001
        assert elapsed < 2.0, f"stop() took {elapsed:.2f}s — expected < 2s"
    finally:
        _BankRefreshRunnable.run = original_run


@_skip_no_qt
def test_lifecycle_exempt_promotion_type_passes_through(qapp, tmp_path) -> None:
    """A regime row with promotion_type='lifecycle_exempt' surfaces correctly."""
    _ = qapp
    db = tmp_path / "test.db"
    _create_fixture_db(db)
    _seed_paper_row(
        db,
        "regime.daily.sma200_rotation.spy_shy.v1",
        promotion_type="lifecycle_exempt",
        sharpe=1.19,
        trade_count=27,
    )

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    assert len(state.paperStrategies) == 1
    row = state.paperStrategies[0]
    assert row["strategyId"] == "regime.daily.sma200_rotation.spy_shy.v1"
    assert row["promotionType"] == "lifecycle_exempt"

    state.stop()


@_skip_no_qt
def test_audit_flag_set_for_pullback_rsi2(qapp, tmp_path) -> None:
    """The pullback_rsi2 strategy carries auditFlag=True (ADR 0032)."""
    _ = qapp
    db = tmp_path / "test.db"
    _create_fixture_db(db)
    _seed_paper_row(db, "meanrev.daily.pullback_rsi2.curated_largecap.v1", sharpe=0.73)
    _seed_paper_row(db, "momentum.daily.tsmom.curated_largecap.v1", sharpe=0.88)

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    paper = {r["strategyId"]: r for r in state.paperStrategies}
    assert paper["meanrev.daily.pullback_rsi2.curated_largecap.v1"]["auditFlag"] is True
    assert paper["momentum.daily.tsmom.curated_largecap.v1"]["auditFlag"] is False

    state.stop()


@_skip_no_qt
def test_flagged_not_retired_set_for_dual_absolute(qapp, tmp_path) -> None:
    """dual_absolute.gem_weekly carries flagFailingNotRetired=True."""
    _ = qapp
    db = tmp_path / "test.db"
    _create_fixture_db(db)
    _seed_blocked_row(
        db,
        "momentum.daily.dual_absolute.gem_weekly.v1",
        "run-dual",
        sharpe=0.83,
        max_dd=17.88,
        trade_count=20,
    )
    _seed_blocked_row(
        db,
        "seasonality.daily.turn_of_month.spy.v1",
        "run-season",
        sharpe=-0.27,
        max_dd=11.59,
        trade_count=40,
    )

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    blocked = {r["strategyId"]: r for r in state.blockedStrategies}
    dual = blocked["momentum.daily.dual_absolute.gem_weekly.v1"]
    assert dual["flagFailingNotRetired"] is True
    assert dual["gateFailures"] == ["D", "N"]  # Sharpe 0.83 passes, D and N fail

    season = blocked["seasonality.daily.turn_of_month.spy.v1"]
    assert season["flagFailingNotRetired"] is False

    state.stop()


# ---------------------------------------------------------------------------
# Pure-logic _query_bank without Qt
# ---------------------------------------------------------------------------


def test_query_bank_returns_correct_structure(tmp_path) -> None:
    """_query_bank returns (paper_list, blocked_list) with expected fields."""
    from milodex.gui.strategy_bank_state import _query_bank

    db = tmp_path / "test.db"
    _create_fixture_db(db)
    _seed_paper_row(db, "breakout.daily.atr_channel.sector_etfs.v1", sharpe=0.64)
    _seed_blocked_row(db, "seasonality.daily.turn_of_month.spy.v1", "run-x", sharpe=-0.27)

    paper, blocked = _query_bank(db)

    assert len(paper) == 1
    assert paper[0]["strategyId"] == "breakout.daily.atr_channel.sector_etfs.v1"
    assert "promotionType" in paper[0]
    assert "auditFlag" in paper[0]

    assert len(blocked) == 1
    assert blocked[0]["strategyId"] == "seasonality.daily.turn_of_month.spy.v1"
    assert "gateFailures" in blocked[0]
    assert "flagFailingNotRetired" in blocked[0]


def test_query_bank_missing_db_raises(tmp_path) -> None:
    """_query_bank raises when the DB path does not exist."""
    from milodex.gui.strategy_bank_state import _query_bank

    with pytest.raises(Exception):  # noqa: B017 — sqlite3.OperationalError subtype
        _query_bank(tmp_path / "nonexistent.db")

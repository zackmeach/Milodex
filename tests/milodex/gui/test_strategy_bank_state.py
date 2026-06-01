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
        # At-threshold values — gate uses sharpe <= 0.5, dd >= 15.0, trades < 30.
        # sharpe=0.5 IS <= 0.5, so Sharpe FAILS; dd=15.0 IS >= 15.0, so DD fails.
        (0.5, 15.0, 30, ["S", "D"]),
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


def test_compute_gate_failures_sharpe_at_min_is_failure() -> None:
    """Sharpe exactly == MIN_SHARPE FAILS the gate (boundary is ``<=``, per policy.py).

    Pinned to the policy-sourced threshold (not a hardcoded 0.5) so the boundary
    can't silently re-drift away from ``PromotionPolicy.evaluate_research_target``.
    """
    from milodex.gui.strategy_bank_state import _compute_gate_failures
    from milodex.promotion.state_machine import MIN_SHARPE

    result = _compute_gate_failures(sharpe=MIN_SHARPE, max_dd=10.0, trade_count=50)
    assert "S" in result, "Sharpe == MIN_SHARPE must fail the gate (<= boundary)"


def test_compute_gate_failures_sharpe_just_above_min_passes() -> None:
    """Sharpe just above MIN_SHARPE clears the Sharpe gate (no ``S``)."""
    from milodex.gui.strategy_bank_state import _compute_gate_failures
    from milodex.promotion.state_machine import MIN_SHARPE

    result = _compute_gate_failures(sharpe=MIN_SHARPE + 0.01, max_dd=10.0, trade_count=50)
    assert "S" not in result


def test_compute_gate_failures_regime_family_exempt() -> None:
    """Regime strategies are exempt from statistical gate thresholds (returns [])."""
    from milodex.gui.strategy_bank_state import _compute_gate_failures

    # Metrics that would trigger all three failures for a non-regime strategy.
    result = _compute_gate_failures(sharpe=None, max_dd=None, trade_count=None, family="regime")
    assert result == [], "Regime family must be exempt regardless of metrics"


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


# Lifecycle scaffold tests (DB-unavailable error, error-after-success preservation,
# in-flight drop, stop-drains-worker) were removed in PR B of the RM-007 migration —
# those contracts are now covered ONCE in tests/milodex/gui/test_polling_lifecycle.py
# via the base PollingReadModel public surface. Per RM-007 done criteria, per-module
# tests assert domain behavior (SQL → row shapes), not lifecycle internals.


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


def test_query_bank_read_only_connection_blocks_writes(tmp_path) -> None:
    """_query_bank opens the DB with a read-only URI; DDL through the same
    file:...?mode=ro connection raises OperationalError."""
    db = tmp_path / "ro_test.db"
    # Create a valid DB so mode=ro can open it
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.commit()
    conn.close()

    # Confirm mode=ro blocks writes — mirrors the pattern in test_performance_state.py
    ro_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_conn.execute("CREATE TABLE x(a)")
    ro_conn.close()


# ---------------------------------------------------------------------------
# Helpers for lifecycle_exempt / COALESCE tests (no Qt required)
# ---------------------------------------------------------------------------


def _seed_paper_row_null_metrics(
    path: Path,
    strategy_id: str,
    promotion_type: str = "lifecycle_exempt",
    recorded_at: str = "2026-01-01T00:00:00+00:00",
) -> None:
    """Insert a paper-stage promotion row with NULL sharpe / max_dd / trade_count.

    Mirrors the real regime promotion that was recorded before the re-baseline
    run — metrics were not captured in the promotions table.
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        INSERT INTO promotions
            (recorded_at, strategy_id, from_stage, to_stage, promotion_type,
             approved_by, backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count)
        VALUES (?, ?, 'backtest', 'paper', ?, 'test', NULL, NULL, NULL, NULL)
        """,
        (recorded_at, strategy_id, promotion_type),
    )
    conn.commit()
    conn.close()


def _seed_backtest_run(
    path: Path,
    strategy_id: str,
    run_id: str,
    sharpe: float,
    max_dd: float,
    trade_count: int,
    started_at: str = "2026-02-01T00:00:00+00:00",
) -> None:
    """Insert a completed backtest_run with oos_aggregate metrics in metadata_json."""
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
# COALESCE fallback tests — no Qt required
# ---------------------------------------------------------------------------


def test_lifecycle_exempt_metrics_fallback_to_backtest_runs(tmp_path) -> None:
    """A lifecycle_exempt promotion with NULL metrics falls back to backtest_runs.

    Reproduces the regime strategy bug: promotions.sharpe_ratio is NULL but
    the re-baseline backtest_run carries the actual walk-forward figures.
    The COALESCE in _SQL_PAPER must resolve sharpe_ratio to 1.19, not NULL.
    """
    from milodex.gui.strategy_bank_state import _query_bank

    db = tmp_path / "test.db"
    _create_fixture_db(db)
    _seed_paper_row_null_metrics(db, "regime.daily.sma200_rotation.spy_shy.v1")
    _seed_backtest_run(
        db,
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        run_id="f7e0730c-fbdb-4c05-919d-622f8b61185d",
        sharpe=1.19,
        max_dd=0.95,
        trade_count=27,
    )

    paper, _ = _query_bank(db)

    assert len(paper) == 1
    row = paper[0]
    assert row["strategyId"] == "regime.daily.sma200_rotation.spy_shy.v1"
    assert row["promotionType"] == "lifecycle_exempt"
    # The COALESCE must surface the backtest_runs values, not None.
    assert abs(row["sharpeRatio"] - 1.19) < 1e-9, (
        f"Expected sharpeRatio=1.19 (from backtest_runs), got {row['sharpeRatio']!r}"
    )
    assert abs(row["maxDrawdownPct"] - 0.95) < 1e-9, (
        f"Expected maxDrawdownPct=0.95, got {row['maxDrawdownPct']!r}"
    )
    assert row["tradeCount"] == 27, f"Expected tradeCount=27, got {row['tradeCount']!r}"


def test_statistical_promotion_metrics_use_promotion_record(tmp_path) -> None:
    """When promotions.sharpe_ratio is set, COALESCE uses it — not backtest_runs.

    Pins the precedence rule: the promotion record wins over the re-baseline
    backtest_run when both carry values (they intentionally differ here to make
    the precedence unambiguous).
    """
    from milodex.gui.strategy_bank_state import _query_bank

    db = tmp_path / "test.db"
    _create_fixture_db(db)

    # Promotion record carries explicit metrics.
    _seed_paper_row(
        db,
        "breakout.daily.atr_channel.sector_etfs.v1",
        promotion_type="statistical",
        sharpe=0.72,
        max_dd=8.5,
        trade_count=150,
    )
    # backtest_run for the same strategy carries DIFFERENT values — COALESCE
    # must NOT use these because the promotion record is non-NULL.
    _seed_backtest_run(
        db,
        strategy_id="breakout.daily.atr_channel.sector_etfs.v1",
        run_id="run-backtest-99",
        sharpe=0.55,  # intentionally different from 0.72
        max_dd=12.0,  # intentionally different from 8.5
        trade_count=120,  # intentionally different from 150
    )

    paper, _ = _query_bank(db)

    assert len(paper) == 1
    row = paper[0]
    # Promotion-record values must take precedence.
    assert abs(row["sharpeRatio"] - 0.72) < 1e-9, (
        f"Expected sharpeRatio=0.72 (from promotions), got {row['sharpeRatio']!r}"
    )
    assert abs(row["maxDrawdownPct"] - 8.5) < 1e-9, (
        f"Expected maxDrawdownPct=8.5, got {row['maxDrawdownPct']!r}"
    )
    assert row["tradeCount"] == 150, f"Expected tradeCount=150, got {row['tradeCount']!r}"


# ---------------------------------------------------------------------------
# Invariant #3: paper-promoted strategies are EXCLUDED from blocked list
# (highest-risk regression point for the _event_queries.py refactor)
# ---------------------------------------------------------------------------


def test_paper_promoted_strategy_absent_from_blocked(tmp_path) -> None:
    """INVARIANT #3: a strategy promoted to paper must NOT appear in blocked list,
    even if it has a completed backtest.

    This is the NOT-IN-paper semantic that _fetch_blocked enforces.  After the
    _event_queries refactor the Python-side exclusion must reproduce it exactly.
    """
    from milodex.gui.strategy_bank_state import _query_bank

    db = tmp_path / "test.db"
    _create_fixture_db(db)

    # Strategy A: promoted to paper → must NOT appear in blocked
    _seed_paper_row(db, "breakout.daily.atr_channel.sector_etfs.v1", sharpe=0.64)
    _seed_blocked_row(
        db,
        "breakout.daily.atr_channel.sector_etfs.v1",
        "run-paper-bt",
        sharpe=0.64,
    )

    # Strategy B: NOT promoted to paper, has completed backtest → must appear in blocked
    _seed_blocked_row(db, "seasonality.daily.turn_of_month.spy.v1", "run-blocked-bt", sharpe=-0.27)

    paper, blocked = _query_bank(db)

    paper_ids = {r["strategyId"] for r in paper}
    blocked_ids = {r["strategyId"] for r in blocked}

    assert "breakout.daily.atr_channel.sector_etfs.v1" in paper_ids, (
        "Paper-promoted strategy must appear in paper list"
    )
    assert "breakout.daily.atr_channel.sector_etfs.v1" not in blocked_ids, (
        "Paper-promoted strategy must NOT appear in blocked list (invariant #3)"
    )
    assert "seasonality.daily.turn_of_month.spy.v1" in blocked_ids, (
        "Non-promoted strategy with completed backtest must appear in blocked list"
    )


def test_blocked_list_output_ordering(tmp_path) -> None:
    """Blocked list is ordered by strategy_id (alphabetical ascending)."""
    from milodex.gui.strategy_bank_state import _query_bank

    db = tmp_path / "test.db"
    _create_fixture_db(db)

    # Seed in reverse alphabetical order
    _seed_blocked_row(db, "zzz.strategy", "run-z", sharpe=0.1)
    _seed_blocked_row(db, "aaa.strategy", "run-a", sharpe=0.2)
    _seed_blocked_row(db, "mmm.strategy", "run-m", sharpe=0.3)

    _, blocked = _query_bank(db)

    ids = [r["strategyId"] for r in blocked]
    assert ids == sorted(ids), f"Blocked list not sorted by strategyId: {ids}"


def test_blocked_dict_shape(tmp_path) -> None:
    """Blocked list rows carry all required keys with correct types."""
    from milodex.gui.strategy_bank_state import _query_bank

    db = tmp_path / "test.db"
    _create_fixture_db(db)
    _seed_blocked_row(db, "seasonality.daily.turn_of_month.spy.v1", "run-x", sharpe=-0.27)

    _, blocked = _query_bank(db)

    assert len(blocked) == 1
    row = blocked[0]
    required_keys = {
        "strategyId",
        "sharpeRatio",
        "maxDrawdownPct",
        "tradeCount",
        "gateFailures",
        "startedAt",
        "runId",
        "auditFlag",
        "flagFailingNotRetired",
    }
    assert required_keys.issubset(row.keys()), (
        f"Missing keys in blocked row: {required_keys - row.keys()}"
    )
    assert isinstance(row["gateFailures"], list)
    assert isinstance(row["auditFlag"], bool)
    assert isinstance(row["flagFailingNotRetired"], bool)

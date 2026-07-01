"""Tests for :class:`milodex.gui.risk_throughput_state.RiskThroughputState`.

Mirrors the PerformanceState test harness:

- Pure-logic helpers are tested without Qt.
- Full QObject lifecycle tests require a QGuiApplication and real (tmp-path)
  SQLite DB. Gated behind ``_skip_no_qt``.
- Tests drive the refresh cycle directly via ``_kick_refresh()``.
- Fixture DB schema matches the real explanations + trades schema exactly.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
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
    reason="PySide6 not installed — skipping Qt-aware RiskThroughputState tests",
)

# ---------------------------------------------------------------------------
# Fixture DB helpers
# ---------------------------------------------------------------------------

_EXPL_DEFAULTS = dict(
    decision_type="submit",
    status="submitted",
    strategy_name="s1",
    strategy_stage="paper",
    strategy_config_path=None,
    config_hash=None,
    symbol="AAPL",
    side="buy",
    quantity=1.0,
    order_type="market",
    time_in_force="day",
    submitted_by="test",
    market_open=1,
    latest_bar_timestamp=None,
    latest_bar_close=None,
    account_equity=10000.0,
    account_cash=10000.0,
    account_portfolio_value=10000.0,
    account_daily_pnl=0.0,
    risk_allowed=1,
    risk_summary="ok",
    reason_codes_json="[]",
    risk_checks_json="{}",
    context_json="{}",
    session_id="sess-001",
    backtest_run_id=None,
)

_TRADE_DEFAULTS = dict(
    status="submitted",
    source="paper",
    symbol="AAPL",
    side="buy",
    quantity=1.0,
    order_type="market",
    time_in_force="day",
    estimated_unit_price=150.0,
    estimated_order_value=150.0,
    strategy_name="s1",
    strategy_stage="paper",
    strategy_config_path=None,
    submitted_by="test",
    broker_order_id=None,
    broker_status=None,
    message=None,
    session_id="sess-001",
    backtest_run_id=None,
)


def _create_fixture_db(path: Path) -> None:
    """Apply the REAL (fully-migrated) schema via EventStore."""
    from milodex.core.event_store import EventStore

    EventStore(path)


def _seed_explanation(db: Path, recorded_at: str, **kwargs) -> int:
    """Insert one explanations row; return inserted id."""
    row = {**_EXPL_DEFAULTS, **kwargs}
    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        """
        INSERT INTO explanations (
            recorded_at, decision_type, status, strategy_name, strategy_stage,
            strategy_config_path, config_hash, symbol, side, quantity,
            order_type, time_in_force, submitted_by, market_open,
            latest_bar_timestamp, latest_bar_close,
            account_equity, account_cash, account_portfolio_value, account_daily_pnl,
            risk_allowed, risk_summary, reason_codes_json, risk_checks_json,
            context_json, session_id, backtest_run_id
        ) VALUES (
            :recorded_at, :decision_type, :status, :strategy_name, :strategy_stage,
            :strategy_config_path, :config_hash, :symbol, :side, :quantity,
            :order_type, :time_in_force, :submitted_by, :market_open,
            :latest_bar_timestamp, :latest_bar_close,
            :account_equity, :account_cash, :account_portfolio_value, :account_daily_pnl,
            :risk_allowed, :risk_summary, :reason_codes_json, :risk_checks_json,
            :context_json, :session_id, :backtest_run_id
        )
        """,
        {"recorded_at": recorded_at, **row},
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id  # type: ignore[return-value]


def _seed_trade(db: Path, explanation_id: int, recorded_at: str, **kwargs) -> int:
    """Insert one trades row; return inserted id."""
    row = {**_TRADE_DEFAULTS, **kwargs}
    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        """
        INSERT INTO trades (
            explanation_id, recorded_at, status, source, symbol, side, quantity,
            order_type, time_in_force, estimated_unit_price, estimated_order_value,
            strategy_name, strategy_stage, strategy_config_path, submitted_by,
            broker_order_id, broker_status, message, session_id, backtest_run_id
        ) VALUES (
            :explanation_id, :recorded_at, :status, :source, :symbol, :side, :quantity,
            :order_type, :time_in_force, :estimated_unit_price, :estimated_order_value,
            :strategy_name, :strategy_stage, :strategy_config_path, :submitted_by,
            :broker_order_id, :broker_status, :message, :session_id, :backtest_run_id
        )
        """,
        {"explanation_id": explanation_id, "recorded_at": recorded_at, **row},
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Helper: fixed "now" for deterministic windows
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 16, 18, 0, 0, tzinfo=UTC)
_TODAY_START = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
_YESTERDAY = (_NOW - timedelta(days=1)).isoformat()
_THREE_DAYS_AGO = (_NOW - timedelta(days=3)).isoformat()
_TEN_DAYS_AGO = (_NOW - timedelta(days=10)).isoformat()
_FORTY_DAYS_AGO = (_NOW - timedelta(days=40)).isoformat()
_DEC_31 = datetime(2025, 12, 31, 0, 0, 0, tzinfo=UTC).isoformat()
_JAN_2 = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC).isoformat()


# ---------------------------------------------------------------------------
# Step 2 — Failing test: test_funnel_stage_counts
# ---------------------------------------------------------------------------


def test_funnel_stage_counts(tmp_path) -> None:
    """Funnel stage counts are correct for a seeded mixed DB.

    Seeds:
    - 1 explanation with decision_type='backtest_fill' (paper stage) → MUST be excluded
    - 1 no_signal paper explanation (counted as Evaluations, not Signals)
    - 1 risk_allowed=0 blocked paper explanation (Evaluations + Rejected)
    - 2 submit paper explanations with risk_allowed=1
      (Evaluations + Signals + Orders proposed + Risk-approved)
    - 1 preview paper explanation with risk_allowed=1
      (Evaluations + Signals + Orders proposed + Risk-approved)
    - 1 submitted trade (explanation_id → one of the submit explanations)
    - 1 filled trade (explanation_id → the preview explanation)

    Expected All-Paper counts (5 paper-scoped explanations after excluding backtest_fill):
    - Evaluations: 5  (6 inserted rows - 1 backtest_fill excluded)
    - Signals: 4      (5 paper-scoped - 1 no_signal row)
    - Orders proposed: 4  (submit/preview decision_type regardless of risk_allowed:
                           blocked-submit + 2 submit + 1 preview)
    - Risk-approved: 3    (orders_proposed AND risk_allowed=1: 2 submit + 1 preview)
    - Rejected: 2     (2 rows with risk_allowed=0: no_signal/no_trade + blocked/submit)
    - Submitted: 1    (trade status=submitted)
    - Filled: 1       (trade broker_status=filled)
    """
    from milodex.gui.risk_throughput_state import _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    ts = _THREE_DAYS_AGO  # within All-Paper, Week, YTD windows

    # Excluded: backtest_fill (paper stage but wrong decision_type)
    _seed_explanation(db, ts, decision_type="backtest_fill", status="submitted", risk_allowed=1)

    # no_signal paper explanation (Evaluations only)
    _seed_explanation(db, ts, decision_type="no_trade", status="no_signal", risk_allowed=0)

    # risk_allowed=0 blocked (Evaluations + Rejected)
    _seed_explanation(db, ts, decision_type="submit", status="blocked", risk_allowed=0)

    # 2 submit paper explanations with risk_allowed=1
    eid_submit1 = _seed_explanation(
        db, ts, decision_type="submit", status="submitted", risk_allowed=1
    )
    _seed_explanation(db, ts, decision_type="submit", status="submitted", risk_allowed=1)

    # 1 preview paper explanation with risk_allowed=1
    eid_preview = _seed_explanation(
        db, ts, decision_type="preview", status="preview", risk_allowed=1
    )

    # 1 submitted trade (linked to eid_submit1)
    _seed_trade(db, eid_submit1, ts, status="submitted", broker_status=None)

    # 1 filled trade (linked to eid_preview)
    _seed_trade(db, eid_preview, ts, status="filled", broker_status="filled")

    result = _query_throughput(db, _NOW)

    by_slice = result["bySlice"]
    assert "All-Paper" in by_slice

    all_paper = by_slice["All-Paper"]
    # Should be an ordered list of stage dicts
    assert isinstance(all_paper, list)
    assert len(all_paper) == 7

    by_key = {item["key"]: item["value"] for item in all_paper}

    assert by_key["evaluations"] == 5, f"evaluations: {by_key['evaluations']}"
    assert by_key["signals"] == 4, f"signals: {by_key['signals']}"
    assert by_key["orders_proposed"] == 4, f"orders_proposed: {by_key['orders_proposed']}"
    assert by_key["risk_approved"] == 3, f"risk_approved: {by_key['risk_approved']}"
    assert by_key["rejected"] == 2, f"rejected: {by_key['rejected']}"
    assert by_key["submitted"] == 1, f"submitted: {by_key['submitted']}"
    assert by_key["filled"] == 1, f"filled: {by_key['filled']}"


# ---------------------------------------------------------------------------
# Step 4 — Regression: backtest rows excluded from every stage
# ---------------------------------------------------------------------------


def test_backtest_rows_excluded_from_all_stages(tmp_path) -> None:
    """Backtest-origin rows must be excluded from every funnel stage.

    Asserts that:
    - A paper-stage explanation with decision_type='backtest_fill' is excluded
    - A strategy_stage='backtest' explanation is excluded
    - A trade with non-null backtest_run_id is excluded
    - A trade linked to a backtest-stage explanation is excluded (join-back)

    Only 1 clean paper explanation and 1 clean paper trade should be counted.
    """
    from milodex.gui.risk_throughput_state import _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    ts = _THREE_DAYS_AGO

    # --- rows that MUST be excluded ---

    # 1. paper stage but decision_type=backtest_fill → excluded by EXPLANATION_PAPER_SQL
    _seed_explanation(db, ts, decision_type="backtest_fill", status="submitted", risk_allowed=1)

    # 2. strategy_stage='backtest' → excluded by EXPLANATION_PAPER_SQL stage filter
    backtest_stage_eid = _seed_explanation(
        db,
        ts,
        strategy_stage="backtest",
        decision_type="submit",
        status="submitted",
        risk_allowed=1,
    )

    # 3. Trade with non-null backtest_run_id → excluded by TRADE_PAPER_SQL
    #    (needs an explanation too; use a valid paper explanation as parent)
    clean_eid = _seed_explanation(
        db, ts, decision_type="submit", status="submitted", risk_allowed=1
    )
    # Seed a backtest_run row first so FK works
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO backtest_runs"
        "(run_id,strategy_id,start_date,end_date,started_at,status,metadata_json) "
        "VALUES ('run-001','s1','2026-01-01','2026-01-02','2026-01-01T00:00:00','completed','{}')"
    )
    conn.commit()
    bt_run_id = conn.execute("SELECT id FROM backtest_runs WHERE run_id='run-001'").fetchone()[0]
    conn.close()

    # Trade with backtest_run_id set → excluded
    _seed_trade(
        db, clean_eid, ts, status="submitted", broker_status=None, backtest_run_id=bt_run_id
    )

    # 4. Trade linked to a backtest-stage explanation → excluded by join-back
    _seed_trade(db, backtest_stage_eid, ts, status="submitted", broker_status=None)

    # --- 1 clean paper explanation + 1 clean trade ---
    paper_eid = _seed_explanation(
        db, ts, decision_type="submit", status="submitted", risk_allowed=1
    )
    _seed_trade(db, paper_eid, ts, status="submitted", broker_status=None, backtest_run_id=None)

    result = _query_throughput(db, _NOW)
    by_slice = result["bySlice"]
    all_paper = by_slice["All-Paper"]
    by_key = {item["key"]: item["value"] for item in all_paper}

    # Only the 1 clean paper explanation counts
    # Note: clean_eid also counts (it's a paper-stage submit with risk_allowed=1)
    # So: paper_eid + clean_eid = 2 evaluations
    assert by_key["evaluations"] == 2, (
        f"Expected 2 evaluations (2 clean paper explanations), got {by_key['evaluations']}"
    )
    # Both are submit + risk_allowed=1
    assert by_key["signals"] == 2, f"signals: {by_key['signals']}"
    assert by_key["orders_proposed"] == 2, f"orders_proposed: {by_key['orders_proposed']}"
    assert by_key["risk_approved"] == 2, f"risk_approved: {by_key['risk_approved']}"
    assert by_key["rejected"] == 0, f"rejected: {by_key['rejected']}"
    # Only 1 trade counts: the clean paper trade (the other has backtest_run_id set or wrong parent)
    assert by_key["submitted"] == 1, f"submitted: {by_key['submitted']}"
    assert by_key["filled"] == 0, f"filled: {by_key['filled']}"


# ---------------------------------------------------------------------------
# Additional pure-logic tests
# ---------------------------------------------------------------------------


def test_slice_windows_coverage(tmp_path) -> None:
    """Each SLICES key is present in bySlice output."""
    from milodex.gui.risk_throughput_state import SLICES, _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    result = _query_throughput(db, _NOW)
    for slice_name in SLICES:
        assert slice_name in result["bySlice"], f"Missing slice: {slice_name}"


def test_today_window_is_computed(tmp_path) -> None:
    """Today slice counts only rows recorded on today (UTC); yesterday row excluded."""
    from milodex.gui.risk_throughput_state import _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    # Row from yesterday — should NOT be in Today
    _seed_explanation(db, _YESTERDAY, decision_type="submit", status="submitted", risk_allowed=1)

    # Row from today (1 hour ago)
    today_ts = (_NOW - timedelta(hours=1)).isoformat()
    _seed_explanation(db, today_ts, decision_type="submit", status="submitted", risk_allowed=1)

    result = _query_throughput(db, _NOW)
    today_items = result["bySlice"]["Today"]
    by_key = {item["key"]: item["value"] for item in today_items}

    assert by_key["evaluations"] == 1, f"Today evaluations: {by_key['evaluations']}"


def test_week_window_excludes_ten_days_ago(tmp_path) -> None:
    """Row from 10 days ago is NOT in the Week slice (7-day window)."""
    from milodex.gui.risk_throughput_state import _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    _seed_explanation(db, _TEN_DAYS_AGO, decision_type="submit", status="submitted", risk_allowed=1)
    _seed_explanation(
        db, _THREE_DAYS_AGO, decision_type="submit", status="submitted", risk_allowed=1
    )

    result = _query_throughput(db, _NOW)
    week_items = result["bySlice"]["Week"]
    by_key = {item["key"]: item["value"] for item in week_items}

    assert by_key["evaluations"] == 1, f"Week evaluations: {by_key['evaluations']}"


def test_ytd_excludes_dec31(tmp_path) -> None:
    """Row from Dec 31 prior year is NOT in the YTD slice."""
    from milodex.gui.risk_throughput_state import _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    _seed_explanation(db, _DEC_31, decision_type="submit", status="submitted", risk_allowed=1)
    _seed_explanation(db, _JAN_2, decision_type="submit", status="submitted", risk_allowed=1)

    result = _query_throughput(db, _NOW)
    ytd_items = result["bySlice"]["YTD"]
    by_key = {item["key"]: item["value"] for item in ytd_items}

    assert by_key["evaluations"] == 1, f"YTD evaluations: {by_key['evaluations']}"


def test_stage_order_is_funnel_order(tmp_path) -> None:
    """bySlice items are in funnel order: evaluations first, filled last."""
    from milodex.gui.risk_throughput_state import _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    result = _query_throughput(db, _NOW)
    items = result["bySlice"]["All-Paper"]
    keys = [item["key"] for item in items]

    expected_order = [
        "evaluations",
        "signals",
        "orders_proposed",
        "risk_approved",
        "rejected",
        "submitted",
        "filled",
    ]
    assert keys == expected_order, f"Order mismatch: {keys}"


def test_empty_db_returns_zeros(tmp_path) -> None:
    """Empty DB returns 0 for all stages across all slices."""
    from milodex.gui.risk_throughput_state import SLICES, _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    result = _query_throughput(db, _NOW)
    for slice_name in SLICES:
        items = result["bySlice"][slice_name]
        for item in items:
            assert item["value"] == 0, (
                f"{slice_name}/{item['key']}: expected 0, got {item['value']}"
            )


def test_missing_db_raises(tmp_path) -> None:
    """_query_throughput raises when the DB path does not exist."""
    from milodex.gui.risk_throughput_state import _query_throughput

    with pytest.raises(Exception):  # noqa: B017
        _query_throughput(tmp_path / "nonexistent.db", _NOW)


def test_filled_requires_explanation_in_scope(tmp_path) -> None:
    """A filled trade whose explanation is backtest-stage is NOT counted as Filled."""
    from milodex.gui.risk_throughput_state import _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    ts = _THREE_DAYS_AGO

    # Backtest-stage explanation — out of scope
    backtest_eid = _seed_explanation(
        db,
        ts,
        strategy_stage="backtest",
        decision_type="submit",
        status="filled",
        risk_allowed=1,
    )
    # Trade that would be "filled" but its explanation is out of paper scope
    _seed_trade(
        db, backtest_eid, ts, status="filled", broker_status="filled", strategy_stage="backtest"
    )

    result = _query_throughput(db, _NOW)
    all_paper = result["bySlice"]["All-Paper"]
    by_key = {item["key"]: item["value"] for item in all_paper}

    assert by_key["filled"] == 0, f"filled should be 0, got {by_key['filled']}"
    assert by_key["evaluations"] == 0, f"evaluations should be 0, got {by_key['evaluations']}"


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
    """Construct a RiskThroughputState with a long interval so timers never fire."""
    from milodex.gui.risk_throughput_state import RiskThroughputState

    return RiskThroughputState(db_path=db_path, refresh_interval_ms=refresh_interval_ms)


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
    db = tmp_path / "rt.db"
    _create_fixture_db(db)
    state = _make_state(db)

    assert state.dataStatus == "loading"
    assert state.bySlice == {}
    assert state.lastRefreshedAt == ""
    assert state.dataErrorMessage == ""


@_skip_no_qt
def test_refresh_populates_by_slice(qapp, tmp_path) -> None:
    """After a successful refresh, bySlice is populated with all slices."""
    _ = qapp
    db = tmp_path / "rt.db"
    _create_fixture_db(db)

    ts = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    _seed_explanation(db, ts, decision_type="submit", status="submitted", risk_allowed=1)

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    by_slice = state.bySlice
    for slice_name in ("Today", "Week", "Month", "YTD", "All-Paper"):
        assert slice_name in by_slice, f"Missing slice: {slice_name}"
        items = by_slice[slice_name]
        assert isinstance(items, list)
        assert len(items) == 7

    state.stop()


# Lifecycle scaffold tests (missing-DB error, error-after-success preservation,
# in-flight drop, stop-drains-worker) were removed in PR C of RM-007 — those
# contracts are now covered ONCE in tests/milodex/gui/test_polling_lifecycle.py.


def test_evaluations_excludes_paper_staged_backtest_explanations(tmp_path) -> None:
    """A backtest explanation (backtest_run_id set) for a PAPER-staged strategy
    must not count as live throughput.

    Regression for the 2026-05-29 benchmark leak: the benchmark strategy is
    paper-staged, so EXPLANATION_PAPER_SQL's stage filter alone let its overnight
    backtest evaluation rows (decision_type='no_trade'/'submit', NOT 'backtest_fill')
    into the live funnel — 69,251 counted vs 358 actual live. backtest_run_id IS NULL
    is the only reliable live/backtest discriminator.
    """
    from milodex.gui.risk_throughput_state import _query_throughput

    db = tmp_path / "rt.db"
    _create_fixture_db(db)
    ts = _NOW.replace(hour=12).isoformat()  # within Today window

    # Live paper evaluation — counts.
    _seed_explanation(
        db,
        ts,
        strategy_stage="paper",
        decision_type="no_trade",
        status="no_signal",
        backtest_run_id=None,
    )
    # Paper-staged BACKTEST evaluation — must be excluded (the benchmark leak).
    _seed_explanation(
        db,
        ts,
        strategy_stage="paper",
        decision_type="no_trade",
        status="no_signal",
        backtest_run_id=1,
    )

    today = _query_throughput(db, _NOW)["bySlice"]["Today"]
    evaluations = next(s for s in today if s["key"] == "evaluations")["value"]
    assert evaluations == 1  # only the live row; the paper-staged backtest row excluded

"""Tests for :class:`milodex.gui.activity_feed_state.ActivityFeedState`.

Mirrors the PerformanceState / RiskThroughputState test harness:

- Pure-logic helpers tested without Qt.
- Full QObject lifecycle tests require a ``QGuiApplication`` and a real
  (tmp-path) SQLite DB.  Gated behind ``_skip_no_qt``.
- Tests drive the refresh cycle directly via ``_kick_refresh()``; the timer
  interval is set to 99 999 999 ms so it never fires in CI.
- Fixture DB schema matches the production schema exactly.
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
    reason="PySide6 not installed — skipping Qt-aware ActivityFeedState tests",
)

# ---------------------------------------------------------------------------
# Fixture DB helpers
# ---------------------------------------------------------------------------


def _create_fixture_db(path: Path) -> None:
    """Create a minimal SQLite DB with explanations + trades tables."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE explanations (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at      TEXT NOT NULL,
            decision_type    TEXT NOT NULL,
            status           TEXT NOT NULL,
            strategy_name    TEXT NOT NULL,
            strategy_stage   TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            side             TEXT NOT NULL,
            quantity         REAL NOT NULL,
            risk_allowed     INTEGER NOT NULL,
            session_id       TEXT NOT NULL,
            backtest_run_id  TEXT
        );

        CREATE TABLE trades (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            explanation_id        INTEGER,
            recorded_at           TEXT NOT NULL,
            status                TEXT NOT NULL,
            source                TEXT,
            symbol                TEXT NOT NULL,
            side                  TEXT NOT NULL,
            quantity              REAL NOT NULL,
            strategy_name         TEXT NOT NULL,
            strategy_stage        TEXT NOT NULL,
            broker_order_id       TEXT,
            broker_status         TEXT,
            estimated_order_value REAL,
            session_id            TEXT NOT NULL,
            backtest_run_id       TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def _seed_explanation(
    db: Path,
    *,
    recorded_at: str,
    decision_type: str = "submit",
    status: str = "submitted",
    strategy_name: str = "alpha",
    strategy_stage: str = "paper",
    symbol: str = "AAPL",
    side: str = "buy",
    quantity: float = 10.0,
    risk_allowed: int = 1,
    session_id: str = "sess-001",
    backtest_run_id: str | None = None,
) -> int:
    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        """
        INSERT INTO explanations
            (recorded_at, decision_type, status, strategy_name, strategy_stage,
             symbol, side, quantity, risk_allowed, session_id, backtest_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recorded_at,
            decision_type,
            status,
            strategy_name,
            strategy_stage,
            symbol,
            side,
            quantity,
            risk_allowed,
            session_id,
            backtest_run_id,
        ),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id  # type: ignore[return-value]


def _seed_trade(
    db: Path,
    *,
    recorded_at: str,
    explanation_id: int | None = None,
    status: str = "submitted",
    source: str = "live",
    symbol: str = "AAPL",
    side: str = "buy",
    quantity: float = 10.0,
    strategy_name: str = "alpha",
    strategy_stage: str = "paper",
    broker_order_id: str | None = None,
    broker_status: str | None = None,
    estimated_order_value: float | None = None,
    session_id: str = "sess-001",
    backtest_run_id: str | None = None,
) -> int:
    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        """
        INSERT INTO trades
            (explanation_id, recorded_at, status, source, symbol, side, quantity,
             strategy_name, strategy_stage, broker_order_id, broker_status,
             estimated_order_value, session_id, backtest_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            explanation_id,
            recorded_at,
            status,
            source,
            symbol,
            side,
            quantity,
            strategy_name,
            strategy_stage,
            broker_order_id,
            broker_status,
            estimated_order_value,
            session_id,
            backtest_run_id,
        ),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Pure _row_tone tests — no Qt required
# ---------------------------------------------------------------------------


def test_row_tone_fill() -> None:
    from milodex.gui.activity_feed_state import _row_tone

    assert _row_tone("fill") == "positive"


def test_row_tone_rejection() -> None:
    from milodex.gui.activity_feed_state import _row_tone

    assert _row_tone("rejection") == "negative"


def test_row_tone_order() -> None:
    from milodex.gui.activity_feed_state import _row_tone

    assert _row_tone("order") == "data"


def test_row_tone_signal() -> None:
    from milodex.gui.activity_feed_state import _row_tone

    assert _row_tone("signal") == "muted"


# ---------------------------------------------------------------------------
# Pure _query_feed tests — no Qt required
# ---------------------------------------------------------------------------


def test_feed_cap_constant() -> None:
    """_FEED_CAP is 200."""
    from milodex.gui.activity_feed_state import _FEED_CAP

    assert _FEED_CAP == 200


def test_feed_union_ordering_and_cap(tmp_path) -> None:
    """Feed is UNION of paper-scoped rows, ordered DESC by recorded_at, capped at 200.

    Seeds >200 mixed rows including a backtest row that MUST be excluded.
    Asserts:
    - total rows ≤ 200
    - strictly descending order by time
    - most-recent rows are present
    - backtest / excluded rows are absent
    - kinds are correctly derived
    """
    from milodex.gui.activity_feed_state import _FEED_CAP, _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)

    # Seed 150 paper explanation rows (risk_allowed=1 → signal)
    for i in range(150):
        ts = (now - timedelta(minutes=i + 10)).isoformat()
        _seed_explanation(db, recorded_at=ts, risk_allowed=1, strategy_name="alpha")

    # Seed 60 paper trade rows (status=submitted → order)
    for i in range(60):
        ts = (now - timedelta(minutes=i + 5)).isoformat()
        _seed_trade(db, recorded_at=ts, status="submitted", strategy_name="beta")

    # Seed one BACKTEST explanation — must be excluded
    backtest_ts = (now - timedelta(seconds=1)).isoformat()
    _seed_explanation(
        db,
        recorded_at=backtest_ts,
        decision_type="backtest_fill",
        strategy_stage="backtest",
        backtest_run_id="bt-run-999",
        risk_allowed=1,
    )

    # Seed one BACKTEST trade (non-null backtest_run_id) — must be excluded
    _seed_trade(
        db,
        recorded_at=backtest_ts,
        strategy_stage="backtest",
        backtest_run_id="bt-run-999",
        status="submitted",
    )

    # Seed a paper rejection (risk_allowed=0) — must appear with kind=rejection
    rejection_ts = (now - timedelta(seconds=30)).isoformat()
    _seed_explanation(
        db,
        recorded_at=rejection_ts,
        risk_allowed=0,
        strategy_name="gamma",
    )

    # Seed a filled trade (broker_status=filled) — must appear with kind=fill
    fill_ts = (now - timedelta(seconds=60)).isoformat()
    _seed_trade(
        db,
        recorded_at=fill_ts,
        status="filled",
        broker_status="filled",
        strategy_name="delta",
    )

    feed = _query_feed(db)

    # Cap
    assert len(feed) <= _FEED_CAP, f"Feed length {len(feed)} exceeds cap {_FEED_CAP}"

    # Strictly descending
    for i in range(len(feed) - 1):
        assert feed[i]["time"] >= feed[i + 1]["time"], (
            f"Out of order at index {i}: {feed[i]['time']} < {feed[i+1]['time']}"
        )

    # Most-recent rows should be present (the rejection and fill are very recent)
    assert any(r["kind"] == "rejection" and r["strategy"] == "gamma" for r in feed), (
        "Expected rejection row from gamma strategy"
    )
    assert any(r["kind"] == "fill" and r["strategy"] == "delta" for r in feed), (
        "Expected fill row from delta strategy"
    )

    # Oldest seeded explanation (i=149 → now - 159 min) must NOT be present.
    # With correct sort-then-cap the 200 newest rows are kept, so this row
    # (ranked ~210th newest overall) is dropped.  A slice-before-sort bug
    # would retain an arbitrary 200 rows and could include this oldest row
    # while missing the most-recent special rows asserted above — the two
    # assertions together prove the cap is applied after sorting.
    oldest_exp_time = (now - timedelta(minutes=159)).isoformat()
    assert not any(
        r["strategy"] == "alpha" and r["time"] == oldest_exp_time for r in feed
    ), (
        f"Oldest explanation (alpha @ {oldest_exp_time}) should have been dropped by cap "
        "but is present — cap was likely applied before sorting"
    )

    # Backtest rows must be absent
    for row in feed:
        assert row.get("strategy") not in {"bt-run-999"}, "Backtest strategy name leaked"
    # No row should have a backtest_run_id in its data (not present in schema — sanity)
    # More robustly: verify the backtest decision_type is not present
    for row in feed:
        assert "backtest_fill" not in row.get("detail", ""), (
            "Backtest decision_type leaked into feed"
        )

    # Kinds are correctly derived: rejections, signals, orders, fills only
    valid_kinds = {"rejection", "signal", "order", "fill"}
    for row in feed:
        assert row["kind"] in valid_kinds, f"Unexpected kind: {row['kind']}"

    # Tones match kind
    expected_tones = {
        "fill": "positive",
        "rejection": "negative",
        "order": "data",
        "signal": "muted",
    }
    for row in feed:
        assert row["tone"] == expected_tones[row["kind"]], (
            f"tone mismatch for kind={row['kind']}: got {row['tone']}"
        )


def test_feed_kind_derivation_rejection(tmp_path) -> None:
    """Explanation with risk_allowed=0 → kind=rejection, tone=negative."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()
    _seed_explanation(db, recorded_at=ts, risk_allowed=0)

    feed = _query_feed(db)
    assert len(feed) == 1
    row = feed[0]
    assert row["kind"] == "rejection"
    assert row["tone"] == "negative"


def test_feed_kind_derivation_signal(tmp_path) -> None:
    """Explanation with risk_allowed=1 → kind=signal, tone=muted."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()
    _seed_explanation(db, recorded_at=ts, risk_allowed=1)

    feed = _query_feed(db)
    assert len(feed) == 1
    row = feed[0]
    assert row["kind"] == "signal"
    assert row["tone"] == "muted"


def test_feed_kind_derivation_order(tmp_path) -> None:
    """Trade with status=submitted → kind=order, tone=data."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()
    _seed_trade(db, recorded_at=ts, status="submitted", broker_status=None)

    feed = _query_feed(db)
    assert len(feed) == 1
    row = feed[0]
    assert row["kind"] == "order"
    assert row["tone"] == "data"


def test_feed_kind_derivation_fill(tmp_path) -> None:
    """Trade with broker_status=filled → kind=fill, tone=positive."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()
    _seed_trade(db, recorded_at=ts, status="filled", broker_status="filled")

    feed = _query_feed(db)
    assert len(feed) == 1
    row = feed[0]
    assert row["kind"] == "fill"
    assert row["tone"] == "positive"


def test_feed_excludes_neither_submitted_nor_filled_trades(tmp_path) -> None:
    """Trades that are neither submitted nor broker_status=filled are excluded."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()
    # blocked trade — neither submitted nor filled
    _seed_trade(db, recorded_at=ts, status="blocked", broker_status=None)

    feed = _query_feed(db)
    assert feed == [], "blocked trade should be excluded from feed"


def test_feed_paper_scoping_backtest_excluded(tmp_path) -> None:
    """Backtest rows (strategy_stage=backtest or backtest_run_id set) never appear."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()

    # Backtest explanation: decision_type=backtest_fill + backtest stage
    _seed_explanation(
        db,
        recorded_at=ts,
        decision_type="backtest_fill",
        strategy_stage="backtest",
        backtest_run_id="bt-001",
        risk_allowed=1,
    )

    # Backtest trade: non-null backtest_run_id
    _seed_trade(
        db,
        recorded_at=ts,
        strategy_stage="backtest",
        backtest_run_id="bt-001",
        status="submitted",
    )

    # Paper explanation — should appear
    paper_ts = (datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC) - timedelta(seconds=1)).isoformat()
    _seed_explanation(db, recorded_at=paper_ts, risk_allowed=1)

    feed = _query_feed(db)
    # Only the paper explanation row
    assert len(feed) == 1
    assert feed[0]["kind"] == "signal"


def test_feed_detail_format_explanation(tmp_path) -> None:
    """Explanation detail is 'decision_type/status'."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()
    _seed_explanation(
        db,
        recorded_at=ts,
        decision_type="submit",
        status="submitted",
        risk_allowed=1,
    )

    feed = _query_feed(db)
    assert feed[0]["detail"] == "submit/submitted"


def test_feed_detail_format_trade(tmp_path) -> None:
    """Trade detail is 'side quantity @ status/broker_status'."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()
    _seed_trade(
        db,
        recorded_at=ts,
        side="buy",
        quantity=5.0,
        status="submitted",
        broker_status="pending",
    )

    feed = _query_feed(db)
    assert feed[0]["detail"] == "buy 5.0 @ submitted/pending"


def test_feed_detail_null_broker_status(tmp_path) -> None:
    """NULL broker_status renders as 'pending' in detail string."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    ts = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()
    _seed_trade(
        db,
        recorded_at=ts,
        side="sell",
        quantity=3.0,
        status="submitted",
        broker_status=None,
    )

    feed = _query_feed(db)
    assert feed[0]["detail"] == "sell 3.0 @ submitted/pending"


def test_feed_missing_db_raises(tmp_path) -> None:
    """_query_feed raises when the DB path does not exist."""
    from milodex.gui.activity_feed_state import _query_feed

    with pytest.raises(Exception):  # noqa: B017
        _query_feed(tmp_path / "nonexistent.db")


def test_read_only_connection_blocks_writes(tmp_path) -> None:
    """Connecting with file:...?mode=ro raises OperationalError on write attempt."""
    db = tmp_path / "readonly_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.commit()
    conn.close()

    ro_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_conn.execute("CREATE TABLE x(a)")
    ro_conn.close()


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
    refresh_interval_ms: int = 99_999_999,
):
    """Construct an ActivityFeedState with a long interval so timers never fire."""
    from milodex.gui.activity_feed_state import ActivityFeedState

    return ActivityFeedState(
        db_path=db_path,
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
    db = tmp_path / "feed.db"
    _create_fixture_db(db)
    state = _make_state(db)

    assert state.dataStatus == "loading"
    assert state.rows == []
    assert state.lastRefreshedAt == ""
    assert state.dataErrorMessage == ""


@_skip_no_qt
def test_refresh_populates_rows(qapp, tmp_path) -> None:
    """After a successful refresh, rows is populated."""
    _ = qapp
    db = tmp_path / "feed.db"
    _create_fixture_db(db)

    now = datetime.now(tz=UTC)
    ts = (now - timedelta(minutes=1)).isoformat()
    _seed_explanation(db, recorded_at=ts, risk_allowed=1)
    ts2 = now.isoformat()
    _seed_trade(db, recorded_at=ts2, status="submitted")

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    assert len(state.rows) == 2
    # Newest first
    assert state.rows[0]["time"] >= state.rows[1]["time"]

    state.stop()


# Lifecycle scaffold tests (missing-DB error, error-after-success preservation,
# in-flight drop, stop-drains-worker) were removed in PR C of RM-007 — those
# contracts are now covered ONCE in tests/milodex/gui/test_polling_lifecycle.py.


@_skip_no_qt
def test_paper_scoping_exclusion_backtest_never_appears(qapp, tmp_path) -> None:
    """Backtest explanation and trade rows never appear in the state's rows."""
    _ = qapp
    db = tmp_path / "feed.db"
    _create_fixture_db(db)

    now = datetime.now(tz=UTC)
    bt_ts = now.isoformat()

    # Seed backtest explanation and trade — both should be excluded
    _seed_explanation(
        db,
        recorded_at=bt_ts,
        decision_type="backtest_fill",
        strategy_stage="backtest",
        backtest_run_id="bt-run-001",
        risk_allowed=1,
        strategy_name="backtest_strat",
    )
    _seed_trade(
        db,
        recorded_at=bt_ts,
        strategy_stage="backtest",
        backtest_run_id="bt-run-001",
        status="submitted",
        strategy_name="backtest_strat",
    )

    # Seed one paper explanation — should appear
    paper_ts = (now - timedelta(minutes=1)).isoformat()
    _seed_explanation(
        db,
        recorded_at=paper_ts,
        risk_allowed=1,
        strategy_name="paper_strat",
    )

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    assert len(state.rows) == 1
    assert state.rows[0]["strategy"] == "paper_strat"
    assert state.rows[0]["kind"] == "signal"

    state.stop()


@_skip_no_qt
def test_kind_derivation_all_four_kinds(qapp, tmp_path) -> None:
    """All four kinds (rejection/signal/order/fill) are correctly derived on state."""
    _ = qapp
    db = tmp_path / "feed.db"
    _create_fixture_db(db)

    now = datetime.now(tz=UTC)

    # rejection
    _seed_explanation(
        db,
        recorded_at=(now - timedelta(minutes=4)).isoformat(),
        risk_allowed=0,
        strategy_name="s_rejection",
    )
    # signal
    _seed_explanation(
        db,
        recorded_at=(now - timedelta(minutes=3)).isoformat(),
        risk_allowed=1,
        strategy_name="s_signal",
    )
    # order
    _seed_trade(
        db,
        recorded_at=(now - timedelta(minutes=2)).isoformat(),
        status="submitted",
        broker_status=None,
        strategy_name="s_order",
    )
    # fill
    _seed_trade(
        db,
        recorded_at=(now - timedelta(minutes=1)).isoformat(),
        status="filled",
        broker_status="filled",
        strategy_name="s_fill",
    )

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    kinds_by_strategy = {r["strategy"]: r["kind"] for r in state.rows}
    assert kinds_by_strategy["s_rejection"] == "rejection"
    assert kinds_by_strategy["s_signal"] == "signal"
    assert kinds_by_strategy["s_order"] == "order"
    assert kinds_by_strategy["s_fill"] == "fill"

    tones_by_strategy = {r["strategy"]: r["tone"] for r in state.rows}
    assert tones_by_strategy["s_rejection"] == "negative"
    assert tones_by_strategy["s_signal"] == "muted"
    assert tones_by_strategy["s_order"] == "data"
    assert tones_by_strategy["s_fill"] == "positive"

    state.stop()


# ---------------------------------------------------------------------------
# Task 23 (PR-6): backtest_runs as third ActivityFeed source
# ---------------------------------------------------------------------------


def _create_fixture_db_with_backtests(path: Path) -> None:
    """Create a minimal DB with explanations, trades, and backtest_runs tables."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE explanations (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at      TEXT NOT NULL,
            decision_type    TEXT NOT NULL,
            status           TEXT NOT NULL,
            strategy_name    TEXT NOT NULL,
            strategy_stage   TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            side             TEXT NOT NULL,
            quantity         REAL NOT NULL,
            risk_allowed     INTEGER NOT NULL,
            session_id       TEXT NOT NULL,
            backtest_run_id  TEXT
        );

        CREATE TABLE trades (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            explanation_id        INTEGER,
            recorded_at           TEXT NOT NULL,
            status                TEXT NOT NULL,
            source                TEXT,
            symbol                TEXT NOT NULL,
            side                  TEXT NOT NULL,
            quantity              REAL NOT NULL,
            strategy_name         TEXT NOT NULL,
            strategy_stage        TEXT NOT NULL,
            broker_order_id       TEXT,
            broker_status         TEXT,
            estimated_order_value REAL,
            session_id            TEXT NOT NULL,
            backtest_run_id       TEXT
        );

        CREATE TABLE backtest_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id           TEXT NOT NULL UNIQUE,
            strategy_id      TEXT NOT NULL,
            config_path      TEXT,
            config_hash      TEXT,
            start_date       TEXT NOT NULL,
            end_date         TEXT NOT NULL,
            started_at       TEXT NOT NULL,
            ended_at         TEXT,
            status           TEXT NOT NULL,
            slippage_pct     REAL,
            commission_per_trade REAL,
            metadata_json    TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _seed_completed_backtest(
    db: Path,
    *,
    run_id: str = "run-1",
    strategy_id: str = "momentum.daily.test.v1",
    ended_at: str = "2026-05-10T12:00:00+00:00",
    sharpe: float | None = 0.72,
    max_drawdown_pct: float | None = 8.5,
    trade_count: int | None = 120,
) -> None:
    import json as _json

    metadata = {}
    if sharpe is not None or max_drawdown_pct is not None or trade_count is not None:
        metadata["oos_aggregate"] = {}
        if sharpe is not None:
            metadata["oos_aggregate"]["sharpe"] = sharpe
        if max_drawdown_pct is not None:
            metadata["oos_aggregate"]["max_drawdown_pct"] = max_drawdown_pct
        if trade_count is not None:
            metadata["oos_aggregate"]["trade_count"] = trade_count

    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, strategy_id, start_date, end_date, started_at, ended_at,
             status, metadata_json)
        VALUES (?, ?, '2020-01-01', '2024-12-31', '2026-05-10T08:00:00+00:00',
                ?, 'completed', ?)
        """,
        (run_id, strategy_id, ended_at, _json.dumps(metadata)),
    )
    conn.commit()
    conn.close()


def test_activity_feed_includes_backtest_results(tmp_path: Path) -> None:
    """Section VII shows backtest results alongside orders/signals/fills/rejections."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db_with_backtests(db)

    # Seed one completed backtest
    _seed_completed_backtest(
        db,
        run_id="run-bt",
        strategy_id="momentum.daily.test.v1",
        ended_at="2026-05-10T12:00:00+00:00",
        sharpe=0.72,
    )

    # Seed one paper explanation to confirm both sources appear
    from datetime import UTC, datetime

    exp_ts = datetime(2026, 5, 10, 11, 0, 0, tzinfo=UTC).isoformat()
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO explanations
           (recorded_at, decision_type, status, strategy_name, strategy_stage,
            symbol, side, quantity, risk_allowed, session_id)
           VALUES (?, 'submit', 'submitted', 'alpha', 'paper', 'SPY', 'buy', 10, 1, 'sess-1')""",
        (exp_ts,),
    )
    conn.commit()
    conn.close()

    feed = _query_feed(db)

    kinds = {r["kind"] for r in feed}
    assert "backtest" in kinds, f"Expected 'backtest' kind in feed; got kinds={kinds}"
    assert "signal" in kinds, f"Expected 'signal' kind in feed; got kinds={kinds}"

    # Backtest row should carry strategy and detail
    bt_rows = [r for r in feed if r["kind"] == "backtest"]
    assert len(bt_rows) == 1
    assert "Sharpe" in bt_rows[0]["detail"], f"Expected Sharpe in detail: {bt_rows[0]['detail']}"


def test_activity_feed_excludes_incomplete_backtests(tmp_path: Path) -> None:
    """Only status='completed' backtest_runs rows appear in the feed."""
    from milodex.gui.activity_feed_state import _query_feed

    db = tmp_path / "feed.db"
    _create_fixture_db_with_backtests(db)

    conn = sqlite3.connect(str(db))
    # incomplete (running) backtest — should NOT appear
    conn.execute(
        """INSERT INTO backtest_runs
           (run_id, strategy_id, start_date, end_date, started_at, ended_at,
            status, metadata_json)
           VALUES ('run-running', 'momentum.daily.test.v1', '2020-01-01', '2024-12-31',
                   '2026-05-10T08:00:00+00:00', NULL, 'running', '{}')""",
    )
    conn.commit()
    conn.close()

    feed = _query_feed(db)
    assert not any(r["kind"] == "backtest" for r in feed), (
        "Running/incomplete backtest should not appear in feed"
    )

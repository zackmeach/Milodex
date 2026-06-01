"""Tests for :mod:`milodex.gui._event_queries`.

Covers:
1. ``oos_aggregate_metrics``:
   - valid JSON with all three fields → correct triple
   - None input → all-None, no exception
   - malformed JSON → all-None, no exception
   - missing ``oos_aggregate`` key → all-None, no exception
   - partial ``oos_aggregate`` (some keys absent) → present keys returned, absent → None

2. ``latest_backtest_metrics``:
   - returns latest (MAX id) completed run per strategy
   - a strategy with only non-completed runs is absent
   - multiple runs per strategy → only the highest-id completed run appears
   - empty table → empty dict
   - sqlite3.Error → returns {} (defensive)

3. Invariant #3 anti-regression: this module's output is used by strategy_bank_state
   to exclude paper-promoted strategies from the blocked list — tested end-to-end in
   test_strategy_bank_state.py; here we pin the raw helper contract.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _create_backtest_table(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
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


def _seed_run(
    path: Path,
    *,
    run_id: str,
    strategy_id: str,
    status: str = "completed",
    sharpe: float | None = None,
    max_drawdown_pct: float | None = None,
    trade_count: int | None = None,
    started_at: str = "2026-01-01T00:00:00+00:00",
) -> None:
    agg: dict = {}
    if sharpe is not None:
        agg["sharpe"] = sharpe
    if max_drawdown_pct is not None:
        agg["max_drawdown_pct"] = max_drawdown_pct
    if trade_count is not None:
        agg["trade_count"] = trade_count
    metadata = {"oos_aggregate": agg} if agg else {}
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, strategy_id, start_date, end_date, started_at, status, metadata_json)
        VALUES (?, ?, '2020-01-01', '2024-12-31', ?, ?, ?)
        """,
        (run_id, strategy_id, started_at, status, json.dumps(metadata)),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# oos_aggregate_metrics
# ---------------------------------------------------------------------------


def test_oos_aggregate_metrics_valid_all_fields() -> None:
    """Valid JSON with all three fields returns the correct triple."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    metadata_json = json.dumps(
        {"oos_aggregate": {"sharpe": 1.23, "max_drawdown_pct": 7.5, "trade_count": 80}}
    )
    result = oos_aggregate_metrics(metadata_json)
    assert result == {"sharpe": 1.23, "max_drawdown_pct": 7.5, "trade_count": 80}


def test_oos_aggregate_metrics_none_input() -> None:
    """None input → all-None triple, no exception."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics(None)
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


def test_oos_aggregate_metrics_malformed_json() -> None:
    """Malformed JSON → all-None triple, no exception."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics("{not valid json}")
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


def test_oos_aggregate_metrics_missing_oos_aggregate() -> None:
    """JSON without oos_aggregate key → all-None triple."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics(json.dumps({"initial_equity": 100000}))
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


def test_oos_aggregate_metrics_partial_fields() -> None:
    """Only sharpe present → sharpe returned, other two None."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics(json.dumps({"oos_aggregate": {"sharpe": 0.88}}))
    assert result["sharpe"] == 0.88
    assert result["max_drawdown_pct"] is None
    assert result["trade_count"] is None


def test_oos_aggregate_metrics_empty_oos_aggregate() -> None:
    """Empty oos_aggregate dict → all-None triple."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics(json.dumps({"oos_aggregate": {}}))
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


def test_oos_aggregate_metrics_empty_string() -> None:
    """Empty string → all-None triple, no exception."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics("")
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


def test_oos_aggregate_metrics_non_dict_json() -> None:
    """JSON that parses but is not a dict (list) → all-None, no exception."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics(json.dumps([1, 2, 3]))
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


# ---------------------------------------------------------------------------
# latest_backtest_metrics
# ---------------------------------------------------------------------------


def test_latest_backtest_metrics_single_run(tmp_path: Path) -> None:
    """Single completed run per strategy → appears in result with all fields."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    _create_backtest_table(db)
    _seed_run(
        db,
        run_id="run-1",
        strategy_id="strat.a",
        sharpe=1.0,
        max_drawdown_pct=5.0,
        trade_count=40,
        started_at="2026-03-01T00:00:00+00:00",
    )

    conn = _make_conn(db)
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    assert "strat.a" in result
    m = result["strat.a"]
    assert m["run_id"] == "run-1"
    assert m["started_at"] == "2026-03-01T00:00:00+00:00"
    assert m["sharpe"] == 1.0
    assert m["max_drawdown_pct"] == 5.0
    assert m["trade_count"] == 40


def test_latest_backtest_metrics_returns_max_id_run(tmp_path: Path) -> None:
    """Multiple completed runs per strategy → only the one with MAX id appears."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    _create_backtest_table(db)
    _seed_run(db, run_id="run-old", strategy_id="strat.b", sharpe=0.5, trade_count=20)
    _seed_run(db, run_id="run-new", strategy_id="strat.b", sharpe=1.5, trade_count=60)

    conn = _make_conn(db)
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    assert "strat.b" in result
    # run-new was inserted second → higher autoincrement id → should win
    assert result["strat.b"]["run_id"] == "run-new"
    assert result["strat.b"]["sharpe"] == 1.5


def test_latest_backtest_metrics_non_completed_absent(tmp_path: Path) -> None:
    """Strategy with only a 'running' run is absent from the result."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    _create_backtest_table(db)
    _seed_run(db, run_id="run-running", strategy_id="strat.c", status="running")

    conn = _make_conn(db)
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    assert "strat.c" not in result


def test_latest_backtest_metrics_mixed_status(tmp_path: Path) -> None:
    """Strategy with one completed and one running run → only the completed appears."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    _create_backtest_table(db)
    _seed_run(db, run_id="run-done", strategy_id="strat.d", status="completed", sharpe=0.9)
    _seed_run(db, run_id="run-live", strategy_id="strat.d", status="running")

    conn = _make_conn(db)
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    assert "strat.d" in result
    assert result["strat.d"]["run_id"] == "run-done"


def test_latest_backtest_metrics_empty_table(tmp_path: Path) -> None:
    """Empty backtest_runs table → empty dict."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    _create_backtest_table(db)

    conn = _make_conn(db)
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    assert result == {}


def test_latest_backtest_metrics_multiple_strategies(tmp_path: Path) -> None:
    """Multiple strategies each get their own latest run entry."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    _create_backtest_table(db)
    _seed_run(db, run_id="r1", strategy_id="s1", sharpe=1.0, trade_count=30)
    _seed_run(db, run_id="r2", strategy_id="s2", sharpe=0.6, trade_count=20)
    _seed_run(db, run_id="r3", strategy_id="s1", sharpe=1.8, trade_count=50)

    conn = _make_conn(db)
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    assert set(result.keys()) == {"s1", "s2"}
    assert result["s1"]["run_id"] == "r3"
    assert result["s1"]["sharpe"] == 1.8
    assert result["s2"]["run_id"] == "r2"


def test_latest_backtest_metrics_sqlite_error_returns_empty(tmp_path: Path) -> None:
    """sqlite3.Error (no table) → defensive {} return, no exception propagated."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    # Create a DB with NO backtest_runs table
    conn2 = sqlite3.connect(str(db))
    conn2.execute("CREATE TABLE other(a INTEGER)")
    conn2.commit()
    conn2.close()

    conn = _make_conn(db)
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    assert result == {}


def test_latest_backtest_metrics_keys_present(tmp_path: Path) -> None:
    """Return dict always has run_id, started_at, sharpe, max_drawdown_pct, trade_count."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    _create_backtest_table(db)
    # Seed with empty metadata (no oos_aggregate)
    _seed_run(db, run_id="r1", strategy_id="strat.e")

    conn = _make_conn(db)
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    m = result["strat.e"]
    assert set(m.keys()) == {"run_id", "started_at", "sharpe", "max_drawdown_pct", "trade_count"}
    assert m["sharpe"] is None
    assert m["max_drawdown_pct"] is None
    assert m["trade_count"] is None


# ---------------------------------------------------------------------------
# Additional robustness: non-dict oos_aggregate sub-values
# ---------------------------------------------------------------------------


def test_oos_aggregate_metrics_oos_aggregate_is_list() -> None:
    """oos_aggregate value is a list → all-None, no exception."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics(json.dumps({"oos_aggregate": [1, 2, 3]}))
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


def test_oos_aggregate_metrics_oos_aggregate_is_null() -> None:
    """oos_aggregate value is null (None) → all-None, no exception."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics(json.dumps({"oos_aggregate": None}))
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


def test_oos_aggregate_metrics_oos_aggregate_is_number() -> None:
    """oos_aggregate value is a number → all-None, no exception."""
    from milodex.gui._event_queries import oos_aggregate_metrics

    result = oos_aggregate_metrics(json.dumps({"oos_aggregate": 42}))
    assert result == {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}


# ---------------------------------------------------------------------------
# latest_backtest_metrics: plain-tuple (no row_factory) fallback
# ---------------------------------------------------------------------------


def test_latest_backtest_metrics_plain_tuple_rows(tmp_path: Path) -> None:
    """latest_backtest_metrics works correctly when row_factory is NOT set (plain tuples)."""
    from milodex.gui._event_queries import latest_backtest_metrics

    db = tmp_path / "test.db"
    _create_backtest_table(db)
    _seed_run(
        db,
        run_id="run-tuple",
        strategy_id="strat.tuple",
        sharpe=1.11,
        max_drawdown_pct=3.5,
        trade_count=55,
        started_at="2026-04-01T00:00:00+00:00",
    )

    # Deliberately do NOT set row_factory — default sqlite3.Connection has no factory.
    conn = sqlite3.connect(str(db))
    try:
        result = latest_backtest_metrics(conn)
    finally:
        conn.close()

    assert "strat.tuple" in result
    m = result["strat.tuple"]
    assert m["run_id"] == "run-tuple"
    assert m["started_at"] == "2026-04-01T00:00:00+00:00"
    assert m["sharpe"] == 1.11
    assert m["max_drawdown_pct"] == 3.5
    assert m["trade_count"] == 55

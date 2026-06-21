"""Tests for database migrations.

Seed strategy: run migrations 001-009 against a raw sqlite3 connection, set
_schema_version = 9, INSERT fixture rows, then construct EventStore (which
triggers migration 010 atomically).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from milodex.core.event_store import EventStore

# ─── helpers ──────────────────────────────────────────────────────────────────


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "src" / "milodex" / "core" / "migrations"


def _run_migrations_up_to(conn: sqlite3.Connection, max_version: int) -> None:
    """Apply numbered SQL migrations in order up to max_version.

    Uses executescript (which issues implicit COMMIT before running) so SQL
    comments and multi-statement files parse correctly. _schema_version is
    set after each migration via a separate execute/commit pair.
    """
    conn.executescript("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)")
    migrations_dir = _migrations_dir()
    for path in sorted(migrations_dir.glob("*.sql")):
        version = int(path.stem.split("_", maxsplit=1)[0])
        if version > max_version:
            continue
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute("DELETE FROM _schema_version")
        conn.execute("INSERT INTO _schema_version(version) VALUES (?)", (version,))
        conn.commit()


def _seed_pre_migration_rows(conn: sqlite3.Connection) -> None:
    """Insert fixture rows that simulate the pre-010 mixed-table state.

    Inserts:
    - 3 walk-forward (:w) backtest rows — should move to backtest_equity_snapshots
    - 2 whole-period backtest rows (session_id IN backtest_runs.run_id) — should move
    - 2 broker rows (session_id in strategy_runs) — should stay in portfolio_snapshots

    Also inserts the corresponding backtest_runs and strategy_runs rows so FKs work.
    """
    empty_positions = json.dumps([])
    bt_run_uuid = "aaaaaaaa-0000-0000-0000-000000000001"
    bt_run_uuid2 = "aaaaaaaa-0000-0000-0000-000000000002"

    # backtest_runs rows (needed for the whole-period backtest rows)
    conn.execute(
        """INSERT INTO backtest_runs (run_id, strategy_id, start_date, end_date,
               started_at, ended_at, status, config_hash, metadata_json)
           VALUES (?, 'test.strategy.v1', '2025-01-01', '2025-12-31',
                   '2026-01-01T00:00:00+00:00', '2026-01-31T00:00:00+00:00',
                   'completed', 'abc', '{}')""",
        (bt_run_uuid,),
    )
    conn.execute(
        """INSERT INTO backtest_runs (run_id, strategy_id, start_date, end_date,
               started_at, ended_at, status, config_hash, metadata_json)
           VALUES (?, 'test.strategy.v2', '2025-01-01', '2025-12-31',
                   '2026-01-01T00:00:00+00:00', '2026-01-31T00:00:00+00:00',
                   'completed', 'def', '{}')""",
        (bt_run_uuid2,),
    )

    # strategy_runs rows (for the broker snapshot sessions)
    broker_session_1 = "cccccccc-0000-0000-0000-000000000001"
    broker_session_2 = "cccccccc-0000-0000-0000-000000000002"
    conn.execute(
        """INSERT INTO strategy_runs (session_id, strategy_id, started_at, metadata_json)
           VALUES (?, 'test.strategy.v1', '2026-04-27T21:00:00+00:00', '{}')""",
        (broker_session_1,),
    )
    conn.execute(
        """INSERT INTO strategy_runs (session_id, strategy_id, started_at, metadata_json)
           VALUES (?, 'test.strategy.v1', '2026-04-28T21:00:00+00:00', '{}')""",
        (broker_session_2,),
    )

    # Walk-forward backtest rows (should migrate to backtest_equity_snapshots)
    for i in range(3):
        conn.execute(
            """INSERT INTO portfolio_snapshots
               (recorded_at, session_id, strategy_id, equity, cash, portfolio_value,
                daily_pnl, positions_json)
               VALUES (?, ?, 'test.strategy.v1', ?, 1000.0, ?, 0.0, ?)""",
            (
                f"2025-12-{i + 10:02d}T00:00:00+00:00",
                f"{bt_run_uuid}:w{i}",
                1000.0 + i * 100,
                1000.0 + i * 100,
                empty_positions,
            ),
        )

    # Whole-period backtest rows (should migrate to backtest_equity_snapshots)
    conn.execute(
        """INSERT INTO portfolio_snapshots
           (recorded_at, session_id, strategy_id, equity, cash, portfolio_value,
            daily_pnl, positions_json)
           VALUES ('2025-12-31T00:00:00+00:00', ?, 'test.strategy.v1',
                   5000.0, 1000.0, 5000.0, 0.0, ?)""",
        (bt_run_uuid, empty_positions),
    )
    conn.execute(
        """INSERT INTO portfolio_snapshots
           (recorded_at, session_id, strategy_id, equity, cash, portfolio_value,
            daily_pnl, positions_json)
           VALUES ('2025-12-31T01:00:00+00:00', ?, 'test.strategy.v2',
                   6000.0, 1200.0, 6000.0, 0.0, ?)""",
        (bt_run_uuid2, empty_positions),
    )

    # Broker snapshot rows (should remain in portfolio_snapshots)
    conn.execute(
        """INSERT INTO portfolio_snapshots
           (recorded_at, session_id, strategy_id, equity, cash, portfolio_value,
            daily_pnl, positions_json)
           VALUES ('2026-04-27T21:09:11+00:00', ?, 'test.strategy.v1',
                   100318.05, 50000.0, 100318.05, 12.5, ?)""",
        (broker_session_1, empty_positions),
    )
    conn.execute(
        """INSERT INTO portfolio_snapshots
           (recorded_at, session_id, strategy_id, equity, cash, portfolio_value,
            daily_pnl, positions_json)
           VALUES ('2026-04-28T21:00:00+00:00', ?, 'test.strategy.v1',
                   100465.62, 50100.0, 100465.62, 25.0, ?)""",
        (broker_session_2, empty_positions),
    )

    conn.commit()


# ─── tests ────────────────────────────────────────────────────────────────────


def test_010_migration_splits_backtest_and_quarantines_stray(tmp_path):
    """Migration 010 invariants:

    1. No :w rows remain in portfolio_snapshots.
    2. All :w rows moved to backtest_equity_snapshots.
    3. Whole-period backtest rows moved to backtest_equity_snapshots with
       backtest_run_id populated.
    4. Broker-only rows remain in portfolio_snapshots.
    5. portfolio_snapshots_quarantine table exists (empty in normal case).
    """
    db_path = tmp_path / "milodex.db"

    # Seed: apply 001-009, insert fixtures, then open EventStore (triggers 010)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _run_migrations_up_to(conn, 9)
        _seed_pre_migration_rows(conn)

    # Opening EventStore applies migrations 010 through current head.
    store = EventStore(db_path)
    assert store.schema_version == 15

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # Invariant 1: no :w rows in portfolio_snapshots
        w_in_broker = conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots WHERE session_id LIKE '%:w%'"
        ).fetchone()[0]
        assert w_in_broker == 0, f"Expected 0 :w rows in portfolio_snapshots, got {w_in_broker}"

        # Invariant 2: all 3 :w rows moved to backtest_equity_snapshots
        w_in_backtest = conn.execute(
            "SELECT COUNT(*) FROM backtest_equity_snapshots WHERE session_id LIKE '%:w%'"
        ).fetchone()[0]
        assert w_in_backtest == 3, (
            f"Expected 3 :w rows in backtest_equity_snapshots, got {w_in_backtest}"
        )

        # Invariant 3: whole-period backtest rows moved with backtest_run_id populated
        whole_period = conn.execute(
            """SELECT COUNT(*) FROM backtest_equity_snapshots
               WHERE session_id NOT LIKE '%:w%'
                 AND backtest_run_id IS NOT NULL"""
        ).fetchone()[0]
        assert whole_period == 2, (
            f"Expected 2 whole-period rows in backtest_equity_snapshots, got {whole_period}"
        )

        # Invariant 4: broker rows still in portfolio_snapshots (2 broker rows)
        broker_rows = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
        assert broker_rows == 2, f"Expected 2 broker rows in portfolio_snapshots, got {broker_rows}"

        # Invariant 5: quarantine table exists (empty in this scenario)
        quarantine_rows = conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots_quarantine"
        ).fetchone()[0]
        assert quarantine_rows == 0


def test_010_migration_idempotent(tmp_path):
    """Running migration 010 twice (by opening EventStore twice) is a no-op.

    Row counts should be identical after the second open.
    """
    db_path = tmp_path / "milodex.db"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _run_migrations_up_to(conn, 9)
        _seed_pre_migration_rows(conn)

    # First open: applies 010
    EventStore(db_path)

    with sqlite3.connect(db_path) as conn:
        broker_after_first = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
        backtest_after_first = conn.execute(
            "SELECT COUNT(*) FROM backtest_equity_snapshots"
        ).fetchone()[0]

    # Second open: migration 010 is already applied (version 10), must be no-op
    EventStore(db_path)

    with sqlite3.connect(db_path) as conn:
        broker_after_second = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
        backtest_after_second = conn.execute(
            "SELECT COUNT(*) FROM backtest_equity_snapshots"
        ).fetchone()[0]

    assert broker_after_first == broker_after_second
    assert backtest_after_first == backtest_after_second


def test_011_creates_risk_profile_changes_table(tmp_path):
    """Migration 011 creates the risk_profile_changes audit table with the
    correct schema (ADR 0054)."""
    db_path = tmp_path / "milodex.db"

    store = EventStore(db_path)
    assert store.schema_version == 15

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='risk_profile_changes'"
        )
        assert cur.fetchone() is not None, (
            "risk_profile_changes table must exist after migration 011"
        )

        cur = conn.execute("PRAGMA table_info(risk_profile_changes)")
        cols = {row[1] for row in cur.fetchall()}
        assert {
            "id",
            "recorded_at",
            "from_profile",
            "to_profile",
            "actor",
            "confirmation_method",
            "context_mode",
            "runners_active_count",
            "success",
            "failure_reason",
        } <= cols

        # Index must exist
        cur = conn.execute("PRAGMA index_list(risk_profile_changes)")
        index_names = {row[1] for row in cur.fetchall()}
        assert "idx_risk_profile_changes_time" in index_names


def test_014_creates_execution_attempts_on_existing_store(tmp_path):
    """Migration 014 applies cleanly on an existing populated store (P1-02).

    Seed: apply 001-013 raw, insert an explanation+trade pair, then open
    EventStore (triggers 014). The new outbox table, its indexes, and the
    trades(broker_order_id) correlation index must exist; pre-existing rows
    must be untouched.
    """
    db_path = tmp_path / "milodex.db"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _run_migrations_up_to(conn, 13)
        cursor = conn.execute(
            """INSERT INTO explanations
               (recorded_at, decision_type, status, symbol, side, quantity, order_type,
                time_in_force, submitted_by, market_open, account_equity, account_cash,
                account_portfolio_value, account_daily_pnl, risk_allowed, risk_summary,
                reason_codes_json, risk_checks_json, context_json)
               VALUES ('2026-06-10T20:00:00+00:00', 'submit', 'submitted', 'SPY', 'buy',
                       1.0, 'market', 'day', 'operator', 1, 10000.0, 10000.0, 10000.0,
                       0.0, 1, 'Allowed', '[]', '[]', '{}')"""
        )
        conn.execute(
            """INSERT INTO trades
               (explanation_id, recorded_at, status, source, symbol, side, quantity,
                order_type, time_in_force, estimated_unit_price, estimated_order_value,
                submitted_by, broker_order_id)
               VALUES (?, '2026-06-10T20:00:00+00:00', 'submitted', 'paper', 'SPY', 'buy',
                       1.0, 'market', 'day', 400.0, 400.0, 'operator', 'b-1')""",
            (cursor.lastrowid,),
        )
        conn.commit()

    store = EventStore(db_path)
    assert store.schema_version == 15

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_attempts'"
        )
        assert cur.fetchone() is not None, "execution_attempts table must exist after migration 014"

        cols = {row[1] for row in conn.execute("PRAGMA table_info(execution_attempts)")}
        assert {
            "id",
            "client_order_id",
            "strategy_name",
            "strategy_config_path",
            "session_id",
            "symbol",
            "side",
            "quantity",
            "order_type",
            "created_at",
            "status",
            "broker_order_id",
            "finalized_at",
            "failure_detail",
        } <= cols

        attempt_indexes = {row[1] for row in conn.execute("PRAGMA index_list(execution_attempts)")}
        assert "idx_execution_attempts_symbol_status_created" in attempt_indexes
        assert "idx_execution_attempts_status_created" in attempt_indexes
        trade_indexes = {row[1] for row in conn.execute("PRAGMA index_list(trades)")}
        assert "idx_trades_broker_order_id" in trade_indexes

        # Pre-existing rows survive untouched.
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM explanations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 0

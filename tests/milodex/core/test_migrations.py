"""Tests for database migrations.

Seed strategy: run migrations 001-009 against a raw sqlite3 connection, set
_schema_version = 9, INSERT fixture rows, then construct EventStore (which
triggers migration 010 atomically).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

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
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)"
    )
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
    stray_session = "bbbbbbbb-0000-0000-0000-000000000099"

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
                f"2025-12-{i+10:02d}T00:00:00+00:00",
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

    # Opening EventStore applies migration 010
    store = EventStore(db_path)
    assert store.schema_version == 10

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
        assert w_in_backtest == 3, f"Expected 3 :w rows in backtest_equity_snapshots, got {w_in_backtest}"

        # Invariant 3: whole-period backtest rows moved with backtest_run_id populated
        whole_period = conn.execute(
            """SELECT COUNT(*) FROM backtest_equity_snapshots
               WHERE session_id NOT LIKE '%:w%'
                 AND backtest_run_id IS NOT NULL"""
        ).fetchone()[0]
        assert whole_period == 2, f"Expected 2 whole-period rows in backtest_equity_snapshots, got {whole_period}"

        # Invariant 4: broker rows still in portfolio_snapshots (2 broker rows)
        broker_rows = conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots"
        ).fetchone()[0]
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
        broker_after_first = conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots"
        ).fetchone()[0]
        backtest_after_first = conn.execute(
            "SELECT COUNT(*) FROM backtest_equity_snapshots"
        ).fetchone()[0]

    # Second open: migration 010 is already applied (version 10), must be no-op
    EventStore(db_path)

    with sqlite3.connect(db_path) as conn:
        broker_after_second = conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots"
        ).fetchone()[0]
        backtest_after_second = conn.execute(
            "SELECT COUNT(*) FROM backtest_equity_snapshots"
        ).fetchone()[0]

    assert broker_after_first == broker_after_second
    assert backtest_after_first == backtest_after_second

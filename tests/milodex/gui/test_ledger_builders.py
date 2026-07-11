"""Unit tests for ``milodex.gui.ledger_builders`` (331 lines).

Pure-Python builders — the module imports no Qt (verified below), so these
tests drive the individual row-builder functions directly against a raw
sqlite3 connection (schema from the real, fully-migrated ``EventStore`` via
the shared ``event_store_db`` fixture in ``tests/milodex/gui/conftest.py``),
bypassing the GUI read-model / polling machinery entirely.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from milodex.gui.ledger_builders import (
    _LEDGER_SOURCE_PRIORITY,
    _backtest_complete_entries,
    _ledger_entries,
    _new_strategy_entries,
    _session_start_entries,
    _session_stop_entries,
)
from milodex.promotion.policy import ACTIVE_PROMOTION_POLICY


def test_ledger_builders_module_imports_no_qt() -> None:
    """Guard the "no Qt needed" premise this test file relies on."""
    import milodex.gui.ledger_builders as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "PySide6" not in source
    assert "QtCore" not in source


def _open_row_conn(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _seed_backtest_row(
    db: Path,
    *,
    run_id: str,
    strategy_id: str,
    ended_at: str,
    sharpe: float | None,
) -> None:
    metadata: dict = {}
    if sharpe is not None:
        metadata = {"oos_aggregate": {"sharpe": sharpe, "max_drawdown_pct": 5.0, "trade_count": 40}}
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, strategy_id, start_date, end_date, started_at, ended_at, status, metadata_json)
        VALUES (?, ?, '2020-01-01', '2020-06-01', '2026-01-01T00:00:00+00:00', ?, 'completed', ?)
        """,
        (run_id, strategy_id, ended_at, json.dumps(metadata)),
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# _backtest_complete_entries: Sharpe tiering (lines 192-199)
# --------------------------------------------------------------------------- #

_PAPER_GATE = ACTIVE_PROMOTION_POLICY.paper_gate.min_sharpe
_CAPITAL_GATE = ACTIVE_PROMOTION_POLICY.capital_gate.min_sharpe


def test_backtest_complete_entries_none_sharpe_is_backtested(event_store_db: Path) -> None:
    db = event_store_db
    _seed_backtest_row(
        db,
        run_id="run-none",
        strategy_id="meanrev.daily.a.v1",
        ended_at="2026-05-01T00:00:00+00:00",
        sharpe=None,
    )
    conn = _open_row_conn(db)
    entries = _backtest_complete_entries(conn)
    conn.close()

    assert len(entries) == 1
    assert entries[0]["outcomeKind"] == "backtested"


def test_backtest_complete_entries_sharpe_at_capital_gate_is_strong(event_store_db: Path) -> None:
    """Boundary: sharpe == capital_gate.min_sharpe is inclusive (>=)."""
    db = event_store_db
    _seed_backtest_row(
        db,
        run_id="run-strong-boundary",
        strategy_id="meanrev.daily.b.v1",
        ended_at="2026-05-01T00:00:00+00:00",
        sharpe=_CAPITAL_GATE,
    )
    conn = _open_row_conn(db)
    entries = _backtest_complete_entries(conn)
    conn.close()

    assert len(entries) == 1
    assert entries[0]["outcomeKind"] == "backtested_strong"


def test_backtest_complete_entries_sharpe_just_below_capital_gate_is_paper(
    event_store_db: Path,
) -> None:
    db = event_store_db
    _seed_backtest_row(
        db,
        run_id="run-paper-high",
        strategy_id="meanrev.daily.c.v1",
        ended_at="2026-05-01T00:00:00+00:00",
        sharpe=_CAPITAL_GATE - 0.01,
    )
    conn = _open_row_conn(db)
    entries = _backtest_complete_entries(conn)
    conn.close()

    assert len(entries) == 1
    assert entries[0]["outcomeKind"] == "backtested_paper"


def test_backtest_complete_entries_sharpe_at_paper_gate_is_paper(event_store_db: Path) -> None:
    """Boundary: sharpe == paper_gate.min_sharpe is inclusive (>=)."""
    db = event_store_db
    _seed_backtest_row(
        db,
        run_id="run-paper-boundary",
        strategy_id="meanrev.daily.d.v1",
        ended_at="2026-05-01T00:00:00+00:00",
        sharpe=_PAPER_GATE,
    )
    conn = _open_row_conn(db)
    entries = _backtest_complete_entries(conn)
    conn.close()

    assert len(entries) == 1
    assert entries[0]["outcomeKind"] == "backtested_paper"


def test_backtest_complete_entries_sharpe_below_paper_gate_is_weak(event_store_db: Path) -> None:
    db = event_store_db
    _seed_backtest_row(
        db,
        run_id="run-weak",
        strategy_id="meanrev.daily.e.v1",
        ended_at="2026-05-01T00:00:00+00:00",
        sharpe=_PAPER_GATE - 0.01,
    )
    conn = _open_row_conn(db)
    entries = _backtest_complete_entries(conn)
    conn.close()

    assert len(entries) == 1
    assert entries[0]["outcomeKind"] == "backtested_weak"


def test_backtest_complete_entries_reason_includes_sharpe_and_trade_count(
    event_store_db: Path,
) -> None:
    db = event_store_db
    _seed_backtest_row(
        db,
        run_id="run-reason",
        strategy_id="meanrev.daily.f.v1",
        ended_at="2026-05-01T00:00:00+00:00",
        sharpe=0.72,
    )
    conn = _open_row_conn(db)
    entries = _backtest_complete_entries(conn)
    conn.close()

    assert "Sharpe 0.72" in entries[0]["reason"]
    assert "n=40" in entries[0]["reason"]


# --------------------------------------------------------------------------- #
# _session_start_entries / _session_stop_entries
# --------------------------------------------------------------------------- #


def test_session_start_entries_basic(event_store_db: Path) -> None:
    db = event_store_db
    sid = "meanrev.daily.g.v1"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-1', ?, '2026-05-01T09:30:00+00:00', NULL, NULL, '{}')""",
        (sid,),
    )
    conn.commit()
    conn.close()

    row_conn = _open_row_conn(db)
    entries = _session_start_entries(row_conn)
    row_conn.close()

    assert len(entries) == 1
    assert entries[0]["strategyId"] == sid
    assert entries[0]["outcomeKind"] == "started"
    assert entries[0]["outcome"] == "STARTED"
    assert entries[0]["timestamp"] == "2026-05-01T09:30:00+00:00"


def test_session_stop_entries_basic(event_store_db: Path) -> None:
    db = event_store_db
    sid = "meanrev.daily.i.v1"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-2', ?, '2026-05-01T09:30:00+00:00',
                   '2026-05-01T16:00:00+00:00', 'controlled_stop', '{}')""",
        (sid,),
    )
    conn.commit()
    conn.close()

    row_conn = _open_row_conn(db)
    entries = _session_stop_entries(row_conn)
    row_conn.close()

    assert len(entries) == 1
    assert entries[0]["strategyId"] == sid
    assert entries[0]["outcomeKind"] == "stopped"
    assert entries[0]["outcome"] == "STOPPED"
    assert entries[0]["reason"] == "controlled_stop"


@pytest.mark.parametrize(
    "excluded_reason", ["kill_switch", "orphan_recovered", "orphaned_no_live_runner"]
)
def test_session_stop_entries_excludes_non_operator_exit_reasons(
    event_store_db: Path, excluded_reason: str
) -> None:
    db = event_store_db
    sid = "meanrev.daily.j.v1"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-excl', ?, '2026-05-01T09:30:00+00:00',
                   '2026-05-01T16:00:00+00:00', ?, '{}')""",
        (sid, excluded_reason),
    )
    conn.commit()
    conn.close()

    row_conn = _open_row_conn(db)
    entries = _session_stop_entries(row_conn)
    row_conn.close()

    assert entries == []


def test_session_stop_entries_excludes_open_sessions(event_store_db: Path) -> None:
    """A still-open session (ended_at IS NULL) never produces a stop row."""
    db = event_store_db
    sid = "meanrev.daily.k.v1"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-open', ?, '2026-05-01T09:30:00+00:00', NULL, NULL, '{}')""",
        (sid,),
    )
    conn.commit()
    conn.close()

    row_conn = _open_row_conn(db)
    entries = _session_stop_entries(row_conn)
    row_conn.close()

    assert entries == []


# --------------------------------------------------------------------------- #
# _new_strategy_entries: event-store-vs-YAML-mtime fallback (lines 227-287)
# --------------------------------------------------------------------------- #


def test_new_strategy_entries_uses_event_store_first_seen(
    event_store_db: Path, tmp_path: Path
) -> None:
    db = event_store_db
    configs = tmp_path / "configs"
    configs.mkdir()
    sid = "meanrev.daily.l.v1"

    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-3', ?, '2026-05-01T00:00:00+00:00', NULL, NULL, '{}')""",
        (sid,),
    )
    conn.commit()
    conn.close()

    row_conn = _open_row_conn(db)
    entries = _new_strategy_entries(row_conn, configs)
    row_conn.close()

    assert len(entries) == 1
    assert entries[0]["strategyId"] == sid
    assert entries[0]["outcomeKind"] == "added"
    assert entries[0]["timestamp"] == "2026-05-01T00:00:00+00:00"
    assert "event store" in entries[0]["reason"]


def test_new_strategy_entries_picks_earliest_across_sources(
    event_store_db: Path, tmp_path: Path
) -> None:
    """MIN(first_at) across promotions/strategy_runs/backtest_runs — the
    earliest cross-source timestamp wins, not insertion order."""
    db = event_store_db
    configs = tmp_path / "configs"
    configs.mkdir()
    sid = "meanrev.daily.m.v1"

    conn = sqlite3.connect(str(db))
    # Later promotion event.
    conn.execute(
        """INSERT INTO promotions
           (recorded_at, strategy_id, from_stage, to_stage, promotion_type, approved_by, notes)
           VALUES ('2026-05-10T00:00:00+00:00', ?, 'backtest', 'paper', 'statistical',
                   'test', 'x')""",
        (sid,),
    )
    # Earlier strategy_runs start — this is the true "first seen" moment.
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-4', ?, '2026-05-01T00:00:00+00:00', NULL, NULL, '{}')""",
        (sid,),
    )
    conn.commit()
    conn.close()

    row_conn = _open_row_conn(db)
    entries = _new_strategy_entries(row_conn, configs)
    row_conn.close()

    assert len(entries) == 1
    assert entries[0]["timestamp"] == "2026-05-01T00:00:00+00:00"


def test_new_strategy_entries_yaml_mtime_fallback_for_no_history_strategy(
    event_store_db: Path, tmp_path: Path
) -> None:
    db = event_store_db
    configs = tmp_path / "configs"
    configs.mkdir()
    yaml_path = configs / "strategy.yaml"
    yaml_path.write_text(
        "strategy:\n  id: meanrev.daily.n.v1\n",
        encoding="utf-8",
    )

    row_conn = _open_row_conn(db)
    entries = _new_strategy_entries(row_conn, configs)
    row_conn.close()

    assert len(entries) == 1
    assert entries[0]["strategyId"] == "meanrev.daily.n.v1"
    assert entries[0]["outcomeKind"] == "added"
    assert "mtime" in entries[0]["reason"]


def test_new_strategy_entries_yaml_fallback_skipped_when_event_history_exists(
    event_store_db: Path, tmp_path: Path
) -> None:
    """A strategy with event-store history is NOT duplicated by the YAML
    fallback even if a config file for the same id also exists."""
    db = event_store_db
    configs = tmp_path / "configs"
    configs.mkdir()
    sid = "meanrev.daily.o.v1"

    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-5', ?, '2026-05-01T00:00:00+00:00', NULL, NULL, '{}')""",
        (sid,),
    )
    conn.commit()
    conn.close()

    yaml_path = configs / "strategy.yaml"
    yaml_path.write_text(f"strategy:\n  id: {sid}\n", encoding="utf-8")

    row_conn = _open_row_conn(db)
    entries = _new_strategy_entries(row_conn, configs)
    row_conn.close()

    assert len(entries) == 1
    assert entries[0]["timestamp"] == "2026-05-01T00:00:00+00:00"
    assert "event store" in entries[0]["reason"]


# --------------------------------------------------------------------------- #
# _LEDGER_SOURCE_PRIORITY tie-break sort (lines 290-323)
# --------------------------------------------------------------------------- #


def test_ledger_source_priority_ordering_matches_documented_precedence() -> None:
    """promotion(0) < lifecycle-fired(1) < lifecycle-started(2)
    < backtest(3) < added(4)."""
    assert _LEDGER_SOURCE_PRIORITY["promoted"] == 0
    assert _LEDGER_SOURCE_PRIORITY["demoted"] == 0
    assert _LEDGER_SOURCE_PRIORITY["returned"] == 0
    assert _LEDGER_SOURCE_PRIORITY["fired"] == 1
    assert _LEDGER_SOURCE_PRIORITY["started"] == 2
    assert _LEDGER_SOURCE_PRIORITY["stopped"] == 2
    assert _LEDGER_SOURCE_PRIORITY["backtested_strong"] == 3
    assert _LEDGER_SOURCE_PRIORITY["added"] == 4
    assert (
        _LEDGER_SOURCE_PRIORITY["promoted"]
        < _LEDGER_SOURCE_PRIORITY["fired"]
        < _LEDGER_SOURCE_PRIORITY["started"]
        < _LEDGER_SOURCE_PRIORITY["backtested_strong"]
        < _LEDGER_SOURCE_PRIORITY["added"]
    )


def test_ledger_entries_tie_break_orders_promoted_before_started_before_added(
    event_store_db: Path, tmp_path: Path
) -> None:
    """Two entries sharing an identical timestamp must sort by ascending
    source priority — lower priority number ranks first — not by insertion
    order (Task 24 cross-source ordering)."""
    db = event_store_db
    configs = tmp_path / "configs"
    configs.mkdir()
    sid_promoted = "meanrev.daily.p.v1"
    sid_started = "meanrev.daily.q.v1"
    ts = "2026-05-01T00:00:00+00:00"

    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO promotions
           (recorded_at, strategy_id, from_stage, to_stage, promotion_type, approved_by, notes)
           VALUES (?, ?, 'backtest', 'paper', 'statistical', 'test', 'x')""",
        (ts, sid_promoted),
    )
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-tie', ?, ?, NULL, NULL, '{}')""",
        (sid_started, ts),
    )
    conn.commit()
    conn.close()

    row_conn = _open_row_conn(db)
    entries = _ledger_entries(row_conn, configs)
    row_conn.close()

    tied = [e for e in entries if e["timestamp"] == ts]
    kinds_in_order = [e["outcomeKind"] for e in tied]

    promoted_idx = kinds_in_order.index("promoted")
    started_idx = kinds_in_order.index("started")
    added_idxs = [i for i, k in enumerate(kinds_in_order) if k == "added"]

    assert promoted_idx < started_idx
    assert all(started_idx < i for i in added_idxs)

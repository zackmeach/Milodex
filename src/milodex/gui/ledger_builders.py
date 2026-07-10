"""Ledger entry builders for the paper-of-record GUI surface.

Assembles the unified, time-ordered ledger from the event store's promotion,
kill-switch, session, backtest, and first-appearance sources.  Read-only:
every function consumes a read-only SQLite connection and returns plain dicts.

Extracted verbatim from ``read_models.py`` (PR12 decompose). No behavior
changed — definitions were moved, not rewritten.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from milodex.gui import _event_queries
from milodex.gui._db_logging import log_db_read_error
from milodex.gui.row_formatters import (
    _compact_timestamp,
    _short_strategy_name,
    _strategy_id_from_yaml,
)


def _promotion_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ledger rows from the promotions table (promoted/demoted/returned)."""
    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute("SELECT * FROM promotions ORDER BY id DESC LIMIT 200").fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("ledger_builders._promotion_entries", exc)
        return []
    for row in rows:
        promotion_type = str(row["promotion_type"])
        if promotion_type == "demotion":
            outcome_kind = "demoted"
        elif promotion_type == "stage_return":
            outcome_kind = "returned"
        else:
            outcome_kind = "promoted"
        entries.append(
            {
                "timestamp": row["recorded_at"],
                "displayTimestamp": _compact_timestamp(str(row["recorded_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": row["to_stage"],
                "transition": f"{row['from_stage']} -> {row['to_stage']}",
                "outcome": _ledger_outcome_label(outcome_kind),
                "outcomeKind": outcome_kind,
                "reason": row["notes"] or promotion_type,
                "recent": True,
            }
        )
    return entries


def _kill_switch_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ledger rows from kill_switch_events."""
    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM kill_switch_events ORDER BY id DESC LIMIT 100"
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("ledger_builders._kill_switch_entries", exc)
        return []
    for row in rows:
        entries.append(
            {
                "timestamp": row["recorded_at"],
                "displayTimestamp": _compact_timestamp(str(row["recorded_at"])),
                "strategyId": "",
                "subject": "kill switch",
                "stage": "system",
                "transition": "session",
                "outcome": str(row["event_type"]).upper(),
                # The store's only writers (execution/state.py activate/reset
                # and the legacy-state migration) write event_type 'activated'
                # / 'reset' — no production row is 'triggered'. Matching
                # 'triggered' rendered every real activation as neutral "info".
                "outcomeKind": "fired" if row["event_type"] == "activated" else "info",
                "reason": row["reason"] or "",
                "recent": True,
            }
        )
    return entries


def _session_start_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ledger rows for strategy session starts."""
    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            """
            SELECT strategy_id, started_at, session_id
            FROM strategy_runs
            WHERE started_at IS NOT NULL
            ORDER BY started_at DESC LIMIT 200
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("ledger_builders._session_start_entries", exc)
        return []
    for row in rows:
        entries.append(
            {
                "timestamp": row["started_at"],
                "displayTimestamp": _compact_timestamp(str(row["started_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": "lifecycle",
                "transition": "session",
                "outcome": "STARTED",
                "outcomeKind": "started",
                "reason": "trading session began",
                "recent": True,
            }
        )
    return entries


def _session_stop_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Sessions ended for operator-initiated reasons. EXCLUDES kill-switch
    and both orphan closures (those emit their own ledger rows from
    kill_switch_events or are synthetic reconciliation rows —
    'orphan_recovered' from the runner's startup self-reconcile,
    'orphaned_no_live_runner' from the GUI bootstrap/periodic reaper)."""
    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            """
            SELECT strategy_id, ended_at, exit_reason, session_id
            FROM strategy_runs
            WHERE ended_at IS NOT NULL
              AND exit_reason NOT IN
                ('kill_switch', 'orphan_recovered', 'orphaned_no_live_runner')
            ORDER BY ended_at DESC LIMIT 200
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("ledger_builders._session_stop_entries", exc)
        return []
    for row in rows:
        entries.append(
            {
                "timestamp": row["ended_at"],
                "displayTimestamp": _compact_timestamp(str(row["ended_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": "lifecycle",
                "transition": "session",
                "outcome": "STOPPED",
                "outcomeKind": "stopped",
                "reason": row["exit_reason"] or "stopped",
                "recent": True,
            }
        )
    return entries


def _backtest_complete_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ledger rows for completed backtest runs.

    Actual columns (confirmed via PRAGMA): ended_at, status, metadata_json.
    Sharpe lives at metadata_json.oos_aggregate.sharpe.
    Three-tone color binding to promotion policy thresholds.
    """
    from milodex.promotion.policy import ACTIVE_PROMOTION_POLICY  # noqa: PLC0415

    paper_gate = ACTIVE_PROMOTION_POLICY.paper_gate.min_sharpe
    capital_gate = ACTIVE_PROMOTION_POLICY.capital_gate.min_sharpe

    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            """
            SELECT id, strategy_id, ended_at, status, metadata_json
            FROM backtest_runs
            WHERE status = 'completed' AND ended_at IS NOT NULL
            ORDER BY ended_at DESC LIMIT 200
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("ledger_builders._backtest_complete_entries", exc)
        return []
    for row in rows:
        metrics = _event_queries.oos_aggregate_metrics(row["metadata_json"])
        sharpe = metrics["sharpe"]
        if sharpe is None:
            kind = "backtested"
        elif sharpe >= capital_gate:
            kind = "backtested_strong"
        elif sharpe >= paper_gate:
            kind = "backtested_paper"
        else:
            kind = "backtested_weak"

        reason_parts = []
        if sharpe is not None:
            reason_parts.append(f"Sharpe {sharpe:.2f}")
        if metrics["max_drawdown_pct"] is not None:
            reason_parts.append(f"max-dd {abs(metrics['max_drawdown_pct']) * 100:.1f}%")
        if metrics["trade_count"] is not None:
            reason_parts.append(f"n={metrics['trade_count']}")
        reason = " · ".join(reason_parts) or "completed"

        entries.append(
            {
                "timestamp": row["ended_at"],
                "displayTimestamp": _compact_timestamp(str(row["ended_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": "backtest",
                "transition": "backtest",
                "outcome": "COMPLETED",
                "outcomeKind": kind,
                "reason": reason,
                "recent": True,
            }
        )
    return entries


def _new_strategy_entries(conn: sqlite3.Connection, configs_dir: Path) -> list[dict[str, Any]]:
    """First appearance per strategy_id across event tables.

    YAML mtime fallback only for strategies with no event-store history.
    """
    try:
        rows = conn.execute(
            """
            WITH first_seen AS (
                SELECT strategy_id, recorded_at AS first_at FROM promotions
                UNION ALL
                SELECT strategy_id, started_at FROM strategy_runs WHERE started_at IS NOT NULL
                UNION ALL
                SELECT strategy_id, started_at FROM backtest_runs WHERE started_at IS NOT NULL
            )
            SELECT strategy_id, MIN(first_at) AS first_at FROM first_seen GROUP BY strategy_id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("ledger_builders._new_strategy_entries", exc)
        rows = []

    seen_with_history: dict[str, str] = {row["strategy_id"]: row["first_at"] for row in rows}

    entries: list[dict[str, Any]] = []
    for sid, first_at in seen_with_history.items():
        entries.append(
            {
                "timestamp": first_at,
                "displayTimestamp": _compact_timestamp(str(first_at)),
                "strategyId": sid,
                "subject": _short_strategy_name(sid),
                "stage": "system",
                "transition": "registration",
                "outcome": "ADDED",
                "outcomeKind": "added",
                "reason": "strategy first appeared in event store",
                "recent": True,
            }
        )

    # Fallback: strategies present in configs/ with no event-store ancestry.
    for yaml_path in Path(configs_dir).glob("*.yaml"):
        sid = _strategy_id_from_yaml(yaml_path)
        if sid and sid not in seen_with_history:
            mtime_iso = datetime.fromtimestamp(yaml_path.stat().st_mtime, tz=UTC).isoformat()
            entries.append(
                {
                    "timestamp": mtime_iso,
                    "displayTimestamp": _compact_timestamp(mtime_iso),
                    "strategyId": sid,
                    "subject": _short_strategy_name(sid),
                    "stage": "system",
                    "transition": "registration",
                    "outcome": "ADDED",
                    "outcomeKind": "added",
                    "reason": "config file mtime (no event-store history)",
                    "recent": True,
                }
            )
    return entries


_LEDGER_SOURCE_PRIORITY = {
    "promoted": 0,
    "demoted": 0,
    "returned": 0,
    "fired": 1,
    "info": 1,
    "started": 2,
    "stopped": 2,
    "backtested_strong": 3,
    "backtested_paper": 3,
    "backtested_weak": 3,
    "backtested": 3,
    "added": 4,
}


def _ledger_entries(conn: sqlite3.Connection, configs_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    entries += _promotion_entries(conn)
    entries += _kill_switch_entries(conn)
    entries += _session_start_entries(conn)
    entries += _session_stop_entries(conn)
    entries += _backtest_complete_entries(conn)
    entries += _new_strategy_entries(conn, configs_dir)
    return sorted(
        entries,
        key=lambda e: (
            # Primary: newest first
            str(e.get("timestamp") or ""),
            # Secondary: lower priority number = higher position when timestamps equal
            -_LEDGER_SOURCE_PRIORITY.get(str(e.get("outcomeKind") or ""), 5),
        ),
        reverse=True,
    )


def _ledger_outcome_label(outcome_kind: str) -> str:
    if outcome_kind == "demoted":
        return "DEMOTED"
    if outcome_kind == "returned":
        return "RETURNED"
    return "PROMOTED"

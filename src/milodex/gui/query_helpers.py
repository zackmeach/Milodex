"""Read-only event-store SQL projections for GUI read models.

Leaf module in the read-model DAG (depends only on ``_event_queries``, the
strategy loader, and the shared config-skip constants).  Each function opens or
consumes a read-only SQLite connection and returns plain dicts the snapshot
builders project into QML payloads.

Extracted verbatim from ``read_models.py`` (PR12 decompose). No behavior
changed — definitions were moved, not rewritten.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from milodex.gui import _event_queries
from milodex.gui._db_logging import log_db_read_error
from milodex.gui.row_formatters import _CONFIG_SKIP_NAMES, _CONFIG_SKIP_PREFIXES
from milodex.strategies.loader import StrategyConfig, load_strategy_config

logger = logging.getLogger(__name__)


def _open_ro_conn(db_path: Path) -> sqlite3.Connection:
    """Open a read-only URI connection with Row factory set.

    Callers MUST verify ``db_path.exists()`` before calling — ``mode=ro``
    raises ``sqlite3.OperationalError`` when the file is absent.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_strategy_configs(configs_dir: Path) -> list[StrategyConfig]:
    result: list[StrategyConfig] = []
    for path in sorted(configs_dir.glob("*.yaml")):
        if path.name in _CONFIG_SKIP_NAMES or path.name.startswith(_CONFIG_SKIP_PREFIXES):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict) or "strategy" not in data:
            continue
        try:
            result.append(load_strategy_config(path))
        except ValueError as exc:
            logger.warning("Skipping invalid strategy config %s: %s", path, exc)
    return result


def _latest_promotions(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT p.*
            FROM promotions p
            INNER JOIN (
                SELECT strategy_id, MAX(id) AS max_id
                FROM promotions
                WHERE promotion_type NOT IN ('demotion', 'stage_return')
                GROUP BY strategy_id
            ) latest ON latest.strategy_id = p.strategy_id AND latest.max_id = p.id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("query_helpers._latest_promotions", exc)
        return {}
    return {row["strategy_id"]: dict(row) for row in rows}


def _latest_session_states(
    conn: sqlite3.Connection, locks_dir: Path | None = None
) -> dict[str, dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT sr.*,
                   (
                       SELECT COUNT(*)
                       FROM trades t
                       WHERE t.session_id = sr.session_id
                         AND t.source = 'paper'
                   ) AS trade_count
            FROM strategy_runs sr
            INNER JOIN (
                SELECT strategy_id, MAX(id) AS max_id
                FROM strategy_runs
                GROUP BY strategy_id
            ) latest ON latest.strategy_id = sr.strategy_id AND latest.max_id = sr.id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("query_helpers._latest_session_states", exc)
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        strategy_id = row["strategy_id"]
        exit_reason = str(row["exit_reason"] or "")
        # Back-compat guard (PR6): phantom detection only engages when an
        # explicit locks_dir is supplied. When None, an open session resolves
        # to legacy "running" (lock_live=True). Lock-verified liveness unifies
        # the running/phantom split via the shared resolver. The resolver only
        # consults lock_live for open rows, so skip the per-strategy lock I/O
        # for already-closed sessions.
        if row["ended_at"] not in (None, ""):
            lock_live = False
        elif locks_dir is None:
            lock_live = True
        else:
            lock_live = _event_queries.runner_lock_live(strategy_id, locks_dir)
        state = _event_queries.resolve_runner_liveness(
            ended_at=row["ended_at"],
            exit_reason=exit_reason,
            lock_live=lock_live,
        )
        if state == "running":
            detail = "session active"
        elif state == "phantom":
            detail = "phantom: no live runner lock"
        elif state == "failed":
            detail = exit_reason
        else:  # stopped
            detail = exit_reason or "session ended"
        result[strategy_id] = {
            "state": state,
            "session_id": str(row["session_id"]),
            "started_at": str(row["started_at"] or ""),
            "ended_at": str(row["ended_at"] or ""),
            "exit_reason": exit_reason,
            "trade_count": int(row["trade_count"] or 0),
            "detail": detail,
        }
    return result


def _latest_orchestration_jobs(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    try:
        rows = conn.execute(
            """
            SELECT oj.*
            FROM orchestration_jobs oj
            INNER JOIN (
                SELECT strategy_id, MAX(id) AS max_id
                FROM orchestration_jobs
                WHERE status IN ('queued', 'starting', 'running')
                GROUP BY strategy_id
            ) latest ON latest.strategy_id = oj.strategy_id AND latest.max_id = oj.id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("query_helpers._latest_orchestration_jobs", exc)
        return {}
    return {
        row["strategy_id"]: {
            "job_id": str(row["job_id"]),
            "status": str(row["status"]),
            "action_type": str(row["action_type"]),
            "detail": str(row["progress_label"] or row["error_message"] or ""),
            "cancel_requested_at": str(row["cancel_requested_at"] or ""),
        }
        for row in rows
    }


def _latest_pnl(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = conn.execute(
            """
            SELECT recorded_at, daily_pnl, portfolio_value
            FROM portfolio_snapshots
            ORDER BY recorded_at DESC
            LIMIT 20
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("query_helpers._latest_pnl", exc)
        return {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]}
    if not rows:
        return {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]}
    latest = rows[0]
    portfolio_value = float(latest["portfolio_value"] or 0.0)
    daily_pnl = float(latest["daily_pnl"] or 0.0)
    pct = 0.0 if portfolio_value == 0 else (daily_pnl / portfolio_value) * 100
    return {
        "today": daily_pnl,
        "todayPct": pct,
        "sparkline": [float(row["daily_pnl"] or 0.0) for row in reversed(rows)],
    }

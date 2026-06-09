"""Shared event-store query helpers for GUI read models.

This module is the single source of truth for two recurring patterns across
the GUI read-model layer:

1. ``oos_aggregate_metrics`` — parse the ``oos_aggregate`` sub-object out of a
   ``backtest_runs.metadata_json`` blob and return the canonical triple
   ``{sharpe, max_drawdown_pct, trade_count}``.

2. ``latest_backtest_metrics`` — the "MAX id per strategy, status=completed"
   self-join that identifies the most recent completed backtest run for each
   strategy.

Both were previously duplicated across:
- ``milodex.gui.read_models._latest_backtest_metrics``
- ``milodex.gui.attention_state._SQL_LATEST_BACKTEST_METRICS``
- ``milodex.gui.strategy_bank_state._SQL_BLOCKED``
- ``milodex.gui.activity_feed_state._SQL_BACKTESTS`` (oos_aggregate triple only)
- ``milodex.gui.read_models`` recent-N backtest query (oos_aggregate triple only)

Design notes
------------
- ``latest_backtest_metrics`` accepts an *open* connection; the caller owns
  the connection lifecycle (open / close / transaction context).  Callers are
  expected to set ``conn.row_factory = sqlite3.Row`` before calling; if they
  do not, the function falls back to index-based access via column aliases.
- On ``sqlite3.Error`` the function returns an empty dict (defensive — mirrors
  the behaviour of ``read_models._latest_backtest_metrics``).
- The recent-N feed queries (``activity_feed_state``, ``read_models`` recent-200)
  share only ``oos_aggregate_metrics``; they are deliberately NOT merged here
  because their ``ORDER BY ended_at DESC LIMIT N`` shape is unrelated to the
  per-strategy MAX-id semantics.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# Runner liveness — shared 4-state resolver (PR6)
#
# The resolver and lock probes moved to ``milodex.strategies.runner_status``
# so non-GUI surfaces (``milodex strategy status``) can consume runner
# liveness without importing the GUI package. Re-exported here for the
# existing GUI callers.
# ---------------------------------------------------------------------------
from milodex.strategies.runner_status import (  # noqa: F401
    _FAILURE_EXIT_REASONS,
    resolve_runner_liveness,
    runner_lock_live,
    runner_lock_mtime_age,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def oos_aggregate_metrics(metadata_json: str | None) -> dict[str, Any]:
    """Extract the ``oos_aggregate`` triple from a ``metadata_json`` blob.

    Parameters
    ----------
    metadata_json:
        The raw ``metadata_json`` text column value from ``backtest_runs``.
        May be ``None``, an empty string, or malformed JSON.

    Returns
    -------
    dict with keys ``sharpe``, ``max_drawdown_pct``, ``trade_count``.
    Any missing or unresolvable key maps to ``None``.  Never raises.
    """
    _empty: dict[str, Any] = {"sharpe": None, "max_drawdown_pct": None, "trade_count": None}
    if not metadata_json:
        return dict(_empty)
    try:
        data = json.loads(metadata_json)
    except json.JSONDecodeError:
        return dict(_empty)
    if not isinstance(data, dict):
        return dict(_empty)
    agg = data.get("oos_aggregate")
    if not isinstance(agg, dict):
        return dict(_empty)
    return {
        "sharpe": agg.get("sharpe"),
        "max_drawdown_pct": agg.get("max_drawdown_pct"),
        "trade_count": agg.get("trade_count"),
    }


_SQL_LATEST_BACKTEST = """
SELECT br.strategy_id,
       br.run_id,
       br.started_at,
       br.metadata_json
FROM backtest_runs br
INNER JOIN (
    SELECT strategy_id, MAX(id) AS max_id
    FROM backtest_runs
    WHERE status = 'completed'
    GROUP BY strategy_id
) latest ON latest.strategy_id = br.strategy_id AND latest.max_id = br.id
"""


def latest_backtest_metrics(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Return the latest completed backtest metrics keyed by strategy_id.

    Executes the MAX-id-per-strategy completed-run self-join against the open
    *conn*.  The caller owns the connection (open/close/transaction context).

    Callers are expected to set ``conn.row_factory = sqlite3.Row`` before
    calling.  A plain-tuple fallback (index-based access) exists for the
    no-factory case, but the function does not defend against missing columns.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection``.

    Returns
    -------
    ``{strategy_id: {"run_id", "started_at", "sharpe", "max_drawdown_pct",
    "trade_count"}}`` — the superset of all three consuming sites' needs.
    On ``sqlite3.Error`` returns ``{}`` without propagating the exception.
    """
    try:
        rows = conn.execute(_SQL_LATEST_BACKTEST).fetchall()
    except sqlite3.Error:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        # Support both sqlite3.Row (name access) and plain tuple (index access).
        try:
            strategy_id = row["strategy_id"]
            run_id = row["run_id"]
            started_at = row["started_at"]
            metadata_json = row["metadata_json"]
        except TypeError:
            strategy_id, run_id, started_at, metadata_json = row
        metrics = oos_aggregate_metrics(metadata_json)
        result[strategy_id] = {
            "run_id": run_id,
            "started_at": started_at,
            **metrics,
        }
    return result

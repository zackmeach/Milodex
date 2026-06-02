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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Runner liveness — shared 4-state resolver (PR6)
# ---------------------------------------------------------------------------

_FAILURE_EXIT_REASONS = frozenset({"crashed", "failed", "kill_switch", "orphan_recovered", "error"})
"""Exit reasons that classify a *closed* runner session as ``"failed"``.

Identical to the previously-local set in ``read_models._latest_session_states``;
consolidated here so the two readers share one definition. The ``crashed:<detail>``
prefix is handled separately by :func:`resolve_runner_liveness`.
"""


def resolve_runner_liveness(
    *, ended_at: str | None, exit_reason: str | None, lock_live: bool
) -> str:
    """Resolve a single runner session to ``running | phantom | stopped | failed``.

    Pure function — the one place the GUI decides what a runner row *is* for
    display-trust and which-actions purposes. Lock-verified liveness arrives as
    the pre-computed ``lock_live`` flag so this stays side-effect-free and
    trivially testable.

    - ``ended_at`` is ``None``/``""`` (open session):
        ``"running"`` if ``lock_live`` else ``"phantom"`` — a hard-killed runner
        whose row never closed is a phantom, not a live runner.
    - closed session with a failure exit reason (in :data:`_FAILURE_EXIT_REASONS`
      or with a ``"crashed:"`` prefix): ``"failed"``.
    - any other closed session: ``"stopped"``.
    """
    if ended_at in (None, ""):
        return "running" if lock_live else "phantom"
    reason = exit_reason or ""
    if reason in _FAILURE_EXIT_REASONS or reason.startswith("crashed:"):
        return "failed"
    return "stopped"


def runner_lock_live(strategy_id: str, locks_dir: Path | None) -> bool:
    """Return ``True`` iff a genuinely-live process holds ``strategy_id``'s runner lock.

    Identity-verified liveness via :func:`milodex.core.advisory_lock.live_lock_holder`
    — a stale / recycled-PID lock file reads as *not* live. Returns ``False`` when
    ``locks_dir`` is ``None`` (no lock surface to inspect) or on any read error.
    """
    if locks_dir is None:
        return False
    from milodex.core.advisory_lock import AdvisoryLock, live_lock_holder
    from milodex.strategies.paper_runner_control import runner_lock_name

    try:
        lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
        return live_lock_holder(lock) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("runner_lock_live: lock read failed for %s: %s", strategy_id, exc)
        return False


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

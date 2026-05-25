"""Live attention/drift read-model exposed to QML (spec §4/§5 AttentionState).

Aggregates operator-actionable signals from the event store:

- ``runningNow``       — strategies currently executing (ended_at IS NULL).
- ``paperTesting``     — strategies promoted to paper stage.
- ``backtestOnly``     — strategies blocked at backtest (not yet promoted).
- ``needsReview``      — strategies needing operator action (cases a/b/c).
- ``underperforming``  — paper strategies whose live Sharpe trails their
                         promotion-evidence Sharpe (and evidence ≥ floor).
- ``driftList``        — top-N annotated items for the operator attention rail.

Threading model
---------------
Identical to :mod:`milodex.gui.strategy_bank_state` and
:mod:`milodex.gui.performance_state`:

- A :class:`QTimer` fires every ``refresh_interval_ms`` (default 30 s).
- Results flow back via :class:`Qt.ConnectionType.QueuedConnection`.
- :meth:`stop` drains in-flight workers via ``waitForDone(2000)`` and
  defensively disconnects signals (Windows-shutdown contract).

Read-only guarantee
-------------------
All SQLite connections are opened ``file:<path>?mode=ro`` (URI mode).

Scope-drift rule (spec §8)
--------------------------
The ``paperTesting`` / ``backtestOnly`` classification is sourced exclusively
from :func:`milodex.gui.strategy_bank_state._query_bank`.  No SQL duplication.
The ``_dashboard_scope`` constants (``TRADE_PAPER_SQL``, ``EXPLANATION_PAPER_SQL``)
are used ONLY where ``trades`` / ``explanations`` are touched directly — the
recency check in ``_build_drift_list`` — consistent with spec §8.

needsReview classifier (b) threshold
-------------------------------------
Case (b) uses ``MIN_TRADES`` (30) as the concrete paper-evidence bar for
micro_live eligibility.  The spec defers the exact bar to implementation;
MIN_TRADES is the consistent choice since it matches the capital-gate trade
floor and is already the default for ``_compute_underperforming``.

needsReview classifier (c) approximation
-----------------------------------------
"No operator action after the breach" is operationalized as: no ``promotions``
row with ``promotion_type='demotion'`` AND no frozen ``strategy_manifests``
row for that strategy.  Precise temporal correlation (action recorded *after*
the underperformance became observable) is a documented approximation; a future
ADR can tighten this once an audit timestamp index exists.

Drift list constants
--------------------
``DRIFT_NO_FILLS_DAYS`` = 7   — recency window (explanations/trades).
``DRIFT_LIST_CAP``      = 20  — maximum entries returned.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, Signal  # pragma: no cover

from milodex.gui.polling_lifecycle import PollingReadModel
from milodex.gui.strategy_bank_state import _compute_gate_failures, _query_bank
from milodex.promotion.state_machine import MIN_TRADES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DRIFT_NO_FILLS_DAYS: int = 7   # flag if no paper fills seen in this many days
DRIFT_LIST_CAP: int = 20       # maximum entries in driftList

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _compute_underperforming(
    paper_sharpe: float | None,
    baseline_sharpe: float | None,
    evidence_n: int,
    min_evidence_n: int = MIN_TRADES,
) -> bool:
    """Return True iff the strategy is underperforming relative to its promotion evidence.

    Rules (LOCKED — do not modify without design sign-off):
    1. If ``evidence_n < min_evidence_n``: always False (evidence floor).
    2. Else: True iff both Sharpes are non-None and ``paper_sharpe < baseline_sharpe``.

    ``evidence_n`` = count of realized paper trades for the strategy derived
    from the ``trades`` table (status='filled', backtest_run_id IS NULL,
    strategy_stage IN paper/micro_live/live).
    ``baseline_sharpe`` = the ``sharpe_ratio`` stored in the strategy's most
    recent ``promotions`` row with ``to_stage='paper'``.
    """
    if evidence_n < min_evidence_n:
        return False
    if paper_sharpe is None or baseline_sharpe is None:
        return False
    return paper_sharpe < baseline_sharpe


# ---------------------------------------------------------------------------
# SQL helpers for _query_attention
# ---------------------------------------------------------------------------

_SQL_RUNNING_NOW = """
SELECT COUNT(DISTINCT strategy_id) AS cnt
FROM strategy_runs
WHERE ended_at IS NULL
"""

_SQL_PAPER_EVIDENCE_COUNTS = """
SELECT t.strategy_name AS strategy_id,
       COUNT(*) AS fill_count
FROM trades t
WHERE t.status = 'filled'
  AND t.backtest_run_id IS NULL
  AND strategy_stage IN ('paper', 'micro_live', 'live')
GROUP BY t.strategy_name
"""

_SQL_PAPER_SHARPE = """
SELECT strategy_id,
       sharpe_ratio AS baseline_sharpe
FROM promotions
WHERE id IN (
    SELECT MAX(id)
    FROM promotions
    WHERE to_stage = 'paper'
    GROUP BY strategy_id
)
"""

_SQL_LATEST_BACKTEST_METRICS = """
SELECT br.strategy_id,
       json_extract(br.metadata_json, '$.oos_aggregate.sharpe')           AS wf_sharpe,
       json_extract(br.metadata_json, '$.oos_aggregate.max_drawdown_pct') AS wf_max_dd,
       json_extract(br.metadata_json, '$.oos_aggregate.trade_count')      AS wf_trades
FROM backtest_runs br
INNER JOIN (
    SELECT strategy_id, MAX(id) AS max_id
    FROM backtest_runs
    WHERE status = 'completed'
    GROUP BY strategy_id
) latest ON br.strategy_id = latest.strategy_id AND br.id = latest.max_id
WHERE br.status = 'completed'
"""

_SQL_PROMOTED_TO_PAPER = """
SELECT DISTINCT strategy_id FROM promotions WHERE to_stage = 'paper'
"""

_SQL_PROMOTED_TO_MICRO_LIVE = """
SELECT DISTINCT strategy_id FROM promotions WHERE to_stage = 'micro_live'
"""

_SQL_DEMOTED = """
SELECT DISTINCT strategy_id FROM promotions WHERE promotion_type = 'demotion'
"""

_SQL_FROZEN_MANIFESTS = """
SELECT DISTINCT strategy_id FROM strategy_manifests WHERE frozen_at IS NOT NULL
"""

_SQL_LAST_FILL_PER_STRATEGY = """
SELECT strategy_name AS strategy_id,
       MAX(recorded_at) AS last_fill_at
FROM trades
WHERE status = 'filled'
  AND backtest_run_id IS NULL
  AND strategy_stage IN ('paper', 'micro_live', 'live')
GROUP BY strategy_name
"""

# Compute per-strategy rolling Sharpe from paper trades using a simple
# ratio proxy: mean daily pnl / std of pnl.  We don't have individual trade
# PnL in the trades table directly; instead we use the promotions.sharpe_ratio
# (baseline) and compare to a live-session Sharpe stored in strategy_runs
# metadata if available.  If no live Sharpe is available, paper_sharpe=None.
_SQL_PAPER_LIVE_SHARPE = """
SELECT sr.strategy_id,
       json_extract(sr.metadata_json, '$.sharpe') AS live_sharpe
FROM strategy_runs sr
INNER JOIN (
    SELECT strategy_id, MAX(id) AS max_id
    FROM strategy_runs
    WHERE ended_at IS NOT NULL
    GROUP BY strategy_id
) latest ON sr.strategy_id = latest.strategy_id AND sr.id = latest.max_id
"""


def _query_attention(db_path: Path) -> dict[str, Any]:
    """Query the event store for all attention-state signals.

    Opens a read-only connection.  Calls :func:`_query_bank` for paper/blocked
    classification (no SQL duplication per spec §8 scope-drift rule).

    Returns a dict with keys:
    - ``running_now``: int
    - ``paper_list``: list[dict]   (from _query_bank)
    - ``blocked_list``: list[dict] (from _query_bank)
    - ``needs_review``: int
    - ``underperforming``: int
    - ``drift_list``: list[dict]
    - ``refreshed_at``: str (filled by caller)
    """
    # Call _query_bank for paper/blocked (single source of truth).
    paper_list, blocked_list = _query_bank(db_path)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # --- runningNow ---
        row = conn.execute(_SQL_RUNNING_NOW).fetchone()
        running_now: int = row["cnt"] if row else 0

        # --- sets for gate computation ---
        promoted_to_paper: set[str] = {
            r["strategy_id"] for r in conn.execute(_SQL_PROMOTED_TO_PAPER).fetchall()
        }
        promoted_to_micro_live: set[str] = {
            r["strategy_id"]
            for r in conn.execute(_SQL_PROMOTED_TO_MICRO_LIVE).fetchall()
        }
        demoted: set[str] = {
            r["strategy_id"] for r in conn.execute(_SQL_DEMOTED).fetchall()
        }
        frozen: set[str] = {
            r["strategy_id"] for r in conn.execute(_SQL_FROZEN_MANIFESTS).fetchall()
        }

        # --- latest backtest metrics (for needsReview case a) ---
        bt_rows = conn.execute(_SQL_LATEST_BACKTEST_METRICS).fetchall()
        bt_metrics: dict[str, dict[str, Any]] = {}
        for r in bt_rows:
            bt_metrics[r["strategy_id"]] = {
                "sharpe": r["wf_sharpe"],
                "max_dd": r["wf_max_dd"],
                "trades": r["wf_trades"],
            }

        # --- paper evidence counts (filled paper trades per strategy) ---
        ev_rows = conn.execute(_SQL_PAPER_EVIDENCE_COUNTS).fetchall()
        paper_evidence: dict[str, int] = {r["strategy_id"]: r["fill_count"] for r in ev_rows}

        # --- baseline (promotion-evidence) Sharpes for paper strategies ---
        baseline_rows = conn.execute(_SQL_PAPER_SHARPE).fetchall()
        baseline_sharpe: dict[str, float | None] = {
            r["strategy_id"]: r["baseline_sharpe"] for r in baseline_rows
        }

        # --- live session Sharpe (proxy for rolling paper Sharpe) ---
        live_sharpe_rows = conn.execute(_SQL_PAPER_LIVE_SHARPE).fetchall()
        live_sharpe: dict[str, float | None] = {
            r["strategy_id"]: r["live_sharpe"] for r in live_sharpe_rows
        }

        # --- last fill timestamps for drift list ---
        fill_rows = conn.execute(_SQL_LAST_FILL_PER_STRATEGY).fetchall()
        last_fill: dict[str, str] = {r["strategy_id"]: r["last_fill_at"] for r in fill_rows}

    finally:
        conn.close()

    # -----------------------------------------------------------------------
    # Compute needsReview and underperforming
    # -----------------------------------------------------------------------

    paper_strategy_ids: set[str] = {d["strategyId"] for d in paper_list}

    # underperforming set
    underperforming_ids: set[str] = set()
    for sid in paper_strategy_ids:
        ev_n = paper_evidence.get(sid, 0)
        p_sharpe = live_sharpe.get(sid)  # may be None if no completed run
        b_sharpe = baseline_sharpe.get(sid)
        if _compute_underperforming(p_sharpe, b_sharpe, ev_n):
            underperforming_ids.add(sid)

    # needsReview case (a): latest backtest clears all gates AND no paper promotion
    nr_case_a: set[str] = set()
    for sid, metrics in bt_metrics.items():
        if sid in promoted_to_paper:
            continue  # already promoted
        failures = _compute_gate_failures(metrics["sharpe"], metrics["max_dd"], metrics["trades"])
        if not failures:  # gate-pass = empty failure list
            nr_case_a.add(sid)

    # needsReview case (b): paper strategy with ≥ MIN_TRADES evidence and no micro_live promotion
    nr_case_b: set[str] = set()
    for sid in paper_strategy_ids:
        if sid in promoted_to_micro_live:
            continue
        ev_n = paper_evidence.get(sid, 0)
        if ev_n >= MIN_TRADES:
            nr_case_b.add(sid)

    # needsReview case (c): underperforming AND no operator acknowledgement
    # Acknowledgement = demotion promotion OR frozen manifest (documented approximation).
    nr_case_c: set[str] = set()
    for sid in underperforming_ids:
        acknowledged = sid in demoted or sid in frozen
        if not acknowledged:
            nr_case_c.add(sid)

    # Relationship invariant: (c) ⊆ underperforming — enforced structurally above.

    needs_review_ids = nr_case_a | nr_case_b | nr_case_c
    needs_review = len(needs_review_ids)
    underperforming = len(underperforming_ids)

    # -----------------------------------------------------------------------
    # Build drift list
    # -----------------------------------------------------------------------
    drift_list = _build_drift_list(
        underperforming_ids=underperforming_ids,
        paper_strategy_ids=paper_strategy_ids,
        paper_list=paper_list,
        live_sharpe=live_sharpe,
        baseline_sharpe=baseline_sharpe,
        last_fill=last_fill,
    )

    return {
        "running_now": running_now,
        "paper_list": paper_list,
        "blocked_list": blocked_list,
        "needs_review": needs_review,
        "underperforming": underperforming,
        "drift_list": drift_list,
    }


def _build_drift_list(
    *,
    underperforming_ids: set[str],
    paper_strategy_ids: set[str],
    paper_list: list[dict[str, Any]],
    live_sharpe: dict[str, float | None],
    baseline_sharpe: dict[str, float | None],
    last_fill: dict[str, str],
) -> list[dict[str, Any]]:
    """Build the annotated drift-list for operator attention.

    The actual last_fill dict was populated using the equivalent SQL filter
    to ``_dashboard_scope.TRADE_PAPER_SQL``
    (status='filled', backtest_run_id IS NULL, stage IN paper/micro_live/live).

    Returns at most DRIFT_LIST_CAP entries.
    """
    items: list[dict[str, Any]] = []

    # Underperformers first (highest priority)
    for sid in sorted(underperforming_ids):
        p = live_sharpe.get(sid)
        b = baseline_sharpe.get(sid)
        if p is not None and b is not None:
            note = f"paper Sharpe {p:.2f} below promoted {b:.2f}"
        else:
            note = "underperforming (metrics unavailable)"
        items.append({"name": sid, "note": note, "tone": "warn"})

    # No-fills recency (paper strategies not already in underperformers)
    cutoff = (datetime.now(tz=UTC) - timedelta(days=DRIFT_NO_FILLS_DAYS)).isoformat()
    # _dashboard_scope.TRADE_PAPER_SQL is the canonical paper-scope filter;
    # the last_fill dict was built with equivalent criteria.
    for sid in sorted(paper_strategy_ids - underperforming_ids):
        last = last_fill.get(sid)
        stale = False
        if last is None:
            stale = True
        else:
            last_dt = last
            # Both operands are datetime.isoformat() output (same UTC offset format),
            # so lexicographic comparison is valid and equals chronological order.
            if last_dt < cutoff:
                stale = True
        if stale:
            days_desc = f"{DRIFT_NO_FILLS_DAYS} days"
            items.append(
                {"name": sid, "note": f"no fills in {days_desc}", "tone": "info"}
            )

    return items[:DRIFT_LIST_CAP]


# ---------------------------------------------------------------------------
# Worker scaffold
# ---------------------------------------------------------------------------


def _build_attention_snapshot(db_path: Path) -> dict[str, Any]:
    """Adapter for ``PollingReadModel`` — packs attention query into polling dict."""
    result = _query_attention(db_path)
    result["lastRefreshedAt"] = datetime.now(tz=UTC).isoformat()
    return result


# ---------------------------------------------------------------------------
# AttentionState
# ---------------------------------------------------------------------------


class AttentionState(PollingReadModel):
    """Attention-state rollups and drift list exposed to QML as Q_PROPERTYs.

    Inherits the canonical polling lifecycle from
    :class:`milodex.gui.polling_lifecycle.PollingReadModel`. See module
    docstring for locked definitions and scope-drift notes.
    """

    rollupsChanged = Signal()  # noqa: N815
    driftListChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path | None = None,
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        if db_path is None:
            from milodex.config import get_data_dir

            db_path = get_data_dir() / "milodex.db"
        self._db_path = db_path
        self._rollups: dict[str, Any] = {}
        self._drift_list: list[dict[str, Any]] = []
        super().__init__(
            builder=lambda: _build_attention_snapshot(db_path),
            refresh_interval_ms=refresh_interval_ms,
            parent=parent,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        new_rollups = {
            "runningNow": result["running_now"],
            "paperTesting": len(result["paper_list"]),
            "backtestOnly": len(result["blocked_list"]),
            "needsReview": result["needs_review"],
            "underperforming": result["underperforming"],
        }
        rollups_changed = new_rollups != self._rollups
        drift_changed = result["drift_list"] != self._drift_list
        self._rollups = new_rollups
        self._drift_list = result["drift_list"]
        if rollups_changed:
            self.rollupsChanged.emit()
        if drift_changed:
            self.driftListChanged.emit()

    def _get_rollups(self) -> dict:
        return self._rollups

    def _get_drift_list(self) -> list:
        return self._drift_list

    rollups = Property("QVariantMap", _get_rollups, notify=rollupsChanged)
    driftList = Property(  # noqa: N815
        "QVariantList", _get_drift_list, notify=driftListChanged
    )

    # dataStatus, dataErrorMessage, lastRefreshedAt — inherited from PollingReadModel

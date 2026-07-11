"""Live attention/drift read-model exposed to QML (spec §4/§5 AttentionState).

Aggregates operator-actionable signals from the event store:

- ``runningNow``       — strategies currently executing (ended_at IS NULL AND
                         PID-verified live when a locks_dir is supplied; see
                         "runningNow liveness" below).
- ``paperTesting``     — strategies promoted to paper stage.
- ``backtestOnly``     — strategies blocked at backtest (not yet promoted).
- ``needsReview``      — strategies needing operator action (cases a/b/c).
- ``underperforming``  — paper strategies whose live Sharpe trails their
                         promotion-evidence Sharpe (and evidence ≥ floor).
- ``driftList``        — top-N annotated items for the operator attention rail.
- ``operatorAlerts``   — recent rows from the ``operator_alerts`` anomaly
                         channel (migration 017), severity/cap-ruled per the
                         CLI ``strategy status`` contract (above-info never
                         hidden behind the info cap).

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

from milodex.gui import _event_queries
from milodex.gui._db_logging import log_db_read_error
from milodex.gui.polling_lifecycle import PollingReadModel
from milodex.gui.row_formatters import _short_strategy_name
from milodex.gui.strategy_bank_state import _compute_gate_failures, _query_bank
from milodex.promotion.state_machine import MIN_TRADES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DRIFT_NO_FILLS_DAYS: int = 7  # flag if no paper fills seen in this many days
DRIFT_LIST_CAP: int = 20  # maximum entries in driftList

# operator_alerts rail (migration 017 anomaly channel).
# Cap rule mirrors CLI `strategy status` fix ff534ba: the lowest `info` tier is
# capped to the most-recent entries; every ABOVE-info alert (warning/critical/…)
# surfaces in full and is NEVER hidden behind the cap.
OPERATOR_ALERT_INFO_TIER: str = "info"
OPERATOR_ALERT_INFO_CAP: int = 10

# Display-ordering rank for the alert rail (higher = surfaced first). The cap
# rule itself keys off the exact `severity != "info"` test above, NOT this map —
# any unknown non-info severity still ranks above info (default 1) so it is
# never treated as capped.
_SEVERITY_RANK: dict[str, int] = {
    "critical": 3,
    "blocker": 3,
    "error": 3,
    "warning": 2,
    "info": 0,
}

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


def _severity_rank(severity: str) -> int:
    """Display-ordering rank for the operator-alert rail (higher = first).

    Unknown non-info severities rank above ``info`` (default 1) so a novel
    above-info alert type is never sorted below routine info noise.
    """
    return _SEVERITY_RANK.get(str(severity).lower(), 1)


def _operator_alert_tone(severity: str) -> str:
    """Map an alert severity to a presentation tone token consumed by QML.

    ``critical`` (rendered negative), ``warn`` (rendered warning), or ``info``
    (rendered muted). Python owns the token; QML owns the token→color mapping —
    consistent with the drift-list ``tone`` field.
    """
    sev = str(severity).lower()
    if sev == OPERATOR_ALERT_INFO_TIER:
        return "info"
    if sev in ("critical", "blocker", "error"):
        return "critical"
    return "warn"


def _relative_age(recorded_at: str | None, now: datetime) -> str:
    """Compact relative age ("5m ago" / "3h ago" / "2d ago") for an alert row.

    Returns ``""`` for a missing/unparseable timestamp and "just now" for
    anything under a minute (or a clock-skewed future timestamp). Naive
    timestamps are treated as UTC (event-store rows are UTC ISO strings).
    """
    if not recorded_at:
        return ""
    try:
        ts = datetime.fromisoformat(str(recorded_at))
    except (ValueError, TypeError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    secs = int((now - ts).total_seconds())
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _build_operator_alerts(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> tuple[list[dict[str, Any]], str]:
    """Build the operator-alert rail from the ``operator_alerts`` channel.

    Reads the append-only anomaly ledger (migration 017: ``exit_intent_dropped``,
    ``queued_intent_persist_failed``, and future M4 alert types) that until now
    was surfaced only by CLI ``strategy status``.

    Fail-soft: a read error is swallowed via :func:`log_db_read_error` and an
    empty rail is returned, so a locked/corrupt/schema-drifted DB renders as
    "no alerts" rather than crashing the polling read model or blanking the
    sibling rollups — the established GUI read-helper contract.

    Cap rule (mirrors CLI fix ff534ba): only the lowest ``info`` tier is capped
    to the ``OPERATOR_ALERT_INFO_CAP`` most-recent entries; every above-info
    alert surfaces in full and is NEVER hidden behind the cap. When info entries
    are dropped, the returned note names the omission.

    Returns ``(items, note)`` where ``items`` is ordered above-info-first then
    most-recent-first, and ``note`` is a non-empty omission string only when
    info alerts were capped out.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    try:
        rows = conn.execute(_SQL_OPERATOR_ALERTS).fetchall()
    except sqlite3.Error as exc:
        log_db_read_error("attention_state._build_operator_alerts", exc)
        return [], ""

    # rows are ordered by id ASC (chronological); the exact `severity != info`
    # test is the locked cap boundary (not the display rank map).
    above_info = [r for r in rows if r["severity"] != OPERATOR_ALERT_INFO_TIER]
    info_only = [r for r in rows if r["severity"] == OPERATOR_ALERT_INFO_TIER]
    capped_info = info_only[-OPERATOR_ALERT_INFO_CAP:]
    omitted_info = len(info_only) - len(capped_info)

    selected = above_info + capped_info
    # Above-info first, then most-recent first within a tier.
    selected.sort(
        key=lambda r: (_severity_rank(r["severity"]), str(r["recorded_at"] or "")),
        reverse=True,
    )

    items = [
        {
            "alertType": str(r["alert_type"]),
            "severity": str(r["severity"]),
            "summary": str(r["summary"]),
            "strategy": _short_strategy_name(r["strategy_id"]) if r["strategy_id"] else "",
            "symbol": r["symbol"] or "",
            "age": _relative_age(r["recorded_at"], now),
            "recordedAt": str(r["recorded_at"] or ""),
            "tone": _operator_alert_tone(str(r["severity"])),
        }
        for r in selected
    ]

    note = ""
    if omitted_info:
        plural = "s" if omitted_info != 1 else ""
        note = f"{omitted_info} older info alert{plural} hidden"
    return items, note


# ---------------------------------------------------------------------------
# SQL helpers for _query_attention
# ---------------------------------------------------------------------------

_SQL_RUNNING_STRATEGY_IDS = """
SELECT DISTINCT strategy_id
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

_SQL_OPERATOR_ALERTS = """
SELECT id, recorded_at, alert_type, severity, summary, strategy_id, symbol
FROM operator_alerts
ORDER BY id ASC
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


def _query_attention(db_path: Path, locks_dir: Path | None = None) -> dict[str, Any]:
    """Query the event store for all attention-state signals.

    Opens a read-only connection.  Calls :func:`_query_bank` for paper/blocked
    classification (no SQL duplication per spec §8 scope-drift rule).

    ``running_now`` (GUI audit finding #3 / M2 item b): with ``locks_dir=None``
    this is the legacy raw count of distinct strategies with an open
    (``ended_at IS NULL``) session — the back-compat guard existing callers
    rely on.  With an explicit ``locks_dir``, each open session is
    PID-verified via :func:`milodex.gui._event_queries.runner_lock_live`
    (mirrors the resolver ``active_ops_state.py`` already applies per row)
    and only genuinely-live runners are counted — a hard-killed runner whose
    ``strategy_runs`` row never closed no longer inflates this rollup.

    Returns a dict with keys:
    - ``running_now``: int
    - ``paper_list``: list[dict]   (from _query_bank)
    - ``blocked_list``: list[dict] (from _query_bank)
    - ``needs_review``: int
    - ``underperforming``: int
    - ``drift_list``: list[dict]
    - ``operator_alerts``: list[dict]  (from _build_operator_alerts)
    - ``operator_alerts_note``: str    (info-omission indicator, "" if none)
    - ``lastRefreshedAt``: str (filled by ``_build_attention_snapshot`` caller
      per the ``PollingReadModel`` contract)
    """
    # Call _query_bank for paper/blocked (single source of truth).
    paper_list, blocked_list = _query_bank(db_path)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # --- runningNow: candidate open sessions (PID-verified below) ---
        running_ids: list[str] = [
            r["strategy_id"] for r in conn.execute(_SQL_RUNNING_STRATEGY_IDS).fetchall()
        ]

        # --- sets for gate computation ---
        promoted_to_paper: set[str] = {
            r["strategy_id"] for r in conn.execute(_SQL_PROMOTED_TO_PAPER).fetchall()
        }
        promoted_to_micro_live: set[str] = {
            r["strategy_id"] for r in conn.execute(_SQL_PROMOTED_TO_MICRO_LIVE).fetchall()
        }
        demoted: set[str] = {r["strategy_id"] for r in conn.execute(_SQL_DEMOTED).fetchall()}
        frozen: set[str] = {
            r["strategy_id"] for r in conn.execute(_SQL_FROZEN_MANIFESTS).fetchall()
        }

        # --- latest backtest metrics (for needsReview case a) ---
        bt_metrics: dict[str, dict[str, Any]] = _event_queries.latest_backtest_metrics(conn)

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

        # --- operator-alert rail (migration 017 anomaly channel) ---
        # Fail-soft inside _build_operator_alerts: a failing read here yields an
        # empty rail without blanking the rollups computed above.
        operator_alerts, operator_alerts_note = _build_operator_alerts(conn)

    finally:
        conn.close()

    # --- runningNow: PID-verify when a locks_dir is given (M2 item b) ---
    # Back-compat guard: locks_dir=None means no lock surface to inspect, so
    # phantom detection stays OFF and the legacy raw open-session count is
    # kept — same guard shape as resolve_runner_liveness's own locks_dir=None
    # short-circuit in active_ops_state.py.
    if locks_dir is None:
        running_now: int = len(running_ids)
    else:
        running_now = sum(
            1 for sid in running_ids if _event_queries.runner_lock_live(sid, locks_dir)
        )

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
        # family arg intentionally omitted — the `if sid in promoted_to_paper: continue` guard above
        # already filters out the lifecycle-exempt regime strategy before this call is reached.
        failures = _compute_gate_failures(
            metrics["sharpe"], metrics["max_drawdown_pct"], metrics["trade_count"]
        )
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
        "operator_alerts": operator_alerts,
        "operator_alerts_note": operator_alerts_note,
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
            items.append({"name": sid, "note": f"no fills in {days_desc}", "tone": "info"})

    return items[:DRIFT_LIST_CAP]


# ---------------------------------------------------------------------------
# Worker scaffold
# ---------------------------------------------------------------------------


def _build_attention_snapshot(db_path: Path, locks_dir: Path | None = None) -> dict[str, Any]:
    """Adapter for ``PollingReadModel`` — packs attention query into polling dict."""
    result = _query_attention(db_path, locks_dir=locks_dir)
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
    operatorAlertsChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path | None = None,
        locks_dir: Path | None = None,
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        if db_path is None:
            from milodex.config import get_data_dir

            db_path = get_data_dir() / "milodex.db"
        self._db_path = db_path
        # Unlike ActiveOpsState, locks_dir is NOT eagerly resolved to the real
        # production locks dir when None — a bare `AttentionState(db_path=...)`
        # (as many existing tests construct it) must keep the legacy raw
        # running_now count (see _query_attention's locks_dir=None guard).
        # Production wiring (gui/app.py) passes the real locks_dir explicitly.
        self._locks_dir = locks_dir
        self._rollups: dict[str, Any] = {}
        self._drift_list: list[dict[str, Any]] = []
        self._operator_alerts: list[dict[str, Any]] = []
        self._operator_alerts_note: str = ""
        super().__init__(
            builder=lambda: _build_attention_snapshot(db_path, locks_dir),
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
        new_alerts = result.get("operator_alerts", [])
        new_alerts_note = result.get("operator_alerts_note", "")
        rollups_changed = new_rollups != self._rollups
        drift_changed = result["drift_list"] != self._drift_list
        alerts_changed = (
            new_alerts != self._operator_alerts
            or new_alerts_note != self._operator_alerts_note
        )
        self._rollups = new_rollups
        self._drift_list = result["drift_list"]
        self._operator_alerts = new_alerts
        self._operator_alerts_note = new_alerts_note
        if rollups_changed:
            self.rollupsChanged.emit()
        if drift_changed:
            self.driftListChanged.emit()
        if alerts_changed:
            self.operatorAlertsChanged.emit()

    def _get_rollups(self) -> dict:
        return self._rollups

    def _get_drift_list(self) -> list:
        return self._drift_list

    def _get_operator_alerts(self) -> list:
        return self._operator_alerts

    def _get_operator_alerts_note(self) -> str:
        return self._operator_alerts_note

    rollups = Property("QVariantMap", _get_rollups, notify=rollupsChanged)
    driftList = Property(  # noqa: N815
        "QVariantList", _get_drift_list, notify=driftListChanged
    )
    operatorAlerts = Property(  # noqa: N815
        "QVariantList", _get_operator_alerts, notify=operatorAlertsChanged
    )
    operatorAlertsNote = Property(  # noqa: N815
        str, _get_operator_alerts_note, notify=operatorAlertsChanged
    )

    # dataStatus, dataErrorMessage, lastRefreshedAt — inherited from PollingReadModel

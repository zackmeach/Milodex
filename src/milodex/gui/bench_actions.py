"""Bench action / intent / evidence engine for GUI read models.

Produces the ``actions`` menu, the per-action Action Intent Preview, and the
Evidence Packet that ``_StrategyRow.as_qml()`` attaches to each bench card.
All functions here are read-only display-layer projections — none submit, none
mutate state (ADR 0049 Decision 2 / ADR 0051 framing).

The only reference to ``_StrategyRow`` in this module is in type hints; the
runtime edge is one-way (``strategy_row`` imports this module lazily, never the
reverse) so the module graph stays an acyclic DAG.

Extracted verbatim from ``read_models.py`` (PR12 decompose). No behavior
changed — definitions were moved, not rewritten.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from milodex.gui.bench_v1 import (
    BenchStrategyState,
    EvidenceRecord,
    Freshness,
    GateResult,
    Stage,
    compute_menu_items,
)
from milodex.gui.row_formatters import _float_or_none, _int_or_none
from milodex.gui.strategy_bank_state import _compute_gate_failures

if TYPE_CHECKING:
    from milodex.gui.strategy_row import _StrategyRow


def _compute_bench_action_menu(row: _StrategyRow) -> list[dict[str, Any]]:
    """Compute the Action menu item list for a bench row via compute_menu_items.

    Constructs a BenchStrategyState from the row fields and delegates to the
    pure-function composer in bench_v1.  The legacy _bench_actions() path is
    removed: this is the only code path that produces the ``actions`` key
    exposed to QML.

    Evidence is derived upstream from durable state: completed backtest metrics
    drive BACKTEST Fresh+Pass/Fresh+Fail, missing backtests render as
    Missing+Pending, and non-terminal orchestration jobs populate
    ``runs_in_flight``. Operational paper controls still use session state.

    The returned list is a list of plain dicts so it serialises cleanly to
    QML's QVariantList.  Each dict carries:

      - ``label``: the operator-facing verb string (from bench_v1 locked labels)
      - ``verbClass``: ``"directional"``, ``"invocation"``, or ``"informational"``
      - ``targetStage``: the target stage string for directional verbs,
        or ``""`` for invocation / informational verbs
    """
    # Prefer the row's evidence_by_stage if it was populated upstream.
    evidence = row.evidence_by_stage

    if not evidence and row.stage in {"idle", "backtest"}:
        evidence = {Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING)}

    try:
        current_stage = Stage(row.stage)
    except ValueError:
        current_stage = Stage.IDLE

    state = BenchStrategyState(
        current_stage=current_stage,
        evidence_by_stage=evidence,
        runs_in_flight=row.runs_in_flight,
        is_session_running=row.session_state == "running",
    )

    items = compute_menu_items(state)
    return [
        {
            "label": item.label,
            "verbClass": item.verb_class,
            "targetStage": item.target_stage or "",
            "actionIntentPreview": _action_intent_preview(row, item),
        }
        for item in items
    ]


def _bench_evidence_by_stage(
    metrics: dict[str, Any],
    family: str,
) -> dict[Stage, EvidenceRecord]:
    sharpe = _float_or_none(metrics.get("sharpe"))
    max_dd = _float_or_none(metrics.get("max_drawdown_pct"))
    trade_count = _int_or_none(metrics.get("trade_count"))
    has_completed_backtest = bool(metrics.get("run_id")) or any(
        value is not None for value in (sharpe, max_dd, trade_count)
    )
    if not has_completed_backtest:
        return {Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING)}

    failures = _compute_gate_failures(sharpe, max_dd, trade_count, family)
    return {
        Stage.BACKTEST: EvidenceRecord(
            Freshness.FRESH,
            GateResult.FAIL if failures else GateResult.PASS,
        )
    }


def _bench_runs_in_flight(job: dict[str, Any]) -> dict[Stage, bool]:
    if not job:
        return {}
    if str(job.get("status") or "") not in {"queued", "starting", "running"}:
        return {}
    if str(job.get("action_type") or "") in {
        "backtest",
        "backtest_single",
        "backtest_walk_forward",
    }:
        return {Stage.BACKTEST: True}
    return {}


# ---------------------------------------------------------------------------
# PR N: Action Intent Preview contract
#
# Stable, read-only preview metadata attached to every Bench action item. The
# QML confirmation modal renders from this object instead of recomputing the
# same classifications inline. The preview is descriptive only — `executable`
# and `wired` are both False in v1; ADR 0049 Decision 2 holds.
# ---------------------------------------------------------------------------

# Plain-language intent copy, keyed by action_kind. The exact wording matches
# the PR L QML _intentCopy() helper so the confirmation modal's prose remains
# identical whether read from the preview or the QML fallback.
_ACTION_INTENT_COPY: dict[str, str] = {
    "promote": (
        "Move this strategy forward from its current stage to the next stage "
        "after evidence and policy gates are satisfied."
    ),
    "demote": (
        "Move this strategy backward to an earlier stage and remove it from "
        "its current operating stage."
    ),
    "return": (
        "Restore this strategy to a previously eligible stage or return it to the idle shelf."
    ),
    "start_trading": (
        "Start a paper trading session for this strategy through the controlled runner boundary."
    ),
    "stop_trading": (
        "Request a controlled stop for the current paper session. This is not the kill switch."
    ),
    "initiate_backtest": ("Run canonical walk-forward backtest evidence for this strategy."),
    "refresh_backtest": (
        "Refresh aging or stale evidence with the canonical walk-forward backtest."
    ),
    "open_evidence": (
        "Open the read-only Evidence snapshot for this strategy. "
        "Informational only — no state changes."
    ),
    "unknown": "Action not recognised by the intent preview.",
}

# Static enumeration of what a future Milodex would validate before this
# action could proceed. Copy-only — no real check is performed by including
# the action item in this list.
_ACTION_REQUIREMENTS: tuple[str, ...] = (
    "Evidence gate check",
    "Freshness check",
    "Operator confirmation",
    "Policy lock check",
    "Risk guard check",
    "Event write after confirmation",
)

# Display string identifying the kind of record a future event-store would
# write. NOT a class name, NOT a function, NOT a payload — purely a label
# for operator orientation.
_ACTION_FUTURE_RECORD: dict[str, str] = {
    "promote": "promotion_event",
    "demote": "demotion_event",
    "return": "stage_return_event",
    "start_trading": "session_start_event",
    "stop_trading": "session_stop_event",
    "initiate_backtest": "backtest_run",
    "refresh_backtest": "backtest_run",
    "open_evidence": "evidence_view",
    "unknown": "—",
}


# Verbatim source-note string for every actionIntentPreview. Single-line
# literal so static grep-based safety tests can match substring-exactly.
_ACTION_PREVIEW_SOURCE_NOTE: str = (
    "Bench action intent previews are display metadata. Submit-capable actions "
    "must still validate through the Bench command bridge before state changes."
)

# Verbatim safety copy strings. These match the PR L QML _COPY_* constants
# so the confirmation modal renders the same prose whether sourced from the
# preview or the QML fallback. The strings MUST remain single-line literals
# so static grep-based safety tests continue to match substring-exactly.
_COPY_SAFETY_BOUNDARY: str = (
    "Bench renders this intent packet for review before any submit-capable action "
    "is validated through the command bridge."
)
_COPY_CAPITAL_LOCK_SHORT: str = (
    "Capital-bearing transitions remain locked while ADR 0004 is in force."
)
_COPY_PAPER_START: str = (
    "Paper-stage sessions use live feed with no capital exposure. "
    "Capital-bearing stages remain locked while ADR 0004 is in force."
)


def _action_kind(label: str) -> str:
    """Coarse classification from the action label.

    Promote/Demote/Return are prefix-matched (multiple target-stage suffixes
    exist); the invocation labels are fixed strings; Open Evidence is the
    informational floor.
    """
    if label.startswith("Promote to "):
        return "promote"
    if label.startswith("Demote to "):
        return "demote"
    if label.startswith("Return to "):
        return "return"
    if label == "Start Trading":
        return "start_trading"
    if label == "Stop Trading":
        return "stop_trading"
    if label == "Initiate Backtest":
        return "initiate_backtest"
    if label == "Refresh Backtest":
        return "refresh_backtest"
    if label == "Freeze Manifest":
        return "freeze_manifest"
    if label == "Open Evidence":
        return "open_evidence"
    return "unknown"


def _is_capital_bearing(label: str, target_stage: str, current_stage: str) -> bool:
    """Classify whether an action crosses ADR 0004 capital-bearing territory.

    Mirrors the PR L QML `_isCapitalBoundary` helper, including the paper-
    stage Start Trading refinement: paper sessions use live feed with no
    capital exposure and are NOT capital-bearing.
    """
    if target_stage in {"micro_live", "live"}:
        return True
    if "Micro Live" in label or "Live" in label:
        return True
    if label == "Start Trading":
        return current_stage in {"micro_live", "live"}
    return False


def _safety_copy(label: str, current_stage: str, capital_bearing: bool) -> str:
    base = _COPY_SAFETY_BOUNDARY
    if label == "Start Trading" and current_stage == "paper":
        return base + "\n\n" + _COPY_PAPER_START
    if capital_bearing:
        return base + "\n\n" + _COPY_CAPITAL_LOCK_SHORT
    return base


def _is_submit_capable_action(kind: str, target_stage: str, current_stage: str) -> bool:
    if kind in {
        "demote",
        "freeze_manifest",
        "initiate_backtest",
        "refresh_backtest",
    }:
        return True
    if kind == "promote":
        return target_stage == "paper"
    if kind == "return":
        return target_stage == "idle"
    if kind in {"start_trading", "stop_trading"}:
        return current_stage == "paper"
    return False


def _action_intent_preview(row: _StrategyRow, item: Any) -> dict[str, Any]:
    """Normalized read-only Action Intent Preview (PR N, ADR 0049).

    Carries the descriptive metadata a confirmation modal needs to render
    an Intent Packet without recomputing classifications in QML. This is a
    *preview*: it never carries a command payload, a proposal object, or any
    field whose name implies execution (submit, dispatch, broker, event).

    The ``executable`` and ``wired`` flags now track *submit-capability* —
    True for actions the ADR-0051 command facade can submit, False otherwise.
    The wired command path landed with ADR 0051 (BenchSurface action-menu
    wiring); downstream UI reads these flags to gate confirmation vs. preview.
    """
    label = item.label
    target_stage = item.target_stage or ""
    kind = _action_kind(label)
    capital_bearing = _is_capital_bearing(label, target_stage, row.stage)
    submit_capable = _is_submit_capable_action(kind, target_stage, row.stage)
    return {
        "schemaVersion": 1,
        "source": {
            "kind": "gui_read_model_preview",
            "authoritative": False,
            "note": _ACTION_PREVIEW_SOURCE_NOTE,
        },
        "strategyId": row.strategy_id,
        "strategyName": row.name,
        "actionKind": kind,
        "actionLabel": label,
        "verbClass": item.verb_class,
        "currentStage": row.stage,
        "targetStage": target_stage,
        "intentCopy": _ACTION_INTENT_COPY.get(kind, _ACTION_INTENT_COPY["unknown"]),
        "requirements": list(_ACTION_REQUIREMENTS),
        "futureRecord": _ACTION_FUTURE_RECORD.get(kind, "—"),
        "capitalBearing": capital_bearing,
        "safetyCopy": _safety_copy(label, row.stage, capital_bearing),
        "executable": submit_capable,
        "wired": submit_capable,
    }


def _evidence_packet(row: _StrategyRow) -> dict[str, Any]:
    """Normalized read-only Evidence Packet for a Bench row (PR M, ADR 0049).

    Consolidates the scattered evidence-related fields already carried on
    _StrategyRow into a single, stable contract for the Evidence modal and
    the Intent Packet preview.  This is a *shape*, not a reconstruction:
    freshness and gate verdicts are not authoritative in v1 — the packet
    only carries what the GUI read-model already exposes.

    Real event-derived freshness and gate reconstruction are deferred
    (ADR 0049 Decision 5). The ``source.authoritative`` flag MUST stay
    False and the ``gate.reconstructionDeferred`` flag MUST stay True
    until real event-derived evidence reconstruction lands; downstream
    UI uses them to keep the v1 framing explicit.
    """
    return {
        "schemaVersion": 1,
        "strategyId": row.strategy_id,
        "strategyName": row.name,
        "currentStage": row.stage,
        "source": {
            "kind": "gui_read_model_snapshot",
            "authoritative": False,
            "note": (
                "Bench v1 evidence is normalized from the current GUI read-model "
                "snapshot. Real event-derived freshness and gate reconstruction "
                "are deferred."
            ),
        },
        "metrics": {
            "sharpe": row.sharpe,
            "maxDrawdownPct": row.max_drawdown_pct,
            "tradeCount": row.trade_count,
        },
        "evidence": {
            "runId": row.evidence_run_id,
            "label": row.meta_evidence_label,
            "observedAt": row.meta_evidence_at,
            "promotedAt": row.promoted_at,
            "promotionType": row.promotion_type,
        },
        "gate": {
            "failures": list(row.gate_failures),
            "freshness": "not_reconstructed_v1",
            "gateResult": "not_reconstructed_v1",
            "reconstructionDeferred": True,
        },
        "status": {
            "kind": row.status_kind,
            "word": row.status_word,
            "tail": row.status_tail,
            "metaLine": row.meta_line,
        },
        "session": {
            "state": row.session_state,
            "id": row.session_id,
            "detail": row.session_detail,
        },
        "paperEvidence": dict(row.paper_evidence),
        "job": {
            "id": row.job_id,
            "status": row.job_status,
            "actionType": row.job_action_type,
            "detail": row.job_detail,
        },
    }

"""Bench action / intent / evidence engine for GUI read models.

Produces the ``actions`` menu, the per-action Action Intent Preview, and the
Evidence Packet that ``_StrategyRow.as_qml()`` attaches to each bench card.
All functions here are read-only display-layer projections — none submit, none
mutate state (ADR 0049 Decision 2 / ADR 0051 framing).

The only reference to ``_StrategyRow`` in this module is in type hints; the
runtime edge is one-way (``strategy_row`` imports this module lazily, never the
reverse) so the module graph stays an acyclic DAG.

Extracted verbatim from ``read_models.py`` (PR12 decompose). P2-12 then
consolidated the per-action-kind tables (intent copy, future-record label,
submit-capability) into the single ``ACTION_KIND_SPECS`` table below — the
canonical Python owner the QML confirmation modal and the Bench command
bridge both consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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

# ---------------------------------------------------------------------------
# Canonical action-kind spec (P2-12).
#
# Single Python owner for everything per-action-kind the GUI previously
# duplicated: plain-language intent copy, the future-record display label,
# the submit-capability rule, and the bridge action family a submit routes
# through. ``_action_intent_preview`` stamps every menu item with this data,
# so QML consumes the spec instead of re-declaring it; the command bridge's
# ``submitCapableActionFamilies()`` derives from the same table via
# ``submit_capable_action_families()``.
#
# ``bridge_family`` values are the ACTION_FAMILY_* strings declared in
# ``milodex.commands.bench``. They are restated as literals here because the
# ADR 0051 import perimeter reserves facade imports for the bridge module;
# tests cross-check that the two vocabularies stay in sync
# (tests/milodex/gui/test_bench_command_bridge.py).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionKindSpec:
    """Per-action-kind descriptor consumed by the preview and the bridge.

    ``submit_capable`` is the base flag; the two ``submit_requires_*`` fields
    narrow it (promote → paper target only, return → idle target only,
    start/stop trading → paper stage only). ``bridge_family`` names the
    command-bridge action family a submit-capable kind routes through —
    display vocabulary ≠ bridge vocabulary: initiate/refresh backtest both
    submit through ``backtest``; return-to-idle routes through ``demote``.
    """

    intent_copy: str
    future_record: str
    bridge_family: str | None = None
    submit_capable: bool = False
    submit_requires_target_stage: str | None = None
    submit_requires_current_stage: str | None = None


# Insertion order is load-bearing for submit_capable_action_families(): the
# derived family list must stay [demote, freeze_manifest, backtest,
# promote_to_paper, start_paper_runner, stop_paper_runner] — the order the
# bridge has exposed since Phase F.
ACTION_KIND_SPECS: dict[str, ActionKindSpec] = {
    "demote": ActionKindSpec(
        intent_copy=(
            "Move this strategy backward to an earlier stage and remove it from "
            "its current operating stage."
        ),
        future_record="demotion_event",
        bridge_family="demote",
        submit_capable=True,
    ),
    "return": ActionKindSpec(
        intent_copy=(
            "Restore this strategy to a previously eligible stage or return it to the idle shelf."
        ),
        future_record="stage_return_event",
        bridge_family="demote",
        submit_capable=True,
        submit_requires_target_stage="idle",
    ),
    "freeze_manifest": ActionKindSpec(
        intent_copy=(
            "Freeze the current strategy config as the active promotion manifest. "
            "Required before promoting to the next stage."
        ),
        future_record="manifest_freeze_event",
        bridge_family="freeze_manifest",
        submit_capable=True,
    ),
    "initiate_backtest": ActionKindSpec(
        intent_copy="Run canonical walk-forward backtest evidence for this strategy.",
        future_record="backtest_run",
        bridge_family="backtest",
        submit_capable=True,
    ),
    "refresh_backtest": ActionKindSpec(
        intent_copy=("Refresh aging or stale evidence with the canonical walk-forward backtest."),
        future_record="backtest_run",
        bridge_family="backtest",
        submit_capable=True,
    ),
    "promote": ActionKindSpec(
        intent_copy=(
            "Move this strategy forward from its current stage to the next stage "
            "after evidence and policy gates are satisfied."
        ),
        future_record="promotion_event",
        bridge_family="promote_to_paper",
        submit_capable=True,
        submit_requires_target_stage="paper",
    ),
    "start_trading": ActionKindSpec(
        intent_copy=(
            "Start a paper trading session for this strategy through the "
            "controlled runner boundary."
        ),
        future_record="session_start_event",
        bridge_family="start_paper_runner",
        submit_capable=True,
        submit_requires_current_stage="paper",
    ),
    "stop_trading": ActionKindSpec(
        intent_copy=(
            "Request a controlled stop for the current paper session. This is not the kill switch."
        ),
        future_record="session_stop_event",
        bridge_family="stop_paper_runner",
        submit_capable=True,
        submit_requires_current_stage="paper",
    ),
    "open_evidence": ActionKindSpec(
        intent_copy=(
            "Open the read-only Evidence snapshot for this strategy. "
            "Informational only — no state changes."
        ),
        future_record="evidence_view",
    ),
    "unknown": ActionKindSpec(
        intent_copy="Action not recognised by the intent preview.",
        future_record="—",
    ),
}


def submit_capable_action_families() -> list[str]:
    """Ordered, de-duplicated bridge action families with a submit-capable kind.

    Consumed by ``BenchCommandBridge.submitCapableActionFamilies()`` so the
    bridge introspection and the preview's submit-capability flags can never
    drift apart (P2-12).
    """
    families: list[str] = []
    for spec in ACTION_KIND_SPECS.values():
        if spec.submit_capable and spec.bridge_family and spec.bridge_family not in families:
            families.append(spec.bridge_family)
    return families


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


# D-8 amendment (founder-decided 2026-07-10; record
# docs/reviews/2026-07-10-D8-evidence-reconstruction-brief.md). The Bench v1
# Promote-to-Paper affordance is still computed from a hardcoded
# ``Freshness.FRESH`` (``_bench_evidence_by_stage``) — the system does NOT
# compute evidence freshness in v1. So the confirmation surface must state the
# evidence's age (from the backtest run's ``started_at``) and an explicit
# caveat that freshness is unverified. Display-only: this LABELS the affordance,
# it does not gate it; the ``Freshness.FRESH`` hardcode is untouched.
_COPY_FRESHNESS_CAVEAT_TAIL: str = (
    "Freshness is not computed in v1 — verify currency before promoting."
)


def _parse_utc_timestamp(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp to a UTC-aware datetime, or None on failure.

    Naive timestamps are assumed UTC. Any parse failure (blank, malformed,
    wrong type) returns None so callers render an honest "unknown" rather than
    a fabricated value or a raised exception.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _evidence_freshness_caveat(started_at_raw: str, now: datetime) -> str:
    """Build the D-8 Promote-to-Paper evidence-age + freshness caveat line.

    ``started_at_raw`` is the backtest run's ``started_at`` (the same value
    assemble_evidence_package derives as ``backtest_run_started_at``). ``now``
    MUST be a UTC-aware datetime. When the timestamp is absent or unparseable
    the age renders as unavailable — never a fabricated age, never a crash.
    """
    started = _parse_utc_timestamp(started_at_raw)
    if started is None:
        return (
            "Evidence age unavailable — backtest run start time not recorded. "
            + _COPY_FRESHNESS_CAVEAT_TAIL
        )
    age_days = (now - started).days
    day_word = "day" if age_days == 1 else "days"
    return (
        f"Evidence from backtest run started {started.date().isoformat()} "
        f"({age_days} {day_word} ago). " + _COPY_FRESHNESS_CAVEAT_TAIL
    )


def _is_submit_capable_action(kind: str, target_stage: str, current_stage: str) -> bool:
    """Evaluate the spec's submit-capability rule for *kind* in context."""
    spec = ACTION_KIND_SPECS.get(kind)
    if spec is None or not spec.submit_capable:
        return False
    if (
        spec.submit_requires_target_stage is not None
        and target_stage != spec.submit_requires_target_stage
    ):
        return False
    if (
        spec.submit_requires_current_stage is not None
        and current_stage != spec.submit_requires_current_stage
    ):
        return False
    return True


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
    spec = ACTION_KIND_SPECS.get(kind, ACTION_KIND_SPECS["unknown"])
    capital_bearing = _is_capital_bearing(label, target_stage, row.stage)
    submit_capable = _is_submit_capable_action(kind, target_stage, row.stage)
    safety_copy = _safety_copy(label, row.stage, capital_bearing)
    # D-8: the Promote-to-Paper confirmation must carry the evidence's age and
    # an explicit not-computed-in-v1 freshness caveat (display-only). Appended
    # to the already-rendered safety copy so no new QML field is needed.
    if kind == "promote" and target_stage == "paper":
        safety_copy += "\n\n" + _evidence_freshness_caveat(
            row.backtest_run_started_at, datetime.now(UTC)
        )
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
        "intentCopy": spec.intent_copy,
        "requirements": list(_ACTION_REQUIREMENTS),
        "futureRecord": spec.future_record,
        "capitalBearing": capital_bearing,
        "safetyCopy": safety_copy,
        "executable": submit_capable,
        "wired": submit_capable,
    }


#: Operator-facing copy for the Bench v1 gate sentinel (GUI audit finding #1 /
#: M2 item c). The raw machine value below (``"not_reconstructed_v1"``) used to
#: render verbatim in BenchEvidenceModal.qml's Freshness / Gate-result fields —
#: this is the humanized display-layer mapping only. It does NOT change the
#: machine value itself (D-8 deferral: ``gate.freshness`` / ``gate.gateResult``
#: stay byte-identical) — see ``_humanize_gate_sentinel``.
_GATE_SENTINEL_DISPLAY = "Deferred (v1) — not yet computed"


def _humanize_gate_sentinel(value: str) -> str:
    """Map the Bench v1 machine gate sentinel to operator-facing copy.

    Display-layer only: unknown values pass through unchanged so a future
    real sentinel doesn't get silently swallowed by this mapping.
    """
    if value == "not_reconstructed_v1":
        return _GATE_SENTINEL_DISPLAY
    return value


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

    ``gate.freshnessDisplay`` / ``gate.gateResultDisplay`` (GUI audit finding
    #1 / M2 item c) are humanized copies of ``gate.freshness`` / ``gate.gateResult``
    for the Evidence modal to render instead of the raw machine sentinel — the
    machine fields themselves are untouched.
    """
    freshness = "not_reconstructed_v1"
    gate_result = "not_reconstructed_v1"
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
            "freshness": freshness,
            "gateResult": gate_result,
            "reconstructionDeferred": True,
            "freshnessDisplay": _humanize_gate_sentinel(freshness),
            "gateResultDisplay": _humanize_gate_sentinel(gate_result),
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

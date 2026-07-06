"""Shared paper-promotion choreography (RM-010).

Single domain-owned entrypoint that CLI and Bench both call for
``backtest -> paper`` promotion. Owns the 6-step sequence: stage validation,
metrics resolution, gate evaluation, manifest hash derivation, evidence
assembly, atomic transition. Callers supply operator intent (recommendation,
known_risks, run_id, etc.) and render the structured result.

Per the accepted RM-002 exploration:

- CLI owns argument parsing, command-result rendering, and CLI-specific errors.
- Bench owns proposal/submit lifecycle, workflow-readiness blockers, and
  ``CommandProposal``/``CommandResult`` serialization.
- This module owns transition legality, metrics lookup, gate evaluation,
  manifest/evidence assembly, and durable transition dispatch.

Scope: ``backtest -> paper`` only. Capital-stage promotion remains
phase-locked by ADR 0004 / R-PRM-006 and must not be widened here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from milodex.promotion.evidence import EvidencePackage, assemble_evidence_package
from milodex.promotion.policy import ACTIVE_PROMOTION_POLICY, STAGE_PAPER, PromotionCheckResult
from milodex.promotion.run_evidence import compute_post_update_hash, metrics_from_run
from milodex.promotion.state_machine import (
    check_gate,
    transition,
    validate_stage_transition,
)
from milodex.strategies.loader import load_strategy_config

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore


# Stable, machine-readable refusal codes. CLI surfaces them in error payloads
# and Bench maps them onto its ``Blocker.reason_code`` taxonomy; both are
# operator-visible, so renames here are payload-breaking changes.
REASON_INVALID_STAGE_TRANSITION = "invalid_stage_transition"
REASON_MISSING_BACKTEST_RUN = "missing_backtest_run"
REASON_GATE_FAILED = "gate_failed"
# D-4 (ADR 0058) scoping + operator-override refusals.
REASON_LIFECYCLE_EXEMPT_NOT_SCOPED = "lifecycle_exempt_not_scoped"
REASON_OVERRIDE_FLAGS_CONFLICT = "override_flags_conflict"
REASON_OVERRIDE_STAGE_NOT_ALLOWED = "operator_override_stage_not_allowed"
REASON_OVERRIDE_REASON_REQUIRED = "operator_override_reason_required"

# Durable ``promotion_type`` value for a general operator override (ADR 0058).
# Distinct from ``lifecycle_exempt`` so the ledger never misdescribes a general
# gate bypass as a lifecycle-proof promotion.
PROMOTION_TYPE_OPERATOR_OVERRIDE = "operator_override"


@dataclass(frozen=True)
class PromoteRequest:
    """Operator intent for a single stage advancement.

    Slice-1 (RM-010) supports ``backtest -> paper``. Capital-stage promotion
    remains phase-locked; do not add capital-stage fields without an ADR.
    """

    strategy_id: str
    config_path: Path
    to_stage: str
    recommendation: str
    known_risks: list[str]
    approved_by: str
    run_id: str | None = None
    lifecycle_exempt: bool = False
    operator_override: bool = False
    notes: str | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class PromoteSuccess:
    """A durable transition committed: manifest event + promotion event + YAML rewrite."""

    strategy_id: str
    from_stage: str
    to_stage: str
    promotion_type: str
    manifest_hash: str
    manifest_id: int | None
    promotion_id: int | None
    evidence: EvidencePackage
    metrics_snapshot: dict[str, float | int | None]
    recorded_at: datetime
    backtest_run_id: str | None
    sharpe_ratio: float | None
    max_drawdown_pct: float | None
    trade_count: int | None


@dataclass(frozen=True)
class PromoteBlocked:
    """A known structured refusal. No durable writes occurred.

    ``from_stage`` and ``to_stage`` are populated on every refusal so callers
    can render operator-facing labels (e.g. "Stage: backtest -> paper") without
    re-loading the strategy config. They are ``None`` only for the defensive
    "future reason code" branch where the orchestrator hasn't established
    them.
    """

    reason_code: str
    message: str
    from_stage: str | None = None
    to_stage: str | None = None
    metrics_snapshot: dict[str, float | int | None] | None = None
    gate_failures: list[str] = field(default_factory=list)
    promotion_type: str | None = None


@dataclass(frozen=True)
class PromoteError:
    """An unexpected governance-time failure surfaced from ``transition()``.

    Reserved for ``ValueError`` from the governance callee that is NOT a
    structured refusal (e.g. manifest-hash mismatch, YAML stage-line not
    found). Durable state remains coherent — ``transition()`` writes the
    durable log first and rewrites the YAML after — but the orchestrator
    must not claim ``PromoteSuccess`` when the call raised.
    """

    message: str
    context: dict[str, Any] = field(default_factory=dict)


PromoteResult = PromoteSuccess | PromoteBlocked | PromoteError


def _check_bypass_admissibility(
    request: PromoteRequest,
    *,
    from_stage: str,
    to_stage: str,
) -> PromoteBlocked | None:
    """Fail-closed admissibility for the two gate-bypass mechanisms (ADR 0058).

    Returns a ``PromoteBlocked`` when the request is inadmissible, or ``None``
    when the requested bypass (if any) is allowed to proceed. No metric lookup
    and no durable writes happen here.

    Rules:

    1. ``lifecycle_exempt`` and ``operator_override`` are mutually exclusive —
       a request that sets both is refused (the operator must name exactly one
       intent).
    2. ``lifecycle_exempt`` is scoped to the policy's ``applies_to`` identity
       list. A lifecycle-exempt request for any other strategy id is refused and
       pointed at ``--operator-override``.
    3. ``operator_override`` is allowed ONLY for the paper stage — the autonomy
       boundary owns capital stages (``micro_live``/``live``), which no operator
       override may bypass.
    4. ``operator_override`` requires a non-empty operator reason. The
       ``recommendation`` field is the operator's written justification and is
       already mandatory for every promotion; the override reuses it as the
       recorded reason and refuses if it is blank.
    """
    if request.lifecycle_exempt and request.operator_override:
        return PromoteBlocked(
            reason_code=REASON_OVERRIDE_FLAGS_CONFLICT,
            message=(
                "lifecycle_exempt and operator_override are mutually exclusive — "
                "a promotion may name at most one gate-bypass mechanism (ADR 0058)."
            ),
            from_stage=from_stage,
            to_stage=to_stage,
        )

    if request.lifecycle_exempt:
        allowed_ids = ACTIVE_PROMOTION_POLICY.lifecycle_gate.applies_to
        if request.strategy_id not in allowed_ids:
            return PromoteBlocked(
                reason_code=REASON_LIFECYCLE_EXEMPT_NOT_SCOPED,
                message=(
                    f"Strategy '{request.strategy_id}' is not a lifecycle-proof "
                    f"strategy. The lifecycle exemption is scoped to the promotion "
                    f"policy's applies_to list {list(allowed_ids)} (ADR 0058). For a "
                    "deliberate statistical-gate bypass, use --operator-override "
                    "(paper-stage only, with an operator reason)."
                ),
                from_stage=from_stage,
                to_stage=to_stage,
            )

    if request.operator_override:
        if to_stage != STAGE_PAPER:
            return PromoteBlocked(
                reason_code=REASON_OVERRIDE_STAGE_NOT_ALLOWED,
                message=(
                    f"operator_override may bypass the statistical gate only for the "
                    f"'{STAGE_PAPER}' stage; '{to_stage}' is a capital stage owned by "
                    "the autonomy boundary and cannot be operator-overridden (ADR 0058)."
                ),
                from_stage=from_stage,
                to_stage=to_stage,
            )
        if not request.recommendation or not request.recommendation.strip():
            return PromoteBlocked(
                reason_code=REASON_OVERRIDE_REASON_REQUIRED,
                message=(
                    "operator_override requires a non-empty operator reason "
                    "(the --recommendation field) — a bare gate bypass is refused "
                    "(ADR 0058)."
                ),
                from_stage=from_stage,
                to_stage=to_stage,
            )

    return None


def _build_gate_check_outcome(
    request: PromoteRequest,
    gate_result: PromotionCheckResult,
) -> dict[str, Any]:
    """Serialize the durable gate outcome for the evidence package (ADR 0058).

    The statistical shape is preserved verbatim so legacy rows and tests do not
    change shape. The lifecycle and operator-override paths append extra keys:

    - lifecycle: the three R-PRM-004 criteria, marked unenforced with
      ``deferred='M4'`` — an honest record that the operational gate was defined
      but not evaluated (enforcement design belongs to roadmap M4).
    - operator_override: the operator's reason (the recommendation text),
      recording that the statistical gate was skipped by an explicit operator act.
    """
    outcome: dict[str, Any] = {
        "allowed": gate_result.allowed,
        "promotion_type": gate_result.promotion_type,
        "failures": list(gate_result.failures),
    }

    if gate_result.promotion_type == PROMOTION_TYPE_OPERATOR_OVERRIDE:
        outcome["operator_override"] = {
            "reason": (request.recommendation or "").strip(),
        }
        return outcome

    if request.lifecycle_exempt:
        lifecycle_gate = ACTIVE_PROMOTION_POLICY.lifecycle_gate
        outcome["lifecycle_criteria"] = {
            "criteria": list(lifecycle_gate.criteria),
            "enforced": lifecycle_gate.enforced,
            "deferred": "M4",
        }

    return outcome


def prepare_and_record_promotion(
    request: PromoteRequest,
    event_store: EventStore,
) -> PromoteResult:
    """Run the full paper-promotion choreography.

    Sequence (matches the RM-002 exploration):

    1. Load the strategy config; derive ``from_stage``.
    2. Validate the stage transition (legality, Phase-1 live-lock).
    3. Resolve backtest metrics from ``run_id`` (``None`` for lifecycle-exempt).
    4. Evaluate the promotion gate at ``to_stage``.
    5. Compute the post-update manifest hash.
    6. Assemble the evidence package.
    7. Dispatch the atomic governance transition.

    No step writes durably until step 7. Refusals at steps 2-4 return a
    ``PromoteBlocked`` with no side effects. Evidence-input validation
    (blank ``recommendation``/``known_risks``) propagates as ``ValueError``
    from ``assemble_evidence_package`` — callers are expected to gate
    operator input *before* invoking the orchestrator (CLI does this via
    ``_require_evidence_inputs``; Bench does it at propose-time).
    """
    config = load_strategy_config(request.config_path)
    from_stage = config.stage
    to_stage = request.to_stage

    try:
        validate_stage_transition(from_stage, to_stage)
    except ValueError as exc:
        return PromoteBlocked(
            reason_code=REASON_INVALID_STAGE_TRANSITION,
            message=str(exc),
            from_stage=from_stage,
            to_stage=to_stage,
        )

    # D-4 (ADR 0058) gate-bypass admissibility. These refusals are fail-closed
    # and evaluated BEFORE any metric lookup — a request that names two
    # conflicting bypass mechanisms, or an unscoped lifecycle exemption, or an
    # unqualified operator override, is refused with no side effects.
    bypass_refusal = _check_bypass_admissibility(request, from_stage=from_stage, to_stage=to_stage)
    if bypass_refusal is not None:
        return bypass_refusal

    try:
        sharpe_ratio, max_drawdown_pct, trade_count = metrics_from_run(request.run_id, event_store)
    except ValueError as exc:
        return PromoteBlocked(
            reason_code=REASON_MISSING_BACKTEST_RUN,
            message=str(exc),
            from_stage=from_stage,
            to_stage=to_stage,
        )

    metrics_snapshot: dict[str, float | int | None] = {
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown_pct": max_drawdown_pct,
        "trade_count": trade_count,
    }

    if request.operator_override:
        # A general operator override is an explicit, loudly-recorded act that
        # skips the statistical gate. It does NOT route through check_gate's
        # lifecycle branch — the result is constructed here so the durable
        # ledger carries promotion_type='operator_override', never a
        # lifecycle-proof label (ADR 0058). Admissibility (paper-only, non-empty
        # reason) was already enforced by _check_bypass_admissibility above.
        gate_result = PromotionCheckResult(
            allowed=True,
            promotion_type=PROMOTION_TYPE_OPERATOR_OVERRIDE,
            failures=[],
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
        )
    else:
        # `.get(k, default)` returns None (not the default) for a present-but-null
        # key; the regime config sets `min_trades_required: null` (R-PRM-004
        # lifecycle exemption). Mirror cli._min_trade_count_from_engine: treat
        # present-but-None as the statistical floor instead of crashing on int(None)
        # — otherwise the one strategy the lifecycle exemption exists for cannot be
        # promoted through this path (the crash lands before check_gate's exempt
        # short-circuit can fire).
        min_trades_configured = config.backtest.get("min_trades_required")
        min_trade_count = 30 if min_trades_configured is None else int(min_trades_configured)
        gate_result = check_gate(
            lifecycle_exempt=request.lifecycle_exempt,
            to_stage=to_stage,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
            min_trade_count=min_trade_count,
        )

    if not gate_result.allowed:
        return PromoteBlocked(
            reason_code=REASON_GATE_FAILED,
            message="; ".join(gate_result.failures) or "gate check failed",
            from_stage=from_stage,
            to_stage=to_stage,
            metrics_snapshot=metrics_snapshot,
            gate_failures=list(gate_result.failures),
            promotion_type=gate_result.promotion_type,
        )

    manifest_hash = compute_post_update_hash(config.raw_data, to_stage)
    evidence = assemble_evidence_package(
        strategy_id=request.strategy_id,
        from_stage=from_stage,
        to_stage=to_stage,
        manifest_hash=manifest_hash,
        backtest_run_id=request.run_id,
        recommendation=request.recommendation,
        known_risks=list(request.known_risks),
        promotion_type=gate_result.promotion_type,
        gate_check_outcome=_build_gate_check_outcome(request, gate_result),
        metrics_snapshot=metrics_snapshot,
        event_store=event_store,
        now=request.now,
    )

    try:
        event = transition(
            config_path=request.config_path,
            to_stage=to_stage,
            gate_result=gate_result,
            evidence=evidence,
            approved_by=request.approved_by,
            event_store=event_store,
            backtest_run_id=request.run_id,
            notes=request.notes,
            now=request.now,
        )
    except ValueError as exc:
        return PromoteError(
            message=str(exc),
            context={"callee": "milodex.promotion.state_machine.transition"},
        )

    return PromoteSuccess(
        strategy_id=event.strategy_id,
        from_stage=event.from_stage,
        to_stage=event.to_stage,
        promotion_type=event.promotion_type,
        manifest_hash=manifest_hash,
        manifest_id=event.manifest_id,
        promotion_id=event.id,
        evidence=evidence,
        metrics_snapshot=metrics_snapshot,
        recorded_at=event.recorded_at,
        backtest_run_id=event.backtest_run_id,
        sharpe_ratio=event.sharpe_ratio,
        max_drawdown_pct=event.max_drawdown_pct,
        trade_count=event.trade_count,
    )

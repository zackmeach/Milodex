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
    """A known structured refusal. No durable writes occurred."""

    reason_code: str
    message: str
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
        )

    try:
        sharpe_ratio, max_drawdown_pct, trade_count = metrics_from_run(
            request.run_id, event_store
        )
    except ValueError as exc:
        return PromoteBlocked(
            reason_code=REASON_MISSING_BACKTEST_RUN,
            message=str(exc),
        )

    gate_result = check_gate(
        lifecycle_exempt=request.lifecycle_exempt,
        to_stage=to_stage,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
        min_trade_count=int(config.backtest.get("min_trades_required", 30)),
    )
    metrics_snapshot: dict[str, float | int | None] = {
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown_pct": max_drawdown_pct,
        "trade_count": trade_count,
    }
    if not gate_result.allowed:
        return PromoteBlocked(
            reason_code=REASON_GATE_FAILED,
            message="; ".join(gate_result.failures) or "gate check failed",
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
        gate_check_outcome={
            "allowed": gate_result.allowed,
            "promotion_type": gate_result.promotion_type,
            "failures": list(gate_result.failures),
        },
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

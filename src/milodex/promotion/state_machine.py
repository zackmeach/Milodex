"""Strategy promotion gate checker.

Rules
-----
Stage progression order: backtest → paper → micro_live → live.
No stage may be skipped; no downgrade is allowed.

Stage-aware thresholds (ADR 0028 supersedes the single-threshold
formulation in ADR 0020):

Statistical strategies (promotion_type='statistical')
  backtest → paper (paper-readiness):
    Sharpe ratio     > 0.0
    Max drawdown     < 25.0%
    Round-trips      >= 15
  paper → micro_live and micro_live → live (live-readiness):
    Sharpe ratio     > 0.5          (SRS R-PRM-001)
    Max drawdown     < 15.0%        (SRS R-PRM-002)
    Round-trips      >= 30          (SRS R-PRM-003, R-BKT-003)

The asymmetry is deliberate. A false negative at the paper-readiness gate
scraps a real edge before any data is collected. A false positive only
occupies one paper slot at $0 risk. The bar should match the cost.

The gate evaluates ``round_trip_count`` (closed positions) when provided,
falling back to ``trade_count`` (raw fills) when not. This is the
counter introduced in PR 2.3 of the rejection-remediation plan; it
captures statistical-power evidence more honestly than the fill count.

Lifecycle-exempt strategies (promotion_type='lifecycle_exempt')
  Statistical thresholds do not apply (SRS R-PRM-004).
  The caller is responsible for passing --lifecycle-exempt only for
  strategies that qualify (currently the regime SPY/SHY strategy).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from milodex.core.event_store import PromotionEvent, StrategyManifestEvent
from milodex.strategies.loader import canonicalize_config_data, load_strategy_config

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore
    from milodex.promotion.evidence import EvidencePackage

STAGE_ORDER: list[str] = ["backtest", "paper", "micro_live", "live"]

# Phase 1 blocks promotion to these stages (ADR 0004, R-PRM-006). The risk
# layer's R-EXE-007 provides runtime defense-in-depth; this constant provides
# the promotion-time refusal. Remove this — and the check in
# validate_stage_transition — in the future ADR that lifts the live-lock.
PHASE_ONE_BLOCKED_STAGES: frozenset[str] = frozenset({"micro_live", "live"})

# Stage-aware threshold dicts (ADR 0028).
PAPER_READINESS_THRESHOLDS: dict[str, float | int] = {
    "min_sharpe": 0.0,
    "max_drawdown_pct": 25.0,
    "min_round_trips": 15,
}
LIVE_READINESS_THRESHOLDS: dict[str, float | int] = {
    "min_sharpe": 0.5,
    "max_drawdown_pct": 15.0,
    "min_round_trips": 30,
}
THRESHOLDS_BY_TARGET_STAGE: dict[str, dict[str, float | int]] = {
    "paper": PAPER_READINESS_THRESHOLDS,
    "micro_live": LIVE_READINESS_THRESHOLDS,
    "live": LIVE_READINESS_THRESHOLDS,
}

# Backward-compat aliases — keep pointing at the live thresholds because
# that's the bar these were intended to encode pre-ADR 0028. Some external
# code may still import these directly. Internal callers should use the
# stage-aware dicts above.
MIN_SHARPE: float = float(LIVE_READINESS_THRESHOLDS["min_sharpe"])
MAX_DRAWDOWN_PCT: float = float(LIVE_READINESS_THRESHOLDS["max_drawdown_pct"])
MIN_TRADES: int = int(LIVE_READINESS_THRESHOLDS["min_round_trips"])


@dataclass(frozen=True)
class PromotionCheckResult:
    """Gate check outcome for a single promotion request."""

    allowed: bool
    promotion_type: str
    failures: list[str] = field(default_factory=list)
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None
    round_trip_count: int | None = None
    to_stage: str | None = None


def validate_stage_transition(from_stage: str, to_stage: str) -> None:
    """Raise ``ValueError`` if the transition is invalid.

    Invalid cases: unknown stage, same stage, downgrade, or stage-skip.
    """
    if from_stage not in STAGE_ORDER:
        msg = f"Unknown from_stage '{from_stage}'. Valid stages: {STAGE_ORDER}."
        raise ValueError(msg)
    if to_stage not in STAGE_ORDER:
        msg = f"Unknown to_stage '{to_stage}'. Valid stages: {STAGE_ORDER}."
        raise ValueError(msg)

    from_idx = STAGE_ORDER.index(from_stage)
    to_idx = STAGE_ORDER.index(to_stage)

    if to_idx == from_idx:
        msg = f"Strategy is already at stage '{from_stage}'."
        raise ValueError(msg)
    if to_idx < from_idx:
        msg = f"Cannot downgrade from '{from_stage}' to '{to_stage}'."
        raise ValueError(msg)
    if to_idx != from_idx + 1:
        msg = (
            f"Skipping stages is not allowed: '{from_stage}' → '{to_stage}'. "
            f"Next valid stage is '{STAGE_ORDER[from_idx + 1]}'."
        )
        raise ValueError(msg)

    if to_stage in PHASE_ONE_BLOCKED_STAGES:
        msg = (
            f"Promotion to '{to_stage}' is blocked during Phase 1 "
            f"(ADR 0004, R-PRM-006). Paper-only period — the live-stage lock "
            f"lifts via a future ADR, not a config edit."
        )
        raise ValueError(msg)


def check_gate(
    *,
    to_stage: str,
    lifecycle_exempt: bool,
    sharpe_ratio: float | None,
    max_drawdown_pct: float | None,
    trade_count: int | None = None,
    round_trip_count: int | None = None,
) -> PromotionCheckResult:
    """Evaluate stage-aware statistical promotion thresholds.

    ``to_stage`` selects the threshold dict (paper-readiness vs
    live-readiness; see ADR 0028). When ``lifecycle_exempt=True`` the
    thresholds are bypassed and the check always passes
    (promotion_type='lifecycle_exempt').

    The "trade count" leg of the gate evaluates ``round_trip_count``
    (closed positions) when provided. Callers that only have a raw
    ``trade_count`` (fills) may pass that instead — back-compat for
    pre-PR-2.3 evidence rows. New callers should always pass
    ``round_trip_count``.
    """
    if to_stage not in THRESHOLDS_BY_TARGET_STAGE:
        msg = (
            f"check_gate received to_stage={to_stage!r}; expected one of "
            f"{sorted(THRESHOLDS_BY_TARGET_STAGE)}."
        )
        raise ValueError(msg)
    thresholds = THRESHOLDS_BY_TARGET_STAGE[to_stage]

    if lifecycle_exempt:
        return PromotionCheckResult(
            allowed=True,
            promotion_type="lifecycle_exempt",
            failures=[],
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
            round_trip_count=round_trip_count,
            to_stage=to_stage,
        )

    failures: list[str] = []

    min_sharpe = float(thresholds["min_sharpe"])
    max_dd = float(thresholds["max_drawdown_pct"])
    min_round_trips = int(thresholds["min_round_trips"])

    if sharpe_ratio is None or sharpe_ratio <= min_sharpe:
        failures.append(
            f"Sharpe {_fmt_or_none(sharpe_ratio)} must be > {min_sharpe} "
            f"for promotion to '{to_stage}' (got {_fmt_or_none(sharpe_ratio)})"
        )

    if max_drawdown_pct is None or max_drawdown_pct >= max_dd:
        failures.append(
            f"Max drawdown {_fmt_or_none(max_drawdown_pct)}% must be < {max_dd}% "
            f"for promotion to '{to_stage}' (got {_fmt_or_none(max_drawdown_pct)})"
        )

    evidence_count = round_trip_count if round_trip_count is not None else trade_count
    if evidence_count is None or evidence_count < min_round_trips:
        kind = "Round-trip" if round_trip_count is not None else "Trade"
        failures.append(
            f"{kind} count must be >= {min_round_trips} "
            f"for promotion to '{to_stage}' (got {_fmt_or_none(evidence_count)})"
        )

    return PromotionCheckResult(
        allowed=len(failures) == 0,
        promotion_type="statistical",
        failures=failures,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
        round_trip_count=round_trip_count,
        to_stage=to_stage,
    )


def _fmt_or_none(value: float | int | None) -> str:
    return "None" if value is None else str(value)


def transition(
    *,
    config_path: Path,
    to_stage: str,
    gate_result: PromotionCheckResult,
    evidence: EvidencePackage,
    approved_by: str,
    event_store: EventStore,
    backtest_run_id: str | None = None,
    notes: str | None = None,
    now: datetime | None = None,
) -> PromotionEvent:
    """Atomic promotion: freeze + append + YAML update (plan AD-5).

    Sequence:
      1. Build the manifest for ``to_stage`` from the YAML with its ``stage:``
         line mentally set to the target — the hash must match what the runtime
         will see AFTER step 3.
      2. Insert manifest + promotion (with evidence_json) in a single event-store
         transaction via :meth:`EventStore.append_manifest_and_promotion`.
      3. After the durable commit succeeds, update the YAML's ``stage:`` line
         in-place. A failure here leaves durable state coherent (manifest and
         promotion are written, YAML is stale) and the next cycle's drift check
         surfaces the discrepancy — the standard "durable log first, side
         effects after" pattern.

    Callers must have already:
      - called :func:`validate_stage_transition`
      - obtained a passing ``gate_result`` from :func:`check_gate`
      - assembled ``evidence`` via ``assemble_evidence_package`` with a
        ``manifest_hash`` matching the post-update YAML hash
    """
    if not gate_result.allowed:
        msg = (
            "transition() called with a failed gate check — refuse. "
            f"Failures: {gate_result.failures}"
        )
        raise ValueError(msg)

    config = load_strategy_config(config_path)
    from_stage = config.stage
    recorded_at = now or datetime.now(tz=UTC)

    canonical = canonicalize_config_data(_raw_data_with_stage(config.raw_data, to_stage))
    config_hash = _hash_canonical(canonical)

    if config_hash != evidence.manifest_hash:
        msg = (
            "Evidence manifest_hash does not match the post-update YAML hash. "
            f"Expected {config_hash}, evidence carried {evidence.manifest_hash}. "
            "Re-assemble the evidence package with the correct hash and retry."
        )
        raise ValueError(msg)

    manifest = StrategyManifestEvent(
        strategy_id=config.strategy_id,
        stage=to_stage,
        config_hash=config_hash,
        config_json=canonical,
        config_path=str(config_path),
        frozen_at=recorded_at,
        frozen_by=approved_by,
    )
    promotion = PromotionEvent(
        strategy_id=config.strategy_id,
        from_stage=from_stage,
        to_stage=to_stage,
        promotion_type=gate_result.promotion_type,
        approved_by=approved_by,
        recorded_at=recorded_at,
        backtest_run_id=backtest_run_id,
        sharpe_ratio=gate_result.sharpe_ratio,
        max_drawdown_pct=gate_result.max_drawdown_pct,
        trade_count=gate_result.trade_count,
        notes=notes,
        evidence_json=evidence.as_dict(),
    )

    manifest_id, promotion_id = event_store.append_manifest_and_promotion(
        manifest=manifest,
        promotion=promotion,
    )

    _update_stage_in_yaml(config_path, from_stage, to_stage)

    return PromotionEvent(
        strategy_id=promotion.strategy_id,
        from_stage=promotion.from_stage,
        to_stage=promotion.to_stage,
        promotion_type=promotion.promotion_type,
        approved_by=promotion.approved_by,
        recorded_at=promotion.recorded_at,
        backtest_run_id=promotion.backtest_run_id,
        sharpe_ratio=promotion.sharpe_ratio,
        max_drawdown_pct=promotion.max_drawdown_pct,
        trade_count=promotion.trade_count,
        notes=promotion.notes,
        manifest_id=manifest_id,
        reverses_event_id=None,
        evidence_json=promotion.evidence_json,
        id=promotion_id,
    )


_DEMOTE_TARGETS = frozenset({"backtest", "disabled"})


def demote(
    *,
    config_path: Path,
    to_stage: str,
    reason: str,
    approved_by: str,
    event_store: EventStore,
    evidence_ref: str | None = None,
    now: datetime | None = None,
) -> PromotionEvent:
    """Demote ``strategy_id`` to ``to_stage`` (``backtest`` or ``disabled``).

    Demotion is always allowed — operator intent is authoritative, no gate
    check runs (plan AD-6). Writes a ``PromotionEvent`` with
    ``promotion_type='demotion'`` and ``reverses_event_id`` pointing at the
    most recent non-reversed promotion for the strategy, producing the
    reversal chain that ``history`` will walk.

    Side effects:
      - ``to_stage='backtest'``: updates the YAML ``stage:`` line from the
        current stage to ``backtest`` so the runner treats it as pre-paper.
      - ``to_stage='disabled'``: YAML untouched; the demotion lives only in
        the governance ledger. (Runtime refusal lands in slice 3.)

    ``reason`` is required and must be non-blank; passing an empty string
    raises ``ValueError`` before any DB write.
    """
    if to_stage not in _DEMOTE_TARGETS:
        msg = (
            f"Demote target '{to_stage}' is not supported in slice 2. "
            f"Valid targets: {sorted(_DEMOTE_TARGETS)}."
        )
        raise ValueError(msg)
    if reason is None or not reason.strip():
        msg = "Demote requires a non-blank --reason."
        raise ValueError(msg)

    config = load_strategy_config(config_path)
    from_stage = config.stage
    if from_stage == to_stage:
        msg = f"Strategy is already at stage '{from_stage}'."
        raise ValueError(msg)

    prior = event_store.get_latest_promotion_for_strategy(config.strategy_id)
    reverses_event_id: int | None = None
    if prior is not None and prior.promotion_type != "demotion":
        reverses_event_id = prior.id

    recorded_at = now or datetime.now(tz=UTC)
    notes_parts = [reason.strip()]
    if evidence_ref and evidence_ref.strip():
        notes_parts.append(f"evidence_ref={evidence_ref.strip()}")
    notes = " | ".join(notes_parts)

    promotion = PromotionEvent(
        strategy_id=config.strategy_id,
        from_stage=from_stage,
        to_stage=to_stage,
        promotion_type="demotion",
        approved_by=approved_by,
        recorded_at=recorded_at,
        notes=notes,
        reverses_event_id=reverses_event_id,
    )
    promotion_id = event_store.append_promotion(promotion)

    if to_stage == "backtest":
        _update_stage_in_yaml(config_path, from_stage, "backtest")

    return PromotionEvent(
        strategy_id=promotion.strategy_id,
        from_stage=promotion.from_stage,
        to_stage=promotion.to_stage,
        promotion_type=promotion.promotion_type,
        approved_by=promotion.approved_by,
        recorded_at=promotion.recorded_at,
        notes=promotion.notes,
        reverses_event_id=promotion.reverses_event_id,
        id=promotion_id,
    )


def _raw_data_with_stage(raw_data: dict, to_stage: str) -> dict:
    """Return a deep-ish copy of ``raw_data`` with ``strategy.stage`` set to ``to_stage``."""
    strategy = dict(raw_data["strategy"])
    strategy["stage"] = to_stage
    return {**raw_data, "strategy": strategy}


def _hash_canonical(canonical: dict) -> str:
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _update_stage_in_yaml(path: Path, from_stage: str, to_stage: str) -> None:
    """Replace the ``stage:`` line in the YAML in-place (preserves comments/formatting)."""
    content = path.read_text(encoding="utf-8")
    old = f'stage: "{from_stage}"'
    new = f'stage: "{to_stage}"'
    if old not in content:
        msg = (
            f"Could not find 'stage: \"{from_stage}\"' in {path}. "
            "Durable state is written, but the YAML could not be updated; "
            "the next cycle's drift check will flag this discrepancy."
        )
        raise ValueError(msg)
    path.write_text(content.replace(old, new, 1), encoding="utf-8")

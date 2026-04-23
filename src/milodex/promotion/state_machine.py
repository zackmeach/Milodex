"""Strategy promotion gate checker.

Rules
-----
Stage progression order: backtest → paper → micro_live → live.
No stage may be skipped; no downgrade is allowed.

Statistical strategies (promotion_type='statistical')
  Sharpe ratio     > 0.5          (SRS R-PRM-001)
  Max drawdown     < 15.0%        (SRS R-PRM-002)
  Trade count      >= 30          (SRS R-PRM-003, R-BKT-003)

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

MIN_SHARPE: float = 0.5
MAX_DRAWDOWN_PCT: float = 15.0
MIN_TRADES: int = 30


@dataclass(frozen=True)
class PromotionCheckResult:
    """Gate check outcome for a single promotion request."""

    allowed: bool
    promotion_type: str
    failures: list[str] = field(default_factory=list)
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None


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


def check_gate(
    *,
    lifecycle_exempt: bool,
    sharpe_ratio: float | None,
    max_drawdown_pct: float | None,
    trade_count: int | None,
) -> PromotionCheckResult:
    """Evaluate statistical promotion thresholds.

    When ``lifecycle_exempt=True`` the thresholds are bypassed and the check
    always passes (promotion_type='lifecycle_exempt').
    """
    if lifecycle_exempt:
        return PromotionCheckResult(
            allowed=True,
            promotion_type="lifecycle_exempt",
            failures=[],
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
        )

    failures: list[str] = []

    if sharpe_ratio is None or sharpe_ratio <= MIN_SHARPE:
        failures.append(
            f"Sharpe {_fmt_or_none(sharpe_ratio)} must be > {MIN_SHARPE} "
            f"(got {_fmt_or_none(sharpe_ratio)})"
        )

    if max_drawdown_pct is None or max_drawdown_pct >= MAX_DRAWDOWN_PCT:
        failures.append(
            f"Max drawdown {_fmt_or_none(max_drawdown_pct)}% must be < {MAX_DRAWDOWN_PCT}% "
            f"(got {_fmt_or_none(max_drawdown_pct)})"
        )

    if trade_count is None or trade_count < MIN_TRADES:
        failures.append(f"Trade count must be >= {MIN_TRADES} (got {_fmt_or_none(trade_count)})")

    return PromotionCheckResult(
        allowed=len(failures) == 0,
        promotion_type="statistical",
        failures=failures,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
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

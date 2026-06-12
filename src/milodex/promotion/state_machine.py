"""Strategy promotion gate checker.

Rules
-----
Stage progression order: backtest → paper → micro_live → live.
No stage may be skipped; no downgrade is allowed.

Gate-decision policy lives in milodex.promotion.policy (ADR 0052); this
module owns structural transition legality and mechanics.

Statistical strategies (promotion_type='statistical')
  Backtest -> paper:
    Paper-readiness gate — see ACTIVE_PROMOTION_POLICY.paper_gate for
    the authoritative Sharpe, max-drawdown, and trade-count thresholds.

  Later capital stages:
    Capital-readiness gate — see ACTIVE_PROMOTION_POLICY.capital_gate
    and ACTIVE_PROMOTION_POLICY.default_trade_floor (SRS R-PRM-004).

Lifecycle-exempt strategies (promotion_type='lifecycle_exempt')
  Statistical thresholds do not apply (SRS R-PRM-004).
  The caller is responsible for passing --lifecycle-exempt only for
  strategies that qualify (currently the regime SPY/SHY strategy).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from milodex.core.event_store import PromotionEvent, StrategyManifestEvent
from milodex.promotion.policy import (
    ACTIVE_PROMOTION_POLICY,
    PromotionCheckResult,
)
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

# Backward-compatible aliases. The source of truth is
# milodex.promotion.policy.ACTIVE_PROMOTION_POLICY (ADR 0052). These names are
# public (re-exported by promotion/__init__.py; imported by gui/read_models.py
# and gui/strategy_bank_state.py) so they are retained as policy-derived
# aliases, not deleted — values are identical.
MIN_SHARPE: float = ACTIVE_PROMOTION_POLICY.capital_gate.min_sharpe
MAX_DRAWDOWN_PCT: float = ACTIVE_PROMOTION_POLICY.capital_gate.max_drawdown_pct
MIN_TRADES: int = ACTIVE_PROMOTION_POLICY.default_trade_floor
PAPER_MIN_SHARPE: float = ACTIVE_PROMOTION_POLICY.paper_gate.min_sharpe
PAPER_MAX_DRAWDOWN_PCT: float = ACTIVE_PROMOTION_POLICY.paper_gate.max_drawdown_pct


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
    lifecycle_exempt: bool,
    to_stage: str = "micro_live",
    sharpe_ratio: float | None,
    max_drawdown_pct: float | None,
    trade_count: int | None,
    min_trade_count: int = MIN_TRADES,
) -> PromotionCheckResult:
    """Evaluate statistical promotion thresholds.

    Thin adapter over ``ACTIVE_PROMOTION_POLICY`` (ADR 0052). When
    ``lifecycle_exempt=True`` the thresholds are bypassed and the check
    always passes (promotion_type='lifecycle_exempt') — define-only; the
    lifecycle operational gate is intentionally NOT enforced here.
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
    return ACTIVE_PROMOTION_POLICY.evaluate_research_target(
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
        target_stage=to_stage,
        min_trade_count=min_trade_count,
    )


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
    """Durable-log-first promotion: freeze + append, then YAML update (plan AD-5).

    NOT atomic across the event store and the YAML file — the DB rows and the
    file write are separate operations, deliberately ordered durable-log-first.

    Sequence:
      1. Build the manifest for ``to_stage`` from the YAML with its ``stage:``
         line mentally set to the target — the hash must match what the runtime
         will see AFTER step 4.
      2. Precompute and validate the YAML ``stage:`` line rewrite. If the line
         cannot be matched (e.g. a trailing inline comment), fail here —
         BEFORE anything durable is written.
      3. Insert manifest + promotion (with evidence_json) in a single event-store
         transaction via :meth:`EventStore.append_manifest_and_promotion`.
      4. After the durable commit succeeds, write the precomputed YAML content.
         A failure here leaves durable state coherent (manifest and promotion
         are written, YAML is stale) and the next cycle's drift check surfaces
         the discrepancy — the standard "durable log first, side effects after"
         pattern.

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

    # Precompute the YAML rewrite so a stage-line match failure aborts the
    # promotion BEFORE any durable state exists (TOCTOU stance: read once,
    # match once, apply this exact content after the commit — no re-read).
    updated_yaml = _prepare_stage_yaml_update(config_path, from_stage, to_stage)

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

    _write_stage_yaml(config_path, updated_yaml)

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


_DEMOTE_TARGETS = frozenset({"idle", "backtest", "disabled"})


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
    """Demote ``strategy_id`` to ``to_stage`` (``idle``, ``backtest``, or ``disabled``).

    Demotion is always allowed — operator intent is authoritative, no gate
    check runs (plan AD-6). Writes a ``PromotionEvent`` with
    ``promotion_type='demotion'`` and ``reverses_event_id`` pointing at the
    most recent non-reversed promotion for the strategy, producing the
    reversal chain that ``history`` will walk.

    Side effects:
      - ``to_stage='idle'`` / ``'backtest'``: updates the YAML ``stage:`` line
        from the current stage so the runner treats it as non-paper. The
        rewrite is precomputed and validated before the DB write (same
        durable-log-first ordering as :func:`transition`), so an unmatched
        stage line refuses before any durable state exists.
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

    # Same durable-log-first ordering as transition(): precompute the YAML
    # rewrite so a stage-line match failure refuses BEFORE the DB write.
    updated_yaml: str | None = None
    if to_stage in {"idle", "backtest"}:
        updated_yaml = _prepare_stage_yaml_update(config_path, from_stage, to_stage)

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

    if updated_yaml is not None:
        _write_stage_yaml(config_path, updated_yaml)

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


def _prepare_stage_yaml_update(path: Path, from_stage: str, to_stage: str) -> str:
    """Locate the ``stage:`` line and return the rewritten file content.

    Tolerant of single quotes, double quotes, no quotes, and variable
    whitespace around the value — a real promotion must not leave the YAML
    stale (and so block all trading for the strategy on the next runner
    cycle) merely because the ``stage:`` line is single-quoted or spaced
    differently. The rewrite is a targeted single-line substitution so
    surrounding comments and formatting are preserved. It still fails loudly
    when the ``from`` stage is genuinely absent — never a silent no-op that
    would mask a real divergence.

    Callers run this BEFORE any durable write so an unmatched stage line
    (e.g. a trailing inline comment) aborts the whole operation cleanly
    instead of stranding DB rows ahead of a failed YAML rewrite.
    """
    content = path.read_text(encoding="utf-8")
    # Anchor to a full stage line: optional indent, ``stage:``, optional
    # whitespace, the from-stage value optionally wrapped in matching single
    # or double quotes, optional trailing whitespace, to end-of-line.
    pattern = re.compile(
        rf'^(?P<indent>[ \t]*stage:[ \t]*)(?P<q>["\']?){re.escape(from_stage)}(?P=q)[ \t]*$',
        re.MULTILINE,
    )
    match = pattern.search(content)
    if match is None:
        msg = (
            f"Could not find a 'stage: {from_stage}' line in {path}. "
            "Refusing before any durable state is written — fix the stage "
            "line (e.g. remove a trailing inline comment) and retry."
        )
        raise ValueError(msg)
    quote = match.group("q")
    replacement = f"{match.group('indent')}{quote}{to_stage}{quote}"
    return content[: match.start()] + replacement + content[match.end() :]


def _write_stage_yaml(path: Path, updated_content: str) -> None:
    """Write content precomputed by :func:`_prepare_stage_yaml_update`.

    Called AFTER the durable commit; a failure here means the DB rows exist
    and the YAML is stale, so the error message names the drift-check
    backstop that will surface the discrepancy.
    """
    try:
        path.write_text(updated_content, encoding="utf-8")
    except OSError as exc:
        msg = (
            f"Failed to write the updated stage line to {path}: {exc}. "
            "Durable state is written, but the YAML could not be updated; "
            "the next cycle's drift check will flag this discrepancy."
        )
        raise ValueError(msg) from exc


def _update_stage_in_yaml(path: Path, from_stage: str, to_stage: str) -> None:
    """One-shot prepare + write of the ``stage:`` line rewrite.

    For callers with no durable write between match and write (e.g. the
    bench facade's idle→backtest stage normalization). ``transition`` and
    ``demote`` use the two phases separately so the match is validated
    before the event-store commit.
    """
    _write_stage_yaml(path, _prepare_stage_yaml_update(path, from_stage, to_stage))

"""Bench command facade — Phase B skeleton (propose-only).

This module is the single backend entry point for Bench-initiated lifecycle
commands per ADR 0051. Phase B establishes the proposal / validation contract.
It does not submit, does not mutate state, and does not import PySide6.

Submission is intentionally inert in this phase. Every ``submit_*`` method
returns a structured ``CommandResult`` whose only ``Blocker`` is
``reason_code="not_submit_capable_phase_b"``. Action-family wiring lands one
PR at a time in Phases C–F (demote → freeze/promote → backtest → runner).

Allowed dependencies:
- ``milodex.promotion`` (state machine, manifest, evidence)
- ``milodex.strategies.loader`` (config inspection)
- ``milodex.core.advisory_lock`` (peek-only)
- ``milodex.core.event_store`` (read-only types; no writes from this module)
- ``milodex.cli.commands.strategy`` (paper-mode / stage-compatibility table —
  the CLI is currently the source of truth; a future PR may graduate the
  table to a shared module without changing semantics)

Forbidden dependencies:
- ``PySide6`` and any QML construct
- ``milodex.broker.*`` direct calls
- ``milodex.strategies.runner`` construction
- ``milodex.execution.*`` write paths
- YAML mutation
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from milodex.cli.commands.strategy import _ALLOWED_STAGES_BY_MODE
from milodex.core.advisory_lock import AdvisoryLock
from milodex.promotion import (
    MAX_DRAWDOWN_PCT,
    MIN_SHARPE,
    MIN_TRADES,
    STAGE_ORDER,
)
from milodex.promotion.manifest import freeze_manifest as _governance_freeze_manifest
from milodex.promotion.state_machine import demote as _governance_demote
from milodex.strategies.loader import load_strategy_config

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore
    from milodex.strategies.loader import StrategyConfig


# Action family identifiers. Kept as plain strings (not an Enum) so they
# serialize transparently into JSON audit records and QVariant payloads.
ACTION_FAMILY_BACKTEST = "backtest"
ACTION_FAMILY_FREEZE_MANIFEST = "freeze_manifest"
ACTION_FAMILY_PROMOTE_TO_PAPER = "promote_to_paper"
ACTION_FAMILY_DEMOTE = "demote"
ACTION_FAMILY_START_PAPER_RUNNER = "start_paper_runner"
ACTION_FAMILY_STOP_PAPER_RUNNER = "stop_paper_runner"

ACTION_FAMILIES: tuple[str, ...] = (
    ACTION_FAMILY_BACKTEST,
    ACTION_FAMILY_FREEZE_MANIFEST,
    ACTION_FAMILY_PROMOTE_TO_PAPER,
    ACTION_FAMILY_DEMOTE,
    ACTION_FAMILY_START_PAPER_RUNNER,
    ACTION_FAMILY_STOP_PAPER_RUNNER,
)

# Demotion targets the existing ``promotion.state_machine.demote`` accepts.
DEMOTION_TARGETS: frozenset[str] = frozenset({"backtest", "disabled"})

# The Phase B sentinel blocker. Every submit_* returns this until the
# corresponding action-family wiring PR lands.
_NOT_SUBMIT_CAPABLE_PHASE_B = "not_submit_capable_phase_b"
_PHASE_B_MESSAGE = (
    "Bench command facade is in Phase B (propose-only). Submission for this "
    "action family is not yet wired; see ADR 0051 §10 (Phases C–F)."
)


@dataclass(frozen=True)
class Blocker:
    """Structured reason an action cannot proceed.

    ``reason_code`` is the stable, test-visible identifier; ``message`` is the
    operator-readable string the GUI renders; ``context`` carries any
    structured detail (gate failures, lock holder PID, stage values, …) that a
    later audit record or operator surface may want to format.
    """

    reason_code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Precondition:
    """Single precondition check the facade evaluated during a proposal.

    Mirrors the per-check pass/fail shape of R-CLI-007 so preview audit
    records can be constructed without re-deriving the check list.
    """

    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CommandProposal:
    """A proposed action, validated against current state but not committed.

    A proposal is *admissible* (``blockers == []``) or *blocked* (one or more
    ``Blocker`` records). The proposal is stateless from the GUI's
    perspective: it carries enough information to be re-validated at submit
    time. No lock is held, no event is reserved, no runner slot is allocated.

    ``proposal_id`` is a fresh UUID per call; it appears on the eventual
    audit record so a submit can be linked back to the preview that produced
    it (per OPERATIONS.md §"Audit Trail: Previews and Submits").
    """

    action_family: str
    strategy_id: str
    inputs: dict[str, Any]
    state_snapshot: dict[str, Any]
    preconditions: list[Precondition]
    projected_outcome: dict[str, Any]
    blockers: list[Blocker]
    proposed_at: datetime
    proposal_id: str

    @property
    def admissible(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_family": self.action_family,
            "strategy_id": self.strategy_id,
            "inputs": dict(self.inputs),
            "state_snapshot": dict(self.state_snapshot),
            "preconditions": [p.to_dict() for p in self.preconditions],
            "projected_outcome": dict(self.projected_outcome),
            "blockers": [b.to_dict() for b in self.blockers],
            "proposed_at": self.proposed_at.isoformat(),
            "proposal_id": self.proposal_id,
        }


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a submit attempt.

    Phase B always returns ``status="blocked"`` with the
    ``not_submit_capable_phase_b`` blocker. The shape is fixed now so the
    Phase C–F wiring PRs only fill in real values, never rename fields.
    """

    proposal_id: str
    action_family: str
    status: str
    durable_refs: dict[str, str] = field(default_factory=dict)
    blockers: list[Blocker] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    submitted_at: datetime | None = None
    audit_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "action_family": self.action_family,
            "status": self.status,
            "durable_refs": dict(self.durable_refs),
            "blockers": [b.to_dict() for b in self.blockers],
            "warnings": list(self.warnings),
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "audit_event_id": self.audit_event_id,
        }


class BenchCommandFacade:
    """Single Python entry point for Bench-initiated commands.

    Constructed once per process with the same factory-shaped dependencies
    the CLI's ``CommandContext`` already carries. Tests construct it directly
    with fakes.
    """

    def __init__(
        self,
        *,
        config_dir: Path,
        locks_dir: Path,
        get_trading_mode: Callable[[], str],
        event_store_factory: Callable[[], EventStore] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._locks_dir = Path(locks_dir)
        self._get_trading_mode = get_trading_mode
        self._event_store_factory = event_store_factory
        self._now = now or (lambda: datetime.now(tz=UTC))

    # ------------------------------------------------------------------ #
    # Proposal methods
    # ------------------------------------------------------------------ #

    def propose_backtest(
        self,
        strategy_id: str,
        *,
        start: date,
        end: date,
        walk_forward: bool = False,
        initial_equity: float = 100_000.0,
        slippage: float | None = None,
        run_id: str | None = None,
    ) -> CommandProposal:
        """Propose a backtest run.

        Backtests are safe-anytime per OPERATIONS.md and require no broker
        state. The only preconditions are: the strategy exists and the date
        range is well-formed.
        """
        inputs = {
            "start": start.isoformat() if isinstance(start, date) else start,
            "end": end.isoformat() if isinstance(end, date) else end,
            "walk_forward": bool(walk_forward),
            "initial_equity": float(initial_equity),
            "slippage": slippage,
            "run_id": run_id,
        }
        config, resolve_blocker = self._resolve_config(strategy_id)
        if resolve_blocker is not None:
            return self._blocked_proposal(
                ACTION_FAMILY_BACKTEST,
                strategy_id,
                inputs,
                state_snapshot={},
                preconditions=[
                    Precondition(
                        "strategy_exists",
                        passed=False,
                        detail=str(resolve_blocker.message),
                    ),
                ],
                projected_outcome={},
                blockers=[resolve_blocker],
            )

        preconditions = [
            Precondition("strategy_exists", passed=True, detail=f"resolved {config.path}"),
        ]
        blockers: list[Blocker] = []
        if isinstance(start, date) and isinstance(end, date) and end < start:
            blockers.append(
                Blocker(
                    reason_code="invalid_date_range",
                    message=(
                        f"Backtest end {end.isoformat()} must be on or after "
                        f"start {start.isoformat()}."
                    ),
                    context={"start": start.isoformat(), "end": end.isoformat()},
                )
            )
            preconditions.append(Precondition("date_range_ordered", passed=False))
        else:
            preconditions.append(Precondition("date_range_ordered", passed=True))

        state_snapshot = self._state_snapshot(config)
        projected_outcome = {
            "summary": (
                "Run a walk-forward backtest and record OOS-aggregate metrics."
                if walk_forward
                else "Run a single-period backtest and record whole-period metrics."
            ),
            "walk_forward": bool(walk_forward),
            "eventual_callee": (
                "milodex.backtesting.walk_forward_runner.run_walk_forward"
                if walk_forward
                else "milodex.backtesting.engine.BacktestEngine.run"
            ),
            "writes_durable_state": True,
            "evidence_produced": True,
        }
        return CommandProposal(
            action_family=ACTION_FAMILY_BACKTEST,
            strategy_id=strategy_id,
            inputs=inputs,
            state_snapshot=state_snapshot,
            preconditions=preconditions,
            projected_outcome=projected_outcome,
            blockers=blockers,
            proposed_at=self._now(),
            proposal_id=_new_proposal_id(),
        )

    def propose_freeze_manifest(
        self,
        strategy_id: str,
        *,
        frozen_by: str = "operator",
    ) -> CommandProposal:
        """Propose freezing the strategy YAML at its current promoted stage.

        Freeze is only valid for promoted stages (``paper``, ``micro_live``,
        ``live``) — matches ``milodex.promotion.manifest._FROZEN_STAGES``.
        A backtest-stage strategy has nothing to snapshot yet.

        ``frozen_by`` is carried on the proposal so ``submit_freeze_manifest``
        can re-pass it to the governance callee on dispatch. The CLI default
        of ``"operator"`` matches ``milodex.promotion.manifest.freeze_manifest``;
        the bridge resolves it backend-side via ``_resolve_operator_identity``
        (Phase C2 review F4 pattern).
        """
        inputs: dict[str, Any] = {"frozen_by": frozen_by}
        config, resolve_blocker = self._resolve_config(strategy_id)
        if resolve_blocker is not None:
            return self._blocked_proposal(
                ACTION_FAMILY_FREEZE_MANIFEST,
                strategy_id,
                inputs,
                state_snapshot={},
                preconditions=[
                    Precondition(
                        "strategy_exists",
                        passed=False,
                        detail=str(resolve_blocker.message),
                    ),
                ],
                projected_outcome={},
                blockers=[resolve_blocker],
            )

        preconditions = [
            Precondition("strategy_exists", passed=True, detail=f"resolved {config.path}"),
        ]
        blockers: list[Blocker] = []
        if config.stage not in _FROZEN_STAGES:
            blockers.append(
                Blocker(
                    reason_code="stage_not_freezable",
                    message=(
                        f"Cannot freeze strategy at stage '{config.stage}'. "
                        f"Freezing is only valid for promoted stages "
                        f"({', '.join(sorted(_FROZEN_STAGES))})."
                    ),
                    context={"stage": config.stage, "allowed_stages": sorted(_FROZEN_STAGES)},
                )
            )
            preconditions.append(Precondition("stage_is_freezable", passed=False))
        else:
            preconditions.append(Precondition("stage_is_freezable", passed=True))

        return CommandProposal(
            action_family=ACTION_FAMILY_FREEZE_MANIFEST,
            strategy_id=strategy_id,
            inputs=inputs,
            state_snapshot=self._state_snapshot(config),
            preconditions=preconditions,
            projected_outcome={
                "summary": (
                    f"Snapshot {config.path.name} at stage '{config.stage}' into the "
                    "event store as a new StrategyManifestEvent."
                ),
                "eventual_callee": "milodex.promotion.manifest.freeze_manifest",
                "writes_durable_state": True,
                "stage": config.stage,
            },
            blockers=blockers,
            proposed_at=self._now(),
            proposal_id=_new_proposal_id(),
        )

    def propose_promote_to_paper(
        self,
        strategy_id: str,
        *,
        recommendation: str | None = None,
        known_risks: list[str] | None = None,
        run_id: str | None = None,
        approved_by: str = "operator",
        lifecycle_exempt: bool = False,
    ) -> CommandProposal:
        """Propose advancing a strategy from ``backtest`` to ``paper``.

        Mirrors the inputs and refusals of ``cli/commands/promotion.py:_promote``
        but scoped to ``to_stage="paper"``. The facade exposes no route to
        ``micro_live`` or ``live`` — that's the ADR 0051 §6 / §7 boundary.
        """
        known_risks_list = list(known_risks) if known_risks else []
        inputs: dict[str, Any] = {
            "to_stage": "paper",
            "recommendation": recommendation,
            "known_risks": list(known_risks_list),
            "run_id": run_id,
            "approved_by": approved_by,
            "lifecycle_exempt": bool(lifecycle_exempt),
        }
        config, resolve_blocker = self._resolve_config(strategy_id)
        if resolve_blocker is not None:
            return self._blocked_proposal(
                ACTION_FAMILY_PROMOTE_TO_PAPER,
                strategy_id,
                inputs,
                state_snapshot={},
                preconditions=[
                    Precondition(
                        "strategy_exists",
                        passed=False,
                        detail=str(resolve_blocker.message),
                    ),
                ],
                projected_outcome={},
                blockers=[resolve_blocker],
            )

        preconditions: list[Precondition] = [
            Precondition("strategy_exists", passed=True, detail=f"resolved {config.path}"),
        ]
        blockers: list[Blocker] = []

        # Stage transition: backtest -> paper. validate_stage_transition would
        # raise on a wrong from_stage; we render the same constraint as a
        # structured blocker so the GUI can surface it without exception handling.
        if config.stage != "backtest":
            blockers.append(
                Blocker(
                    reason_code="wrong_source_stage",
                    message=(
                        f"Promotion to 'paper' requires from_stage 'backtest' but "
                        f"strategy is at '{config.stage}'."
                    ),
                    context={
                        "from_stage": config.stage,
                        "to_stage": "paper",
                        "expected_from_stage": "backtest",
                    },
                )
            )
            preconditions.append(Precondition("stage_is_backtest", passed=False))
        else:
            preconditions.append(Precondition("stage_is_backtest", passed=True))

        # Evidence inputs — same shape the CLI requires non-blank at submit.
        # Per R-PRM-008: a recommendation and at least one known risk.
        recommendation_ok = bool(recommendation and recommendation.strip())
        preconditions.append(
            Precondition("recommendation_present", passed=recommendation_ok)
        )
        risks_ok = any(r and r.strip() for r in known_risks_list)
        preconditions.append(Precondition("known_risks_present", passed=risks_ok))
        if not recommendation_ok:
            blockers.append(
                Blocker(
                    reason_code="missing_recommendation",
                    message=(
                        "Promotion to paper requires a non-blank --recommendation "
                        "(R-PRM-008)."
                    ),
                    context={},
                )
            )
        if not risks_ok:
            blockers.append(
                Blocker(
                    reason_code="missing_known_risks",
                    message=(
                        "Promotion to paper requires at least one non-blank --risk "
                        "entry (R-PRM-008)."
                    ),
                    context={},
                )
            )

        # Gate evidence: a run_id is needed to derive the statistical metrics
        # unless lifecycle_exempt. We surface this as a precondition; the gate
        # itself is evaluated at submit (Phase D) using the same check_gate
        # callee as the CLI.
        if not lifecycle_exempt and not run_id:
            blockers.append(
                Blocker(
                    reason_code="missing_run_id",
                    message=(
                        "Statistical promotion to paper requires --run-id pointing at "
                        f"a backtest with Sharpe > {MIN_SHARPE}, max drawdown < "
                        f"{MAX_DRAWDOWN_PCT}%, trades >= {MIN_TRADES}, or pass "
                        "lifecycle_exempt=True for regime-family strategies (R-PRM-004)."
                    ),
                    context={
                        "min_sharpe": MIN_SHARPE,
                        "max_drawdown_pct": MAX_DRAWDOWN_PCT,
                        "min_trades": MIN_TRADES,
                    },
                )
            )
            preconditions.append(Precondition("evidence_run_id_present", passed=False))
        else:
            preconditions.append(Precondition("evidence_run_id_present", passed=True))

        projected_outcome = {
            "summary": (
                f"Promote {strategy_id} from 'backtest' to 'paper'. Freezes the "
                "manifest, appends a promotion event with the evidence package, "
                "and updates the YAML stage line."
            ),
            "eventual_callees": [
                "milodex.promotion.validate_stage_transition",
                "milodex.promotion.check_gate",
                "milodex.promotion.assemble_evidence_package",
                "milodex.promotion.state_machine.transition",
            ],
            "writes_durable_state": True,
            "from_stage": config.stage,
            "to_stage": "paper",
            "promotion_type": "lifecycle_exempt" if lifecycle_exempt else "statistical",
        }
        return CommandProposal(
            action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
            strategy_id=strategy_id,
            inputs=inputs,
            state_snapshot=self._state_snapshot(config),
            preconditions=preconditions,
            projected_outcome=projected_outcome,
            blockers=blockers,
            proposed_at=self._now(),
            proposal_id=_new_proposal_id(),
        )

    def propose_demote(
        self,
        strategy_id: str,
        *,
        to_stage: str,
        reason: str | None = None,
        approved_by: str = "operator",
        evidence_ref: str | None = None,
        gui_submit: bool = False,
    ) -> CommandProposal:
        """Propose a demotion or walk-back.

        Targets are constrained to ``backtest`` or ``disabled`` — matches
        ``promotion.state_machine.demote`` and the CLI's demote choices.
        Demotion is always allowed at the governance layer, but the facade
        still requires a non-blank reason so the audit record is reconstructable.

        ``gui_submit=True`` activates the Bench-GUI-submit guardrail: while the
        CLI demote path is free to walk a strategy to ``disabled`` (ledger-only
        per ``promotion.state_machine`` slice 2), the GUI submit surface must
        not advertise that target until runtime refusal lands (slice 3). The
        bridge passes ``gui_submit=True``; CLI callers keep the default ``False``
        and retain the existing behaviour.
        """
        inputs: dict[str, Any] = {
            "to_stage": to_stage,
            "reason": reason,
            "approved_by": approved_by,
            "evidence_ref": evidence_ref,
        }
        config, resolve_blocker = self._resolve_config(strategy_id)
        if resolve_blocker is not None:
            return self._blocked_proposal(
                ACTION_FAMILY_DEMOTE,
                strategy_id,
                inputs,
                state_snapshot={},
                preconditions=[
                    Precondition(
                        "strategy_exists",
                        passed=False,
                        detail=str(resolve_blocker.message),
                    ),
                ],
                projected_outcome={},
                blockers=[resolve_blocker],
            )

        preconditions: list[Precondition] = [
            Precondition("strategy_exists", passed=True, detail=f"resolved {config.path}"),
        ]
        blockers: list[Blocker] = []

        if to_stage not in DEMOTION_TARGETS:
            blockers.append(
                Blocker(
                    reason_code="invalid_demotion_target",
                    message=(
                        f"Demotion target must be one of "
                        f"{sorted(DEMOTION_TARGETS)}; got '{to_stage}'."
                    ),
                    context={"to_stage": to_stage, "allowed": sorted(DEMOTION_TARGETS)},
                )
            )
            preconditions.append(Precondition("demotion_target_valid", passed=False))
        else:
            preconditions.append(Precondition("demotion_target_valid", passed=True))

        # GUI submit must not advertise a "disabled" target until runtime
        # refusal of disabled strategies lands (promotion.state_machine slice
        # 3). The CLI keeps allowing ledger-only disabled demotion (gui_submit
        # defaults to False). Pinned by
        # tests/milodex/commands/test_bench_facade.py::
        # test_propose_demote_to_disabled_refused_when_gui_submit_true.
        gui_disabled_ok = not (gui_submit and to_stage == "disabled")
        preconditions.append(
            Precondition("disabled_demote_gui_ready", passed=gui_disabled_ok)
        )
        if not gui_disabled_ok:
            blockers.append(
                Blocker(
                    reason_code="disabled_demote_not_gui_ready",
                    message=(
                        "Demotion to 'disabled' is currently ledger-only and "
                        "not yet safe to expose as a Bench GUI submit. Use the "
                        "CLI 'milodex promotion demote --to disabled' path "
                        "until runtime refusal of disabled strategies lands "
                        "(promotion.state_machine slice 3)."
                    ),
                    context={"strategy_id": strategy_id, "to_stage": to_stage},
                )
            )

        reason_ok = bool(reason and reason.strip())
        preconditions.append(Precondition("reason_present", passed=reason_ok))
        if not reason_ok:
            blockers.append(
                Blocker(
                    reason_code="missing_reason",
                    message="Demotion requires a non-blank --reason for the audit record.",
                    context={},
                )
            )

        # A demote from the current stage to itself is a no-op; refuse it so
        # the audit trail doesn't accumulate empty events.
        if to_stage == config.stage:
            blockers.append(
                Blocker(
                    reason_code="demotion_is_noop",
                    message=(
                        f"Strategy is already at stage '{config.stage}'; demoting to "
                        "the same stage would be a no-op."
                    ),
                    context={"current_stage": config.stage, "to_stage": to_stage},
                )
            )
            preconditions.append(Precondition("demotion_is_movement", passed=False))
        else:
            preconditions.append(Precondition("demotion_is_movement", passed=True))

        projected_outcome = {
            "summary": (
                f"Demote {strategy_id} from '{config.stage}' to '{to_stage}'. "
                "Appends a promotion event with promotion_type='demotion'."
            ),
            "eventual_callee": "milodex.promotion.state_machine.demote",
            "writes_durable_state": True,
            "from_stage": config.stage,
            "to_stage": to_stage,
            "yaml_updated": to_stage == "backtest",
        }
        return CommandProposal(
            action_family=ACTION_FAMILY_DEMOTE,
            strategy_id=strategy_id,
            inputs=inputs,
            state_snapshot=self._state_snapshot(config),
            preconditions=preconditions,
            projected_outcome=projected_outcome,
            blockers=blockers,
            proposed_at=self._now(),
            proposal_id=_new_proposal_id(),
        )

    def propose_start_paper_runner(self, strategy_id: str) -> CommandProposal:
        """Propose starting a foreground paper-trading session.

        Mirrors ``cli/commands/strategy.py:run`` — paper mode only, stage
        must equal the trading mode (``paper``), and the per-strategy
        advisory lock must not be currently held. Live and micro-live remain
        out of scope by construction; this method does not accept a
        ``trading_mode`` argument and refuses anything other than paper.
        """
        inputs: dict[str, Any] = {}
        config, resolve_blocker = self._resolve_config(strategy_id)
        if resolve_blocker is not None:
            return self._blocked_proposal(
                ACTION_FAMILY_START_PAPER_RUNNER,
                strategy_id,
                inputs,
                state_snapshot={},
                preconditions=[
                    Precondition(
                        "strategy_exists",
                        passed=False,
                        detail=str(resolve_blocker.message),
                    ),
                ],
                projected_outcome={},
                blockers=[resolve_blocker],
            )

        preconditions: list[Precondition] = [
            Precondition("strategy_exists", passed=True, detail=f"resolved {config.path}"),
        ]
        blockers: list[Blocker] = []

        trading_mode = self._get_trading_mode()
        if trading_mode != "paper":
            blockers.append(
                Blocker(
                    reason_code="trading_mode_not_paper",
                    message=(
                        f"Paper runner start is paper-only in Phase 1 (ADR 0004). "
                        f"Active trading mode is '{trading_mode}'."
                    ),
                    context={"trading_mode": trading_mode, "required": "paper"},
                )
            )
            preconditions.append(Precondition("trading_mode_paper", passed=False))
        else:
            preconditions.append(Precondition("trading_mode_paper", passed=True))

        allowed_stages = _ALLOWED_STAGES_BY_MODE.get("paper", frozenset({"paper"}))
        if config.stage not in allowed_stages:
            blockers.append(
                Blocker(
                    reason_code="stage_incompatible_with_mode",
                    message=(
                        f"Strategy '{strategy_id}' has stage='{config.stage}' but the "
                        f"active trading mode 'paper' requires stage(s): "
                        f"{', '.join(sorted(allowed_stages))}. Promote the strategy "
                        "to the correct stage before running it in this mode."
                    ),
                    context={
                        "stage": config.stage,
                        "trading_mode": "paper",
                        "allowed_stages": sorted(allowed_stages),
                    },
                )
            )
            preconditions.append(Precondition("stage_paper", passed=False))
        else:
            preconditions.append(Precondition("stage_paper", passed=True))

        lock_holder = self._peek_runner_lock(strategy_id)
        if lock_holder is not None:
            blockers.append(
                Blocker(
                    reason_code="advisory_lock_held",
                    message=(
                        f"Per-strategy runner lock for '{strategy_id}' is held by "
                        f"{lock_holder['holder_name']} (pid {lock_holder['pid']} on "
                        f"{lock_holder['hostname']}, started {lock_holder['started_at']}). "
                        "Stop the other process or wait for it to exit, then retry."
                    ),
                    context={"holder": lock_holder},
                )
            )
            preconditions.append(Precondition("advisory_lock_free", passed=False))
        else:
            preconditions.append(Precondition("advisory_lock_free", passed=True))

        projected_outcome = {
            "summary": (
                f"Start a foreground paper-trading session for {strategy_id}. "
                "Acquires the per-strategy advisory lock, runs the strategy under "
                "the existing risk-evaluator chokepoint, and writes a strategy_runs row."
            ),
            "eventual_callees": [
                "milodex.core.advisory_lock.AdvisoryLock",
                "milodex.strategies.runner.StrategyRunner.run",
            ],
            "writes_durable_state": True,
            "trading_mode": "paper",
        }
        return CommandProposal(
            action_family=ACTION_FAMILY_START_PAPER_RUNNER,
            strategy_id=strategy_id,
            inputs=inputs,
            state_snapshot=self._state_snapshot(config),
            preconditions=preconditions,
            projected_outcome=projected_outcome,
            blockers=blockers,
            proposed_at=self._now(),
            proposal_id=_new_proposal_id(),
        )

    def propose_stop_paper_runner(self, strategy_id: str) -> CommandProposal:
        """Propose a controlled stop of an active paper-trading session.

        Controlled-stop only — not the kill switch (ADR 0049 Decision 4,
        ADR 0012). If no runner is active (lock not held), the proposal is
        blocked: there is nothing to stop.
        """
        inputs: dict[str, Any] = {}
        config, resolve_blocker = self._resolve_config(strategy_id)
        if resolve_blocker is not None:
            return self._blocked_proposal(
                ACTION_FAMILY_STOP_PAPER_RUNNER,
                strategy_id,
                inputs,
                state_snapshot={},
                preconditions=[
                    Precondition(
                        "strategy_exists",
                        passed=False,
                        detail=str(resolve_blocker.message),
                    ),
                ],
                projected_outcome={},
                blockers=[resolve_blocker],
            )

        preconditions: list[Precondition] = [
            Precondition("strategy_exists", passed=True, detail=f"resolved {config.path}"),
        ]
        blockers: list[Blocker] = []

        lock_holder = self._peek_runner_lock(strategy_id)
        if lock_holder is None:
            blockers.append(
                Blocker(
                    reason_code="no_active_runner",
                    message=(
                        f"No active paper-runner session for '{strategy_id}'. "
                        "Controlled stop requires an active runner; nothing to stop."
                    ),
                    context={"lock_name": _runner_lock_name(strategy_id)},
                )
            )
            preconditions.append(Precondition("runner_active", passed=False))
        else:
            preconditions.append(Precondition("runner_active", passed=True))

        projected_outcome = {
            "summary": (
                f"Issue a controlled-stop request to the active runner for "
                f"{strategy_id}. Finishes the current cycle and closes the "
                "strategy_runs row cleanly. NOT the kill switch."
            ),
            "eventual_callee": (
                "milodex.strategies.runner.StrategyRunner.shutdown(mode=\"controlled\")"
            ),
            "writes_durable_state": True,
            "exit_reason": "controlled_stop",
            "kill_switch": False,
        }
        return CommandProposal(
            action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
            strategy_id=strategy_id,
            inputs=inputs,
            state_snapshot=self._state_snapshot(config),
            preconditions=preconditions,
            projected_outcome=projected_outcome,
            blockers=blockers,
            proposed_at=self._now(),
            proposal_id=_new_proposal_id(),
        )

    # ------------------------------------------------------------------ #
    # Submit methods — Phase B: all return the not_submit_capable_phase_b
    # blocker. Phase C–F replace each one with the real wiring.
    # ------------------------------------------------------------------ #

    def submit_backtest(self, proposal: CommandProposal) -> CommandResult:
        return self._phase_b_blocked(proposal, ACTION_FAMILY_BACKTEST)

    def submit_freeze_manifest(self, proposal: CommandProposal) -> CommandResult:
        """Second submit-capable action (ADR 0051 Phase D1).

        Re-validates the proposal against current state, then routes through
        ``milodex.promotion.manifest.freeze_manifest`` — the same callee the
        CLI's ``milodex promotion freeze`` uses. The governance path owns:

        * stage eligibility (``paper`` / ``micro_live`` / ``live`` only),
        * canonical YAML hashing via ``compute_config_hash``,
        * append-only ``StrategyManifestEvent`` write to the event store.

        The facade does not duplicate those rules; it surfaces refusals as
        ``Blocker`` records (mirroring ``submit_demote``) and returns durable
        manifest identifiers on success.
        """
        if proposal.action_family != ACTION_FAMILY_FREEZE_MANIFEST:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=proposal.action_family,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="proposal_action_family_mismatch",
                        message=(
                            f"submit_freeze_manifest received a proposal for "
                            f"'{proposal.action_family}'; expected "
                            f"'{ACTION_FAMILY_FREEZE_MANIFEST}'."
                        ),
                        context={
                            "expected": ACTION_FAMILY_FREEZE_MANIFEST,
                            "received": proposal.action_family,
                        },
                    )
                ],
            )

        event_store = self._require_event_store(proposal)
        if isinstance(event_store, CommandResult):
            return event_store

        frozen_by = str(proposal.inputs.get("frozen_by", "operator"))

        # Re-validate against the current world. A proposal that was
        # admissible at propose-time may have gone stale (stage changed,
        # YAML deleted, …) — refuse cleanly rather than dispatch.
        revalidation = self.propose_freeze_manifest(
            proposal.strategy_id,
            frozen_by=frozen_by,
        )
        if not revalidation.admissible:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_FREEZE_MANIFEST,
                status="blocked",
                blockers=list(revalidation.blockers),
            )

        config, resolve_blocker = self._resolve_config(proposal.strategy_id)
        if resolve_blocker is not None or config is None:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_FREEZE_MANIFEST,
                status="blocked",
                blockers=[resolve_blocker] if resolve_blocker is not None else [],
            )

        try:
            event = _governance_freeze_manifest(
                config.path,
                event_store=event_store,
                frozen_by=frozen_by,
                now=self._now(),
            )
        except ValueError as exc:
            # Governance layer raised. Surface as a structured blocker so
            # the exception does not cross the facade boundary.
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_FREEZE_MANIFEST,
                status="blocked",
                blockers=[
                    Blocker(
                        reason_code="governance_refused",
                        message=str(exc),
                        context={
                            "callee": "milodex.promotion.manifest.freeze_manifest",
                        },
                    )
                ],
            )

        durable_refs: dict[str, str] = {
            "strategy_id": event.strategy_id,
            "stage": event.stage,
            "config_hash": event.config_hash,
            "config_path": event.config_path,
            "frozen_by": event.frozen_by,
            "frozen_at": event.frozen_at.isoformat(),
        }
        if event.id is not None:
            durable_refs["manifest_event_id"] = str(event.id)

        return CommandResult(
            proposal_id=proposal.proposal_id,
            action_family=ACTION_FAMILY_FREEZE_MANIFEST,
            status="submitted",
            durable_refs=durable_refs,
            blockers=[],
            warnings=[],
            submitted_at=event.frozen_at,
            audit_event_id=str(event.id) if event.id is not None else None,
        )

    def submit_promote_to_paper(self, proposal: CommandProposal) -> CommandResult:
        return self._phase_b_blocked(proposal, ACTION_FAMILY_PROMOTE_TO_PAPER)

    def submit_demote(
        self, proposal: CommandProposal, *, gui_submit: bool = False
    ) -> CommandResult:
        """First submit-capable action (ADR 0051 Phase C1).

        Re-validates the proposal against current state, then routes through
        ``milodex.promotion.state_machine.demote`` — the same callee the CLI
        uses. The governance path owns:

        * append-only ``PromotionEvent`` with ``promotion_type='demotion'``
          and ``reverses_event_id`` chaining,
        * YAML stage-line update when ``to_stage='backtest'`` (ledger-only
          when ``to_stage='disabled'``),
        * non-blank reason refusal and target-set enforcement.

        The facade does not duplicate those rules; it surfaces refusals as
        ``Blocker`` records and returns durable identifiers on success.
        """
        if proposal.action_family != ACTION_FAMILY_DEMOTE:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=proposal.action_family,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="proposal_action_family_mismatch",
                        message=(
                            f"submit_demote received a proposal for "
                            f"'{proposal.action_family}'; expected "
                            f"'{ACTION_FAMILY_DEMOTE}'."
                        ),
                        context={
                            "expected": ACTION_FAMILY_DEMOTE,
                            "received": proposal.action_family,
                        },
                    )
                ],
            )

        event_store = self._require_event_store(proposal)
        if isinstance(event_store, CommandResult):
            return event_store

        # Re-validate against the current world. A proposal that was admissible
        # at propose-time may have gone stale (stage changed, evidence
        # invalidated, …) — refuse cleanly rather than dispatch.
        revalidation = self.propose_demote(
            proposal.strategy_id,
            to_stage=proposal.inputs.get("to_stage", ""),
            reason=proposal.inputs.get("reason"),
            approved_by=proposal.inputs.get("approved_by", "operator"),
            evidence_ref=proposal.inputs.get("evidence_ref"),
            gui_submit=gui_submit,
        )
        if not revalidation.admissible:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_DEMOTE,
                status="blocked",
                blockers=list(revalidation.blockers),
            )

        config, resolve_blocker = self._resolve_config(proposal.strategy_id)
        if resolve_blocker is not None or config is None:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_DEMOTE,
                status="blocked",
                blockers=[resolve_blocker] if resolve_blocker is not None else [],
            )

        to_stage = str(proposal.inputs["to_stage"])
        reason = str(proposal.inputs["reason"])
        approved_by = str(proposal.inputs.get("approved_by", "operator"))
        evidence_ref = proposal.inputs.get("evidence_ref")

        try:
            event = _governance_demote(
                config_path=config.path,
                to_stage=to_stage,
                reason=reason,
                approved_by=approved_by,
                event_store=event_store,
                evidence_ref=evidence_ref,
                now=self._now(),
            )
        except ValueError as exc:
            # The governance layer raised. Surface it as a structured blocker
            # rather than letting the exception cross the facade boundary.
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_DEMOTE,
                status="blocked",
                blockers=[
                    Blocker(
                        reason_code="governance_refused",
                        message=str(exc),
                        context={
                            "callee": "milodex.promotion.state_machine.demote",
                        },
                    )
                ],
            )

        durable_refs: dict[str, str] = {}
        if event.id is not None:
            durable_refs["promotion_id"] = str(event.id)
        if event.reverses_event_id is not None:
            durable_refs["reverses_event_id"] = str(event.reverses_event_id)
        durable_refs["strategy_id"] = event.strategy_id
        durable_refs["from_stage"] = event.from_stage
        durable_refs["to_stage"] = event.to_stage
        durable_refs["promotion_type"] = event.promotion_type

        warnings: list[str] = []
        if to_stage == "disabled":
            warnings.append(
                "Demotion to 'disabled' is ledger-only: the YAML stage line is "
                "unchanged. Runtime refusal of disabled strategies is a "
                "separate concern (promotion.state_machine slice 3)."
            )

        return CommandResult(
            proposal_id=proposal.proposal_id,
            action_family=ACTION_FAMILY_DEMOTE,
            status="submitted",
            durable_refs=durable_refs,
            blockers=[],
            warnings=warnings,
            submitted_at=event.recorded_at,
            audit_event_id=str(event.id) if event.id is not None else None,
        )

    def submit_start_paper_runner(self, proposal: CommandProposal) -> CommandResult:
        return self._phase_b_blocked(proposal, ACTION_FAMILY_START_PAPER_RUNNER)

    def submit_stop_paper_runner(self, proposal: CommandProposal) -> CommandResult:
        return self._phase_b_blocked(proposal, ACTION_FAMILY_STOP_PAPER_RUNNER)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _resolve_config(
        self, strategy_id: str
    ) -> tuple[StrategyConfig | None, Blocker | None]:
        """Locate the YAML for ``strategy_id`` under ``config_dir``.

        Returns ``(config, None)`` on success or ``(None, blocker)`` on
        failure. Read-only: no YAML mutation.
        """
        if not strategy_id or not strategy_id.strip():
            return None, Blocker(
                reason_code="strategy_id_blank",
                message="strategy_id must be a non-empty string.",
                context={},
            )
        for path in sorted(self._config_dir.glob("*.yaml")):
            try:
                config = load_strategy_config(path)
            except (ValueError, yaml.YAMLError):
                continue
            if config.strategy_id == strategy_id:
                return config, None
        return None, Blocker(
            reason_code="strategy_not_found",
            message=f"No strategy config found for strategy_id '{strategy_id}'.",
            context={"strategy_id": strategy_id, "config_dir": str(self._config_dir)},
        )

    def _state_snapshot(self, config: StrategyConfig) -> dict[str, Any]:
        """Compact, JSON-safe snapshot of the strategy state the facade saw."""
        return {
            "strategy_id": config.strategy_id,
            "family": config.family,
            "template": config.template,
            "stage": config.stage,
            "enabled": bool(config.enabled),
            "config_path": str(config.path),
        }

    def _require_event_store(
        self, proposal: CommandProposal
    ) -> EventStore | CommandResult:
        """Return the live event store or a structured blocker.

        Submit paths need a durable backend; without one we refuse cleanly
        instead of letting an ``AttributeError`` cross the facade boundary.
        """
        if self._event_store_factory is None:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=proposal.action_family,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="event_store_unavailable",
                        message=(
                            "BenchCommandFacade was constructed without an "
                            "event_store_factory; submit paths require one."
                        ),
                        context={},
                    )
                ],
            )
        return self._event_store_factory()

    def _peek_runner_lock(self, strategy_id: str) -> dict[str, Any] | None:
        """Peek the per-strategy runner advisory lock without acquiring it.

        Uses ``AdvisoryLock``'s read-holder protocol (the same one
        ``acquire()`` consults before raising ``AdvisoryLockError``). Returns
        the holder dict if held, ``None`` if free.
        """
        lock = AdvisoryLock(
            _runner_lock_name(strategy_id),
            locks_dir=self._locks_dir,
            holder_name="bench.facade.peek",
        )
        holder = lock._read_holder()  # noqa: SLF001 — single permitted peek
        if holder is None:
            return None
        return {
            "pid": holder.pid,
            "hostname": holder.hostname,
            "holder_name": holder.holder_name,
            "started_at": holder.started_at.isoformat(),
        }

    def _blocked_proposal(
        self,
        action_family: str,
        strategy_id: str,
        inputs: dict[str, Any],
        *,
        state_snapshot: dict[str, Any],
        preconditions: list[Precondition],
        projected_outcome: dict[str, Any],
        blockers: list[Blocker],
    ) -> CommandProposal:
        return CommandProposal(
            action_family=action_family,
            strategy_id=strategy_id,
            inputs=inputs,
            state_snapshot=state_snapshot,
            preconditions=preconditions,
            projected_outcome=projected_outcome,
            blockers=blockers,
            proposed_at=self._now(),
            proposal_id=_new_proposal_id(),
        )

    def _phase_b_blocked(
        self, proposal: CommandProposal, action_family: str
    ) -> CommandResult:
        return CommandResult(
            proposal_id=proposal.proposal_id,
            action_family=action_family,
            status="blocked",
            durable_refs={},
            blockers=[
                Blocker(
                    reason_code=_NOT_SUBMIT_CAPABLE_PHASE_B,
                    message=_PHASE_B_MESSAGE,
                    context={"phase": "B", "action_family": action_family},
                )
            ],
            warnings=[],
            submitted_at=None,
            audit_event_id=None,
        )


# Mirror milodex.promotion.manifest._FROZEN_STAGES locally so the facade has
# no dependency on a private name. Stages where freezing is meaningful.
_FROZEN_STAGES: frozenset[str] = frozenset({"paper", "micro_live", "live"})

# Sanity: the locally mirrored stage names must remain valid stages.
assert set(_FROZEN_STAGES).issubset(set(STAGE_ORDER)), (
    "_FROZEN_STAGES must be a subset of promotion.STAGE_ORDER."
)


def _runner_lock_name(strategy_id: str) -> str:
    """Per-strategy advisory-lock name (ADR 0026, strategy.py:120-124)."""
    return f"milodex.runtime.strategy.{strategy_id}"


def _new_proposal_id() -> str:
    return str(uuid.uuid4())

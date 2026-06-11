"""Bench command facade for proposed and submitted paper-lifecycle actions.

This module is the single backend entry point for Bench-initiated lifecycle
commands per ADR 0051. It owns the proposal / validation / submit contract
while remaining independent of PySide6 and QML.

Submit-capable action families route through existing backend seams for
promotion governance, backtest execution, event-store audit linkage, and
runner control. The retained ``not_submit_capable_phase_b`` blocker is a
legacy compatibility fallback for inert submit paths, not the current state
of the wired paper-lifecycle actions.

Allowed dependencies:
- ``milodex.promotion`` (state machine, manifest, evidence, run_evidence, stage_compat)
- ``milodex.backtesting.walk_forward_runner`` (derive_walk_forward_spans, run_walk_forward)
- ``milodex.strategies.loader`` (config inspection)
- ``milodex.core.advisory_lock`` (peek-only)
- ``milodex.core.event_store`` (event types and orchestration/audit linkage)

Forbidden dependencies:
- ``milodex.cli.*`` (facade must not reach into CLI internals)
- ``PySide6`` and any QML construct
- ``milodex.broker.*`` direct calls
- ``milodex.strategies.runner`` construction
- ``milodex.execution.*`` write paths
- YAML mutation outside existing governance callees
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from milodex.backtesting.walk_forward_runner import derive_walk_forward_spans, run_walk_forward
from milodex.core.advisory_lock import AdvisoryLock, live_lock_holder
from milodex.core.event_store import (
    OrchestrationBatchEvent,
    OrchestrationJobEvent,
    PromotionEvent,
)
from milodex.operations.reconciliation import latest_readiness
from milodex.promotion import (
    FROZEN_STAGES,
    MIN_TRADES,
    PAPER_MAX_DRAWDOWN_PCT,
    PAPER_MIN_SHARPE,
    REASON_GATE_FAILED,
    REASON_INVALID_STAGE_TRANSITION,
    REASON_MISSING_BACKTEST_RUN,
    PromoteBlocked,
    PromoteError,
    PromoteRequest,
    PromoteSuccess,
    prepare_and_record_promotion,
)
from milodex.promotion.manifest import freeze_manifest as _governance_freeze_manifest
from milodex.promotion.manifest import resolve_strategy_config_path
from milodex.promotion.stage_compat import ALLOWED_STAGES_BY_MODE
from milodex.promotion.state_machine import _update_stage_in_yaml as _governance_update_stage
from milodex.promotion.state_machine import demote as _governance_demote
from milodex.risk.policy import RiskPolicy
from milodex.strategies.loader import load_strategy_config
from milodex.strategies.paper_runner_control import (
    evaluation_symbol_for_config,
    live_runner_eval_symbols,
    runner_lock_name,
)

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore
    from milodex.strategies.loader import StrategyConfig

logger = logging.getLogger(__name__)


# Action family identifiers. Kept as plain strings (not an Enum) so they
# serialize transparently into JSON audit records and QVariant payloads.
ACTION_FAMILY_BACKTEST = "backtest"
ACTION_FAMILY_FREEZE_MANIFEST = "freeze_manifest"
ACTION_FAMILY_PROMOTE_TO_PAPER = "promote_to_paper"
ACTION_FAMILY_DEMOTE = "demote"
ACTION_FAMILY_START_PAPER_RUNNER = "start_paper_runner"
ACTION_FAMILY_STOP_PAPER_RUNNER = "stop_paper_runner"

READINESS_RECONCILIATION = "reconciliation"
READINESS_KILL_SWITCH = "kill_switch"
READINESS_DATA_FRESHNESS = "data_freshness"
READINESS_BROKER_REACHABILITY = "broker_reachability"

_WORKFLOW_REQUIRED_FULL: frozenset[str] = frozenset(
    {
        READINESS_RECONCILIATION,
        READINESS_KILL_SWITCH,
        READINESS_DATA_FRESHNESS,
        READINESS_BROKER_REACHABILITY,
    }
)
_WORKFLOW_REQUIRED_START_RUNNER: frozenset[str] = frozenset(
    {
        READINESS_RECONCILIATION,
        READINESS_KILL_SWITCH,
        READINESS_BROKER_REACHABILITY,
    }
)

ACTION_FAMILIES: tuple[str, ...] = (
    ACTION_FAMILY_BACKTEST,
    ACTION_FAMILY_FREEZE_MANIFEST,
    ACTION_FAMILY_PROMOTE_TO_PAPER,
    ACTION_FAMILY_DEMOTE,
    ACTION_FAMILY_START_PAPER_RUNNER,
    ACTION_FAMILY_STOP_PAPER_RUNNER,
)

# Demotion targets the existing ``promotion.state_machine.demote`` accepts.
DEMOTION_TARGETS: frozenset[str] = frozenset({"idle", "backtest", "disabled"})


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
class WorkflowReadinessIssue:
    """Structured workflow-readiness finding for a Bench action."""

    dimension: str
    reason_code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_blocker(self) -> Blocker:
        return Blocker(
            reason_code=self.reason_code,
            message=self.message,
            context={
                "dimension": self.dimension,
                "workflow_readiness": True,
                **dict(self.context),
            },
        )


@dataclass(frozen=True)
class WorkflowReadinessReport:
    """Workflow-readiness verdict supplied to the Bench facade."""

    issues: tuple[WorkflowReadinessIssue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"issues": [issue.to_dict() for issue in self.issues]}


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

    Wired submit paths return durable refs, blockers, warnings, and audit
    linkage through this stable shape. Inert legacy paths may still return
    ``not_submit_capable_phase_b`` without renaming fields.
    """

    proposal_id: str
    action_family: str
    status: str
    durable_refs: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
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
            "data": dict(self.data),
            "blockers": [b.to_dict() for b in self.blockers],
            "warnings": list(self.warnings),
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "audit_event_id": self.audit_event_id,
        }


# Freshness threshold shared with the CLI trust report (_data_freshness in
# report.py).  A bar older than this is considered stale.
_DATA_FRESHNESS_STALE_HOURS = 24.0


class _DefaultWorkflowReadiness:
    """Fail-closed production fallback for workflow-readiness verdicts.

    One ``EventStore`` is constructed per :meth:`evaluate` call and shared
    across all per-dimension helpers (G-P3-5 — avoids WAL/migration setup
    overhead multiplied by dimension count on every GUI propose path).
    """

    def __init__(self, event_store_factory: Callable[[], EventStore] | None) -> None:
        self._event_store_factory = event_store_factory

    def evaluate(
        self,
        *,
        action_family: str,
        strategy_id: str,
        required_checks: frozenset[str],
        inspected_checks: frozenset[str],
    ) -> WorkflowReadinessReport:
        # Construct one EventStore for the entire evaluate call (G-P3-5).
        event_store: EventStore | None = (
            self._event_store_factory() if self._event_store_factory is not None else None
        )
        issues: list[WorkflowReadinessIssue] = []
        for dimension in sorted(required_checks | inspected_checks):
            blocking = dimension in required_checks
            if dimension == READINESS_KILL_SWITCH:
                issue = self._kill_switch_issue(event_store=event_store, blocking=blocking)
                if issue is not None:
                    issues.append(issue)
                continue
            if dimension == READINESS_RECONCILIATION:
                issue = self._reconciliation_issue(
                    event_store=event_store,
                    action_family=action_family,
                    strategy_id=strategy_id,
                    blocking=blocking,
                )
                if issue is not None:
                    issues.append(issue)
                continue
            if dimension == READINESS_DATA_FRESHNESS:
                issue = self._data_freshness_issue(
                    event_store=event_store,
                    action_family=action_family,
                    strategy_id=strategy_id,
                    blocking=blocking,
                )
                if issue is not None:
                    issues.append(issue)
                continue
            if dimension == READINESS_BROKER_REACHABILITY:
                issue = self._broker_reachability_issue(
                    event_store=event_store,
                    action_family=action_family,
                    strategy_id=strategy_id,
                    blocking=blocking,
                )
                if issue is not None:
                    issues.append(issue)
        return WorkflowReadinessReport(issues=tuple(issues))

    def _reconciliation_issue(
        self,
        *,
        event_store: EventStore | None,
        action_family: str,
        strategy_id: str,
        blocking: bool,
    ) -> WorkflowReadinessIssue | None:
        if event_store is None:
            return WorkflowReadinessIssue(
                dimension=READINESS_RECONCILIATION,
                reason_code="reconciliation_required",
                message=(
                    "Workflow readiness cannot read durable reconciliation state; "
                    "submit-capable workflow actions fail closed."
                ),
                context={"action_family": action_family, "strategy_id": strategy_id},
                blocking=blocking,
            )
        readiness = latest_readiness(event_store)
        if readiness.ready:
            return None
        return WorkflowReadinessIssue(
            dimension=READINESS_RECONCILIATION,
            reason_code=readiness.reason_code or "reconciliation_required",
            message=readiness.message,
            context={
                "action_family": action_family,
                "strategy_id": strategy_id,
                **dict(readiness.context),
            },
            blocking=blocking,
        )

    def _broker_reachability_issue(
        self,
        *,
        event_store: EventStore | None,
        action_family: str,
        strategy_id: str,
        blocking: bool,
    ) -> WorkflowReadinessIssue | None:
        if event_store is None:
            return WorkflowReadinessIssue(
                dimension=READINESS_BROKER_REACHABILITY,
                reason_code="broker_unreachable",
                message=(
                    "Workflow readiness cannot read durable reconciliation state; "
                    "broker reachability cannot be proven."
                ),
                context={"action_family": action_family, "strategy_id": strategy_id},
                blocking=blocking,
            )
        readiness = latest_readiness(event_store)
        if readiness.ready and readiness.broker_connected:
            return None
        return WorkflowReadinessIssue(
            dimension=READINESS_BROKER_REACHABILITY,
            reason_code="broker_unreachable",
            message=(
                "Workflow readiness cannot prove current-day broker reachability; "
                "run a clean reconciliation before starting a paper runner."
            ),
            context={
                "action_family": action_family,
                "strategy_id": strategy_id,
                "reconciliation_reason_code": readiness.reason_code,
                **dict(readiness.context),
            },
            blocking=blocking,
        )

    def _kill_switch_issue(
        self, *, event_store: EventStore | None, blocking: bool
    ) -> WorkflowReadinessIssue | None:
        if event_store is None:
            return WorkflowReadinessIssue(
                dimension=READINESS_KILL_SWITCH,
                reason_code="kill_switch_open",
                message=(
                    "Workflow readiness cannot verify that the kill switch is inactive; "
                    "submit-capable workflow actions fail closed."
                ),
                context={},
                blocking=blocking,
            )
        event = event_store.get_latest_kill_switch_event()
        if event is None or event.event_type == "reset":
            return None
        return WorkflowReadinessIssue(
            dimension=READINESS_KILL_SWITCH,
            reason_code="kill_switch_open",
            message="Kill switch is active; resolve and manually reset it before submitting.",
            context={
                "reason": event.reason,
                "last_triggered_at": event.recorded_at.isoformat(),
            },
            blocking=blocking,
        )

    def _data_freshness_issue(
        self,
        *,
        event_store: EventStore | None,
        action_family: str,
        strategy_id: str,
        blocking: bool,
    ) -> WorkflowReadinessIssue | None:
        """Return a data-freshness issue, or ``None`` if data is fresh enough.

        Threshold: ``_DATA_FRESHNESS_STALE_HOURS`` (24 h), matching the CLI
        trust report's ``_data_freshness`` helper in ``report.py``.  Both call
        ``EventStore.get_latest_bar_timestamp()`` — a bounded single-row query
        that is safe on the GUI propose path.

        Fail-closed cases (return a blocking issue):
        - ``event_store`` is ``None`` (no factory configured)
        - the event store has no explanation rows with a bar timestamp
        - the latest bar timestamp is older than the threshold
        """
        if event_store is None:
            return WorkflowReadinessIssue(
                dimension=READINESS_DATA_FRESHNESS,
                reason_code="data_stale",
                message=(
                    "Workflow readiness cannot read the event store; "
                    "data freshness cannot be proven."
                ),
                context={"action_family": action_family, "strategy_id": strategy_id},
                blocking=blocking,
            )
        latest_bar_ts = event_store.get_latest_bar_timestamp()
        if latest_bar_ts is None:
            return WorkflowReadinessIssue(
                dimension=READINESS_DATA_FRESHNESS,
                reason_code="data_stale",
                message=(
                    "No bar data found in the event store; "
                    "run at least one strategy evaluation before promoting."
                ),
                context={"action_family": action_family, "strategy_id": strategy_id},
                blocking=blocking,
            )
        age_hours = (
            datetime.now(tz=UTC) - latest_bar_ts.replace(tzinfo=UTC)
            if latest_bar_ts.tzinfo is None
            else datetime.now(tz=UTC) - latest_bar_ts
        ).total_seconds() / 3600.0
        if age_hours > _DATA_FRESHNESS_STALE_HOURS:
            return WorkflowReadinessIssue(
                dimension=READINESS_DATA_FRESHNESS,
                reason_code="data_stale",
                message=(
                    f"Market data is stale: latest bar is {age_hours:.1f} h old "
                    f"(threshold {_DATA_FRESHNESS_STALE_HOURS:.0f} h). "
                    "Verify the runner fleet is healthy and data is flowing."
                ),
                context={
                    "action_family": action_family,
                    "strategy_id": strategy_id,
                    "latest_bar_timestamp": latest_bar_ts.isoformat(),
                    "age_hours": round(age_hours, 2),
                    "threshold_hours": _DATA_FRESHNESS_STALE_HOURS,
                },
                blocking=blocking,
            )
        return None


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
        backtest_engine_factory: Callable[..., Any] | None = None,
        paper_runner_control: Any | None = None,
        workflow_readiness: Any | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config_dir = Path(config_dir)
        self._locks_dir = Path(locks_dir)
        self._get_trading_mode = get_trading_mode
        self._event_store_factory = event_store_factory
        self._backtest_engine_factory = backtest_engine_factory
        self._paper_runner_control = paper_runner_control
        self._workflow_readiness = workflow_readiness or _DefaultWorkflowReadiness(
            event_store_factory
        )
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
        risk_policy: str = RiskPolicy.BYPASS.value,
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
            "risk_policy": risk_policy,
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
        ``live``) — matches ``milodex.promotion.FROZEN_STAGES``.
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
        if config.stage not in FROZEN_STAGES:
            blockers.append(
                Blocker(
                    reason_code="stage_not_freezable",
                    message=(
                        f"Cannot freeze strategy at stage '{config.stage}'. "
                        f"Freezing is only valid for promoted stages "
                        f"({', '.join(sorted(FROZEN_STAGES))})."
                    ),
                    context={"stage": config.stage, "allowed_stages": sorted(FROZEN_STAGES)},
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
        preconditions.append(Precondition("recommendation_present", passed=recommendation_ok))
        risks_ok = any(r and r.strip() for r in known_risks_list)
        preconditions.append(Precondition("known_risks_present", passed=risks_ok))
        if not recommendation_ok:
            blockers.append(
                Blocker(
                    reason_code="missing_recommendation",
                    message=(
                        "Promotion to paper requires a non-blank --recommendation (R-PRM-008)."
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

        min_trades_required = int(config.backtest.get("min_trades_required", MIN_TRADES))

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
                        f"a backtest with Sharpe > {PAPER_MIN_SHARPE}, max drawdown < "
                        f"{PAPER_MAX_DRAWDOWN_PCT}%, trades >= {min_trades_required}, or pass "
                        "lifecycle_exempt=True for regime-family strategies (R-PRM-004)."
                    ),
                    context={
                        "min_sharpe": PAPER_MIN_SHARPE,
                        "max_drawdown_pct": PAPER_MAX_DRAWDOWN_PCT,
                        "min_trades": min_trades_required,
                    },
                )
            )
            preconditions.append(Precondition("evidence_run_id_present", passed=False))
        else:
            preconditions.append(Precondition("evidence_run_id_present", passed=True))

        readiness_report, readiness_blockers, readiness_preconditions = (
            self._evaluate_workflow_readiness(
                action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
                strategy_id=strategy_id,
                required_checks=_WORKFLOW_REQUIRED_FULL,
            )
        )
        blockers.extend(readiness_blockers)
        preconditions.extend(readiness_preconditions)

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
        projected_outcome = self._attach_workflow_readiness(
            projected_outcome,
            readiness_report,
        )
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

        Targets are constrained to ``idle``, ``backtest`` or ``disabled`` — matches
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
        preconditions.append(Precondition("disabled_demote_gui_ready", passed=gui_disabled_ok))
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

        active_runner_holder = self._peek_runner_lock(strategy_id)
        readiness_report = WorkflowReadinessReport()
        if active_runner_holder is not None:
            readiness_report, readiness_blockers, readiness_preconditions = (
                self._evaluate_workflow_readiness(
                    action_family=ACTION_FAMILY_DEMOTE,
                    strategy_id=strategy_id,
                    required_checks=frozenset({READINESS_RECONCILIATION, READINESS_KILL_SWITCH}),
                    inspected_checks=frozenset(
                        {READINESS_DATA_FRESHNESS, READINESS_BROKER_REACHABILITY}
                    ),
                )
            )
            blockers.extend(readiness_blockers)
            preconditions.extend(readiness_preconditions)

        projected_outcome = {
            "summary": (
                f"Demote {strategy_id} from '{config.stage}' to '{to_stage}'. "
                "Appends a promotion event with promotion_type='demotion'."
            ),
            "eventual_callee": "milodex.promotion.state_machine.demote",
            "writes_durable_state": True,
            "from_stage": config.stage,
            "to_stage": to_stage,
            "yaml_updated": to_stage in {"idle", "backtest"},
        }
        projected_outcome = self._attach_workflow_readiness(
            projected_outcome,
            readiness_report,
        )
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

        allowed_stages = ALLOWED_STAGES_BY_MODE.get("paper", frozenset({"paper"}))
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

        collision_blocker = self._peek_eval_symbol_collision(strategy_id, config)
        if collision_blocker is not None:
            blockers.append(collision_blocker)
            preconditions.append(Precondition("evaluation_symbol_free", passed=False))
        else:
            preconditions.append(Precondition("evaluation_symbol_free", passed=True))

        readiness_report, readiness_blockers, readiness_preconditions = (
            self._evaluate_workflow_readiness(
                action_family=ACTION_FAMILY_START_PAPER_RUNNER,
                strategy_id=strategy_id,
                required_checks=_WORKFLOW_REQUIRED_START_RUNNER,
            )
        )
        blockers.extend(readiness_blockers)
        preconditions.extend(readiness_preconditions)

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
        projected_outcome = self._attach_workflow_readiness(
            projected_outcome,
            readiness_report,
        )
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
                    context={"lock_name": runner_lock_name(strategy_id)},
                )
            )
            preconditions.append(Precondition("runner_active", passed=False))
        else:
            preconditions.append(Precondition("runner_active", passed=True))

        readiness_report = WorkflowReadinessReport()
        if lock_holder is not None:
            readiness_report, readiness_blockers, readiness_preconditions = (
                self._evaluate_workflow_readiness(
                    action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
                    strategy_id=strategy_id,
                    required_checks=frozenset(),
                    # READINESS_KILL_SWITCH is inspected, not required: a controlled stop
                    # writes a request file and closes a session — it submits no trades.
                    # The risk layer independently blocks anything a still-running runner
                    # attempts. Gating a de-risking action on the kill switch being inactive
                    # inverts the asymmetry principle and wedges the GUI during a safety event.
                    inspected_checks=frozenset(
                        {
                            READINESS_KILL_SWITCH,
                            READINESS_RECONCILIATION,
                            READINESS_DATA_FRESHNESS,
                            READINESS_BROKER_REACHABILITY,
                        }
                    ),
                )
            )
            blockers.extend(readiness_blockers)
            preconditions.extend(readiness_preconditions)

        projected_outcome = {
            "summary": (
                f"Issue a controlled-stop request to the active runner for "
                f"{strategy_id}. Finishes the current cycle and closes the "
                "strategy_runs row cleanly. NOT the kill switch."
            ),
            "eventual_callee": (
                'milodex.strategies.runner.StrategyRunner.shutdown(mode="controlled")'
            ),
            "writes_durable_state": True,
            "exit_reason": "controlled_stop",
            "kill_switch": False,
        }
        projected_outcome = self._attach_workflow_readiness(
            projected_outcome,
            readiness_report,
        )
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
    # Submit methods — wired action families route through existing backend
    # callees and return durable refs or structured blockers.
    # ------------------------------------------------------------------ #

    def submit_backtest(self, proposal: CommandProposal) -> CommandResult:
        """Run a Bench-requested backtest and return durable evidence refs."""
        if proposal.action_family != ACTION_FAMILY_BACKTEST:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=proposal.action_family,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="proposal_action_family_mismatch",
                        message=(
                            f"submit_backtest received a proposal for "
                            f"'{proposal.action_family}'; expected "
                            f"'{ACTION_FAMILY_BACKTEST}'."
                        ),
                        context={
                            "expected": ACTION_FAMILY_BACKTEST,
                            "received": proposal.action_family,
                        },
                    )
                ],
            )

        required_store = self._require_event_store(proposal)
        if isinstance(required_store, CommandResult):
            return required_store

        if self._backtest_engine_factory is None:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_BACKTEST,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="backtest_engine_unavailable",
                        message=(
                            "BenchCommandFacade was constructed without a "
                            "backtest_engine_factory; backtest submits require one."
                        ),
                        context={},
                    )
                ],
            )

        try:
            start = date.fromisoformat(str(proposal.inputs.get("start")))
            end = date.fromisoformat(str(proposal.inputs.get("end")))
            walk_forward = bool(proposal.inputs.get("walk_forward", False))
            initial_equity = float(proposal.inputs.get("initial_equity", 100_000.0))
            slippage_raw = proposal.inputs.get("slippage")
            slippage = float(slippage_raw) if slippage_raw is not None else None
            run_id_raw = proposal.inputs.get("run_id")
            run_id = str(run_id_raw) if run_id_raw else None
        except (TypeError, ValueError) as exc:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_BACKTEST,
                status="blocked",
                blockers=[
                    Blocker(
                        reason_code="invalid_backtest_input",
                        message=f"Backtest proposal inputs are invalid: {exc}",
                        context={"inputs": dict(proposal.inputs)},
                    )
                ],
            )

        revalidation = self.propose_backtest(
            proposal.strategy_id,
            start=start,
            end=end,
            walk_forward=walk_forward,
            initial_equity=initial_equity,
            slippage=slippage,
            run_id=run_id,
            risk_policy=RiskPolicy.BYPASS.value,
        )
        if not revalidation.admissible:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_BACKTEST,
                status="blocked",
                blockers=list(revalidation.blockers),
            )

        engine_kwargs: dict[str, Any] = {
            "initial_equity": initial_equity,
            "risk_policy": RiskPolicy.BYPASS,
        }
        if slippage is not None:
            engine_kwargs["slippage_pct"] = slippage

        action_type = "backtest_walk_forward" if walk_forward else "backtest_single"
        job_ref = self._create_orchestration_job(
            proposal,
            action_type=action_type,
            requested_stage="backtest",
            progress_label="running backtest",
        )

        try:
            engine = self._backtest_engine_factory(proposal.strategy_id, **engine_kwargs)
            if walk_forward:
                all_bars, train_days, test_days, step_days = derive_walk_forward_spans(
                    engine, start, end
                )
                result = run_walk_forward(
                    engine,
                    start_date=start,
                    end_date=end,
                    train_days=train_days,
                    test_days=test_days,
                    step_days=step_days,
                    initial_equity=initial_equity,
                    run_id=run_id,
                    all_bars=all_bars,
                )
            else:
                result = engine.run(start, end, run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - facade returns structured submit failures.
            error_result = CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_BACKTEST,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="backtest_failed",
                        message=f"Backtest submit failed: {exc}",
                        context={"error_type": exc.__class__.__name__},
                    )
                ],
                submitted_at=self._now(),
            )
            return self._finish_orchestration_job(job_ref, error_result)

        durable_refs = self._backtest_durable_refs(result, walk_forward=walk_forward)
        from_stage = str(revalidation.state_snapshot.get("stage") or "")
        if from_stage == "idle":
            config_path = Path(str(revalidation.state_snapshot.get("config_path") or ""))
            try:
                _governance_update_stage(config_path, "idle", "backtest")
            except ValueError as exc:
                error_result = CommandResult(
                    proposal_id=proposal.proposal_id,
                    action_family=ACTION_FAMILY_BACKTEST,
                    status="error",
                    durable_refs=durable_refs,
                    blockers=[
                        Blocker(
                            reason_code="idle_to_backtest_stage_update_failed",
                            message=str(exc),
                            context={"config_path": str(config_path)},
                        )
                    ],
                    submitted_at=self._now(),
                )
                return self._finish_orchestration_job(job_ref, error_result)
            stage_return_id = required_store.append_promotion(
                PromotionEvent(
                    strategy_id=proposal.strategy_id,
                    from_stage="idle",
                    to_stage="backtest",
                    promotion_type="stage_return",
                    approved_by="bench_gui",
                    recorded_at=self._now(),
                    backtest_run_id=str(result.run_id),
                    notes="Initiate Backtest via Bench GUI",
                    evidence_json={
                        "proposal_id": proposal.proposal_id,
                        "action_family": proposal.action_family,
                        "run_id": str(result.run_id),
                    },
                )
            )
            durable_refs["from_stage"] = "idle"
            durable_refs["to_stage"] = "backtest"
            durable_refs["stage_return_promotion_id"] = str(stage_return_id)

        submit_result = CommandResult(
            proposal_id=proposal.proposal_id,
            action_family=ACTION_FAMILY_BACKTEST,
            status="submitted",
            durable_refs=durable_refs,
            data=self._backtest_result_data(result, walk_forward=walk_forward),
            submitted_at=self._now(),
            audit_event_id=str(result.run_id),
        )
        return self._finish_orchestration_job(job_ref, submit_result)

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
        frozen_by = str(proposal.inputs.get("frozen_by", "operator"))

        def _dispatch(
            proposal: CommandProposal,
            revalidation: CommandProposal,  # noqa: ARG001 - revalidation has no post-dispatch use here
            config: StrategyConfig,
            event_store: EventStore,
        ) -> CommandResult:
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

        return self._submit_with_config(
            proposal,
            expected_action_family=ACTION_FAMILY_FREEZE_MANIFEST,
            caller_method="submit_freeze_manifest",
            revalidate=lambda: self.propose_freeze_manifest(
                proposal.strategy_id,
                frozen_by=frozen_by,
            ),
            dispatch=_dispatch,
        )

    def submit_promote_to_paper(self, proposal: CommandProposal) -> CommandResult:
        """Third submit-capable action (ADR 0051 Phase D2).

        Routes through ``milodex.promotion.prepare_and_record_promotion`` (the
        RM-010 shared paper-promotion orchestrator) — the same entrypoint the
        CLI's ``milodex promotion promote --to paper`` uses. The orchestrator
        owns the full governance choreography: stage-transition validation,
        OOS metrics resolution, gate evaluation, manifest hash derivation,
        evidence assembly, and the atomic ``transition()`` that appends
        manifest + promotion events in one event-store transaction and
        rewrites the YAML ``stage:`` line in-place.

        The facade keeps three responsibilities the orchestrator deliberately
        does not own: (1) stale-proposal revalidation by re-running
        ``propose_promote_to_paper`` to detect drift since propose-time,
        (2) translation of ``PromoteBlocked``/``PromoteError`` into
        Bench-shaped ``Blocker`` records (CLI and Bench have distinct
        operator-visible reason-code namespaces), and (3) packaging the
        ``durable_refs`` payload the GUI bench bridge consumes. Operator
        identity and evidence text are supplied by the bridge and threaded
        through unchanged.
        """
        # Extract operator-supplied inputs. These are the same fields the CLI's
        # `--recommendation` / `--risk` / `--run-id` / `--lifecycle-exempt` /
        # `--approved-by` flags populate. The RAW shapes (None-able
        # `recommendation`, the un-normalized `known_risks_raw`, the un-cast
        # `run_id`) are what propose_promote_to_paper expects for revalidation
        # — do NOT normalize before passing into the revalidate closure.
        # PromoteRequest dispatch DOES normalize (str(recommendation) if … else
        # ""), but revalidation must mirror the propose-side input shape.
        recommendation = proposal.inputs.get("recommendation")
        known_risks_raw = proposal.inputs.get("known_risks") or []
        known_risks = [str(r) for r in known_risks_raw if r and str(r).strip()]
        run_id = proposal.inputs.get("run_id")
        approved_by = str(proposal.inputs.get("approved_by", "operator"))
        lifecycle_exempt = bool(proposal.inputs.get("lifecycle_exempt", False))

        def _dispatch(
            proposal: CommandProposal,
            revalidation: CommandProposal,  # noqa: ARG001 - no post-dispatch use here
            config: StrategyConfig,
            event_store: EventStore,
        ) -> CommandResult:
            from_stage = config.stage
            to_stage = "paper"

            # Delegate the choreography (validate_stage_transition →
            # metrics_from_run → check_gate → compute_post_update_hash →
            # assemble_evidence_package → transition) to the shared promotion
            # orchestrator (RM-010). The Bench facade keeps proposal/submit
            # lifecycle, workflow-readiness blockers, stale-proposal
            # revalidation (in the shell), and Blocker translation; the
            # orchestrator owns governance choreography. CLI and Bench cannot
            # drift on gate behavior or evidence shape because both go
            # through this entrypoint.
            request = PromoteRequest(
                strategy_id=config.strategy_id,
                config_path=config.path,
                to_stage=to_stage,
                recommendation=str(recommendation) if recommendation else "",
                known_risks=known_risks,
                approved_by=approved_by,
                run_id=run_id,
                lifecycle_exempt=lifecycle_exempt,
                now=self._now(),
            )
            result = prepare_and_record_promotion(request, event_store)

            if isinstance(result, PromoteBlocked):
                return CommandResult(
                    proposal_id=proposal.proposal_id,
                    action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
                    status="blocked",
                    blockers=_blockers_from_promote_blocked(
                        result, from_stage=from_stage, to_stage=to_stage, run_id=run_id
                    ),
                )
            if isinstance(result, PromoteError):
                return CommandResult(
                    proposal_id=proposal.proposal_id,
                    action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
                    status="blocked",
                    blockers=[
                        Blocker(
                            reason_code="governance_refused",
                            message=result.message,
                            context=dict(result.context),
                        )
                    ],
                )

            assert isinstance(result, PromoteSuccess)
            durable_refs: dict[str, str] = {
                "strategy_id": result.strategy_id,
                "from_stage": result.from_stage,
                "to_stage": result.to_stage,
                "promotion_type": result.promotion_type,
                "approved_by": approved_by,
                "recorded_at": result.recorded_at.isoformat(),
                "manifest_hash": result.manifest_hash,
            }
            if result.promotion_id is not None:
                durable_refs["promotion_id"] = str(result.promotion_id)
            if result.manifest_id is not None:
                durable_refs["manifest_id"] = str(result.manifest_id)
            if result.backtest_run_id is not None:
                durable_refs["backtest_run_id"] = result.backtest_run_id
            if result.sharpe_ratio is not None:
                durable_refs["sharpe_ratio"] = f"{result.sharpe_ratio:.6f}"
            if result.max_drawdown_pct is not None:
                durable_refs["max_drawdown_pct"] = f"{result.max_drawdown_pct:.6f}"
            if result.trade_count is not None:
                durable_refs["trade_count"] = str(result.trade_count)

            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
                status="submitted",
                durable_refs=durable_refs,
                blockers=[],
                warnings=[],
                submitted_at=result.recorded_at,
                audit_event_id=str(result.promotion_id)
                if result.promotion_id is not None
                else None,
            )

        # H6 / v2-review-b: literal-copy ALL FIVE revalidation kwargs. The raw
        # shapes (recommendation can be None; known_risks_raw is un-normalized;
        # run_id is un-cast) must match what propose_promote_to_paper accepts.
        # Do NOT normalize here — propose_promote_to_paper handles validation
        # internally, and changing the kwarg shapes would silently shift
        # revalidation outcomes.
        return self._submit_with_config(
            proposal,
            expected_action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
            caller_method="submit_promote_to_paper",
            revalidate=lambda: self.propose_promote_to_paper(
                proposal.strategy_id,
                recommendation=recommendation,
                known_risks=list(known_risks_raw),
                run_id=run_id,
                approved_by=approved_by,
                lifecycle_exempt=lifecycle_exempt,
            ),
            dispatch=_dispatch,
        )

    def submit_demote(
        self, proposal: CommandProposal, *, gui_submit: bool = False
    ) -> CommandResult:
        """First submit-capable action (ADR 0051 Phase C1).

        Re-validates the proposal against current state, then routes through
        ``milodex.promotion.state_machine.demote`` — the same callee the CLI
        uses. The governance path owns:

        * append-only ``PromotionEvent`` with ``promotion_type='demotion'``
          and ``reverses_event_id`` chaining,
        * YAML stage-line update when ``to_stage='idle'`` or ``'backtest'``
          (ledger-only when ``to_stage='disabled'``),
        * non-blank reason refusal and target-set enforcement.

        The facade does not duplicate those rules; it surfaces refusals as
        ``Blocker`` records and returns durable identifiers on success.
        """

        def _dispatch(
            proposal: CommandProposal,
            revalidation: CommandProposal,
            config: StrategyConfig,
            event_store: EventStore,
        ) -> CommandResult:
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
                # The governance layer raised. Surface it as a structured
                # blocker rather than letting the exception cross the facade
                # boundary.
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

            warnings: list[str] = self._readiness_warnings_from(revalidation)
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

        # H2: the revalidation closure MUST capture `gui_submit` — propose_demote
        # uses it to enforce the disabled-target GUI guard. Dropping it here
        # would silently let GUI-submitted disabled demotions bypass the guard.
        return self._submit_with_config(
            proposal,
            expected_action_family=ACTION_FAMILY_DEMOTE,
            caller_method="submit_demote",
            revalidate=lambda: self.propose_demote(
                proposal.strategy_id,
                to_stage=proposal.inputs.get("to_stage", ""),
                reason=proposal.inputs.get("reason"),
                approved_by=proposal.inputs.get("approved_by", "operator"),
                evidence_ref=proposal.inputs.get("evidence_ref"),
                gui_submit=gui_submit,
            ),
            dispatch=_dispatch,
        )

    def submit_start_paper_runner(self, proposal: CommandProposal) -> CommandResult:
        """Launch a non-blocking paper runner for a Bench-approved proposal."""
        if proposal.action_family != ACTION_FAMILY_START_PAPER_RUNNER:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=proposal.action_family,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="proposal_action_family_mismatch",
                        message=(
                            f"submit_start_paper_runner received a proposal for "
                            f"'{proposal.action_family}'; expected "
                            f"'{ACTION_FAMILY_START_PAPER_RUNNER}'."
                        ),
                        context={
                            "expected": ACTION_FAMILY_START_PAPER_RUNNER,
                            "received": proposal.action_family,
                        },
                    )
                ],
            )

        revalidation = self.propose_start_paper_runner(proposal.strategy_id)
        if not revalidation.admissible:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_START_PAPER_RUNNER,
                status="blocked",
                blockers=list(revalidation.blockers),
            )

        if self._paper_runner_control is None:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_START_PAPER_RUNNER,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="paper_runner_control_unavailable",
                        message=(
                            "BenchCommandFacade was constructed without a "
                            "paper_runner_control; runner submits require one."
                        ),
                        context={},
                    )
                ],
            )
        control = self._paper_runner_control
        job_ref = self._create_orchestration_job(
            proposal,
            action_type="paper_session_start",
            requested_stage="paper",
            progress_label="starting paper runner",
        )
        try:
            result = control.start(proposal.strategy_id)
        except Exception as exc:  # noqa: BLE001 - facade returns structured failures.
            error_result = CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_START_PAPER_RUNNER,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="paper_runner_start_failed",
                        message=f"Paper runner start failed: {exc}",
                        context={"error_type": exc.__class__.__name__},
                    )
                ],
                submitted_at=self._now(),
            )
            return self._finish_orchestration_job(job_ref, error_result)

        durable_refs = {
            "strategy_id": proposal.strategy_id,
            "runner_pid": str(getattr(result, "pid", "")),
            "stop_request_path": str(getattr(result, "stop_request_path", "")),
            "action": "start_paper_runner",
        }
        command = tuple(getattr(result, "command", ()))
        session_id = self._latest_open_session_id(proposal.strategy_id)
        if not session_id:
            error_result = CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_START_PAPER_RUNNER,
                status="error",
                durable_refs=durable_refs,
                blockers=[
                    Blocker(
                        reason_code="runner_audit_link_missing",
                        message=(
                            "Paper runner start launched, but Bench could not link it "
                            "to an open strategy_runs session. Submitted runner-control "
                            "results require durable audit evidence (ADR 0051)."
                        ),
                        context={
                            "strategy_id": proposal.strategy_id,
                            "runner_pid": durable_refs["runner_pid"],
                            "stop_request_path": durable_refs["stop_request_path"],
                        },
                    )
                ],
                data={
                    "strategy_id": proposal.strategy_id,
                    "runner_pid": getattr(result, "pid", None),
                    "session_id": None,
                    "command": list(command),
                },
                submitted_at=getattr(result, "launched_at", self._now()),
                audit_event_id=None,
            )
            return self._finish_orchestration_job(job_ref, error_result)

        durable_refs["session_id"] = session_id
        submit_result = CommandResult(
            proposal_id=proposal.proposal_id,
            action_family=ACTION_FAMILY_START_PAPER_RUNNER,
            status="submitted",
            durable_refs=durable_refs,
            data={
                "strategy_id": proposal.strategy_id,
                "runner_pid": getattr(result, "pid", None),
                "session_id": session_id,
                "command": list(command),
            },
            submitted_at=getattr(result, "launched_at", self._now()),
            audit_event_id=session_id or None,
        )
        return self._finish_orchestration_job(job_ref, submit_result)

    def submit_stop_paper_runner(self, proposal: CommandProposal) -> CommandResult:
        """Request controlled stop for an active paper runner."""
        if proposal.action_family != ACTION_FAMILY_STOP_PAPER_RUNNER:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=proposal.action_family,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="proposal_action_family_mismatch",
                        message=(
                            f"submit_stop_paper_runner received a proposal for "
                            f"'{proposal.action_family}'; expected "
                            f"'{ACTION_FAMILY_STOP_PAPER_RUNNER}'."
                        ),
                        context={
                            "expected": ACTION_FAMILY_STOP_PAPER_RUNNER,
                            "received": proposal.action_family,
                        },
                    )
                ],
            )

        revalidation = self.propose_stop_paper_runner(proposal.strategy_id)
        if not revalidation.admissible:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
                status="blocked",
                blockers=list(revalidation.blockers),
            )

        holder = self._peek_runner_lock(proposal.strategy_id)
        if holder is None:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
                status="blocked",
                blockers=[
                    Blocker(
                        reason_code="no_active_runner",
                        message=(f"No active paper-runner session for '{proposal.strategy_id}'."),
                        context={"lock_name": runner_lock_name(proposal.strategy_id)},
                    )
                ],
            )

        if self._paper_runner_control is None:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="paper_runner_control_unavailable",
                        message=(
                            "BenchCommandFacade was constructed without a "
                            "paper_runner_control; runner submits require one."
                        ),
                        context={},
                    )
                ],
            )
        control = self._paper_runner_control
        session_id = self._latest_open_session_id(proposal.strategy_id)
        if not session_id:
            return CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
                status="blocked",
                blockers=[
                    Blocker(
                        reason_code="runner_audit_link_missing",
                        message=(
                            "Controlled stop requires an open strategy_runs session "
                            "so the submit can link durable audit evidence (ADR 0051)."
                        ),
                        context={
                            "strategy_id": proposal.strategy_id,
                            "lock_holder": holder,
                        },
                    )
                ],
            )

        job_ref = self._create_orchestration_job(
            proposal,
            action_type="paper_session_stop",
            requested_stage="paper",
            progress_label="stopping paper runner",
        )
        try:
            result = control.request_controlled_stop(proposal.strategy_id, holder=holder)
        except Exception as exc:  # noqa: BLE001 - facade returns structured failures.
            error_result = CommandResult(
                proposal_id=proposal.proposal_id,
                action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="paper_runner_stop_failed",
                        message=f"Controlled-stop request failed: {exc}",
                        context={"error_type": exc.__class__.__name__},
                    )
                ],
                submitted_at=self._now(),
            )
            return self._finish_orchestration_job(job_ref, error_result)

        durable_refs = {
            "strategy_id": proposal.strategy_id,
            "stop_request_path": str(getattr(result, "request_path", "")),
            "requested_pid": str(holder.get("pid", "")),
            "exit_reason": "controlled_stop",
            "kill_switch": "false",
            "action": "stop_paper_runner",
        }
        durable_refs["session_id"] = session_id
        submit_result = CommandResult(
            proposal_id=proposal.proposal_id,
            action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
            status="submitted",
            durable_refs=durable_refs,
            data={
                "strategy_id": proposal.strategy_id,
                "session_id": session_id,
                "holder": dict(holder),
                "controlled_stop": True,
                "kill_switch": False,
                "workflow_readiness": revalidation.projected_outcome.get(
                    "workflow_readiness",
                    {},
                ),
            },
            warnings=self._readiness_warnings_from(revalidation),
            submitted_at=getattr(result, "requested_at", self._now()),
            audit_event_id=session_id,
        )
        return self._finish_orchestration_job(job_ref, submit_result)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _evaluate_workflow_readiness(
        self,
        *,
        action_family: str,
        strategy_id: str,
        required_checks: frozenset[str],
        inspected_checks: frozenset[str] = frozenset(),
    ) -> tuple[WorkflowReadinessReport, list[Blocker], list[Precondition]]:
        report = self._workflow_readiness.evaluate(
            action_family=action_family,
            strategy_id=strategy_id,
            required_checks=required_checks,
            inspected_checks=inspected_checks,
        )
        # Only issues in required_checks dimensions can block; inspected-only
        # issues surface as informational payload but never gate admissibility.
        blockers = [
            issue.to_blocker()
            for issue in report.issues
            if issue.blocking and issue.dimension in required_checks
        ]
        preconditions = [
            Precondition(
                f"workflow_readiness_{dimension}",
                passed=not any(
                    issue.blocking and issue.dimension == dimension for issue in report.issues
                ),
            )
            for dimension in sorted(required_checks)
        ]
        return report, blockers, preconditions

    @staticmethod
    def _attach_workflow_readiness(
        projected_outcome: dict[str, Any],
        report: WorkflowReadinessReport,
    ) -> dict[str, Any]:
        if not report.issues:
            return projected_outcome
        return {**projected_outcome, "workflow_readiness": report.to_dict()}

    @staticmethod
    def _readiness_warnings_from(proposal: CommandProposal) -> list[str]:
        report = proposal.projected_outcome.get("workflow_readiness")
        if not isinstance(report, dict):
            return []
        warnings: list[str] = []
        for issue in report.get("issues", []):
            if isinstance(issue, dict) and not bool(issue.get("blocking", True)):
                warnings.append(str(issue.get("message", "")))
        return [warning for warning in warnings if warning]

    def _create_orchestration_job(
        self,
        proposal: CommandProposal,
        *,
        action_type: str,
        requested_stage: str,
        progress_label: str,
    ) -> dict[str, str] | None:
        if self._event_store_factory is None:
            return None
        try:
            event_store = self._event_store_factory()
            now = self._now()
            batch_id = str(uuid.uuid4())
            job_id = str(uuid.uuid4())
            event_store.create_orchestration_batch(
                OrchestrationBatchEvent(
                    batch_id=batch_id,
                    action_type=action_type,
                    requested_by="bench_gui",
                    requested_at=now,
                    status="running",
                    metadata={
                        "source": "bench_command_facade",
                        "proposal_id": proposal.proposal_id,
                        "action_family": proposal.action_family,
                    },
                )
            )
            event_store.create_orchestration_job(
                OrchestrationJobEvent(
                    job_id=job_id,
                    batch_id=batch_id,
                    strategy_id=proposal.strategy_id,
                    action_type=action_type,
                    requested_stage=requested_stage,
                    status="running",
                    queued_at=now,
                    started_at=now,
                    ended_at=None,
                    cancel_requested_at=None,
                    execution_ref_type=None,
                    execution_ref=None,
                    progress_current=None,
                    progress_total=None,
                    progress_label=progress_label,
                    error_code=None,
                    error_message=None,
                    metadata={
                        "proposal_id": proposal.proposal_id,
                        "action_family": proposal.action_family,
                        "inputs": dict(proposal.inputs),
                    },
                )
            )
            return {"job_id": job_id, "batch_id": batch_id}
        except Exception:  # noqa: BLE001 - submit should continue if job journaling fails.
            logger.exception("Failed to create Bench orchestration job.")
            return None

    def _finish_orchestration_job(
        self,
        job_ref: dict[str, str] | None,
        result: CommandResult,
    ) -> CommandResult:
        if not job_ref or self._event_store_factory is None:
            return result

        durable_refs = {
            **result.durable_refs,
            "orchestration_job_id": job_ref["job_id"],
            "orchestration_batch_id": job_ref["batch_id"],
        }
        result = replace(result, durable_refs=durable_refs)

        job_status = {
            "submitted": "completed",
            "blocked": "blocked",
        }.get(result.status, "failed")
        execution_ref_type, execution_ref = self._execution_ref(result)
        first_blocker = result.blockers[0] if result.blockers else None
        try:
            event_store = self._event_store_factory()
            event_store.update_orchestration_job_status(
                job_ref["job_id"],
                status=job_status,
                ended_at=result.submitted_at or self._now(),
                execution_ref_type=execution_ref_type,
                execution_ref=execution_ref,
                progress_label=result.status,
                error_code=first_blocker.reason_code if first_blocker else None,
                error_message=first_blocker.message if first_blocker else None,
                metadata={
                    "proposal_id": result.proposal_id,
                    "action_family": result.action_family,
                    "status": result.status,
                    "durable_refs": durable_refs,
                },
            )
            event_store.update_orchestration_batch_status(
                job_ref["batch_id"],
                status=job_status,
                metadata={
                    "proposal_id": result.proposal_id,
                    "action_family": result.action_family,
                    "status": result.status,
                },
            )
        except Exception:  # noqa: BLE001 - result remains authoritative.
            logger.exception("Failed to finish Bench orchestration job.")
        return result

    @staticmethod
    def _execution_ref(result: CommandResult) -> tuple[str | None, str | None]:
        if result.action_family == ACTION_FAMILY_BACKTEST and result.durable_refs.get("run_id"):
            return "backtest_run", result.durable_refs["run_id"]
        if result.durable_refs.get("session_id"):
            return "strategy_run", result.durable_refs["session_id"]
        return None, None

    def _resolve_config(self, strategy_id: str) -> tuple[StrategyConfig | None, Blocker | None]:
        """Locate the YAML for ``strategy_id`` under ``config_dir``.

        Bench-side adapter over the canonical
        ``milodex.promotion.manifest.resolve_strategy_config_path``. Returns
        ``(config, None)`` on success or ``(None, blocker)`` on failure.
        Read-only: no YAML mutation.

        Preserves three behaviors from the previous in-place glob loop:

        1. **Blank-string short-circuit** with distinct ``strategy_id_blank``
           reason code — the canonical helper has no blank check, but the
           facade's contract pins this precondition.
        2. **Multi-file glob ordering + parse-error skipping** owned by
           ``resolve_strategy_config_path`` (not duplicated here).
        3. **Malformed-YAML guard on the matched file** — defense-in-depth.
           Today the canonical helper already calls ``load_strategy_config``
           successfully before returning a path, so this guard rarely fires.
           But protecting against future helper changes (or file-system races
           between the helper's read and ours) keeps the facade's error
           surface stable and structured rather than letting an uncaught
           ``ValueError`` cross the boundary.
        """
        if not strategy_id or not strategy_id.strip():
            return None, Blocker(
                reason_code="strategy_id_blank",
                message="strategy_id must be a non-empty string.",
                context={},
            )
        try:
            config_path = resolve_strategy_config_path(strategy_id, config_dir=self._config_dir)
        except ValueError:
            return None, Blocker(
                reason_code="strategy_not_found",
                message=f"No strategy config found for strategy_id '{strategy_id}'.",
                context={"strategy_id": strategy_id, "config_dir": str(self._config_dir)},
            )
        try:
            config = load_strategy_config(config_path)
        except (ValueError, yaml.YAMLError):
            return None, Blocker(
                reason_code="strategy_config_invalid",
                message=(
                    f"Strategy YAML at {config_path} matched strategy_id "
                    f"'{strategy_id}' during path resolution but failed to load."
                ),
                context={"strategy_id": strategy_id, "config_path": str(config_path)},
            )
        return config, None

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

    def _backtest_durable_refs(self, result: Any, *, walk_forward: bool) -> dict[str, str]:
        refs = {
            "run_id": str(result.run_id),
            "strategy_id": str(result.strategy_id),
            "start": result.start_date.isoformat(),
            "end": result.end_date.isoformat(),
            "walk_forward": str(bool(walk_forward)).lower(),
            "risk_policy": self._risk_policy_value(
                getattr(result, "risk_policy", RiskPolicy.BYPASS)
            ),
        }
        db_id = getattr(result, "db_id", None)
        if db_id is not None:
            refs["backtest_run_db_id"] = str(db_id)
        return refs

    def _backtest_result_data(self, result: Any, *, walk_forward: bool) -> dict[str, Any]:
        data_quality = dict(getattr(result, "data_quality", {}) or {})
        run_manifest = dict(getattr(result, "run_manifest", {}) or {})
        payload: dict[str, Any] = {
            "walk_forward": bool(walk_forward),
            "data_quality": data_quality,
            "data_quality_status": data_quality.get("status"),
            "run_manifest": run_manifest,
        }
        if walk_forward:
            payload["skipped_count"] = int(getattr(result, "oos_skipped_count", 0))
            payload["oos_aggregate"] = {
                "trade_count": int(getattr(result, "oos_trade_count", 0)),
                "skipped_count": int(getattr(result, "oos_skipped_count", 0)),
                "trading_days": int(getattr(result, "oos_trading_days", 0)),
                "total_return_pct": float(getattr(result, "oos_total_return_pct", 0.0)),
                "sharpe": getattr(result, "oos_sharpe", None),
                "max_drawdown_pct": float(getattr(result, "oos_max_drawdown_pct", 0.0)),
            }
        else:
            payload["skipped_count"] = int(getattr(result, "skipped_count", 0))
            payload["metrics"] = {
                "trade_count": int(getattr(result, "trade_count", 0)),
                "skipped_count": int(getattr(result, "skipped_count", 0)),
                "trading_days": int(getattr(result, "trading_days", 0)),
                "initial_equity": float(getattr(result, "initial_equity", 0.0)),
                "final_equity": float(getattr(result, "final_equity", 0.0)),
                "total_return_pct": float(getattr(result, "total_return_pct", 0.0)),
                "buy_count": int(getattr(result, "buy_count", 0)),
                "sell_count": int(getattr(result, "sell_count", 0)),
                "round_trip_count": int(getattr(result, "round_trip_count", 0)),
            }
        return payload

    @staticmethod
    def _risk_policy_value(policy: Any) -> str:
        value = getattr(policy, "value", policy)
        return str(value)

    def _require_event_store(self, proposal: CommandProposal) -> EventStore | CommandResult:
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

    def _submit_with_config(
        self,
        proposal: CommandProposal,
        *,
        expected_action_family: str,
        caller_method: str,
        revalidate: Callable[[], CommandProposal],
        dispatch: Callable[
            [CommandProposal, CommandProposal, StrategyConfig, EventStore],
            CommandResult,
        ],
    ) -> CommandResult:
        """Shared spine for the four config-resolving submits.

        Flow: family check → event-store require → revalidate → resolve config
        → dispatch(proposal, revalidation, config, event_store) → return.

        The dispatch callable receives BOTH the original proposal AND the
        revalidation result, because some submits (notably demote) build
        post-dispatch warnings from ``revalidation.blockers`` via
        ``_readiness_warnings_from(revalidation)``. Passing revalidation
        up-front avoids a wasteful second ``revalidate()`` call inside dispatch
        and eliminates a drift risk between the shell's admissibility check
        and the dispatch's warnings derivation.

        Orchestration journaling stays OUTSIDE the shell — only backtest
        journals on this code path (freeze/demote/promote do not). The backtest
        dispatch callback owns its ``_create_orchestration_job`` /
        ``_finish_orchestration_job`` pair explicitly because all error-path
        returns inside the dispatch body must finish the job; the shell cannot
        do that on the dispatch's behalf.

        NOTE: any propose-side kwargs that affect admissibility (e.g.
        ``gui_submit`` on demote) MUST be captured in the ``revalidate``
        closure at the call site — the shell forwards no extra context.

        Runner submits do NOT use this shell: they do not resolve config and
        have lifecycle pre/post checks (``_peek_runner_lock`` /
        ``_latest_open_session_id``) that don't fit the uniform spine.
        """
        if proposal.action_family != expected_action_family:
            return _action_family_mismatch_result(
                proposal, expected=expected_action_family, caller_method=caller_method
            )
        event_store_or_error = self._require_event_store(proposal)
        if isinstance(event_store_or_error, CommandResult):
            return event_store_or_error
        revalidation = revalidate()
        if not revalidation.admissible:
            return _stale_proposal_result(proposal, revalidation)
        config, resolve_blocker = self._resolve_config(proposal.strategy_id)
        if resolve_blocker is not None or config is None:
            # _resolve_config's contract pairs None config with a non-None
            # Blocker (never returns (None, None)).  The assert is a tripwire
            # in case a future change to _resolve_config weakens that
            # invariant — fail loudly with a clear message rather than
            # producing a malformed CommandResult with no blocker.  Opus
            # reviewer 2026-05-24 flagged the prior synthesis fallback as
            # dead defensive code post-PR #186.
            assert resolve_blocker is not None, (
                "_resolve_config contract violated: returned (None, None) "
                f"for strategy_id={proposal.strategy_id!r}"
            )
            return _resolve_failed_result(proposal, resolve_blocker)
        return dispatch(proposal, revalidation, config, event_store_or_error)

    def _peek_runner_lock(self, strategy_id: str) -> dict[str, Any] | None:
        """Peek the per-strategy runner advisory lock without acquiring it.

        Routes through the shared identity-verified liveness helper
        (:func:`milodex.core.advisory_lock.live_lock_holder`): returns the
        holder dict only when a *genuinely-live* process holds the lock, and
        ``None`` when the lock is free **or** held by a stale / recycled-PID
        lock file. This keeps every operator-facing surface that consults it
        honest — controlled stop (``submit_stop_paper_runner``), duplicate-start
        (``propose_start``/``submit_start_paper_runner``), and stop
        admissibility (``propose_stop``) — so a hard-killed-but-lock-present
        runner is reported as absent (``no_active_runner``), not stoppable, and
        a stale lock no longer blocks a legitimate relaunch. The child's
        ``O_EXCL`` acquire in ``PaperRunnerControl.start`` remains the final
        single-runner correctness backstop.
        """
        lock = AdvisoryLock(
            runner_lock_name(strategy_id),
            locks_dir=self._locks_dir,
            holder_name="bench.facade.peek",
        )
        holder = live_lock_holder(lock)
        if holder is None:
            return None
        return {
            "pid": holder.pid,
            "hostname": holder.hostname,
            "holder_name": holder.holder_name,
            "started_at": holder.started_at.isoformat(),
        }

    def _peek_eval_symbol_collision(
        self, strategy_id: str, config: StrategyConfig
    ) -> Blocker | None:
        """Pre-spawn mirror of the ``milodex strategy run`` launch guard.

        The CLI child enforces the same evaluation-symbol co-run refusal (ADR
        0026 addendum 2026-06-05), but from the GUI spawn path that refusal
        happens inside a detached subprocess — the child dies and Bench shows
        nothing. Surfacing it here as a structured proposal blocker makes the
        collision visible *before* any process is spawned. Read-only; the
        child's own check remains the enforcement backstop, and the residual
        scan→acquire TOCTOU documented in the addendum is unchanged.
        """
        try:
            eval_symbol = evaluation_symbol_for_config(config)
        except ValueError as exc:
            return Blocker(
                reason_code="evaluation_symbol_unresolvable",
                message=(
                    f"Strategy '{strategy_id}' has no resolvable evaluation symbol; "
                    f"the runner would refuse to start: {exc}"
                ),
                context={"strategy_id": strategy_id},
            )
        live_by_symbol = live_runner_eval_symbols(
            self._config_dir,
            self._locks_dir,
            exclude_strategy_id=strategy_id,
        )
        colliding_strategy_id = live_by_symbol.get(eval_symbol)
        if colliding_strategy_id is None:
            return None
        return Blocker(
            reason_code="evaluation_symbol_in_use",
            message=(
                f"Evaluation symbol '{eval_symbol}' is already in use by the live "
                f"runner for '{colliding_strategy_id}'. Stop that runner before "
                f"starting '{strategy_id}' on the same symbol (same-symbol co-run "
                "guardrail, ADR 0026 addendum / ADR 0055)."
            ),
            context={
                "evaluation_symbol": eval_symbol,
                "colliding_strategy_id": colliding_strategy_id,
            },
        )

    def _latest_open_session_id(self, strategy_id: str) -> str | None:
        if self._event_store_factory is None:
            return None
        try:
            runs = self._event_store_factory().list_strategy_runs()
        except Exception:  # noqa: BLE001 - best-effort durable ref enrichment.
            return None
        open_runs = [run for run in runs if run.strategy_id == strategy_id and run.ended_at is None]
        if not open_runs:
            return None
        return str(open_runs[-1].session_id)

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


def _action_family_mismatch_result(
    proposal: CommandProposal, *, expected: str, caller_method: str
) -> CommandResult:
    """Build the standard `proposal_action_family_mismatch` error CommandResult.

    Replaces the inline ``CommandResult(..., blockers=[Blocker(reason_code=
    "proposal_action_family_mismatch", ...)])`` constructions that recur at
    the top of each ``submit_*`` method. Keeps the error shape uniform.

    ``caller_method`` is the name of the submit method that received the
    mismatch (e.g. ``"submit_freeze_manifest"``) — used in the message so an
    operator reading raw logs can tell which submit refused the proposal
    without having to cross-reference ``context['received']``.
    """
    return CommandResult(
        proposal_id=proposal.proposal_id,
        action_family=proposal.action_family,
        status="error",
        blockers=[
            Blocker(
                reason_code="proposal_action_family_mismatch",
                message=(
                    f"{caller_method} received a proposal for '{proposal.action_family}'; "
                    f"expected '{expected}'."
                ),
                context={"expected": expected, "received": proposal.action_family},
            )
        ],
    )


def _stale_proposal_result(
    proposal: CommandProposal, revalidation: CommandProposal
) -> CommandResult:
    """Build the standard `blocked` CommandResult for an inadmissible revalidation.

    Propagates the revalidation's blockers verbatim — the propose-side already
    constructed structured reasons (stage_drift, evidence_invalidated, etc.).
    """
    return CommandResult(
        proposal_id=proposal.proposal_id,
        action_family=proposal.action_family,
        status="blocked",
        blockers=list(revalidation.blockers),
    )


def _resolve_failed_result(proposal: CommandProposal, resolve_blocker: Blocker) -> CommandResult:
    """Build the standard `blocked` CommandResult for a resolve_config failure."""
    return CommandResult(
        proposal_id=proposal.proposal_id,
        action_family=proposal.action_family,
        status="blocked",
        blockers=[resolve_blocker],
    )


def _new_proposal_id() -> str:
    return str(uuid.uuid4())


def _blockers_from_promote_blocked(
    result: PromoteBlocked,
    *,
    from_stage: str,
    to_stage: str,
    run_id: str | None,
) -> list[Blocker]:
    """Translate a ``PromoteBlocked`` from the orchestrator into Bench-shaped
    ``Blocker`` records.

    The orchestrator uses domain-neutral reason codes; Bench has historically
    surfaced these as ``gate_check_failed`` / ``backtest_run_not_found`` /
    ``invalid_stage_transition``. QML and ``test_bench_facade.py`` pin those
    codes, so the translation lives at the facade boundary rather than in the
    orchestrator.
    """
    if result.reason_code == REASON_GATE_FAILED:
        snapshot = result.metrics_snapshot or {}
        return [
            Blocker(
                reason_code="gate_check_failed",
                message=failure,
                context={
                    "promotion_type": result.promotion_type,
                    "sharpe_ratio": snapshot.get("sharpe_ratio"),
                    "max_drawdown_pct": snapshot.get("max_drawdown_pct"),
                    "trade_count": snapshot.get("trade_count"),
                },
            )
            for failure in (result.gate_failures or [result.message])
        ]
    if result.reason_code == REASON_MISSING_BACKTEST_RUN:
        return [
            Blocker(
                reason_code="backtest_run_not_found",
                message=result.message,
                context={"run_id": run_id},
            )
        ]
    if result.reason_code == REASON_INVALID_STAGE_TRANSITION:
        return [
            Blocker(
                reason_code="invalid_stage_transition",
                message=result.message,
                context={"from_stage": from_stage, "to_stage": to_stage},
            )
        ]
    # Defensive: any future orchestrator reason code surfaces as a structured
    # generic blocker rather than vanishing silently.
    return [
        Blocker(
            reason_code=result.reason_code,
            message=result.message,
            context={"from_stage": from_stage, "to_stage": to_stage},
        )
    ]

"""Qt bridge over ``milodex.commands.bench.BenchCommandFacade``.

This module is the **only** file under ``src/milodex/gui/`` permitted to
import the Bench command facade (ADR 0051 §4 / §5). It translates
Q_INVOKABLE slot calls from QML into facade calls and translates the
result dataclasses into ``QVariant``-friendly dicts.

The bridge owns **no business rules**. Every decision — stage transitions,
evidence requirements, advisory-lock peek, governance refusals — is owned
by the facade and the modules it routes into (``milodex.promotion``,
``milodex.core.advisory_lock``, …). The bridge:

* exposes propose/submit slot pairs for all six submit-capable action
  families (demote, freeze-manifest, promote-to-paper, backtest, start-
  paper-runner, stop-paper-runner — Phases C2 through F),
* caches the live ``CommandProposal`` between propose and submit so the
  proposal identity (``proposal_id``) survives the round-trip without QML
  needing to reconstruct dataclasses,
* refreshes the Bench and Ledger read models on a successful submit (single
  permitted reach into ``_kick_refresh`` on each polling model), and
* emits a ``submitCompleted`` signal QML surfaces can listen to.

``submitCapableActionFamilies()`` enumerates all six wired families.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot

from milodex.commands.bench import (
    ACTION_FAMILY_BACKTEST,
    ACTION_FAMILY_DEMOTE,
    ACTION_FAMILY_FREEZE_MANIFEST,
    ACTION_FAMILY_PROMOTE_TO_PAPER,
    ACTION_FAMILY_START_PAPER_RUNNER,
    ACTION_FAMILY_STOP_PAPER_RUNNER,
    BenchCommandFacade,
    Blocker,
    CommandProposal,
    CommandResult,
)

if TYPE_CHECKING:
    from milodex.gui.read_models import BenchState, LedgerState

logger = logging.getLogger(__name__)


class _SubmitSignals(QObject):
    completed = Signal("QVariantMap")


class _SubmitRunnable(QRunnable):
    def __init__(
        self,
        proposal: CommandProposal,
        submitter: Callable[[CommandProposal], CommandResult],
        signals: _SubmitSignals,
    ) -> None:
        super().__init__()
        self._proposal = proposal
        self._submitter = submitter
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - exercised through QObject lifecycle tests
        try:
            result = self._submitter(self._proposal)
        except Exception as exc:  # noqa: BLE001 - bridge must surface final result.
            logger.exception("Bench async submit failed.")
            result = CommandResult(
                proposal_id=self._proposal.proposal_id,
                action_family=self._proposal.action_family,
                status="error",
                blockers=[
                    Blocker(
                        reason_code="bridge_async_submit_failed",
                        message=f"Async submit failed: {exc}",
                        context={"error_type": exc.__class__.__name__},
                    )
                ],
            )
        self._signals.completed.emit(result.to_dict())


def _resolve_operator_identity() -> str:
    """Return the operator identity recorded as ``approved_by`` for GUI submits.

    Single backend source for ``approved_by`` from the Bench GUI surface; QML
    must not decide identity (Phase C2 review F4). The current default mirrors
    the prior hardcoded literal so existing audit-trail expectations hold; a
    future PR may swap this for an OS user, env var, or settings-backed value
    without touching the QML or the facade contract.
    """
    return "operator"


class BenchCommandBridge(QObject):
    """Qt-side bridge to the Bench command facade (ADR 0051 Phase F).

    All six action families are submit-capable:
    - demote / walk-back (Phase C2)
    - freeze-manifest (Phase D1)
    - promote-to-paper (Phase D3)
    - canonical backtest evidence generation (Phase E)
    - start paper runner (Phase F)
    - stop paper runner / controlled-stop (Phase F)

    Exposed via ``submitCapableActionFamilies()``. No other Bench action
    families are wired; preview-only families are handled by the read-model
    surface (ADR 0049).
    """

    # Emitted after every submit attempt — successful, blocked, or errored.
    # Payload is the ``CommandResult.to_dict()`` mapping.
    submitCompleted = Signal("QVariantMap")  # noqa: N815
    submitQueued = Signal("QVariantMap")  # noqa: N815

    def __init__(
        self,
        facade: BenchCommandFacade,
        *,
        bench_state: BenchState | None = None,
        ledger_state: LedgerState | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._facade = facade
        self._bench_state = bench_state
        self._ledger_state = ledger_state
        # Proposal cache. propose_demote stores the live CommandProposal here
        # so submit_demote can retrieve it by id without QML reconstructing
        # the dataclass. Each proposal is consumed exactly once; stale ids
        # produce a structured error result.
        self._proposals: dict[str, CommandProposal] = {}
        self._thread_pool = QThreadPool()
        self._submit_signals = _SubmitSignals(self)
        self._submit_signals.completed.connect(
            self._on_async_submit_completed,
            Qt.ConnectionType.QueuedConnection,
        )

    def _unknown_proposal_payload(
        self, proposal_id: str, *, action_family: str, submit_method: str, propose_method: str
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "proposal_id": proposal_id,
            "action_family": action_family,
            "status": "error",
            "durable_refs": {},
            "data": {},
            "blockers": [
                {
                    "reason_code": "unknown_proposal_id",
                    "message": (
                        f"{submit_method} was called with an unknown proposal id. "
                        f"Re-run {propose_method} and retry."
                    ),
                    "context": {"proposal_id": proposal_id},
                }
            ],
            "warnings": [],
            "submitted_at": None,
            "audit_event_id": None,
        }
        self.submitCompleted.emit(payload)
        return payload

    def _refresh_after_submit(self, operation: str) -> None:
        if self._bench_state is not None:
            try:
                self._bench_state._kick_refresh()  # noqa: SLF001
            except Exception:  # noqa: BLE001
                logger.exception("BenchState refresh after %s failed.", operation)
        if self._ledger_state is not None:
            try:
                self._ledger_state._kick_refresh()  # noqa: SLF001
            except Exception:  # noqa: BLE001
                logger.exception("LedgerState refresh after %s failed.", operation)

    def _queued_payload(self, proposal: CommandProposal) -> dict[str, Any]:
        return {
            "proposal_id": proposal.proposal_id,
            "action_family": proposal.action_family,
            "strategy_id": proposal.strategy_id,
            "bridge_status": "queued",
            "blockers": [],
            "warnings": [],
        }

    def _submit_sync(
        self,
        proposal_id: str,
        *,
        action_family: str,
        submit_method: str,
        propose_method: str,
        submitter: Callable[[CommandProposal], CommandResult],
        refresh_label: str,
    ) -> dict[str, Any]:
        proposal = self._proposals.pop(proposal_id, None)
        if proposal is None:
            return self._unknown_proposal_payload(
                proposal_id,
                action_family=action_family,
                submit_method=submit_method,
                propose_method=propose_method,
            )

        result = submitter(proposal)
        payload = result.to_dict()

        if result.status == "submitted":
            self._refresh_after_submit(refresh_label)

        self.submitCompleted.emit(payload)
        return payload

    def _submit_async(
        self,
        proposal_id: str,
        *,
        action_family: str,
        submit_method: str,
        propose_method: str,
        submitter: Callable[[CommandProposal], CommandResult],
    ) -> dict[str, Any]:
        proposal = self._proposals.pop(proposal_id, None)
        if proposal is None:
            return self._unknown_proposal_payload(
                proposal_id,
                action_family=action_family,
                submit_method=submit_method,
                propose_method=propose_method,
            )

        payload = self._queued_payload(proposal)
        self.submitQueued.emit(payload)
        self._thread_pool.start(_SubmitRunnable(proposal, submitter, self._submit_signals))
        return payload

    @Slot("QVariantMap")
    def _on_async_submit_completed(self, payload: dict[str, Any]) -> None:
        if str(payload.get("status") or "") == "submitted":
            self._refresh_after_submit(str(payload.get("action_family") or "async_submit"))
        self.submitCompleted.emit(payload)

    # ------------------------------------------------------------------ #
    # QML-callable slots (demotion / walk-back only — ADR 0051 Phase C2)
    # ------------------------------------------------------------------ #

    @Slot("QVariantMap", result="QVariantMap")
    def proposeDemote(self, inputs: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        """Build a demotion proposal and return it as a QVariant map.

        The QML caller passes ``{strategy_id, to_stage, reason, evidence_ref}``.
        Any ``approved_by`` key in the payload is ignored: identity is sourced
        backend-side via ``_resolve_operator_identity()`` so QML cannot decide
        who signs the audit record (Phase C2 review F4). The proposal is
        cached by id; ``submitDemote`` consumes it.
        """
        strategy_id = str(inputs.get("strategy_id", ""))
        to_stage = str(inputs.get("to_stage", ""))
        reason_raw = inputs.get("reason")
        reason = str(reason_raw) if reason_raw is not None else None
        evidence_ref_raw = inputs.get("evidence_ref")
        evidence_ref = (
            str(evidence_ref_raw)
            if evidence_ref_raw is not None and str(evidence_ref_raw)
            else None
        )

        proposal = self._facade.propose_demote(
            strategy_id,
            to_stage=to_stage,
            reason=reason,
            approved_by=_resolve_operator_identity(),
            evidence_ref=evidence_ref,
            gui_submit=True,
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal.to_dict()

    @Slot(str, result="QVariantMap")
    def submitDemote(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Submit a previously-proposed demotion by id.

        Refuses cleanly with a structured error result if the proposal id
        is unknown or already consumed. On success, refreshes the Bench
        read model so the next state the operator sees is post-submit.
        """
        return self._submit_sync(
            proposal_id,
            action_family=ACTION_FAMILY_DEMOTE,
            submit_method="submitDemote",
            propose_method="proposeDemote",
            submitter=lambda p: self._facade.submit_demote(p, gui_submit=True),
            refresh_label="submit_demote",
        )

    # ------------------------------------------------------------------ #
    # QML-callable slots (freeze-manifest — ADR 0051 Phase D1)
    # ------------------------------------------------------------------ #

    @Slot("QVariantMap", result="QVariantMap")
    def proposeFreezeManifest(self, inputs: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        """Build a freeze-manifest proposal and return it as a QVariant map.

        The QML caller passes ``{strategy_id}``. ``frozen_by`` is sourced
        backend-side via ``_resolve_operator_identity()`` so QML cannot decide
        identity (mirrors the Phase C2 review F4 pattern). The proposal is
        cached by id; ``submitFreezeManifest`` consumes it.
        """
        strategy_id = str(inputs.get("strategy_id", ""))
        proposal = self._facade.propose_freeze_manifest(
            strategy_id,
            frozen_by=_resolve_operator_identity(),
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal.to_dict()

    @Slot(str, result="QVariantMap")
    def submitFreezeManifest(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Submit a previously-proposed freeze-manifest by id.

        Mirrors ``submitDemote``: refuses cleanly on an unknown / consumed
        proposal id, refreshes the Bench read model on success, emits
        ``submitCompleted`` with the result payload in every branch.
        """
        return self._submit_sync(
            proposal_id,
            action_family=ACTION_FAMILY_FREEZE_MANIFEST,
            submit_method="submitFreezeManifest",
            propose_method="proposeFreezeManifest",
            submitter=self._facade.submit_freeze_manifest,
            refresh_label="submit_freeze_manifest",
        )

    # ------------------------------------------------------------------ #
    # QML-callable slots (canonical backtest evidence)
    # ------------------------------------------------------------------ #

    @Slot("QVariantMap", result="QVariantMap")
    def proposeBacktest(self, inputs: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        """Build a canonical Bench backtest proposal and cache it by id."""
        strategy_id = str(inputs.get("strategy_id", ""))
        start = _date_from_qvariant(inputs.get("start"), date(2020, 1, 1))
        end = _date_from_qvariant(inputs.get("end"), date(2024, 12, 31))
        walk_forward = bool(inputs.get("walk_forward", True))
        initial_equity = float(inputs.get("initial_equity", 100_000.0))
        slippage_raw = inputs.get("slippage")
        slippage = float(slippage_raw) if slippage_raw not in (None, "") else None
        run_id_raw = inputs.get("run_id")
        run_id = str(run_id_raw) if run_id_raw else None

        proposal = self._facade.propose_backtest(
            strategy_id,
            start=start,
            end=end,
            walk_forward=walk_forward,
            initial_equity=initial_equity,
            slippage=slippage,
            run_id=run_id,
            risk_policy=str(inputs.get("risk_policy", "bypass")),
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal.to_dict()

    @Slot(str, result="QVariantMap")
    def submitBacktest(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Submit a previously-proposed canonical backtest evidence run."""
        return self._submit_sync(
            proposal_id,
            action_family=ACTION_FAMILY_BACKTEST,
            submit_method="submitBacktest",
            propose_method="proposeBacktest",
            submitter=self._facade.submit_backtest,
            refresh_label="submit_backtest",
        )

    @Slot(str, result="QVariantMap")
    def submitBacktestAsync(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Queue a previously-proposed canonical backtest evidence run."""
        return self._submit_async(
            proposal_id,
            action_family=ACTION_FAMILY_BACKTEST,
            submit_method="submitBacktestAsync",
            propose_method="proposeBacktest",
            submitter=self._facade.submit_backtest,
        )

    # ------------------------------------------------------------------ #
    # QML-callable slots (promote to paper)
    # ------------------------------------------------------------------ #

    @Slot("QVariantMap", result="QVariantMap")
    def proposePromoteToPaper(self, inputs: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        """Build a promote-to-paper proposal and cache it by id."""
        strategy_id = str(inputs.get("strategy_id", ""))
        recommendation_raw = inputs.get("recommendation")
        recommendation = str(recommendation_raw) if recommendation_raw is not None else None
        run_id_raw = inputs.get("run_id")
        run_id = str(run_id_raw) if run_id_raw else None
        proposal = self._facade.propose_promote_to_paper(
            strategy_id,
            recommendation=recommendation,
            known_risks=_known_risks_from_qvariant(inputs),
            run_id=run_id,
            approved_by=_resolve_operator_identity(),
            lifecycle_exempt=bool(inputs.get("lifecycle_exempt", False)),
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal.to_dict()

    @Slot(str, result="QVariantMap")
    def submitPromoteToPaper(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Submit a previously-proposed promote-to-paper request."""
        return self._submit_sync(
            proposal_id,
            action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
            submit_method="submitPromoteToPaper",
            propose_method="proposePromoteToPaper",
            submitter=self._facade.submit_promote_to_paper,
            refresh_label="submit_promote_to_paper",
        )

    # ------------------------------------------------------------------ #
    # QML-callable slots (paper runner controls)
    # ------------------------------------------------------------------ #

    @Slot("QVariantMap", result="QVariantMap")
    def proposeStartPaperRunner(self, inputs: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        """Build a start-paper-runner proposal and cache it by id."""
        strategy_id = str(inputs.get("strategy_id", ""))
        proposal = self._facade.propose_start_paper_runner(strategy_id)
        self._proposals[proposal.proposal_id] = proposal
        return proposal.to_dict()

    @Slot(str, result="QVariantMap")
    def submitStartPaperRunner(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Submit a previously-proposed start-paper-runner request."""
        return self._submit_sync(
            proposal_id,
            action_family=ACTION_FAMILY_START_PAPER_RUNNER,
            submit_method="submitStartPaperRunner",
            propose_method="proposeStartPaperRunner",
            submitter=self._facade.submit_start_paper_runner,
            refresh_label="submit_start_paper_runner",
        )

    @Slot(str, result="QVariantMap")
    def submitStartPaperRunnerAsync(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Queue a previously-proposed start-paper-runner request."""
        return self._submit_async(
            proposal_id,
            action_family=ACTION_FAMILY_START_PAPER_RUNNER,
            submit_method="submitStartPaperRunnerAsync",
            propose_method="proposeStartPaperRunner",
            submitter=self._facade.submit_start_paper_runner,
        )

    @Slot("QVariantMap", result="QVariantMap")
    def proposeStopPaperRunner(self, inputs: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        """Build a stop-paper-runner proposal and cache it by id."""
        strategy_id = str(inputs.get("strategy_id", ""))
        proposal = self._facade.propose_stop_paper_runner(strategy_id)
        self._proposals[proposal.proposal_id] = proposal
        return proposal.to_dict()

    @Slot(str, result="QVariantMap")
    def submitStopPaperRunner(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Submit a previously-proposed controlled-stop request."""
        return self._submit_sync(
            proposal_id,
            action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
            submit_method="submitStopPaperRunner",
            propose_method="proposeStopPaperRunner",
            submitter=self._facade.submit_stop_paper_runner,
            refresh_label="submit_stop_paper_runner",
        )

    @Slot(str, result="QVariantMap")
    def submitStopPaperRunnerAsync(self, proposal_id: str) -> dict[str, Any]:  # noqa: N802
        """Queue a previously-proposed controlled-stop request."""
        return self._submit_async(
            proposal_id,
            action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
            submit_method="submitStopPaperRunnerAsync",
            propose_method="proposeStopPaperRunner",
            submitter=self._facade.submit_stop_paper_runner,
        )

    # ------------------------------------------------------------------ #
    # Introspection (used by tests and operator surfaces)
    # ------------------------------------------------------------------ #

    @Slot(result="QStringList")
    def submitCapableActionFamilies(self) -> list[str]:  # noqa: N802
        """Return the list of action families this bridge can submit.

        Bench submit-capable families are exposed here so QML can keep its
        modal affordances in sync with the bridge.
        """
        return [
            ACTION_FAMILY_DEMOTE,
            ACTION_FAMILY_FREEZE_MANIFEST,
            ACTION_FAMILY_BACKTEST,
            ACTION_FAMILY_PROMOTE_TO_PAPER,
            ACTION_FAMILY_START_PAPER_RUNNER,
            ACTION_FAMILY_STOP_PAPER_RUNNER,
        ]


def _date_from_qvariant(value: Any, default: date) -> date:
    if value in (None, ""):
        return default
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _known_risks_from_qvariant(inputs: dict[str, Any]) -> list[str]:
    risks_raw = inputs.get("known_risks")
    if isinstance(risks_raw, list | tuple):
        return [str(risk) for risk in risks_raw if risk and str(risk).strip()]
    if risks_raw not in (None, ""):
        return [str(risks_raw)]
    known_risk_raw = inputs.get("known_risk")
    if known_risk_raw in (None, ""):
        return []
    return [str(known_risk_raw)]

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
  permitted reach into ``_kick_refresh`` on each polling model),
* emits a ``submitCompleted`` signal QML surfaces can listen to, and
* retains a bounded, **read-only** record of recent completion outcomes
  (``recentCompletions``) for a display-fallback sink. This record exists so a
  completion can never vanish when the operator closes the confirmation modal
  mid-spawn (P18): the modal's listener returns early when closed, but the
  bridge records every outcome regardless. The sink **never re-issues or acks a
  command** — recording is a list insert, dismissing (``dismissCompletion``) is
  a list removal. No facade call, no submit, no event-store / broker / lock
  write is performed by the sink.

``submitCapableActionFamilies()`` enumerates all six wired families.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QRunnable, Qt, QThreadPool, Signal, Slot

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

# Upper bound on the read-only recent-completion display record. The sink is a
# fallback banner, not an audit log — durable history lives in the event store.
_MAX_RECENT_COMPLETIONS = 20

# Best-effort drain timeout (ms) for the bridge's private async pool on
# shutdown. Matches the AppController global-pool drain value so a backtest
# in flight is given the same grace period regardless of which pool it ran on.
_SHUTDOWN_DRAIN_TIMEOUT_MS = 3000


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
    # Emitted whenever the read-only recent-completion record changes (a new
    # completion is recorded or one is dismissed). Read-only display state only.
    recentCompletionsChanged = Signal()  # noqa: N815

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
        # Bounded, newest-first, read-only display record of recent completion
        # outcomes. Populated by _emit_completion on every completion (sync,
        # async, unknown-proposal error); trimmed at _MAX_RECENT_COMPLETIONS.
        # Mutated only by _emit_completion (insert) and dismissCompletion
        # (remove); never re-issues or acks a command.
        self._completions: list[dict[str, Any]] = []
        self._thread_pool = QThreadPool()
        self._submit_signals = _SubmitSignals(self)
        self._completed_connected = False
        self._submit_signals.completed.connect(
            self._on_async_submit_completed,
            Qt.ConnectionType.QueuedConnection,
        )
        self._completed_connected = True

    # ------------------------------------------------------------------ #
    # Shutdown lifecycle (P2). The bridge owns a PRIVATE QThreadPool used by
    # _submit_async; it is registered with lifecycle=False, so it is excluded
    # from the lifecycle_models drained by app.aboutToQuit / AppController.
    # Before this fix NOTHING drained this private pool — quitting mid-async-
    # submit abandoned a worker writing backtest_runs + explanations and
    # dropped the queued completion. stop() is the explicit drain, wired into
    # both shutdown paths in app.run_app outside the lifecycle filter.
    # ------------------------------------------------------------------ #

    def stop(self) -> bool:
        """Best-effort drain of the private async pool, then disconnect.

        Mirrors ``PollingReadModel.stop`` / ``_disconnect_signals``: drains
        in-flight workers via ``waitForDone`` and defensively disconnects the
        completion signal so a late ``QueuedConnection`` delivery cannot touch a
        half-torn object on Windows shutdown. The drain is best-effort — a
        backtest exceeding ``_SHUTDOWN_DRAIN_TIMEOUT_MS`` is abandoned just as
        the prior global-pool drain would have abandoned it.

        Idempotent: both shutdown paths can fire (``quitRequested`` calls
        ``QGuiApplication.quit()``, which triggers ``aboutToQuit``), so this is
        safe to call more than once.

        Performs ZERO command actions: no facade call, no submit, no
        re-dispatch, no ``_kick_refresh``, no DB / broker / lock write.

        Returns ``True`` if the pool drained within the timeout.
        """
        drained = self._thread_pool.waitForDone(_SHUTDOWN_DRAIN_TIMEOUT_MS)
        self._disconnect_completed_signal()
        return drained

    def _disconnect_completed_signal(self) -> None:
        """Disconnect the async-completion signal if connected. Idempotent."""
        if not self._completed_connected:
            return
        try:
            self._submit_signals.completed.disconnect(self._on_async_submit_completed)
        except (RuntimeError, TypeError):
            pass
        self._completed_connected = False

    # ------------------------------------------------------------------ #
    # Read-only completion sink (P18). Recording is a list insert; it never
    # re-issues or acks a command, never touches the facade / broker / event
    # store / advisory locks, and never calls _kick_refresh.
    # ------------------------------------------------------------------ #

    def _emit_completion(self, payload: dict[str, Any]) -> None:
        """Single emit chokepoint for every submit completion.

        Records a compact, read-only display entry from *payload* and then
        re-emits the unmodified ``submitCompleted`` signal. Routing all three
        completion sites (sync, async, unknown-proposal error) through here
        guarantees every outcome is captured even when the modal — the only
        ``submitCompleted`` listener that renders inline feedback — is closed
        and drops the result.

        This method performs ZERO command actions: no facade call, no submit,
        no re-dispatch, no DB / broker / lock write. It mutates only the
        in-memory display list, then forwards the original payload.
        """
        self._record_completion(payload)
        self.submitCompleted.emit(payload)

    def _record_completion(self, payload: dict[str, Any]) -> None:
        durable_refs = payload.get("durable_refs") or {}
        strategy_id = durable_refs.get("strategy_id") or payload.get("strategy_id") or ""
        status = str(payload.get("status") or "")
        record = {
            "proposalId": str(payload.get("proposal_id") or ""),
            "strategyId": str(strategy_id),
            "actionFamily": str(payload.get("action_family") or ""),
            "status": status,
            "message": self._completion_message(payload, status),
            # Monotonic display ordering key. Python datetime is fine here —
            # this never crosses into the audit trail.
            "recordedAt": datetime.now(tz=UTC).isoformat(),
        }
        self._completions.insert(0, record)
        del self._completions[_MAX_RECENT_COMPLETIONS:]
        self.recentCompletionsChanged.emit()

    @staticmethod
    def _completion_message(payload: dict[str, Any], status: str) -> str:
        if status == "submitted":
            family = str(payload.get("action_family") or "command")
            return f"{family} submitted."
        blockers = payload.get("blockers") or []
        if blockers:
            first = blockers[0]
            message = first.get("message") if isinstance(first, dict) else None
            if message:
                return str(message)
        return "Submit did not complete."

    def _get_recent_completions(self) -> list[dict[str, Any]]:
        return list(self._completions)

    recentCompletions = Property(  # noqa: N815
        "QVariantList", _get_recent_completions, notify=recentCompletionsChanged
    )

    @Slot(str)
    def dismissCompletion(self, proposal_id: str) -> None:  # noqa: N802
        """Remove the display entry/entries for *proposal_id* from the banner.

        Display-only: dismisses the banner notice, it does NOT ack a command.
        Makes no facade call, no submit, no re-dispatch, no _kick_refresh, and
        no event-store / broker / lock write — it only mutates the read-only
        display list.
        """
        if not proposal_id:
            return
        before = len(self._completions)
        self._completions = [
            record for record in self._completions if record.get("proposalId") != proposal_id
        ]
        if len(self._completions) != before:
            self.recentCompletionsChanged.emit()

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
        self._emit_completion(payload)
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

        self._emit_completion(payload)
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
        self._emit_completion(payload)

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

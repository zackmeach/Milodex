"""Qt bridge over ``milodex.commands.bench.BenchCommandFacade``.

This module is the **only** file under ``src/milodex/gui/`` permitted to
import the Bench command facade (ADR 0051 §4 / §5). It translates
Q_INVOKABLE slot calls from QML into facade calls and translates the
result dataclasses into ``QVariant``-friendly dicts.

The bridge owns **no business rules**. Every decision — stage transitions,
evidence requirements, advisory-lock peek, governance refusals — is owned
by the facade and the modules it routes into (``milodex.promotion``,
``milodex.core.advisory_lock``, …). The bridge:

* exposes ``proposeDemote`` and ``submitDemote`` as QML-callable slots,
* caches the live ``CommandProposal`` between propose and submit so the
  proposal identity (``proposal_id``) survives the round-trip without QML
  needing to reconstruct dataclasses,
* refreshes the Bench read model on a successful submit (single permitted
  reach into ``_PollingReadModel._kick_refresh``), and
* emits a ``submitCompleted`` signal QML surfaces can listen to.

Backtest evidence generation is wired through ``proposeBacktest`` /
``submitBacktest``. Runner start/stop action families remain absent until
their respective wiring PRs land.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot

from milodex.commands.bench import (
    ACTION_FAMILY_BACKTEST,
    ACTION_FAMILY_DEMOTE,
    ACTION_FAMILY_FREEZE_MANIFEST,
    BenchCommandFacade,
    CommandProposal,
)

if TYPE_CHECKING:
    from milodex.gui.read_models import BenchState

logger = logging.getLogger(__name__)


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
    """Qt-side bridge to the Bench command facade (ADR 0051 Phase D1).

    The demotion / walk-back action family (Phase C2) and the freeze-manifest
    action family (Phase D1), and canonical backtest evidence generation
    are submit-capable. Every other action family
    remains preview-only and is *not* exposed here. The facade itself still
    defines ``submit_promote_to_paper`` /
    ``submit_start_paper_runner`` / ``submit_stop_paper_runner`` as Phase B
    stubs returning ``not_submit_capable_phase_b``, but the GUI cannot reach
    them through this bridge.
    """

    # Emitted after every submit attempt — successful, blocked, or errored.
    # Payload is the ``CommandResult.to_dict()`` mapping.
    submitCompleted = Signal("QVariantMap")  # noqa: N815

    def __init__(
        self,
        facade: BenchCommandFacade,
        *,
        bench_state: BenchState | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._facade = facade
        self._bench_state = bench_state
        # Proposal cache. propose_demote stores the live CommandProposal here
        # so submit_demote can retrieve it by id without QML reconstructing
        # the dataclass. Each proposal is consumed exactly once; stale ids
        # produce a structured error result.
        self._proposals: dict[str, CommandProposal] = {}

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
        if self._bench_state is None:
            return
        try:
            self._bench_state._kick_refresh()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            logger.exception("BenchState refresh after %s failed.", operation)

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
        proposal = self._proposals.pop(proposal_id, None)
        if proposal is None:
            error_payload: dict[str, Any] = {
                "proposal_id": proposal_id,
                "action_family": ACTION_FAMILY_DEMOTE,
                "status": "error",
                "durable_refs": {},
                "data": {},
                "blockers": [
                    {
                        "reason_code": "unknown_proposal_id",
                        "message": (
                            "submitDemote was called with an unknown proposal id. "
                            "Re-run proposeDemote and retry."
                        ),
                        "context": {"proposal_id": proposal_id},
                    }
                ],
                "warnings": [],
                "submitted_at": None,
                "audit_event_id": None,
            }
            self.submitCompleted.emit(error_payload)
            return error_payload

        result = self._facade.submit_demote(proposal, gui_submit=True)
        payload = result.to_dict()

        if result.status == "submitted" and self._bench_state is not None:
            # Single permitted private reach. `_kick_refresh` is the only
            # `BenchState` private surface the bridge touches; the contract is
            # documented here and pinned by
            # tests/milodex/gui/test_bench_command_bridge.py::
            # test_submit_demote_logs_when_kick_refresh_raises. If it raises,
            # the next polling tick recovers the read model on its own; we
            # log via `logger.exception` so the failure is auditable rather
            # than silently re-trying.
            try:
                self._bench_state._kick_refresh()  # noqa: SLF001
            except Exception:  # noqa: BLE001
                logger.exception("BenchState refresh after submit_demote failed.")

        self.submitCompleted.emit(payload)
        return payload

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
        proposal = self._proposals.pop(proposal_id, None)
        if proposal is None:
            error_payload: dict[str, Any] = {
                "proposal_id": proposal_id,
                "action_family": ACTION_FAMILY_FREEZE_MANIFEST,
                "status": "error",
                "durable_refs": {},
                "data": {},
                "blockers": [
                    {
                        "reason_code": "unknown_proposal_id",
                        "message": (
                            "submitFreezeManifest was called with an unknown "
                            "proposal id. Re-run proposeFreezeManifest and retry."
                        ),
                        "context": {"proposal_id": proposal_id},
                    }
                ],
                "warnings": [],
                "submitted_at": None,
                "audit_event_id": None,
            }
            self.submitCompleted.emit(error_payload)
            return error_payload

        result = self._facade.submit_freeze_manifest(proposal)
        payload = result.to_dict()

        if result.status == "submitted" and self._bench_state is not None:
            # Single permitted private reach (same contract as the demote
            # path). The next polling tick recovers the read model if this
            # raises; we log via `logger.exception` so the failure is
            # auditable. Pinned by
            # test_submit_freeze_manifest_logs_when_kick_refresh_raises.
            try:
                self._bench_state._kick_refresh()  # noqa: SLF001
            except Exception:  # noqa: BLE001
                logger.exception(
                    "BenchState refresh after submit_freeze_manifest failed."
                )

        self.submitCompleted.emit(payload)
        return payload

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
        initial_equity = float(inputs.get("initial_equity", 1_000.0))
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
        proposal = self._proposals.pop(proposal_id, None)
        if proposal is None:
            return self._unknown_proposal_payload(
                proposal_id,
                action_family=ACTION_FAMILY_BACKTEST,
                submit_method="submitBacktest",
                propose_method="proposeBacktest",
            )

        result = self._facade.submit_backtest(proposal)
        payload = result.to_dict()

        if result.status == "submitted":
            self._refresh_after_submit("submit_backtest")

        self.submitCompleted.emit(payload)
        return payload

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
        ]


def _date_from_qvariant(value: Any, default: date) -> date:
    if value in (None, ""):
        return default
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))

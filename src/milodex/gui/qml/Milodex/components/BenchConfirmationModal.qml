// BenchConfirmationModal.qml — Visual confirmation preview shell (PR K).
//
// ADR 0049 (Decision 2): Bench v1 is a visual prototype. This modal is a
// strict preview — it surfaces what a future confirmation flow will look
// like for state-changing Bench menu items (Promote / Demote / Return /
// Start Trading / Stop Trading / Initiate Backtest / Refresh Backtest),
// but its primary action is disabled and labelled "Not wired in v1".
// No backend command is dispatched along this path. No state is mutated.
//
// SUPERSESSION (ADR 0051, 2026-05-30): ADR 0051 narrowly supersedes ADR 0049
// for the wired Bench command families. See ADR 0051 for the authoritative
// current behavior; the ADR-0049 prototype description above is retained as
// historical context for this shell.
//
// PR 13 decompose: the seven inline body sub-components (SectionLabel,
// SectionRule, SafetyBanner, ProseBlock, RequirementRow, DetailRow) and the
// three repeated operator-input fields (LabeledTextField) were extracted to
// shared components/ files. The seven section INSTANCES (their literal
// labels/copy) and every bridge socket call stay INLINE here — those
// substrings are the doctrine perimeter the smoke tests pin. The seven
// dispatch functions share a propose→validate→route skeleton via
// _validateProposal / _finishSyncSubmit / _handleQueuedSubmit, keeping each
// family's bridge method names inline.
//
// P2-12: the per-action-kind SPEC (submit-capable kinds, intent copy,
// future-record labels, safety copy, capital classification, canonical
// backtest params) is Python-owned. The read model stamps every menu item
// with an actionIntentPreview built from bench_actions.ACTION_KIND_SPECS;
// the bridge fills canonical backtest params (CANONICAL_BACKTEST_PARAMS).
// This file consumes the preview and routes to explicit bridge sockets —
// it declares no kind tables and no fallback classifiers of its own.
//
// Layered on top of BenchModal.qml (the shared modal chrome) — this file
// adds the four things BenchModal does not provide:
//   - `open: bool` toggle with focus grab on open
//   - Escape key closes (Keys.onEscapePressed → closeRequested)
//   - Wheel events on the overlay are absorbed (don't leak under)
//   - A uniform `closeRequested()` signal that re-emits BenchModal.dismissed
//     so BenchSurface can wire one onCloseRequested handler.
//
// Visual treatment selection:
//   - Pattern B (oxblood, brand.accent top rule) for capital-bearing
//     transitions, as classified Python-side by the preview's
//     `capitalBearing` flag (bench_actions._is_capital_bearing).
//   - Pattern C (rust, status.negative top rule) for ordinary directional
//     / invocation previews.
//
// Public API:
//   property bool open                — show/hide
//   property var  rowData             — _StrategyRow.as_qml() dict
//   property var  actionData          — menu item dict (label, verbClass, targetStage)
//   signal closeRequested()           — emitted on Escape, ✕, outside-click, Cancel

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property bool open: false
    property var  rowData: ({})
    property var  actionData: ({})

    signal closeRequested()

    // ADR 0051 Phase C2: emitted after a successful demotion submit. The
    // surface listens to this so it can clear stale selection state once
    // the read-model refresh lands. The payload is the CommandResult dict.
    signal submitted(var result)

    // ADR 0051 Phase C2: emitted when a demotion submit is refused before
    // dispatch (blank reason, drifted stage, governance refusal). Carries
    // the structured blocker list so the surface can render reason codes
    // and human messages without parsing free text.
    signal submitBlocked(var blockers)

    // Submit-capability is Python-owned (P2-12): the read model stamps every
    // menu item's actionIntentPreview with `actionKind` and the spec-derived
    // submit-capability flag (`executable`, from
    // bench_actions.ACTION_KIND_SPECS via _is_submit_capable_action). QML
    // only ROUTES — the per-family booleans below pick which explicit bridge
    // socket the submit button dispatches to. Every non-submit-capable
    // action family still gets the inert "Not wired in v1" primary below.
    readonly property string _currentActionKind: _preview.actionKind || "unknown"
    readonly property bool _isDemoteSubmit: _currentActionKind === "demote"
    readonly property bool _isReturnToIdleSubmit: _currentActionKind === "return"
                                                  && ((actionData && actionData.targetStage) || "") === "idle"
    readonly property bool _isStageWalkbackSubmit: root._isDemoteSubmit || root._isReturnToIdleSubmit
    readonly property bool _isFreezeManifestSubmit: _currentActionKind === "freeze_manifest"
    readonly property bool _isBacktestSubmit: _currentActionKind === "initiate_backtest"
                                            || _currentActionKind === "refresh_backtest"
    readonly property bool _isStartPaperRunnerSubmit: _currentActionKind === "start_trading"
                                                      && ((rowData && rowData.stage) || "") === "paper"
    readonly property bool _isStopPaperRunnerSubmit: _currentActionKind === "stop_trading"
                                                     && ((rowData && rowData.stage) || "") === "paper"
    readonly property bool _isPromoteToPaperSubmit: _currentActionKind === "promote"
                                                  && ((actionData && actionData.targetStage) || "") === "paper"
    readonly property bool _isSubmitCapable: !!_preview.executable

    // Reason text the operator types into the inline input when the action
    // is submit-capable. The submit button is gated on non-blank reason so
    // the audit record always carries one.
    property string _reasonText: ""
    property string _recommendationText: ""
    property string _knownRiskText: ""
    property bool   _submitInFlight: false
    property string _submitErrorMessage: ""
    property string _pendingProposalId: ""

    // PR M (ADR 0049): prefer the normalized evidencePacket carried on
    // rowData when present, otherwise fall back to the flat rowData fields.
    // Read-only; never mutated here.
    readonly property var _packet:      (rowData && rowData.evidencePacket) || ({})
    readonly property var _pktMetrics:  _packet.metrics  || ({})
    readonly property var _pktEvidence: _packet.evidence || ({})
    readonly property var _pktStatus:   _packet.status   || ({})

    // PR N (ADR 0049) / P2-12: the normalized actionIntentPreview carried on
    // the menu item dict is the single source for the action kind, intent
    // copy, requirements, future-record label, safety copy, capital
    // classification, and submit-capability. Python guarantees the preview
    // on every menu item (bench_actions._compute_bench_action_menu); the
    // former QML fallback classifiers were removed. Read-only; never
    // mutated here.
    readonly property var _preview: (actionData && actionData.actionIntentPreview) || ({})

    // ------------------------------------------------------------------
    // PR O (ADR 0049): local command-draft preview
    //
    // The first explicit visual boundary between "the operator is reviewing
    // an intent" and "a future command could be submitted." Composed
    // locally in QML from the read-only evidencePacket + actionIntentPreview.
    //
    // This object MUST stay display-only:
    //   - `executable` is false
    //   - `wired` is false
    //   - `submissionState` is the literal "not_submittable_v1"
    //   - `validationState` is the literal "not_validated_v1"
    //
    // It is NOT a command-proposal object. It does NOT carry a payload. The
    // name is deliberately `commandDraftPreview` — "draft" + "preview" —
    // to keep the boundary visible to future readers who might be tempted
    // to graft a submit path onto this object.
    //
    // The `source.note` says explicitly: "No command is submitted, no event
    // is written, and no state is changed." The visible UI copy says
    // "Milodex can render this draft for review, but Bench v1 cannot
    // submit it."
    // ------------------------------------------------------------------
    readonly property string _COPY_DRAFT_SOURCE_NOTE: "This is a local UI draft preview only. No command is submitted, no event is written, and no state is changed."

    readonly property string _COPY_DRAFT_BANNER: "Milodex can render this draft for review, but Bench v1 cannot submit it."
    readonly property string _COPY_SUBMIT_CAPABLE_BANNER: "Milodex will validate this proposal through the command bridge before submitting it."

    readonly property var _draftBlockedBy: [
        "Bench v1 command submission is not wired",
        "Authoritative evidence reconstruction is deferred",
        "Risk/policy validation is not executed from the UI"
    ]

    readonly property var commandDraftPreview: ({
        "schemaVersion": 1,
        "source": {
            "kind": "local_ui_draft_preview",
            "authoritative": false,
            "note": _COPY_DRAFT_SOURCE_NOTE
        },
        "strategyId":   (_preview.strategyId   || (rowData && rowData.strategyId)   || ""),
        "strategyName": (_preview.strategyName || (rowData && (rowData.name || rowData.displayName)) || ""),
        "actionKind":   (_preview.actionKind   || ""),
        "actionLabel":  (_preview.actionLabel  || (actionData && actionData.label) || ""),
        "currentStage": (_preview.currentStage || (rowData && rowData.stage)      || ""),
        "targetStage":  (_preview.targetStage  || (actionData && actionData.targetStage) || ""),
        "evidencePacketSchemaVersion":      (_packet.schemaVersion  || 0),
        "actionIntentPreviewSchemaVersion": (_preview.schemaVersion || 0),
        "expectedFutureRecord": (_preview.futureRecord || "—"),
        "executable": root._isSubmitCapable,
        "wired": root._isSubmitCapable,
        "submissionState": root._isSubmitCapable ? "submit_capable" : "not_submittable_v1",
        "validationState": root._isSubmitCapable ? "validated_on_submit" : "not_validated_v1",
        "blockedBy": root._isSubmitCapable ? [] : _draftBlockedBy
    })

    visible: open
    focus: open

    onOpenChanged: {
        if (open) {
            // Clear per-open state so each operator session of the modal
            // starts with an empty reason input and no stale error string.
            // The reason input is only visible when _isSubmitCapable.
            root._reasonText = root._defaultReasonText()
            root._recommendationText = root._defaultRecommendationText()
            root._knownRiskText = root._defaultKnownRiskText()
            root._submitErrorMessage = ""
            root._submitInFlight = false
            root._pendingProposalId = ""
            forceActiveFocus()
        }
    }

    Connections {
        target: BenchCommandBridge
        function onSubmitCompleted(result) { root._handleAsyncSubmitCompleted(result) }
    }

    function _defaultReasonText() {
        if (!root._isStageWalkbackSubmit) {
            return ""
        }
        var label = (root.actionData && root.actionData.label) || "Stage walkback"
        return label + " via Bench GUI"
    }

    function _defaultRecommendationText() {
        if (!root._isPromoteToPaperSubmit) {
            return ""
        }
        return "Promote to paper from passing Bench backtest evidence."
    }

    function _defaultKnownRiskText() {
        if (!root._isPromoteToPaperSubmit) {
            return ""
        }
        return "Paper mode only; monitor live-feed behavior and stop if evidence drifts."
    }

    // Format EVERY blocker's operator-facing message as a refusal summary, so a
    // blocked proposal surfaces all reasons (not just the first) framed as a hard
    // refusal. The prior code showed only the first blocker's message, silently
    // dropping additional blockers — e.g. start_paper_runner refused with both
    // broker_unreachable AND reconciliation_drift only showed the first
    // (2026-05-29 "nothing happened" report). Falls back to `fallback` when empty.
    function _blockerSummary(blockers, fallback) {
        if (!blockers || blockers.length === 0)
            return fallback
        var lines = []
        for (var i = 0; i < blockers.length; i++) {
            var b = blockers[i] || ({})
            lines.push("• " + (b.message || b.reason_code || "Blocked."))
        }
        return "Blocked — not submitted:\n" + lines.join("\n")
    }

    function _handleAsyncSubmitCompleted(result) {
        if (!root.open || !result || result.proposal_id !== root._pendingProposalId) {
            return
        }

        root._pendingProposalId = ""
        root._submitInFlight = false

        if (result.status !== "submitted") {
            var msg = ""
            if (result.blockers && result.blockers.length > 0) {
                msg = root._blockerSummary(result.blockers, "Submit refused.")
            }
            root._submitErrorMessage = msg
            root.submitBlocked(result.blockers || [])
            return
        }

        root.submitted(result)
        root.closeRequested()
    }

    function _handleQueuedSubmit(result) {
        if (result && result.bridge_status === "queued") {
            root._submitErrorMessage = ""
            return true
        }

        root._pendingProposalId = ""
        root._submitInFlight = false
        var msg = ""
        if (result && result.blockers && result.blockers.length > 0) {
            msg = root._blockerSummary(result.blockers, "Submit refused.")
        }
        root._submitErrorMessage = msg || "Submit could not be queued."
        root.submitBlocked(result ? (result.blockers || []) : [])
        return false
    }

    // ------------------------------------------------------------------
    // Shared dispatch skeleton (PR 13).
    //
    // The seven action-family dispatch functions below all run the same
    // shape: extract+validate inputs → propose → validate the proposal →
    // route (sync submit | async queue). Only the inputs, the bridge
    // method names, and (for backtest) the canonical payload differ — and
    // those stay INLINE in each function so the socket-contract substrings
    // remain greppable in this file. The two common steps below carry NO
    // bridge method names; they only fold the duplicated proposal-blocker
    // and sync-result handling.
    // ------------------------------------------------------------------

    // Returns true when `proposal` is well-formed and unblocked. On a bad
    // proposal it sets the error string (and emits submitBlocked for the
    // blockers case), clears _submitInFlight, and returns false so the
    // caller bails before submit.
    function _validateProposal(proposal) {
        if (!proposal || !proposal.proposal_id) {
            root._submitInFlight = false
            root._submitErrorMessage = "Proposal could not be constructed."
            return false
        }
        if (proposal.blockers && proposal.blockers.length > 0) {
            root._submitInFlight = false
            root._submitErrorMessage = root._blockerSummary(proposal.blockers, "Proposal blocked.")
            root.submitBlocked(proposal.blockers)
            return false
        }
        return true
    }

    // Handle a synchronous submit result: on refusal set the error string +
    // emit submitBlocked; on success emit submitted + close. Clears
    // _submitInFlight either way.
    function _finishSyncSubmit(result) {
        root._submitInFlight = false
        if (!result || result.status !== "submitted") {
            var msg = ""
            if (result && result.blockers && result.blockers.length > 0) {
                msg = root._blockerSummary(result.blockers, "Submit refused.")
            }
            root._submitErrorMessage = msg
            root.submitBlocked(result ? (result.blockers || []) : [])
            return
        }
        root.submitted(result)
        root.closeRequested()
    }

    // ------------------------------------------------------------------
    // ADR 0051 Phase C2 — demotion submit dispatch
    //
    // The MouseArea on the demote-only primary button calls this function.
    // No other action family reaches the bridge through this modal.
    //
    // The function reads the operator-typed reason from `_reasonText`,
    // builds a proposal via BenchCommandBridge.proposeDemote, refuses if
    // the proposal carries blockers, otherwise calls submitDemote with the
    // proposal id. All governance decisions live on the Python side; this
    // function does not touch broker, event store, runner, or YAML.
    // ------------------------------------------------------------------
    function _dispatchDemoteSubmit() {
        if (!root._isStageWalkbackSubmit) return
        if (!root._isSubmitCapable) return
        if (root._submitInFlight) return

        var strategyId = (root.rowData && root.rowData.strategyId) || ""
        var targetStage = (root.actionData && root.actionData.targetStage) || ""
        var reason = root._reasonText
        if (!strategyId || !targetStage || !reason || !reason.trim().length) {
            root._submitErrorMessage = "Reason is required before submitting this stage change."
            return
        }

        root._submitErrorMessage = ""
        root._submitInFlight = true

        // approved_by is sourced backend-side by BenchCommandBridge
        // (_resolve_operator_identity) — QML does not decide identity.
        var proposal = BenchCommandBridge.proposeDemote({
            "strategy_id": strategyId,
            "to_stage": targetStage,
            "reason": reason
        })
        if (!root._validateProposal(proposal)) return

        var result = BenchCommandBridge.submitDemote(proposal.proposal_id)
        root._finishSyncSubmit(result)
    }

    // ------------------------------------------------------------------
    // Freeze-manifest submit (ADR 0051 Phase D1).
    //
    // Mirrors `_dispatchDemoteSubmit`. Freeze has no `reason` input — the
    // backend governance callee takes only the strategy config path and a
    // backend-sourced `frozen_by` (resolved by the bridge). QML must NOT
    // pass `frozen_by`.
    // ------------------------------------------------------------------
    function _dispatchFreezeManifestSubmit() {
        if (!root._isFreezeManifestSubmit) return
        if (root._submitInFlight) return

        var strategyId = (root.rowData && root.rowData.strategyId) || ""
        if (!strategyId) {
            root._submitErrorMessage = "Strategy id missing — cannot freeze manifest."
            return
        }

        root._submitErrorMessage = ""
        root._submitInFlight = true

        // frozen_by is sourced backend-side by BenchCommandBridge
        // (_resolve_operator_identity) — QML does not decide identity.
        var proposal = BenchCommandBridge.proposeFreezeManifest({
            "strategy_id": strategyId
        })
        if (!root._validateProposal(proposal)) return

        var result = BenchCommandBridge.submitFreezeManifest(proposal.proposal_id)
        root._finishSyncSubmit(result)
    }

    // ------------------------------------------------------------------
    // Canonical backtest submit (PR 13 / P2-12).
    //
    // Runs the evidence shape used by the strategy bank. QML supplies ONLY
    // the strategy id: the canonical walk-forward parameters are
    // Python-owned — CANONICAL_BACKTEST_PARAMS in bench_command_bridge.py
    // fills the proposal defaults, so the literal lives in exactly one
    // place. All validation and execution stays behind the Python command
    // bridge.
    // ------------------------------------------------------------------
    function _dispatchBacktestSubmit() {
        if (!root._isBacktestSubmit) return
        if (root._submitInFlight) return

        var strategyId = (root.rowData && root.rowData.strategyId) || ""
        if (!strategyId) {
            root._submitErrorMessage = "Strategy id missing — cannot run backtest."
            return
        }

        root._submitErrorMessage = ""
        root._submitInFlight = true

        var proposal = BenchCommandBridge.proposeBacktest({
            "strategy_id": strategyId
        })
        if (!root._validateProposal(proposal)) return

        root._pendingProposalId = proposal.proposal_id
        var result = BenchCommandBridge.submitBacktestAsync(proposal.proposal_id)
        root._handleQueuedSubmit(result)
    }

    // ------------------------------------------------------------------
    // Promote-to-paper submit.
    //
    // QML supplies only operator-authored evidence text and the current
    // evidence run id. The bridge supplies approved_by; the facade owns
    // gate checks, manifest writes, YAML stage mutation, and blocker shape.
    // ------------------------------------------------------------------
    function _dispatchPromoteToPaperSubmit() {
        if (!root._isPromoteToPaperSubmit) return
        if (root._submitInFlight) return

        var strategyId = (root.rowData && root.rowData.strategyId) || ""
        var runId = (root.rowData && root.rowData.evidenceRunId) || ""
        var recommendation = root._recommendationText
        var knownRisk = root._knownRiskText
        if (!strategyId || !recommendation || !recommendation.trim().length
                || !knownRisk || !knownRisk.trim().length) {
            root._submitErrorMessage = "Recommendation and known risk are required before promotion."
            return
        }

        root._submitErrorMessage = ""
        root._submitInFlight = true

        var proposal = BenchCommandBridge.proposePromoteToPaper({
            "strategy_id": strategyId,
            "recommendation": recommendation,
            "known_risk": knownRisk,
            "run_id": runId,
            "lifecycle_exempt": false
        })
        if (!root._validateProposal(proposal)) return

        var result = BenchCommandBridge.submitPromoteToPaper(proposal.proposal_id)
        root._finishSyncSubmit(result)
    }

    // ------------------------------------------------------------------
    // Paper runner controls (ADR 0051 Phase F).
    //
    // Start launches the existing paper runner asynchronously through the
    // bridge/facade boundary. Stop writes a controlled-stop request for the
    // runner to consume between cycles. This is deliberately not a kill
    // switch path; QML does not touch broker, runner, event store, or YAML.
    // ------------------------------------------------------------------
    function _dispatchStartPaperRunnerSubmit() {
        if (!root._isStartPaperRunnerSubmit) return
        if (root._submitInFlight) return

        var strategyId = (root.rowData && root.rowData.strategyId) || ""
        if (!strategyId) {
            root._submitErrorMessage = "Strategy id missing — cannot start paper runner."
            return
        }

        root._submitErrorMessage = ""
        root._submitInFlight = true

        var proposal = BenchCommandBridge.proposeStartPaperRunner({
            "strategy_id": strategyId
        })
        if (!root._validateProposal(proposal)) return

        root._pendingProposalId = proposal.proposal_id
        var result = BenchCommandBridge.submitStartPaperRunnerAsync(proposal.proposal_id)
        root._handleQueuedSubmit(result)
    }

    function _dispatchStopPaperRunnerSubmit() {
        if (!root._isStopPaperRunnerSubmit) return
        if (root._submitInFlight) return

        var strategyId = (root.rowData && root.rowData.strategyId) || ""
        if (!strategyId) {
            root._submitErrorMessage = "Strategy id missing — cannot stop paper runner."
            return
        }

        root._submitErrorMessage = ""
        root._submitInFlight = true

        var proposal = BenchCommandBridge.proposeStopPaperRunner({
            "strategy_id": strategyId
        })
        if (!root._validateProposal(proposal)) return

        root._pendingProposalId = proposal.proposal_id
        var result = BenchCommandBridge.submitStopPaperRunnerAsync(proposal.proposal_id)
        root._handleQueuedSubmit(result)
    }

    // Top-level dispatcher: routes the submit button click to the right
    // action-family dispatch function. Adding a new submit-capable action
    // family means adding one branch here and one spec entry in
    // bench_actions.ACTION_KIND_SPECS (Python owns submit-capability).
    function _dispatchSubmit() {
        if (root._isDemoteSubmit || root._isReturnToIdleSubmit) {
            _dispatchDemoteSubmit()
        } else if (root._isFreezeManifestSubmit) {
            _dispatchFreezeManifestSubmit()
        } else if (root._isBacktestSubmit) {
            _dispatchBacktestSubmit()
        } else if (root._isPromoteToPaperSubmit) {
            _dispatchPromoteToPaperSubmit()
        } else if (root._isStartPaperRunnerSubmit) {
            _dispatchStartPaperRunnerSubmit()
        } else if (root._isStopPaperRunnerSubmit) {
            _dispatchStopPaperRunnerSubmit()
        }
    }

    Keys.onEscapePressed: (event) => {
        if (open) {
            event.accepted = true
            root.closeRequested()
        }
    }

    // Absorb any wheel events that hit the overlay so they cannot bubble
    // to the Bench Flickable underneath. (The Flickable's WheelHandler is
    // also gated on activeModal !== "none", but absorbing here is belt &
    // braces against ordering surprises.)
    WheelHandler {
        target: null
        onWheel: (event) => { event.accepted = true }
    }

    // ------------------------------------------------------------------
    // Helpers — visual classification + copy selection
    // ------------------------------------------------------------------

    // Capital-bearing classification is Python-owned (P2-12):
    // bench_actions._is_capital_bearing stamps the preview's capitalBearing
    // flag, including the PR L refinement that paper-stage Start Trading is
    // NOT capital-bearing (live feed, no capital exposure).
    readonly property bool _isCapitalBoundary: !!_preview.capitalBearing

    readonly property string _verbClass: {
        if (actionData && actionData.verbClass) return actionData.verbClass
        return ""
    }

    // Top-rule color per BenchModal Pattern B (oxblood) vs Pattern C (rust).
    readonly property color _topBorderColor:
        _isCapitalBoundary ? Theme.color.brand.accent : Theme.status.negative

    readonly property string _eyebrow:
        _isCapitalBoundary ? "CAPITAL-BEARING TRANSITION" : "CONFIRMATION PREVIEW"

    readonly property color _eyebrowColor:
        _isCapitalBoundary ? Theme.color.brand.accent : Theme.color.text.muted

    readonly property string _title: {
        var label = (actionData && actionData.label) ? actionData.label : "(no action)"
        var name = (rowData && (rowData.name || rowData.strategyId)) || "(strategy)"
        return label + " — " + name
    }

    // Mandatory verbatim copy strings — single-line literals so static
    // grep-based safety tests can match them substring-exactly.
    readonly property string _COPY_DIRECTIONAL: "This preview shows the confirmation Milodex will require before changing a strategy's Bench stage. Command execution is not wired in Bench v1."

    readonly property string _COPY_INVOCATION: "This confirmation sends operational process requests through the Bench command bridge. Paper runner start and controlled stop are validated again before submit."

    readonly property string _COPY_CAPITAL_LOCK: "Capital-bearing transitions remain locked while ADR 0004 is in force. This modal is a visual shell only."

    readonly property string _proseBase: {
        if (_verbClass === "directional") return _COPY_DIRECTIONAL
        if (_verbClass === "invocation") return _COPY_INVOCATION
        return _COPY_DIRECTIONAL  // safe default; only informational reaches Evidence path
    }

    readonly property string _prose:
        _isCapitalBoundary ? (_proseBase + "\n\n" + _COPY_CAPITAL_LOCK) : _proseBase

    // ------------------------------------------------------------------
    // PR L / P2-12: Intent Packet content.
    //
    // All per-action-kind copy (intent prose, requirements enumeration,
    // future-record label, safety-boundary copy) is Python-owned —
    // bench_actions.ACTION_KIND_SPECS et al. pre-render it onto the
    // actionIntentPreview. The strings describe what a future Milodex
    // *would* validate / record — they are never evidence that a strategy
    // actually passes or that an event has been written. The sections
    // below bind `_preview.*` directly; no QML fallback tables remain.
    // ------------------------------------------------------------------

    // Static enumeration of what a future Milodex would validate before
    // this action could proceed. Copy-only — rendered verbatim from the
    // Python-owned preview (bench_actions._ACTION_REQUIREMENTS).
    readonly property var _requirements: _preview.requirements || []

    // ------------------------------------------------------------------
    // Formatting helpers — delegate to Formatters singleton (PR10).
    // ------------------------------------------------------------------

    function _or(v)        { return Formatters.orDash(v) }
    function _fmtSharpe(v) { return Formatters.sharpe(v) }
    function _fmtPct(v)    { return Formatters.pct1(v) }
    function _fmtInt(v)    { return Formatters.count(v) }

    // ------------------------------------------------------------------
    // Shared chrome — BenchModal owns overlay / box / eyebrow / title /
    // prose / footer separator / action row. Body slot is our content;
    // action slot is our footer buttons.
    // ------------------------------------------------------------------

    BenchModal {
        id: shell
        anchors.fill: parent

        topBorderColor: root._topBorderColor
        eyebrowText:    root._eyebrow
        eyebrowColor:   root._eyebrowColor
        titleText:      root._title
        // PR L: prose owned by SAFETY BOUNDARY section in the body slot.
        // BenchModal hides the prose Text when proseText is empty.
        proseText:      ""

        onDismissed: root.closeRequested()

        // ---- Body: six-section structured Intent Packet ----------------
        Column {
            width: parent.width
            spacing: Theme.space[3]

            // ============================================================
            // 1. ACTION
            // ============================================================
            SectionLabel { label: "ACTION"; ordinal: "01" }

            DetailRow { label: "Action";        value: root._or(root.actionData.label) }
            DetailRow { label: "Verb class";    value: root._or(root.actionData.verbClass) }
            DetailRow { label: "Current stage"; value: root._or(root.rowData.stage) }
            DetailRow {
                visible: !!(root.actionData && root.actionData.targetStage)
                label: "Target stage"
                value: root._or(root.actionData.targetStage)
            }
            DetailRow { label: "Strategy ID";   value: root._or(root.rowData.strategyId) }
            DetailRow { label: "Strategy name"; value: root._or(root.rowData.name) }

            SectionRule {}

            // ============================================================
            // 2. INTENT PACKET
            // ============================================================
            SectionLabel { label: "INTENT PACKET"; ordinal: "02" }

            ProseBlock { text: root._preview.intentCopy || "" }

            SectionRule {}

            // ============================================================
            // 3. CURRENT SNAPSHOT  (existing rowData only)
            // ============================================================
            SectionLabel { label: "CURRENT SNAPSHOT"; ordinal: "03" }

            DetailRow {
                label: "Sharpe"
                numeric: true
                value: root._fmtSharpe(root._pktMetrics.sharpe !== undefined
                                       ? root._pktMetrics.sharpe
                                       : root.rowData.sharpe)
            }
            DetailRow {
                label: "Max drawdown"
                numeric: true
                value: root._fmtPct(root._pktMetrics.maxDrawdownPct !== undefined
                                    ? root._pktMetrics.maxDrawdownPct
                                    : root.rowData.maxDrawdownPct)
            }
            DetailRow {
                label: "Trade count"
                numeric: true
                value: root._fmtInt(root._pktMetrics.tradeCount !== undefined
                                    ? root._pktMetrics.tradeCount
                                    : root.rowData.tradeCount)
            }
            DetailRow {
                label: "Status"
                value: {
                    var w = (root._pktStatus.word !== undefined
                             ? root._pktStatus.word
                             : (root.rowData && root.rowData.statusWord)) || ""
                    var t = (root._pktStatus.tail !== undefined
                             ? root._pktStatus.tail
                             : (root.rowData && root.rowData.statusTail)) || ""
                    if (w.length && t.length) return w + " — " + t
                    if (w.length || t.length) return w + t
                    return "—"
                }
            }
            DetailRow {
                label: "Evidence run"
                labelWidth: 116
                value: root._or(root._pktEvidence.runId || root.rowData.evidenceRunId)
            }
            DetailRow {
                label: "Evidence label"
                labelWidth: 116
                value: root._or(root._pktEvidence.label || root.rowData.metaEvidenceLabel)
            }
            DetailRow {
                label: "Evidence at"
                labelWidth: 116
                value: root._or(root._pktEvidence.observedAt || root.rowData.metaEvidenceAt)
            }

            SectionRule {}

            // ============================================================
            // 4. WOULD EVENTUALLY REQUIRE  (copy-only; no real check)
            // ============================================================
            SectionLabel { label: "WOULD EVENTUALLY REQUIRE"; ordinal: "04" }

            Repeater {
                model: root._requirements
                delegate: RequirementRow {
                    required property string modelData
                    text: modelData
                }
            }

            SectionRule {}

            // ============================================================
            // 5. FUTURE RECORD  (display string only; not a class, not a payload)
            // ============================================================
            SectionLabel { label: "FUTURE RECORD"; ordinal: "05" }

            DetailRow { label: "Record kind"; value: root._preview.futureRecord || "—" }

            SectionRule {}

            // ============================================================
            // 6. COMMAND DRAFT PREVIEW  (PR O — display-only boundary)
            //
            // Renders the local `commandDraftPreview` object. NO submission,
            // NO dispatch, NO state change. The visible banner makes the
            // boundary explicit; the rows below show the future-command
            // shape so the operator knows what would be assembled.
            //
            // PR 5 (post-DESIGN.md v0.2): the "NOT SUBMITTABLE" banner is
            // rendered in the sober typeset treatment specified by
            // DESIGN_SYSTEM.md v0.2 §7.6 — small-caps red bounded by
            // hairline rules above and below. NOT a yellow alert bar.
            // NOT a rotated rubber-stamp graphic. The italic prose below
            // the banner is the verbatim ADR 0049 _COPY_DRAFT_BANNER
            // sentence.
            // ============================================================
            SectionLabel { label: "COMMAND DRAFT PREVIEW"; ordinal: "06" }

            SafetyBanner { text: root._isSubmitCapable ? "SUBMIT CAPABLE" : "NOT SUBMITTABLE" }
            ProseBlock {
                text: root._isSubmitCapable
                      ? root._COPY_SUBMIT_CAPABLE_BANNER
                      : root._COPY_DRAFT_BANNER
            }

            DetailRow {
                label: "Submission state"
                labelWidth: 124
                value: root.commandDraftPreview.submissionState
            }
            DetailRow {
                label: "Validation state"
                labelWidth: 124
                value: root.commandDraftPreview.validationState
            }
            DetailRow {
                label: "Expected record"
                labelWidth: 124
                value: root.commandDraftPreview.expectedFutureRecord
            }
            DetailRow {
                label: "Evidence packet v"
                labelWidth: 124
                numeric: true
                value: "" + root.commandDraftPreview.evidencePacketSchemaVersion
            }
            DetailRow {
                label: "Action preview v"
                labelWidth: 124
                numeric: true
                value: "" + root.commandDraftPreview.actionIntentPreviewSchemaVersion
            }
            DetailRow {
                label: "Executable"
                labelWidth: 124
                value: root.commandDraftPreview.executable ? "true" : "false"
            }
            DetailRow {
                label: "Wired"
                labelWidth: 124
                value: root.commandDraftPreview.wired ? "true" : "false"
            }

            // Blocked-by list — quiet ledger rows, not an alert block.
            Repeater {
                model: root.commandDraftPreview.blockedBy
                delegate: RequirementRow {
                    required property string modelData
                    text: modelData
                }
            }

            SectionRule {}

            // ============================================================
            // 7. SAFETY BOUNDARY  (always present; appended capital copy)
            // ============================================================
            SectionLabel { label: "SAFETY BOUNDARY"; ordinal: "07" }

            ProseBlock { text: root._preview.safetyCopy || "" }

            // ============================================================
            // 8. DEMOTION SUBMIT (ADR 0051 Phase C2 — submit-capable branch)
            //
            // Visible ONLY when the action is a stage walkback. Collects the
            // required reason and surfaces submit/refuse status. Every
            // other submit-capable family bypasses this section.
            // Freeze-manifest and backtest have no reason concept, so they
            // skip this block.
            // ============================================================
            SectionLabel {
                visible: root._isStageWalkbackSubmit
                label: "OPERATOR INPUT"
                ordinal: "08"
            }

            ProseBlock {
                text: "The reason below is recorded with the append-only promotion event so the audit log can be reconstructed without re-asking the operator."
            }

            LabeledTextField {
                visible: root._isStageWalkbackSubmit
                placeholderText: "Reason required for the audit record"
                text: root._reasonText
                focusBorderColor: Theme.status.negative
                selectedTextColor: Theme.color.text.primary
                onTextEdited: (t) => root._reasonText = t
            }

            SectionLabel {
                visible: root._isPromoteToPaperSubmit
                label: "OPERATOR EVIDENCE"
                ordinal: "08"
            }

            ProseBlock {
                visible: root._isPromoteToPaperSubmit
                text: "Promotion to paper records the operator recommendation and known risk alongside the selected backtest evidence run."
            }

            LabeledTextField {
                visible: root._isPromoteToPaperSubmit
                placeholderText: "Recommendation required"
                text: root._recommendationText
                focusBorderColor: Theme.color.text.primary
                selectedTextColor: Theme.color.surface.base
                onTextEdited: (t) => root._recommendationText = t
            }

            LabeledTextField {
                visible: root._isPromoteToPaperSubmit
                placeholderText: "Known risk required"
                text: root._knownRiskText
                focusBorderColor: Theme.color.text.primary
                selectedTextColor: Theme.color.surface.base
                onTextEdited: (t) => root._knownRiskText = t
            }

            Text {
                visible: root._isSubmitCapable && root._submitErrorMessage.length > 0
                width: parent.width
                text: root._submitErrorMessage
                color: Theme.status.negative
                font.family:    Theme.typography.data.sm.family
                font.pixelSize: Theme.typography.data.sm.size
                font.features:  Theme.typography.data.sm.features
                wrapMode: Text.WordWrap
            }
        }

        // ---- Footer actions -------------------------------------------
        // PR 5 (post-DESIGN.md v0.2): Cancel is rendered as the `ghost`
        // variant per DESIGN_SYSTEM.md v0.2 §7.1 + §7.6 ("for safety-
        // critical surfaces critical + ghost cancel is the correct
        // pairing"). No border chrome, no filled hover; hover is a
        // text-color shift only.
        //
        // The primary button below remains exactly as-is: explicitly
        // disabled, no MouseArea, verbatim contract comment intact.
        // Per ADR 0049 Decision 2 and the contract comment, those
        // properties MUST NOT change in this PR. PR 5 polishes the
        // interior only; the submission-state plumbing is sacred.
        actionContent: [
            // Cancel — closes the modal. Ghost variant: no chrome.
            Item {
                implicitWidth:  cancelLabel.implicitWidth + Theme.space[4] * 2
                implicitHeight: 36

                Text {
                    id: cancelLabel
                    anchors.centerIn: parent
                    text:  "Cancel"
                    color: cancelMa.containsMouse
                           ? Theme.color.text.primary
                           : Theme.color.text.secondary
                    font.family:    Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size
                    font.weight:    Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                    Behavior on color { ColorAnimation { duration: Theme.motion.fast } }
                }

                MouseArea {
                    id: cancelMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape:  Qt.PointingHandCursor
                    onClicked:    root.closeRequested()
                }
            },

            // Primary — action-aware. ADR 0051 Phase C2 opens the demote
            // action family for end-to-end submit. Every other action family
            // routes to the inert "Not wired in v1" placeholder below.
            //
            // The verbatim "Not wired in v1" string and the inert visual
            // remain in this file (visible only when !_isSubmitCapable) so
            // operators viewing a non-demote action see the same boundary
            // copy ADR 0049 established for v1. Forbidden-token tests in
            // tests/milodex/gui/test_qml_load_smoke.py allow exactly one
            // MouseArea here (the demote submit) and continue to reject
            // broker / event-store / direct config writes.

            // Inert primary — preserved for non-demote action families
            // (Promote / Return / Start Trading / Stop Trading / Initiate
            // Backtest / Refresh Backtest). Visible only when the current
            // action is not submit-capable.
            Item {
                visible: !root._isSubmitCapable
                implicitWidth:  primaryLabel.implicitWidth + Theme.space[4] * 2
                implicitHeight: 36

                Rectangle {
                    anchors.fill: parent
                    color:        Theme.color.surface.raised
                    border.color: Theme.color.border.subtle
                    border.width: 1
                    radius:       Theme.radius.md
                    opacity:      0.55
                }

                Text {
                    id: primaryLabel
                    anchors.centerIn: parent
                    text:  "Not wired in v1"
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size
                    font.weight:    Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                // No MouseArea — these action families remain preview-only
                // until their respective wiring PRs land (ADR 0051 Phases
                // D / E / F). Adding a dispatch here without an ADR
                // amendment + the corresponding facade submit method is a
                // contract bug.
            },

            // Submit-capable primary — demote (Phase C2) and
            // freeze_manifest (Phase D1) action families. The MouseArea
            // routes through the bridge's per-family propose + submit
            // slots — the same callee path the CLI uses. This Item never
            // reaches the event store, broker, runner, or YAML directly;
            // every governance decision (gate, stage, audit) lives behind
            // the Python facade.
            //
            // Label, color tone, and reason-gate are action-family-aware:
            // - demote: red, "Confirm demotion", reason required.
            // - freeze_manifest: muted, "Confirm freeze", no reason input.
            Item {
                id: submitDemoteItem
                visible: root._isSubmitCapable
                implicitWidth:  submitLabel.implicitWidth + Theme.space[4] * 2
                implicitHeight: 36

                readonly property color _submitTone:
                    root._isDemoteSubmit ? Theme.status.negative
                                         : Theme.color.text.primary

                Rectangle {
                    anchors.fill: parent
                    color: submitMa.containsMouse && submitMa.enabled
                           ? submitDemoteItem._submitTone
                           : Theme.color.surface.raised
                    border.color: submitDemoteItem._submitTone
                    border.width: 1
                    radius:       Theme.radius.md
                    opacity: submitMa.enabled ? 1.0 : 0.55
                    Behavior on color { ColorAnimation { duration: Theme.motion.fast } }
                }

                Text {
                    id: submitLabel
                    anchors.centerIn: parent
                    text: {
                        if (root._submitInFlight) return "Submitting…"
                        if (root._isPromoteToPaperSubmit) return "Confirm promotion"
                        if (root._isStartPaperRunnerSubmit) return "Start paper runner"
                        if (root._isStopPaperRunnerSubmit) return "Request stop"
                        if (root._isBacktestSubmit) return "Run backtest"
                        if (root._isFreezeManifestSubmit) return "Confirm freeze"
                        if (root._isReturnToIdleSubmit) return "Return to idle"
                        return "Confirm demotion"
                    }
                    color: submitMa.containsMouse && submitMa.enabled
                           ? Theme.color.text.primary
                           : submitDemoteItem._submitTone
                    font.family:    Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size
                    font.weight:    Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                MouseArea {
                    id: submitMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    // Demote requires a non-blank reason for the audit
                    // record; freeze_manifest and backtest have no reason concept.
                    enabled: !root._submitInFlight
                             && (root._isFreezeManifestSubmit
                                 || root._isBacktestSubmit
                                 || root._isStartPaperRunnerSubmit
                                 || root._isStopPaperRunnerSubmit
                                 || (root._isPromoteToPaperSubmit
                                     && root._recommendationText.trim().length > 0
                                     && root._knownRiskText.trim().length > 0)
                                 || (root._isStageWalkbackSubmit
                                     && root._reasonText.trim().length > 0))
                    onClicked: root._dispatchSubmit()
                }
            }
        ]
    }
}

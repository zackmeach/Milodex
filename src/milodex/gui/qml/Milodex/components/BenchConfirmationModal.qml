// BenchConfirmationModal.qml — Visual confirmation preview shell (PR K).
//
// ADR 0049 (Decision 2): Bench v1 is a visual prototype. This modal is a
// strict preview — it surfaces what a future confirmation flow will look
// like for state-changing Bench menu items (Promote / Demote / Return /
// Start Trading / Stop Trading / Initiate Backtest / Refresh Backtest),
// but its primary action is disabled and labelled "Not wired in v1".
// No backend command is dispatched along this path. No state is mutated.
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
//     transitions: when `actionData.targetStage` or `actionData.label`
//     references "micro_live" / "live" / "Micro Live" / "Live", or when
//     the label is "Start Trading" (capital becomes deployable).
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

    // Submit affordance is *only* enabled for the demote action family.
    // Every other action family still routes to the inert "Not wired in v1"
    // primary button below. The kind comes from the action-intent preview
    // when present, otherwise from the action label's prefix.
    readonly property bool _isSubmitCapable: {
        return _actionKind(actionData) === "demote"
    }

    // Reason text the operator types into the inline input when the action
    // is submit-capable. The submit button is gated on non-blank reason so
    // the audit record always carries one.
    property string _reasonText: ""
    property bool   _submitInFlight: false
    property string _submitErrorMessage: ""

    // PR M (ADR 0049): prefer the normalized evidencePacket carried on
    // rowData when present, otherwise fall back to the flat rowData fields.
    // Read-only; never mutated here.
    readonly property var _packet:      (rowData && rowData.evidencePacket) || ({})
    readonly property var _pktMetrics:  _packet.metrics  || ({})
    readonly property var _pktEvidence: _packet.evidence || ({})
    readonly property var _pktStatus:   _packet.status   || ({})

    // PR N (ADR 0049): prefer the normalized actionIntentPreview carried on
    // the menu item dict when present. The QML helpers below remain as a
    // fallback path so the modal survives a future read-model rebuild that
    // temporarily omits the preview. Read-only; never mutated here.
    readonly property var  _preview:           (actionData && actionData.actionIntentPreview) || ({})
    readonly property bool _previewAvailable:  !!(actionData && actionData.actionIntentPreview)

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
        "executable": false,
        "wired": false,
        "submissionState": "not_submittable_v1",
        "validationState": "not_validated_v1",
        "blockedBy": _draftBlockedBy
    })

    visible: open
    focus: open

    onOpenChanged: {
        if (open) {
            // Clear per-open state so each operator session of the modal
            // starts with an empty reason input and no stale error string.
            // The reason input is only visible when _isSubmitCapable.
            root._reasonText = ""
            root._submitErrorMessage = ""
            root._submitInFlight = false
            forceActiveFocus()
        }
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
        if (!root._isSubmitCapable) return
        if (root._submitInFlight) return

        var strategyId = (root.rowData && root.rowData.strategyId) || ""
        var targetStage = (root.actionData && root.actionData.targetStage) || ""
        var reason = root._reasonText
        if (!strategyId || !targetStage || !reason || !reason.trim().length) {
            root._submitErrorMessage = "Reason is required before submitting a demotion."
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

        if (!proposal || !proposal.proposal_id) {
            root._submitInFlight = false
            root._submitErrorMessage = "Proposal could not be constructed."
            return
        }

        if (proposal.blockers && proposal.blockers.length > 0) {
            root._submitInFlight = false
            root._submitErrorMessage = proposal.blockers[0].message || "Proposal blocked."
            root.submitBlocked(proposal.blockers)
            return
        }

        var result = BenchCommandBridge.submitDemote(proposal.proposal_id)
        root._submitInFlight = false

        if (!result || result.status !== "submitted") {
            var msg = ""
            if (result && result.blockers && result.blockers.length > 0) {
                msg = result.blockers[0].message || "Submit refused."
            }
            root._submitErrorMessage = msg
            root.submitBlocked(result ? (result.blockers || []) : [])
            return
        }

        root.submitted(result)
        root.closeRequested()
    }

    Keys.onEscapePressed: (event) => {
        if (open) {
            event.accepted = true
            root.closeRequested()
        }
    }

    // Absorb any wheel events that hit the overlay so they cannot bubble
    // to the Bench Flickable underneath. (The Flickable's WheelHandler is
    // also gated on confirmationPreviewOpen, but absorbing here is belt &
    // braces against ordering surprises.)
    WheelHandler {
        target: null
        onWheel: (event) => { event.accepted = true }
    }

    // ------------------------------------------------------------------
    // Helpers — visual classification + copy selection
    // ------------------------------------------------------------------

    // PR L: paper-stage Start Trading is NOT a capital-bearing transition
    // (live feed, no capital exposure). Only flag capital-bearing when
    // either the targetStage explicitly lands on micro_live/live, the label
    // mentions Live/Micro Live, OR Start Trading is invoked while already
    // at micro_live/live. See _safetyCopy for the matching prose branches.
    readonly property bool _isCapitalBoundary: {
        // PR N: prefer the normalized preview's capitalBearing flag.
        if (_previewAvailable && _preview.capitalBearing !== undefined) {
            return !!_preview.capitalBearing
        }
        // Fallback (matches PR L semantics, including paper-stage Start
        // Trading refinement).
        var label = (actionData && actionData.label) ? actionData.label : ""
        var target = (actionData && actionData.targetStage) ? actionData.targetStage : ""
        var stage = (rowData && rowData.stage) ? rowData.stage : ""
        if (target === "micro_live" || target === "live") return true
        if (label.indexOf("Micro Live") >= 0) return true
        if (label.indexOf("Live") >= 0) return true
        if (label === "Start Trading") {
            return stage === "micro_live" || stage === "live"
        }
        return false
    }

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

    readonly property string _COPY_INVOCATION: "This preview shows the confirmation Milodex will require before starting, stopping, initiating, or refreshing an operational process. Command execution is not wired in Bench v1."

    readonly property string _COPY_CAPITAL_LOCK: "Capital-bearing transitions remain locked while ADR 0004 is in force. This modal is a visual shell only."

    readonly property string _proseBase: {
        if (_verbClass === "directional") return _COPY_DIRECTIONAL
        if (_verbClass === "invocation") return _COPY_INVOCATION
        return _COPY_DIRECTIONAL  // safe default; only informational reaches Evidence path
    }

    readonly property string _prose:
        _isCapitalBoundary ? (_proseBase + "\n\n" + _COPY_CAPITAL_LOCK) : _proseBase

    // ------------------------------------------------------------------
    // PR L: Intent Packet helpers
    //
    // Pure presentational copy selection. No side effects, no I/O, no
    // event-store calls, no eligibility computation. The strings below
    // describe what a future Milodex *would* validate / record — they are
    // never evidence that a strategy actually passes or that an event has
    // been written. Wording must not imply real freshness, real gate
    // evaluation, real risk approval, or command readiness.
    // ------------------------------------------------------------------

    // Coarse verb classification from the action label. Promote/Demote/
    // Return are prefix-matched (multiple target-stage suffixes exist);
    // the four invocation labels are fixed strings.
    //
    // PR N: when an `actionIntentPreview` is available on the action dict,
    // prefer its normalized `actionKind`. The fallback path below preserves
    // PR L semantics for any future read-model rebuild that omits the
    // preview.
    function _actionKind(action) {
        if (action && action.actionIntentPreview && action.actionIntentPreview.actionKind) {
            return action.actionIntentPreview.actionKind
        }
        var label = (action && action.label) ? action.label : ""
        if (label.indexOf("Promote to ") === 0)  return "promote"
        if (label.indexOf("Demote to ") === 0)   return "demote"
        if (label.indexOf("Return to ") === 0)   return "return"
        if (label === "Start Trading")           return "start_trading"
        if (label === "Stop Trading")            return "stop_trading"
        if (label === "Initiate Backtest")       return "initiate_backtest"
        if (label === "Refresh Backtest")        return "refresh_backtest"
        return "unknown"
    }

    // Plain-language explanation of what the selected action means.
    // PR N: prefer `actionIntentPreview.intentCopy` when present.
    function _intentCopy(action) {
        if (action && action.actionIntentPreview && action.actionIntentPreview.intentCopy) {
            return action.actionIntentPreview.intentCopy
        }
        var kind = _actionKind(action)
        if (kind === "promote")
            return "Move this strategy forward from its current stage to the next stage after evidence and policy gates are satisfied."
        if (kind === "demote")
            return "Move this strategy backward to an earlier stage and remove it from its current operating stage."
        if (kind === "return")
            return "Restore this strategy to a previously eligible stage or return it to the idle shelf."
        if (kind === "start_trading")
            return "Start an operational session for this strategy at its current stage. In Bench v1 this is preview-only."
        if (kind === "stop_trading")
            return "Stop the current operational session for this strategy. In Bench v1 this is preview-only."
        if (kind === "initiate_backtest")
            return "Request new backtest evidence for this strategy. In Bench v1 this is preview-only."
        if (kind === "refresh_backtest")
            return "Refresh aging or stale backtest evidence for this strategy. In Bench v1 this is preview-only."
        return "Action not recognised by the intent packet renderer."
    }

    // Static enumeration of what a future Milodex would validate before
    // this action could proceed. Copy-only — no real check is performed here.
    //
    // PR N: when the preview carries a `requirements` list, prefer it. The
    // QML literal below remains as a fallback path.
    readonly property var _requirements: {
        if (_previewAvailable
                && _preview.requirements
                && _preview.requirements.length !== undefined) {
            return _preview.requirements
        }
        return [
            "Evidence gate check",
            "Freshness check",
            "Operator confirmation",
            "Policy lock check",
            "Risk guard check",
            "Event write after confirmation"
        ]
    }

    // Non-executable display string identifying the kind of record the
    // future event-store will write. NOT a class name, NOT a function,
    // NOT a payload — purely a label for operator orientation.
    // PR N: prefer the preview's `futureRecord` field when present.
    function _futureRecord(action) {
        if (action && action.actionIntentPreview && action.actionIntentPreview.futureRecord) {
            return action.actionIntentPreview.futureRecord
        }
        var kind = _actionKind(action)
        if (kind === "promote")            return "promotion_event"
        if (kind === "demote")             return "demotion_event"
        if (kind === "return")             return "stage_return_event"
        if (kind === "start_trading")      return "session_start_event"
        if (kind === "stop_trading")       return "session_stop_event"
        if (kind === "initiate_backtest")  return "backtest_request_event"
        if (kind === "refresh_backtest")   return "backtest_refresh_event"
        return "—"
    }

    // SAFETY BOUNDARY copy. Always opens with the boundary sentence; appends
    // capital-bearing language when the action crosses ADR 0004 territory.
    // Three branches:
    //   1. capital-bearing transition (target or label implies live capital)
    //   2. Start Trading at paper stage (live feed, no capital exposure)
    //   3. everything else — bare boundary sentence
    readonly property string _COPY_SAFETY_BOUNDARY: "Bench v1 renders this intent packet for review only. No command is submitted, no event is written, and no state is changed."
    readonly property string _COPY_CAPITAL_LOCK_SHORT: "Capital-bearing transitions remain locked while ADR 0004 is in force."
    readonly property string _COPY_PAPER_START: "Paper-stage sessions use live feed with no capital exposure. Capital-bearing stages remain locked while ADR 0004 is in force."

    function _safetyCopy(rowDataIn, action) {
        // PR N: prefer the preview's pre-rendered safety copy when present.
        if (action && action.actionIntentPreview && action.actionIntentPreview.safetyCopy) {
            return action.actionIntentPreview.safetyCopy
        }
        var base = _COPY_SAFETY_BOUNDARY
        var label = (action && action.label) ? action.label : ""
        var stage = (rowDataIn && rowDataIn.stage) ? rowDataIn.stage : ""
        if (label === "Start Trading" && stage === "paper") {
            return base + "\n\n" + _COPY_PAPER_START
        }
        if (_isCapitalBoundary) {
            return base + "\n\n" + _COPY_CAPITAL_LOCK_SHORT
        }
        return base
    }

    // ------------------------------------------------------------------
    // Formatting helpers
    // ------------------------------------------------------------------

    function _or(v) {
        if (v === undefined || v === null) return "—"
        if (typeof v === "string" && v.length === 0) return "—"
        return v
    }
    function _fmtSharpe(v) {
        if (v === undefined || v === null) return "—"
        return ("+" + Number(v).toFixed(2)).replace("+-", "-")
    }
    function _fmtPct(v) {
        if (v === undefined || v === null) return "—"
        return Number(v).toFixed(1) + "%"
    }
    function _fmtInt(v) {
        if (v === undefined || v === null) return "—"
        if (v === 0) return "0"
        return "" + v
    }

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

            ProseBlock { text: root._intentCopy(root.actionData) }

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

            DetailRow { label: "Record kind"; value: root._futureRecord(root.actionData) }

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

            SafetyBanner { text: "NOT SUBMITTABLE" }
            ProseBlock { text: root._COPY_DRAFT_BANNER }

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

            ProseBlock { text: root._safetyCopy(root.rowData, root.actionData) }

            // ============================================================
            // 8. DEMOTION SUBMIT (ADR 0051 Phase C2 — submit-capable branch)
            //
            // Visible ONLY when the action is a demote. Collects the
            // required reason and surfaces submit/refuse status. Every
            // other action family bypasses this section and renders the
            // existing "Not wired in v1" inert primary action below.
            // ============================================================
            SectionLabel {
                visible: root._isSubmitCapable
                label: "OPERATOR INPUT"
                ordinal: "08"
            }

            ProseBlock {
                visible: root._isSubmitCapable
                text: "Demotion is the first Bench action family wired end-to-end. The reason below is recorded with the append-only promotion event so the audit log can be reconstructed without re-asking the operator."
            }

            Rectangle {
                visible: root._isSubmitCapable
                width: parent.width
                implicitHeight: reasonField.implicitHeight + Theme.space[3] * 2
                color: Theme.color.surface.raised
                border.color: reasonField.activeFocus ? Theme.status.negative : Theme.color.border.subtle
                border.width: 1
                radius: Theme.radius.md

                TextInput {
                    id: reasonField
                    anchors.fill: parent
                    anchors.margins: Theme.space[3]
                    text: root._reasonText
                    color: Theme.color.text.primary
                    selectByMouse: true
                    selectionColor: Theme.status.negative
                    selectedTextColor: Theme.color.text.primary
                    clip: true
                    font.family:    Theme.typography.data.xs.family
                    font.pixelSize: Theme.typography.data.xs.size
                    font.features:  Theme.typography.data.xs.features
                    onTextChanged: root._reasonText = text

                    Text {
                        visible: reasonField.text.length === 0
                        anchors.fill: parent
                        verticalAlignment: Text.AlignVCenter
                        text: "Reason required for the audit record"
                        color: Theme.color.text.disabled
                        font.family:    Theme.typography.data.xs.family
                        font.pixelSize: Theme.typography.data.xs.size
                        font.features:  Theme.typography.data.xs.features
                        font.italic: true
                    }
                }
            }

            Text {
                visible: root._isSubmitCapable && root._submitErrorMessage.length > 0
                width: parent.width
                text: root._submitErrorMessage
                color: Theme.status.negative
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                font.italic: true
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

            // Submit-capable primary — demote action family only (ADR 0051
            // Phase C2). The MouseArea routes through the bridge's
            // proposeDemote + submitDemote slots — the same callee path the
            // CLI uses. This Item never reaches the event store, broker,
            // runner, or YAML directly; every governance decision (gate,
            // stage, audit) lives behind the Python facade.
            Item {
                id: submitDemoteItem
                visible: root._isSubmitCapable
                implicitWidth:  submitLabel.implicitWidth + Theme.space[4] * 2
                implicitHeight: 36

                Rectangle {
                    anchors.fill: parent
                    color: submitMa.containsMouse && submitMa.enabled
                           ? Theme.status.negative
                           : Theme.color.surface.raised
                    border.color: Theme.status.negative
                    border.width: 1
                    radius:       Theme.radius.md
                    opacity: submitMa.enabled ? 1.0 : 0.55
                    Behavior on color { ColorAnimation { duration: Theme.motion.fast } }
                }

                Text {
                    id: submitLabel
                    anchors.centerIn: parent
                    text:  root._submitInFlight ? "Submitting…" : "Confirm demotion"
                    color: submitMa.containsMouse && submitMa.enabled
                           ? Theme.color.text.primary
                           : Theme.status.negative
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
                    enabled: !root._submitInFlight
                             && root._reasonText.trim().length > 0
                    onClicked: root._dispatchDemoteSubmit()
                }
            }
        ]
    }

    // ------------------------------------------------------------------
    // PR L: Inline sub-components for the Intent Packet body
    // ------------------------------------------------------------------

    // Quiet ALL-CAPS section label — used to mark each of the seven packet
    // sections. Same typographic register as BenchEvidenceModal's section
    // headers but rendered in muted color (not brand.accent) per PR L
    // visual direction ("structured, not decorative").
    //
    // Polish: optional `ordinal` prefix renders as a small mono numeral
    // (`01`, `02`, …) ahead of the label so the seven-section structure
    // reads as a deliberate progression rather than a flat list. Extra
    // top breathing room (Theme.space[3]) separates sections visually
    // without needing heavier rules.
    component SectionLabel: Item {
        id: sectionLabelRoot
        property string label: ""
        property string ordinal: ""
        width: parent ? parent.width : 0
        implicitHeight: sectionText.implicitHeight + Theme.space[3]

        Row {
            anchors.bottom: parent.bottom
            spacing: Theme.space[2]

            Text {
                visible: sectionLabelRoot.ordinal.length > 0
                text:  sectionLabelRoot.ordinal
                color: Theme.color.text.disabled
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
            }

            Text {
                id: sectionText
                text:  sectionLabelRoot.label
                color: Theme.color.text.muted
                font.family:         Theme.typography.label.xs.family
                font.pixelSize:      Theme.typography.label.xs.size
                font.weight:         Font.DemiBold
                font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                font.capitalization: Font.AllUppercase
            }
        }
    }

    // Section separator hairline — consistent with PR K body separators.
    component SectionRule: Rectangle {
        width: parent ? parent.width : 0
        height: 1
        color: Theme.color.border.subtle
    }

    // Safety banner — sober typeset stamp, NOT a yellow alert bar or a
    // rotated rubber-stamp graphic. Per DESIGN_SYSTEM.md v0.2 §7.6's
    // confirmation-modal interior contract: "Safety banner (when
    // applicable): small-caps typography.label.xs red set in a band
    // bounded by hairline rules above and below, color status.negative.
    // Never a yellow alert bar and never a decorative rubber-stamp
    // graphic."
    //
    // Used by COMMAND DRAFT PREVIEW to render "NOT SUBMITTABLE" as a
    // typeset stamp band before the verbatim ADR 0049 banner copy.
    component SafetyBanner: Column {
        id: safetyBannerRoot
        property string text: ""
        width: parent ? parent.width : 0
        spacing: 0

        Rectangle {
            width:  parent.width
            height: 1
            color:  Theme.status.negative
        }

        Item {
            width:  parent.width
            implicitHeight: bannerText.implicitHeight + Theme.space[3] * 2

            Text {
                id: bannerText
                anchors.centerIn: parent
                text:  safetyBannerRoot.text
                color: Theme.status.negative
                font.family:         Theme.typography.label.xs.family
                font.pixelSize:      Theme.typography.label.xs.size
                font.weight:         Font.DemiBold
                font.letterSpacing:  Theme.typography.label.xs.letterSpacing + 0.6
                font.capitalization: Font.AllUppercase
            }
        }

        Rectangle {
            width:  parent.width
            height: 1
            color:  Theme.status.negative
        }
    }

    // Multi-line prose block — used by INTENT PACKET and SAFETY BOUNDARY.
    component ProseBlock: Text {
        width: parent ? parent.width : 0
        color: Theme.color.text.secondary
        font.family:    Theme.typography.deck.family
        font.pixelSize: Theme.typography.deck.size
        font.italic:    true
        wrapMode:       Text.WordWrap
    }

    // Single bullet/glyph row inside WOULD EVENTUALLY REQUIRE.
    component RequirementRow: RowLayout {
        property string text: ""
        width: parent ? parent.width : 0
        spacing: Theme.space[2]

        Text {
            text:  "·"
            color: Theme.color.text.muted
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
        }

        Text {
            text:  parent.text
            color: Theme.color.text.secondary
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
    }

    // Single label/value row (PR K original).
    //
    // Polish: label column narrowed 132 → 96 px so short labels ("Sharpe",
    // "Wired", "Stage") sit closer to their values; the eye no longer
    // crosses a wide gutter for every read. `numeric: true` right-aligns
    // the value so columns of numbers (Sharpe / Max-DD / schema versions)
    // read as a tabular stack instead of a left-aligned string list.
    //
    // `labelWidth` overrides the 96 px default for sections whose labels
    // are visibly longer ("Submission state", "Evidence packet v", etc.) —
    // applied per-row at the call site so the tight default still holds
    // wherever it is visibly safe.
    component DetailRow: RowLayout {
        property string label: ""
        property string value: ""
        property bool   numeric: false
        property real   labelWidth: 96
        width: parent ? parent.width : 0
        spacing: Theme.space[3]

        Text {
            text: label
            color: Theme.color.text.muted
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            Layout.preferredWidth: labelWidth
        }

        Text {
            text: value
            color: Theme.color.text.secondary
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            Layout.fillWidth: true
            horizontalAlignment: numeric ? Text.AlignRight : Text.AlignLeft
            elide: Text.ElideRight
        }
    }
}

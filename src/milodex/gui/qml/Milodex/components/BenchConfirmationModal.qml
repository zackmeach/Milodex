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

    visible: open
    focus: open

    onOpenChanged: {
        if (open) forceActiveFocus()
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
            SectionLabel { label: "ACTION" }

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
            SectionLabel { label: "INTENT PACKET" }

            ProseBlock { text: root._intentCopy(root.actionData) }

            SectionRule {}

            // ============================================================
            // 3. CURRENT SNAPSHOT  (existing rowData only)
            // ============================================================
            SectionLabel { label: "CURRENT SNAPSHOT" }

            DetailRow {
                label: "Sharpe"
                value: root._fmtSharpe(root._pktMetrics.sharpe !== undefined
                                       ? root._pktMetrics.sharpe
                                       : root.rowData.sharpe)
            }
            DetailRow {
                label: "Max drawdown"
                value: root._fmtPct(root._pktMetrics.maxDrawdownPct !== undefined
                                    ? root._pktMetrics.maxDrawdownPct
                                    : root.rowData.maxDrawdownPct)
            }
            DetailRow {
                label: "Trade count"
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
                value: root._or(root._pktEvidence.runId || root.rowData.evidenceRunId)
            }
            DetailRow {
                label: "Evidence label"
                value: root._or(root._pktEvidence.label || root.rowData.metaEvidenceLabel)
            }
            DetailRow {
                label: "Evidence at"
                value: root._or(root._pktEvidence.observedAt || root.rowData.metaEvidenceAt)
            }

            SectionRule {}

            // ============================================================
            // 4. WOULD EVENTUALLY REQUIRE  (copy-only; no real check)
            // ============================================================
            SectionLabel { label: "WOULD EVENTUALLY REQUIRE" }

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
            SectionLabel { label: "FUTURE RECORD" }

            DetailRow { label: "Record kind"; value: root._futureRecord(root.actionData) }

            SectionRule {}

            // ============================================================
            // 6. SAFETY BOUNDARY  (always present; appended capital copy)
            // ============================================================
            SectionLabel { label: "SAFETY BOUNDARY" }

            ProseBlock { text: root._safetyCopy(root.rowData, root.actionData) }
        }

        // ---- Footer actions -------------------------------------------
        actionContent: [
            // Cancel — closes the modal.
            Item {
                implicitWidth:  cancelLabel.implicitWidth + Theme.space[4] * 2
                implicitHeight: 36

                Rectangle {
                    anchors.fill: parent
                    color: cancelMa.containsMouse
                           ? Theme.color.surface.raised
                           : "transparent"
                    border.color: Theme.color.border.regular
                    border.width: 1
                    radius: Theme.radius.md
                    Behavior on color { ColorAnimation { duration: Theme.motion.fast } }
                }

                Text {
                    id: cancelLabel
                    anchors.centerIn: parent
                    text:  "Cancel"
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size
                    font.weight:    Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                MouseArea {
                    id: cancelMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape:  Qt.PointingHandCursor
                    onClicked:    root.closeRequested()
                }
            },

            // Primary — explicitly disabled. NEVER wire an onClicked handler
            // here that calls into any backend, BenchState, broker, event
            // store, or config write. ADR 0049 Decision 2 holds.
            Item {
                implicitWidth:  primaryLabel.implicitWidth + Theme.space[4] * 2
                implicitHeight: 36

                Rectangle {
                    anchors.fill: parent
                    // Disabled visual: muted fill, hairline border, no hover.
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

                // Intentionally no MouseArea — there is no clickable target.
                // A future PR that wires real commands MUST add the MouseArea
                // AND its dispatch AND remove this comment AND update
                // tests/milodex/gui/test_qml_load_smoke.py — failure to
                // touch all four is a contract bug.
            }
        ]
    }

    // ------------------------------------------------------------------
    // PR L: Inline sub-components for the Intent Packet body
    // ------------------------------------------------------------------

    // Quiet ALL-CAPS section label — used to mark each of the six packet
    // sections. Same typographic register as BenchEvidenceModal's section
    // headers but rendered in muted color (not brand.accent) per PR L
    // visual direction ("structured, not decorative").
    component SectionLabel: Item {
        property string label: ""
        width: parent ? parent.width : 0
        implicitHeight: sectionText.implicitHeight + Theme.space[1]

        Text {
            id: sectionText
            anchors.bottom: parent.bottom
            text:  parent.label
            color: Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Font.DemiBold
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
    }

    // Section separator hairline — consistent with PR K body separators.
    component SectionRule: Rectangle {
        width: parent ? parent.width : 0
        height: 1
        color: Theme.color.border.subtle
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
    component DetailRow: RowLayout {
        property string label: ""
        property string value: ""
        width: parent ? parent.width : 0
        spacing: Theme.space[3]

        Text {
            text: label
            color: Theme.color.text.muted
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            Layout.preferredWidth: 132
        }

        Text {
            text: value
            color: Theme.color.text.secondary
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
    }
}

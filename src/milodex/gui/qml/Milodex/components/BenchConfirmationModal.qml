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

    readonly property bool _isCapitalBoundary: {
        var label = (actionData && actionData.label) ? actionData.label : ""
        var target = (actionData && actionData.targetStage) ? actionData.targetStage : ""
        if (target === "micro_live" || target === "live") return true
        if (label.indexOf("Micro Live") >= 0) return true
        if (label.indexOf("Live") >= 0) return true
        if (label === "Start Trading") return true
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
        proseText:      root._prose

        onDismissed: root.closeRequested()

        // ---- Body: row + action details grid --------------------------
        Column {
            width: parent.width
            spacing: Theme.space[3]

            DetailRow { label: "Strategy ID";   value: root._or(root.rowData.strategyId) }
            DetailRow { label: "Current stage"; value: root._or(root.rowData.stage) }

            // Separator
            Rectangle {
                width: parent.width
                height: 1
                color: Theme.color.border.subtle
            }

            DetailRow { label: "Action";        value: root._or(root.actionData.label) }
            DetailRow { label: "Verb class";    value: root._or(root.actionData.verbClass) }
            DetailRow {
                visible: !!(root.actionData && root.actionData.targetStage)
                label: "Target stage"
                value: root._or(root.actionData.targetStage)
            }

            // Separator
            Rectangle {
                width: parent.width
                height: 1
                color: Theme.color.border.subtle
            }

            DetailRow { label: "Sharpe";        value: root._fmtSharpe(root.rowData.sharpe) }
            DetailRow { label: "Max drawdown";  value: root._fmtPct(root.rowData.maxDrawdownPct) }
            DetailRow { label: "Trade count";   value: root._fmtInt(root.rowData.tradeCount) }
            DetailRow {
                visible: !!(root.rowData && root.rowData.statusWord)
                label: "Status"
                value: {
                    var w = root.rowData.statusWord || ""
                    var t = root.rowData.statusTail || ""
                    if (w.length && t.length) return w + " — " + t
                    return w + t
                }
            }

            // Footer note — reinforces visual-shell scope.
            Item { width: 1; height: Theme.space[1] }

            Text {
                width: parent.width
                text:  "This modal is a visual shell only — no confirmation, "
                     + "no dispatch, no state mutation. Real command wiring is "
                     + "deferred."
                color: Theme.color.text.muted
                font.family:    Theme.typography.deck.family
                font.pixelSize: Theme.typography.deck.size
                font.italic:    true
                wrapMode:       Text.WordWrap
            }
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
    // Inline sub-component — single label/value row
    // ------------------------------------------------------------------

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

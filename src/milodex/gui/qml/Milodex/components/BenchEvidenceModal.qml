// BenchEvidenceModal.qml — Read-only Evidence snapshot for a Bench row.
//
// ADR 0049 (Decision 2): Bench v1 is a visual prototype. This modal is
// strictly read-only — it surfaces what the GUI read-model already exposes
// for a strategy, with no event-store reconstruction, no freshness
// computation, no mutation. The mandatory footer disclaimer below must
// remain verbatim so the operator never mistakes this snapshot for an
// authoritative gate result.
//
// Public API:
//   property bool open                — show/hide the overlay
//   property var  rowData             — as_qml() dict from _StrategyRow
//   signal closeRequested()           — emitted on ✕, Escape, or outside-click
//
// BenchSurface owns exactly one instance of this modal and toggles `open`
// in response to BenchRow's `evidenceRequested` signal. BenchRow MUST NOT
// instantiate this modal per-row.

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

    signal closeRequested()

    // PR M (ADR 0049): prefer the normalized evidencePacket when present.
    // Fall back to flat rowData fields so the modal remains resilient if a
    // future read-model rebuild omits the packet.  The packet is read-only
    // and never mutated here.
    readonly property var _packet:        (rowData && rowData.evidencePacket) || ({})
    readonly property var _pktMetrics:    _packet.metrics  || ({})
    readonly property var _pktEvidence:   _packet.evidence || ({})
    readonly property var _pktGate:       _packet.gate     || ({})
    readonly property var _pktStatus:     _packet.status   || ({})
    readonly property var _pktSession:    _packet.session  || ({})
    readonly property var _pktJob:        _packet.job      || ({})
    readonly property var _pktSource:     _packet.source   || ({})

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

    // ------------------------------------------------------------------
    // Tinted backdrop — full-surface MouseArea dismisses on outside-click
    // ------------------------------------------------------------------

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(Theme.color.surface.canvas.r,
                       Theme.color.surface.canvas.g,
                       Theme.color.surface.canvas.b,
                       0.82)

        MouseArea {
            anchors.fill: parent
            onClicked: {
                if (root.open) root.closeRequested()
            }
        }
    }

    // ------------------------------------------------------------------
    // Modal box — centered evidence packet
    // ------------------------------------------------------------------

    Rectangle {
        id: box
        anchors.centerIn: parent
        width:  Math.min(parent.width  - Theme.space[6] * 2, 640)
        height: Math.min(parent.height - Theme.space[6] * 2, 640)

        color:        Theme.color.surface.base
        radius:       Theme.radius.lg
        border.color: Theme.color.border.regular
        border.width: 1

        // Swallow clicks on the box (don't dismiss)
        MouseArea { anchors.fill: parent }

        // -- Brass/oxblood top rule (evidence-packet feel) --
        Rectangle {
            anchors.top:    parent.top
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.topMargin:    1
            anchors.leftMargin:   1
            anchors.rightMargin:  1
            height: 2
            color:  Theme.color.brand.accent
        }

        // -- Header: eyebrow + strategy name + close glyph --
        Item {
            id: headerBlock
            anchors.top:    parent.top
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.topMargin:    Theme.space[6]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[5]
            implicitHeight: headerContent.implicitHeight

            Column {
                id: headerContent
                anchors.left:        parent.left
                anchors.right:       closeGlyph.left
                anchors.rightMargin: Theme.space[3]
                spacing: Theme.space[1]

                Text {
                    text: "EVIDENCE SNAPSHOT"
                    color: Theme.color.text.muted
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                Text {
                    width: parent.width
                    text:  root._packet.strategyName
                           || root.rowData.name
                           || root.rowData.displayName
                           || root._packet.strategyId
                           || root.rowData.strategyId
                           || "(unnamed strategy)"
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.display.sm.family
                    font.pixelSize: Theme.typography.display.sm.size
                    font.weight:    Theme.typography.display.sm.weight
                    elide:          Text.ElideRight
                }
            }

            // Close glyph — top-right, plain text button
            Text {
                id: closeGlyph
                anchors.right:          parent.right
                anchors.verticalCenter: headerContent.verticalCenter
                text:  "✕"   // ✕
                color: closeMa.containsMouse
                       ? Theme.color.text.primary
                       : Theme.color.text.muted
                font.family:    Theme.typography.label.xs.family
                font.pixelSize: 14
                Behavior on color { ColorAnimation { duration: Theme.motion.fast } }

                MouseArea {
                    id: closeMa
                    anchors.fill: parent
                    anchors.margins: -8
                    hoverEnabled: true
                    cursorShape:  Qt.PointingHandCursor
                    onClicked:    root.closeRequested()
                }
            }
        }

        // Hairline below header
        Rectangle {
            id: headerRule
            anchors.top:    headerBlock.bottom
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.topMargin:    Theme.space[4]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[6]
            height: 1
            color:  Theme.color.border.regular
        }

        // -- Scrollable field block --
        Flickable {
            id: scrollArea
            anchors.top:    headerRule.bottom
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.bottom: footerBlock.top
            anchors.topMargin:    Theme.space[4]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[6]
            anchors.bottomMargin: Theme.space[3]
            contentWidth:  width
            contentHeight: fieldsCol.implicitHeight
            clip: true
            flickableDirection: Flickable.VerticalFlick

            WheelHandler {
                target: null
                onWheel: (event) => {
                    var max = Math.max(0, scrollArea.contentHeight - scrollArea.height)
                    var step = event.angleDelta.y / 120 * 40
                    scrollArea.contentY = Math.max(0, Math.min(max, scrollArea.contentY - step))
                    event.accepted = true
                }
            }

            Column {
                id: fieldsCol
                width: parent.width
                spacing: Theme.space[2]

                // ---- IDENTITY ----
                EvidenceSection { label: "IDENTITY" }
                EvidenceField {
                    label: "Strategy ID"
                    value: root._or(root._packet.strategyId || root.rowData.strategyId)
                }
                EvidenceField {
                    label: "Stage"
                    value: root._or(root._packet.currentStage || root.rowData.stage)
                }
                EvidenceField { label: "Family";       value: root._or(root.rowData.family) }
                EvidenceField { label: "Template";     value: root._or(root.rowData.template) }
                EvidenceField {
                    label: "Description"
                    value: root._or(root.rowData.description)
                    multiLine: true
                }

                // ---- SOURCE ----
                EvidenceSection { label: "SOURCE" }
                EvidenceField { label: "Kind";          value: root._or(root._pktSource.kind) }
                EvidenceField { label: "Authoritative"; value: root._fmtBool(root._pktSource.authoritative) }
                EvidenceField {
                    label: "Source note"
                    value: root._or(root._pktSource.note)
                    multiLine: true
                }

                // ---- GATE METRICS ----
                EvidenceSection { label: "GATE METRICS" }
                EvidenceField {
                    label: "Sharpe"
                    value: root._fmtSharpe(root._pktMetrics.sharpe !== undefined
                                           ? root._pktMetrics.sharpe
                                           : root.rowData.sharpe)
                }
                EvidenceField {
                    label: "Max drawdown"
                    value: root._fmtPct(root._pktMetrics.maxDrawdownPct !== undefined
                                        ? root._pktMetrics.maxDrawdownPct
                                        : root.rowData.maxDrawdownPct)
                }
                EvidenceField {
                    label: "Trade count"
                    value: root._fmtInt(root._pktMetrics.tradeCount !== undefined
                                        ? root._pktMetrics.tradeCount
                                        : root.rowData.tradeCount)
                }
                EvidenceField {
                    label: "Evidence run"
                    value: root._or(root._pktEvidence.runId || root.rowData.evidenceRunId)
                }
                EvidenceField {
                    label: "Gate failures"
                    value: root._fmtList(root._pktGate.failures || root.rowData.gateFailures)
                    multiLine: true
                }
                EvidenceField {
                    label: "Freshness"
                    value: root._or(root._pktGate.freshness)
                }
                EvidenceField {
                    label: "Gate result"
                    value: root._or(root._pktGate.gateResult)
                }

                // ---- EVIDENCE TIMESTAMPS ----
                EvidenceSection { label: "EVIDENCE TIMESTAMPS" }
                EvidenceField {
                    label: "Meta label"
                    value: root._or(root._pktEvidence.label || root.rowData.metaEvidenceLabel)
                }
                EvidenceField {
                    label: "Meta at"
                    value: root._or(root._pktEvidence.observedAt || root.rowData.metaEvidenceAt)
                }
                EvidenceField {
                    label: "Promoted at"
                    value: root._or(root._pktEvidence.promotedAt || root.rowData.promotedAt)
                }
                EvidenceField {
                    label: "Promotion type"
                    value: root._or(root._pktEvidence.promotionType || root.rowData.promotionType)
                }

                // ---- STATUS ----
                EvidenceSection { label: "STATUS" }
                EvidenceField {
                    label: "Status word"
                    value: root._or(root._pktStatus.word || root.rowData.statusWord)
                }
                EvidenceField {
                    label: "Status detail"
                    value: root._or(root._pktStatus.tail || root.rowData.statusTail)
                    multiLine: true
                }

                // ---- SESSION & JOB ----
                EvidenceSection { label: "SESSION & JOB" }
                EvidenceField {
                    label: "Session state"
                    value: root._or(root._pktSession.state || root.rowData.sessionState)
                }
                EvidenceField {
                    label: "Session detail"
                    value: root._or(root._pktSession.detail || root.rowData.sessionDetail)
                    multiLine: true
                }
                EvidenceField {
                    label: "Job status"
                    value: root._or(root._pktJob.status || root.rowData.jobStatus)
                }
                EvidenceField {
                    label: "Job action"
                    value: root._or(root._pktJob.actionType || root.rowData.jobActionType)
                }
                EvidenceField {
                    label: "Job detail"
                    value: root._or(root._pktJob.detail || root.rowData.jobDetail)
                    multiLine: true
                }

                // ---- AVAILABLE BENCH ACTIONS (informational only in v1) ----
                EvidenceSection { label: "AVAILABLE BENCH ACTIONS" }

                Text {
                    width: parent.width
                    text:  "These are the menu items the Bench currently offers for this row. "
                         + "In v1 only Open Evidence has wired behavior; the rest are informational placeholders."
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.italic:    true
                    wrapMode:       Text.WordWrap
                }

                Repeater {
                    model: root.rowData.actions || []

                    delegate: EvidenceField {
                        required property var modelData
                        label: modelData.verbClass || "—"
                        value: modelData.label || "—"
                    }
                }

                Item { width: 1; height: Theme.space[3] }
            }
        }

        // -- Footer: mandatory disclaimer --
        Item {
            id: footerBlock
            anchors.bottom: parent.bottom
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.bottomMargin: Theme.space[5]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[6]
            implicitHeight: footerRule.height + Theme.space[3] + footerText.implicitHeight

            Rectangle {
                id: footerRule
                anchors.top:    parent.top
                anchors.left:   parent.left
                anchors.right:  parent.right
                height: 1
                color:  Theme.color.border.regular
            }

            Text {
                id: footerText
                anchors.top:        footerRule.bottom
                anchors.left:       parent.left
                anchors.right:      parent.right
                anchors.topMargin:  Theme.space[3]
                // MANDATORY verbatim — do not reword.
                text:  "Bench v1 evidence is read-only and sourced from the current GUI read-model snapshot. Real event-derived freshness and gate reconstruction are deferred."
                color: Theme.color.text.muted
                font.family:    Theme.typography.deck.family
                font.pixelSize: Theme.typography.deck.size
                font.italic:    true
                wrapMode:       Text.WordWrap
            }
        }
    }

    // ------------------------------------------------------------------
    // Formatting helpers (pure JS — no side effects)
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
    function _fmtList(v) {
        if (!v || !v.length) return "—"
        return v.join(", ")
    }
    function _fmtBool(v) {
        if (v === undefined || v === null) return "—"
        return v ? "true" : "false"
    }

    // ------------------------------------------------------------------
    // Inline sub-components — section header and field row
    // ------------------------------------------------------------------

    component EvidenceSection: Item {
        property string label: ""
        width: parent ? parent.width : 0
        implicitHeight: sectionLabel.implicitHeight + Theme.space[2]

        Text {
            id: sectionLabel
            anchors.bottom: parent.bottom
            text:  label
            color: Theme.color.brand.accent
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Font.DemiBold
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
    }

    component EvidenceField: Item {
        property string label: ""
        property string value: ""
        property bool   multiLine: false
        width: parent ? parent.width : 0
        implicitHeight: fieldRow.implicitHeight

        RowLayout {
            id: fieldRow
            width: parent.width
            spacing: Theme.space[3]

            Text {
                text:  label
                color: Theme.color.text.muted
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                Layout.preferredWidth: 132
                Layout.alignment:      Qt.AlignTop
            }

            Text {
                text:  value
                color: Theme.color.text.secondary
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                Layout.fillWidth: true
                wrapMode: multiLine ? Text.WordWrap : Text.NoWrap
                elide:    multiLine ? Text.ElideNone : Text.ElideRight
            }
        }
    }
}

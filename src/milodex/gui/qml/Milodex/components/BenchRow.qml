// BenchRow.qml — Strategy row for the Bench surface.
//
// Distinct from StrategyRow.qml: that component is for the existing
// tabular strategy bank with a single-line strategy ID + status pill +
// metric columns.  BenchRow is for the editorial Bench surface — name
// in display weight + ID below, status carried as italic prose with
// one inline colored signal word, action button on the right edge.
//
// Tokens consumed:
//   color.surface.base    — default row background (transparent default)
//   color.surface.raised  — hover background
//   color.brand.accent    — LIVE-row left border + tint
//   color.text.primary    — strategy name (display.sm Newsreader)
//                         — numeric metrics
//   color.text.secondary  — IDLE-stage strategy name (visually quieter)
//                         — italic prose body
//   color.text.muted      — strategy ID, meta line under prose
//   color.text.disabled   — em-dash placeholders on IDLE rows
//   status.positive/.warning/.info/.negative — inline signal-word colors
//   typography.display.sm — strategy name
//   typography.data.md    — strategy ID + numeric metrics (mono tabular)
//   typography.data.sm    — meta line under prose
//   typography.body.md    — italic prose status
//   space[2, 3, 5]        — row padding / inter-column gap
//   motion.fast           — hover transition
//
// Public API:
//   strategyName    : string
//   strategyId      : string  (dotted notation, e.g. "breakout.daily.donchian_20_10.sector_etfs.v1")
//   stage           : string  ("idle" | "backtest" | "paper" | "micro_live" | "live")
//   sharpe          : string  (pre-formatted, e.g. "+0.87" or "—")
//   maxDD           : string  (pre-formatted, e.g. "9.2%" or "—")
//   tradeCount      : string  (pre-formatted, e.g. "435" or "—")
//   signalKind      : string  ("positive" | "warning" | "info" | "negative" | "")
//   signalWord      : string  (the inline colored phrase, e.g. "Walk-forward complete")
//   proseTail       : string  (the rest of the italic sentence)
//   metaLine        : string  (smaller mono text under prose)
//   actionVariant   : string  (Button variant — "ghost"/"outlined"/"primary"/"critical"/"")
//   actionLabel     : string  (button label; "" hides the button)
//   onActionClicked : signal
//
// MOTION DISCIPLINE: numeric values do NOT animate from one to another.
// Background/border colors may animate; metric/text content swaps instantly.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property string strategyName: ""
    property string strategyId:   ""
    property string stage:        "paper"
    property string sharpe:       "—"
    property string maxDD:        "—"
    property string tradeCount:   "—"
    property string signalKind:   ""
    property string signalWord:   ""
    property string proseTail:    ""
    property string metaLine:     ""
    property string metaConfigKey: ""
    property string metaStage:     ""
    property string metaEvidence:  ""
    property string actionVariant: ""
    property string actionLabel:   ""

    signal actionClicked()

    // ------------------------------------------------------------------
    // Stage-derived appearance flags
    // ------------------------------------------------------------------

    readonly property bool _isIdle: stage === "idle"
    readonly property bool _isLive: stage === "live"

    // ------------------------------------------------------------------
    // Sizing
    // ------------------------------------------------------------------

    implicitHeight: Math.max(rowLayout.implicitHeight, 56) + Theme.space[3] * 2

    // ------------------------------------------------------------------
    // Background — transparent default; tinted oxblood for LIVE; surface
    // .raised on hover for non-LIVE rows.
    // ------------------------------------------------------------------

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: 0
        color: {
            if (root._isLive) {
                // 5%-alpha brand.accent wash — the editorial "lead story" treatment
                return Qt.rgba(Theme.color.brand.accent.r,
                               Theme.color.brand.accent.g,
                               Theme.color.brand.accent.b,
                               0.07)
            }
            if (mouseArea.containsMouse) {
                return Theme.color.surface.raised
            }
            return "transparent"
        }
        Behavior on color {
            ColorAnimation { duration: Theme.motion.fast }
        }
    }

    // 2px brand-accent left border on LIVE rows
    Rectangle {
        visible: root._isLive
        width: 2
        anchors.left:   parent.left
        anchors.top:    parent.top
        anchors.bottom: parent.bottom
        color: Theme.color.brand.accent
    }

    // Hover capture (lights up surface on non-LIVE rows; no click on the row body itself)
    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        // The row itself is not click-actionable — actions live on the button only.
        // Forward clicks to children so the button MouseArea still works.
        propagateComposedEvents: true
        onClicked: (mouse) => mouse.accepted = false
    }

    // ------------------------------------------------------------------
    // Drag-handle affordance — visual only in PR1.  PR2 wires DragHandler.
    // ------------------------------------------------------------------

    Text {
        id: dragHandle
        visible: false
        text: "⋮⋮"
        font.family:        Theme.typography.data.md.family
        font.pixelSize:     14
        font.letterSpacing: 2
        color:              Theme.color.text.muted
        opacity:            mouseArea.containsMouse ? 0.65 : 0.30
        anchors.left:       parent.left
        anchors.leftMargin: Theme.space[2]
        anchors.verticalCenter: parent.verticalCenter
        Behavior on opacity {
            NumberAnimation { duration: Theme.motion.fast }
        }
    }

    // ------------------------------------------------------------------
    // Row layout
    // ------------------------------------------------------------------

    RowLayout {
        id: rowLayout
        anchors.left:   parent.left
        anchors.right:  parent.right
        anchors.top:    parent.top
        anchors.bottom: parent.bottom
        anchors.leftMargin:  Theme.space[6]   // leaves room for drag handle
        anchors.rightMargin: Theme.space[5]
        anchors.topMargin:    Theme.space[3]
        anchors.bottomMargin: Theme.space[3]
        spacing: Theme.space[4]

        // -- Strategy block: name + ID --
        Column {
            spacing: 4
            Layout.fillWidth: true
            Layout.preferredWidth: 1  // RowLayout flex hint
            Layout.minimumWidth: 200

            Text {
                width: parent.width
                text:  root.strategyName
                color: root._isIdle ? Theme.color.text.secondary
                                    : Theme.color.text.primary
                font.family:    Theme.typography.display.sm.family
                font.pixelSize: Theme.typography.display.sm.size
                font.weight:    Theme.typography.display.sm.weight
                elide:          Text.ElideRight
            }
            Text {
                width: parent.width
                text:  root.strategyId
                color: Theme.color.text.muted
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                elide:          Text.ElideRight
            }
        }

        // -- Sharpe --
        Text {
            text: root.sharpe
            color: root._isIdle ? Theme.color.text.disabled : Theme.color.text.primary
            font.family:    Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features:  Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        // -- max-dd --
        Text {
            text: root.maxDD
            color: root._isIdle ? Theme.color.text.disabled : Theme.color.text.primary
            font.family:    Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features:  Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        // -- trades --
        Text {
            text: root.tradeCount
            color: root._isIdle ? Theme.color.text.disabled : Theme.color.text.primary
            font.family:    Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features:  Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        // -- Status prose: italic Newsreader with inline colored signal word --
        Column {
            spacing: 4
            visible: false
            Layout.fillWidth: false
            Layout.preferredWidth: 0
            Layout.minimumWidth: 0
            Layout.maximumWidth: 0

            Text {
                width:    parent.width
                wrapMode: Text.WordWrap
                color:    Theme.color.text.secondary
                font.family:    Theme.typography.body.md.family
                font.pixelSize: Theme.typography.body.md.size + 1  // body.lg-feeling for the prose moment
                font.italic:    true

                // RichText so the inline-signal span can override italic + color
                textFormat: Text.RichText
                text: {
                    // IDLE rows have no real outcome yet — let the signal word
                    // ride the secondary text color so it doesn't compete with
                    // the more meaningful gate-pass / gate-fail rows below.
                    // Keep the bold + non-italic span treatment for hierarchy.
                    var color = Theme.status.info
                    if (root.signalKind === "positive") color = Theme.status.positive
                    else if (root.signalKind === "warning") color = Theme.status.warning
                    else if (root.signalKind === "negative") color = Theme.status.negative
                    else if (root.signalKind === "info") {
                        color = root._isIdle ? Theme.color.text.secondary
                                             : Theme.status.info
                    }

                    var hex = color.toString()
                    if (root.signalWord === "" && root.proseTail === "") return ""
                    if (root.signalWord === "") return root.proseTail

                    return "<span style='color:" + hex + "; font-style:normal; font-weight:500;'>"
                         + root.signalWord
                         + "</span>"
                         + (root.proseTail.length > 0 ? " " + root.proseTail : "")
                }
            }
            Text {
                visible:  root.metaLine.length > 0
                width:    parent.width
                text:     root.metaLine
                color:    Theme.color.text.muted
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                elide:          Text.ElideRight
            }
        }

        // -- Aligned evidence metadata --
        Text {
            text: root.metaConfigKey || root.metaLine
            color: Theme.color.text.muted
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            elide:          Text.ElideRight
            Layout.preferredWidth: Theme.column.benchConfigKey
        }
        Text {
            text: root.metaStage
            color: Theme.color.text.secondary
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            elide:          Text.ElideRight
            Layout.preferredWidth: Theme.column.benchStage
        }
        Text {
            text: root.metaEvidence
            color: Theme.color.text.muted
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            elide:          Text.ElideRight
            Layout.preferredWidth: Theme.column.benchEvidence
        }

        // -- Action button --
        Item {
            Layout.preferredWidth: Theme.column.benchAction
            Layout.alignment: Qt.AlignVCenter
            implicitHeight: actionBtnLoader.implicitHeight

            Loader {
                id: actionBtnLoader
                active: root.actionVariant !== "" && root.actionLabel !== ""
                anchors.right:          parent.right
                anchors.verticalCenter: parent.verticalCenter
                sourceComponent: actionBtnComponent
            }

            Component {
                id: actionBtnComponent

                QuietAction {
                    text: root.actionLabel
                    onClicked: root.actionClicked()
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Bottom hairline separator
    // ------------------------------------------------------------------

    Rectangle {
        anchors.bottom: parent.bottom
        anchors.left:   parent.left
        anchors.right:  parent.right
        height: 1
        color:  Theme.color.border.subtle
    }
}

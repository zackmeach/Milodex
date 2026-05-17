// TapeRow.qml — Single market tape row: symbol, close price, pct change, asOf.
//
// Tokens consumed:
//   color.text.primary    — symbol + close
//   color.text.muted      — asOf timestamp
//   status.positive       — positive pctChange
//   status.negative       — negative pctChange
//   color.text.secondary  — neutral pctChange (zero)
//   color.border.subtle   — bottom separator
//   typography.data.sm    — symbol + close + pctChange (mono tnum)
//   typography.label.xs   — asOf label
//   space[2], space[3]    — padding
//
// MOTION DISCIPLINE: no Behavior — price updates are instant.
//
// Public API:
//   symbol    : string — ticker symbol (e.g. "SPY")
//   close     : string — formatted close price (e.g. "523.14")
//   pctChange : string — formatted change (e.g. "+0.43%")
//   asOf      : string — timestamp label (e.g. "16:00")

import QtQuick
import Milodex 1.0

Item {
    id: root

    property string symbol:    ""
    property string close:     ""
    property string pctChange: ""
    property string asOf:      ""

    // Change tone: positive if starts with "+", negative if starts with "-",
    // secondary otherwise.
    readonly property color _changeColor: {
        if (root.pctChange.startsWith("+")) return Theme.status.positive
        if (root.pctChange.startsWith("-")) return Theme.status.negative
        return Theme.color.text.secondary
    }

    implicitWidth:  200
    implicitHeight: symbolText.implicitHeight + Theme.space[2] * 2

    // Bottom separator
    Rectangle {
        anchors.bottom: parent.bottom
        anchors.left:   parent.left
        anchors.right:  parent.right
        height: 1
        color:  Theme.color.border.subtle
    }

    // Symbol
    Text {
        id: symbolText
        anchors.left:           parent.left
        anchors.leftMargin:     Theme.space[2]
        anchors.verticalCenter: parent.verticalCenter
        text:                   root.symbol
        color:                  Theme.color.text.primary
        font.family:            Theme.typography.data.sm.family
        font.pixelSize:         Theme.typography.data.sm.size
        font.weight:            Font.Medium
        font.features:          Theme.typography.data.sm.features
    }

    // AsOf
    Text {
        id: asOfText
        anchors.right:          closeText.left
        anchors.rightMargin:    Theme.space[3]
        anchors.verticalCenter: parent.verticalCenter
        text:                   root.asOf
        color:                  Theme.color.text.muted
        font.family:            Theme.typography.label.xs.family
        font.pixelSize:         Theme.typography.label.xs.size
        font.weight:            Theme.typography.label.xs.weight
    }

    // Close
    Text {
        id: closeText
        anchors.right:          pctChangeText.left
        anchors.rightMargin:    Theme.space[3]
        anchors.verticalCenter: parent.verticalCenter
        text:                   root.close
        color:                  Theme.color.text.primary
        font.family:            Theme.typography.data.sm.family
        font.pixelSize:         Theme.typography.data.sm.size
        font.weight:            Theme.typography.data.sm.weight
        font.features:          Theme.typography.data.sm.features
        horizontalAlignment:    Text.AlignRight
    }

    // PctChange
    Text {
        id: pctChangeText
        anchors.right:          parent.right
        anchors.rightMargin:    Theme.space[2]
        anchors.verticalCenter: parent.verticalCenter
        width:                  72
        text:                   root.pctChange
        color:                  root._changeColor
        font.family:            Theme.typography.data.sm.family
        font.pixelSize:         Theme.typography.data.sm.size
        font.weight:            Theme.typography.data.sm.weight
        font.features:          Theme.typography.data.sm.features
        horizontalAlignment:    Text.AlignRight
    }
}

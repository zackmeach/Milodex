// FunnelRow.qml — Risk-gate stage row (master STRATEGY LADDER idiom).
//
// Spec: 2026-05-17-trading-desk-fidelity-design.md §3 IV. Magnitude is the
// right-aligned mono-tnum value on a hairline — never a filled bar.
//
// Tokens consumed:
//   color.text.primary    — value (mono tnum)
//   color.text.secondary  — stage label
//   color.text.muted      — em-dash gloss
//   color.border.subtle   — per-row hairline
//   typography.data.sm    — value
//   typography.body.sm    — label + gloss
//   space[1], space[2]    — vertical padding / label↔gloss gap
//
// Public API:
//   label : string — stage name (e.g. "Evaluations")
//   gloss : string — short em-dash micro-gloss (e.g. "gate inputs")
//   value : string — formatted count (e.g. "142")

import QtQuick
import Milodex 1.0

Item {
    id: root

    property string label: ""
    property string gloss: ""
    property string value: ""

    implicitWidth:  200
    implicitHeight: labelText.implicitHeight + Theme.space[2] * 2

    Text {
        id: labelText
        anchors.left:           parent.left
        anchors.verticalCenter: parent.verticalCenter
        text:                   root.gloss !== ""
                                ? root.label + " — " + root.gloss
                                : root.label
        color:                  Theme.color.text.secondary
        font.family:    Theme.typography.body.sm.family
        font.pixelSize: Theme.typography.body.sm.size
        font.weight:    Theme.typography.body.sm.weight
        elide:          Text.ElideRight
    }

    Text {
        id: valueText
        anchors.right:          parent.right
        anchors.verticalCenter: parent.verticalCenter
        text:                   root.value
        color:                  Theme.color.text.primary
        font.family:    Theme.typography.data.sm.family
        font.pixelSize: Theme.typography.data.sm.size
        font.weight:    Theme.typography.data.sm.weight
        font.features:  Theme.typography.data.sm.features
        horizontalAlignment: Text.AlignRight
    }

    Rectangle {
        objectName:     "funnelRule"
        anchors.bottom: parent.bottom
        anchors.left:   parent.left
        anchors.right:  parent.right
        height:         1
        color:          Theme.color.border.subtle
    }
}

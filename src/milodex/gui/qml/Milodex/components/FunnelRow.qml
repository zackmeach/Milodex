// FunnelRow.qml — Single funnel step row: label, proportion bar, value.
//
// Tokens consumed:
//   color.surface.raised    — proportion bar fill
//   color.border.subtle     — proportion bar track
//   color.text.primary      — value text
//   color.text.muted        — label text
//   typography.data.sm      — value (mono)
//   typography.body.sm      — label
//   space[2], space[3]      — padding
//
// Public API:
//   label      : string — step label (e.g. "Screened")
//   value      : string — formatted value (e.g. "142")
//   proportion : real   — fill fraction 0.0–1.0

import QtQuick
import Milodex 1.0

Item {
    id: root

    property string label:      ""
    property string value:      ""
    property real   proportion: 0.0

    implicitWidth:  200
    implicitHeight: Theme.space[4]

    // Label
    Text {
        id: labelText
        anchors.left:           parent.left
        anchors.verticalCenter: parent.verticalCenter
        width:                  80
        text:                   root.label
        color:                  Theme.color.text.muted
        font.family:            Theme.typography.body.sm.family
        font.pixelSize:         Theme.typography.body.sm.size
        font.weight:            Theme.typography.body.sm.weight
        elide:                  Text.ElideRight
    }

    // Proportion bar track
    Rectangle {
        id: barTrack
        anchors.left:           labelText.right
        anchors.leftMargin:     Theme.space[2]
        anchors.right:          valueText.left
        anchors.rightMargin:    Theme.space[2]
        anchors.verticalCenter: parent.verticalCenter
        height: 4
        radius: 2
        color:  Theme.color.border.subtle

        // Bar fill
        Rectangle {
            anchors.left:   parent.left
            anchors.top:    parent.top
            anchors.bottom: parent.bottom
            width:          Math.max(0, Math.min(1, root.proportion)) * parent.width
            radius:         parent.radius
            color:          Theme.color.surface.raised
        }
    }

    // Value
    Text {
        id: valueText
        anchors.right:          parent.right
        anchors.verticalCenter: parent.verticalCenter
        width:                  60
        text:                   root.value
        color:                  Theme.color.text.primary
        font.family:            Theme.typography.data.sm.family
        font.pixelSize:         Theme.typography.data.sm.size
        font.weight:            Theme.typography.data.sm.weight
        font.features:          Theme.typography.data.sm.features
        horizontalAlignment:    Text.AlignRight
    }
}

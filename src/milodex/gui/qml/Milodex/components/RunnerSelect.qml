// RunnerSelect.qml — Dropdown-style runner selector; emits selected(string).
//
// Tokens consumed:
//   color.surface.base       — closed background
//   color.surface.raised     — dropdown panel background
//   color.border.subtle      — closed border
//   color.border.regular     — open border / row separator
//   color.text.primary       — current + hovered item label
//   color.text.muted         — unselected item labels
//   color.brand.accent       — selected item accent bar
//   typography.body.sm       — item labels
//   typography.label.xs      — "RUNNER" eyebrow
//   space[1], space[2], space[3] — padding
//   radius.md                — corners
//
// MOTION DISCIPLINE: no Behavior on any color property.
//
// Public API:
//   runners  : var    — array of { id: string, label: string }
//   current  : string — currently selected runner id
//   signal selected(string runnerId)

import QtQuick
import Milodex 1.0

Item {
    id: root

    property var    runners: []
    property string current: ""

    signal selected(string runnerId)

    // Internal state
    property bool _open: false

    readonly property string _currentLabel: {
        for (var i = 0; i < root.runners.length; i++) {
            if (root.runners[i].id === root.current) return root.runners[i].label
        }
        return root.current
    }

    implicitWidth:  200
    implicitHeight: triggerRow.height

    // Closed trigger row
    Rectangle {
        id: triggerRow
        anchors.top:  parent.top
        anchors.left: parent.left
        width:        parent.width
        height:       triggerLabel.implicitHeight + Theme.space[2] * 2
        color:        Theme.color.surface.base
        radius:       Theme.radius.md
        border.color: root._open ? Theme.color.border.regular : Theme.color.border.subtle
        border.width: 1

        Text {
            id: eyebrow
            anchors.left:           parent.left
            anchors.leftMargin:     Theme.space[2]
            anchors.verticalCenter: parent.verticalCenter
            text:                   "RUNNER"
            color:                  Theme.color.text.muted
            font.family:            Theme.typography.label.xs.family
            font.pixelSize:         Theme.typography.label.xs.size
            font.weight:            Theme.typography.label.xs.weight
            font.letterSpacing:     Theme.typography.label.xs.letterSpacing
            font.capitalization:    Font.AllUppercase
        }

        Text {
            id: triggerLabel
            anchors.left:           eyebrow.right
            anchors.leftMargin:     Theme.space[2]
            anchors.right:          chevron.left
            anchors.rightMargin:    Theme.space[1]
            anchors.verticalCenter: parent.verticalCenter
            text:                   root._currentLabel
            color:                  Theme.color.text.primary
            font.family:            Theme.typography.body.sm.family
            font.pixelSize:         Theme.typography.body.sm.size
            font.weight:            Theme.typography.body.sm.weight
            elide:                  Text.ElideRight
        }

        Text {
            id: chevron
            anchors.right:          parent.right
            anchors.rightMargin:    Theme.space[2]
            anchors.verticalCenter: parent.verticalCenter
            text:                   root._open ? "▲" : "▼"
            color:                  Theme.color.text.muted
            font.pixelSize:         10
        }

        MouseArea {
            anchors.fill: parent
            onClicked: root._open = !root._open
        }
    }

    // Dropdown panel (floats below trigger; z-elevated)
    Rectangle {
        id: dropPanel
        visible:      root._open
        anchors.top:  triggerRow.bottom
        anchors.topMargin: 2
        anchors.left: parent.left
        width:        parent.width
        height:       dropColumn.implicitHeight + Theme.space[1] * 2
        color:        Theme.color.surface.raised
        border.color: Theme.color.border.regular
        border.width: 1
        radius:       Theme.radius.md
        z:            10

        Column {
            id: dropColumn
            anchors.top:        parent.top
            anchors.topMargin:  Theme.space[1]
            anchors.left:       parent.left
            anchors.right:      parent.right

            Repeater {
                model: root.runners

                Item {
                    id: dropItem
                    width:  dropColumn.width
                    height: dropItemLabel.implicitHeight + Theme.space[2] * 2

                    readonly property bool _isCurrent: modelData.id === root.current

                    // Selected accent bar
                    Rectangle {
                        visible:        dropItem._isCurrent
                        anchors.left:   parent.left
                        anchors.top:    parent.top
                        anchors.bottom: parent.bottom
                        width:          3
                        color:          Theme.color.brand.accent
                    }

                    Text {
                        id: dropItemLabel
                        anchors.left:           parent.left
                        anchors.leftMargin:     Theme.space[3]
                        anchors.right:          parent.right
                        anchors.rightMargin:    Theme.space[2]
                        anchors.verticalCenter: parent.verticalCenter
                        text:                   modelData.label
                        color:                  dropItem._isCurrent ? Theme.color.text.primary
                                                                    : Theme.color.text.muted
                        font.family:            Theme.typography.body.sm.family
                        font.pixelSize:         Theme.typography.body.sm.size
                        font.weight:            Theme.typography.body.sm.weight
                        elide:                  Text.ElideRight
                    }

                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            root._open = false
                            if (modelData.id !== root.current)
                                root.selected(modelData.id)
                        }
                    }
                }
            }
        }
    }
}

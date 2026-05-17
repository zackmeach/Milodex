// SegmentedToggle.qml — Horizontal segmented control; emits activated(string).
//
// Tokens consumed:
//   color.surface.raised       — selected segment background
//   color.surface.base         — track background
//   color.border.subtle        — track border
//   color.border.regular       — separator between segments
//   color.text.primary         — selected label
//   color.text.muted           — unselected label
//   typography.label.xs        — uppercase letter-spaced labels
//   space[2], space[3]         — vertical / horizontal padding
//   radius.md                  — track + thumb corner radius
//
// MOTION DISCIPLINE: state changes are instant — no Behavior on color.
//
// Public API:
//   options  : var    — array of { label: string, value: string }
//   current  : string — currently selected value
//   signal activated(string value)

import QtQuick
import Milodex 1.0

Item {
    id: root

    property var    options: []
    property string current: ""

    signal activated(string value)

    implicitWidth:  trackRow.implicitWidth  + Theme.space[2] * 2
    implicitHeight: trackRow.implicitHeight + Theme.space[1] * 2

    // Track background
    Rectangle {
        anchors.fill: parent
        color:        Theme.color.surface.base
        border.color: Theme.color.border.subtle
        border.width: 1
        radius:       Theme.radius.md
    }

    Row {
        id: trackRow
        anchors.fill: parent
        spacing: 0

        Repeater {
            model: root.options

            Item {
                id: segItem
                width:  segLabel.implicitWidth + Theme.space[3] * 2
                height: trackRow.height

                readonly property bool _selected: modelData.value === root.current

                // Selected segment highlight
                Rectangle {
                    anchors.fill:        parent
                    // 2px inset — intentional half of Theme.space[1] (4px); no exact token
                    anchors.topMargin:   2
                    anchors.bottomMargin: 2
                    anchors.leftMargin:  index === 0 ? 2 : 0
                    anchors.rightMargin: index === root.options.length - 1 ? 2 : 0
                    color:  segItem._selected ? Theme.color.surface.raised : "transparent"
                    radius: Theme.radius.md
                }

                // Separator (right edge, not on last item)
                Rectangle {
                    visible: index < root.options.length - 1
                    anchors.right:  parent.right
                    anchors.top:    parent.top
                    anchors.bottom: parent.bottom
                    anchors.topMargin:    Theme.space[1]
                    anchors.bottomMargin: Theme.space[1]
                    width: 1
                    color: Theme.color.border.regular
                }

                Text {
                    id: segLabel
                    anchors.centerIn:    parent
                    text:                modelData.label
                    color:               segItem._selected ? Theme.color.text.primary
                                                           : Theme.color.text.muted
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        if (modelData.value !== root.current)
                            root.activated(modelData.value)
                    }
                }
            }
        }
    }
}

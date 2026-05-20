// RunnerSelect.qml — Type-only runner selector (no chrome, no glyph).
//
// Spec: 2026-05-17-trading-desk-fidelity-design.md §3 III. App idiom is
// type + colour + hairline only. Behaviour & signal FROZEN (spec §5).
//
// DESIGN DECISION (2026-05-19 amendment): the open dropdown carries a
// solid surface.canvas backing + border.regular outline. This overrides the
// 2026-05-18 "intentionally borderless" decision per operator feedback that
// the borderless dropdown was unreadable in practice when overlaying the
// KeyStat grid below (issue 03 in the 2026-05-19 UI readiness batch).
//
// The dropdown still closes on outside-click (issue 05 amendment, same date)
// and the CLOSE pill affordance is preserved.
//
// Tokens consumed:
//   color.text.muted      — eyebrow / unselected / affordance
//   color.text.primary    — current label
//   color.brand.primary   — selected row
//   color.border.subtle   — row hairline
//   typography.label.xs   — eyebrow / affordance (uppercase)
//   typography.body.sm    — labels
//   space[1..3]           — padding
//
// Public API (FROZEN):
//   runners : var    — array of { id: string, label: string }
//   current : string — selected runner id
//   signal selected(string runnerId)

import QtQuick
import Milodex 1.0

Item {
    id: root

    property var    runners: []
    property string current: ""
    signal selected(string runnerId)

    // Issue 05: dismiss signals for outside-click / ESC integration.
    signal opened()
    signal dismissed()

    // _open: internal state. `expanded` is the public alias for Main.qml wiring.
    property bool _open: false
    property bool expanded: _open
    on_OpenChanged: {
        if (_open) {
            opened()
        } else {
            dismissed()
        }
    }
    onExpandedChanged: {
        if (expanded !== _open) _open = expanded
    }

    Keys.onEscapePressed: function(event) {
        if (root._open) {
            root._open = false
            event.accepted = true
        }
    }

    readonly property string _currentLabel: {
        for (var i = 0; i < root.runners.length; i++) {
            if (root.runners[i].id === root.current) return root.runners[i].label
        }
        return root.current
    }

    implicitWidth:  200
    implicitHeight: triggerRow.height

    // Trigger row — plain Item so anchors.fill is valid on the MouseArea
    Item {
        id: triggerRow
        anchors.top:  parent.top
        anchors.left: parent.left
        width:        parent.width
        height:       triggerLabel.implicitHeight + Theme.space[2] * 2

        Text {
            id: eyebrowText
            anchors.left:           parent.left
            anchors.verticalCenter: parent.verticalCenter
            text:  "RUNNER"
            color: Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }

        Text {
            id: triggerLabel
            anchors.left:           eyebrowText.right
            anchors.leftMargin:     Theme.space[2]
            anchors.right:          affordanceText.left
            anchors.rightMargin:    Theme.space[1]
            anchors.verticalCenter: parent.verticalCenter
            text:  root._currentLabel
            color: Theme.color.text.primary
            font.family:    Theme.typography.body.sm.family
            font.pixelSize: Theme.typography.body.sm.size
            font.weight:    Theme.typography.body.sm.weight
            elide:          Text.ElideRight
        }

        Text {
            id: affordanceText
            anchors.right:          parent.right
            anchors.verticalCenter: parent.verticalCenter
            text:  root._open ? "CLOSE" : "CHANGE"
            color: Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }

        MouseArea {
            anchors.fill: parent
            onClicked: root._open = !root._open
        }
    }

    Rectangle {
        anchors.top:       triggerRow.bottom
        anchors.topMargin: Theme.space[1]
        anchors.left:      parent.left
        width:             parent.width
        height:            dropColumn.height
        visible:           root._open
        color:             Theme.color.surface.canvas
        border.color:      Theme.color.border.regular
        border.width:      1
        z:                 9
    }

    Column {
        id: dropColumn
        visible:           root._open
        anchors.top:       triggerRow.bottom
        anchors.topMargin: Theme.space[1]
        anchors.left:      parent.left
        width:             parent.width
        z:                 10

        Repeater {
            model: root.runners

            Item {
                id: dropItem
                width:  dropColumn.width
                height: dropLabel.implicitHeight + Theme.space[2] * 2

                readonly property bool _isCurrent: modelData.id === root.current

                Text {
                    id: dropLabel
                    anchors.left:           parent.left
                    anchors.right:          parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    text:  modelData.label
                    color: dropItem._isCurrent ? Theme.color.brand.primary
                                               : Theme.color.text.muted
                    font.family:    Theme.typography.body.sm.family
                    font.pixelSize: Theme.typography.body.sm.size
                    font.weight:    Theme.typography.body.sm.weight
                    elide:          Text.ElideRight
                }

                Rectangle {
                    anchors.bottom: parent.bottom
                    anchors.left:   parent.left
                    anchors.right:  parent.right
                    height: 1
                    color:  Theme.color.border.subtle
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

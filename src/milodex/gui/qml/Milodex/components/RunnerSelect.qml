// RunnerSelect.qml — Type-only runner selector (no chrome, no glyph).
//
// Spec: 2026-05-17-trading-desk-fidelity-design.md §3 III. App idiom is
// type + colour + hairline only. Behaviour & signal FROZEN (spec §5).
//
// DESIGN DECISION (operator-accepted 2026-05-18): the open dropdown is
// intentionally borderless and floats over the §III KeyStat grid with no
// opaque backing — the no-chrome law applies here too. The transient
// open-state overlap was reviewed (code-review I-1) and accepted as-is.
// Do NOT re-introduce a surface fill / panel to "fix" this without a
// spec §3-III amendment.
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

    property bool _open: false

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

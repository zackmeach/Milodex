// RunnerSelect.qml — Type-only runner selector (no chrome, no glyph).
//
// Spec: 2026-05-17-trading-desk-fidelity-design.md §3 III. App idiom is
// type + colour + hairline only. Behaviour & signal FROZEN (spec §5).
//
// DESIGN DECISION (2026-05-29 amendment): the open dropdown renders in a
// top-level Popup (window overlay layer), not an inline sibling Item. The
// previous inline panel reported no layout height and carried no elevated z,
// so later-declared siblings (the KeyStat grid, the italic eval line, and
// Section VI Market Tape in the adjacent layout branch) painted *over* it —
// the open dropdown was unreadable (2026-05-29 GUI report). A Popup lives in
// the QtQuick.Controls overlay layer, which composites above all normal items
// regardless of z, so it floats cleanly above every sibling.
//
// The Popup is non-modal with CloseOnPressOutside DISABLED: dismissal still
// flows through Main.qml's z:9000 outside-click overlay (issues 03/05), which
// sets _open=false. Keeping that single dismiss path avoids two competing
// outside-click handlers. The solid surface.canvas backing + border.regular
// outline (issue 03) and the CLOSE pill affordance are preserved.
//
// Tokens consumed:
//   color.surface.canvas  — dropdown backing
//   color.border.regular  — dropdown outline
//   color.border.subtle   — row hairline
//   color.text.muted      — eyebrow / unselected / affordance
//   color.text.primary    — current label
//   color.brand.primary   — selected row
//   typography.label.xs   — eyebrow / affordance (uppercase)
//   typography.body.sm    — labels
//   space[1..3]           — padding
//
// Public API (FROZEN):
//   runners : var    — array of { id: string, label: string }
//   current : string — selected runner id
//   signal selected(string runnerId)

import QtQuick
import QtQuick.Controls.Basic
import Milodex 1.0

Item {
    id: root

    property var    runners: []
    property string current: ""
    signal selected(string runnerId)

    // Issue 05: dismiss signals for outside-click / ESC integration.
    signal opened()
    signal dismissed()

    // I-2: guard signals so they only fire on true user-driven transitions,
    // not during construction (initial _open = false would otherwise emit
    // dismissed() before any interaction).
    property bool _initialized: false
    Component.onCompleted: { _initialized = true }

    // _open: internal state. `expanded` is the public alias for Main.qml wiring.
    property bool _open: false
    property bool expanded: _open
    on_OpenChanged: {
        if (!_initialized) return
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

    // I-1: expose the dropdown's scene-space bounding rect so Main.qml's
    // outside-click overlay can exclude clicks that land inside the open
    // dropdown. Returns Qt.rect(0,0,0,0) when closed (nothing to exclude).
    // The Popup content lives in the overlay layer, so map its contentItem.
    function dropdownBoundsInScene() {
        if (!_open) return Qt.rect(0, 0, 0, 0)
        var p = dropPopup.contentItem.mapToItem(null, 0, 0)
        return Qt.rect(p.x, p.y, dropPopup.width, dropPopup.height)
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

    // Open dropdown — Popup in the window overlay layer so it floats above all
    // sibling content. Non-modal: outside-click dismissal is owned by Main.qml's
    // overlay (see header), so CloseOnPressOutside is intentionally disabled.
    Popup {
        id: dropPopup
        x:       0
        y:       triggerRow.height + Theme.space[1]
        width:   root.width
        visible: root._open
        padding: 0
        closePolicy: Popup.NoAutoClose

        background: Rectangle {
            color:        Theme.color.surface.canvas
            border.color: Theme.color.border.regular
            border.width: 1
        }

        contentItem: Column {
            id: dropColumn
            width: dropPopup.availableWidth

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
}

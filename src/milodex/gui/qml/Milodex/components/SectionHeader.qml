// SectionHeader.qml — Desk section header with optional numeral accent and right slot.
//
// Tokens consumed:
//   color.text.primary     — title text
//   color.text.muted       — numeral accent
//   color.border.subtle    — bottom divider line
//   typography.label.xs    — uppercase letter-spaced label for title
//   typography.data.sm     — mono numeral
//   space[2], space[3]     — vertical / horizontal padding
//
// Public API:
//   numeral  : string  — optional mono count/label shown left of title (e.g. "14")
//   title    : string  — section label text (rendered uppercase via label.xs)
//   rightSlot: Item    — optional right-aligned item (default property alias)

import QtQuick
import Milodex 1.0

Item {
    id: root

    property string numeral: ""
    property string title:   ""

    // Right-slot: consumers place an Item child and it anchors to the right
    // of the header row.  Uses default property alias onto a loader Item.
    default property alias rightSlot: rightSlotArea.children

    implicitWidth:  200
    implicitHeight: labelText.implicitHeight + Theme.space[2] * 2

    // Bottom divider
    Rectangle {
        anchors.bottom: parent.bottom
        anchors.left:   parent.left
        anchors.right:  parent.right
        height: 1
        color:  Theme.color.border.subtle
    }

    // Numeral
    Text {
        id: numeralText
        anchors.left:           parent.left
        anchors.leftMargin:     0
        anchors.verticalCenter: parent.verticalCenter
        visible:                root.numeral !== ""
        text:                   root.numeral
        color:                  Theme.color.text.muted
        font.family:            Theme.typography.data.sm.family
        font.pixelSize:         Theme.typography.data.sm.size
        font.weight:            Theme.typography.data.sm.weight
        font.features:          Theme.typography.data.sm.features
    }

    // Title label
    Text {
        id: labelText
        anchors.left:           root.numeral !== "" ? numeralText.right : parent.left
        anchors.leftMargin:     root.numeral !== "" ? Theme.space[2] : 0
        anchors.verticalCenter: parent.verticalCenter
        text:                   root.title
        color:                  Theme.color.text.primary
        font.family:            Theme.typography.label.xs.family
        font.pixelSize:         Theme.typography.label.xs.size
        font.weight:            Theme.typography.label.xs.weight
        font.letterSpacing:     Theme.typography.label.xs.letterSpacing
        font.capitalization:    Font.AllUppercase
    }

    // Right-slot container
    Item {
        id: rightSlotArea
        anchors.right:          parent.right
        anchors.verticalCenter: parent.verticalCenter
        implicitWidth:          childrenRect.width
        implicitHeight:         childrenRect.height
    }
}

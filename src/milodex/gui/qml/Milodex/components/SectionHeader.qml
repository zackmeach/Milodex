// SectionHeader.qml — Editorial section-label band (master SectionLabel,
// reference DeskSurface.qml@757afe7:208-262).
//
// Tokens consumed:
//   color.brand.primary    — serif letter (numeral)
//   color.text.secondary   — uppercase section name (title)
//   color.text.muted       — right meta slot
//   color.border.subtle    — hairline rule
//   typography.display.sm  — letter (serif)
//   typography.label.xs    — name (uppercase, letter-spaced)
//   typography.data.sm     — meta slot (mono tnum)
//   space[2], space[3]     — rule gap / letter→name gap
//
// Public API (unchanged — 7 DeskSurface call sites depend on it):
//   numeral  : string  — the serif letter shown left of the name
//   title    : string  — section name (rendered uppercase)
//   rightSlot: Item    — default property alias; meta Text child(ren)

import QtQuick
import Milodex 1.0

Column {
    id: root

    property string numeral: ""
    property string title:   ""
    default property alias rightSlot: rightSlotArea.children

    width:   parent ? parent.width : implicitWidth
    spacing: Theme.space[2]

    Item {
        width:          parent.width
        implicitHeight: root.numeral !== "" ? letterText.implicitHeight : nameText.implicitHeight

        Text {
            id: letterText
            objectName:    "sectionHeaderLetter"
            anchors.left:  parent.left
            anchors.top:   parent.top
            visible:       root.numeral !== ""
            text:          root.numeral
            color:         Theme.color.brand.primary
            font.family:    Theme.typography.display.sm.family
            font.pixelSize: Theme.typography.display.sm.size
            font.weight:    Theme.typography.display.sm.weight
        }

        Text {
            id: nameText
            anchors.left:       root.numeral !== "" ? letterText.right : parent.left
            anchors.leftMargin: root.numeral !== "" ? Theme.space[3] : 0
            anchors.baseline:   root.numeral !== "" ? letterText.baseline : undefined
            anchors.top:        root.numeral !== "" ? undefined : parent.top
            text:               root.title
            color:              Theme.color.text.secondary
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }

        Item {
            id: rightSlotArea
            anchors.right:    parent.right
            anchors.baseline: root.numeral !== "" ? letterText.baseline : undefined
            anchors.top:      root.numeral !== "" ? undefined : parent.top
            implicitWidth:    childrenRect.width
            implicitHeight:   childrenRect.height
        }
    }

    Rectangle {
        objectName: "sectionHeaderRule"
        width:      parent.width
        height:     1
        color:      Theme.color.border.subtle
    }
}

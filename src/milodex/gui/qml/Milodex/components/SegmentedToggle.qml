// SegmentedToggle.qml — Type-only period/filter control (no chrome).
//
// Spec: 2026-05-17-trading-desk-fidelity-design.md §2 P4. The app idiom is
// "type and colour only, no iconography" — this control renders as a row of
// uppercase words, not a pill. No Rectangle anywhere (gate G3 absence half).
//
// Tokens consumed:
//   color.brand.primary  — selected word
//   color.text.muted     — unselected word
//   typography.label.xs  — uppercase letter-spaced words
//   space[4]             — inter-word gap
//   space[1]             — invisible tap-target inset
//
// MOTION DISCIPLINE: state changes are instant — no Behavior on color.
//
// Public API (FROZEN — spec §5):
//   options  : var    — array of { label: string, value: string }
//   current  : string — currently selected value
//   signal activated(string value)

import QtQuick
import Milodex 1.0

Row {
    id: root

    property var    options: []
    property string current: ""

    signal activated(string value)

    spacing: Theme.space[4]

    Repeater {
        model: root.options

        Text {
            text:                modelData.label
            color:               modelData.value === root.current
                                 ? Theme.color.brand.primary
                                 : Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase

            MouseArea {
                anchors.fill:    parent
                anchors.margins: -Theme.space[1]
                onClicked: {
                    if (modelData.value !== root.current)
                        root.activated(modelData.value)
                }
            }
        }
    }
}

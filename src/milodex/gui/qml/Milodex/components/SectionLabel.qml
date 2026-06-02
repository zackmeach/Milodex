// SectionLabel.qml — Quiet ALL-CAPS section label for the Bench confirmation
// modal's structured Intent Packet body. Extracted from
// BenchConfirmationModal.qml (PR 13 decompose).
//
// Same typographic register as BenchEvidenceModal's section headers but
// rendered in muted color (not brand.accent) per PR L visual direction
// ("structured, not decorative"). This is deliberately NOT SectionHeader —
// the desk/front SectionHeader carries a serif lettermark + hairline rule;
// this is a flat muted eyebrow with an optional mono ordinal.
//
// Polish: optional `ordinal` prefix renders as a small mono numeral
// (`01`, `02`, …) ahead of the label so the seven-section structure reads
// as a deliberate progression rather than a flat list. Extra top breathing
// room (Theme.space[3]) separates sections visually without heavier rules.

import QtQuick
import Milodex 1.0

Item {
    id: sectionLabelRoot
    property string label: ""
    property string ordinal: ""
    width: parent ? parent.width : 0
    implicitHeight: sectionText.implicitHeight + Theme.space[3]

    Row {
        anchors.bottom: parent.bottom
        spacing: Theme.space[2]

        Text {
            visible: sectionLabelRoot.ordinal.length > 0
            text:  sectionLabelRoot.ordinal
            color: Theme.color.text.disabled
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
        }

        Text {
            id: sectionText
            text:  sectionLabelRoot.label
            color: Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Font.DemiBold
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
    }
}

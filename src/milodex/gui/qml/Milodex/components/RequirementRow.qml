// RequirementRow.qml — Single bullet/glyph row used inside the Bench
// confirmation modal's WOULD EVENTUALLY REQUIRE and blocked-by lists.
// Extracted from BenchConfirmationModal.qml (PR 13 decompose).

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

RowLayout {
    id: requirementRowRoot
    property string text: ""
    width: parent ? parent.width : 0
    spacing: Theme.space[2]

    Text {
        text:  "·"
        color: Theme.color.text.muted
        font.family:    Theme.typography.data.xs.family
        font.pixelSize: Theme.typography.data.xs.size
        font.features:  Theme.typography.data.xs.features
    }

    Text {
        text:  requirementRowRoot.text
        color: Theme.color.text.secondary
        font.family:    Theme.typography.data.xs.family
        font.pixelSize: Theme.typography.data.xs.size
        font.features:  Theme.typography.data.xs.features
        Layout.fillWidth: true
        elide: Text.ElideRight
    }
}

// SectionStatus.qml — reusable per-section status banner (loading / error /
// empty-data isolation). Extracted from DeskSurface.qml's inline
// `component SectionStatus` (PR-8 GUI surface honesty) so FRONT, BENCH, and
// LEDGER can bind the same dataStatus/dataErrorMessage contract that DESK
// already uses, instead of copy-pasting the block per surface.
//
// One section's error never blanks the surface — visibility is scoped to
// this banner, not the whole page.
//
// Usage:
//   SectionStatus {
//       status: SomeReadModel.dataStatus
//       errorMessage: SomeReadModel.dataErrorMessage
//       hasData: <bool: true once real content is present>
//   }

import QtQuick
import Milodex 1.0

Column {
    id: root

    property string status: "ready"
    property string errorMessage: ""
    property bool   hasData: false

    width: parent ? parent.width : 0
    spacing: Theme.space[2]
    visible: root.status !== "ready" || !root.hasData

    Text {
        visible: root.status === "loading" && !root.hasData
        width: parent.width
        text: "Loading…"
        color: Theme.color.text.muted
        font.family:    Theme.typography.body.sm.family
        font.pixelSize: Theme.typography.body.sm.size
        font.italic:    true
    }
    Text {
        visible: root.status === "error"
        width: parent.width
        text: root.errorMessage !== ""
              ? "Unavailable — " + root.errorMessage
              : "Unavailable."
        color: Theme.status.warning
        font.family:    Theme.typography.body.sm.family
        font.pixelSize: Theme.typography.body.sm.size
        font.italic:    true
        wrapMode: Text.WordWrap
    }
    Text {
        visible: root.status === "ready" && !root.hasData
        width: parent.width
        text: "No data yet."
        color: Theme.color.text.muted
        font.family:    Theme.typography.body.sm.family
        font.pixelSize: Theme.typography.body.sm.size
        font.italic:    true
    }
}

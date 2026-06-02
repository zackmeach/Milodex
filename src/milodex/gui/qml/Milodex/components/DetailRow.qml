// DetailRow.qml — Single label/value row used across the Bench confirmation
// modal's ACTION, CURRENT SNAPSHOT, FUTURE RECORD, and COMMAND DRAFT
// PREVIEW sections. Extracted from BenchConfirmationModal.qml (PR 13
// decompose).
//
// Label column defaults to 96 px so short labels ("Sharpe", "Wired",
// "Stage") sit close to their values. `numeric: true` right-aligns the
// value so columns of numbers read as a tabular stack. `labelWidth`
// overrides the 96 px default per-row for visibly longer labels
// ("Submission state", "Evidence packet v", etc.).

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

RowLayout {
    property string label: ""
    property string value: ""
    property bool   numeric: false
    property real   labelWidth: 96
    width: parent ? parent.width : 0
    spacing: Theme.space[3]

    Text {
        text: label
        color: Theme.color.text.muted
        font.family:    Theme.typography.data.xs.family
        font.pixelSize: Theme.typography.data.xs.size
        font.features:  Theme.typography.data.xs.features
        Layout.preferredWidth: labelWidth
    }

    Text {
        text: value
        color: Theme.color.text.secondary
        font.family:    Theme.typography.data.xs.family
        font.pixelSize: Theme.typography.data.xs.size
        font.features:  Theme.typography.data.xs.features
        Layout.fillWidth: true
        horizontalAlignment: numeric ? Text.AlignRight : Text.AlignLeft
        elide: Text.ElideRight
    }
}

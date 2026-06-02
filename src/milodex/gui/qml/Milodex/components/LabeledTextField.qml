// LabeledTextField.qml — Single-line operator text input used by the Bench
// confirmation modal's OPERATOR INPUT (reason) and OPERATOR EVIDENCE
// (recommendation / known-risk) sections. Extracted from the three
// repeated Rectangle+TextInput blocks in BenchConfirmationModal.qml
// (PR 13 decompose).
//
// The raised-surface card carries a 1px border that shifts to
// `focusBorderColor` while the field has active focus. The placeholder
// Text shows when the field is empty.
//
// API:
//   property string  placeholderText   — italic placeholder shown when empty
//   property alias   text               — two-way bound input text
//   property color   focusBorderColor   — border + selection color on focus
//                                          (reason uses Theme.status.negative;
//                                          recommendation/known-risk use
//                                          Theme.color.text.primary)
//   property color   selectedTextColor  — text color inside the selection
//   signal textEdited(string text)      — emitted on every keystroke so the
//                                          host can mirror into its own state
//
// `text` is a two-way alias onto the inner TextInput; the host binds it to
// a backing property and listens to `textEdited` to keep that property in
// sync (mirrors the original inline `onTextChanged: root._reasonText = text`).

import QtQuick
import Milodex 1.0

Rectangle {
    id: fieldRoot

    property alias  text: input.text
    property string placeholderText: ""
    property color  focusBorderColor: Theme.color.text.primary
    property color  selectedTextColor: Theme.color.surface.base

    signal textEdited(string text)

    width: parent ? parent.width : 0
    implicitHeight: input.implicitHeight + Theme.space[3] * 2
    color: Theme.color.surface.raised
    border.color: input.activeFocus ? focusBorderColor : Theme.color.border.subtle
    border.width: 1
    radius: Theme.radius.md

    TextInput {
        id: input
        anchors.fill: parent
        anchors.margins: Theme.space[3]
        color: Theme.color.text.primary
        selectByMouse: true
        selectionColor: fieldRoot.focusBorderColor
        selectedTextColor: fieldRoot.selectedTextColor
        clip: true
        font.family:    Theme.typography.data.xs.family
        font.pixelSize: Theme.typography.data.xs.size
        font.features:  Theme.typography.data.xs.features
        onTextChanged: fieldRoot.textEdited(text)

        Text {
            visible: input.text.length === 0
            anchors.fill: parent
            verticalAlignment: Text.AlignVCenter
            text: fieldRoot.placeholderText
            color: Theme.color.text.disabled
            font.family:    Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features:  Theme.typography.data.xs.features
            font.italic: true
        }
    }
}

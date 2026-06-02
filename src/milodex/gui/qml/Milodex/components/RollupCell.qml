// RollupCell.qml — Hero metric cell: label over value, tone-colored value.
//
// Tokens consumed:
//   color.text.muted           — label
//   color.brand.primary        — tone "brand"
//   color.text.primary         — tone "data"
//   color.text.muted           — tone "muted"
//   status.positive            — tone "positive"
//   status.negative            — tone "negative"
//   status.warning             — tone "warning"
//   typography.label.xs        — label (uppercase)
//   typography.display.deskNum — value (large Newsreader)
//   space[1]                   — label/value gap
//
// MOTION DISCIPLINE: no Behavior — tone changes are instant.
//
// Public API:
//   label : string — metric label (e.g. "Day P&L")
//   value : string — formatted value (e.g. "+$1,240")
//   tone  : string — "brand"|"positive"|"negative"|"warning"|"muted"|"data"

import QtQuick
import Milodex 1.0

Item {
    id: root

    property string label: ""
    property string value: ""
    property string tone:  "data"

    // Tone → editorial token mapping — delegates to Formatters.toneColor (PR10).
    // The "keep in sync with ActivityTable" comment is retired; both now share
    // the single Formatters.toneColor implementation.
    // No Behavior — state changes are instant.
    readonly property color _valueColor: Formatters.toneColor(root.tone)

    implicitWidth:  valueText.implicitWidth
    implicitHeight: labelText.implicitHeight + Theme.space[1] + valueText.implicitHeight

    // Label
    Text {
        id: labelText
        anchors.top:  parent.top
        anchors.left: parent.left
        text:                root.label
        color:               Theme.color.text.muted
        font.family:         Theme.typography.label.xs.family
        font.pixelSize:      Theme.typography.label.xs.size
        font.weight:         Theme.typography.label.xs.weight
        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
        font.capitalization: Font.AllUppercase
    }

    // Value
    Text {
        id: valueText
        anchors.top:        labelText.bottom
        anchors.topMargin:  Theme.space[1]
        anchors.left:       parent.left
        text:               root.value
        color:              root._valueColor
        font.family:        Theme.typography.display.deskNum.family
        font.pixelSize:     Theme.typography.display.deskNum.size
        font.weight:        Theme.typography.display.deskNum.weight
    }
}

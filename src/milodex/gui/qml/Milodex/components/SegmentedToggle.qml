// SegmentedToggle.qml — Horizontal segmented control; emits activated(string).
//
// Tokens consumed:
//   color.surface.raised       — selected segment background
//   color.surface.base         — track background
//   color.border.subtle        — track border
//   color.border.regular       — separator between segments
//   color.text.primary         — selected label
//   color.text.muted           — unselected label
//   typography.label.xs        — uppercase letter-spaced labels
//   space[2], space[3]         — vertical / horizontal padding
//   radius.md                  — track + thumb corner radius
//
// MOTION DISCIPLINE: state changes are instant — no Behavior on color.
//
// Public API:
//   options  : var    — array of { label: string, value: string }
//   current  : string — currently selected value
//   signal activated(string value)

import QtQuick
import Milodex 1.0

Item {
    id: root

    property var    options: []
    property string current: ""

    signal activated(string value)

    // ------------------------------------------------------------------
    // One-way sizing model (no parent⇄child implicit cycle).
    //
    //   implicitHeight ← intrinsic label height (_labelMetrics) + vertical
    //                    padding.  Never reads trackRow.
    //   implicitWidth  ← sum of every segment's intrinsic width + padding,
    //                    measured by a hidden non-positioned Repeater
    //                    (_widthProbe) that does NOT fill root.
    //
    // Render flow is strictly top-down: trackRow is positioned (it does NOT
    // anchors.fill parent), and each segItem derives its height from
    // root.height — so size flows root → segItem, never back.
    // ------------------------------------------------------------------

    // Hidden text used purely for intrinsic label-height metrics. It is not
    // laid out (visible:false, zero-size footprint) and never feeds a Row.
    Text {
        id: _labelMetrics
        visible: false
        text: "Mg"  // ascender + descender — representative cap/baseline span
        font.family:         Theme.typography.label.xs.family
        font.pixelSize:      Theme.typography.label.xs.size
        font.weight:         Theme.typography.label.xs.weight
        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
        font.capitalization: Font.AllUppercase
    }

    // Hidden, non-positioned probe: one Text per option giving each
    // segment's intrinsic label width. Summed (+ per-segment horizontal
    // padding) for root.implicitWidth. No Row, no anchors.fill — cannot
    // feed back into root size.
    Repeater {
        id: _widthProbe
        model: root.options
        Text {
            visible: false
            text:                modelData.label
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
    }

    readonly property real _segmentsWidth: {
        var total = 0
        for (var i = 0; i < _widthProbe.count; i++) {
            var t = _widthProbe.itemAt(i)
            if (t)
                total += t.implicitWidth + Theme.space[3] * 2
        }
        return total
    }

    implicitWidth:  _segmentsWidth + Theme.space[2] * 2
    implicitHeight: _labelMetrics.implicitHeight + Theme.space[1] * 2

    // Track background
    Rectangle {
        anchors.fill: parent
        color:        Theme.color.surface.base
        border.color: Theme.color.border.subtle
        border.width: 1
        radius:       Theme.radius.md
    }

    Row {
        id: trackRow
        // Positioned (NOT anchors.fill parent): the Row lays segments out
        // along x only. Height comes top-down from root.height so the Row's
        // implicit size never feeds back into root.implicitHeight.
        x: Theme.space[2]
        y: Theme.space[1]
        height: root.height - Theme.space[1] * 2
        spacing: 0

        Repeater {
            model: root.options

            Item {
                id: segItem
                width:  segLabel.implicitWidth + Theme.space[3] * 2
                height: trackRow.height

                readonly property bool _selected: modelData.value === root.current

                // Selected segment highlight
                Rectangle {
                    anchors.fill:        parent
                    // 2px inset — intentional half of Theme.space[1] (4px); no exact token
                    anchors.topMargin:   2
                    anchors.bottomMargin: 2
                    anchors.leftMargin:  index === 0 ? 2 : 0
                    anchors.rightMargin: index === root.options.length - 1 ? 2 : 0
                    color:  segItem._selected ? Theme.color.surface.raised : "transparent"
                    radius: Theme.radius.md
                }

                // Separator (right edge, not on last item)
                Rectangle {
                    visible: index < root.options.length - 1
                    anchors.right:  parent.right
                    anchors.top:    parent.top
                    anchors.bottom: parent.bottom
                    anchors.topMargin:    Theme.space[1]
                    anchors.bottomMargin: Theme.space[1]
                    width: 1
                    color: Theme.color.border.regular
                }

                Text {
                    id: segLabel
                    anchors.centerIn:    parent
                    text:                modelData.label
                    color:               segItem._selected ? Theme.color.text.primary
                                                           : Theme.color.text.muted
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        if (modelData.value !== root.current)
                            root.activated(modelData.value)
                    }
                }
            }
        }
    }
}

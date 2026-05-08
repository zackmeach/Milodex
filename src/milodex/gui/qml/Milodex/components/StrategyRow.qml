// StrategyRow.qml — Milodex strategy-bank list row.
//
// Tokens consumed (DESIGN_SYSTEM.md §7.3):
//   color.surface.base        — default background
//   color.surface.raised      — hover background
//   color.border.subtle       — default border
//   color.border.regular      — hover border
//   color.brand.accent        — selected left-accent bar (2px)
//   color.text.muted          — trade-count muted color
//   typography.data.md        — strategy ID + metric text
//   typography.data.sm        — trade count text
//   space[1], space[2], space[3], space[5] — padding + inter-column gap
//   radius.md                 — row corner radius
//   motion.fast               — hover transition
//
// StatusPill consumes: status.positive / .info / .warning / .negative,
//   typography.label.xs, radius.sm, space[1], space[2]
//
// Data expected:
//   strategyId  : string — dotted strategy identifier (e.g. "momentum.breakout.v1")
//   stage       : string — "paper" | "backtest" | "blocked" | "killed"
//   metricValue : string — pre-formatted metric string (e.g. "+1.19")
//   tradeCount  : int    — trade count (e.g. 433)
//   selected    : bool   — selected state (default false)
//   signal clicked()
//
// MOTION DISCIPLINE (DESIGN_SYSTEM.md §5.3, §8):
//   Numeric metric / trade-count values must NOT animate from one value
//   to another.  Row background/border may animate; values update instantly.

import QtQuick
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property string strategyId: ""
    property string stage: "paper"
    property string metricValue: ""
    property int    tradeCount: 0
    property bool   selected: false

    signal clicked()

    // ------------------------------------------------------------------
    // Sizing
    // ------------------------------------------------------------------

    implicitWidth:  rowLayout.implicitWidth + Theme.space[3] * 2
    implicitHeight: rowLayout.implicitHeight + Theme.space[2] * 2

    // ------------------------------------------------------------------
    // Background rectangle (child of Item)
    // ------------------------------------------------------------------

    Rectangle {
        id: bg
        anchors.fill: parent
        color:  (mouseArea.containsMouse || root.selected) ? Theme.color.surface.raised
                                                           : Theme.color.surface.base
        radius: Theme.radius.md
        border.color: mouseArea.containsMouse ? Theme.color.border.regular
                                              : Theme.color.border.subtle
        border.width: 1

        Behavior on color {
            ColorAnimation { duration: Theme.motion.fast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.motion.fast }
        }
    }

    // MouseArea for hover + click
    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        onClicked:    root.clicked()
    }

    // ------------------------------------------------------------------
    // Selected left accent bar (2px, color.brand.accent)
    // ------------------------------------------------------------------

    Rectangle {
        id: selectedAccent
        visible:       root.selected
        width:         2
        anchors.left:         parent.left
        anchors.top:          parent.top
        anchors.bottom:       parent.bottom
        anchors.topMargin:    Theme.radius.md
        anchors.bottomMargin: Theme.radius.md
        color:  Theme.color.brand.accent
    }

    // ------------------------------------------------------------------
    // Row layout
    // ------------------------------------------------------------------

    Row {
        id: rowLayout
        anchors.left:           parent.left
        anchors.right:          parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin:     Theme.space[3]
        anchors.rightMargin:    Theme.space[3]
        spacing: Theme.space[5]
        // Long strategy IDs (e.g., `momentum.daily.dual_absolute.gem_weekly.v1`)
        // can exceed the row width. clip: true masks overflow at the row
        // boundary rather than letting text spill outside the surface.
        // For full elision (with "...") a future refactor to RowLayout
        // with Layout.fillWidth on idText would be ideal.
        clip: true

        // Strategy ID — data.md mono
        Text {
            id: idText
            text:  root.strategyId
            color: Theme.color.text.primary
            font.family:    Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.weight:    Theme.typography.data.md.weight
            font.features:  Theme.typography.data.md.features
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        // Stage pill — composed from StatusPill (DESIGN_SYSTEM.md §10 principle #3)
        StatusPill {
            id: stagePill
            variant: root.stage
            text:    root.stage
            anchors.verticalCenter: parent.verticalCenter
        }

        // Primary metric — data.md mono
        Text {
            id: metricText
            text:  root.metricValue
            color: Theme.color.text.primary
            font.family:    Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.weight:    Theme.typography.data.md.weight
            font.features:  Theme.typography.data.md.features
            verticalAlignment: Text.AlignVCenter
        }

        // Trade count — data.sm mono, muted
        Text {
            id: tradeCountText
            text:  root.tradeCount + " trades"
            color: Theme.color.text.muted
            font.family:    Theme.typography.data.sm.family
            font.pixelSize: Theme.typography.data.sm.size
            font.weight:    Theme.typography.data.sm.weight
            font.features:  Theme.typography.data.sm.features
            verticalAlignment: Text.AlignVCenter
        }
    }
}

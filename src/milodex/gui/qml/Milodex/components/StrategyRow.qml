// StrategyRow.qml — Milodex strategy-bank list row.
//
// Tokens consumed (DESIGN_SYSTEM.md §7.3):
//   color.surface.base        — default background
//   color.surface.raised      — hover background
//   color.border.subtle       — default border
//   color.border.regular      — hover border
//   color.brand.accent        — selected left-accent bar (2px)
//   color.text.muted          — trade-count muted color + lifecycle-exempt badge color
//   color.text.primary        — strategy ID + metric text
//   status.negative           — gate-failure chip color
//   status.warning            — FLAGGED badge color
//   typography.data.md        — strategy ID + metric text
//   typography.data.sm        — trade count text
//   typography.data.xs        — gate-failure chip text + audit asterisk
//   typography.label.xs       — lifecycle-exempt badge + FLAGGED badge text
//   space[1], space[2], space[3], space[5] — padding + inter-column gap
//   radius.md                 — row corner radius
//   radius.sm                 — gate-failure chip radius
//   motion.fast               — hover transition
//
// StatusPill consumes: status.positive / .info / .warning / .negative,
//   typography.label.xs, radius.sm, space[1], space[2]
//
// Data expected:
//   strategyId         : string   — dotted strategy identifier (e.g. "momentum.breakout.v1")
//   stage              : string   — "paper" | "backtest" | "blocked" | "killed"
//   metricValue        : string   — pre-formatted metric string (e.g. "+1.19")
//   tradeCount         : int      — trade count (e.g. 433)
//   selected           : bool     — selected state (default false)
//   badge              : string   — optional badge label right of tradeCount (e.g. "LIFECYCLE EXEMPT")
//   gateFailures       : var      — optional list of gate-code strings (e.g. ["S", "D"])
//   auditFlag          : bool     — optional; when true renders a "*" superscript after strategyId
//   flagFailingNotRetired : bool  — optional; when true renders a "FLAGGED" warning badge
//   signal clicked()
//
// MOTION DISCIPLINE (DESIGN_SYSTEM.md §5.3, §8):
//   Numeric metric / trade-count values must NOT animate from one value
//   to another.  Row background/border may animate; values update instantly.

import QtQuick
import QtQuick.Layouts
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

    // Optional extensions — existing call sites that omit these see no change.
    // badge: non-empty string renders a small label after the tradeCount column.
    //   Typical use: "LIFECYCLE EXEMPT" on the regime strategy row.
    property string badge: ""
    // gateFailures: list of short gate-code strings rendered as inline chips
    //   between the metric and tradeCount columns.  Each chip reads "[code]".
    //   Typical use: ["S", "D", "N"] for blocked strategies (ADR 0009).
    property var    gateFailures: []
    // auditFlag: when true, renders a "*" superscript to the right of strategyId.
    //   Signals a manual audit trail event (ADR 0032).
    property bool   auditFlag: false
    // flagFailingNotRetired: when true, renders a "FLAGGED" warning badge alongside
    //   the gate chips.  Used for dual_absolute.gem_weekly per governance callout
    //   in STRATEGY_BANK.md — strategy is kept at backtest pending a methodology
    //   decision, not retired.
    property bool   flagFailingNotRetired: false

    signal clicked()

    // ------------------------------------------------------------------
    // Sizing
    // ------------------------------------------------------------------

    implicitWidth:  400
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
    // Row layout — RowLayout for fixed-width column alignment
    // ------------------------------------------------------------------

    RowLayout {
        id: rowLayout
        anchors.left:           parent.left
        anchors.right:          parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin:     Theme.space[3]
        anchors.rightMargin:    Theme.space[3]
        spacing: Theme.space[5]

        // Strategy ID column — mono, fills remaining width with elision.
        // When auditFlag is true, the ID and the asterisk sit in a Row so the
        // asterisk floats directly after the last visible character rather than
        // hanging at the end of the full column width.
        Item {
            id: idColumn
            Layout.fillWidth: true
            implicitHeight: idText.implicitHeight

            Text {
                id: idText
                anchors.left:           parent.left
                anchors.right:          auditAsterisk.visible ? auditAsterisk.left : parent.right
                anchors.verticalCenter: parent.verticalCenter
                text:  root.strategyId
                color: Theme.color.text.primary
                font.family:    Theme.typography.data.md.family
                font.pixelSize: Theme.typography.data.md.size
                font.weight:    Theme.typography.data.md.weight
                font.features:  Theme.typography.data.md.features
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }

            // Audit-trail asterisk (ADR 0032). Rendered as a superscript "*"
            // immediately after the strategy ID when auditFlag is true.
            // Hover/tooltip is out of scope for PR E.
            Text {
                id: auditAsterisk
                visible:         root.auditFlag
                anchors.left:    idText.right
                anchors.top:     parent.top
                text:            "*"
                color:           Theme.color.text.muted
                font.family:     Theme.typography.data.xs.family
                font.pixelSize:  Theme.typography.data.xs.size
            }
        }

        // Stage pill column — fixed-width slot, pill itself sizes to its label
        // so the capsule shape (DESIGN_SYSTEM.md §6.3) is preserved. Wrapping
        // the StatusPill in an Item lets RowLayout allocate the column width
        // (Theme.column.pill) without forcing the pill background to stretch
        // across the whole slot.
        Item {
            Layout.preferredWidth: Theme.column.pill
            Layout.fillHeight: true

            StatusPill {
                id: stagePill
                variant: root.stage
                text:    root.stage
                anchors.left:           parent.left
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        // Primary metric — data.md mono, right-aligned
        Text {
            id: metricText
            text:  root.metricValue
            color: Theme.color.text.primary
            font.family:    Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.weight:    Theme.typography.data.md.weight
            font.features:  Theme.typography.data.md.features
            verticalAlignment:   Text.AlignVCenter
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.metric
        }

        // Gate-failure chips (ADR 0009) + optional FLAGGED badge.
        // Rendered between metric and tradeCount columns when gateFailures is
        // non-empty.  Each chip reads "[S]", "[D]", or "[N]".
        // The flagFailingNotRetired badge sits inline in the same Row so that
        // the dual_absolute "flagged, not retired" governance note is always
        // adjacent to the failure codes.
        Row {
            id: chipsRow
            visible: root.gateFailures.length > 0 || root.flagFailingNotRetired
            spacing: Theme.space[1]
            Layout.preferredWidth: visible ? implicitWidth : 0
            verticalAlignment: Qt.AlignVCenter

            Repeater {
                model: root.gateFailures
                delegate: Item {
                    implicitWidth:  chipLabel.implicitWidth + Theme.space[1] * 2
                    implicitHeight: chipLabel.implicitHeight + Theme.space[1] * 2
                    anchors.verticalCenter: parent.verticalCenter

                    Rectangle {
                        anchors.fill: parent
                        radius: Theme.radius.sm
                        color:        Qt.rgba(Theme.status.negative.r,
                                              Theme.status.negative.g,
                                              Theme.status.negative.b, 0.12)
                        border.color: Qt.rgba(Theme.status.negative.r,
                                              Theme.status.negative.g,
                                              Theme.status.negative.b, 0.30)
                        border.width: 1
                    }

                    Text {
                        id: chipLabel
                        anchors.centerIn: parent
                        text:  "[" + modelData + "]"
                        color: Theme.status.negative
                        font.family:    Theme.typography.data.xs.family
                        font.pixelSize: Theme.typography.data.xs.size
                        font.features:  Theme.typography.data.xs.features
                    }
                }
            }

            // FLAGGED badge: dual_absolute.gem_weekly governance status —
            // strategy is kept at backtest pending a methodology resolution,
            // not retired.  Warning color (mustard) signals "review needed"
            // without the urgency of status.negative.
            Item {
                visible: root.flagFailingNotRetired
                implicitWidth:  flagLabel.implicitWidth + Theme.space[1] * 2
                implicitHeight: flagLabel.implicitHeight + Theme.space[1] * 2
                anchors.verticalCenter: parent.verticalCenter

                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radius.sm
                    color:        Qt.rgba(Theme.status.warning.r,
                                          Theme.status.warning.g,
                                          Theme.status.warning.b, 0.12)
                    border.color: Qt.rgba(Theme.status.warning.r,
                                          Theme.status.warning.g,
                                          Theme.status.warning.b, 0.30)
                    border.width: 1
                }

                Text {
                    id: flagLabel
                    anchors.centerIn: parent
                    text:  "FLAGGED"
                    color: Theme.status.warning
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }
            }
        }

        // Trade count — data.sm mono, muted, right-aligned
        Text {
            id: tradeCountText
            text:  root.tradeCount + " trades"
            color: Theme.color.text.muted
            font.family:    Theme.typography.data.sm.family
            font.pixelSize: Theme.typography.data.sm.size
            font.weight:    Theme.typography.data.sm.weight
            font.features:  Theme.typography.data.sm.features
            verticalAlignment:   Text.AlignVCenter
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.tradeCount
        }

        // Badge column — optional label right of tradeCount.
        // Non-empty badge (e.g. "LIFECYCLE EXEMPT") renders in label.xs muted.
        // Layout.preferredWidth collapses to 0 when badge is empty so the
        // empty string doesn't consume column space.
        Text {
            id: badgeText
            visible: root.badge !== ""
            text:    root.badge
            color:   Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
            verticalAlignment:   Text.AlignVCenter
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: root.badge !== "" ? 96 : 0
        }
    }
}

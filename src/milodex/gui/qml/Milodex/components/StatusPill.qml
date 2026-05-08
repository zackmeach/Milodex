// StatusPill.qml — Milodex strategy-stage status pill.
//
// Tokens consumed (DESIGN_SYSTEM.md §6.3, §7.2):
//   status.positive    — paper variant
//   status.info        — backtest variant
//   status.warning     — blocked variant
//   status.negative    — killed variant
//   radius.sm          — corner radius
//   space[1], space[2] — vertical / horizontal padding
//   typography.label.xs — uppercase, letter-spaced label
//
// Data expected:
//   variant : string — "paper" | "backtest" | "blocked" | "killed"
//   text    : string — visible label text
//
// MOTION DISCIPLINE (DESIGN_SYSTEM.md §8):
//   Status pills must NOT animate variant transitions.  When a strategy's
//   stage changes, the pill flips color instantly — the instantaneous
//   change is honest signal.  No Behavior / ColorAnimation on any
//   property derived from `variant`.
//
// Implementation note: Item is used as root rather than Rectangle because
// Qt 6.11's module-compiled type cache advertises Rectangle.data as
// list<QObject> with strict checking that rejects QQuickText and other
// QQuickItem subclasses as children in registered-module components.
// Item.data does not have this restriction.  The background Rectangle is
// a child of Item.

import QtQuick
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property string variant: "paper"
    property string text: ""

    // ------------------------------------------------------------------
    // Base color resolved from variant — no Behavior (motion discipline).
    // ------------------------------------------------------------------

    readonly property color _baseColor: {
        if (root.variant === "paper")    return Theme.status.positive
        if (root.variant === "backtest") return Theme.status.info
        if (root.variant === "blocked")  return Theme.status.warning
        if (root.variant === "killed")   return Theme.status.negative
        return Theme.status.info
    }

    // ------------------------------------------------------------------
    // Sizing driven by label
    // ------------------------------------------------------------------

    implicitWidth:  label.implicitWidth + Theme.space[2] * 2
    implicitHeight: label.implicitHeight + Theme.space[1] * 2

    // ------------------------------------------------------------------
    // Background + border rectangle (child of Item — avoids Qt 6.11 issue)
    // ------------------------------------------------------------------

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: Theme.radius.sm
        // Background: base color at 12% alpha
        color:        Qt.rgba(root._baseColor.r, root._baseColor.g, root._baseColor.b, 0.12)
        border.color: Qt.rgba(root._baseColor.r, root._baseColor.g, root._baseColor.b, 0.30)
        border.width: 1
    }

    // ------------------------------------------------------------------
    // Label
    // ------------------------------------------------------------------

    Text {
        id: label
        anchors.centerIn: parent
        text:  root.text
        color: root._baseColor
        font.family:         Theme.typography.label.xs.family
        font.pixelSize:      Theme.typography.label.xs.size
        font.weight:         Theme.typography.label.xs.weight
        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
        font.capitalization: Font.AllUppercase
    }
}

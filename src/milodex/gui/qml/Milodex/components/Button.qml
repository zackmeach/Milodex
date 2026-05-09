// Button.qml — Milodex foundational button component.
//
// Tokens consumed (DESIGN_SYSTEM.md §7.1):
//   color.brand.accent        — primary background
//   color.brand.accentHover   — primary background on hover
//   color.brand.accentPressed — primary background on pressed
//   color.text.onBrand        — primary label (high-contrast cream on oxblood/verdigris)
//   color.text.onCritical     — critical label (theme-specific contrast on status.negative)
//   color.text.primary        — secondary label
//   color.text.secondary      — ghost label
//   color.text.disabled       — disabled label
//   color.border.regular      — secondary border
//   color.border.emphasis     — secondary/danger border on hover
//   color.border.subtle       — disabled border
//   status.negative           — danger label + border / critical filled background
//   status.negativeHover      — danger border on hover/pressed / critical hover background
//   status.negativePressed    — critical pressed background
//   typography.body.md        — label font (weight overridden to 500 = Medium)
//   space[2], space[3]        — vertical / horizontal padding
//   radius.md                 — corner radius
//   motion.fast               — hover / pressed transition duration
//
// Data expected:
//   variant  : string  — "primary" | "secondary" | "ghost" | "danger" | "critical"
//                        (default: "primary")
//   text     : string  — button label
//   enabled  : bool    — inherited from Item; disables interaction when false
//   signal clicked()
//
// Variant hierarchy (DESIGN_SYSTEM.md §7.1):
//   critical  — stop-the-world, requires deliberate operator action
//               (kill switch, kill-switch reset, manual force-close).
//               Filled rust background, highest visual emphasis.
//   primary   — main happy-path action.  Filled accent background.
//   danger    — destructive but bounded (delete row, cancel order).
//               Outlined rust, no fill.
//   secondary — alternative.  Outlined neutral.
//   ghost     — dismissive.  No chrome, just text.
//
import QtQuick
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property string variant: "primary"
    property string text: ""

    signal clicked()

    // ------------------------------------------------------------------
    // Internal state via MouseArea
    // ------------------------------------------------------------------

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: root.enabled
        enabled:      root.enabled
        onClicked:    root.clicked()
    }

    readonly property bool _hovered: mouseArea.containsMouse
    readonly property bool _pressed: mouseArea.pressed

    // ------------------------------------------------------------------
    // Sizing
    // ------------------------------------------------------------------

    // Critical buttons get extra horizontal padding so the letter-spaced
    // uppercase label has breathing room (matches the brief's typographic
    // weighting — the kill-switch-class action should feel substantial).
    implicitWidth:  contentText.implicitWidth + (root._isCritical ? Theme.space[4] : Theme.space[3]) * 2
    implicitHeight: contentText.implicitHeight + Theme.space[2] * 2

    // ------------------------------------------------------------------
    // Background color computed properties
    // ------------------------------------------------------------------

    readonly property color _bgColor: {
        if (!root.enabled) return "transparent"
        if (root.variant === "primary") {
            if (root._pressed) return Theme.color.brand.accentPressed
            if (root._hovered) return Theme.color.brand.accentHover
            return Theme.color.brand.accent
        }
        if (root.variant === "critical") {
            if (root._pressed) return Theme.status.negativePressed
            if (root._hovered) return Theme.status.negativeHover
            return Theme.status.negative
        }
        return "transparent"
    }

    readonly property color _borderColor: {
        if (!root.enabled) return Theme.color.border.subtle
        if (root.variant === "secondary") {
            if (root._hovered || root._pressed) return Theme.color.border.emphasis
            return Theme.color.border.regular
        }
        if (root.variant === "danger") {
            if (root._hovered || root._pressed) return Theme.status.negativeHover
            return Theme.status.negative
        }
        return "transparent"
    }

    readonly property int _borderWidth: {
        if (root.variant === "secondary" || root.variant === "danger") return 1
        return 0
    }

    readonly property color _textColor: {
        if (!root.enabled) return Theme.color.text.disabled
        if (root.variant === "primary") return Theme.color.text.onBrand
        if (root.variant === "critical") return Theme.color.text.onCritical
        if (root.variant === "secondary") return Theme.color.text.primary
        if (root.variant === "ghost") {
            return (root._hovered || root._pressed) ? Theme.color.text.primary
                                                    : Theme.color.text.secondary
        }
        if (root.variant === "danger") return Theme.status.negative
        return Theme.color.text.primary
    }

    // ------------------------------------------------------------------
    // Background rectangle (child of Item)
    // ------------------------------------------------------------------

    Rectangle {
        id: bg
        anchors.fill: parent
        color:        root._bgColor
        radius:       Theme.radius.md
        border.color: root._borderColor
        border.width: root._borderWidth

        Behavior on color {
            ColorAnimation { duration: Theme.motion.fast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.motion.fast }
        }
    }

    // ------------------------------------------------------------------
    // Label
    // ------------------------------------------------------------------

    // Critical-variant typography distinguishers (per DESIGN_SYSTEM.md §7.1
    // and the Bench brief): the `critical` variant alone among the five
    // renders uppercase with widened letter-spacing.  This is the visual
    // signature of "this action is structurally different from every other
    // action on the surface" — paired with filled rust + DemiBold weight,
    // it announces stop-the-world intent without iconography.
    readonly property bool _isCritical: root.variant === "critical"

    Text {
        id: contentText
        anchors.centerIn: parent
        text:  root.text
        color: root._textColor
        font.family:    Theme.typography.body.md.family
        font.pixelSize: Theme.typography.body.md.size
        font.weight:    root._isCritical ? Font.DemiBold : Font.Medium
        font.letterSpacing:  root._isCritical ? Theme.typography.label.xs.criticalTrack : 0
        font.capitalization: root._isCritical ? Font.AllUppercase : Font.MixedCase
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment:   Text.AlignVCenter

        Behavior on color {
            ColorAnimation { duration: Theme.motion.fast }
        }
    }
}

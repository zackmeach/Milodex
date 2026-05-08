// Button.qml — Milodex foundational button component.
//
// Tokens consumed (DESIGN_SYSTEM.md §7.1):
//   color.brand.accent        — primary background
//   color.brand.accentHover   — primary background on hover
//   color.brand.accentPressed — primary background on pressed
//   color.text.onBrand        — primary label (high-contrast cream on oxblood/verdigris)
//   color.text.primary        — secondary label
//   color.text.secondary      — ghost label
//   color.text.disabled       — disabled label
//   color.border.regular      — secondary border
//   color.border.emphasis     — secondary/danger border on hover
//   color.border.subtle       — disabled border
//   status.negative           — danger label + border
//   status.negativeHover      — danger border on hover/pressed
//   typography.body.md        — label font (weight overridden to 500 = Medium)
//   space[2], space[3]        — vertical / horizontal padding
//   radius.md                 — corner radius
//   motion.fast               — hover / pressed transition duration
//
// Data expected:
//   variant  : string  — "primary" | "secondary" | "ghost" | "danger"
//                        (default: "primary")
//   text     : string  — button label
//   enabled  : bool    — disables interaction when false
//   signal clicked()
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
    property bool   enabled: true

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

    implicitWidth:  contentText.implicitWidth + Theme.space[3] * 2
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

    Text {
        id: contentText
        anchors.centerIn: parent
        text:  root.text
        color: root._textColor
        font.family:    Theme.typography.body.md.family
        font.pixelSize: Theme.typography.body.md.size
        font.weight:    Font.Medium  // 500 per §7.1
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment:   Text.AlignVCenter

        Behavior on color {
            ColorAnimation { duration: Theme.motion.fast }
        }
    }
}

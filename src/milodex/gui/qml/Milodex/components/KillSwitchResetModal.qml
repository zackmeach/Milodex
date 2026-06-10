// KillSwitchResetModal.qml — type-to-confirm kill-switch reset modal (ADR 0005).
//
// Extracted from AnchorSurface.qml (HR-4).  Reachable from the RiskStrip
// badge kill-switch indicator and the Risk Office drawer KILL SWITCH section.
//
// Token contract (unchanged from AnchorSurface):
//   resetKillSwitchToken  → OperationalState.resetKillSwitchToken  (Q_PROPERTY)
//   reset_kill_switch(token) → OperationalState.reset_kill_switch  (Slot)
//
// Usage:
//   KillSwitchResetModal {
//       id: ksResetModal
//       open: someCondition
//       onCloseRequested: open = false
//   }

import QtQuick
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public interface
    // ------------------------------------------------------------------

    property bool open: false

    signal closeRequested()

    // ------------------------------------------------------------------
    // Geometry — overlay fills parent
    // ------------------------------------------------------------------

    anchors.fill: parent
    visible: root.open

    // Clear the confirmation input each time the dialog closes so a
    // re-open always starts empty (no lingering text from a prior attempt).
    onOpenChanged: {
        if (!root.open) confirmInput.text = ""
    }

    // ------------------------------------------------------------------
    // Block click-through behind the dialog
    // ------------------------------------------------------------------

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onClicked: { /* swallow — prevent click-through to surface behind */ }
    }

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.55)
    }

    // ------------------------------------------------------------------
    // Modal card
    // ------------------------------------------------------------------

    Surface {
        anchors.centerIn: parent
        width:  Math.min(parent.width  - Theme.space[6] * 2, 480)
        height: Math.min(parent.height - Theme.space[6] * 2, 280)

        Column {
            anchors.fill: parent
            spacing: Theme.space[3]

            Text {
                width: parent.width
                text: "Reset kill switch?"
                color: Theme.color.text.primary
                font.family:    Theme.typography.display.sm.family
                font.pixelSize: Theme.typography.display.sm.size
                font.weight:    Theme.typography.display.sm.weight
            }

            Text {
                width: parent.width
                text: "This will allow the kill switch to be cleared. " +
                      "Trading will resume only when explicitly re-enabled. " +
                      "Continue?"
                color: Theme.color.text.secondary
                font.family:    Theme.typography.body.md.family
                font.pixelSize: Theme.typography.body.md.size
                wrapMode:       Text.WordWrap
            }

            // Type-to-confirm input
            Text {
                width: parent.width
                text: "Type “" + OperationalState.resetKillSwitchToken + "” to enable Reset"
                color: Theme.color.text.muted
                font.family:    Theme.typography.body.sm.family
                font.pixelSize: Theme.typography.body.sm.size
            }

            Rectangle {
                width: parent.width
                height: confirmInput.implicitHeight + Theme.space[2] * 2
                color: Theme.color.surface.base
                border.color: Theme.color.border.regular
                border.width: 1
                radius: Theme.radius.md

                // Placeholder hint -- shown when the input is empty.
                Text {
                    anchors {
                        left: parent.left
                        right: parent.right
                        verticalCenter: parent.verticalCenter
                        leftMargin: Theme.space[2]
                        rightMargin: Theme.space[2]
                    }
                    visible: confirmInput.text === ""
                    text: OperationalState.resetKillSwitchToken
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size
                }

                TextInput {
                    id: confirmInput
                    anchors {
                        left: parent.left
                        right: parent.right
                        verticalCenter: parent.verticalCenter
                        leftMargin: Theme.space[2]
                        rightMargin: Theme.space[2]
                    }
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size
                    clip: true
                }
            }

            Row {
                spacing: Theme.space[3]
                anchors.right: parent.right

                Button {
                    variant: "secondary"
                    text:    "Cancel"
                    onClicked: root.closeRequested()
                }
                Button {
                    variant: "critical"
                    text:    "Yes, reset"
                    // Enabled only when the operator has typed the exact token.
                    // Prevents muscle-memory click-through; the Button disables
                    // its MouseArea so no click fires unless the condition holds.
                    enabled: confirmInput.text === OperationalState.resetKillSwitchToken
                    // The critical variant sets background to transparent when
                    // disabled (Button.qml _bgColor fallback), which would make the
                    // button invisible.  Override opacity so the button remains
                    // visually present but clearly inactive.
                    opacity: enabled ? 1.0 : 0.5
                    onClicked: {
                        OperationalState.reset_kill_switch(OperationalState.resetKillSwitchToken)
                        root.closeRequested()
                    }
                }
            }
        }
    }
}

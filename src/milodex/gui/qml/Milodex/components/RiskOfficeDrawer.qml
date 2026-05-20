// RiskOfficeDrawer.qml — slide-in risk-office side drawer (PR-7c / ADR 0054).
//
// Three sections:
//   RISK PROFILE  — inspect active profile; switch with confirmation gate
//   TIME FORMAT   — toggle 24-HOUR / 12-HOUR display (Task 37)
//   SYSTEM        — Quit handler (Task 37)
//
// Tokens consumed:
//   color.surface.canvas / .base / .raised — backgrounds
//   color.border.regular / .emphasis       — border + dividers
//   color.text.primary / .secondary / .muted / .disabled — labels
//   color.brand.accent                     — active-profile highlight ring + quit button
//   color.brand.primary                    — section eyebrow
//   color.text.onBrand                     — text on oxblood
//   status.negative / .negativeHover       — refusal error message
//   typography.label.xs / .sm             — eyebrows, button labels
//   typography.body.md                     — profile names
//   space[1..5]                            — padding, gaps

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public interface
    // ------------------------------------------------------------------

    property bool open: false

    // activeProfile — reflects the bridge's current profile.
    // Seeded from Main.qml via onSwitchApplied; initialised on Component.onCompleted.
    property string activeProfile: "conservative"

    // Signals relayed up to Main.qml (which calls RiskProfileBridge.attemptSwitch).
    signal switchRequested(string targetProfile, string confirmationToken)
    signal quitRequested()

    // ------------------------------------------------------------------
    // Geometry — slides in from the right
    // ------------------------------------------------------------------

    width: 320
    anchors.top:    parent ? parent.top    : undefined
    anchors.bottom: parent ? parent.bottom : undefined
    anchors.right:  parent ? parent.right  : undefined
    anchors.rightMargin: root.open ? 0 : -root.width
    Behavior on anchors.rightMargin {
        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
    }

    // Escape closes
    Keys.onEscapePressed: function(event) {
        root.open = false
        event.accepted = true
    }

    // ------------------------------------------------------------------
    // Background panel
    // ------------------------------------------------------------------

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.base
        border.color: Theme.color.border.regular
        border.width: 1
    }

    // ------------------------------------------------------------------
    // Internal state — which profile card is expanded for confirmation
    // ------------------------------------------------------------------

    property string _pendingTarget: ""
    property string _typedToken: ""
    property string _errorMessage: ""

    Timer {
        id: errorDismissTimer
        interval: 5000
        onTriggered: root._errorMessage = ""
    }

    function showError(msg) {
        root._errorMessage = msg
        errorDismissTimer.restart()
    }

    function clearPending() {
        root._pendingTarget = ""
        root._typedToken = ""
        root._errorMessage = ""
        errorDismissTimer.stop()
    }

    // ------------------------------------------------------------------
    // Scroll container — drawer content may exceed window height
    // ------------------------------------------------------------------

    Flickable {
        anchors.fill: parent
        anchors.margins: 1  // inside border
        contentWidth: width
        contentHeight: drawerBody.implicitHeight + Theme.space[5] * 2
        flickableDirection: Flickable.VerticalFlick
        clip: true

        ColumnLayout {
            id: drawerBody
            width: parent.width
            anchors.top: parent.top
            anchors.topMargin: Theme.space[5]
            anchors.left: parent.left
            anchors.leftMargin: Theme.space[4]
            anchors.right: parent.right
            anchors.rightMargin: Theme.space[4]
            spacing: Theme.space[5]

            // ==============================================================
            // RISK PROFILE section
            // ==============================================================

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.space[2]

                // Section eyebrow
                Text {
                    text: "RISK PROFILE"
                    color: Theme.color.brand.primary
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Font.DemiBold
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.6
                    font.capitalization: Font.AllUppercase
                }

                // Profile cards
                Repeater {
                    model: ["conservative", "standard", "aggressive"]

                    delegate: ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        readonly property string profileName: modelData
                        readonly property bool isActive: root.activeProfile === profileName
                        readonly property bool isPending: root._pendingTarget === profileName

                        // Profile row
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: profileRow.implicitHeight + Theme.space[2] * 2
                            color: isActive ? Theme.color.brand.accent
                                            : (profileRowMouse.containsMouse ? Theme.color.surface.raised
                                                                              : Theme.color.surface.canvas)
                            border.color: isActive ? Theme.color.brand.accent : Theme.color.border.regular
                            border.width: 1
                            radius: Theme.radius.sm

                            Behavior on color {
                                ColorAnimation { duration: Theme.motion.fast }
                            }

                            RowLayout {
                                id: profileRow
                                anchors {
                                    left:   parent.left
                                    right:  parent.right
                                    top:    parent.top
                                    leftMargin:   Theme.space[3]
                                    rightMargin:  Theme.space[3]
                                    topMargin:    Theme.space[2]
                                    bottomMargin: Theme.space[2]
                                }
                                // anchors.bottom not set; height is implicit

                                Text {
                                    text: profileName.toUpperCase()
                                    color: isActive ? Theme.color.text.onBrand : Theme.color.text.primary
                                    font.family:    Theme.typography.body.md.family
                                    font.pixelSize: Theme.typography.body.md.size
                                    font.weight:    Font.Medium
                                }

                                Item { Layout.fillWidth: true }

                                Text {
                                    visible: isActive
                                    text: "ACTIVE"
                                    color: Theme.color.text.onBrand
                                    font.family:        Theme.typography.label.xs.family
                                    font.pixelSize:     Theme.typography.label.xs.size
                                    font.letterSpacing: 0.6
                                    font.capitalization: Font.AllUppercase
                                }
                            }

                            MouseArea {
                                id: profileRowMouse
                                anchors.fill: parent
                                hoverEnabled: !isActive
                                cursorShape: isActive ? Qt.ArrowCursor : Qt.PointingHandCursor
                                onClicked: {
                                    if (isActive) return
                                    root._errorMessage = ""
                                    errorDismissTimer.stop()
                                    if (root._pendingTarget === profileName) {
                                        // toggle closed
                                        root.clearPending()
                                    } else {
                                        root._pendingTarget = profileName
                                        root._typedToken = ""
                                    }
                                }
                            }
                        }

                        // Inline confirmation panel (visible when pending)
                        ColumnLayout {
                            visible: isPending
                            Layout.fillWidth: true
                            spacing: Theme.space[2]
                            Layout.topMargin: Theme.space[1]

                            readonly property bool isElevation: {
                                var order = {"conservative": 0, "standard": 1, "aggressive": 2}
                                return (order[profileName] || 0) > (order[root.activeProfile] || 0)
                            }

                            // Typed-confirmation input (elevation only)
                            Rectangle {
                                id: typedInputBox
                                visible: parent.isElevation
                                Layout.fillWidth: true
                                implicitHeight: Theme.typography.body.md.size + Theme.space[2] * 2 + 4
                                color: Theme.color.surface.canvas
                                border.color: Theme.color.border.regular
                                border.width: 1
                                radius: Theme.radius.sm

                                // Placeholder text (visible when input is empty)
                                Text {
                                    anchors {
                                        left:   parent.left
                                        right:  parent.right
                                        verticalCenter: parent.verticalCenter
                                        leftMargin:  Theme.space[3]
                                        rightMargin: Theme.space[3]
                                    }
                                    visible: typedInput.text === ""
                                    text: "Type '" + profileName + "' to confirm."
                                    color: Theme.color.text.disabled
                                    font.family:    Theme.typography.body.md.family
                                    font.pixelSize: Theme.typography.body.md.size
                                }

                                TextInput {
                                    id: typedInput
                                    anchors {
                                        left:   parent.left
                                        right:  parent.right
                                        verticalCenter: parent.verticalCenter
                                        leftMargin:  Theme.space[3]
                                        rightMargin: Theme.space[3]
                                    }
                                    color: Theme.color.text.primary
                                    font.family:    Theme.typography.body.md.family
                                    font.pixelSize: Theme.typography.body.md.size
                                    onTextChanged: root._typedToken = text
                                }
                            }

                            // Confirm button
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: confirmLabel.implicitHeight + Theme.space[2] * 2
                                color: confirmMouse.containsMouse ? Theme.color.brand.accent
                                                                   : Theme.color.surface.raised
                                border.color: Theme.color.brand.accent
                                border.width: 1
                                radius: Theme.radius.sm

                                Behavior on color {
                                    ColorAnimation { duration: Theme.motion.fast }
                                }

                                Text {
                                    id: confirmLabel
                                    anchors.centerIn: parent
                                    text: "CONFIRM"
                                    color: confirmMouse.containsMouse
                                           ? Theme.color.text.onBrand
                                           : Theme.color.text.primary
                                    font.family:        Theme.typography.label.xs.family
                                    font.pixelSize:     Theme.typography.label.xs.size
                                    font.weight:        Font.DemiBold
                                    font.letterSpacing: 0.6
                                    font.capitalization: Font.AllUppercase
                                    Behavior on color {
                                        ColorAnimation { duration: Theme.motion.fast }
                                    }
                                }

                                MouseArea {
                                    id: confirmMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        var target = profileName
                                        var token
                                        if (parent.parent.isElevation) {
                                            token = root._typedToken
                                        } else {
                                            token = "confirm_reduction"
                                        }
                                        root.clearPending()
                                        root.switchRequested(target, token)
                                    }
                                }
                            }
                        }
                    }
                }

                // Inline refusal error
                Text {
                    visible: root._errorMessage !== ""
                    text: root._errorMessage
                    color: Theme.status.negative
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }

            // Section divider
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: Theme.color.border.regular
            }

            // ==============================================================
            // TIME FORMAT section (wired in Task 37)
            // ==============================================================

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.space[2]

                Text {
                    text: "TIME FORMAT"
                    color: Theme.color.brand.primary
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Font.DemiBold
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.6
                    font.capitalization: Font.AllUppercase
                }

                // Placeholder — wired in Task 37
                Text {
                    id: timeFmtPlaceholder
                    text: "(toggle wired in Task 37)"
                    color: Theme.color.text.disabled
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size
                }
            }

            // Section divider
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: Theme.color.border.regular
            }

            // ==============================================================
            // SYSTEM section (quit handler wired in Task 37)
            // ==============================================================

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.space[2]

                Text {
                    text: "SYSTEM"
                    color: Theme.color.brand.primary
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Font.DemiBold
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.6
                    font.capitalization: Font.AllUppercase
                }

                // Placeholder — wired in Task 37
                Text {
                    text: "(quit button wired in Task 37)"
                    color: Theme.color.text.disabled
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size
                }

                // Bottom spacer
                Item { height: Theme.space[4] }
            }
        }
    }
}

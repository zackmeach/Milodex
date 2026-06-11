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
    // Operator-driven mutation request — Main.qml writes sessionBag.timeFormat in response.
    signal timeFormatRequested(string format)
    // Operator-driven reap-interval change — Main.qml calls OrphanReaperController.persistInterval.
    signal reapIntervalRequested(int seconds)
    // HR-4: emitted when the operator presses "Reset kill switch" in the drawer.
    // Main.qml opens KillSwitchResetModal in response.
    signal killSwitchResetRequested()

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

    // sessionBag is bound by Main.qml. Standalone test harnesses leave it
    // null, in which case timeFormat falls back to the "24h" default.
    property var sessionBag: null

    // timeFormat: read-only mirror of sessionBag.timeFormat. sessionBag is the
    // sole writer; the drawer requests changes via timeFormatRequested above,
    // which Main.qml translates into a sessionBag write.
    readonly property string timeFormat: sessionBag ? sessionBag.timeFormat : "24h"

    // reapIntervalSeconds: read-only mirror of the periodic-reaper interval. Guarded
    // because standalone QML harnesses (the load-smoke test) do not register the
    // OrphanReaperController singleton — in that case it falls back to the 60s default.
    readonly property int reapIntervalSeconds:
        (typeof OrphanReaperController !== "undefined") ? OrphanReaperController.intervalSeconds : 60

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
            // HR-4: KILL SWITCH section — only visible when active.
            // The operator presses the button here; Main.qml opens
            // KillSwitchResetModal in response to killSwitchResetRequested.
            // Guarded: standalone QML harnesses (load-smoke) do not register
            // OperationalState — in that case the section is hidden.
            // ==============================================================

            ColumnLayout {
                id: killSwitchSection
                Layout.fillWidth: true
                spacing: Theme.space[2]
                visible: {
                    if (typeof OperationalState === "undefined") return false
                    return OperationalState.killSwitchActive
                }

                // Section eyebrow
                Text {
                    text: "KILL SWITCH"
                    color: Theme.status.negative
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Font.DemiBold
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.6
                    font.capitalization: Font.AllUppercase
                }

                // Status line
                Text {
                    text: "Kill switch is ACTIVE — trading halted."
                    color: Theme.status.negative
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                // Reset affordance button
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: ksResetLabel.implicitHeight + Theme.space[2] * 2
                    color: ksResetMouse.containsMouse ? Theme.status.negativeHover
                                                      : Theme.color.surface.raised
                    border.color: Theme.status.negative
                    border.width: 1
                    radius: Theme.radius.sm

                    Behavior on color {
                        ColorAnimation { duration: Theme.motion.fast }
                    }

                    Text {
                        id: ksResetLabel
                        anchors.centerIn: parent
                        text: "RESET KILL SWITCH"
                        color: ksResetMouse.containsMouse
                               ? Theme.color.text.onBrand
                               : Theme.status.negative
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
                        id: ksResetMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.killSwitchResetRequested()
                    }
                }
            }

            // Kill-switch section divider (only when section is visible)
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: Theme.color.border.regular
                visible: killSwitchSection.visible
            }

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
                            id: confirmPanel
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
                                visible: confirmPanel.isElevation
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
                                        if (confirmPanel.isElevation) {
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
                Layout.preferredHeight: 1
                color: Theme.color.border.regular
            }

            // ==============================================================
            // TIME FORMAT section
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

                // Two radio-like buttons: 24-HOUR / 12-HOUR.
                // Toggle emits root.timeFormatRequested; Main.qml writes
                // sessionBag.timeFormat in response. Drawer's timeFormat is a
                // readonly mirror.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space[2]

                    Repeater {
                        model: [
                            { label: "24-HOUR", value: "24h" },
                            { label: "12-HOUR", value: "12h" }
                        ]

                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: fmtBtnLabel.implicitHeight + Theme.space[2] * 2
                            readonly property bool isSelected: root.timeFormat === modelData.value
                            color: isSelected ? Theme.color.brand.accent
                                              : (fmtBtnMouse.containsMouse ? Theme.color.surface.raised
                                                                            : Theme.color.surface.canvas)
                            border.color: isSelected ? Theme.color.brand.accent : Theme.color.border.regular
                            border.width: 1
                            radius: Theme.radius.sm

                            Behavior on color {
                                ColorAnimation { duration: Theme.motion.fast }
                            }

                            Text {
                                id: fmtBtnLabel
                                anchors.centerIn: parent
                                text: modelData.label
                                color: isSelected ? Theme.color.text.onBrand : Theme.color.text.primary
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: 0.4
                                font.capitalization: Font.AllUppercase
                                Behavior on color {
                                    ColorAnimation { duration: Theme.motion.fast }
                                }
                            }

                            MouseArea {
                                id: fmtBtnMouse
                                anchors.fill: parent
                                hoverEnabled: !isSelected
                                cursorShape: isSelected ? Qt.ArrowCursor : Qt.PointingHandCursor
                                onClicked: {
                                    if (!isSelected) {
                                        root.timeFormatRequested(modelData.value)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Section divider
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: Theme.color.border.regular
            }

            // ==============================================================
            // RUNNER HEALTH section
            // ==============================================================

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.space[2]

                Text {
                    text: "RUNNER HEALTH"
                    color: Theme.color.brand.primary
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Font.DemiBold
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.6
                    font.capitalization: Font.AllUppercase
                }

                // Three presets: 30s / 60s / 5 MIN — how often phantom runner rows
                // self-clear. Emits root.reapIntervalRequested; Main.qml calls
                // OrphanReaperController.persistInterval. The delegate binds only
                // root.reapIntervalSeconds (a guarded mirror), never the singleton
                // directly, so the load-smoke harness (no controller) stays clean.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space[2]

                    Repeater {
                        model: [
                            { label: "30s",   value: 30 },
                            { label: "60s",   value: 60 },
                            { label: "5 MIN", value: 300 }
                        ]

                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: reapBtnLabel.implicitHeight + Theme.space[2] * 2
                            readonly property bool isSelected: root.reapIntervalSeconds === modelData.value
                            color: isSelected ? Theme.color.brand.accent
                                              : (reapBtnMouse.containsMouse ? Theme.color.surface.raised
                                                                            : Theme.color.surface.canvas)
                            border.color: isSelected ? Theme.color.brand.accent : Theme.color.border.regular
                            border.width: 1
                            radius: Theme.radius.sm

                            Behavior on color {
                                ColorAnimation { duration: Theme.motion.fast }
                            }

                            Text {
                                id: reapBtnLabel
                                anchors.centerIn: parent
                                text: modelData.label
                                color: isSelected ? Theme.color.text.onBrand : Theme.color.text.primary
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: 0.4
                                font.capitalization: Font.AllUppercase
                                Behavior on color {
                                    ColorAnimation { duration: Theme.motion.fast }
                                }
                            }

                            MouseArea {
                                id: reapBtnMouse
                                anchors.fill: parent
                                hoverEnabled: !isSelected
                                cursorShape: isSelected ? Qt.ArrowCursor : Qt.PointingHandCursor
                                onClicked: {
                                    if (!isSelected) {
                                        root.reapIntervalRequested(modelData.value)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Section divider
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: Theme.color.border.regular
            }

            // ==============================================================
            // SYSTEM section
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

                // QUIT MILODEX button (oxblood)
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: quitLabel.implicitHeight + Theme.space[2] * 2
                    color: quitMouse.containsMouse ? Theme.color.brand.accentHover
                                                   : Theme.color.brand.accent
                    border.color: Theme.color.brand.accentPressed
                    border.width: 1
                    radius: Theme.radius.sm

                    Behavior on color {
                        ColorAnimation { duration: Theme.motion.fast }
                    }

                    Text {
                        id: quitLabel
                        anchors.centerIn: parent
                        text: "QUIT MILODEX"
                        color: Theme.color.text.onBrand
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Font.DemiBold
                        font.letterSpacing: 0.6
                        font.capitalization: Font.AllUppercase
                    }

                    MouseArea {
                        id: quitMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.quitRequested()
                    }
                }

                // Bottom spacer
                Item { Layout.preferredHeight: Theme.space[4] }
            }
        }
    }
}

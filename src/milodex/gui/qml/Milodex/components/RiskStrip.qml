// RiskStrip.qml — compact institutional risk posture.
//
// PR-7c: The left-hand badge ("RISK OFFICE · <PROFILE>") is now a
// clickable affordance that opens the Risk Office drawer.  A separate
// badgeClicked signal lets Main.qml toggle the drawer without a direct
// reference between RiskStrip and the drawer.  The activeProfileName
// property carries the current profile label into the badge copy.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Rectangle {
    id: root

    property bool killSwitchActive: false
    property bool marketOpen: false
    property string brokerStatus: "stale"
    property string tradingMode: "paper"
    property string lastRefreshedAt: ""
    property string exposureText: "No real exposure"
    property string activeProfileName: "conservative"  // PR-7c: drives badge copy

    signal badgeClicked()  // PR-7c: emitted when Risk Office badge is pressed

    readonly property bool brokerConnected: root.brokerStatus === "connected"
    readonly property color postureColor: root.killSwitchActive ? Theme.status.negative : Theme.status.positive
    readonly property string brokerCopy: root.brokerConnected ? "Broker connected"
                                      : root.brokerStatus === "error" ? "Broker error"
                                      : "Broker stale"
    readonly property string modeCopy: root.tradingMode === "live" ? "Live mode"
                                : root.tradingMode === "micro_live" ? "Micro live"
                                : root.tradingMode === "paper" ? "Paper only"
                                : root.tradingMode
    readonly property string postureCopy: root.killSwitchActive
        ? "Kill switch fired | Manual reset required | Trading halted"
        : "Guard ready | " + root.modeCopy + " | " + root.brokerCopy + " | " + root.exposureText

    implicitWidth: stripRow.implicitWidth + Theme.space[4] * 2
    implicitHeight: stripRow.implicitHeight + Theme.space[2] * 2
    color: Theme.color.surface.base
    border.color: root.killSwitchActive ? Theme.status.negative : Theme.color.border.regular
    border.width: 1
    radius: Theme.radius.md

    RowLayout {
        id: stripRow
        anchors.centerIn: parent
        spacing: Theme.space[2]

        Rectangle {
            Layout.preferredWidth: 6
            Layout.preferredHeight: 6
            radius: 3
            color: root.postureColor
        }

        // PR-7c: clickable badge — opens Risk Office drawer.
        Item {
            implicitWidth: badgeText.implicitWidth
            implicitHeight: badgeText.implicitHeight

            Text {
                id: badgeText
                text: "RISK OFFICE · " + root.activeProfileName.toUpperCase()
                color: badgeMouse.containsMouse
                       ? Theme.color.brand.primary
                       : Theme.color.text.primary
                font.family:        Theme.typography.label.xs.family
                font.pixelSize:     Theme.typography.label.xs.size
                font.weight:        Font.DemiBold
                font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.4
                font.capitalization: Font.AllUppercase
                Behavior on color {
                    ColorAnimation { duration: Theme.motion.fast }
                }
            }

            MouseArea {
                id: badgeMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.badgeClicked()
            }
        }

        Rectangle {
            Layout.preferredWidth: 1
            Layout.preferredHeight: riskCopy.implicitHeight
            color: Theme.color.border.regular
        }

        Text {
            id: riskCopy
            text: root.postureCopy
            color: root.killSwitchActive ? Theme.status.negative : Theme.color.text.secondary
            font.family:        Theme.typography.label.xs.family
            font.pixelSize:     Theme.typography.label.xs.size
            font.weight:        Theme.typography.label.xs.weight
            font.letterSpacing: 0.4
            font.capitalization: Font.AllUppercase
        }
    }
}

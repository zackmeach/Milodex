// RiskStrip.qml — compact institutional risk posture.

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

        Text {
            text: "RISK OFFICE"
            color: Theme.color.text.primary
            font.family:        Theme.typography.label.xs.family
            font.pixelSize:     Theme.typography.label.xs.size
            font.weight:        Font.DemiBold
            font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.4
            font.capitalization: Font.AllUppercase
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

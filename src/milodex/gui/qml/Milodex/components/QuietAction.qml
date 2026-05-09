// QuietAction.qml — low-risk, low-noise action affordance.

import QtQuick
import Milodex 1.0

Rectangle {
    id: root

    property alias text: label.text
    property bool hovered: mouseArea.containsMouse
    signal clicked()

    implicitWidth: label.implicitWidth + Theme.space[3] * 2
    implicitHeight: label.implicitHeight + Theme.space[2] * 1.5
    color: root.enabled && root.hovered ? Theme.color.surface.raised : "transparent"
    border.color: root.enabled && root.hovered ? Theme.color.border.emphasis : Theme.color.border.regular
    border.width: 1
    radius: Theme.radius.sm
    opacity: root.enabled ? 1.0 : 0.55

    Text {
        id: label
        anchors.centerIn: parent
        color: root.enabled ? Theme.color.text.primary : Theme.color.text.disabled
        font.family:        Theme.typography.label.xs.family
        font.pixelSize:     Theme.typography.label.xs.size
        font.weight:        Theme.typography.label.xs.weight
        font.letterSpacing: Theme.typography.label.xs.letterSpacing
        font.capitalization: Font.AllUppercase
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        enabled: root.enabled
        cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.clicked()
    }
}

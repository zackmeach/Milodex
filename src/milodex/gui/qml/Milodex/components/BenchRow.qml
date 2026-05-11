// BenchRow.qml - sparse row for the full-width Bench ledger.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    property string strategyName: ""
    property string strategyId: ""
    property string stage: "paper"
    property string sharpe: "-"
    property string maxDD: "-"
    property string tradeCount: "-"
    property string metaConfigKey: ""
    property string metaStage: ""
    property string metaEvidence: ""
    property bool dragging: false

    signal actionClicked(var anchorItem)
    signal moveRequested(real localY)

    readonly property bool _isIdle: stage === "idle"

    implicitHeight: 78
    z: dragging ? 20 : 0
    scale: dragging ? 1.006 : 1.0
    transformOrigin: Item.Center

    Behavior on scale {
        NumberAnimation { duration: Theme.motion.fast }
    }

    Rectangle {
        anchors.fill: parent
        color: mouseArea.containsMouse || root.dragging
               ? Qt.rgba(Theme.color.surface.raised.r,
                         Theme.color.surface.raised.g,
                         Theme.color.surface.raised.b,
                         root.dragging ? 0.72 : 0.42)
               : "transparent"
        radius: 0
        Behavior on color { ColorAnimation { duration: Theme.motion.fast } }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        propagateComposedEvents: true
        onClicked: (mouse) => mouse.accepted = false
    }

    Item {
        id: handleSlot
        width: Theme.space[5]
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        Text {
            anchors.centerIn: parent
            text: "::"
            color: Theme.color.text.muted
            opacity: handleMouse.containsMouse || root.dragging || mouseArea.containsMouse ? 0.72 : 0.0
            font.family: Theme.typography.data.sm.family
            font.pixelSize: Theme.typography.data.sm.size
            font.letterSpacing: 1.2
            rotation: 90
            Behavior on opacity { NumberAnimation { duration: Theme.motion.fast } }
        }

        MouseArea {
            id: handleMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.SizeVerCursor
            onPressed: root.dragging = true
            onPositionChanged: {
                if (!root.dragging) return
                var point = mapToItem(root.parent, mouse.x, mouse.y)
                root.moveRequested(point.y)
            }
            onReleased: root.dragging = false
            onCanceled: root.dragging = false
        }
    }

    RowLayout {
        id: rowLayout
        anchors.fill: parent
        anchors.leftMargin: Theme.space[5]
        anchors.rightMargin: Theme.space[3]
        spacing: Theme.space[4]

        Column {
            spacing: 5
            Layout.fillWidth: true
            Layout.minimumWidth: 260

            Text {
                width: parent.width
                text: root.strategyName
                color: root._isIdle ? Theme.color.text.secondary : Theme.color.text.primary
                font.family: Theme.typography.display.sm.family
                font.pixelSize: Theme.typography.display.sm.size
                font.weight: Theme.typography.display.sm.weight
                elide: Text.ElideRight
            }

            Text {
                width: parent.width
                text: root.strategyId
                color: Theme.color.text.muted
                font.family: Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features: Theme.typography.data.xs.features
                elide: Text.ElideRight
            }
        }

        Text {
            text: root.sharpe
            color: root._isIdle ? Theme.color.text.disabled : Theme.color.text.primary
            font.family: Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features: Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        Text {
            text: root.maxDD
            color: root._isIdle ? Theme.color.text.disabled : Theme.color.text.primary
            font.family: Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features: Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        Text {
            text: root.tradeCount
            color: root._isIdle ? Theme.color.text.disabled : Theme.color.text.primary
            font.family: Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features: Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        Text {
            text: root.metaConfigKey
            color: Theme.color.text.muted
            font.family: Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features: Theme.typography.data.xs.features
            elide: Text.ElideRight
            Layout.preferredWidth: Theme.column.benchConfigKey
        }

        Text {
            text: root.metaStage
            color: Theme.color.text.secondary
            font.family: Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features: Theme.typography.data.xs.features
            elide: Text.ElideRight
            Layout.preferredWidth: Theme.column.benchStage
        }

        Text {
            text: root.metaEvidence
            color: Theme.color.text.muted
            font.family: Theme.typography.data.xs.family
            font.pixelSize: Theme.typography.data.xs.size
            font.features: Theme.typography.data.xs.features
            elide: Text.ElideRight
            Layout.preferredWidth: Theme.column.benchEvidence
        }

        Item {
            Layout.preferredWidth: Theme.column.benchAction
            Layout.alignment: Qt.AlignVCenter
            implicitHeight: actionButton.implicitHeight

            QuietAction {
                id: actionButton
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "Action ->"
                onClicked: root.actionClicked(actionButton)
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.color.border.subtle
    }
}

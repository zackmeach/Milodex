// BenchSurface.qml - full-width vertical strategy ledger.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    property real captureContentHeight: scroller.contentHeight
    property var benchData: BenchState.sections

    property var activeStrategy: null
    property var activeAction: null
    property string activeModalKind: ""
    property real menuX: 0
    property real menuY: 0

    function sectionCount(section) {
        return section && section.strategies ? section.strategies.length : 0
    }

    function formatCount(section) {
        var count = sectionCount(section)
        return count < 10 ? "0" + count : "" + count
    }

    function formattedSharpe(row) {
        if (row.sharpe === undefined || row.sharpe === null) return "-"
        return ("+" + Number(row.sharpe).toFixed(2)).replace("+-", "-")
    }

    function formattedMaxDD(row) {
        if (row.maxDrawdownPct === undefined || row.maxDrawdownPct === null) return "-"
        return Number(row.maxDrawdownPct).toFixed(1) + "%"
    }

    function formattedTrades(row) {
        return row.tradeCount === 0 || row.tradeCount === undefined || row.tradeCount === null
               ? "-"
               : "" + row.tradeCount
    }

    function formattedEvidence(row) {
        return ((row.metaEvidenceLabel || "") + " " + (row.metaEvidenceAt || "")).trim()
    }

    function openActionMenu(strategy, anchor) {
        root.activeStrategy = strategy
        root.activeAction = null
        root.activeModalKind = "menu"
        var point = anchor.mapToItem(root, 0, anchor.height + Theme.space[1])
        root.menuX = Math.max(
            Theme.space[4],
            Math.min(root.width - actionMenu.width - Theme.space[4], point.x + anchor.width - actionMenu.width)
        )
        root.menuY = Math.max(Theme.space[4], Math.min(root.height - actionMenu.implicitHeight - Theme.space[4], point.y))
    }

    function closeMenu() {
        if (root.activeModalKind === "menu") {
            root.activeModalKind = ""
            root.activeAction = null
        }
    }

    function dismissModal() {
        root.activeStrategy = null
        root.activeAction = null
        root.activeModalKind = ""
    }

    function selectAction(action) {
        root.activeAction = action
        if (action.kind === "evidence") {
            root.activeModalKind = "evidence"
            return
        }
        root.activeModalKind = action.requiresConfirmation ? "confirm" : "prototype"
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.canvas
    }

    Flickable {
        id: scroller
        anchors.fill: parent
        contentWidth: width
        contentHeight: pageColumn.implicitHeight + Theme.space[7] * 2
        clip: true
        flickableDirection: Flickable.VerticalFlick

        Column {
            id: pageColumn
            width: scroller.width
            padding: Theme.space[7]
            spacing: Theme.space[7]

            Column {
                width: parent.width - Theme.space[7] * 2
                spacing: Theme.space[3]

                Text {
                    text: "Milodex · Strategy Bench"
                    color: Theme.color.text.muted
                    font.family: Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size
                    font.weight: Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                Row {
                    spacing: 0
                    Text {
                        text: "The Strategy Bench"
                        color: Theme.color.text.primary
                        font.family: Theme.typography.display.lg.family
                        font.pixelSize: Theme.typography.display.lg.size
                        font.weight: Theme.typography.display.lg.weight
                    }
                    Text {
                        text: "."
                        color: Theme.color.brand.accent
                        font.family: Theme.typography.display.lg.family
                        font.pixelSize: Theme.typography.display.lg.size
                        font.weight: Theme.typography.display.lg.weight
                    }
                }

                Text {
                    width: parent.width
                    text: "Every config on the ladder, top to bottom: what is working, what is stuck, and what is waiting at the next gate. Drag a row to move it; the system enforces the gates."
                    color: Theme.color.text.secondary
                    font.family: Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.italic: true
                    wrapMode: Text.WordWrap
                }

                Rectangle {
                    width: parent.width
                    height: 1
                    color: Theme.color.border.regular
                }
            }

            Repeater {
                model: root.benchData

                delegate: Item {
                    id: sectionRoot
                    width: pageColumn.width - Theme.space[7] * 2
                    height: sectionColumn.implicitHeight

                    property var sectionData: modelData
                    property var rowOrder: []

                    function syncRows() {
                        rowOrder = sectionData && sectionData.strategies
                                   ? sectionData.strategies.slice()
                                   : []
                    }

                    function moveRow(fromIndex, localY) {
                        if (fromIndex < 0 || fromIndex >= rowOrder.length) return
                        var target = Math.max(0, Math.min(rowOrder.length - 1, Math.floor(localY / 78)))
                        if (target === fromIndex) return
                        var rows = rowOrder.slice()
                        var moved = rows.splice(fromIndex, 1)[0]
                        rows.splice(target, 0, moved)
                        rowOrder = rows
                    }

                    Component.onCompleted: syncRows()
                    onSectionDataChanged: syncRows()

                    Column {
                        id: sectionColumn
                        width: parent.width
                        spacing: 0

                        Rectangle {
                            width: parent.width
                            height: sectionData.stage === "live" ? 2 : 1
                            color: sectionData.stage === "live"
                                   ? Theme.color.brand.accent
                                   : Theme.color.border.regular
                        }

                        Item {
                            width: parent.width
                            height: 54

                            Row {
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Theme.space[3]

                                Text {
                                    text: sectionData.stageRoman
                                    color: Theme.color.text.secondary
                                    font.family: Theme.typography.deck.family
                                    font.pixelSize: Theme.typography.deck.size
                                    font.italic: true
                                }

                                Text {
                                    text: sectionData.stageName + " · " + root.formatCount(sectionData)
                                    color: Theme.color.text.primary
                                    font.family: Theme.typography.label.xs.family
                                    font.pixelSize: Theme.typography.label.xs.size
                                    font.weight: Theme.typography.label.xs.weight
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                    font.capitalization: Font.AllUppercase
                                }
                            }

                            Text {
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                text: sectionData.stageCaption
                                color: Theme.color.text.secondary
                                font.family: Theme.typography.deck.family
                                font.pixelSize: Theme.typography.deck.size
                                font.italic: true
                                elide: Text.ElideRight
                            }
                        }

                        Rectangle {
                            width: parent.width
                            height: 1
                            color: Theme.color.border.subtle
                        }

                        Item {
                            visible: rowOrder.length === 0
                            width: parent.width
                            height: 64

                            Text {
                                anchors.centerIn: parent
                                text: "no strategies in this stage"
                                color: Theme.color.text.muted
                                font.family: Theme.typography.deck.family
                                font.pixelSize: Theme.typography.deck.size
                                font.italic: true
                            }
                        }

                        Item {
                            visible: rowOrder.length > 0
                            width: parent.width
                            height: 34

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: Theme.space[5]
                                anchors.rightMargin: Theme.space[3]
                                spacing: Theme.space[4]

                                Item { Layout.fillWidth: true; Layout.minimumWidth: 260 }
                                HeaderCell { text: "Sharpe"; alignRight: true; Layout.preferredWidth: Theme.column.benchMetric }
                                HeaderCell { text: "Max-DD"; alignRight: true; Layout.preferredWidth: Theme.column.benchMetric }
                                HeaderCell { text: "Trades"; alignRight: true; Layout.preferredWidth: Theme.column.benchMetric }
                                HeaderCell { text: "Config"; Layout.preferredWidth: Theme.column.benchConfigKey }
                                HeaderCell { text: "Stage"; Layout.preferredWidth: Theme.column.benchStage }
                                HeaderCell { text: "Evidence"; Layout.preferredWidth: Theme.column.benchEvidence }
                                HeaderCell { text: "Action"; alignRight: true; Layout.preferredWidth: Theme.column.benchAction }
                            }
                        }

                        Repeater {
                            model: sectionRoot.rowOrder

                            delegate: BenchRow {
                                width: sectionRoot.width
                                strategyName: modelData.name || modelData.strategyName
                                strategyId: modelData.strategyId
                                stage: sectionData.stage
                                sharpe: root.formattedSharpe(modelData)
                                maxDD: root.formattedMaxDD(modelData)
                                tradeCount: root.formattedTrades(modelData)
                                metaConfigKey: modelData.metaConfigKey || ""
                                metaStage: modelData.metaStage || sectionData.stage
                                metaEvidence: root.formattedEvidence(modelData)
                                onMoveRequested: (localY) => sectionRoot.moveRow(index, localY)
                                onActionClicked: (anchorItem) => root.openActionMenu(modelData, anchorItem)
                            }
                        }
                    }
                }
            }

            Rectangle {
                width: parent.width - Theme.space[7] * 2
                height: 1
                color: Theme.color.border.regular
            }

            Text {
                width: (parent.width - Theme.space[7] * 2) * 0.78
                text: "Promotion to paper requires walk-forward gate-pass on every window. Promotion to micro-live requires Sharpe >= 0.50, max-dd <= 15%, n >= 30. Promotion to live requires explicit human review and a recorded decision."
                color: Theme.color.text.muted
                font.family: Theme.typography.deck.family
                font.pixelSize: Theme.typography.deck.size
                font.italic: true
                wrapMode: Text.WordWrap
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        visible: root.activeModalKind === "menu"
        z: 80
        onClicked: root.closeMenu()
    }

    Rectangle {
        id: actionMenu
        visible: root.activeModalKind === "menu"
        x: root.menuX
        y: root.menuY
        z: 90
        width: 240
        implicitHeight: menuColumn.implicitHeight + Theme.space[2] * 2
        color: Theme.color.surface.base
        border.color: Theme.color.border.regular
        border.width: 1
        radius: Theme.radius.lg

        Column {
            id: menuColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.topMargin: Theme.space[2]
            anchors.bottomMargin: Theme.space[2]
            spacing: 0

            Repeater {
                model: root.activeStrategy ? (root.activeStrategy.actions || []) : []

                delegate: Item {
                    width: menuColumn.width
                    height: 36

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.space[3]
                        anchors.right: parent.right
                        anchors.rightMargin: Theme.space[3]
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.label
                        color: menuMouse.containsMouse ? Theme.color.text.primary : Theme.color.text.secondary
                        font.family: Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        elide: Text.ElideRight
                    }

                    MouseArea {
                        id: menuMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.selectAction(modelData)
                    }
                }
            }
        }
    }

    Loader {
        anchors.fill: parent
        active: root.activeModalKind === "evidence"
        sourceComponent: evidenceComponent
        z: 100
    }

    Component {
        id: evidenceComponent
        BenchModal {
            anchors.fill: parent
            topBorderColor: root.activeStrategy && root.activeStrategy.statusKind === "warning"
                            ? Theme.status.warning
                            : Theme.status.info
            eyebrowText: "Evidence"
            titleText: root.activeStrategy ? (root.activeStrategy.name || root.activeStrategy.strategyId) : ""
            proseText: root.activeStrategy
                       ? ((root.activeStrategy.statusWord || "State recorded") + " "
                          + (root.activeStrategy.statusTail || "No write actions are exposed in this prototype."))
                       : ""
            onDismissed: root.dismissModal()

            Text {
                width: parent.width
                text: root.activeStrategy
                      ? ("Stage " + root.activeStrategy.stage
                         + " | Sharpe " + root.formattedSharpe(root.activeStrategy)
                         + " | Max-dd " + root.formattedMaxDD(root.activeStrategy)
                         + " | Trades " + root.formattedTrades(root.activeStrategy))
                      : ""
                color: Theme.color.text.secondary
                font.family: Theme.typography.data.sm.family
                font.pixelSize: Theme.typography.data.sm.size
                font.features: Theme.typography.data.sm.features
                wrapMode: Text.WordWrap
            }

            Text {
                width: parent.width
                text: root.activeStrategy ? ("Config: " + root.activeStrategy.configPath) : ""
                color: Theme.color.text.muted
                font.family: Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features: Theme.typography.data.xs.features
                wrapMode: Text.WrapAnywhere
            }

            actionContent: [
                Button {
                    variant: "ghost"
                    text: "Close"
                    onClicked: root.dismissModal()
                }
            ]
        }
    }

    Loader {
        anchors.fill: parent
        active: root.activeModalKind === "prototype" || root.activeModalKind === "confirm"
        sourceComponent: prototypeComponent
        z: 100
    }

    Component {
        id: prototypeComponent
        BenchModal {
            anchors.fill: parent
            topBorderColor: root.activeModalKind === "confirm"
                            ? Theme.color.brand.accent
                            : Theme.status.info
            eyebrowText: root.activeModalKind === "confirm" ? "Confirmation prototype" : "Prototype only"
            titleText: root.activeAction ? root.activeAction.label : ""
            proseText: root.activeAction
                       ? (root.activeAction.label
                          + " is available for this strategy, but this visual prototype does not mutate strategy stage, write to the ledger, start trading, stop trading, or run backtests.")
                       : ""
            onDismissed: root.dismissModal()

            Text {
                visible: root.activeModalKind === "confirm"
                width: parent.width
                text: "Moving into Micro Live or Live remains consequential. The write-capable phase will require governed confirmation before any state changes."
                color: Theme.color.text.secondary
                font.family: Theme.typography.body.md.family
                font.pixelSize: Theme.typography.body.md.size
                wrapMode: Text.WordWrap
            }

            actionContent: [
                Button {
                    variant: "ghost"
                    text: "Cancel"
                    onClicked: root.dismissModal()
                },
                Button {
                    variant: root.activeModalKind === "confirm" ? "secondary" : "ghost"
                    text: "Prototype only - no change"
                    onClicked: root.dismissModal()
                }
            ]
        }
    }

    component HeaderCell: Text {
        property bool alignRight: false
        color: Theme.color.text.secondary
        font.family: Theme.typography.label.xs.family
        font.pixelSize: Theme.typography.label.xs.size
        font.weight: Font.DemiBold
        font.letterSpacing: Theme.typography.label.xs.letterSpacing
        font.capitalization: Font.AllUppercase
        horizontalAlignment: alignRight ? Text.AlignRight : Text.AlignLeft
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}

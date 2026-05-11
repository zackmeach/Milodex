// KanbanSurface.qml - Phase 6 read-only operator Kanban.
//
// Foundation-only surface: renders the promotion-ladder read model without
// drag/drop, bulk dispatch, runner controls, promotion, demotion, or live
// authorization. Capital-bearing stages are visible as locked future stages.

import QtQuick
import Milodex 1.0

Item {
    id: root

    property real captureContentHeight: pageColumn.implicitHeight + Theme.space[7] * 2
    readonly property var lanes: KanbanState.lanes
    readonly property var summary: KanbanState.summary

    function stageColor(stage) {
        if (stage === "idle") return Theme.stage.idle
        if (stage === "backtest") return Theme.stage.backtest
        if (stage === "paper") return Theme.stage.paper
        if (stage === "micro_live") return Theme.stage.microLive
        if (stage === "live") return Theme.stage.live
        return Theme.color.border.emphasis
    }

    function stageLabel(stage) {
        if (stage === "micro_live") return "micro live"
        return String(stage || "").replace("_", " ")
    }

    function fmtNumber(value, digits, suffix, signed) {
        if (value === undefined || value === null) return "-"
        var n = Number(value)
        if (isNaN(n)) return "-"
        var out = n.toFixed(digits)
        if (signed && n > 0) out = "+" + out
        return out + (suffix || "")
    }

    component KanbanMetric: Item {
        property string label: ""
        property string value: "-"

        width: Theme.column.kanbanMetric
        height: metricCol.implicitHeight

        Column {
            id: metricCol
            width: parent.width
            spacing: Theme.space[1]

            Text {
                width: parent.width
                text: label
                color: Theme.color.text.muted
                horizontalAlignment: Text.AlignRight
                font.family: Theme.typography.label.xs.family
                font.pixelSize: Theme.typography.label.xs.size
                font.weight: Theme.typography.label.xs.weight
                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                font.capitalization: Font.AllUppercase
            }

            Text {
                width: parent.width
                text: value
                color: Theme.color.text.primary
                horizontalAlignment: Text.AlignRight
                font.family: Theme.typography.data.sm.family
                font.pixelSize: Theme.typography.data.sm.size
                font.features: Theme.typography.data.sm.features
            }
        }
    }

    component KanbanCard: Item {
        id: cardRoot

        property var cardData: ({})
        property color accent: Theme.color.border.emphasis

        width: Theme.column.kanbanCard
        height: Math.max(Theme.column.kanbanCardMinHeight, cardColumn.implicitHeight + Theme.space[4] * 2)

        Rectangle {
            anchors.fill: parent
            color: Theme.color.surface.base
            border.color: Theme.color.border.subtle
            border.width: 1
            radius: Theme.radius.lg
        }

        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 3
            color: cardRoot.accent
            radius: Theme.radius.sm
        }

        Column {
            id: cardColumn
            anchors {
                left: parent.left
                right: parent.right
                top: parent.top
                margins: Theme.space[4]
                leftMargin: Theme.space[4] + Theme.space[2]
            }
            spacing: Theme.space[3]

            Row {
                width: parent.width
                spacing: Theme.space[2]

                Column {
                    width: parent.width - stageChip.width - Theme.space[2]
                    spacing: Theme.space[1]

                    Text {
                        width: parent.width
                        text: cardRoot.cardData.displayName || cardRoot.cardData.name || cardRoot.cardData.strategyId
                        color: Theme.color.text.primary
                        elide: Text.ElideRight
                        font.family: Theme.typography.display.sm.family
                        font.pixelSize: Theme.typography.display.sm.size
                        font.weight: Theme.typography.display.sm.weight
                    }

                    Text {
                        width: parent.width
                        text: cardRoot.cardData.strategyId || ""
                        color: Theme.color.text.secondary
                        elide: Text.ElideMiddle
                        font.family: Theme.typography.data.xs.family
                        font.pixelSize: Theme.typography.data.xs.size
                        font.features: Theme.typography.data.xs.features
                    }
                }

                Rectangle {
                    id: stageChip
                    width: Math.max(stageText.implicitWidth + Theme.space[3], 74)
                    height: stageText.implicitHeight + Theme.space[1] * 2
                    color: "transparent"
                    border.color: cardRoot.accent
                    border.width: 1
                    radius: Theme.radius.sm

                    Text {
                        id: stageText
                        anchors.centerIn: parent
                        text: root.stageLabel(cardRoot.cardData.promotionStage || cardRoot.cardData.stage)
                        color: cardRoot.accent
                        font.family: Theme.typography.label.xs.family
                        font.pixelSize: Theme.typography.label.xs.size
                        font.weight: Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }
                }
            }

            Text {
                width: parent.width
                text: cardRoot.cardData.eligibilityCopy || cardRoot.cardData.statusTail || "Read-only evidence card."
                color: Theme.color.text.secondary
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
                font.family: Theme.typography.deck.family
                font.pixelSize: Theme.typography.deck.size
                font.weight: Theme.typography.deck.weight
                font.italic: Theme.typography.deck.italic
            }

            Row {
                width: parent.width
                spacing: Theme.space[2]

                KanbanMetric {
                    label: "Sharpe"
                    value: root.fmtNumber(cardRoot.cardData.sharpe, 2, "", true)
                }
                KanbanMetric {
                    label: "max-dd"
                    value: root.fmtNumber(cardRoot.cardData.maxDrawdownPct, 1, "%", false)
                }
                KanbanMetric {
                    label: "trades"
                    value: cardRoot.cardData.tradeCount ? String(cardRoot.cardData.tradeCount) : "-"
                }
            }

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.color.border.subtle
            }

            Text {
                width: parent.width
                text: (cardRoot.cardData.sessionState || "not_running")
                      + (cardRoot.cardData.sessionDetail ? " | " + cardRoot.cardData.sessionDetail : "")
                color: Theme.color.text.muted
                elide: Text.ElideRight
                font.family: Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features: Theme.typography.data.xs.features
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.canvas
    }

    Flickable {
        id: verticalScroller
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: pageColumn.implicitHeight + Theme.space[7] * 2
        flickableDirection: Flickable.VerticalFlick

        Column {
            id: pageColumn
            width: verticalScroller.width
            padding: Theme.space[7]
            spacing: Theme.space[6]

            Column {
                width: parent.width - Theme.space[7] * 2
                spacing: Theme.space[3]

                Text {
                    text: "Milodex | Operator Kanban"
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
                        text: "THE BENCH"
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
                    text: "Read-only promotion-ladder foundation. Drag, bulk dispatch, runner controls, micro-live, and live remain locked."
                    color: Theme.color.text.secondary
                    wrapMode: Text.WordWrap
                    font.family: Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.weight: Theme.typography.deck.weight
                    font.italic: Theme.typography.deck.italic
                }

                Text {
                    text: "read-only | " + (summary.totalConfigs || 0) + " configs | capital stages locked by ADR 0004"
                    color: Theme.color.text.muted
                    font.family: Theme.typography.data.xs.family
                    font.pixelSize: Theme.typography.data.xs.size
                    font.features: Theme.typography.data.xs.features
                }
            }

            Flickable {
                id: boardScroller
                width: parent.width - Theme.space[7] * 2
                height: Math.max(360, verticalScroller.height - Theme.space[7] * 4 - 120)
                contentWidth: boardRow.implicitWidth
                contentHeight: height
                clip: true
                flickableDirection: Flickable.HorizontalFlick

                Row {
                    id: boardRow
                    height: parent.height
                    spacing: Theme.space[4]

                    Repeater {
                        model: root.lanes
                        delegate: Item {
                            id: laneRoot
                            property var laneData: modelData

                            width: Theme.column.kanbanLane
                            height: boardScroller.height

                            Rectangle {
                                anchors.fill: parent
                                color: Theme.color.surface.raised
                                border.color: Theme.color.border.subtle
                                border.width: 1
                                radius: Theme.radius.lg
                            }

                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                height: 3
                                color: root.stageColor(laneRoot.laneData.lane)
                            }

                            Column {
                                anchors {
                                    fill: parent
                                    margins: Theme.space[4]
                                }
                                spacing: Theme.space[4]

                                Column {
                                    width: parent.width
                                    spacing: Theme.space[1]

                                    Text {
                                        text: (laneRoot.laneData.laneRoman || "") + " " + (laneRoot.laneData.laneName || "")
                                        color: Theme.color.text.primary
                                        font.family: Theme.typography.label.xs.family
                                        font.pixelSize: Theme.typography.label.xs.size
                                        font.weight: Theme.typography.label.xs.weight
                                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                        font.capitalization: Font.AllUppercase
                                    }

                                    Text {
                                        width: parent.width
                                        text: laneRoot.laneData.laneCaption || ""
                                        color: Theme.color.text.muted
                                        wrapMode: Text.WordWrap
                                        font.family: Theme.typography.deck.family
                                        font.pixelSize: Theme.typography.deck.size
                                        font.weight: Theme.typography.deck.weight
                                        font.italic: Theme.typography.deck.italic
                                    }
                                }

                                Rectangle {
                                    width: parent.width
                                    height: 1
                                    color: Theme.color.border.subtle
                                }

                                Flickable {
                                    width: parent.width
                                    height: parent.height - y
                                    clip: true
                                    contentWidth: width
                                    contentHeight: cardColumn.implicitHeight
                                    flickableDirection: Flickable.VerticalFlick

                                    Column {
                                        id: cardColumn
                                        width: parent.width
                                        spacing: Theme.space[3]

                                        Repeater {
                                            model: laneRoot.laneData.cards || []
                                            delegate: KanbanCard {
                                                cardData: modelData
                                                accent: root.stageColor(laneRoot.laneData.lane)
                                            }
                                        }

                                        Text {
                                            visible: !laneRoot.laneData.cards || laneRoot.laneData.cards.length === 0
                                            width: parent.width
                                            text: "no strategies in this lane"
                                            color: Theme.color.text.muted
                                            horizontalAlignment: Text.AlignHCenter
                                            font.family: Theme.typography.body.md.family
                                            font.pixelSize: Theme.typography.body.md.size
                                            font.italic: true
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

// LedgerSurface.qml — The Ledger (paper of record).
//
// Chronological log of every promotion, every refusal, every kill-switch
// fire.  The system's behavior, on paper.  Aesthetic: reads like a
// printout — mono everywhere, columns aligned, outcomes in bright color.
//
// This is the surface that makes Milodex's USP (honesty about what
// works) visible: every refusal is recorded here permanently.  That's
// why the footer says "every refusal is a permanent test."
//
// PR1: mock entries.  Real wiring (durable event store query) follows.
//
// Tokens consumed:
//   color.surface.canvas          — page background
//   color.brand.primary / .accent — title / oxblood period ornament
//   color.text.primary / .secondary / .muted / .disabled — entry typography
//   color.border.subtle / .regular — dividers
//   status.positive (sage)        — PROMOTED outcome, gate-pass language
//   status.negative (rust)        — REFUSED / FIRED outcome
//   status.warning                — filter chip on hover (future)
//   typography.display.lg         — title
//   typography.body.md            — italic deck + filter labels
//   typography.label.xs           — eyebrows, filter-pill labels
//   typography.data.sm / .md      — entry body (mono, tabular)

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    property real captureContentHeight: scroller.contentHeight

    // ------------------------------------------------------------------
    // Mock ledger entries — each entry covers one event.
    //
    // Entry shape:
    //   ts            : string  — timestamp, "YYYY-MM-DD HH:MM ET"
    //   subject       : string  — strategy name OR "kill switch"
    //   transition    : string  — "backtest → paper" / "paper" / "session"
    //   outcome       : string  — "PROMOTED" / "REFUSED" / "FIRED"
    //   outcomeKind   : string  — "promoted" | "refused" | "fired"
    //   reason        : string  — second-line explanation (mono)
    // ------------------------------------------------------------------

    readonly property var entries: LedgerState.entries
    /* Legacy PR1 mock entries retained for reference only.
    [
        { ts: "2026-05-08 09:14 ET", subject: "RSI-2 Pullback",      transition: "micro-live → live", outcome: "PENDING", outcomeKind: "pending",
          reason: "live-eligibility window open — awaiting typed confirmation" },
        { ts: "2026-05-06 14:22 ET", subject: "Time-Series Momentum", transition: "paper",            outcome: "GATES PASS", outcomeKind: "info",
          reason: "Sharpe 0.88 · max-dd 8.9% · n=458 · micro-eligible" },
        { ts: "2026-05-03 11:40 ET", subject: "NR7 Inside-Day Brk.",  transition: "paper",            outcome: "FLAGGED", outcomeKind: "refused",
          reason: "Sharpe 0.31 below 0.50 floor — candidate for reparam" },
        { ts: "2026-04-28 10:08 ET", subject: "BBands Lower-Band MR", transition: "backtest → paper", outcome: "PROMOTED", outcomeKind: "promoted",
          reason: "gate passed · Sharpe 0.52 · max-dd 13.7% · n=361" },
        { ts: "2026-04-24 16:55 ET", subject: "ATR Channel Breakout", transition: "backtest → paper", outcome: "PROMOTED", outcomeKind: "promoted",
          reason: "gate passed · Sharpe 0.64 · max-dd 11.2% · n=433" },
        { ts: "2026-04-12 14:33 ET", subject: "Donchian 20/10",        transition: "backtest → paper", outcome: "PROMOTED", outcomeKind: "promoted",
          reason: "gate passed · Sharpe 0.87 · max-dd 9.2% · n=435" },
        { ts: "2026-04-08 09:14 ET", subject: "Dual Absolute GEM",     transition: "paper",            outcome: "REFUSED", outcomeKind: "refused",
          reason: "gate failed · n=20 below 30-trade minimum" },
        { ts: "2026-03-29 11:02 ET", subject: "Turn-of-Month SPY",     transition: "backtest",         outcome: "REFUSED", outcomeKind: "refused",
          reason: "gate failed · Sharpe 0.41 below 0.50" },
        { ts: "2026-03-21 08:45 ET", subject: "kill switch",            transition: "session",          outcome: "FIRED",   outcomeKind: "fired",
          reason: "daily loss cap exceeded (−3.4%) — manual reset required" },
        { ts: "2026-03-14 13:18 ET", subject: "RSI-2 Pullback",         transition: "paper → micro",    outcome: "PROMOTED", outcomeKind: "promoted",
          reason: "gate passed · Sharpe 0.71 · max-dd 9.4% · n=702" },
        { ts: "2026-02-27 10:11 ET", subject: "52-Week High Proximity", transition: "backtest",         outcome: "REFUSED", outcomeKind: "refused",
          reason: "gate failed · max-dd 17.1% over 15.0% gate · Sharpe 0.41 also below 0.50" },
        { ts: "2026-01-14 09:30 ET", subject: "SPY/SHY Regime Rotation", transition: "→ live (lifecycle exempt)", outcome: "PROMOTED", outcomeKind: "promoted",
          reason: "regime strategy · exempt from standard gate per ADR 0021" }
    ]
    */

    // ------------------------------------------------------------------
    // Background
    // ------------------------------------------------------------------

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.canvas
    }

    // ------------------------------------------------------------------
    // Scroll container, centered with comfortable max-width
    // ------------------------------------------------------------------

    Flickable {
        id: scroller
        anchors.fill: parent
        contentWidth:  width
        contentHeight: pageColumn.implicitHeight + Theme.space[7] * 2
        clip:          true
        flickableDirection: Flickable.VerticalFlick

        Item {
            anchors.horizontalCenter: parent.horizontalCenter
            width:  Math.min(scroller.width - Theme.space[7] * 2, 1280)
            height: pageColumn.implicitHeight + Theme.space[7] * 2

            Column {
                id: pageColumn
                anchors.left:   parent.left
                anchors.right:  parent.right
                anchors.top:    parent.top
                anchors.topMargin: Theme.space[7]
                spacing: Theme.space[5]

                // ====================================================
                // HEADER — eyebrow + title + italic deck
                // ====================================================
                Text {
                    text: "Paper of Record"
                    color: Theme.color.text.muted
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                Row {
                    spacing: 0
                    Text {
                        text:  "The Ledger"
                        color: Theme.color.brand.primary
                        font.family:    Theme.typography.display.lg.family
                        font.pixelSize: Theme.typography.display.lg.size
                        font.weight:    Theme.typography.display.lg.weight
                        font.letterSpacing: -0.6
                    }
                    Text {
                        text:  "."
                        color: Theme.color.brand.accent
                        font.family:    Theme.typography.display.lg.family
                        font.pixelSize: Theme.typography.display.lg.size
                        font.weight:    Theme.typography.display.lg.weight
                    }
                }

                Text {
                    width: parent.width * 0.78
                    text: "A chronological record of every promotion, every refusal, every kill-switch fire. The system's behavior, on paper."
                    color: Theme.color.text.secondary
                    font.family:    Theme.typography.body.lgPlus.family
                    font.pixelSize: Theme.typography.body.lgPlus.size
                    font.italic:    true
                    wrapMode:       Text.WordWrap
                    lineHeight:     1.5
                }

                // Hairline rule
                Item { width: parent.width; height: Theme.space[3] }
                Rectangle {
                    width:  parent.width
                    height: 1
                    color:  Theme.color.border.regular
                }

                // ====================================================
                // FILTER ROW — visual stub for PR1
                // ====================================================
                Row {
                    spacing: Theme.space[2]

                    Text {
                        text: "FILTER —"
                        color: Theme.color.text.muted
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        anchors.verticalCenter: parent.verticalCenter
                    }

                    Repeater {
                        model: [
                            { label: "all stages", stage: "all", strategy: LedgerState.strategyFilter, outcome: LedgerState.outcomeFilter, time: LedgerState.timeFilter },
                            { label: "paper stage", stage: "paper", strategy: LedgerState.strategyFilter, outcome: LedgerState.outcomeFilter, time: LedgerState.timeFilter },
                            { label: "promoted", stage: LedgerState.stageFilter, strategy: LedgerState.strategyFilter, outcome: "promoted", time: LedgerState.timeFilter },
                            { label: "system events", stage: "system", strategy: LedgerState.strategyFilter, outcome: LedgerState.outcomeFilter, time: LedgerState.timeFilter },
                            { label: "clear", stage: "all", strategy: "all", outcome: "all", time: "all" }
                        ]
                        delegate: Rectangle {
                            readonly property bool activeFilter: modelData.label !== "clear"
                                                              && modelData.stage === LedgerState.stageFilter
                                                              && modelData.strategy === LedgerState.strategyFilter
                                                              && modelData.outcome === LedgerState.outcomeFilter
                                                              && modelData.time === LedgerState.timeFilter
                            implicitWidth:  filterLabel.implicitWidth + Theme.space[3] * 2
                            implicitHeight: filterLabel.implicitHeight + Theme.space[2] * 1.5
                            color: activeFilter ? Theme.color.surface.raised
                                  : modelData.label === "clear" ? Theme.color.surface.base
                                  : "transparent"
                            border.color: activeFilter ? Theme.color.border.emphasis : Theme.color.border.regular
                            border.width: 1
                            radius: Theme.radius.sm

                            Text {
                                id: filterLabel
                                anchors.centerIn: parent
                                text:  modelData.label
                                color: parent.activeFilter ? Theme.color.text.primary : Theme.color.text.secondary
                                font.family:    Theme.typography.body.md.family
                                font.pixelSize: Theme.typography.body.sm.size
                                font.italic:    true
                            }

                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: LedgerState.setLedgerFilter(
                                    modelData.stage,
                                    modelData.strategy,
                                    modelData.outcome,
                                    modelData.time
                                )
                            }
                        }
                    }
                }

                Item { width: parent.width; height: Theme.space[2] }

                // ====================================================
                // ENTRIES — one per event
                // ====================================================
                Repeater {
                    model: root.entries
                    delegate: Item {
                        id: entryRoot
                        property bool expanded: false
                        readonly property bool longReason: (modelData.reason || "").length > 140
                        width:  pageColumn.width
                        height: entryColumn.implicitHeight + Theme.space[4] * 2

                        Rectangle {
                            anchors.bottom: parent.bottom
                            width:  parent.width
                            height: 1
                            color:  Theme.color.border.subtle
                        }

                        Column {
                            id: entryColumn
                            anchors.left:    parent.left
                            anchors.right:   parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: Theme.space[1]

                            // -- Top row: timestamp / subject / transition / outcome --
                            RowLayout {
                                width: parent.width
                                spacing: Theme.space[3]

                                // Timestamp
                                Text {
                                    text: modelData.displayTimestamp || modelData.timestamp
                                    color: Theme.color.text.secondary
                                    font.family:    Theme.typography.data.sm.family
                                    font.pixelSize: Theme.typography.data.sm.size
                                    font.features:  Theme.typography.data.sm.features
                                    Layout.preferredWidth: Theme.column.ledgerTimestamp
                                }
                                // Subject (strategy name or "kill switch")
                                Text {
                                    text: modelData.subject
                                    color: Theme.color.text.primary
                                    font.family:    Theme.typography.data.sm.family
                                    font.pixelSize: Theme.typography.data.sm.size
                                    font.features:  Theme.typography.data.sm.features
                                    font.weight:    Font.Medium
                                    Layout.preferredWidth: Theme.column.ledgerSubject
                                    elide: Text.ElideRight
                                }
                                // Transition arrow
                                Text {
                                    text: modelData.transition
                                    color: Theme.color.text.secondary
                                    font.family:    Theme.typography.data.sm.family
                                    font.pixelSize: Theme.typography.data.sm.size
                                    font.features:  Theme.typography.data.sm.features
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: Theme.column.ledgerTransition
                                }
                                // Outcome — colored language, right-aligned
                                Text {
                                    text: modelData.outcome
                                    horizontalAlignment: Text.AlignRight
                                    color: {
                                        if (modelData.outcomeKind === "promoted") return Theme.status.positive
                                        if (modelData.outcomeKind === "refused")  return Theme.status.negative
                                        if (modelData.outcomeKind === "fired")    return Theme.status.negative
                                        if (modelData.outcomeKind === "demoted")  return Theme.status.negative
                                        if (modelData.outcomeKind === "info")     return Theme.status.info
                                        if (modelData.outcomeKind === "pending")  return Theme.status.warning
                                        return Theme.color.text.muted
                                    }
                                    font.family:        Theme.typography.label.xs.family
                                    font.pixelSize:     Theme.typography.label.xs.size
                                    font.weight:        Font.DemiBold
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.2
                                    Layout.preferredWidth: Theme.column.ledgerOutcome
                                }
                            }

                            // -- Reason line (indented under subject column) --
                            Item {
                                width: parent.width
                                height: reasonText.implicitHeight
                                        + (openRecordAction.visible ? openRecordAction.implicitHeight + Theme.space[2] : 0)
                                        + Theme.space[1]

                                Text {
                                    id: reasonText
                                    anchors.left: parent.left
                                    anchors.leftMargin: Theme.column.ledgerTimestamp + Theme.space[3]
                                    anchors.top: parent.top
                                    anchors.topMargin: Theme.space[1]
                                    width: parent.width - Theme.column.ledgerTimestamp - Theme.column.ledgerOutcome - Theme.space[3] * 2
                                    text:  modelData.reason
                                    color: Theme.color.text.secondary
                                    font.family:    Theme.typography.data.sm.family
                                    font.pixelSize: Theme.typography.data.sm.size
                                    font.features:  Theme.typography.data.sm.features
                                    wrapMode:       Text.WordWrap
                                    maximumLineCount: entryRoot.expanded ? 100 : 2
                                    elide: entryRoot.expanded ? Text.ElideNone : Text.ElideRight
                                }

                                QuietAction {
                                    id: openRecordAction
                                    visible: entryRoot.longReason
                                    text: entryRoot.expanded ? "Close record" : "Open record"
                                    anchors.left: reasonText.left
                                    anchors.top: reasonText.bottom
                                    anchors.topMargin: Theme.space[2]
                                    onClicked: entryRoot.expanded = !entryRoot.expanded
                                }
                            }
                        }
                    }
                }

                // ====================================================
                // FOOTER — italic close-out
                // ====================================================
                Item { width: parent.width; height: Theme.space[3] }

                Rectangle {
                    width:  parent.width
                    height: 1
                    color:  Theme.color.border.subtle
                }

                Text {
                    width:  parent.width
                    text: "Filterable by stage, strategy, outcome, date range. Every refusal is a permanent test."
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.sm.size
                    font.italic:    true
                    wrapMode: Text.WordWrap
                }

                Item { width: parent.width; height: Theme.space[7] }
            }
        }
    }
}

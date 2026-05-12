// BenchSurface.qml — full-width vertical strategy ledger.
//
// Visual model (bench-brief §2): five promotion stages rendered as vertical
// sections stacked top-to-bottom. Section height is determined by strategy
// count — the funnel shape (many strategies early, few at live) is the design.
//
// Section anatomy (bench-brief §3):
//   roman numeral · STAGE NAME · count     [right-aligned caption]
//   ─────────────────────────────────────  (hairline; 2px brand.accent for LIVE)
//   [column header row]
//   [BenchRow …]
//
// Row anatomy (bench-brief §4) handled by BenchRow.qml:
//   drag handle | strategy block | Sharpe | Max-DD | Trades | status prose | Action
//
// PR F scope: visual reconciliation only.
//   - Within-section drag handle rendered but not wired; PR H wires drag.
//   - Evidence modal and confirmation modals deferred to PR I.
//   - No backend mutation (ADR 0049 Decision 2).
//   - No legacy "View Evidence" primary button rail.
//   - No kanban column markup.
//
// PR G: Action menu wired.
//   - BenchRow.actionItems populated from modelData.actions (compute_menu_items
//     output via read_models._compute_bench_action_menu).
//   - BenchRow.actionVariant derived from actual item verbClasses, not statusKind.
//   - Clicking a menu item is a visual-prototype no-op (ADR 0049 Decision 2).
//   - PAPER section uses "secondary" (outlined) variant to avoid visual over-emphasis.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // Sections from the BenchState read model.
    // Each section: { stage, stageRoman, stageName, stageCaption, strategies[] }
    // Each strategy: as_qml() output from _StrategyRow (read_models.py)
    property var benchData: BenchState.sections

    // -----------------------------------------------------------------------
    // Formatting helpers
    // -----------------------------------------------------------------------

    function formattedSharpe(row) {
        if (row.sharpe === undefined || row.sharpe === null) return "—"
        return ("+" + Number(row.sharpe).toFixed(2)).replace("+-", "-")
    }

    function formattedMaxDD(row) {
        if (row.maxDrawdownPct === undefined || row.maxDrawdownPct === null) return "—"
        return Number(row.maxDrawdownPct).toFixed(1) + "%"
    }

    function formattedTrades(row) {
        if (!row.tradeCount || row.tradeCount === 0) return "—"
        return "" + row.tradeCount
    }

    // Resolve status word color from statusKind string
    function statusWordColor(kind) {
        if (kind === "positive") return Theme.status.positive
        if (kind === "warning")  return Theme.status.warning
        if (kind === "negative") return Theme.status.negative
        return Theme.status.info
    }

    // Determine the Action button variant for a row (bench-brief §6, §7).
    // Derived from the verbClass of items in row.actions (compute_menu_items()
    // output from bench_v1.py).
    //
    // Variant logic (post-PR G polish):
    //   secondary — default for all rows with at least one state-changing verb
    //               (directional or invocation), including promote-eligible rows.
    //               Renders as outlined border.regular + text.primary, which
    //               keeps the right-side Action column visible but not a
    //               dominant repeated rail.
    //   ghost     — Open-Evidence-only rows (informational floor only).
    //
    // The filled-oxblood treatment (variant: "primary") is no longer assigned
    // here.  BenchRow promotes the button to "primary" transiently on hover or
    // while its action menu is open — see BenchRow.qml.
    function actionVariant(row) {
        var actions = row.actions || []
        var hasStateChanging = false
        for (var i = 0; i < actions.length; ++i) {
            if (actions[i].verbClass === "directional" || actions[i].verbClass === "invocation") {
                hasStateChanging = true
                break
            }
        }
        if (hasStateChanging) return "secondary"
        return "ghost"
    }

    // Format the meta line for the status column.
    // MICRO LIVE rows gain capital-deployed detail per brief §5.
    function metaLine(row) {
        var parts = []
        if (row.metaLine && row.metaLine.length > 0) return row.metaLine
        if (row.stage === "micro_live" || row.stage === "live") {
            parts.push("session: " + (row.sessionState || "not_running"))
        }
        return parts.join(" · ")
    }

    // -----------------------------------------------------------------------
    // Surface background
    // -----------------------------------------------------------------------

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.canvas
    }

    // -----------------------------------------------------------------------
    // Scrollable ledger
    // -----------------------------------------------------------------------

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
            topPadding: Theme.space[7]
            bottomPadding: Theme.space[7]
            leftPadding: Theme.space[7]
            rightPadding: Theme.space[7]
            spacing: Theme.space[7]

            // ---- Page header -----------------------------------------------
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

                // "THE BENCH." headline — Newsreader display with oxblood period
                Row {
                    spacing: 0
                    Text {
                        text: "The Bench"
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

                // Standfirst — italic Newsreader deck
                Text {
                    width: parent.width
                    text: "Every strategy on the ladder, top to bottom — what is working, what is blocked, and what is waiting at the next gate."
                    color: Theme.color.text.secondary
                    font.family: Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.italic: true
                    wrapMode: Text.WordWrap
                }

                // Hairline below header
                Rectangle {
                    width: parent.width
                    height: 1
                    color: Theme.color.border.regular
                }
            }

            // ---- Stage sections --------------------------------------------
            // Repeater over sections from BenchState.sections
            Repeater {
                model: root.benchData

                delegate: Item {
                    id: sectionRoot
                    width: pageColumn.width - Theme.space[7] * 2
                    height: sectionCol.implicitHeight

                    property var sectionData: modelData

                    // Session-only row order (within-section reorder lives
                    // here; PR H will wire drag; non-persisting per ADR 0049).
                    property var rowOrder: []

                    function syncRows() {
                        rowOrder = (sectionData && sectionData.strategies)
                                   ? sectionData.strategies.slice()
                                   : []
                    }

                    Component.onCompleted: syncRows()
                    onSectionDataChanged: syncRows()

                    Column {
                        id: sectionCol
                        width: parent.width
                        spacing: 0

                        // ---- Section rule (2px oxblood for LIVE, 1px for others) ----
                        Rectangle {
                            width: parent.width
                            height: sectionData.stage === "live" ? 2 : 1
                            color: sectionData.stage === "live"
                                   ? Theme.color.brand.accent
                                   : Theme.color.border.regular
                        }

                        // ---- Section header (bench-brief §3) -------------------
                        Item {
                            width: parent.width
                            height: 54

                            // Left cluster: roman numeral · STAGE NAME · count
                            Row {
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Theme.space[2]

                                // Roman numeral — Newsreader italic, text.muted
                                Text {
                                    text: sectionData.stageRoman
                                    color: Theme.color.text.muted
                                    font.family: Theme.typography.deck.family
                                    font.pixelSize: Theme.typography.display.sm.size  // 18px; nearest token, 1px under original
                                    font.italic: true
                                }

                                // Stage name — Public Sans, letter-spaced uppercase, weight 600
                                Text {
                                    text: sectionData.stageName.toUpperCase()
                                    color: Theme.color.text.primary
                                    font.family: Theme.typography.label.xs.family
                                    font.pixelSize: Theme.typography.label.xs.size
                                    font.weight: Font.DemiBold
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                }

                                // Mid-dot separator
                                Text {
                                    text: "·"
                                    color: Theme.color.text.muted
                                    font.family: Theme.typography.label.xs.family
                                    font.pixelSize: Theme.typography.label.xs.size
                                }

                                // Strategy count — JetBrains Mono, 2-digit zero-padded
                                Text {
                                    text: {
                                        var n = sectionRoot.rowOrder.length
                                        return n < 10 ? "0" + n : "" + n
                                    }
                                    color: Theme.color.text.muted
                                    font.family: Theme.typography.data.sm.family
                                    font.pixelSize: Theme.typography.data.sm.size
                                    font.features: Theme.typography.data.sm.features
                                }
                            }

                            // Right: section caption — Newsreader italic, text.secondary
                            Text {
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                text: sectionData.stageCaption
                                color: Theme.color.text.secondary
                                font.family: Theme.typography.deck.family
                                font.pixelSize: Theme.typography.deck.size
                                font.italic: true
                                elide: Text.ElideRight
                                maximumLineCount: 1
                            }
                        }

                        // Hairline below section header (border.subtle per brief §3)
                        Rectangle {
                            width: parent.width
                            height: 1
                            color: Theme.color.border.subtle
                        }

                        // ---- Empty-state (bench-brief §3 empty-state treatment) -----
                        Item {
                            visible: sectionRoot.rowOrder.length === 0
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

                        // ---- Column header row (bench-brief §4) ----------------
                        // Visible only when section has rows.
                        Item {
                            visible: sectionRoot.rowOrder.length > 0
                            width: parent.width
                            height: 32

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 0
                                anchors.rightMargin: Theme.space[3]
                                spacing: Theme.space[4]

                                // Gutter spacer — matches handleSlot width in BenchRow
                                Item { Layout.preferredWidth: Theme.space[5] }

                                // Strategy column — fills remaining space
                                Item { Layout.fillWidth: true; Layout.minimumWidth: 200 }

                                ColHeader { text: "Sharpe";   alignRight: true; Layout.preferredWidth: Theme.column.benchMetric }
                                ColHeader { text: "Max-DD";   alignRight: true; Layout.preferredWidth: Theme.column.benchMetric }
                                ColHeader { text: "Trades";   alignRight: true; Layout.preferredWidth: Theme.column.benchMetric }
                                ColHeader { text: "Status";   Layout.fillWidth: true; Layout.minimumWidth: 180 }
                                ColHeader { text: "Action";   alignRight: true; Layout.preferredWidth: Theme.column.benchAction }
                            }
                        }

                        // ---- Strategy rows ----------------------------------
                        Repeater {
                            model: sectionRoot.rowOrder

                            delegate: BenchRow {
                                width: sectionRoot.width
                                stage: sectionData.stage
                                strategyName: modelData.name || modelData.displayName || ""
                                strategyId: modelData.strategyId || ""
                                sharpe: root.formattedSharpe(modelData)
                                maxDD: root.formattedMaxDD(modelData)
                                tradeCount: root.formattedTrades(modelData)
                                // Status prose column (bench-brief §4)
                                statusWord: modelData.statusWord || ""
                                statusWordColor: root.statusWordColor(modelData.statusKind || "info")
                                statusProse: modelData.statusTail || ""
                                metaLine: root.metaLine(modelData)
                                // Action slot — wired in PR G.
                                // actionItems carries the compute_menu_items() output from
                                // read_models._compute_bench_action_menu (bench_v1.py).
                                // variant is derived from the items so the button weight
                                // reflects actual menu content, not a statusKind heuristic.
                                actionItems: modelData.actions || []
                                actionVariant: root.actionVariant(modelData)
                                // Drag reorder — PR H wires; no-op in PR F
                                onMoveRequested: () => {}
                            }
                        }
                    }
                }
            }

            // ---- Page footer -----------------------------------------------
            Column {
                width: parent.width - Theme.space[7] * 2
                spacing: Theme.space[3]

                Rectangle {
                    width: parent.width
                    height: 1
                    color: Theme.color.border.regular
                }

                Text {
                    width: parent.width * 0.78
                    text: "Promotion to paper requires walk-forward gate-pass on every window. "
                        + "Promotion beyond paper requires explicit human review per ADR 0004. "
                        + "Capital-bearing stages remain locked. Kill switch is a global affordance on the Anchor view."
                    color: Theme.color.text.muted
                    font.family: Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.italic: true
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Internal component: column header label (num-label style per brief §4)
    // 10px Public Sans uppercase letter-spaced, text.muted
    // -----------------------------------------------------------------------
    component ColHeader: Text {
        property bool alignRight: false
        color: Theme.color.text.muted
        font.family: Theme.typography.label.xs.family
        font.pixelSize: 10  // intentionally below label.xs (11px) — column headers read as meta, not content
        font.weight: Font.Medium
        font.letterSpacing: Theme.typography.label.xs.letterSpacing
        font.capitalization: Font.AllUppercase
        horizontalAlignment: alignRight ? Text.AlignRight : Text.AlignLeft
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}

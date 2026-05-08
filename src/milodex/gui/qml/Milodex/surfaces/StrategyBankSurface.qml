// StrategyBankSurface.qml — Milodex Strategy Bank observability surface.
//
// Second observability surface; second instance of the OperationalState
// architectural pattern (DESIGN_SYSTEM.md §9.1, AnchorSurface.qml pattern).
//
// Renders the canonical strategy bank from docs/STRATEGY_BANK.md:
//   - PAPER section: strategies authorised to run (with lifecycle-exempt
//     marginalia on the regime row and audit asterisk on pullback_rsi2)
//   - BLOCKED section: strategies failing walk-forward gates (with S/D/N
//     gate-failure chips and "flagged, not retired" italic marginalia for
//     dual_absolute)
//
// Data source: StrategyBankState QObject singleton (Python-side, 30s poll against
// data/milodex.db).  All property reads go through that singleton so the surface
// stays stateless.
//
// Tokens consumed:
//   color.surface.base, color.surface.raised, color.border.subtle, color.border.regular
//   color.text.primary, color.text.secondary, color.text.muted
//   color.brand.accent          — selected row accent bar (via StrategyRow)
//   status.negative             — error banner border, BLOCKED section wash, gate chips
//   typography.display.sm / .smItalic  — section headers + empty state
//   typography.deck             — section subheaders (italic Newsreader kickers)
//   typography.body.md          — banner body text
//   typography.label.xs         — section labels
//   space[2], space[3], space[5], space[6] — margins, gaps
//   radius.lg                   — BLOCKED section wash + banner
//   radius.md                   — banner corner
//   motion.fast                 — row hover (via StrategyRow)
//
// Selection state: selectedStrategyId tracks the currently-selected row.
// StrategyRow.selected is wired to it.  No detail inset in PR E; this is
// readiness for PR F (details panel).
//
// Editorial flourish (PR E polish): the BLOCKED section carries a low-alpha rust
// wash (status.negative @ 0.06 — bumped from 0.04 so it registers on Editorial
// Dark's near-black canvas) that reads "these are the failures" before any text
// is parsed.  One wash per surface; used only because the section's failure
// status IS the structural information.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Local state
    // ------------------------------------------------------------------

    // Tracks which strategy row is selected.  Visual only in PR E;
    // detail panel is PR F's concern.
    property string selectedStrategyId: ""

    // ------------------------------------------------------------------
    // Sharpe formatter — signed, 2 decimal places.
    // "+1.19" for positive, "-0.27" for negative, "—" for null/undefined.
    // ------------------------------------------------------------------

    function _formatSharpe(s) {
        if (s === null || s === undefined || isNaN(s)) return "—"
        var n = parseFloat(s)
        if (isNaN(n)) return "—"
        var abs = Math.abs(n).toFixed(2)
        return (n >= 0 ? "+" : "-") + abs
    }

    // ------------------------------------------------------------------
    // Main scroll column
    // ------------------------------------------------------------------

    Flickable {
        id: scrollArea
        anchors.fill: parent
        contentHeight: mainColumn.implicitHeight + Theme.space[6] * 2
        clip: true

        Column {
            id: mainColumn
            anchors {
                top:    parent.top
                left:   parent.left
                right:  parent.right
                margins: Theme.space[6]
            }
            spacing: Theme.space[5]

            // ==============================================================
            // Error banner — visible when StrategyBankState.dataStatus = "error"
            // Mirrors AnchorSurface.qml's broker-error banner pattern.
            // ==============================================================

            Item {
                id: errorBanner
                width: parent.width
                height: errorBannerRect.implicitHeight
                visible: StrategyBankState.dataStatus === "error"

                Rectangle {
                    id: errorBannerRect
                    anchors.left:  parent.left
                    anchors.right: parent.right
                    implicitHeight: errorBannerRow.implicitHeight + Theme.space[3] * 2
                    color:  Theme.color.surface.base
                    radius: Theme.radius.md
                    border.width: 1
                    border.color: Theme.status.negative

                    Row {
                        id: errorBannerRow
                        anchors {
                            left:    parent.left
                            right:   parent.right
                            top:     parent.top
                            margins: Theme.space[3]
                        }
                        spacing: Theme.space[2]

                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width:  8
                            height: 8
                            radius: Theme.radius.full
                            color:  Theme.status.negative
                        }

                        Text {
                            width:    parent.width - 8 - Theme.space[2]
                            text:     "Could not load strategy bank: " + StrategyBankState.dataErrorMessage
                            color:    Theme.status.negative
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.md.size
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            // ==============================================================
            // Loading state — only when status is "loading" and no data yet
            // ==============================================================

            Item {
                width:  parent.width
                height: Theme.space[7]
                visible: StrategyBankState.dataStatus === "loading"
                         && StrategyBankState.paperStrategies.length === 0
                         && StrategyBankState.blockedStrategies.length === 0

                Text {
                    anchors.centerIn: parent
                    text:  "Loading strategy bank..."
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size
                }
            }

            // ==============================================================
            // PAPER section
            // ==============================================================

            Column {
                id: paperSection
                width: parent.width
                spacing: Theme.space[3]
                visible: StrategyBankState.dataStatus !== "loading"
                         || StrategyBankState.paperStrategies.length > 0

                // Section header
                Column {
                    spacing: Theme.space[1]

                    Text {
                        text: "PAPER"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.display.sm.family
                        font.pixelSize: Theme.typography.display.sm.size
                        font.weight:    Theme.typography.display.sm.weight
                    }

                    // Italic Newsreader deck / kicker — the phrase "the deserving
                    // list" is sourced verbatim from STRATEGY_BANK.md's section
                    // heading and carries founder voice into the surface.
                    Text {
                        text: {
                            var n = StrategyBankState.paperStrategies.length
                            return "the deserving list — " + n + (n === 1 ? " strategy running" : " strategies running")
                        }
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.deck.family
                        font.pixelSize: Theme.typography.deck.size
                        font.weight:    Theme.typography.deck.weight
                        font.italic:    Theme.typography.deck.italic
                    }
                }

                // Paper rows
                Column {
                    width: parent.width
                    spacing: Theme.space[2]

                    Repeater {
                        model: StrategyBankState.paperStrategies
                        delegate: StrategyRow {
                            width: parent.width

                            strategyId:  modelData.strategyId
                            stage:       "paper"
                            metricValue: root._formatSharpe(modelData.sharpeRatio)
                            tradeCount:  modelData.tradeCount || 0
                            selected:    root.selectedStrategyId === modelData.strategyId
                            auditFlag:   modelData.auditFlag || false

                            // Lifecycle-exempt marginalia on the regime strategy row.
                            // Lower-case italic serif reads as editorial commentary —
                            // "this row has a different story" — rather than a
                            // bureaucratic stamp.  The operator still gets the
                            // information; the visual tone is calibrated.
                            note: (modelData.promotionType === "lifecycle_exempt")
                                  ? "lifecycle exempt"
                                  : ""

                            onClicked: root.selectedStrategyId = modelData.strategyId
                        }
                    }

                    // PAPER empty state — italic Newsreader editorial convention.
                    Text {
                        visible:    StrategyBankState.paperStrategies.length === 0
                                    && StrategyBankState.dataStatus === "ready"
                        width:      parent.width
                        text:       "No strategies promoted to paper."
                        color:      Theme.color.text.secondary
                        font.family:    Theme.typography.display.sm.family
                        font.pixelSize: Theme.typography.display.sm.size
                        font.weight:    Font.Normal
                        font.italic:    true
                    }
                }
            }

            // ==============================================================
            // BLOCKED section — with editorial rust-wash flourish.
            //
            // Round-2 reviewer requested more editorial flourishes.
            // The rust wash (status.negative @ 0.06) signals "these are the
            // failures" before any text is parsed — the section's failure status
            // IS the structural information, per DESIGN_SYSTEM.md §6.
            // Alpha bumped to 0.06 in PR E polish pass (0.04 was invisible on
            // Editorial Dark canvas).  One section-wash per surface; used only here.
            // ==============================================================

            Item {
                id: blockedSection
                width: parent.width
                // Grow to fit the inner column plus the negative-margin bleed.
                implicitHeight: blockedInner.implicitHeight + Theme.space[3] * 2
                visible: StrategyBankState.dataStatus !== "loading"
                         || StrategyBankState.blockedStrategies.length > 0

                // Rust-wash rectangle behind the section content.
                // Inset by negative space[3] on all sides so the wash bleeds
                // slightly past the section's content edge — the "blood under
                // the headline" editorial convention (round-2 reviewer steer).
                Rectangle {
                    id: blockedWash
                    anchors {
                        fill:          blockedInner
                        topMargin:     -Theme.space[3]
                        bottomMargin:  -Theme.space[3]
                        leftMargin:    -Theme.space[3]
                        rightMargin:   -Theme.space[3]
                    }
                    radius: Theme.radius.lg
                    // status.negative @ 0.06 — empirically registers across all three
                    // themes; 0.04 disappears on Editorial Dark's near-black canvas.
                    // Still well below the kill-switch panel wash (0.10), which is an
                    // active-alarm indicator; this is a section hint only.
                    color: Qt.rgba(Theme.status.negative.r,
                                   Theme.status.negative.g,
                                   Theme.status.negative.b, 0.06)
                }

                // Section inner column (sits above the wash in z-order by default)
                Column {
                    id: blockedInner
                    anchors {
                        top:   parent.top
                        left:  parent.left
                        right: parent.right
                        topMargin: Theme.space[3]
                    }
                    spacing: Theme.space[3]

                    // Section header
                    Column {
                        spacing: Theme.space[1]

                        Text {
                            text: "BLOCKED AT BACKTEST"
                            color: Theme.color.text.primary
                            font.family:    Theme.typography.display.sm.family
                            font.pixelSize: Theme.typography.display.sm.size
                            font.weight:    Theme.typography.display.sm.weight
                        }

                        // Italic Newsreader deck / kicker — the phrase "the
                        // failures" is sourced from the STRATEGY_BANK.md section
                        // tone and carries founder voice without invention.
                        Text {
                            text: {
                                var n = StrategyBankState.blockedStrategies.length
                                return "the failures — " + n + (n === 1 ? " strategy failing gates"
                                                                         : " strategies failing gates")
                            }
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.deck.family
                            font.pixelSize: Theme.typography.deck.size
                            font.weight:    Theme.typography.deck.weight
                            font.italic:    Theme.typography.deck.italic
                        }
                    }

                    // Blocked rows
                    Column {
                        width: parent.width
                        spacing: Theme.space[2]

                        Repeater {
                            model: StrategyBankState.blockedStrategies
                            delegate: StrategyRow {
                                width: parent.width

                                strategyId:           modelData.strategyId
                                stage:                "blocked"
                                metricValue:          root._formatSharpe(modelData.sharpeRatio)
                                tradeCount:           modelData.tradeCount || 0
                                selected:             root.selectedStrategyId === modelData.strategyId
                                auditFlag:            modelData.auditFlag || false
                                gateFailures:         modelData.gateFailures || []
                                flagFailingNotRetired: modelData.flagFailingNotRetired || false

                                onClicked: root.selectedStrategyId = modelData.strategyId
                            }
                        }

                        // BLOCKED empty state
                        Text {
                            visible:    StrategyBankState.blockedStrategies.length === 0
                                        && StrategyBankState.dataStatus === "ready"
                            width:      parent.width
                            text:       "No strategies blocked. Either you're nailing the gate, or the backtest queue is empty."
                            color:      Theme.color.text.secondary
                            font.family:    Theme.typography.display.sm.family
                            font.pixelSize: Theme.typography.display.sm.size
                            font.weight:    Font.Normal
                            font.italic:    true
                            wrapMode:       Text.WordWrap
                        }
                    }
                }
            }
        }
    }
}

// FrontSurface.qml — The Front Page (digest / front-porch view).
//
// The calm, conversational landing surface.  One column.  Date as the
// headline, P&L as the lead number, plain-language prose explaining the
// state of the bench, a featured strategy card ("at the gate"), and a
// short market summary.  No greetings — a newspaper does not greet you.
//
// Tone: warm but factual.  Reports state in plain language.  Does NOT
// recommend ("we suggest you promote X") — surfaces facts and lets the
// operator decide.
//
// Tokens consumed:
//   color.surface.canvas       — page background
//   color.brand.primary        — large P&L number, hero numerics
//   color.brand.accent         — period ornament on headline
//   color.text.primary / .secondary / .muted — prose hierarchy
//   color.border.subtle / .regular — section dividers, card border
//   status.positive / .negative — P&L sign, market tick coloring
//   typography.display.xl / .lg / .sm — date headline / P&L / featured-card title
//   typography.body.lg / .md   — italic prose
//   typography.label.xs        — eyebrows, section headers, tally labels
//   typography.data.md / .sm   — featured-card metric values, tally numbers
//
// Binds to read-only FrontPageState plus OperationalState.  Missing data is
// labelled honestly rather than filled with market-looking placeholders.

import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import Milodex 1.0

SurfaceBase {
    id: root

    captureContentHeight: scroller.contentHeight

    // ------------------------------------------------------------------
    // Live read model.  FrontPageState is read-only: it renders current
    // observability state and never mutates strategy/risk state.
    // ------------------------------------------------------------------

    readonly property var summary: FrontPageState.summary || ({})
    readonly property bool hasSummaryData: FrontPageState.lastRefreshedAt !== ""
    readonly property var asOf: summary.asOf || ""
    readonly property var pnl: summary.pnl || ({ today: 0, todayPct: 0, sparkline: [0] })
    readonly property int totalConfigs: summary.totalConfigs || 0
    readonly property int runningCount: summary.runningCount || 0
    readonly property int liveCount: summary.liveCount || 0
    readonly property var stageTally: summary.stageTally || ({ backtest: 0, paper: 0, micro_live: 0, live: 0 })
    readonly property var feature: summary.feature || ({})
    // Market tape: read directly from MarketTapeState.rows (same data DESK uses).
    // Helper to find a symbol's pctChange from the tape rows list.
    function tapeRowPct(symbol) {
        var rows = MarketTapeState.rows
        for (var i = 0; i < rows.length; i++) {
            if (rows[i].symbol === symbol && rows[i].pctChange !== null)
                return rows[i].pctChange * 100
        }
        return null
    }
    readonly property var _tapeSpy: tapeRowPct("SPY")
    readonly property var _tapeQqq: tapeRowPct("QQQ")
    readonly property var _tapeIwm: tapeRowPct("IWM")
    // marketKnown: true when MarketTapeState has at least one valid pctChange row
    readonly property bool marketKnown: (_tapeSpy !== null || _tapeQqq !== null || _tapeIwm !== null)
    readonly property var market: ({
        spyPct: _tapeSpy !== null ? _tapeSpy : 0,
        qqqPct: _tapeQqq !== null ? _tapeQqq : 0,
        iwmPct: _tapeIwm !== null ? _tapeIwm : 0,
        weatherLine: MarketTapeState.dataStatus !== "error" ? "" : "Market tape pending.",
        regime: marketKnown ? "live" : "UNKNOWN",
    })
    readonly property string sessionLine: "Today | Market "
                                          + (OperationalState.marketOpen ? "open" : "closed")
                                          + " | " + OperationalState.tradingMode
    readonly property string frontRiskCopy: OperationalState.killSwitchActive
        ? "Kill switch fired · manual reset required"
        : ((OperationalState.tradingMode === "paper" ? "Paper only" : OperationalState.tradingMode)
           + " · "
           + (OperationalState.brokerStatus === "connected" ? "broker connected"
              : OperationalState.brokerStatus === "error" ? "broker error"
              : "broker stale")
           + " · no real exposure")
    readonly property color sessionDotColor: OperationalState.killSwitchActive ? Theme.status.negative
                                           : OperationalState.marketOpen ? Theme.status.positive
                                           : Theme.status.warning

    // Delegate to Formatters singleton (PR10).
    function moneyParts(value) { return Formatters.moneyParts(value) }

    readonly property var pnlParts: moneyParts(pnl.today || 0)
    readonly property bool pnlIsFlat: Math.abs(Number(pnl.today || 0)) < 0.005
    readonly property bool pnlIsPositive: Number(pnl.today || 0) > 0
    readonly property color pnlSignalColor: pnlIsFlat ? Theme.color.text.secondary
                                      : pnlIsPositive ? Theme.status.positive
                                      : Theme.status.negative
    readonly property string pnlDirectionWord: pnlIsFlat ? "Flat" : (pnlIsPositive ? "Up" : "Down")
    readonly property string marketSummaryText: marketKnown
        ? (root.market.weatherLine || "Market summary is available from the read model.")
        : (root.market.weatherLine || "Market summary awaits a data-feed read model.")

    // ------------------------------------------------------------------
    // Background fill
    // ------------------------------------------------------------------

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.canvas
    }

    // ------------------------------------------------------------------
    // Single-column scrollable layout, centered with comfortable margins
    // ------------------------------------------------------------------

    // Center the column with max-width so prose stays readable (720).
    ScrollSurface {
        id: scroller
        maxContentWidth: 720

        Column {
            id: pageColumn
            width: parent.width
            spacing: Theme.space[6]

            // ====================================================
            // RUNNING HEAD — canvas-anchoring marginalia
            //
            // Per DESIGN.md v0.2 §4 (FRONT canvas-anchoring), a
            // single mono running head pins the page to its edition
            // and date. Hairline rule below separates it from the
            // editorial body.
            // ====================================================
            Column {
                width: parent.width
                spacing: Theme.space[2]

                Text {
                    width: parent.width
                    text:  "MILODEX · NO. 1" + (root.asOf ? "  —  " + root.asOf : "")
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.data.xs.family
                    font.pixelSize: Theme.typography.data.xs.size
                    font.features:  Theme.typography.data.xs.features
                    font.capitalization: Font.AllUppercase
                    font.letterSpacing: 0.6
                    elide: Text.ElideRight
                }
                Rectangle {
                    width:  parent.width
                    height: 1
                    color:  Theme.color.border.subtle
                }
            }

            // Section status — loading / error / no-data-yet for the
            // FrontPageState read model. One failed builder never blanks
            // the rest of the page (PR-8 GUI surface honesty).
            SectionStatus {
                width: parent.width
                status: FrontPageState.dataStatus
                errorMessage: FrontPageState.dataErrorMessage
                hasData: root.hasSummaryData
            }

            // ====================================================
            // HEADLINE — eyebrow + date
            // ====================================================
            Column {
                width: parent.width
                spacing: Theme.space[3]

                Row {
                    spacing: Theme.space[2]

                    Rectangle {
                        width:  6; height: 6; radius: 3
                        anchors.verticalCenter: parent.verticalCenter
                        color:  root.sessionDotColor
                    }
                    Text {
                        text: root.sessionLine
                        color: Theme.color.text.secondary
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Row {
                    spacing: 0
                    Text {
                        text:  root.asOf
                        color: Theme.color.brand.primary
                        font.family:    Theme.typography.display.xl.family
                        font.pixelSize: Theme.typography.display.xl.size
                        font.weight:    Theme.typography.display.xl.weight
                        font.letterSpacing: -0.8
                    }
                    Text {
                        text:  "."
                        color: Theme.color.brand.accent
                        font.family:    Theme.typography.display.xl.family
                        font.pixelSize: Theme.typography.display.xl.size
                        font.weight:    Theme.typography.display.xl.weight
                    }
                }
            }

            Rectangle {
                id: frontRiskPosture
                width: parent.width
                height: riskPostureRow.implicitHeight + Theme.space[2] * 2
                color: "transparent"
                border.color: Theme.color.border.regular
                border.width: 1
                radius: Theme.radius.sm

                Row {
                    id: riskPostureRow
                    anchors.centerIn: parent
                    spacing: Theme.space[2]

                    Text {
                        text: "Risk posture"
                        color: Theme.color.text.primary
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Font.DemiBold
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }
                    Text {
                        text: root.frontRiskCopy
                        color: OperationalState.killSwitchActive ? Theme.status.negative : Theme.color.text.secondary
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.italic:    true
                    }
                }
            }

            // ====================================================
            // TODAY — P&L block with sparkline
            //
            // Gated on FrontPageState (feeds pnl): on error/first-load the
            // property defaults would render a fabricated calm "$0.00 / Flat /
            // No realized movement" — hide the block instead (mirrors DESK's
            // per-section hide-on-error contract). The SectionStatus banner
            // above stays visible and carries the honest state.
            // ====================================================
            Column {
                width: parent.width
                spacing: Theme.space[3]
                visible: FrontPageState.dataStatus !== "error"

                Text {
                    text: root.pnlIsFlat ? "Flat" : "Today"
                    color: Theme.color.text.secondary
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                // Massive P&L number
                Row {
                    spacing: 0
                    anchors.left: parent.left
                    Text {
                        text:  root.pnlIsFlat ? "" : root.pnlParts.sign
                        color: root.pnlSignalColor
                        font.family:    Theme.typography.display.heroNum.family
                        font.pixelSize: Theme.typography.display.heroNum.size
                        font.weight:    Font.Normal
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text:  "$"
                        visible: !root.pnlIsFlat
                        color: Theme.color.brand.primary
                        font.family:    Theme.typography.display.heroAccent.family
                        font.pixelSize: Theme.typography.display.heroAccent.size
                        font.weight:    Theme.typography.display.heroAccent.weight
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 18
                    }
                    Text {
                        text:  root.pnlIsFlat ? "$0.00" : root.pnlParts.whole
                        color: Theme.color.brand.primary
                        font.family:    Theme.typography.display.heroNum.family
                        font.pixelSize: Theme.typography.display.heroNum.size
                        font.weight:    Theme.typography.display.heroNum.weight
                        font.letterSpacing: -2
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text:  root.pnlParts.cents
                        visible: !root.pnlIsFlat
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.display.heroCents.family
                        font.pixelSize: Theme.typography.display.heroCents.size
                        font.italic:    Theme.typography.display.heroCents.italic
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 18
                    }
                }

                // Italic-prose context — facts only, no reassurance
                Text {
                    width:    parent.width
                    wrapMode: Text.WordWrap
                    textFormat: Text.RichText
                    color:    Theme.color.text.secondary
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size + 1
                    text: "<span style='font-style:italic; color:" + Theme.color.text.primary + "'>"
                          + (root.pnlIsFlat ? "No realized movement" : root.pnlDirectionWord + " " + Math.abs(Number(root.pnl.todayPct || 0)).toFixed(2) + "%")
                          + "</span> on the latest snapshot. "
                          + "<span style='font-style:italic'>Broker and market state stay pinned above.</span>"
                }

                // Sparkline
                Sparkline {
                    width:  parent.width
                    height: 72
                    series: root.pnl.sparkline
                    areaAlpha: 0.10
                }

                // Sparkline caption — small italic right-aligned
                Item {
                    width: parent.width
                    height: sparkCaption.implicitHeight
                    Text {
                        id: sparkCaption
                        anchors.right: parent.right
                        text: "latest portfolio snapshots"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.sm.size - 1
                        font.italic:    true
                    }
                }
            }

            // ====================================================
            // YOUR STRATEGIES — prose + tally
            //
            // Gated on FrontPageState (feeds totalConfigs / runningCount /
            // liveCount / stageTally): on error the zero defaults would render
            // a fabricated "00 on the bench / 0 of your 0 strategies / all-zero
            // tally". Hide on error rather than assert a false calm.
            // ====================================================
            Column {
                width: parent.width
                spacing: Theme.space[4]
                visible: FrontPageState.dataStatus !== "error"

                // Heading row with right-aligned count
                Item {
                    width:  parent.width
                    height: yourStratLabel.implicitHeight

                    Text {
                        id: yourStratLabel
                        text: "Your Strategies"
                        anchors.left: parent.left
                        color: Theme.color.text.muted
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }
                    Text {
                        text: ("0" + root.totalConfigs).slice(-2) + " on the bench"
                        anchors.right: parent.right
                        anchors.verticalCenter: yourStratLabel.verticalCenter
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.data.sm.family
                        font.pixelSize: Theme.typography.data.sm.size
                        font.features:  Theme.typography.data.sm.features
                    }
                }

                // Larger conversational prose — numbers rendered in data mono (DESIGN.md §5.3)
                // Restructured into three Row-segments so numeric tokens use JetBrains Mono
                // rather than the body-sans family.
                Column {
                    width: parent.width
                    spacing: 0

                    // Line 1: "<N> of your <N> strategies are working right now."
                    Flow {
                        width: parent.width
                        spacing: 0

                        Text {
                            text: root.runningCount
                            color: Theme.color.text.primary
                            font.family:   Theme.typography.data.md.family
                            font.pixelSize: Theme.typography.body.lgPlus.size
                            font.features: Theme.typography.data.md.features
                            font.weight:   Font.Medium
                        }
                        Text {
                            text: " of your "
                            color: Theme.color.text.secondary
                            font.family:   Theme.typography.body.lgPlus.family
                            font.pixelSize: Theme.typography.body.lgPlus.size
                            font.italic:   true
                            lineHeight:    Theme.typography.body.lgPlus.lineHeight
                        }
                        Text {
                            text: root.totalConfigs
                            color: Theme.color.text.primary
                            font.family:   Theme.typography.data.md.family
                            font.pixelSize: Theme.typography.body.lgPlus.size
                            font.features: Theme.typography.data.md.features
                            font.weight:   Font.Medium
                        }
                        Text {
                            text: " strategies are working right now."
                            color: Theme.color.text.secondary
                            font.family:   Theme.typography.body.lgPlus.family
                            font.pixelSize: Theme.typography.body.lgPlus.size
                            font.italic:   true
                            lineHeight:    Theme.typography.body.lgPlus.lineHeight
                        }
                    }

                    // Line 2: pure prose
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: "Most are still on paper, collecting live-feed evidence with simulated capital. Only"
                        color: Theme.color.text.secondary
                        font.family:   Theme.typography.body.lgPlus.family
                        font.pixelSize: Theme.typography.body.lgPlus.size
                        font.italic:   true
                        lineHeight:    Theme.typography.body.lgPlus.lineHeight
                    }

                    // Line 3: "<N> have any real capital, and even those are capped small."
                    Flow {
                        width: parent.width
                        spacing: 0

                        Text {
                            text: root.liveCount
                            color: Theme.color.text.primary
                            font.family:   Theme.typography.data.md.family
                            font.pixelSize: Theme.typography.body.lgPlus.size
                            font.features: Theme.typography.data.md.features
                            font.weight:   Font.Medium
                        }
                        Text {
                            text: " have any real capital, and even those are capped small."
                            color: Theme.color.text.secondary
                            font.family:   Theme.typography.body.lgPlus.family
                            font.pixelSize: Theme.typography.body.lgPlus.size
                            font.italic:   true
                            lineHeight:    Theme.typography.body.lgPlus.lineHeight
                        }
                    }
                }

                // Hairline rule
                Rectangle {
                    width: parent.width
                    height: 1
                    color: Theme.color.border.subtle
                }

                // 4-up tally: backtest / paper / micro-live / full-live
                RowLayout {
                    width: parent.width
                    spacing: Theme.space[6]

                    Repeater {
                        model: [
                            { num: root.stageTally.backtest, label: "in backtest" },
                            { num: root.stageTally.paper,    label: "on paper" },
                            { num: root.stageTally.micro_live || 0, label: "micro-live" },
                            { num: root.stageTally.live,     label: "full live" }
                        ]
                        delegate: Column {
                            Layout.fillWidth: true
                            spacing: 4

                            Text {
                                text: modelData.num
                                color: Theme.color.brand.primary
                                font.family:    Theme.typography.display.tally.family
                                font.pixelSize: Theme.typography.display.tally.size
                                font.weight:    Theme.typography.display.tally.weight
                            }
                            Text {
                                text:  modelData.label
                                color: Theme.color.text.muted
                                font.family: Theme.typography.body.md.family
                                font.pixelSize: Theme.typography.body.sm.size
                                font.italic: true
                            }
                        }
                    }
                }
            }

            // ====================================================
            // AT THE GATE — featured strategy as definition block
            //
            // Per DESIGN.md v0.2 §6 (negative space — bordered card
            // frames for content blocks) and DESIGN_SYSTEM.md v0.2 §7.7
            // (Definition block): no bordered card chrome around the
            // content. Section bounded by a hairline rule below the
            // small-caps section head; content rendered as a
            // definition list (small-caps labels in left gutter, mono
            // values right). Actions are inline text links, not
            // pinned-bottom CTA buttons.
            //
            // Gated on FrontPageState (feeds feature): on error the empty
            // feature default would render a fabricated "No strategy needs
            // attention / no queue" idle card. Hide on error.
            // ====================================================
            Column {
                width: parent.width
                spacing: Theme.space[3]
                visible: FrontPageState.dataStatus !== "error"

                Item {
                    width:  parent.width
                    height: gateLabel.implicitHeight

                    Text {
                        id: gateLabel
                        text: "At the gate"
                        anchors.left: parent.left
                        color: Theme.color.text.muted
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }
                    Text {
                        text: root.feature.name ? "watching next gate" : "no queue"
                        anchors.right: parent.right
                        anchors.verticalCenter: gateLabel.verticalCenter
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.data.sm.family
                        font.pixelSize: Theme.typography.data.sm.size
                        font.features:  Theme.typography.data.sm.features
                    }
                }

                // Section hairline — DESIGN_SYSTEM.md §7.7 definition-block
                // pattern: sections bounded by hairline rules, not card chrome.
                // border.regular (vs border.subtle) for legible separation at
                // editorial body sizes — the subtle weight read as ambiguous
                // in the 2026-05-13 visual smoke.
                Rectangle {
                    width:  parent.width
                    height: 1
                    color:  Theme.color.border.regular
                }

                Column {
                    id: featureCardCol
                    width: parent.width
                    spacing: Theme.space[3]

                    Text {
                        text: root.feature.name || "No strategy needs attention"
                        color: Theme.color.brand.primary
                        font.family:    Theme.typography.display.sm.family
                        font.pixelSize: Theme.typography.display.lg.size - 12
                        font.weight:    Theme.typography.display.sm.weight
                    }

                    // 2-column "facts" grid — labels in left gutter, values right-aligned
                    GridLayout {
                        width: parent.width
                        columns: 2
                        rowSpacing:    Theme.space[2]
                        columnSpacing: Theme.space[6]

                        // Stage / Sharpe
                        Text {
                            text: "Stage"
                            color: Theme.color.text.muted
                            font.family:        Theme.typography.label.xs.family
                            font.pixelSize:     Theme.typography.label.xs.size
                            font.weight:        Theme.typography.label.xs.weight
                            font.letterSpacing: Theme.typography.label.xs.letterSpacing
                            font.capitalization: Font.AllUppercase
                            Layout.preferredWidth: 140
                        }
                        Item {
                            Layout.fillWidth: true
                            implicitHeight:   factRow1.implicitHeight
                            Row {
                                id: factRow1
                                anchors.right: parent.right
                                spacing: Theme.space[6]

                                Text {
                                    text: root.feature.stage || "idle"
                                    color: Theme.color.text.primary
                                    font.family:    Theme.typography.data.md.family
                                    font.pixelSize: Theme.typography.data.md.size
                                    font.features:  Theme.typography.data.md.features
                                }
                                Text {
                                    text: "sharpe"
                                    color: Theme.color.text.muted
                                    font.family:        Theme.typography.label.xs.family
                                    font.pixelSize:     Theme.typography.label.xs.size
                                    font.weight:        Theme.typography.label.xs.weight
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                    font.capitalization: Font.AllUppercase
                                }
                                Text {
                                    text: root.feature.sharpe === undefined || root.feature.sharpe === null ? "—" : Number(root.feature.sharpe).toFixed(2)
                                    color: Theme.color.text.primary
                                    font.family:    Theme.typography.data.md.family
                                    font.pixelSize: Theme.typography.data.md.size
                                    font.features:  Theme.typography.data.md.features
                                }
                            }
                        }

                        // Max drawdown / Trades
                        Text {
                            text: "Max drawdown"
                            color: Theme.color.text.muted
                            font.family:        Theme.typography.label.xs.family
                            font.pixelSize:     Theme.typography.label.xs.size
                            font.weight:        Theme.typography.label.xs.weight
                            font.letterSpacing: Theme.typography.label.xs.letterSpacing
                            font.capitalization: Font.AllUppercase
                            Layout.preferredWidth: 140
                        }
                        Item {
                            Layout.fillWidth: true
                            implicitHeight:   factRow2.implicitHeight
                            Row {
                                id: factRow2
                                anchors.right: parent.right
                                spacing: Theme.space[6]

                                Text {
                                    text: root.feature.maxDrawdownPct === undefined || root.feature.maxDrawdownPct === null ? "—" : Number(root.feature.maxDrawdownPct).toFixed(1) + "%"
                                    color: Theme.color.text.primary
                                    font.family:    Theme.typography.data.md.family
                                    font.pixelSize: Theme.typography.data.md.size
                                    font.features:  Theme.typography.data.md.features
                                }
                                Text {
                                    text: "trades"
                                    color: Theme.color.text.muted
                                    font.family:        Theme.typography.label.xs.family
                                    font.pixelSize:     Theme.typography.label.xs.size
                                    font.weight:        Theme.typography.label.xs.weight
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                    font.capitalization: Font.AllUppercase
                                }
                                Text {
                                    text: root.feature.tradeCount || 0
                                    color: Theme.color.text.primary
                                    font.family:    Theme.typography.data.md.family
                                    font.pixelSize: Theme.typography.data.md.size
                                    font.features:  Theme.typography.data.md.features
                                }
                            }
                        }

                        // Gates for next stage — derived from feature.stage via JS map
                        // (_StrategyRow.as_qml() exports `stage`, not `gatesFor`)
                        Text {
                            text: "Gates for " + ({"backtest": "paper", "paper": "micro_live", "micro_live": "live"}[root.feature.stage] || root.feature.stage || "next")
                            color: Theme.color.text.muted
                            font.family:        Theme.typography.label.xs.family
                            font.pixelSize:     Theme.typography.label.xs.size
                            font.weight:        Theme.typography.label.xs.weight
                            font.letterSpacing: Theme.typography.label.xs.letterSpacing
                            font.capitalization: Font.AllUppercase
                            Layout.preferredWidth: 140
                        }
                        Text {
                            text: root.feature.statusWord || "watching"
                            color: root.feature.statusKind === "warning" ? Theme.status.warning
                                 : root.feature.statusKind === "positive" ? Theme.status.positive
                                 : Theme.status.info
                            horizontalAlignment: Text.AlignRight
                            font.family:    Theme.typography.data.md.family
                            font.pixelSize: Theme.typography.data.md.size
                            font.features:  Theme.typography.data.md.features
                            font.weight:    Font.Medium
                            Layout.fillWidth: true
                        }
                    }

                    // Spacer + inline action links — DESIGN_SYSTEM.md §7.7:
                    // no pinned-bottom CTAs, no bordered-button chrome.
                    // Actions render as inline editorial text links.
                    Item { width: parent.width; height: Theme.space[2] }

                    Row {
                        spacing: Theme.space[5]

                        Text {
                            id: openInBenchLink
                            text: "open in bench →"
                            color: openInBenchMouse.containsMouse
                                    ? Theme.color.text.primary
                                    : Theme.color.text.secondary
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.md.size
                            Behavior on color {
                                ColorAnimation { duration: Theme.motion.fast }
                            }
                            MouseArea {
                                id: openInBenchMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: Window.window.activeSurface = "bench"
                            }
                        }
                    }
                }
            }

            // ====================================================
            // THE WIDER MARKET — single italic prose line
            //
            // Gated on MarketTapeState (feeds the index percentages and
            // weatherLine) — its OWN read model, not FrontPageState. On a tape
            // error, hide the whole block rather than print stale/zero index
            // moves (mirrors DESK's MarketTape hide-on-error).
            // ====================================================
            Column {
                width: parent.width
                spacing: Theme.space[3]
                visible: MarketTapeState.dataStatus !== "error"

                Text {
                    text: "The Wider Market"
                    color: Theme.color.text.muted
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                // Market percentages rendered in data mono (DESIGN.md §5.3); prose in body sans.
                // When market is unknown, falls back to a single prose Text.
                Loader {
                    width: parent.width
                    sourceComponent: root.marketKnown ? marketKnownRow : marketUnknownText
                }

                Component {
                    id: marketKnownRow
                    Flow {
                        width: parent.width
                        spacing: 0

                        Text {
                            text: "S&P "
                            color: Theme.color.text.secondary
                            font.family:   Theme.typography.body.mdPlus.family
                            font.pixelSize: Theme.typography.body.mdPlus.size
                            font.italic:   true
                            lineHeight:    Theme.typography.body.mdPlus.lineHeight
                        }
                        Text {
                            text: Number(root.market.spyPct || 0).toFixed(2) + "%"
                            color: Theme.color.text.primary
                            font.family:   Theme.typography.data.md.family
                            font.pixelSize: Theme.typography.body.mdPlus.size
                            font.features: Theme.typography.data.md.features
                        }
                        Text {
                            text: ", Nasdaq "
                            color: Theme.color.text.secondary
                            font.family:   Theme.typography.body.mdPlus.family
                            font.pixelSize: Theme.typography.body.mdPlus.size
                            font.italic:   true
                            lineHeight:    Theme.typography.body.mdPlus.lineHeight
                        }
                        Text {
                            text: Number(root.market.qqqPct || 0).toFixed(2) + "%"
                            color: Theme.color.text.primary
                            font.family:   Theme.typography.data.md.family
                            font.pixelSize: Theme.typography.body.mdPlus.size
                            font.features: Theme.typography.data.md.features
                        }
                        Text {
                            text: ", small-caps "
                            color: Theme.color.text.secondary
                            font.family:   Theme.typography.body.mdPlus.family
                            font.pixelSize: Theme.typography.body.mdPlus.size
                            font.italic:   true
                            lineHeight:    Theme.typography.body.mdPlus.lineHeight
                        }
                        Text {
                            text: Number(root.market.iwmPct || 0).toFixed(2) + "%"
                            color: Theme.color.text.primary
                            font.family:   Theme.typography.data.md.family
                            font.pixelSize: Theme.typography.body.mdPlus.size
                            font.features: Theme.typography.data.md.features
                        }
                        Text {
                            text: ". " + root.marketSummaryText
                            color: Theme.color.text.secondary
                            font.family:   Theme.typography.body.mdPlus.family
                            font.pixelSize: Theme.typography.body.mdPlus.size
                            font.italic:   true
                            lineHeight:    Theme.typography.body.mdPlus.lineHeight
                            wrapMode:      Text.WordWrap
                        }
                    }
                }

                Component {
                    id: marketUnknownText
                    Text {
                        width:    parent.width
                        wrapMode: Text.WordWrap
                        text:     root.marketSummaryText
                        color:    Theme.color.text.secondary
                        font.family:    Theme.typography.body.mdPlus.family
                        font.pixelSize: Theme.typography.body.mdPlus.size
                        font.italic:    true
                        lineHeight:     Theme.typography.body.mdPlus.lineHeight
                    }
                }
            }

            // ====================================================
            // FOOTER — italic close-out
            // ====================================================
            Item {
                width: parent.width
                height: Theme.space[6]
            }

            Rectangle {
                width:  parent.width
                height: 1
                color:  Theme.color.border.subtle
            }

            // Colophon — running edition mark + session state.
            // Replaces the prior italic close-out filler ("that's the
            // whole picture.") with a real mono colophon that pins
            // the page to its edition and market context.
            Item {
                width: parent.width
                height: colophonLeft.implicitHeight + Theme.space[3]

                Text {
                    id: colophonLeft
                    anchors.left: parent.left
                    anchors.top:  parent.top
                    anchors.topMargin: Theme.space[3]
                    text:  "MILODEX · NO. 1"
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.data.xs.family
                    font.pixelSize: Theme.typography.data.xs.size
                    font.features:  Theme.typography.data.xs.features
                    font.capitalization: Font.AllUppercase
                    font.letterSpacing: 0.6
                }
                Text {
                    anchors.right: parent.right
                    anchors.top:   parent.top
                    anchors.topMargin: Theme.space[3]
                    text:  "Filed " + (root.asOf || "—") + " · Market " + (OperationalState.marketOpen ? "open" : "closed")
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.data.xs.family
                    font.pixelSize: Theme.typography.data.xs.size
                    font.features:  Theme.typography.data.xs.features
                    font.capitalization: Font.AllUppercase
                    font.letterSpacing: 0.6
                }
            }

            // Bottom padding
            Item { width: parent.width; height: Theme.space[7] }
        }
    }
}

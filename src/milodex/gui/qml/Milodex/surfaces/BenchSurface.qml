// BenchSurface.qml — The Strategy Bench surface.
//
// Implements the synthesis design from docs/mockups/bench-brief.md:
// kanban-ergonomics + editorial-print aesthetics.  Vertical stage-
// stacks (idle → backtest → paper → micro-live → live), full-width
// rows with editorial typography, and read-only evidence modals for the
// gate-driven promotion model.
//
// Phase 5 scope: read-only surface wired to BenchState.sections. Phase 6
// may add drag or write gestures; this pass keeps rows observational.
//
// Tokens consumed:
//   color.surface.canvas   — page background
//   color.text.primary     — headline (Newsreader display)
//   color.text.secondary   — italic standfirst captions
//   color.text.muted       — eyebrow, stage counts, empty states
//   color.brand.accent     — LIVE-section rule (heavier 2px)
//   color.border           — section-header rule (1px)
//   color.border.subtle    — column-header underline
//   typography.display.xl  — page headline ("THE BENCH")
//   typography.body.md     — italic standfirst
//   typography.label.xs    — eyebrow, stage names, column headers
//   typography.data.xs     — stage counts (mono)
//
// Implementation notes:
//   - Flickable scroll container (per Surface.qml / DesignSystemShowcase
//     Qt 6.11 module-cache constraints).
//   - Modal overlays are siblings of the Flickable at root z-level so
//     they cover the whole surface area.
//   - Each section has: header (i. STAGE · NN · caption + heavy rule),
//     column-headers row (Sharpe / max-dd / trades), then BenchRow per
//     strategy, then bottom margin.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    property real captureContentHeight: scroller.contentHeight

    // ------------------------------------------------------------------
    // Mock data — strategies grouped by stage.
    //
    // Per-strategy fields used by BenchRow + modal flow:
    //   strategyName, strategyId       — display
    //   sharpe, maxDD, tradeCount      — pre-formatted
    //   signalKind                     — "positive" / "warning" / "info" / "negative"
    //   signalWord, proseTail          — split status sentence (signal word + tail)
    //   metaLine                       — small mono context line under prose
    //   action                         — { variant, label }
    //   modalKind                      — "blocked" / "typed" / "consequence" / ""
    //                                    (drives which modal opens on action click)
    //   modalEyebrow, modalTitle       — modal copy
    //   modalProse                     — modal italic body paragraph
    //   gateRows                       — array for blocked-promotion modal
    //                                    [{name, current, required, passes}]
    //   remediation                    — string array for "what unblocks this"
    //   confirmPhrase                  — typed-confirm required string ("PROMOTE", "RETIRE", etc.)
    //   confirmActionLabel             — typed-confirm action label
    //   consequenceActionLabel         — consequence-confirm primary action label
    //
    // The data faithfully mirrors docs/mockups/bench-surface.html so the
    // mockup and the live surface can be A/B compared.
    // ------------------------------------------------------------------

    property var benchData: BenchState.sections
    /* Legacy PR1 mock data retained below only as historical context.
    [
        {
            stage: "idle",
            stageRoman: "i.",
            stageName: "Idle",
            stageCaption: "configured, not yet run",
            strategies: [
                {
                    strategyName: "Internal Bar Strength",
                    strategyId:   "meanrev.daily.ibs_lowclose.v1",
                    sharpe: "—", maxDD: "—", tradeCount: "—",
                    signalKind: "info",
                    signalWord: "Config valid",
                    proseTail:  "— ready to backtest.",
                    metaLine:   "awaiting first run",
                    action: { variant: "secondary", label: "Run backtest →" },
                    modalKind: "consequence",
                    modalEyebrow: "Confirm move",
                    modalTitle:   "Run backtest on Internal Bar Strength.",
                    modalProse:   "Walk-forward backtest will run across configured windows. This may take 1-2 hours and does not affect any active session.",
                    consequenceActionLabel: "Run backtest →"
                },
                {
                    strategyName: "Turn-of-Month SPY",
                    strategyId:   "seasonality.daily.turn_of_month.spy.v1",
                    sharpe: "—", maxDD: "—", tradeCount: "—",
                    signalKind: "info",
                    signalWord: "Config valid",
                    proseTail:  "— ready to backtest.",
                    metaLine:   "awaiting first run",
                    action: { variant: "secondary", label: "Run backtest →" },
                    modalKind: "consequence",
                    modalEyebrow: "Confirm move",
                    modalTitle:   "Run backtest on Turn-of-Month SPY.",
                    modalProse:   "Walk-forward backtest will run across configured windows. This may take 1-2 hours and does not affect any active session.",
                    consequenceActionLabel: "Run backtest →"
                }
            ]
        },
        {
            stage: "backtest",
            stageRoman: "ii.",
            stageName: "Backtest",
            stageCaption: "historical evidence in flight",
            strategies: [
                {
                    strategyName: "Donchian 20/10",
                    strategyId:   "breakout.daily.donchian_20_10.sector_etfs.v1",
                    sharpe: "+0.87", maxDD: "9.2%", tradeCount: "435",
                    signalKind: "positive",
                    signalWord: "Walk-forward complete",
                    proseTail:  "— gates pass on every window.",
                    metaLine:   "paper-ready · evidence locked 14d ago",
                    action: { variant: "primary", label: "→ Promote to paper" },
                    modalKind: "consequence",
                    modalEyebrow: "Confirm promotion",
                    modalTitle:   "Promote Donchian 20/10 to paper.",
                    modalProse:   "This will begin paper-trading the strategy against the live market data feed with no real capital. Evidence collection on paper performance starts immediately.",
                    consequenceActionLabel: "Move →"
                },
                {
                    strategyName: "52-Week High Proximity",
                    strategyId:   "momentum.daily.52w_high_proximity.largecap.v1",
                    sharpe: "+0.41", maxDD: "17.1%", tradeCount: "192",
                    signalKind: "warning",
                    signalWord: "Max-dd 17.1% over 15% gate",
                    proseTail:  "— needs reparam.",
                    metaLine:   "sharpe 0.41 also below 0.50 floor",
                    action: { variant: "secondary", label: "View evidence →" },
                    modalKind: "blocked",
                    modalEyebrow: "Cannot promote yet",
                    modalTitle:   "52-Week High Proximity fails two gates.",
                    modalProse:   "Promotion to paper requires the strategy to clear all walk-forward gates. This config fails on max drawdown and Sharpe.",
                    gateRows: [
                        { name: "Sharpe ratio", current: "+0.41", required: "≥ 0.50",  passes: false },
                        { name: "Max drawdown", current: "17.1%", required: "≤ 15.0%", passes: false },
                        { name: "Trade count",  current: "192",   required: "≥ 30",    passes: true }
                    ],
                    remediation: [
                        "Reparam the config to target a higher Sharpe and tighter drawdown.",
                        "Re-run walk-forward on the reparam'd config.",
                        "Open the strategy detail to inspect Sharpe by walk-forward window."
                    ]
                },
                {
                    strategyName: "Dual Absolute (GEM)",
                    strategyId:   "momentum.daily.dual_absolute_gem.weekly.v1",
                    sharpe: "+0.55", maxDD: "11.4%", tradeCount: "88",
                    signalKind: "info",
                    signalWord: "Run incomplete",
                    proseTail:  "— 32% of walk-forward windows complete.",
                    metaLine:   "eta ~1h 40m · started 11:42 ET",
                    action: { variant: "ghost", label: "in progress…" },
                    modalKind: ""
                }
            ]
        },
        {
            stage: "paper",
            stageRoman: "iii.",
            stageName: "Paper",
            stageCaption: "live feed, no capital",
            strategies: [
                {
                    strategyName: "ATR Channel Breakout",
                    strategyId:   "breakout.daily.atr_channel.sector_etfs.v1",
                    sharpe: "+0.64", maxDD: "11.2%", tradeCount: "433",
                    signalKind: "positive",
                    signalWord: "Gates pass",
                    proseTail:  "— paper evidence passes; capital stages locked.",
                    metaLine:   "day 88 · 3 open positions",
                    action: { variant: "ghost", label: "locked" },
                    modalKind: "consequence",
                    modalEyebrow: "Capital stages locked",
                    modalTitle:   "Micro-live is not authorized.",
                    modalProse:   "ADR 0004 remains in force. This evidence can be reviewed, but the GUI cannot allocate live capital or start live order placement.",
                    consequenceActionLabel: "Locked"
                },
                {
                    strategyName: "BBands Lower-Band Mean Rev.",
                    strategyId:   "meanrev.daily.bbands_lowerband.curated_largecap.v1",
                    sharpe: "+0.52", maxDD: "13.7%", tradeCount: "361",
                    signalKind: "positive",
                    signalWord: "Gates pass",
                    proseTail:  "— paper evidence passes; capital stages locked.",
                    metaLine:   "day 71 · 2 open positions",
                    action: { variant: "ghost", label: "locked" },
                    modalKind: "consequence",
                    modalEyebrow: "Capital stages locked",
                    modalTitle:   "Micro-live is not authorized.",
                    modalProse:   "ADR 0004 remains in force. This evidence can be reviewed, but the GUI cannot allocate live capital or start live order placement.",
                    consequenceActionLabel: "Locked"
                },
                {
                    strategyName: "Time-Series Momentum",
                    strategyId:   "momentum.daily.tsmom.curated_largecap.v1",
                    sharpe: "+0.88", maxDD: "8.9%", tradeCount: "458",
                    signalKind: "positive",
                    signalWord: "Gates pass",
                    proseTail:  "— paper evidence passes; strongest run remains capital-locked.",
                    metaLine:   "day 92 · 5 open positions",
                    action: { variant: "ghost", label: "locked" },
                    modalKind: "consequence",
                    modalEyebrow: "Capital stages locked",
                    modalTitle:   "Micro-live is not authorized.",
                    modalProse:   "ADR 0004 remains in force. This evidence can be reviewed, but the GUI cannot allocate live capital or start live order placement.",
                    consequenceActionLabel: "Locked"
                },
                {
                    strategyName: "NR7 Inside-Day Breakout",
                    strategyId:   "breakout.daily.nr7_inside.liquid_largecap.v1",
                    sharpe: "+0.31", maxDD: "14.8%", tradeCount: "218",
                    signalKind: "warning",
                    signalWord: "Sharpe 0.31 below 0.50",
                    proseTail:  "— gate failing.",
                    metaLine:   "day 44 · 1 open position · candidate for reparam",
                    action: { variant: "secondary", label: "View evidence →" },
                    modalKind: "blocked",
                    modalEyebrow: "Cannot promote yet",
                    modalTitle:   "NR7 Inside-Day Breakout fails one gate.",
                    modalProse:   "Promotion from paper to micro-live requires the strategy to clear three gates. This config clears two — but its Sharpe ratio sits below the floor.",
                    gateRows: [
                        { name: "Sharpe ratio", current: "+0.31", required: "≥ 0.50",  passes: false },
                        { name: "Max drawdown", current: "14.8%", required: "≤ 15.0%", passes: true  },
                        { name: "Trade count",  current: "218",   required: "≥ 30",    passes: true  }
                    ],
                    remediation: [
                        "Reparam the config and re-run paper to target a higher Sharpe.",
                        "Continue paper longer if Sharpe is trending up — the current sample may not be stable.",
                        "Open the strategy detail to inspect Sharpe distribution per walk-forward window."
                    ]
                }
            ]
        },
        {
            stage: "micro_live",
            stageRoman: "iv.",
            stageName: "Micro live",
            stageCaption: "live capital, capped sizing",
            strategies: [
                {
                    strategyName: "RSI-2 Pullback",
                    strategyId:   "meanrev.daily.pullback_rsi2.curated_largecap.v1",
                    sharpe: "+0.73", maxDD: "9.1%", tradeCount: "776",
                    signalKind: "positive",
                    signalWord: "Sharpe holds",
                    proseTail:  "— capital stages remain locked by ADR 0004.",
                    metaLine:   "paper evidence · 4 open · live locked",
                    action: { variant: "ghost", label: "locked" },
                    modalKind: "typed",
                    modalEyebrow: "Live boundary locked",
                    modalTitle:   "Live trading is not authorized.",
                    modalProse:   "ADR 0004 remains in force. A future ADR must explicitly open micro-live or live before any promotion control becomes actionable.",
                    confirmPhrase: "",
                    confirmActionLabel: "LOCKED"
                },
                {
                    strategyName: "Cross-Sectional Sector Rotation",
                    strategyId:   "momentum.daily.xsec_rotation.sector_etfs.v1",
                    sharpe: "+0.92", maxDD: "7.8%", tradeCount: "144",
                    signalKind: "info",
                    signalWord: "Cooking",
                    proseTail:  "— more paper evidence required; capital stages locked.",
                    metaLine:   "paper evidence · 2 open · live locked",
                    action: { variant: "ghost", label: "no action" },
                    modalKind: ""
                }
            ]
        },
        {
            stage: "live",
            stageRoman: "v.",
            stageName: "Live",
            stageCaption: "full attribution, real capital",
            strategies: [
                {
                    strategyName: "SPY/SHY Regime Rotation",
                    strategyId:   "regime.daily.sma200_rotation.spy_shy.v1",
                    sharpe: "+1.19", maxDD: "6.4%", tradeCount: "27",
                    signalKind: "positive",
                    signalWord: "Lifecycle exempt.",
                    proseTail:  "Currently holding SPY.",
                    metaLine:   "1 open · $48k deployed · live since 2026-01-14",
                    action: { variant: "secondary", label: "Open detail →" },
                    modalKind: "consequence",
                    modalEyebrow: "Open strategy detail",
                    modalTitle:   "SPY/SHY Regime Rotation — full attribution.",
                    modalProse:   "(Strategy detail surface lands in a separate PR. This placeholder confirms the row's Open-detail action wires correctly.)",
                    consequenceActionLabel: "OK"
                }
            ]
        }
    ]

    */

    // ------------------------------------------------------------------
    // Active modal state
    // ------------------------------------------------------------------

    // null when no modal showing.  Otherwise: the strategy data object
    // whose action button was clicked.  modalKind on the data drives
    // which modal renders.
    property var activeStrategy: null

    function openModal(strategy) {
        root.activeStrategy = strategy
    }

    function dismissModal() {
        root.activeStrategy = null
    }

    // ------------------------------------------------------------------
    // Background fill for the surface (canvas)
    // ------------------------------------------------------------------

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.canvas
    }

    // ------------------------------------------------------------------
    // Scrollable surface body
    // ------------------------------------------------------------------

    Flickable {
        id: scroller
        anchors.fill: parent
        contentWidth:  width
        contentHeight: pageColumn.implicitHeight + Theme.space[7] * 2
        clip:          true
        flickableDirection: Flickable.VerticalFlick

        Column {
            id: pageColumn
            width: scroller.width
            padding: Theme.space[7]
            spacing: Theme.space[6]

            // ==========================================================
            // Page header
            // ==========================================================
            Item {
                width:  parent.width - Theme.space[7] * 2
                height: headerLayout.implicitHeight

                Column {
                    id: headerLayout
                    width:   parent.width
                    spacing: Theme.space[3]

                    // Eyebrow
                    Text {
                        text: "Milodex · Strategy Bench"
                        color: Theme.color.text.muted
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }

                    // Headline — Newsreader display.lg, title case + oxblood period
                    Row {
                        spacing: 0
                        Text {
                            text: "The Strategy Bench"
                            color: Theme.color.brand.primary
                            font.family:    Theme.typography.display.lg.family
                            font.pixelSize: Theme.typography.display.lg.size + 8
                            font.weight:    Theme.typography.display.lg.weight
                            font.letterSpacing: -0.6
                        }
                        Text {
                            text:  "."
                            color: Theme.color.brand.accent
                            font.family:    Theme.typography.display.lg.family
                            font.pixelSize: Theme.typography.display.lg.size + 8
                            font.weight:    Theme.typography.display.lg.weight
                        }
                    }

                    // Standfirst — italic Newsreader
                    Text {
                        width: parent.width * 0.7
                        text: "Every config on the ladder, top to bottom — what's working, what's stuck, and what's waiting at the next gate. Phase 6 Kanban will add governed movement; this surface remains view-only."
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.md.size + 1
                        font.italic:    true
                        wrapMode:       Text.WordWrap
                    }
                }
            }

            // Top hairline
            Rectangle {
                width: parent.width - Theme.space[7] * 2
                height: 1
                color: Theme.color.border.regular
            }

            // ==========================================================
            // Section repeater — one Section per stage in benchData
            // ==========================================================
            Repeater {
                model: root.benchData
                delegate: Column {
                    width:   parent.width - Theme.space[7] * 2
                    spacing: 0
                    bottomPadding: Theme.space[5]

                    // Bind the model object so child elements can reach modelData
                    property var sectionData: modelData

                    // -- Section header --
                    Item {
                        width:  parent.width
                        height: sectionHeaderRow.implicitHeight

                        RowLayout {
                            id: sectionHeaderRow
                            anchors.left:  parent.left
                            anchors.right: parent.right
                            spacing: Theme.space[3]

                            // Roman numeral
                            Text {
                                text: sectionData.stageRoman
                                color: Theme.color.text.muted
                                font.family:    Theme.typography.display.sm.family
                                font.pixelSize: Theme.typography.display.sm.size + 1  // §9.1: token-anchored (display.sm=18)
                                font.italic:    true
                                Layout.preferredWidth: 20
                            }
                            // Stage name
                            Text {
                                text: sectionData.stageName
                                color: Theme.color.text.primary
                                font.family:        Theme.typography.body.md.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: 2.0
                                font.capitalization: Font.AllUppercase
                            }
                            // Mid-dot
                            Text {
                                text: "·"
                                color: Theme.color.text.muted
                                font.pixelSize: Theme.typography.body.md.size
                            }
                            // Stage count (zero-padded)
                            Text {
                                text: ("0" + sectionData.strategies.length).slice(-2)
                                color: Theme.color.text.muted
                                font.family:    Theme.typography.data.sm.family
                                font.pixelSize: Theme.typography.data.sm.size
                                font.features:  Theme.typography.data.sm.features
                                font.letterSpacing: 0.5
                            }
                            // Stretch
                            Item { Layout.fillWidth: true }
                            // Standfirst caption — right-aligned italic
                            Text {
                                text: sectionData.stageCaption
                                color: Theme.color.text.secondary
                                font.family:    Theme.typography.body.md.family
                                font.pixelSize: Theme.typography.body.md.size
                                font.italic:    true
                                horizontalAlignment: Text.AlignRight
                            }
                        }
                    }

                    // Spacer
                    Item { width: 1; height: Theme.space[3] }

                    // -- Section header rule (heavier 2px brand-accent for LIVE) --
                    Rectangle {
                        width:  parent.width
                        height: sectionData.stage === "live" ? 2 : 1
                        color:  sectionData.stage === "live" ? Theme.color.brand.accent
                                                             : Theme.color.border.regular
                    }

                    // -- Empty-state caption --
                    Item {
                        visible: sectionData.strategies.length === 0
                        width:   parent.width
                        height:  Theme.space[6]

                        Text {
                            anchors.centerIn: parent
                            text: "no strategies in this stage"
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.md.size
                            font.italic:    true
                        }
                    }

                    // -- Column header row (per section, between header and first row) --
                    Item {
                        visible: sectionData.strategies.length > 0
                        width:   parent.width
                        height:  Theme.space[5]

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin:  Theme.space[6]   // matches BenchRow padding
                            anchors.rightMargin: Theme.space[5]
                            spacing: Theme.space[4]

                            // Empty header above the strategy block + strategy block flex
                            Item { Layout.fillWidth: true; Layout.preferredWidth: 1 }
                            Text {
                                text: "Sharpe"
                                color: Theme.color.text.secondary
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                font.capitalization: Font.AllUppercase
                                horizontalAlignment: Text.AlignRight
                                Layout.preferredWidth: Theme.column.benchMetric
                            }
                            Text {
                                text: "max-dd"
                                color: Theme.color.text.secondary
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                font.capitalization: Font.AllUppercase
                                horizontalAlignment: Text.AlignRight
                                Layout.preferredWidth: Theme.column.benchMetric
                            }
                            Text {
                                text: "trades"
                                color: Theme.color.text.secondary
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                font.capitalization: Font.AllUppercase
                                horizontalAlignment: Text.AlignRight
                                Layout.preferredWidth: Theme.column.benchMetric
                            }
                            Text {
                                text: "config"
                                color: Theme.color.text.secondary
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                font.capitalization: Font.AllUppercase
                                Layout.preferredWidth: Theme.column.benchConfigKey
                            }
                            Text {
                                text: "stage"
                                color: Theme.color.text.secondary
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                font.capitalization: Font.AllUppercase
                                Layout.preferredWidth: Theme.column.benchStage
                            }
                            Text {
                                text: "evidence"
                                color: Theme.color.text.secondary
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size
                                font.weight:        Font.DemiBold
                                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                font.capitalization: Font.AllUppercase
                                Layout.preferredWidth: Theme.column.benchEvidence
                            }
                            Item { Layout.preferredWidth: Theme.column.benchAction }
                        }
                    }

                    // -- Strategies in this section --
                    Repeater {
                        model: sectionData.strategies
                        delegate: BenchRow {
                            width: pageColumn.width - Theme.space[7] * 2
                            strategyName: modelData.name || modelData.strategyName
                            strategyId:   modelData.strategyId
                            stage:        sectionData.stage
                            sharpe:       modelData.sharpe === undefined || modelData.sharpe === null ? "—" : ("+" + Number(modelData.sharpe).toFixed(2)).replace("+-", "-")
                            maxDD:        modelData.maxDrawdownPct === undefined || modelData.maxDrawdownPct === null ? "—" : Number(modelData.maxDrawdownPct).toFixed(1) + "%"
                            tradeCount:   modelData.tradeCount === 0 ? "—" : "" + modelData.tradeCount
                            signalKind:   modelData.statusKind || modelData.signalKind
                            signalWord:   modelData.statusWord || modelData.signalWord
                            proseTail:    modelData.statusTail || modelData.proseTail
                            metaLine:     modelData.metaLine
                            metaConfigKey: modelData.metaConfigKey || ""
                            metaStage:     modelData.metaStage || sectionData.stage
                            metaEvidence:  ((modelData.metaEvidenceLabel || "") + " " + (modelData.metaEvidenceAt || "")).trim()
                            actionVariant: "secondary"
                            actionLabel:   "View evidence →"
                            onActionClicked: root.openModal(modelData)
                        }
                    }
                }
            }

            // ==========================================================
            // Footer
            // ==========================================================
            Rectangle {
                width: parent.width - Theme.space[7] * 2
                height: 1
                color: Theme.color.border.regular
            }
            Item {
                width:  parent.width - Theme.space[7] * 2
                height: footerCaption.implicitHeight

                Text {
                    id: footerCaption
                    width:   parent.width * 0.75
                    text: "Promotion to paper requires walk-forward gate-pass on every window. Promotion to micro-live requires Sharpe ≥ 0.50, max-dd ≤ 15%, n ≥ 30. Promotion to live requires explicit typed confirmation and a recorded human decision per ADR 0005."
                    color:   Theme.color.text.muted
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.sm.size
                    font.italic:    true
                    wrapMode:       Text.WordWrap
                }
            }
        }
    }

    // ==================================================================
    // MODAL OVERLAYS — three flavors, conditionally visible by modalKind
    // ==================================================================

    // -- Phase 5 read-only evidence/detail view
    Loader {
        anchors.fill: parent
        active: root.activeStrategy !== null
                && (!root.activeStrategy.modalKind || root.activeStrategy.modalKind === "")
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
                          + (root.activeStrategy.statusTail || "No write actions are exposed in Phase 5."))
                       : ""
            onDismissed: root.dismissModal()

            Text {
                width: parent.width
                text: root.activeStrategy
                      ? ("Stage " + root.activeStrategy.stage
                         + " | Sharpe " + (root.activeStrategy.sharpe === undefined || root.activeStrategy.sharpe === null ? "—" : Number(root.activeStrategy.sharpe).toFixed(2))
                         + " | Max-dd " + (root.activeStrategy.maxDrawdownPct === undefined || root.activeStrategy.maxDrawdownPct === null ? "—" : Number(root.activeStrategy.maxDrawdownPct).toFixed(1) + "%")
                         + " | Trades " + (root.activeStrategy.tradeCount || 0))
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

    // -- Pattern A: Blocked promotion (gate-failure)
    Loader {
        anchors.fill: parent
        active: root.activeStrategy !== null && root.activeStrategy.modalKind === "blocked"
        sourceComponent: blockedComponent
        z: 100
    }
    Component {
        id: blockedComponent
        BenchModal {
            anchors.fill: parent
            topBorderColor: Theme.status.negative
            eyebrowText:    root.activeStrategy ? root.activeStrategy.modalEyebrow : ""
            eyebrowColor:   Theme.status.negative
            titleText:      root.activeStrategy ? root.activeStrategy.modalTitle   : ""
            proseText:      root.activeStrategy ? root.activeStrategy.modalProse   : ""
            onDismissed:    root.dismissModal()

            GateTable {
                width: parent.width
                rows:  root.activeStrategy ? (root.activeStrategy.gateRows || []) : []
            }

            Column {
                width: parent.width
                spacing: Theme.space[2]

                Text {
                    text: "What unblocks this"
                    color: Theme.color.text.muted
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                Repeater {
                    model: root.activeStrategy ? (root.activeStrategy.remediation || []) : []
                    delegate: Row {
                        width: parent.width
                        spacing: Theme.space[2]

                        Text {
                            text: "·"
                            color: Theme.status.negative
                            font.pixelSize: Theme.typography.display.sm.size  // §9.1: display.sm=18
                            font.bold: true
                            anchors.top: parent.top
                            anchors.topMargin: -2
                        }
                        Text {
                            width: parent.parent.width - Theme.space[3]
                            text: modelData
                            color: Theme.color.text.secondary
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.md.size + 1
                            font.italic:    true
                            wrapMode:       Text.WordWrap
                        }
                    }
                }
            }

            // Action footer
            actionContent: [
                Button {
                    variant: "secondary"
                    text:    "Open evidence →"
                    onClicked: root.dismissModal()  // PR1: stub — opens nothing
                },
                Button {
                    variant: "ghost"
                    text:    "Cancel"
                    onClicked: root.dismissModal()
                }
            ]
        }
    }

    // -- Pattern B: Typed-confirm to live (oxblood top-border)
    Loader {
        anchors.fill: parent
        active: root.activeStrategy !== null && root.activeStrategy.modalKind === "typed"
        sourceComponent: typedComponent
        z: 100
    }
    Component {
        id: typedComponent
        BenchModal {
            id: typedModal
            anchors.fill: parent
            topBorderColor: Theme.color.brand.accent  // oxblood — brand-level commitment
            eyebrowText:    root.activeStrategy ? root.activeStrategy.modalEyebrow : ""
            titleText:      root.activeStrategy ? root.activeStrategy.modalTitle   : ""
            proseText:      root.activeStrategy ? root.activeStrategy.modalProse   : ""
            onDismissed:    root.dismissModal()

            // Required-phrase TextInput
            Item {
                width:  parent.width
                height: 64

                Column {
                    width: parent.width
                    spacing: Theme.space[2]

                    Text {
                        text: "Type \"" + (root.activeStrategy ? root.activeStrategy.confirmPhrase : "") + "\" to enable the action."
                        color: Theme.color.text.muted
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }

                    Rectangle {
                        width:  parent.width
                        height: 38
                        color:  Theme.color.surface.canvas
                        border.color: confirmInput.activeFocus ? Theme.color.brand.accent
                                                               : Theme.color.border.emphasis
                        border.width: 1
                        radius: Theme.radius.sm

                        TextInput {
                            id: confirmInput
                            anchors.fill: parent
                            anchors.leftMargin:  Theme.space[3]
                            anchors.rightMargin: Theme.space[3]
                            verticalAlignment:   TextInput.AlignVCenter
                            color: Theme.color.text.primary
                            font.family:        Theme.typography.data.md.family
                            font.pixelSize:     Theme.typography.data.md.size
                            font.letterSpacing: 1.0
                            selectByMouse: true
                            focus: true
                        }
                    }
                }
            }

            // Action footer — critical button enabled only on phrase match
            actionContent: [
                Button {
                    variant: "ghost"
                    text:    "Cancel"
                    onClicked: root.dismissModal()
                },
                Button {
                    variant: "critical"
                    text:    root.activeStrategy ? root.activeStrategy.confirmActionLabel : ""
                    enabled: confirmInput.text === (root.activeStrategy ? root.activeStrategy.confirmPhrase : "__")
                    onClicked: root.dismissModal()  // PR1: stub
                }
            ]
        }
    }

    // -- Pattern C: Consequence-confirm (rust top-border)
    Loader {
        anchors.fill: parent
        active: root.activeStrategy !== null && root.activeStrategy.modalKind === "consequence"
        sourceComponent: consequenceComponent
        z: 100
    }
    Component {
        id: consequenceComponent
        BenchModal {
            anchors.fill: parent
            topBorderColor: Theme.status.negative
            eyebrowText:    root.activeStrategy ? root.activeStrategy.modalEyebrow : ""
            titleText:      root.activeStrategy ? root.activeStrategy.modalTitle   : ""
            proseText:      root.activeStrategy ? root.activeStrategy.modalProse   : ""
            onDismissed:    root.dismissModal()

            actionContent: [
                Button {
                    variant: "ghost"
                    text:    "Cancel"
                    onClicked: root.dismissModal()
                },
                Button {
                    variant: "primary"
                    text:    root.activeStrategy ? root.activeStrategy.consequenceActionLabel : ""
                    onClicked: root.dismissModal()  // PR1: stub
                }
            ]
        }
    }
}

// DeskSurface.qml — The Trading Desk (dense cockpit view).
//
// Newspaper front page: a hero band with three cells (Session, P/L,
// Market regime) and a 3-column body with eight sections (A through H)
// covering the strategy ladder, system & risk, promotion queue, runners,
// event ticker, today's tape, sector heat, and the day's calendar.
//
// Editorial-print conventions throughout: A./B./C. section labels (cap
// letter + period), italic standfirsts, em-dashes, hairline rules,
// tabular numerals, mono numerics with `tnum`.  No iconography — color
// + language carry the signal.
//
// PR1: mock data inline.  Real data wiring (per the OperationalState +
// StrategyBankState pattern) lands in a follow-up PR.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    property real captureContentHeight: scroller.contentHeight

    readonly property var liveSnapshot: DeskState.snapshot || ({})
    readonly property var deskPnl: liveSnapshot.pnl || ({ today: 0, todayPct: 0, sparkline: [0] })
    readonly property var deskSystem: liveSnapshot.system || ({})
    readonly property var deskMarket: liveSnapshot.market || ({ regime: "UNKNOWN", regimeNote: "Market tape not wired yet.", tape: [] })
    readonly property bool marketKnown: (deskMarket.regime || "UNKNOWN") !== "UNKNOWN"
    readonly property var tapeRows: (deskMarket.tape && deskMarket.tape.length > 0) ? deskMarket.tape : []
    readonly property var queueRows: (liveSnapshot.promotionQueue && liveSnapshot.promotionQueue.length > 0) ? liveSnapshot.promotionQueue : []
    readonly property var runnerRows: (liveSnapshot.runners && liveSnapshot.runners.length > 0) ? liveSnapshot.runners : []
    readonly property var eventRows: (liveSnapshot.events && liveSnapshot.events.length > 0) ? liveSnapshot.events : []
    readonly property var stageRows: liveSnapshot.stageRows || []
    readonly property var stageCounts: liveSnapshot.stageCounts || ({ idle: 0, backtest: 0, paper: 0, micro_live: 0, live: 0 })
    readonly property int idleCount: Number(stageCounts.idle || 0)
    readonly property int backtestCount: Number(stageCounts.backtest || 0)
    readonly property int paperCount: Number(stageCounts.paper || 0)
    readonly property int microLiveCount: Number(stageCounts.micro_live || 0)
    readonly property int liveCount: Number(stageCounts.live || 0)
    readonly property int strategyTotal: liveSnapshot.strategyTotal || 0
    readonly property string brokerLine: OperationalState.brokerStatus === "connected" ? "Broker connected"
                                     : OperationalState.brokerStatus === "error" ? "Broker error"
                                     : "Broker stale"
    readonly property string marketLine: OperationalState.marketOpen ? "Market open" : "Market closed"
    readonly property string riskLine: OperationalState.killSwitchActive ? "Kill switch fired" : "Guard ready"
    readonly property color riskColor: OperationalState.killSwitchActive ? Theme.status.negative : Theme.status.positive

    function formatMoney(value) {
        var n = Number(value || 0)
        var sign = n < 0 ? "-" : ""
        var abs = Math.abs(n)
        return sign + "$" + abs.toLocaleString(Qt.locale("en_US"), "f", 2)
    }

    function formatPct(value) {
        var n = Number(value || 0)
        var sign = n > 0 ? "+" : ""
        return sign + n.toFixed(2) + "%"
    }

    function stageFill(stage) {
        if (root.strategyTotal <= 0) return 0
        return Math.max(0, Math.min(1, Number(root.stageCounts[stage] || 0) / root.strategyTotal))
    }

    function eventDetailText(modelData) {
        var detail = modelData.reason || modelData.body || ""
        if (detail.length <= 150) return detail
        return detail.slice(0, 147) + "..."
    }

    // ------------------------------------------------------------------
    // Mock data — mirrors dashboard-data.js shape
    // ------------------------------------------------------------------

    readonly property var session: ({
        state: "open",
        hoursIn: 3, minsIn: 2,
        hoursLeft: 3, minsLeft: 28,
        weatherLine: "Friday, May 8 — a quiet first hour, breadth firming through the lunch ladle."
    })

    readonly property var positions: ({
        open: 11, longs: 7, shorts: 4, gross: 71, net: 18  // pct
    })

    readonly property var pnl: ({
        today: "1,247.36", todayPct: "+0.42",
        mtd: "+$4,892.18", ytd: "−$12,104.55",
        realized: "$612.40", unrealized: "$634.96",
        sparkline: [0, 120, 80, 240, 180, 90, 60, 220, 380, 290, 410, 580,
                    720, 690, 840, 920, 1010, 1140, 1080, 1180, 1247.36]
    })

    readonly property var market: ({
        regime: "RISK-ON",
        regimeNote: "SPY > SMA200, breadth firm, VIX easy.",
        spy: "562.18",      spyDelta: "+0.33%",
        qqqDelta: "+0.64%", iwmDelta: "−0.22%",
        vix: "13.84",       tenY: "4.31%"
    })

    readonly property var ladder: [
        { tick: "i.",   name: "IDLE",       deck: "awaiting first run",   count: 2, running: 0, fillPct: 0.00 },
        { tick: "ii.",  name: "BACKTEST",   deck: "historical evidence",  count: 3, running: 2, fillPct: 0.67 },
        { tick: "iii.", name: "PAPER",      deck: "live feed, no capital", count: 4, running: 3, fillPct: 0.75 },
        { tick: "iv.",  name: "MICRO LIVE", deck: "live capital, capped", count: 2, running: 2, fillPct: 1.00 },
        { tick: "v.",   name: "LIVE",       deck: "full attribution",     count: 1, running: 1, fillPct: 1.00 }
    ]

    readonly property var system: ({
        uptime: "7d 14h", feedLatency: "38 ms",
        cpu: 0.34, mem: 0.58,
        capitalDeployed: "$184k", capitalTotal: "of 300k",
        drawdown: "−3.4", drawdownNote: "% from MTD high",
        var95: "$2,140", concentration: "18", concentrationNote: "% top name"
    })

    readonly property var promotionQueue: [
        { name: "ATR Channel Breakout",        from: "paper",    to: "micro", days: "3d", note: "gates passing — capital stages locked" },
        { name: "Time-Series Momentum",        from: "paper",    to: "micro", days: "8d", note: "gates passing — capital stages locked" },
        { name: "BBands Lower-Band Mean Rev.", from: "paper",    to: "micro", days: "1d", note: "gates passing — capital stages locked" },
        { name: "Donchian 20/10",              from: "backtest", to: "paper", days: "1d", note: "walk-forward complete" },
        { name: "RSI-2 Pullback",              from: "micro",    to: "live",  days: "5d", note: "locked by ADR 0004" }
    ]

    readonly property var runners: [
        { pid: 81204, name: "Donchian 20/10",            detail: "walk-forward 87%", state: "running" },
        { pid: 81211, name: "52-Week High Proximity",    detail: "in-sample 41%",    state: "running" },
        { pid: 81012, name: "ATR Channel Breakout",      detail: "day 88",           state: "running" },
        { pid: 80877, name: "Time-Series Momentum",      detail: "day 92",           state: "running" },
        { pid: 80831, name: "BBands Mean Reversion",     detail: "day 71",           state: "running" },
        { pid: 80790, name: "NR7 Inside-Day Breakout",   detail: "awaiting reparam", state: "paused"  },
        { pid: 80541, name: "RSI-2 Pullback",            detail: "day 62 · 4 open",  state: "running" },
        { pid: 80540, name: "XSec Sector Rotation",      detail: "session boundary", state: "paused"  },
        { pid: 80112, name: "SPY/SHY Regime Rotation",   detail: "day 184 · 1 open", state: "running" }
    ]

    readonly property var events: [
        { ts: "14:31:08", kind: "TRADE", body: "RSI-2 Pullback bought NVDA · 14 sh @ 1107.42",          src: "micro" },
        { ts: "14:28:51", kind: "INFO",  body: "Walk-forward fold 7/8 complete · Donchian 20/10",        src: "backtest" },
        { ts: "14:21:19", kind: "RUN",   body: "SPY/SHY Regime Rotation rebalance check — held SPY",     src: "live" },
        { ts: "14:15:02", kind: "WARN",  body: "Concentration on AAPL approaching 18% gate",             src: "risk" },
        { ts: "14:08:44", kind: "TRADE", body: "SPY/SHY Regime Rotation sold COKE · 22 sh @ 71.30",      src: "live" },
        { ts: "13:42:00", kind: "RUN",   body: "NR7 Inside-Day Breakout paused — sharpe below threshold", src: "paper" }
    ]

    readonly property var tape: [
        { sym: "SPY",  name: "S&P 500",         last: "562.18", pct: "+0.33%", up: true  },
        { sym: "QQQ",  name: "Nasdaq 100",      last: "491.07", pct: "+0.64%", up: true  },
        { sym: "IWM",  name: "Russell 2000",    last: "218.44", pct: "−0.22%", up: false },
        { sym: "DIA",  name: "Dow 30",          last: "432.01", pct: "+0.18%", up: true  },
        { sym: "EFA",  name: "Developed ex-US", last:  "86.55", pct: "+0.42%", up: true  },
        { sym: "EEM",  name: "Emerging mkts",   last:  "47.10", pct: "−0.31%", up: false },
        { sym: "TLT",  name: "20yr Treasuries", last:  "88.92", pct: "−0.78%", up: false },
        { sym: "GLD",  name: "Gold",            last: "232.40", pct: "+0.55%", up: true  },
        { sym: "VIX",  name: "Volatility idx",  last:  "13.84", pct: "−3.21%", up: false }
    ]

    readonly property var sectors: [
        { code: "XLK",  pct: "+0.92%", up: true  },
        { code: "XLY",  pct: "+0.61%", up: true  },
        { code: "XLC",  pct: "+0.48%", up: true  },
        { code: "XLI",  pct: "+0.21%", up: true  },
        { code: "XLF",  pct: "+0.04%", up: true  },
        { code: "XLV",  pct: "−0.12%", up: false },
        { code: "XLB",  pct: "−0.34%", up: false },
        { code: "XLP",  pct: "−0.41%", up: false },
        { code: "XLRE", pct: "−0.55%", up: false },
        { code: "XLU",  pct: "−0.68%", up: false },
        { code: "XLE",  pct: "−0.83%", up: false }
    ]

    readonly property var calendar: [
        { time: "08:30",     what: "Initial Jobless Claims",     result: "218k vs. 220k est.",    imp: "MED",  done: true  },
        { time: "10:00",     what: "Wholesale Inventories",      result: "+0.1% vs. flat",        imp: "LOW",  done: true  },
        { time: "13:00",     what: "30Y Treasury Auction",       result: "tail 0.4 bp",           imp: "MED",  done: true  },
        { time: "14:00",     what: "FOMC speakers — Williams",   result: "in progress",           imp: "MED",  done: false },
        { time: "16:30",     what: "Fed Balance Sheet (H.4.1)",  result: "—",                     imp: "LOW",  done: false },
        { time: "Tomorrow",  what: "Core PPI (Apr)",             result: "cons. +0.2% m/m",       imp: "HIGH", done: false }
    ]

    // ------------------------------------------------------------------
    // Background
    // ------------------------------------------------------------------

    Rectangle { anchors.fill: parent; color: Theme.color.surface.canvas }

    // ------------------------------------------------------------------
    // Reusable inline components
    // ------------------------------------------------------------------

    // Lettered section header per DESIGN_SYSTEM.md v0.2 §7.9.
    //
    // Letter (A. / B. / C. …) in display-serif Newsreader, parchment
    // (color.brand.primary), weight 500 — not italic, not muted.
    // Section name in label.xs tracked uppercase, text.secondary.
    // Optional right-aligned meta in data.sm mono with tnum, text.muted —
    // baseline-aligned with the letter.
    // Full-width 1px border.subtle hairline rule below the header,
    // space[2] (8px) below the header bottom edge per §7.9 spacing
    // contract. Section content begins below the rule with the parent
    // Column's spacing.
    //
    // Replaces the v0.1 pattern (text.muted italic 14px letter, no
    // hairline rule, separately-anchored right-meta Text per call site).
    component SectionLabel: Column {
        id: sectionLabelRoot
        property string letter: ""
        property string name: ""
        property string meta: ""
        width: parent ? parent.width : 0
        spacing: Theme.space[2]

        Item {
            width: parent.width
            implicitHeight: letterText.implicitHeight

            Text {
                id: letterText
                anchors.left: parent.left
                anchors.top:  parent.top
                text:  sectionLabelRoot.letter
                color: Theme.color.brand.primary
                font.family:    Theme.typography.display.sm.family
                font.pixelSize: Theme.typography.display.sm.size
                font.weight:    Theme.typography.display.sm.weight
            }

            Text {
                id: nameText
                anchors.left:       letterText.right
                anchors.leftMargin: Theme.space[3]
                anchors.baseline:   letterText.baseline
                text:  sectionLabelRoot.name
                color: Theme.color.text.secondary
                font.family:         Theme.typography.label.xs.family
                font.pixelSize:      Theme.typography.label.xs.size
                font.weight:         Theme.typography.label.xs.weight
                font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                font.capitalization: Font.AllUppercase
            }

            Text {
                visible: sectionLabelRoot.meta !== ""
                text:    sectionLabelRoot.meta
                color:   Theme.color.text.muted
                font.family:    Theme.typography.data.sm.family
                font.pixelSize: Theme.typography.data.sm.size
                font.features:  Theme.typography.data.sm.features
                anchors.right:    parent.right
                anchors.baseline: letterText.baseline
            }
        }

        Rectangle {
            width:  parent.width
            height: 1
            color:  Theme.color.border.subtle
        }
    }

    // ------------------------------------------------------------------
    // Scroll container
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
            x: Theme.space[7]
            width: scroller.width - Theme.space[7] * 2
            topPadding: Theme.space[7]
            spacing: Theme.space[6]

            // ============================================================
            // PAGE HEADER
            // ============================================================
            Column {
                width: parent.width
                spacing: Theme.space[2]

                Text {
                    text: "Daily Dashboard · Detailed View"
                    color: Theme.color.text.muted
                    font.family:        Theme.typography.label.xs.family
                    font.pixelSize:     Theme.typography.label.xs.size
                    font.weight:        Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.space[5]

                    Row {
                        spacing: 0
                        Layout.fillWidth: false

                        Text {
                            text:  "The Trading Desk"
                            color: Theme.color.brand.primary
                            font.family:    Theme.typography.display.lg.family
                            font.pixelSize: Theme.typography.display.lg.size
                            font.weight:    Theme.typography.display.lg.weight
                            font.letterSpacing: -0.4
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
                        Layout.fillWidth: true
                        text: "the operator's working spread — risk posture, the strategy bench, and the wider market weather, all on one fold."
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.md.size + 1
                        font.italic:    true
                        wrapMode:       Text.WordWrap
                    }
                }
            }

            // Top hairline
            Rectangle { width: parent.width; height: 1; color: Theme.color.border.regular }

            // ============================================================
            // HERO BAND — three cells: Risk & Mode | P/L | Market
            // ============================================================
            RowLayout {
                width: parent.width
                spacing: Theme.space[6]

                // -- Cell 1: RISK & MODE --
                Column {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    spacing: Theme.space[2]

                    Row {
                        spacing: Theme.space[2]
                        Rectangle {
                            width: 6; height: 6; radius: 3
                            color: root.riskColor
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            text: "Risk & Mode"
                            color: Theme.color.text.secondary
                            font.family:        Theme.typography.label.xs.family
                            font.pixelSize:     Theme.typography.label.xs.size
                            font.weight:        Theme.typography.label.xs.weight
                            font.letterSpacing: Theme.typography.label.xs.letterSpacing
                            font.capitalization: Font.AllUppercase
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Text {
                        width: parent.width
                        text: root.riskLine
                        color: root.riskColor
                        font.family:    Theme.typography.display.md.family
                        font.pixelSize: Theme.typography.display.md.size
                        font.weight:    Theme.typography.display.md.weight
                        wrapMode: Text.WordWrap
                    }
                    Text {
                        width: parent.width
                        text: OperationalState.killSwitchActive
                              ? "Manual reset required. Trading halted."
                              : (OperationalState.tradingMode.toUpperCase() + " mode. " + root.marketLine + ".")
                        color: Theme.color.text.secondary
                        font.family: Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.italic: true
                        wrapMode: Text.WordWrap
                    }
                    // Mini-stats row
                    Row {
                        spacing: Theme.space[6]
                        Repeater {
                            model: [
                                { l: "Mode",   v: OperationalState.tradingMode.toUpperCase() },
                                { l: "Broker", v: OperationalState.brokerStatus.toUpperCase() },
                                { l: "Market", v: OperationalState.marketOpen ? "OPEN" : "CLOSED" }
                            ]
                            delegate: Column {
                                spacing: 2
                                Text {
                                    text: modelData.l
                                    color: Theme.color.text.muted
                                    font.family:        Theme.typography.label.xs.family
                                    font.pixelSize:     Theme.typography.label.xs.size
                                    font.weight:        Theme.typography.label.xs.weight
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                    font.capitalization: Font.AllUppercase
                                }
                                Text {
                                    text: modelData.v
                                    color: Theme.color.text.primary
                                    font.family:    Theme.typography.data.md.family
                                    font.pixelSize: Theme.typography.data.md.size + 2
                                    font.features:  Theme.typography.data.md.features
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    color: Theme.color.border.subtle
                }

                // -- Cell 2: P/L --
                Column {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 4
                    spacing: Theme.space[2]

                    Text {
                        text: "P/L · Today"
                        color: Theme.color.text.secondary
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }
                    // Hero P/L number
                    Row {
                        spacing: 0
                        Text {
                            text:  root.formatMoney(root.deskPnl.today)
                            color: Theme.color.brand.primary
                            font.family:    Theme.typography.display.xl.family
                            font.pixelSize: Theme.typography.display.xl.size
                            font.weight:    Theme.typography.display.xl.weight
                            font.letterSpacing: -1.0
                        }
                    }
                    // Sparkline
                    Sparkline {
                        width: parent.width
                        height: 60
                        series: root.deskPnl.sparkline
                        showAxis: false
                        showGrid: true
                        areaAlpha: 0.12
                    }
                    // Mini-stats row
                    Row {
                        spacing: Theme.space[5]
                        Repeater {
                            model: [
                                { l: "Day %", v: root.formatPct(root.deskPnl.todayPct), kind: Number(root.deskPnl.todayPct || 0) < 0 ? "neg" : "pos" },
                                { l: "Snapshot", v: root.liveSnapshot.lastRefreshedAt ? "ready" : "pending", kind: "dim" }
                            ]
                            delegate: Column {
                                spacing: 2
                                Text {
                                    text: modelData.l
                                    color: Theme.color.text.muted
                                    font.family:        Theme.typography.label.xs.family
                                    font.pixelSize:     Theme.typography.label.xs.size
                                    font.weight:        Theme.typography.label.xs.weight
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                    font.capitalization: Font.AllUppercase
                                }
                                Text {
                                    text: modelData.v
                                    color: modelData.kind === "pos" ? Theme.status.positive
                                          : modelData.kind === "neg" ? Theme.status.negative
                                          : Theme.color.text.muted
                                    font.family:    Theme.typography.data.md.family
                                    font.pixelSize: Theme.typography.data.md.size
                                    font.features:  Theme.typography.data.md.features
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    color: Theme.color.border.subtle
                }

                // -- Cell 3: MARKET --
                Column {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    spacing: Theme.space[2]

                    Text {
                        text: "Market · Regime"
                        color: Theme.color.text.secondary
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }

                    // Market status pill
                    Rectangle {
                        implicitWidth: regimeLabel.implicitWidth + Theme.space[3] * 2
                        implicitHeight: regimeLabel.implicitHeight + Theme.space[2]
                        color: Theme.color.surface.base
                        border.color: root.marketKnown ? Theme.status.info : Theme.color.border.regular
                        border.width: 1
                        radius: Theme.radius.sm

                        Row {
                            anchors.centerIn: parent
                            spacing: Theme.space[1]
                            Rectangle {
                                width: 6; height: 6; radius: 3
                                color: root.marketKnown ? Theme.status.info : Theme.status.warning
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                id: regimeLabel
                                text: root.marketKnown ? root.deskMarket.regime : "Not wired"
                                color: root.marketKnown ? Theme.status.info : Theme.status.warning
                                font.family:    Theme.typography.display.sm.family
                                font.pixelSize: Theme.typography.display.sm.size
                                font.weight:    Theme.typography.display.sm.weight
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }

                    Row {
                        visible: root.marketKnown
                        spacing: Theme.space[2]
                        Text {
                            text: Number(root.deskMarket.spyPct || 0).toFixed(2) + "%"
                            color: Theme.color.brand.primary
                            font.family:    Theme.typography.display.tally.family
                            font.pixelSize: Theme.typography.display.tally.size
                            font.weight:    Font.Medium
                            font.letterSpacing: -0.3
                        }
                        Text {
                            text: "SPY"
                            color: Theme.color.text.muted
                            font.family:        Theme.typography.label.xs.family
                            font.pixelSize:     Theme.typography.label.xs.size
                            font.weight:        Theme.typography.label.xs.weight
                            font.letterSpacing: Theme.typography.label.xs.letterSpacing
                            font.capitalization: Font.AllUppercase
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: 6
                        }
                        Text {
                            text: root.formatPct(root.deskMarket.spyPct)
                            color: Number(root.deskMarket.spyPct || 0) < 0 ? Theme.status.negative : Theme.status.positive
                            font.family:    Theme.typography.data.md.family
                            font.pixelSize: Theme.typography.data.md.size + 1
                            font.features:  Theme.typography.data.md.features
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: 6
                        }
                    }

                    Text {
                        text: root.marketKnown ? root.deskMarket.regimeNote : (root.deskMarket.weatherLine || root.deskMarket.regimeNote || "Market tape not wired yet.")
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.italic:    true
                    }

                    // Mini-stats row
                    Row {
                        spacing: Theme.space[6]
                        Repeater {
                            visible: root.marketKnown
                            model: [
                                { l: "SPY",  v: root.formatPct(root.deskMarket.spyPct), kind: Number(root.deskMarket.spyPct || 0) < 0 ? "neg" : "pos" },
                                { l: "QQQ",  v: root.formatPct(root.deskMarket.qqqPct), kind: Number(root.deskMarket.qqqPct || 0) < 0 ? "neg" : "pos" },
                                { l: "IWM",  v: root.formatPct(root.deskMarket.iwmPct), kind: Number(root.deskMarket.iwmPct || 0) < 0 ? "neg" : "pos" }
                            ]
                            delegate: Column {
                                spacing: 2
                                Text {
                                    text: modelData.l
                                    color: Theme.color.text.muted
                                    font.family:        Theme.typography.label.xs.family
                                    font.pixelSize:     Theme.typography.label.xs.size
                                    font.weight:        Theme.typography.label.xs.weight
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                    font.capitalization: Font.AllUppercase
                                }
                                Text {
                                    text: modelData.v
                                    color: modelData.kind === "pos" ? Theme.status.positive
                                          : modelData.kind === "neg" ? Theme.status.negative
                                          : Theme.color.text.primary
                                    font.family:    Theme.typography.data.md.family
                                    font.pixelSize: Theme.typography.data.md.size + 1
                                    font.features:  Theme.typography.data.md.features
                                }
                            }
                        }
                    }
                }
            }

            // Hairline below hero
            Rectangle { width: parent.width; height: 1; color: Theme.color.border.regular }

            // ============================================================
            // BODY — 3-column layout
            // ============================================================
            RowLayout {
                width: parent.width
                spacing: Theme.space[6]

                // ============= LEFT COLUMN =============
                Column {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[6]

                    // -- A. STRATEGY LADDER --
                    Column {
                        width: parent.width
                        spacing: Theme.space[3]

                        SectionLabel {
                            id: ladderHead
                            letter: "A."
                            name: "Strategy Ladder"
                            meta: root.strategyTotal + " configs"
                        }
                        Text {
                            text: "how the bench stacks today, by promotion stage"
                            color: Theme.color.text.secondary
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                        }

                        // Ladder rows
                        Column {
                            width: parent.width
                            spacing: Theme.space[3]

                            Repeater {
                                model: root.stageRows
                                delegate: Column {
                                    width: parent.width
                                    spacing: 4

                                    Item {
                                        width: parent.width
                                        height: 22

                                        Row {
                                            spacing: Theme.space[2]
                                            anchors.left: parent.left
                                            anchors.verticalCenter: parent.verticalCenter

                                            Text {
                                                text: modelData.tick
                                                color: Theme.color.text.muted
                                                font.family:    Theme.typography.display.sm.family
                                                font.pixelSize: Theme.typography.data.sm.size
                                                font.italic:    true
                                                width: 22
                                            }
                                            Text {
                                                text: modelData.name
                                                color: Theme.color.text.primary
                                                font.family:        Theme.typography.label.xs.family
                                                font.pixelSize:     Theme.typography.label.xs.size
                                                font.weight:        Font.DemiBold
                                                font.letterSpacing: 1.6
                                                font.capitalization: Font.AllUppercase
                                            }
                                            Text {
                                                text: "— " + modelData.deck
                                                color: Theme.color.text.secondary
                                                font.family: Theme.typography.body.md.family
                                                font.pixelSize: Theme.typography.body.sm.size
                                                font.italic: true
                                            }
                                        }
                                        Row {
                                            spacing: Theme.space[3]
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter

                                            Text {
                                                visible: modelData.running > 0
                                                text: modelData.running + " running"
                                                color: Theme.color.text.muted
                                                font.family:    Theme.typography.body.md.family
                                                font.pixelSize: Theme.typography.body.sm.size
                                                font.italic:    true
                                            }
                                            Text {
                                                visible: modelData.running === 0
                                                text: "idle"
                                                color: Theme.color.text.muted
                                                font.family: Theme.typography.body.md.family
                                                font.pixelSize: Theme.typography.body.sm.size
                                                font.italic: true
                                            }
                                            Text {
                                                text: ("0" + modelData.strategyCount).slice(-2)
                                                color: Theme.color.text.primary
                                                font.family:    Theme.typography.data.md.family
                                                font.pixelSize: Theme.typography.data.md.size
                                                font.features:  Theme.typography.data.md.features
                                            }
                                        }
                                    }

                                    // Stage fill bar
                                    Item {
                                        width: parent.width; height: 3
                                        Rectangle {
                                            anchors.fill: parent
                                            color: Theme.color.border.subtle
                                        }
                                        Rectangle {
                                            anchors.left:   parent.left
                                            anchors.top:    parent.top
                                            anchors.bottom: parent.bottom
                                            width: parent.width * modelData.fillPct
                                            color: modelData.fillPct >= 1.0 ? Theme.color.brand.accent
                                                  : modelData.fillPct > 0   ? Theme.color.brand.primary
                                                                            : "transparent"
                                            opacity: 0.55
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // -- B. SYSTEM & RISK --
                    Column {
                        width: parent.width
                        spacing: Theme.space[3]
                        topPadding: Theme.space[3]

                        SectionLabel { letter: "B."; name: "System & Risk" }
                        Text {
                            text: "resource ledger and the day's risk envelope"
                            color: Theme.color.text.secondary
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                        }

                        GridLayout {
                            width: parent.width
                            columns: 2
                            rowSpacing:    Theme.space[3]
                            columnSpacing: Theme.space[6]

                            Repeater {
                                model: [
                                    { l: "Risk Mode",    v: root.deskSystem.riskMode || OperationalState.tradingMode, u: "", available: true },
                                    { l: "Database",     v: root.deskSystem.dbPresent ? "present" : "not found", u: "", available: true },
                                    { l: "Feed Latency", v: root.deskSystem.feedLatency && root.deskSystem.feedLatency !== "n/a" ? root.deskSystem.feedLatency : "not wired", u: "", available: false },
                                    { l: "Capital Deployed", v: root.deskSystem.capitalDeployed && root.deskSystem.capitalDeployed !== "n/a" ? root.deskSystem.capitalDeployed : "not wired", u: "", available: false },
                                    { l: "Drawdown",     v: root.deskSystem.drawdown && root.deskSystem.drawdown !== "n/a" ? root.deskSystem.drawdown : "not wired", u: "", available: false }
                                ]
                                delegate: Column {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Text {
                                        text: modelData.l
                                        color: Theme.color.text.muted
                                        font.family:        Theme.typography.label.xs.family
                                        font.pixelSize:     Theme.typography.label.xs.size
                                        font.weight:        Theme.typography.label.xs.weight
                                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                        font.capitalization: Font.AllUppercase
                                    }
                                    Row {
                                        spacing: 4
                                        Text {
                                            text: modelData.v
                                            color: modelData.available
                                                   ? (modelData.l === "Drawdown" ? Theme.status.negative : Theme.color.text.primary)
                                                   : Theme.color.text.secondary
                                            font.family:    Theme.typography.data.md.family
                                            font.pixelSize: modelData.available ? Theme.typography.data.md.size + 1
                                                                                : Theme.typography.data.sm.size
                                            font.features:  Theme.typography.data.md.features
                                        }
                                        Text {
                                            visible: modelData.u !== ""
                                            text: modelData.u
                                            color: Theme.color.text.muted
                                            font.family:    Theme.typography.body.md.family
                                            font.pixelSize: Theme.typography.body.sm.size
                                            font.italic:    true
                                            anchors.bottom: parent.bottom
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    color: Theme.color.border.subtle
                }

                // ============= CENTER COLUMN =============
                Column {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 4
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[6]

                    // -- C. PROMOTION QUEUE --
                    Column {
                        width: parent.width
                        spacing: Theme.space[3]

                        SectionLabel {
                            id: pqHead
                            letter: "C."
                            name: "Promotion Queue"
                            meta: root.queueRows.length + " ready"
                        }
                        Text {
                            text: "strategies whose gates have passed and now wait at the next stage's door"
                            color: Theme.color.text.secondary
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                            wrapMode: Text.WordWrap
                            width: parent.width
                        }

                        Repeater {
                            model: root.queueRows
                            delegate: Column {
                                width: parent.width
                                bottomPadding: Theme.space[3]

                                Rectangle { width: parent.width; height: 1; color: Theme.color.border.subtle }

                                RowLayout {
                                    width: parent.width
                                    height: Math.max(queueText.implicitHeight, transitionRow.implicitHeight, readyText.implicitHeight) + Theme.space[4]
                                    spacing: Theme.space[3]

                                    Column {
                                        id: queueText
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 160
                                        Layout.alignment: Qt.AlignVCenter
                                        spacing: 2
                                        Text {
                                            width: parent.width
                                            text: modelData.name
                                            color: Theme.color.text.primary
                                            font.family:    Theme.typography.body.md.family
                                            font.pixelSize: Theme.typography.body.md.size + 1
                                            font.weight:    Font.Medium
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            width: parent.width
                                            text: modelData.note
                                            color: Theme.color.text.secondary
                                            font.family:    Theme.typography.body.md.family
                                            font.pixelSize: Theme.typography.body.sm.size
                                            font.italic:    true
                                            elide: Text.ElideRight
                                        }
                                    }

                                    Row {
                                        id: transitionRow
                                        Layout.preferredWidth: 120
                                        Layout.alignment: Qt.AlignVCenter
                                        spacing: Theme.space[1]
                                        Text {
                                            text: modelData.from
                                            color: Theme.color.text.secondary
                                            font.family:    Theme.typography.body.md.family
                                            font.pixelSize: Theme.typography.body.sm.size
                                            font.italic:    true
                                        }
                                        Text {
                                            text: "\u2192"
                                            color: Theme.color.text.muted
                                        }
                                        Text {
                                            text: modelData.to
                                            color: Theme.color.text.primary
                                            font.family:    Theme.typography.body.md.family
                                            font.pixelSize: Theme.typography.body.sm.size
                                            font.italic:    true
                                        }
                                    }

                                    Column {
                                        Layout.preferredWidth: 78
                                        Layout.alignment: Qt.AlignVCenter
                                        spacing: 2
                                        Text {
                                            id: readyText
                                            width: parent.width
                                            text: "ready " + modelData.days
                                            color: Theme.color.text.muted
                                            font.family:    Theme.typography.data.sm.family
                                            font.pixelSize: Theme.typography.data.sm.size
                                            font.features:  Theme.typography.data.sm.features
                                            horizontalAlignment: Text.AlignRight
                                        }
                                    }
                                }

                            }
                        }

                        Text {
                            visible: root.queueRows.length === 0
                            width: parent.width
                            text: "No strategies are waiting at the next gate."
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                            wrapMode: Text.WordWrap
                        }
                    }

                    // -- D. RUNNERS --
                    Column {
                        width: parent.width
                        spacing: Theme.space[3]

                        SectionLabel {
                            id: runHead
                            letter: "D."
                            name: "Runners"
                            meta: root.runnerRows.length + " observed"
                        }
                        Text {
                            text: "processes alive in the current session"
                            color: Theme.color.text.secondary
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                        }

                        Repeater {
                            model: root.runnerRows
                            delegate: Item {
                                width: parent.width
                                height: 22

                                Row {
                                    spacing: Theme.space[3]
                                    anchors.left: parent.left
                                    anchors.verticalCenter: parent.verticalCenter

                                    Text {
                                        text: modelData.pid
                                        color: Theme.color.text.muted
                                        font.family:    Theme.typography.data.sm.family
                                        font.pixelSize: Theme.typography.data.sm.size
                                        font.features:  Theme.typography.data.sm.features
                                        width: 50
                                        elide: Text.ElideRight
                                        clip: true
                                    }
                                    Text {
                                        text: modelData.name
                                        color: Theme.color.text.primary
                                        font.family:    Theme.typography.body.md.family
                                        font.pixelSize: Theme.typography.body.sm.size
                                        font.weight:    Font.Medium
                                    }
                                    Text {
                                        text: "· " + modelData.detail
                                        color: Theme.color.text.muted
                                        font.family:    Theme.typography.body.md.family
                                        font.pixelSize: Theme.typography.body.sm.size
                                        font.italic:    true
                                    }
                                }
                                Row {
                                    spacing: 4
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter

                                    Rectangle {
                                        width: 6; height: 6; radius: 3
                                        color: modelData.state === "running" ? Theme.status.positive : Theme.status.warning
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Text {
                                        text: modelData.state.toUpperCase()
                                        color: modelData.state === "running" ? Theme.status.positive : Theme.status.warning
                                        font.family:        Theme.typography.label.xs.family
                                        font.pixelSize:     Theme.typography.label.xs.size
                                        font.weight:        Font.DemiBold
                                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                    }
                                }
                            }
                        }

                        Text {
                            visible: root.runnerRows.length === 0
                            width: parent.width
                            text: "No strategy-run sessions are active in the event store."
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                            wrapMode: Text.WordWrap
                        }
                    }

                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    color: Theme.color.border.subtle
                }

                // ============= RIGHT COLUMN =============
                Column {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[6]

                    // -- F. TODAY'S TAPE --
                    Column {
                        width: parent.width
                        spacing: Theme.space[3]

                        SectionLabel { letter: "F."; name: "Today's Tape" }
                        Text {
                            text: root.tapeRows.length === 0
                                  ? "instrument status for the market-data feed"
                                  : "major indices, rates, vol — reading the wider weather"
                            color: Theme.color.text.secondary
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                            wrapMode: Text.WordWrap
                            width: parent.width
                        }

                        Repeater {
                            model: root.tapeRows
                            delegate: Item {
                                width: parent.width
                                height: 24

                                Row {
                                    anchors.fill: parent
                                    spacing: Theme.space[2]

                                    Text {
                                        text: modelData.sym
                                        color: Theme.color.text.primary
                                        font.family:    Theme.typography.data.md.family
                                        font.pixelSize: Theme.typography.data.md.size
                                        font.features:  Theme.typography.data.md.features
                                        font.weight:    Font.Medium
                                        width: 44
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Text {
                                        text: modelData.name
                                        color: Theme.color.text.muted
                                        font.family: Theme.typography.body.md.family
                                        font.pixelSize: Theme.typography.body.sm.size
                                        font.italic: true
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                                Row {
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: Theme.space[3]

                                    Text {
                                        text: modelData.last
                                        color: Theme.color.text.primary
                                        font.family:    Theme.typography.data.md.family
                                        font.pixelSize: Theme.typography.data.md.size
                                        font.features:  Theme.typography.data.md.features
                                    }
                                    Text {
                                        text: modelData.pct
                                        color: modelData.up ? Theme.status.positive : Theme.status.negative
                                        font.family:    Theme.typography.data.md.family
                                        font.pixelSize: Theme.typography.data.md.size
                                        font.features:  Theme.typography.data.md.features
                                        width: 60
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }
                            }
                        }

                        Column {
                            visible: root.tapeRows.length === 0
                            width: parent.width
                            spacing: Theme.space[2]

                            Repeater {
                                model: [
                                    { l: "Market tape", v: "awaits data-feed read model" },
                                    { l: "Portfolio snapshot", v: root.liveSnapshot.lastRefreshedAt ? "ready" : "pending" },
                                    { l: "Broker state", v: OperationalState.brokerStatus }
                                ]
                                delegate: RowLayout {
                                    width: parent.width
                                    spacing: Theme.space[3]

                                    Text {
                                        text: modelData.l
                                        color: Theme.color.text.secondary
                                        font.family:    Theme.typography.body.md.family
                                        font.pixelSize: Theme.typography.body.sm.size
                                        Layout.fillWidth: true
                                    }
                                    Text {
                                        text: modelData.v
                                        color: Theme.color.text.muted
                                        font.family:    Theme.typography.data.sm.family
                                        font.pixelSize: Theme.typography.data.sm.size
                                        font.features:  Theme.typography.data.sm.features
                                    }
                                }
                            }
                        }
                    }

                    // -- G. SECTOR HEAT --
                    Column {
                        width: parent.width
                        spacing: Theme.space[3]
                        topPadding: Theme.space[3]

                        SectionLabel { letter: "G."; name: "Sector Heat" }
                        Text {
                            width: parent.width
                            text: "Sector heatmap — not wired"
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                            wrapMode: Text.WordWrap
                        }
                    }

                    // -- H. TODAY'S CALENDAR --
                    Column {
                        width: parent.width
                        spacing: Theme.space[3]
                        topPadding: Theme.space[3]

                        SectionLabel { letter: "H."; name: "Today's Calendar" }
                        Text {
                            width: parent.width
                            text: "Economic calendar — not wired"
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            // ============================================================
            // FULL-WIDTH EVENT TICKER
            // ============================================================
            Column {
                width: parent.width
                spacing: Theme.space[3]

                Rectangle { width: parent.width; height: 1; color: Theme.color.border.regular }

                SectionLabel { letter: "E."; name: "Event Ticker" }
                Text {
                    text: "last hour's traffic — trades, runs, warnings"
                    color: Theme.color.text.secondary
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.sm.size
                    font.italic:    true
                }

                Repeater {
                    model: root.eventRows
                    delegate: RowLayout {
                        width: parent.width
                        height: Math.max(eventCopy.implicitHeight, kindBadge.implicitHeight, eventTime.implicitHeight) + Theme.space[2]
                        spacing: Theme.space[3]

                        Text {
                            id: eventTime
                            text: modelData.ts
                            color: Theme.color.text.secondary
                            font.family:    Theme.typography.data.sm.family
                            font.pixelSize: Theme.typography.data.sm.size
                            font.features:  Theme.typography.data.sm.features
                            Layout.preferredWidth: 64
                            Layout.alignment: Qt.AlignVCenter
                        }

                        Rectangle {
                            id: kindBadge
                            Layout.preferredWidth: kindLabelFull.implicitWidth + Theme.space[2]
                            Layout.preferredHeight: kindLabelFull.implicitHeight + 2
                            Layout.alignment: Qt.AlignVCenter
                            color: "transparent"
                            border.color: modelData.kind === "WARN"      ? Theme.status.warning
                                        : modelData.kind === "TRADE"     ? Theme.color.brand.accent
                                        : modelData.kind === "RUN"       ? Theme.status.info
                                        : modelData.kind === "PROMOTED"  ? Theme.status.positive
                                        : modelData.kind === "DEMOTED"   ? Theme.status.negative
                                        : modelData.kind === "TRIGGERED" ? Theme.status.negative
                                        : modelData.kind === "RESET"     ? Theme.status.info
                                        : Theme.color.border.regular
                            border.width: 1
                            radius: Theme.radius.sm

                            Text {
                                id: kindLabelFull
                                anchors.centerIn: parent
                                text: modelData.kind
                                color: modelData.kind === "WARN"      ? Theme.status.warning
                                     : modelData.kind === "TRADE"     ? Theme.color.brand.accent
                                     : modelData.kind === "RUN"       ? Theme.status.info
                                     : modelData.kind === "PROMOTED"  ? Theme.status.positive
                                     : modelData.kind === "DEMOTED"   ? Theme.status.negative
                                     : modelData.kind === "TRIGGERED" ? Theme.status.negative
                                     : modelData.kind === "RESET"     ? Theme.status.info
                                     : Theme.color.text.muted
                                font.family:        Theme.typography.label.xs.family
                                font.pixelSize:     Theme.typography.label.xs.size - 1
                                font.weight:        Font.DemiBold
                                font.letterSpacing: 0.6
                            }
                        }

                        Column {
                            id: eventCopy
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter
                            spacing: 2

                            Text {
                                width: parent.width
                                text: (modelData.subject || "") + " " + (modelData.transition || "")
                                color: Theme.color.text.primary
                                font.family:    Theme.typography.data.sm.family
                                font.pixelSize: Theme.typography.data.sm.size
                                font.features:  Theme.typography.data.sm.features
                                elide: Text.ElideRight
                            }
                            Text {
                                width: parent.width
                                text: root.eventDetailText(modelData)
                                color: Theme.color.text.secondary
                                font.family:    Theme.typography.body.md.family
                                font.pixelSize: Theme.typography.body.sm.size
                                wrapMode: Text.WordWrap
                                maximumLineCount: 1
                                elide: Text.ElideRight
                            }
                        }
                    }
                }

                Text {
                    visible: root.eventRows.length === 0
                    width: parent.width
                    text: "No material events recorded in the current desk snapshot."
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.sm.size
                    font.italic:    true
                    wrapMode: Text.WordWrap
                }
            }

            // Bottom padding
            Item { width: parent.width; height: Theme.space[7] }
        }
    }
}

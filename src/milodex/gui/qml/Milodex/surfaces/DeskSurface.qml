// DeskSurface.qml — The Trading Desk (7-section live cockpit).
//
// Spec: docs/superpowers/specs/2026-05-16-trading-desk-redesign-design.md §5.
// IA: header band → Row 1 (I Risk&Mode · II Performance&Trust · III Active
// Ops) → hairline → Row 2 (IV Risk Throughput · V Strategy Attention · VI
// Market Tape) → hairline → VII Order/Signal Tape (full-width).
//
// Binding model (spec §3 IA→read-model map; QML binds Q_PROPERTYs only,
// never queries):
//   I   Risk & Mode          ← OperationalState (+ DB-present indicator)
//   II  Performance & Trust   ← PerformanceState; "Today" P/L from
//                               OperationalState.dailyPnl; stale treatment
//   III Active Operations     ← ActiveOpsState
//   IV  Risk Layer Throughput ← RiskThroughputState
//   V   Strategy Attention    ← AttentionState
//   VI  Market Tape           ← MarketTapeState (timestamp-only)
//   VII Order / Signal Tape   ← ActivityFeedState (client-side filter)
//
// Slice toggles (II, IV) are pure client-side indices into the precomputed
// bySlice maps — toggling never triggers a re-query.
//
// Animation discipline (locked): state changes instant; P&L figures never
// crossfade; no idle animation. Chrome (Main.qml strip / kill banner) is
// untouched and out of scope.
//
// Token-binding contract: NO hardcoded hex / size literals — Theme tokens
// only. PR-7 components are composed, not re-implemented inline.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    property real captureContentHeight: scroller.contentHeight

    // ------------------------------------------------------------------
    // Issue 05: RunnerSelect dropdown relay signals.
    //
    // RunnerSelect lives inside activeOpsCol (Section III). Main.qml cannot
    // reach it directly because it is loaded inside a Loader. These relay
    // signals and the closeRunnerDropdown() function bridge the boundary:
    //   Main.qml Connections.onRunnerDropdownOpened  → sets _dropdownOpen = true
    //   Main.qml Connections.onRunnerDropdownDismissed → sets _dropdownOpen = false
    //   Main.qml onDropdownDismissedSignal → calls closeRunnerDropdown() here
    // ------------------------------------------------------------------
    signal runnerDropdownOpened()
    signal runnerDropdownDismissed()

    function closeRunnerDropdown() {
        runnerSelectInst.expanded = false
    }

    // I-1: relay the dropdown's scene rect to Main.qml's overlay hit-test.
    // Main.qml cannot reach runnerSelectInst directly across the Loader boundary.
    function runnerDropdownSceneRect() {
        return runnerSelectInst.dropdownBoundsInScene()
    }

    Connections {
        target: runnerSelectInst
        function onOpened()    { root.runnerDropdownOpened() }
        function onDismissed() { root.runnerDropdownDismissed() }
    }

    // ------------------------------------------------------------------
    // Slice selection — pure client-side index into precomputed bySlice
    // maps. Sections II and IV each own an independent slice.
    //
    // Persisted across page switches via Main.qml sessionBag (issue 12).
    // Session-only — does not survive app restart.
    // Initial values are seeded by Main.qml's surfaceLoader.onLoaded handler;
    // user changes flow back to sessionBag via the Connections write-back.
    // Default string values ("") are safe — QML string properties default to
    // "" and the bySlice map lookups fall back to ({}) via || ({}).
    // ------------------------------------------------------------------
    property string perfSlice
    property string throughputSlice

    readonly property var sliceOptions: [
        { label: "Today",     value: "Today" },
        { label: "Week",      value: "Week" },
        { label: "Month",     value: "Month" },
        { label: "YTD",       value: "YTD" },
        { label: "All-Paper", value: "All-Paper" }
    ]

    // ------------------------------------------------------------------
    // Pure formatting helpers (no literals that bind to the design system;
    // these are value-formatting only, not tokens).
    // ------------------------------------------------------------------
    function fmtMoney(value) {
        var n = Number(value || 0)
        var sign = n < 0 ? "-" : ""
        return sign + "$" + Math.abs(n).toLocaleString(Qt.locale("en_US"), "f", 2)
    }

    function fmtPct(value) {
        if (value === null || value === undefined)
            return "—"
        var n = Number(value) * 100
        var sign = n > 0 ? "+" : ""
        return sign + n.toFixed(2) + "%"
    }

    // Returns "positive" | "negative" | "muted" for a numeric value.
    function toneOf(value) {
        if (value === null || value === undefined)
            return "muted"
        var n = Number(value)
        if (n > 0) return "positive"
        if (n < 0) return "negative"
        return "muted"
    }

    function shortTime(iso) {
        if (!iso)
            return "—"
        var s = String(iso)
        // ISO "YYYY-MM-DDTHH:MM:SS..." → "HH:MM"
        if (s.length >= 16 && s.indexOf("T") === 10)
            return s.substring(11, 16)
        return s
    }

    // ------------------------------------------------------------------
    // Background
    // ------------------------------------------------------------------
    Rectangle { anchors.fill: parent; color: Theme.color.surface.canvas }

    // ------------------------------------------------------------------
    // Reusable per-section status banner (loading / error isolation).
    // One section's error never blanks the surface.
    // ------------------------------------------------------------------
    component SectionStatus: Column {
        id: secStatus
        property string status: "ready"
        property string errorMessage: ""
        property bool   hasData: false
        width: parent ? parent.width : 0
        spacing: Theme.space[2]
        visible: secStatus.status !== "ready" || !secStatus.hasData

        Text {
            visible: secStatus.status === "loading" && !secStatus.hasData
            width: parent.width
            text: "Loading…"
            color: Theme.color.text.muted
            font.family:    Theme.typography.body.sm.family
            font.pixelSize: Theme.typography.body.sm.size
            font.italic:    true
        }
        Text {
            visible: secStatus.status === "error"
            width: parent.width
            text: secStatus.errorMessage !== ""
                  ? "Unavailable — " + secStatus.errorMessage
                  : "Unavailable."
            color: Theme.status.warning
            font.family:    Theme.typography.body.sm.family
            font.pixelSize: Theme.typography.body.sm.size
            font.italic:    true
            wrapMode: Text.WordWrap
        }
        Text {
            visible: secStatus.status === "ready" && !secStatus.hasData
            width: parent.width
            text: "No data yet."
            color: Theme.color.text.muted
            font.family:    Theme.typography.body.sm.family
            font.pixelSize: Theme.typography.body.sm.size
            font.italic:    true
        }
    }

    // A small labelled key/value used in Section I.
    component KeyStat: Column {
        property string k: ""
        property string v: ""
        property color  vColor: Theme.color.text.primary
        spacing: Theme.space[1]
        Text {
            text: parent.k
            color: Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
        Text {
            text: parent.v
            color: parent.vColor
            font.family:    Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features:  Theme.typography.data.md.features
        }
    }

    // Editorial section standfirst — master section idiom, reference
    // DeskSurface.qml@757afe7:653-659. Deliberate scale split: body.md.family
    // (typeface) + body.sm.size (scale) — do not normalize to one scale.
    component Standfirst: Text {
        width:          parent ? parent.width : implicitWidth
        color:          Theme.color.text.secondary
        font.family:    Theme.typography.body.md.family
        font.pixelSize: Theme.typography.body.sm.size
        font.italic:    true
        wrapMode:       Text.WordWrap
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
        // Deterministic desktop scrolling: wheel scrolls, click-drag does not.
        interactive: false

        Column {
            id: pageColumn
            x: Theme.space[7]
            width: scroller.width - Theme.space[7] * 2
            topPadding: Theme.space[7]
            spacing: Theme.space[6]

            // ========================================================
            // HEADER BAND — kicker / title / standfirst
            // ========================================================
            Column {
                width: parent.width
                spacing: Theme.space[2]

                Text {
                    text: "Live Operations · The Trading Desk"
                    color: Theme.color.text.muted
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.space[5]

                    Row {
                        spacing: 0
                        Text {
                            text:  "The Trading Desk"
                            color: Theme.color.brand.primary
                            font.family:    Theme.typography.display.lg.family
                            font.pixelSize: Theme.typography.display.lg.size
                            font.weight:    Theme.typography.display.lg.weight
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
                        text: "the operator's working spread — risk posture, performance, live operations, and market weather on one fold, on live data."
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.md.size
                        font.italic:    true
                        wrapMode:       Text.WordWrap
                    }
                }
            }

            Rectangle { width: parent.width; height: 1; color: Theme.color.border.regular }

            // ========================================================
            // ROW 1 — I Risk & Mode · II Performance & Trust · III Active Ops
            // ========================================================
            RowLayout {
                width: parent.width
                spacing: Theme.space[6]

                // ---- I · RISK & MODE -------------------------------
                Column {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    Layout.preferredHeight: implicitHeight
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[3]

                    SectionHeader { width: parent.width; numeral: "I"; title: "Risk & Mode" }
                    Standfirst { text: "risk posture and operating mode, on live broker state" }

                    // Kill-switch / guard headline
                    Text {
                        width: parent.width
                        text: OperationalState.killSwitchActive
                              ? "Kill switch fired"
                              : "Guard ready"
                        color: OperationalState.killSwitchActive
                               ? Theme.status.negative
                               : Theme.status.positive
                        font.family:    Theme.typography.display.sm.family
                        font.pixelSize: Theme.typography.display.sm.size
                        font.weight:    Theme.typography.display.sm.weight
                        wrapMode: Text.WordWrap
                    }
                    Text {
                        width: parent.width
                        text: OperationalState.killSwitchActive
                              ? (OperationalState.killSwitchReason !== ""
                                 ? OperationalState.killSwitchReason
                                 : "Manual reset required. Trading halted.")
                              : (OperationalState.tradingMode.toUpperCase()
                                 + " mode · "
                                 + (OperationalState.marketOpen ? "market open" : "market closed"))
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.italic:    true
                        wrapMode: Text.WordWrap
                    }

                    Row {
                        width: parent.width
                        spacing: Theme.space[6]
                        KeyStat {
                            k: "Mode"
                            v: OperationalState.tradingMode.toUpperCase()
                        }
                        KeyStat {
                            k: "Broker"
                            v: OperationalState.brokerStatus.toUpperCase()
                            vColor: OperationalState.brokerStatus === "connected"
                                    ? Theme.status.positive
                                    : OperationalState.brokerStatus === "error"
                                      ? Theme.status.negative
                                      : Theme.color.text.muted
                        }
                        KeyStat {
                            k: "Market"
                            v: OperationalState.marketOpen ? "OPEN" : "CLOSED"
                        }
                    }
                    Row {
                        width: parent.width
                        spacing: Theme.space[6]
                        KeyStat {
                            k: "Open Pos."
                            v: String(OperationalState.openPositionsCount)
                        }
                        KeyStat {
                            // DB-present indicator (spec §3 / §5 Section I):
                            // a non-empty PerformanceState refresh timestamp
                            // means the event store DB was readable.
                            k: "Data Store"
                            v: PerformanceState.dataStatus === "error"
                               ? "UNREADABLE"
                               : (PerformanceState.lastRefreshedAt !== "" ? "PRESENT" : "PENDING")
                            vColor: PerformanceState.dataStatus === "error"
                                    ? Theme.status.negative
                                    : (PerformanceState.lastRefreshedAt !== ""
                                       ? Theme.status.positive
                                       : Theme.color.text.muted)
                        }
                    }
                    Text {
                        width: parent.width
                        visible: OperationalState.brokerStatus === "error"
                                 && OperationalState.brokerErrorMessage !== ""
                        text: "Broker: " + OperationalState.brokerErrorMessage
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.italic:    true
                        wrapMode: Text.WordWrap
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    color: Theme.color.border.subtle
                }

                // ---- II · PERFORMANCE & TRUST ----------------------
                Column {
                    id: perfCol
                    Layout.fillWidth: true
                    Layout.preferredWidth: 4
                    Layout.preferredHeight: implicitHeight
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[3]

                    readonly property bool isToday: root.perfSlice === "Today"
                    readonly property var slice: PerformanceState.bySlice[root.perfSlice] || ({})
                    readonly property var bench: PerformanceState.benchmarkBySlice[root.perfSlice] || ({})
                    // Stale treatment applies to Section II only and only to
                    // the hero (not the live-broker Today figure).
                    // showStale is true only when a snapshot exists AND it is old.
                    readonly property bool hasSnapshot: PerformanceState.hasSnapshot
                    readonly property bool showStale: PerformanceState.isStale && !isToday
                    readonly property bool hasData: PerformanceState.dataStatus !== "error"
                                                    && PerformanceState.lastRefreshedAt !== ""

                    SectionHeader {
                        width: parent.width
                        numeral: "II"
                        title: "Performance & Trust"
                        Text {
                            text: PerformanceState.lastRefreshedAt !== ""
                                  ? "as of " + root.shortTime(PerformanceState.lastRefreshedAt)
                                  : ""
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.data.sm.family
                            font.pixelSize: Theme.typography.data.sm.size
                            font.features:  Theme.typography.data.sm.features
                        }
                    }
                    Standfirst { text: "realised P/L for the selected window, with snapshot freshness stated plainly" }

                    SegmentedToggle {
                        width: parent.width
                        options: root.sliceOptions
                        current: root.perfSlice
                        onActivated: function(value) { root.perfSlice = value }
                    }

                    SectionStatus {
                        status: PerformanceState.dataStatus
                        errorMessage: PerformanceState.dataErrorMessage
                        hasData: PerformanceState.lastRefreshedAt !== ""
                    }

                    // Empty state — no snapshot at all (honest "no data yet").
                    SectionStatus {
                        visible: perfCol.hasData && !perfCol.hasSnapshot
                        status: "ready"
                        errorMessage: ""
                        hasData: false
                    }

                    // Stale hero — muted "stale as of <date>" (spec-locked).
                    // Only shown when a snapshot exists AND it is older than threshold.
                    Column {
                        visible: perfCol.hasData && perfCol.hasSnapshot && perfCol.showStale
                        width: parent.width
                        spacing: Theme.space[1]
                        Text {
                            text: "P/L · " + root.perfSlice
                            color: Theme.color.text.muted
                            font.family:         Theme.typography.label.xs.family
                            font.pixelSize:      Theme.typography.label.xs.size
                            font.weight:         Theme.typography.label.xs.weight
                            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                            font.capitalization: Font.AllUppercase
                        }
                        Text {
                            text: "stale as of " + (PerformanceState.staleAsOf !== ""
                                                    ? PerformanceState.staleAsOf
                                                    : "unknown")
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.display.sm.family
                            font.pixelSize: Theme.typography.display.sm.size
                            font.weight:    Theme.typography.display.sm.weight
                            font.italic:    true
                        }
                        Text {
                            width: perfCol.width
                            text: "Snapshot older than the freshness threshold — not presented as current."
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.sm.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                            wrapMode: Text.WordWrap
                        }
                    }

                    // Fresh hero — Today binds OperationalState.dailyPnl;
                    // Week+ bind PerformanceState.bySlice[slice].return.
                    Column {
                        visible: perfCol.hasData && perfCol.hasSnapshot && !perfCol.showStale
                        width: parent.width
                        spacing: Theme.space[3]

                        RollupCell {
                            width: parent.width
                            label: "P/L · " + root.perfSlice
                            value: perfCol.isToday
                                   ? root.fmtMoney(OperationalState.dailyPnl)
                                   : root.fmtPct(perfCol.slice.return)
                            tone: perfCol.isToday
                                  ? root.toneOf(OperationalState.dailyPnl)
                                  : root.toneOf(perfCol.slice.return)
                        }

                        Sparkline {
                            width: parent.width
                            height: Theme.space[7] * 2
                            series: PerformanceState.sparkline
                            showAxis: false
                            showGrid: false
                            hairline: true
                        }

                        Item {
                            width: parent.width
                            // 44 = ceil(label.xs 12×1.40 + space[1] 4 + data.md 14×1.60) — matches the
                            // natural SubGrid row height when DRAWDOWN/SPY/EXCESS is rendered. If those
                            // theme tokens change, this constant must be re-derived.
                            height: 44
                            Loader {
                                anchors.fill: parent
                                active: !perfCol.isToday
                                sourceComponent: drawdownSpyExcessComponent
                            }
                        }
                        Component {
                            id: drawdownSpyExcessComponent
                            Row {
                                width: parent ? parent.width : 0
                                spacing: Theme.space[6]
                                KeyStat {
                                    k: "Drawdown"
                                    v: root.fmtPct(perfCol.slice.drawdown)
                                    vColor: Theme.status.negative
                                }
                                KeyStat {
                                    k: "SPY"
                                    v: root.fmtPct(perfCol.bench.spyReturn)
                                }
                                KeyStat {
                                    k: "Excess"
                                    v: root.fmtPct(perfCol.bench.excess)
                                    vColor: root.toneOf(perfCol.bench.excess) === "positive"
                                            ? Theme.status.positive
                                            : root.toneOf(perfCol.bench.excess) === "negative"
                                              ? Theme.status.negative
                                              : Theme.color.text.primary
                                }
                            }
                        }
                        Text {
                            width: parent.width
                            visible: perfCol.isToday
                            text: "Live broker daily P/L. Period slices use end-of-day portfolio snapshots."
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.sm.family
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

                // ---- III · ACTIVE OPERATIONS ----------------------
                Column {
                    id: activeOpsCol
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    Layout.preferredHeight: implicitHeight
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[3]

                    property string selectedRunner: ""

                    readonly property var _runnerOptions: {
                        var out = []
                        var rs = ActiveOpsState.runners
                        for (var i = 0; i < rs.length; i++)
                            out.push({ id: rs[i].strategyId, label: rs[i].strategyId })
                        return out
                    }
                    readonly property var _selected: {
                        var rs = ActiveOpsState.runners
                        if (rs.length === 0) return ({})
                        var want = activeOpsCol.selectedRunner
                        for (var i = 0; i < rs.length; i++) {
                            if (rs[i].strategyId === want) return rs[i]
                        }
                        return rs[0]
                    }

                    SectionHeader {
                        width: parent.width
                        numeral: "III"
                        title: "Active Operations"
                        Text {
                            text: ActiveOpsState.runners.length > 0
                                  ? ActiveOpsState.runners.length + " runners"
                                  : ""
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.data.sm.family
                            font.pixelSize: Theme.typography.data.sm.size
                            font.features:  Theme.typography.data.sm.features
                        }
                    }
                    Standfirst { text: "what is running right now in this session" }

                    SectionStatus {
                        status: ActiveOpsState.dataStatus
                        errorMessage: ActiveOpsState.dataErrorMessage
                        hasData: ActiveOpsState.runners.length > 0
                    }

                    RunnerSelect {
                        id: runnerSelectInst
                        width: parent.width
                        visible: ActiveOpsState.runners.length > 0
                        runners: activeOpsCol._runnerOptions
                        current: activeOpsCol._selected.strategyId || ""
                        onSelected: function(runnerId) { activeOpsCol.selectedRunner = runnerId }
                    }

                    GridLayout {
                        width: parent.width
                        visible: ActiveOpsState.runners.length > 0
                        columns: 2
                        rowSpacing:    Theme.space[3]
                        columnSpacing: Theme.space[6]

                        KeyStat {
                            Layout.fillWidth: true
                            k: "Session"
                            v: (activeOpsCol._selected.sessionState || "—").toUpperCase()
                            vColor: (activeOpsCol._selected.sessionState || "").indexOf("running") === 0
                                    ? Theme.status.positive
                                    : Theme.color.text.secondary
                        }
                        KeyStat {
                            Layout.fillWidth: true
                            k: "Cadence"
                            v: activeOpsCol._selected.cadence || "—"
                        }
                        KeyStat {
                            Layout.fillWidth: true
                            k: "Heartbeat"
                            v: activeOpsCol._selected.heartbeat || "—"
                            vColor: (activeOpsCol._selected.heartbeat || "") === "on schedule"
                                    ? Theme.status.positive
                                    : (activeOpsCol._selected.heartbeat || "").indexOf("overdue") === 0
                                      ? Theme.status.warning
                                      : Theme.color.text.muted
                        }
                        KeyStat {
                            Layout.fillWidth: true
                            k: "Lock"
                            v: (activeOpsCol._selected.runnerLock || "—").toUpperCase()
                        }
                        KeyStat {
                            Layout.fillWidth: true
                            k: "Stop Req."
                            v: activeOpsCol._selected.stopRequested ? "YES" : "NO"
                            vColor: activeOpsCol._selected.stopRequested
                                    ? Theme.status.warning
                                    : Theme.color.text.secondary
                        }
                        KeyStat {
                            Layout.fillWidth: true
                            k: "Session Age"
                            v: activeOpsCol._selected.sessionAge || "—"
                        }
                    }
                    Text {
                        width: parent.width
                        visible: ActiveOpsState.runners.length > 0
                        text: activeOpsCol._selected.lastEval
                              ? "last eval " + root.shortTime(activeOpsCol._selected.lastEval)
                              : "no evaluations recorded"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.italic:    true
                    }
                }
            }

            Rectangle { width: parent.width; height: 1; color: Theme.color.border.regular }

            // ========================================================
            // ROW 2 — IV Risk Throughput · V Strategy Attention · VI Market Tape
            // ========================================================
            RowLayout {
                width: parent.width
                spacing: Theme.space[6]

                // ---- IV · RISK LAYER THROUGHPUT -------------------
                Column {
                    id: throughputCol
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    Layout.preferredHeight: implicitHeight
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[3]

                    readonly property var stages: RiskThroughputState.bySlice[root.throughputSlice] || []
                    readonly property var _stageGloss: ({
                        "Evaluations":     "gate inputs",
                        "Signals":         "raised",
                        "Orders Proposed": "pre-risk",
                        "Risk-Approved":   "passed gate",
                        "Rejected":        "blocked",
                        "Submitted":       "sent to broker",
                        "Filled":          "executed"
                    })

                    SectionHeader { width: parent.width; numeral: "IV"; title: "Risk Layer Throughput" }
                    Standfirst { text: "how work moved through the risk gate, stage by stage" }

                    SegmentedToggle {
                        width: parent.width
                        options: root.sliceOptions
                        current: root.throughputSlice
                        onActivated: function(value) { root.throughputSlice = value }
                    }

                    SectionStatus {
                        status: RiskThroughputState.dataStatus
                        errorMessage: RiskThroughputState.dataErrorMessage
                        hasData: RiskThroughputState.lastRefreshedAt !== ""
                    }

                    Column {
                        width: parent.width
                        spacing: Theme.space[2]
                        visible: RiskThroughputState.dataStatus !== "error"

                        Repeater {
                            model: throughputCol.stages
                            delegate: FunnelRow {
                                width: parent.width
                                label: modelData.label
                                gloss: throughputCol._stageGloss[modelData.label] || ""
                                value: String(modelData.value)
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    color: Theme.color.border.subtle
                }

                // ---- V · STRATEGY ATTENTION -----------------------
                Column {
                    id: attentionCol
                    Layout.fillWidth: true
                    Layout.preferredWidth: 4
                    Layout.preferredHeight: implicitHeight
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[3]

                    readonly property var rollups: AttentionState.rollups

                    SectionHeader { width: parent.width; numeral: "V"; title: "Strategy Attention" }
                    Standfirst { text: "strategies that need an operator's eye, by reason" }

                    SectionStatus {
                        status: AttentionState.dataStatus
                        errorMessage: AttentionState.dataErrorMessage
                        hasData: AttentionState.lastRefreshedAt !== ""
                    }

                    GridLayout {
                        width: parent.width
                        visible: AttentionState.dataStatus !== "error"
                        columns: 3
                        rowSpacing:    Theme.space[5]
                        columnSpacing: Theme.space[5]

                        RollupCell {
                            Layout.fillWidth: true
                            label: "Running Now"
                            value: String(attentionCol.rollups.runningNow || 0)
                            tone: "brand"
                        }
                        RollupCell {
                            Layout.fillWidth: true
                            label: "Paper Testing"
                            value: String(attentionCol.rollups.paperTesting || 0)
                            tone: "brand"
                        }
                        RollupCell {
                            Layout.fillWidth: true
                            label: "Backtest Only"
                            value: String(attentionCol.rollups.backtestOnly || 0)
                            tone: "muted"
                        }
                        RollupCell {
                            Layout.fillWidth: true
                            label: "Needs Review"
                            value: String(attentionCol.rollups.needsReview || 0)
                            tone: Number(attentionCol.rollups.needsReview || 0) > 0 ? "warning" : "muted"
                        }
                        RollupCell {
                            Layout.fillWidth: true
                            label: "Underperforming"
                            value: String(attentionCol.rollups.underperforming || 0)
                            tone: Number(attentionCol.rollups.underperforming || 0) > 0 ? "negative" : "muted"
                        }
                    }

                    Rectangle {
                        width: parent.width
                        height: 1
                        color: Theme.color.border.subtle
                        visible: AttentionState.driftList.length > 0
                    }

                    Column {
                        width: parent.width
                        spacing: Theme.space[2]
                        visible: AttentionState.driftList.length > 0

                        Repeater {
                            model: AttentionState.driftList
                            delegate: RowLayout {
                                width: parent.width
                                spacing: Theme.space[3]
                                Text {
                                    Layout.preferredWidth: 160
                                    text: modelData.name
                                    color: Theme.color.text.primary
                                    font.family:    Theme.typography.body.sm.family
                                    font.pixelSize: Theme.typography.body.sm.size
                                    font.weight:    Font.Medium
                                    elide: Text.ElideRight
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.note
                                    color: modelData.tone === "warn"
                                           ? Theme.status.warning
                                           : Theme.color.text.secondary
                                    font.family:    Theme.typography.body.sm.family
                                    font.pixelSize: Theme.typography.body.sm.size
                                    font.italic:    true
                                    elide: Text.ElideRight
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

                // ---- VI · MARKET TAPE -----------------------------
                Column {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    Layout.preferredHeight: implicitHeight
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[3]

                    SectionHeader {
                        width: parent.width
                        numeral: "VI"
                        title: "Market Tape"
                        Text {
                            text: MarketTapeState.lastRefreshedAt !== ""
                                  ? "as of " + root.shortTime(MarketTapeState.lastRefreshedAt)
                                  : ""
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.data.sm.family
                            font.pixelSize: Theme.typography.data.sm.size
                            font.features:  Theme.typography.data.sm.features
                        }
                    }
                    Standfirst { text: "instrument status for the market-data feed" }

                    SectionStatus {
                        status: MarketTapeState.dataStatus
                        errorMessage: MarketTapeState.dataErrorMessage
                        hasData: MarketTapeState.rows.length > 0
                    }

                    Column {
                        width: parent.width
                        spacing: 0
                        visible: MarketTapeState.dataStatus !== "error"

                        Repeater {
                            model: MarketTapeState.rows
                            delegate: TapeRow {
                                width: parent.width
                                symbol: modelData.symbol
                                close: modelData.close !== null && modelData.close !== undefined
                                       ? Number(modelData.close).toLocaleString(Qt.locale("en_US"), "f", 2)
                                       : "—"
                                pctChange: root.fmtPct(modelData.pctChange)
                                asOf: modelData.asOf ? String(modelData.asOf) : "—"
                            }
                        }
                    }
                }
            }

            Rectangle { width: parent.width; height: 1; color: Theme.color.border.regular }

            // ========================================================
            // VII · ORDER / SIGNAL TAPE (full-width)
            // ========================================================
            Column {
                id: feedCol
                width: parent.width
                spacing: Theme.space[3]

                property string feedFilter: "All"

                readonly property var _filterOptions: [
                    { label: "All",        value: "All" },
                    { label: "Orders",     value: "order" },
                    { label: "Rejections", value: "rejection" },
                    { label: "Signals",    value: "signal" },
                    { label: "Fills",      value: "fill" }
                ]

                // Normalize ActivityFeedState rows {time,strategy,kind,detail,
                // symbol,tone} → ActivityTable shape {ts,kind,subject,detail,
                // tone}. Presentational mapping only; no query logic.
                readonly property var _tableRows: {
                    var src = ActivityFeedState.rows
                    var out = []
                    for (var i = 0; i < src.length; i++) {
                        var r = src[i]
                        out.push({
                            ts: root.shortTime(r.time),
                            kind: r.kind,
                            subject: (r.strategy || "") + (r.symbol ? " · " + r.symbol : ""),
                            detail: r.detail || "",
                            tone: r.tone || "data"
                        })
                    }
                    return out
                }

                SectionHeader {
                    width: parent.width
                    numeral: "VII"
                    title: "Order / Signal Tape"
                    Text {
                        text: ActivityFeedState.rows.length > 0
                              ? ActivityFeedState.rows.length + " events"
                              : ""
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.data.sm.family
                        font.pixelSize: Theme.typography.data.sm.size
                        font.features:  Theme.typography.data.sm.features
                    }
                }
                Standfirst { text: "the chronological record of orders, signals, and fills" }

                SegmentedToggle {
                    options: feedCol._filterOptions
                    current: feedCol.feedFilter
                    onActivated: function(value) { feedCol.feedFilter = value }
                }

                SectionStatus {
                    status: ActivityFeedState.dataStatus
                    errorMessage: ActivityFeedState.dataErrorMessage
                    hasData: ActivityFeedState.rows.length > 0
                }

                ActivityTable {
                    width: parent.width
                    height: Theme.space[7] * 10
                    visible: ActivityFeedState.dataStatus !== "error"
                    rows: feedCol._tableRows
                    // kindFilter drives the category toggle: "All" → "" (show
                    // all kinds); other values are exact kind tokens that must
                    // equal the row's kind field — no substring false-positives.
                    kindFilter: feedCol.feedFilter === "All" ? "" : feedCol.feedFilter
                    // filter is reserved for free-text search; leave empty by default.
                    filter: ""
                }
            }

            Item { width: parent.width; height: Theme.space[7] }
        }
    }
}

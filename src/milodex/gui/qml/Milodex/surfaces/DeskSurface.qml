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
// Animation discipline (locked, amended 2026-07-22): state changes instant;
// P&L figures never crossfade; no idle animation — with ONE sanctioned
// exception: the Section III fleet table's RUNNING pulse (a slow opacity
// beat on the state dot), a founder-approved liveness affordance for
// fleet-watching, not decoration. Chrome (Main.qml strip / kill banner) is
// untouched and out of scope.
//
// Token-binding contract: NO hardcoded hex / size literals — Theme tokens
// only. PR-7 components are composed, not re-implemented inline.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

SurfaceBase {
    id: root

    captureContentHeight: scroller.contentHeight

    // ------------------------------------------------------------------
    // Slice selection — pure client-side index into precomputed bySlice
    // maps. Sections II and IV each own an independent slice.
    //
    // Persisted across page switches via Main.qml sessionBag (issue 12).
    // Session-only — does not survive app restart.
    // Initial values are seeded by Main.qml's surfaceLoader.onLoaded handler;
    // user changes flow back to sessionBag via the Connections write-back.
    // Default string values are "Week" — matches sessionBag's defaults and
    // ensures SegmentedToggle.current is never "" (which would render
    // all-unselected). The Main.qml seed handler fires on every surface load
    // and still wins for any persisted user selection; these literals only
    // matter if the seed handler is ever bypassed. Belt-and-suspenders:
    // bySlice[""] || ({}) is still valid fallback protection inside the file.
    // ------------------------------------------------------------------
    property string perfSlice: "Week"
    property string throughputSlice: "Week"

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
    // Delegate to Formatters singleton (PR10).
    // fmtPct is intentionally NOT repointed: it scales ×100 and adds a sign,
    // which differs from Formatters.pct1 semantics (single-site, different semantics).
    function fmtMoney(value) { return Formatters.money(value) }

    function fmtPct(value) {
        if (value === null || value === undefined)
            return "—"
        var n = Number(value) * 100
        var sign = n > 0 ? "+" : ""
        return sign + n.toFixed(2) + "%"
    }

    function toneOf(value) { return Formatters.toneOf(value) }

    // sessionBag is inherited from SurfaceBase; Main.qml parameterizes it in on
    // surface load. The surface binds one-way to its timeFormat; the operator-
    // facing writer is the Risk Office drawer, which emits timeFormatRequested
    // → Main.qml writes back.

    // timeFormat: read-only mirror of sessionBag.timeFormat. Drives shortTime().
    readonly property string timeFormat: sessionBag ? sessionBag.timeFormat : "24h"

    // shortTime: formats an ISO 8601 string per root.timeFormat.
    // Returns "—" for empty/null. Delegates to Formatters.shortTime (PR10).
    function shortTime(iso) { return Formatters.shortTime(iso, root.timeFormat) }

    // shortDateTime: "YYYY-MM-DD " + shortTime(iso). For timestamps that may
    // be days old (an ended session's close), where a bare clock time is
    // ambiguous.
    function shortDateTime(iso) {
        if (!iso) return "—"
        var d = new Date(iso)
        if (isNaN(d)) return iso
        var mo = d.getMonth() + 1
        var day = d.getDate()
        return d.getFullYear() + "-" + (mo < 10 ? "0" + mo : mo)
               + "-" + (day < 10 ? "0" + day : day) + " " + root.shortTime(iso)
    }

    // ------------------------------------------------------------------
    // Background
    // ------------------------------------------------------------------
    Rectangle { anchors.fill: parent; color: Theme.color.surface.canvas }

    // Reusable per-section status banner (loading / error isolation) now
    // lives in components/SectionStatus.qml (PR-8) — resolved via the
    // `Milodex 1.0` import above. One section's error never blanks the
    // surface.

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

    // Uppercase column label for the Section III fleet-table header row.
    component FleetHeadLabel: Text {
        anchors.verticalCenter: parent.verticalCenter
        color: Theme.color.text.muted
        font.family:         Theme.typography.label.xs.family
        font.pixelSize:      Theme.typography.label.xs.size
        font.weight:         Theme.typography.label.xs.weight
        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
        font.capitalization: Font.AllUppercase
    }

    // ------------------------------------------------------------------
    // Scroll container — full-width (maxContentWidth 0), wheel-only scroll.
    // Deterministic desktop scrolling: wheel scrolls, click-drag does not.
    // ------------------------------------------------------------------
    ScrollSurface {
        id: scroller
        interactive: false

        Column {
            id: pageColumn
            width: parent.width
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
                    objectName: "deskSectionRiskMode"
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
                    objectName: "deskSectionPerformance"
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
                //
                // FLEET TABLE (2026-07-22, founder-approved): one row per
                // strategy in ActiveOpsState.runners (live first, then most
                // recently started), replacing the single-runner
                // RunnerSelect + KeyStat panel that hid all but one runner
                // of a 6–11 runner fleet. Clicking a row expands the
                // original KeyStat detail grid beneath the table (click
                // again to collapse) — no capability lost. The heartbeat
                // column ticks live for running rows; E · V · S are today's
                // evaluations / vetoes / submits from the read model's
                // single aggregate query.
                Column {
                    id: activeOpsCol
                    objectName: "deskSectionActiveOps"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 3
                    Layout.preferredHeight: implicitHeight
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.space[3]

                    // "" = no row expanded. Set by row click; cleared by
                    // clicking the expanded row again.
                    property string selectedRunner: ""

                    readonly property var _selected: {
                        var rs = ActiveOpsState.runners
                        var want = activeOpsCol.selectedRunner
                        if (want === "") return ({})
                        for (var i = 0; i < rs.length; i++) {
                            if (rs[i].strategyId === want) return rs[i]
                        }
                        // Selected runner vanished from the model (e.g. DB
                        // swap) — treat as collapsed rather than showing a
                        // stale detail grid.
                        return ({})
                    }
                    readonly property bool _expanded:
                        (activeOpsCol._selected.strategyId || "") !== ""
                    readonly property bool _selectedEnded:
                        (activeOpsCol._selected.endedAt || "") !== ""

                    // Live tick for the heartbeat column: the read model
                    // refreshes every 30 s; between refreshes running rows
                    // age their heartbeat client-side from lastRefreshedAt.
                    property double _nowMs: Date.now()
                    readonly property double _refreshedMs: {
                        var iso = ActiveOpsState.lastRefreshedAt
                        if (!iso) return 0
                        var t = Date.parse(iso)
                        return isNaN(t) ? 0 : t
                    }
                    Timer {
                        interval: 1000
                        repeat: true
                        running: activeOpsCol.visible && ActiveOpsState.liveCount > 0
                        onTriggered: activeOpsCol._nowMs = Date.now()
                    }

                    // Compact age: "32s" / "4m" / "2h" / "3d".
                    function fmtAge(secs) {
                        var s = Math.floor(secs)
                        if (s < 0) s = 0
                        if (s < 60) return s + "s"
                        var m = Math.floor(s / 60)
                        if (m < 60) return m + "m"
                        var h = Math.floor(m / 60)
                        if (h < 24) return h + "h"
                        return Math.floor(h / 24) + "d"
                    }

                    function hbText(row) {
                        var base = row.heartbeatAgeSeconds
                        if (base === null || base === undefined) return "—"
                        var age = Number(base)
                        if (row.sessionState === "running" && activeOpsCol._refreshedMs > 0) {
                            var extra = (activeOpsCol._nowMs - activeOpsCol._refreshedMs) / 1000
                            if (extra > 0) age += extra
                        }
                        return activeOpsCol.fmtAge(age)
                    }

                    SectionHeader {
                        width: parent.width
                        numeral: "III"
                        title: "Active Operations"
                        Text {
                            text: ActiveOpsState.runners.length > 0
                                  ? ActiveOpsState.liveCount + " runners"
                                  : ""
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.data.sm.family
                            font.pixelSize: Theme.typography.data.sm.size
                            font.features:  Theme.typography.data.sm.features
                        }
                    }
                    Standfirst { text: "the fleet, one row per strategy — E · V · S are today's evaluations, vetoes, and submits" }

                    SectionStatus {
                        status: ActiveOpsState.dataStatus
                        errorMessage: ActiveOpsState.dataErrorMessage
                        hasData: ActiveOpsState.lastRefreshedAt !== ""
                    }

                    // Empty state — honest quiet line, not an empty table
                    // frame (mirrors LedgerSurface's "No entries yet.").
                    Text {
                        visible: ActiveOpsState.dataStatus !== "error"
                                 && ActiveOpsState.lastRefreshedAt !== ""
                                 && ActiveOpsState.runners.length === 0
                        width: parent.width
                        text: "No runner sessions on record — nothing has launched yet."
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.italic:    true
                        wrapMode: Text.WordWrap
                    }

                    // ---- fleet table ------------------------------------
                    Column {
                        objectName: "deskFleetTable"
                        width: parent.width
                        spacing: 0
                        visible: ActiveOpsState.runners.length > 0

                        // Header row — uppercase mono-adjacent labels over a
                        // hairline, same geometry as the data rows below.
                        Item {
                            width: parent.width
                            height: fleetHeadStrategy.implicitHeight + Theme.space[2] * 2

                            Rectangle {
                                anchors.bottom: parent.bottom
                                anchors.left:   parent.left
                                anchors.right:  parent.right
                                height: 1
                                color:  Theme.color.border.regular
                            }

                            FleetHeadLabel {
                                id: fleetHeadStrategy
                                anchors.left: parent.left
                                text: "Strategy"
                            }
                            FleetHeadLabel {
                                id: fleetHeadSubmits
                                anchors.right: parent.right
                                width: Theme.column.fleetCount
                                horizontalAlignment: Text.AlignRight
                                text: "S"
                            }
                            FleetHeadLabel {
                                id: fleetHeadVetoes
                                anchors.right: fleetHeadSubmits.left
                                anchors.rightMargin: Theme.space[2]
                                width: Theme.column.fleetCount
                                horizontalAlignment: Text.AlignRight
                                text: "V"
                            }
                            FleetHeadLabel {
                                id: fleetHeadEvals
                                anchors.right: fleetHeadVetoes.left
                                anchors.rightMargin: Theme.space[2]
                                width: Theme.column.fleetCount
                                horizontalAlignment: Text.AlignRight
                                text: "E"
                            }
                            FleetHeadLabel {
                                id: fleetHeadLast
                                anchors.right: fleetHeadEvals.left
                                anchors.rightMargin: Theme.space[2]
                                width: Theme.column.fleetLast
                                horizontalAlignment: Text.AlignRight
                                text: "Last"
                            }
                            FleetHeadLabel {
                                id: fleetHeadHb
                                anchors.right: fleetHeadLast.left
                                anchors.rightMargin: Theme.space[2]
                                width: Theme.column.fleetHb
                                horizontalAlignment: Text.AlignRight
                                text: "HB"
                            }
                            FleetHeadLabel {
                                anchors.right: fleetHeadHb.left
                                anchors.rightMargin: Theme.space[2]
                                width: Theme.column.fleetState
                                text: "State"
                            }
                        }

                        Repeater {
                            model: ActiveOpsState.runners

                            delegate: Item {
                                id: fleetRow
                                objectName: "deskFleetRow"
                                width: parent.width
                                height: fleetRowName.implicitHeight + Theme.space[2] * 2

                                readonly property string strategyId: modelData.strategyId || ""
                                readonly property string sessionState: modelData.sessionState || ""
                                readonly property bool _isRunning: fleetRow.sessionState === "running"
                                readonly property bool _isPhantom: fleetRow.sessionState === "phantom"
                                readonly property bool _isSelected:
                                    activeOpsCol.selectedRunner === fleetRow.strategyId

                                Rectangle {
                                    anchors.bottom: parent.bottom
                                    anchors.left:   parent.left
                                    anchors.right:  parent.right
                                    height: 1
                                    color:  Theme.color.border.subtle
                                }

                                // Strategy — family badge + display name,
                                // filling the space left of the state cell.
                                Text {
                                    id: fleetRowFamily
                                    anchors.left:           parent.left
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.family || ""
                                    color: Theme.color.text.muted
                                    font.family:         Theme.typography.label.xs.family
                                    font.pixelSize:      Theme.typography.label.xs.size
                                    font.weight:         Theme.typography.label.xs.weight
                                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                                    font.capitalization: Font.AllUppercase
                                }
                                Text {
                                    id: fleetRowName
                                    anchors.left:           fleetRowFamily.right
                                    anchors.leftMargin:     Theme.space[2]
                                    anchors.right:          fleetRowState.left
                                    anchors.rightMargin:    Theme.space[2]
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.displayName || fleetRow.strategyId
                                    color: fleetRow._isSelected
                                           ? Theme.color.brand.primary
                                           : Theme.color.text.primary
                                    font.family:    Theme.typography.body.sm.family
                                    font.pixelSize: Theme.typography.body.sm.size
                                    font.weight:    Font.Medium
                                    elide:          Text.ElideRight
                                }

                                // State — RUNNING pulses (the one sanctioned
                                // idle animation, see header); PHANTOM is
                                // alarming on purpose: dead-but-open.
                                Row {
                                    id: fleetRowState
                                    anchors.right:          fleetRowHb.left
                                    anchors.rightMargin:    Theme.space[2]
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: Theme.column.fleetState
                                    spacing: Theme.space[1]

                                    Text {
                                        id: fleetRowDot
                                        visible: fleetRow._isRunning || fleetRow._isPhantom
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "●"
                                        color: fleetRow._isRunning
                                               ? Theme.status.positive
                                               : Theme.status.negative
                                        font.family:    Theme.typography.label.xs.family
                                        font.pixelSize: Theme.typography.label.xs.size

                                        SequentialAnimation {
                                            running: fleetRow._isRunning && fleetRow.visible
                                            loops: Animation.Infinite
                                            NumberAnimation {
                                                target: fleetRowDot; property: "opacity"
                                                from: 1.0; to: 0.3
                                                duration: Theme.motion.deliberate * 3
                                                easing.type: Easing.InOutQuad
                                            }
                                            NumberAnimation {
                                                target: fleetRowDot; property: "opacity"
                                                from: 0.3; to: 1.0
                                                duration: Theme.motion.deliberate * 3
                                                easing.type: Easing.InOutQuad
                                            }
                                            onStopped: fleetRowDot.opacity = 1.0
                                        }
                                    }
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: (fleetRow.sessionState || "—").toUpperCase()
                                        color: fleetRow._isRunning
                                               ? Theme.status.positive
                                               : fleetRow._isPhantom
                                                 ? Theme.status.negative
                                                 : fleetRow.sessionState === "failed"
                                                   ? Theme.status.negative
                                                   : Theme.color.text.muted
                                        font.family:         Theme.typography.label.xs.family
                                        font.pixelSize:      Theme.typography.label.xs.size
                                        font.weight:         fleetRow._isPhantom
                                                             ? Font.DemiBold
                                                             : Theme.typography.label.xs.weight
                                        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                                        font.capitalization: Font.AllUppercase
                                    }
                                }

                                // Heartbeat age — compact, ticking while running.
                                Text {
                                    id: fleetRowHb
                                    anchors.right:          fleetRowLast.left
                                    anchors.rightMargin:    Theme.space[2]
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: Theme.column.fleetHb
                                    text: activeOpsCol.hbText(modelData)
                                    color: (modelData.heartbeat || "") === "on schedule"
                                           ? Theme.color.text.primary
                                           : (modelData.heartbeat || "").indexOf("overdue") === 0
                                             ? Theme.status.warning
                                             : Theme.color.text.muted
                                    font.family:    Theme.typography.data.xs.family
                                    font.pixelSize: Theme.typography.data.xs.size
                                    font.features:  Theme.typography.data.xs.features
                                    horizontalAlignment: Text.AlignRight
                                }

                                // Last eval time.
                                Text {
                                    id: fleetRowLast
                                    anchors.right:          fleetRowEvals.left
                                    anchors.rightMargin:    Theme.space[2]
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: Theme.column.fleetLast
                                    text: modelData.lastEval
                                          ? root.shortTime(modelData.lastEval)
                                          : "—"
                                    color: modelData.lastEval
                                           ? Theme.color.text.primary
                                           : Theme.color.text.muted
                                    font.family:    Theme.typography.data.xs.family
                                    font.pixelSize: Theme.typography.data.xs.size
                                    font.features:  Theme.typography.data.xs.features
                                    horizontalAlignment: Text.AlignRight
                                }

                                // Today counts — evaluations / vetoes / submits.
                                Text {
                                    id: fleetRowEvals
                                    anchors.right:          fleetRowVetoes.left
                                    anchors.rightMargin:    Theme.space[2]
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: Theme.column.fleetCount
                                    text: String(modelData.evalsToday || 0)
                                    color: (modelData.evalsToday || 0) > 0
                                           ? Theme.color.text.primary
                                           : Theme.color.text.muted
                                    font.family:    Theme.typography.data.xs.family
                                    font.pixelSize: Theme.typography.data.xs.size
                                    font.features:  Theme.typography.data.xs.features
                                    horizontalAlignment: Text.AlignRight
                                }
                                Text {
                                    id: fleetRowVetoes
                                    anchors.right:          fleetRowSubmits.left
                                    anchors.rightMargin:    Theme.space[2]
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: Theme.column.fleetCount
                                    text: String(modelData.vetoesToday || 0)
                                    color: (modelData.vetoesToday || 0) > 0
                                           ? Theme.status.warning
                                           : Theme.color.text.muted
                                    font.family:    Theme.typography.data.xs.family
                                    font.pixelSize: Theme.typography.data.xs.size
                                    font.features:  Theme.typography.data.xs.features
                                    horizontalAlignment: Text.AlignRight
                                }
                                Text {
                                    id: fleetRowSubmits
                                    anchors.right:          parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: Theme.column.fleetCount
                                    text: String(modelData.submitsToday || 0)
                                    color: (modelData.submitsToday || 0) > 0
                                           ? Theme.status.positive
                                           : Theme.color.text.muted
                                    font.family:    Theme.typography.data.xs.family
                                    font.pixelSize: Theme.typography.data.xs.size
                                    font.features:  Theme.typography.data.xs.features
                                    horizontalAlignment: Text.AlignRight
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: activeOpsCol.selectedRunner =
                                        fleetRow._isSelected ? "" : fleetRow.strategyId
                                }
                            }
                        }
                    }

                    // ---- drill-down detail (the original KeyStat grid) ----
                    Column {
                        objectName: "deskFleetDetail"
                        width: parent.width
                        spacing: Theme.space[3]
                        visible: activeOpsCol._expanded

                        Text {
                            width: parent.width
                            text: activeOpsCol._selected.strategyId || ""
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.data.xs.family
                            font.pixelSize: Theme.typography.data.xs.size
                            font.features:  Theme.typography.data.xs.features
                            elide: Text.ElideRight
                        }

                        GridLayout {
                            width: parent.width
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
                                objectName: "deskSessionAgeStat"
                                Layout.fillWidth: true
                                // An ended session must not wear a ticking age —
                                // say plainly that it ended, and when.
                                k: activeOpsCol._selectedEnded ? "Ended" : "Session Age"
                                v: activeOpsCol._selectedEnded
                                   ? root.shortDateTime(activeOpsCol._selected.endedAt)
                                   : (activeOpsCol._selected.sessionAge || "—")
                            }
                        }
                        Text {
                            width: parent.width
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
                    objectName: "deskSectionRiskThroughput"
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
                    objectName: "deskSectionAttention"
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

                    // ---- operator-alert rail (operator_alerts channel) ----
                    Rectangle {
                        width: parent.width
                        height: 1
                        color: Theme.color.border.subtle
                        visible: AttentionState.operatorAlerts.length > 0
                    }

                    Column {
                        objectName: "deskAttentionOperatorAlerts"
                        width: parent.width
                        spacing: Theme.space[2]
                        visible: AttentionState.operatorAlerts.length > 0

                        Text {
                            text: "Operator Alerts"
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.sm.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.weight:    Font.Medium
                        }

                        Repeater {
                            model: AttentionState.operatorAlerts
                            delegate: RowLayout {
                                width: parent.width
                                spacing: Theme.space[3]
                                Text {
                                    Layout.preferredWidth: 68
                                    Layout.alignment: Qt.AlignTop
                                    text: String(modelData.severity).toUpperCase()
                                    color: modelData.tone === "critical"
                                           ? Theme.status.negative
                                           : modelData.tone === "warn"
                                             ? Theme.status.warning
                                             : Theme.color.text.secondary
                                    font.family:    Theme.typography.body.sm.family
                                    font.pixelSize: Theme.typography.body.sm.size
                                    font.weight:    Font.Medium
                                    elide: Text.ElideRight
                                }
                                Column {
                                    Layout.fillWidth: true
                                    spacing: 1
                                    Text {
                                        width: parent.width
                                        text: modelData.summary
                                        color: Theme.color.text.primary
                                        font.family:    Theme.typography.body.sm.family
                                        font.pixelSize: Theme.typography.body.sm.size
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        width: parent.width
                                        text: [modelData.alertType, modelData.strategy, modelData.age]
                                              .filter(function (p) { return p !== "" && p !== undefined; })
                                              .join(" · ")
                                        color: Theme.color.text.muted
                                        font.family:    Theme.typography.body.sm.family
                                        font.pixelSize: Theme.typography.body.sm.size
                                        font.italic:    true
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }

                        Text {
                            width: parent.width
                            visible: AttentionState.operatorAlertsNote !== ""
                            text: AttentionState.operatorAlertsNote
                            color: Theme.color.text.muted
                            font.family:    Theme.typography.body.sm.family
                            font.pixelSize: Theme.typography.body.sm.size
                            font.italic:    true
                            elide: Text.ElideRight
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
                    objectName: "deskSectionMarketTape"
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
                objectName: "deskSectionOrderTape"
                width: parent.width
                spacing: Theme.space[3]

                property string feedFilter: "All"

                readonly property var _filterOptions: [
                    { label: "All",       value: "All"       },
                    { label: "Orders",    value: "order"     },
                    { label: "Rejections",value: "rejection" },
                    { label: "Signals",   value: "signal"    },
                    { label: "Fills",     value: "fill"      },
                    { label: "Backtests", value: "backtest"  }
                ]

                // Normalize ActivityFeedState rows {time,strategy,kind,detail,
                // symbol,tone,reason} → ActivityTable shape {ts,kind,subject,
                // detail,tone}. Presentational mapping only; no query logic.
                // Rejection rows fold the vetoing rule (reason) into detail —
                // GUI audit finding #2 (veto-reason surfacing).
                readonly property var _tableRows: {
                    var src = ActivityFeedState.rows
                    var out = []
                    for (var i = 0; i < src.length; i++) {
                        var r = src[i]
                        var detail = r.detail || ""
                        if (r.reason) detail = detail + " — " + r.reason
                        out.push({
                            ts: root.shortTime(r.time),
                            kind: r.kind,
                            subject: (r.strategy || "") + (r.symbol ? " · " + r.symbol : ""),
                            detail: detail,
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

            // Bottom gutter parity: master DeskSurface double-counted
            // pageColumn.topPadding (space[7]) into contentHeight, yielding ~96px
            // of bottom breathing. The ScrollSurface recompose drops that
            // double-count; this second trailing spacer restores the missing
            // ~space[7], matching Front/Ledger bottom rhythm. Intentionally
            // un-objectName'd so the section composition net ignores it.
            Item { width: parent.width; height: Theme.space[7] }
        }
    }
}

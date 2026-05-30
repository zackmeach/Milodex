// Main.qml -- Milodex top-level application window.
//
// Tokens consumed (DESIGN_SYSTEM.md §3, §4):
//   color.surface.canvas  -- window background
//   color.border.subtle   -- top tab-bar divider
//   color.surface.raised  -- active-tab background
//   color.brand.accent    -- active-tab 2px accent bar
//   color.text.primary    -- active-tab label
//   color.text.secondary  -- inactive-tab label
//   typography.body.md    -- tab label
//   space[3]/[5]/[6]      -- tab padding / vertical gap
//
// Wires:
//   - top-bar tabs to switch between operational surfaces (Anchor first)
//     and the design-system showcase
//   - AnchorSurface is the FIRST surface shown so the operator sees
//     operational state before anything else (per PR D.6 brief)
//   - Component.onCompleted logs Qt+QML+ThemeManager connectivity
//   - engine.quit is connected to app.quit by the Python shell
//
// Bench tab (PR F-bench): enabled; loads BenchSurface.qml (active
// management surface, mock data — drag mechanics deferred to PR2).
// Attribution tab remains stubbed; PR F will enable it.
//
// Uses QtQuick.Window (plain Window) rather than QtQuick.Controls
// ApplicationWindow so that loading Main.qml does not trigger the
// Windows native-style QQuickStyleItem registration in the process-global
// module cache.  The native-style registration is the root cause of the
// Qt 6.11 strict-type-cache constraint documented across Button.qml,
// StatusPill.qml, Surface.qml, and StrategyRow.qml.

import QtQuick
import QtQuick.Window
import Milodex 1.0

Window {
    id: root

    // ------------------------------------------------------------------
    // Window geometry
    // ------------------------------------------------------------------

    title: "Milodex"
    width: 1280
    height: 800
    minimumWidth: 960
    minimumHeight: 600
    visible: true

    color: Theme.color.surface.canvas

    // ------------------------------------------------------------------
    // Active surface state
    //
    // String enum: "anchor" | "strategy-bank" | "bench" | "design-system" |
    // "attribution-stub".  Stubs render a "(coming soon)" placeholder.
    // ------------------------------------------------------------------

    property string activeSurface: "front"
    property real screenshotContentHeight: surfaceLoader.item && surfaceLoader.item.captureContentHeight
                                        ? topBar.height + surfaceLoader.item.captureContentHeight
                                        : height

    // Issue 05: outside-click dismiss for RunnerSelect dropdown.
    // _dropdownOpen drives the overlay visibility. Wired via surfaceLoader
    // status change — see Connections block below.
    property bool _dropdownOpen: false

    signal dropdownDismissedSignal()

    // PR-7c: active risk profile — initialised on Component.onCompleted,
    // updated via onSwitchApplied from RiskProfileBridge.
    property string _activeProfile: "conservative"

    // Session-scoped UI state shared across surface page switches (issue 12).
    // Lives at Window root so it survives surfaceLoader unloads. Defaults match
    // what DeskSurface used to initialize locally. Does NOT persist across app
    // restart — survival is session-only by design.
    QtObject {
        id: sessionBag
        objectName: "sessionBag"
        property string perfSlice: "Week"
        property string throughputSlice: "Week"
        property string timeFormat: "24h"  // "24h" | "12h"
    }

    // ------------------------------------------------------------------
    // Time-format helper — converts raw ISO 8601 strings to HH:MM (24h)
    // or H:MM AM/PM (12h) based on sessionBag.timeFormat.
    // Returns "" for empty input; returns raw input on unparseable input.
    // Used by every surface that renders a stored timestamp.
    // ------------------------------------------------------------------
    function formatTimestamp(isoString, format) {
        if (!isoString) return "";
        var d = new Date(isoString);
        if (isNaN(d)) return isoString;  // unparseable; return raw
        var hh = d.getHours();
        var mm = d.getMinutes();
        if (format === "12h") {
            var ampm = hh >= 12 ? "PM" : "AM";
            var h12 = hh % 12; if (h12 === 0) h12 = 12;
            return h12 + ":" + (mm < 10 ? "0" + mm : mm) + " " + ampm;
        }
        // default 24h
        return (hh < 10 ? "0" + hh : hh) + ":" + (mm < 10 ? "0" + mm : mm);
    }

    MouseArea {
        id: dropdownOutsideClick
        anchors.fill: parent
        visible: root._dropdownOpen
        z: 9000  // Above page content, below dropdown bounds (which sit higher in z).
        // I-1: propagateComposedEvents allows mouse.accepted = false to pass
        // the click through to sibling items (surfaceLoader subtree) when the
        // click lands inside the open dropdown's bounding rect.
        propagateComposedEvents: true
        onClicked: function(mouse) {
            // Geometric exclusion: if the click is inside the open dropdown,
            // do not dismiss — let the event fall through to the dropdown row.
            var rect = (surfaceLoader.item && typeof surfaceLoader.item.runnerDropdownSceneRect === "function")
                       ? surfaceLoader.item.runnerDropdownSceneRect()
                       : Qt.rect(0, 0, 0, 0)
            var inside = rect.width > 0
                         && mouse.x >= rect.x && mouse.x <= rect.x + rect.width
                         && mouse.y >= rect.y && mouse.y <= rect.y + rect.height
            if (inside) {
                mouse.accepted = false
                return
            }
            root._dropdownOpen = false
            root.dropdownDismissedSignal()
        }
    }

    // Issue 05: connect to DeskSurface relay signals.
    // DeskSurface declares runnerDropdownOpened / runnerDropdownDismissed and
    // closeRunnerDropdown(); the Connections block targets surfaceLoader.item
    // (which is DeskSurface when the desk tab is active) and ignores unknown
    // signals on other surfaces.
    //
    // Issue 12: perfSlice / throughputSlice write-back to sessionBag.
    // When the operator changes a slice toggle inside DeskSurface, the surface
    // emits perfSliceChanged / throughputSliceChanged (Qt auto-signals for
    // Q_PROPERTY / property changes). These handlers write back into sessionBag
    // so the next DeskSurface load (on page return) can restore the selection.
    // ignoreUnknownSignals suppresses these handlers on non-Desk surfaces.
    Connections {
        target: surfaceLoader.item
        ignoreUnknownSignals: true
        function onRunnerDropdownOpened() { root._dropdownOpen = true }
        function onRunnerDropdownDismissed() { root._dropdownOpen = false }
        function onPerfSliceChanged() {
            if (surfaceLoader.item && surfaceLoader.item.perfSlice !== undefined) {
                sessionBag.perfSlice = surfaceLoader.item.perfSlice
            }
        }
        function onThroughputSliceChanged() {
            if (surfaceLoader.item && surfaceLoader.item.throughputSlice !== undefined) {
                sessionBag.throughputSlice = surfaceLoader.item.throughputSlice
            }
        }
    }

    // sessionBag.timeFormat is the single source of truth. Surfaces and the
    // drawer bind to it one-way; the drawer requests changes via
    // timeFormatRequested, which we translate into a sessionBag write here.
    Connections {
        target: riskDrawer
        function onTimeFormatRequested(format) {
            sessionBag.timeFormat = format
        }
        // RUNNER HEALTH reap-interval change → persist + apply via the controller.
        // Guarded: standalone harnesses (load-smoke) don't register the singleton.
        function onReapIntervalRequested(seconds) {
            if (typeof OrphanReaperController !== "undefined")
                OrphanReaperController.persistInterval(seconds)
        }
    }
    onDropdownDismissedSignal: {
        if (surfaceLoader.item && typeof surfaceLoader.item.closeRunnerDropdown === "function") {
            surfaceLoader.item.closeRunnerDropdown()
        }
    }

    // ------------------------------------------------------------------
    // Inline component: tab control.
    //
    // Active state is rendered as a quiet baseline rule under the label
    // (1px parchment hairline) rather than a filled pill with an oxblood
    // left border — per DESIGN_SYSTEM.md §7.8 and §10 principle 6
    // ("controls stay quiet"). The baseline-rule treatment is the
    // canonical worked example of editorial-default-for-content +
    // conventional-affordances-quiet-for-controls: clearly affordant
    // (the eye reads "this one is selected") without the loud SaaS-tab
    // dialect of a brand-accent fill. Oxblood is reserved for primary
    // buttons and row-selection rings, not nav.
    //
    // Per ADR 0035, state changes are honest signal — the baseline rule
    // appears instantly on active flip. Text color animates via
    // motion.fast within Behavior on color (in-state hover transitions).
    // ------------------------------------------------------------------

    component NavTab: Item {
        id: tabRoot
        property string label: ""
        property string surfaceId: ""
        property bool   tabEnabled: true
        readonly property bool _active: root.activeSurface === tabRoot.surfaceId

        implicitWidth:  tabText.implicitWidth + Theme.space[5] * 2 + 2
        implicitHeight: tabText.implicitHeight + Theme.space[3] * 2

        // Active baseline rule — 1px parchment hairline under the label,
        // visible width matching the text. DESIGN_SYSTEM.md §7.8.
        Rectangle {
            visible: tabRoot._active
            anchors.top:              tabText.bottom
            anchors.topMargin:        Theme.space[1]
            anchors.horizontalCenter: tabText.horizontalCenter
            width:  tabText.implicitWidth
            height: 1
            color:  Theme.color.brand.primary
        }

        Text {
            id: tabText
            anchors.centerIn: parent
            text: tabRoot.label + (tabRoot.tabEnabled ? "" : "  (soon)")
            color: !tabRoot.tabEnabled
                    ? Theme.color.text.disabled
                    : (tabRoot._active
                        ? Theme.color.text.primary
                        : (tabMouse.containsMouse
                            ? Theme.color.text.secondary
                            : Theme.color.text.muted))
            font.family:        Theme.typography.label.xs.family
            font.pixelSize:     Theme.typography.label.xs.size + 1
            font.weight:        Font.DemiBold
            font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.6
            font.capitalization: Font.AllUppercase
            Behavior on color {
                ColorAnimation { duration: Theme.motion.fast }
            }
        }

        MouseArea {
            id: tabMouse
            anchors.fill: parent
            hoverEnabled: tabRoot.tabEnabled
            cursorShape: tabRoot.tabEnabled ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: {
                if (tabRoot.tabEnabled) root.activeSurface = tabRoot.surfaceId
            }
        }
    }

    // ------------------------------------------------------------------
    // Top tab-bar
    // ------------------------------------------------------------------

    Item {
        id: topBar
        anchors {
            top:   parent.top
            left:  parent.left
            right: parent.right
        }
        height: tabRow.implicitHeight + Theme.space[3] * 2

        Row {
            id: tabRow
            anchors {
                left:           parent.left
                verticalCenter: parent.verticalCenter
                leftMargin:     Theme.space[6]
            }
            spacing: Theme.space[2]

            // Primary nav: four surfaces of the editorial-print product.
            //   FRONT   = the front page (calm digest / front-porch view)
            //   BENCH   = the strategy bench (active management)
            //   LEDGER  = paper of record (chronological log)
            //   DESK    = the trading desk (dense cockpit)
            NavTab { label: "FRONT";  surfaceId: "front" }
            NavTab { label: "BENCH";  surfaceId: "bench" }
            NavTab { label: "LEDGER"; surfaceId: "ledger" }
            NavTab { label: "DESK";   surfaceId: "desk" }
        }

        RiskStrip {
            id: statusChrome
            anchors {
                right: parent.right
                rightMargin: Theme.space[6]
                verticalCenter: parent.verticalCenter
            }
            killSwitchActive: OperationalState.killSwitchActive
            brokerStatus: OperationalState.brokerStatus
            marketOpen: OperationalState.marketOpen
            tradingMode: OperationalState.tradingMode
            lastRefreshedAt: OperationalState.lastRefreshedAt
            activeProfileName: root._activeProfile  // PR-7c: badge copy
            onBadgeClicked: riskDrawer.open = !riskDrawer.open  // PR-7c: toggle drawer
        }

        // 1px hairline divider below the bar
        Rectangle {
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.bottom: parent.bottom
            height: 1
            color:  Theme.color.border.subtle
        }
    }

    // ------------------------------------------------------------------
    // PR-7c: Elevated Posture Banner — persistent oxblood band below chrome
    // when active risk profile is "aggressive" (ADR 0054 §8).
    // ------------------------------------------------------------------

    ElevatedPostureBanner {
        id: elevatedBanner
        anchors {
            top:   topBar.bottom
            left:  parent.left
            right: parent.right
        }
        width: parent.width
        activeProfile: root._activeProfile
    }

    // ------------------------------------------------------------------
    // Surface body -- single Loader swaps source by activeSurface
    // ------------------------------------------------------------------

    Loader {
        id: surfaceLoader
        anchors {
            top:    elevatedBanner.bottom
            left:   parent.left
            right:  parent.right
            bottom: parent.bottom
        }

        // Issue 12: seed slice selections from sessionBag when a new surface
        // loads. The undefined-guard means non-Desk surfaces (which lack these
        // properties) are silently skipped — same safety pattern as the
        // Pass sessionBag into the loaded surface so it can bind one-way to
        // sessionBag.timeFormat (and any future session-scoped state).
        // perfSlice/throughputSlice remain seeded directly per existing pattern.
        onLoaded: {
            if (surfaceLoader.item && surfaceLoader.item.perfSlice !== undefined) {
                surfaceLoader.item.perfSlice = sessionBag.perfSlice
                surfaceLoader.item.throughputSlice = sessionBag.throughputSlice
            }
            if (surfaceLoader.item && surfaceLoader.item.sessionBag !== undefined) {
                surfaceLoader.item.sessionBag = sessionBag
            }
        }

        // Surface routing.  The four primary surfaces are FRONT / BENCH /
        // LEDGER / DESK.  The earlier surfaces (anchor, strategy-bank,
        // design-system) remain in the codebase and are reachable by
        // setting `activeSurface` to their ID programmatically — they're
        // simply not exposed in the primary nav anymore.
        source: {
            if (root.activeSurface === "front")          return "surfaces/FrontSurface.qml"
            if (root.activeSurface === "bench")          return "surfaces/BenchSurface.qml"
            if (root.activeSurface === "ledger")         return "surfaces/LedgerSurface.qml"
            if (root.activeSurface === "desk")           return "surfaces/DeskSurface.qml"
            // Hidden surfaces (reachable only by programmatic activeSurface assignment):
            //   "anchor"         — kill-switch reset modal (sole GUI path; ADR 0035)
            //   "design-system"  — token/theme preview (developer-internal; ADR 0035 integration smoke)
            if (root.activeSurface === "anchor")         return "surfaces/AnchorSurface.qml"
            if (root.activeSurface === "design-system")  return "surfaces/DesignSystemShowcase.qml"
            return ""  // unknown id renders the placeholder
        }

        // Stub placeholder when no source is set (Strategy Bank / Attribution).
        Item {
            anchors.fill: parent
            visible: surfaceLoader.source == ""
            Text {
                anchors.centerIn: parent
                text: "Coming soon."
                color: Theme.color.text.muted
                font.family:    Theme.typography.body.md.family
                font.pixelSize: Theme.typography.body.md.size
            }
        }
    }

    // ------------------------------------------------------------------
    // PR-7c: Risk Office Drawer (slide-in from right, z above surfaces)
    // ------------------------------------------------------------------

    // Outside-click overlay — dismisses the drawer when clicking outside it.
    MouseArea {
        id: drawerOutsideClick
        anchors.fill: parent
        visible: riskDrawer.open
        z: 8999  // below drawer (9000) but above surface content
        onClicked: riskDrawer.open = false
    }

    RiskOfficeDrawer {
        id: riskDrawer
        parent: root.contentItem   // parent to Window.contentItem so anchors work
        anchors.top:    parent.top
        anchors.bottom: parent.bottom
        anchors.right:  parent.right
        z: 10000
        activeProfile: root._activeProfile
        sessionBag: sessionBag

        // Relay switchRequested up to the bridge (RiskProfileBridge QML singleton)
        onSwitchRequested: function(target, token) {
            RiskProfileBridge.attemptSwitch(target, token)
        }
        // Task 37: route quit through AppController context property
        onQuitRequested: AppController.quitRequested()
    }

    // Wire RiskProfileBridge signals
    Connections {
        target: RiskProfileBridge
        function onSwitchApplied(name) {
            root._activeProfile = name
        }
        function onSwitchRefused(code, msg) {
            riskDrawer.showError(msg)
        }
    }

    // ------------------------------------------------------------------
    // Startup connectivity log
    // ------------------------------------------------------------------

    Component.onCompleted: {
        console.log(
            "[Milodex] Main.qml ready -- ThemeManager.theme = " + ThemeManager.theme
        )
        // PR-7c: seed active profile from bridge
        root._activeProfile = RiskProfileBridge.activeProfileName()
        // Drawer's timeFormat is a readonly mirror of sessionBag.timeFormat;
        // no seed required.
    }
}

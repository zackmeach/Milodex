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
// Strategy Bank tab (PR E): enabled; loads StrategyBankSurface.qml
// (live observability surface).
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

    // ------------------------------------------------------------------
    // Inline component: tab control (mirrors theme switcher in
    // DesignSystemShowcase.qml).  Hover and active state both use
    // motion.fast colour transitions.  ADR 0035: state changes are
    // honest signal, so the tab swap itself does not animate.
    // ------------------------------------------------------------------

    component NavTab: Item {
        id: tabRoot
        property string label: ""
        property string surfaceId: ""
        property bool   tabEnabled: true
        readonly property bool _active: root.activeSurface === tabRoot.surfaceId

        implicitWidth:  tabText.implicitWidth + Theme.space[5] * 2 + 2
        implicitHeight: tabText.implicitHeight + Theme.space[3] * 2

        Rectangle {
            anchors.fill: parent
            color: tabRoot._active
                    ? Theme.color.surface.raised
                    : (tabMouse.containsMouse && tabRoot.tabEnabled
                        ? Theme.color.surface.base
                        : "transparent")
            radius: Theme.radius.md
            Behavior on color {
                ColorAnimation { duration: Theme.motion.fast }
            }
        }

        Rectangle {
            anchors.left:   parent.left
            anchors.top:    parent.top
            anchors.bottom: parent.bottom
            width:  2
            color:  tabRoot._active ? Theme.color.brand.accent : "transparent"
            Behavior on color {
                ColorAnimation { duration: Theme.motion.fast }
            }
        }

        Text {
            id: tabText
            anchors.centerIn: parent
            text: tabRoot.label + (tabRoot.tabEnabled ? "" : "  (soon)")
            color: !tabRoot.tabEnabled
                    ? Theme.color.text.muted
                    : (tabRoot._active
                        ? Theme.color.text.primary
                        : (tabMouse.containsMouse
                            ? Theme.color.text.primary
                            : Theme.color.text.secondary))
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
    // Surface body -- single Loader swaps source by activeSurface
    // ------------------------------------------------------------------

    Loader {
        id: surfaceLoader
        anchors {
            top:    topBar.bottom
            left:   parent.left
            right:  parent.right
            bottom: parent.bottom
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
            // Hidden surfaces (kept in codebase for reference):
            if (root.activeSurface === "anchor")         return "surfaces/AnchorSurface.qml"
            if (root.activeSurface === "strategy-bank")  return "surfaces/StrategyBankSurface.qml"
            if (root.activeSurface === "bench-legacy")   return "surfaces/KanbanSurface.qml"
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
    // Startup connectivity log
    // ------------------------------------------------------------------

    Component.onCompleted: {
        console.log(
            "[Milodex] Main.qml ready -- ThemeManager.theme = " + ThemeManager.theme
        )
    }
}

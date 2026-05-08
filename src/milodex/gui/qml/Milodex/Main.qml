// Main.qml -- Milodex top-level application window.
//
// Tokens consumed (DESIGN_SYSTEM.md §3, §4):
//   color.surface.canvas  -- window background
//
// Wires:
//   - loads DesignSystemShowcase as the single top-level surface
//   - Component.onCompleted logs Qt+QML+ThemeManager connectivity
//   - engine.quit is connected to app.quit by the Python shell
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

    // ------------------------------------------------------------------
    // Background
    // ------------------------------------------------------------------

    color: Theme.color.surface.canvas

    // ------------------------------------------------------------------
    // Content: Design System Showcase
    // ------------------------------------------------------------------

    Loader {
        id: showcaseLoader
        anchors.fill: parent
        source: "surfaces/DesignSystemShowcase.qml"
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

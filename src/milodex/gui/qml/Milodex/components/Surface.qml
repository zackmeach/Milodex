// Surface.qml — Milodex default panel / card container.
//
// Tokens consumed (DESIGN_SYSTEM.md §7.4):
//   color.surface.base    — background
//   color.border.subtle   — default border
//   color.border.regular  — hover border (when interactive: true)
//   radius.lg             — corner radius
//   space[5]              — internal padding
//   motion.fast           — hover border transition (when interactive)
//
// Data expected:
//   interactive : bool — when true, border brightens on hover (default false)
//   children    : any  — arbitrary child content via default alias
//
// Implementation note: Item is used as root instead of Rectangle to avoid
// a Qt 6.11 strict module-cache issue where Rectangle.data rejects
// QQuickItem subclasses as children in registered-module components.

import QtQuick
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property bool interactive: false

    // Allow consumers to place children inside the padded content area.
    default property alias content: contentArea.children

    // ------------------------------------------------------------------
    // Background rectangle (child of Item)
    // ------------------------------------------------------------------

    Rectangle {
        id: bg
        anchors.fill: parent
        color:  Theme.color.surface.base
        radius: Theme.radius.lg
        border.color: (root.interactive && mouseArea.containsMouse)
                        ? Theme.color.border.regular
                        : Theme.color.border.subtle
        border.width: 1

        Behavior on border.color {
            ColorAnimation { duration: Theme.motion.fast }
        }
    }

    // Interactive hover via MouseArea (sibling to bg, not child of Rectangle)
    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: root.interactive
        enabled:      root.interactive
        // Propagate clicks so child interactive elements remain functional.
        propagateComposedEvents: true
        onClicked: (mouse) => mouse.accepted = false
    }

    // ------------------------------------------------------------------
    // Content area with standard padding
    // ------------------------------------------------------------------

    Item {
        id: contentArea
        anchors {
            fill:    parent
            margins: Theme.space[5]
        }
    }
}

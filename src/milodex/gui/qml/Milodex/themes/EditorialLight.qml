// EditorialLight.qml — concrete color values for the Editorial Light theme.
//
// Source of truth: docs/DESIGN_SYSTEM.md sections 3.2 and 6.1.
// Do not edit values without a corresponding doc update.
// See EditorialDark.qml for the `property var` choice rationale.

import QtQuick

QtObject {
    readonly property string name: "editorial-light"

    readonly property var color: QtObject {
        readonly property var surface: QtObject {
            readonly property string canvas: "#f5efe1"
            readonly property string base: "#ede5d4"
            readonly property string raised: "#e2d8c2"
        }
        // See EditorialDark.qml for the `default` -> `regular` rename rationale.
        readonly property var border: QtObject {
            readonly property string subtle: "#ddd2bc"
            readonly property string regular: "#c2b596"
            readonly property string emphasis: "#9a8967"
        }
        readonly property var brand: QtObject {
            readonly property string primary: "#2a2218"
            readonly property string accent: "#722f37"
        }
        readonly property var text: QtObject {
            readonly property string primary: "#2a2218"
            readonly property string secondary: "#6b5d44"
            readonly property string muted: "#8a7c5e"
            readonly property string disabled: "#bbae8c"
        }
    }

    readonly property var status: QtObject {
        readonly property string positive: "#4a7a4d"
        readonly property string warning: "#8b6510"
        readonly property string negative: "#a04020"
        readonly property string info: "#3a5474"
    }
}

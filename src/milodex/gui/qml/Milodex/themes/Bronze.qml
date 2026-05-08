// Bronze.qml — concrete color values for the Bronze theme.
//
// Source of truth: docs/DESIGN_SYSTEM.md sections 3.3 and 6.1.
// Do not edit values without a corresponding doc update.
// See EditorialDark.qml for the `property var` choice rationale.

import QtQuick

QtObject {
    readonly property string name: "bronze"

    readonly property var color: QtObject {
        readonly property var surface: QtObject {
            readonly property string canvas: "#0d0c0a"
            readonly property string base: "#19170f"
            readonly property string raised: "#22201a"
        }
        // See EditorialDark.qml for the `default` -> `regular` rename rationale.
        readonly property var border: QtObject {
            readonly property string subtle: "#28241b"
            readonly property string regular: "#3d3625"
            readonly property string emphasis: "#5a5036"
        }
        readonly property var brand: QtObject {
            readonly property string primary: "#a68063"
            readonly property string accent: "#5e8b7e"
        }
        readonly property var text: QtObject {
            readonly property string primary: "#e0d4bd"
            readonly property string secondary: "#a89070"
            readonly property string muted: "#7e7565"
            readonly property string disabled: "#3d3525"
        }
    }

    readonly property var status: QtObject {
        readonly property string positive: "#5e8b7e"
        readonly property string warning: "#c4965a"
        readonly property string negative: "#a04020"
        readonly property string info: "#6c89a3"
    }
}

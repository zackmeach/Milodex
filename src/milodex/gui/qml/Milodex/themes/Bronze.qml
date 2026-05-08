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
            // Pre-computed hover/pressed states for color.brand.accent (verdigris).
            // Verdigris has more headroom than oxblood; hover steps visibly lighter.
            readonly property string accentHover: "#72a89a"
            readonly property string accentPressed: "#4d7268"
        }
        readonly property var text: QtObject {
            readonly property string primary: "#e0d4bd"
            readonly property string secondary: "#a89070"
            readonly property string muted: "#7e7565"
            readonly property string disabled: "#3d3525"
            // onBrand: high-contrast warm white for text rendered on top of
            // color.brand.accent (verdigris #5e8b7e).  Uses a warm near-white
            // rather than the parchment cream — verdigris reads lighter than
            // oxblood, so a slightly cooler contrast holds WCAG AA.
            readonly property string onBrand: "#f0ede8"
        }
    }

    readonly property var status: QtObject {
        readonly property string positive: "#5e8b7e"
        readonly property string warning: "#c4965a"
        readonly property string negative: "#a04020"
        // negativeHover: status.negative lightened for danger button hover/pressed border.
        readonly property string negativeHover: "#c04d28"
        readonly property string info: "#6c89a3"
    }
}

// Bronze.qml — concrete color values for the Bronze theme.
//
// Source of truth: docs/DESIGN_SYSTEM.md sections 3.3 and 6.1.
// Do not edit values without a corresponding doc update.
// See EditorialDark.qml for the `property var` choice rationale.

import QtQuick

QtObject {
    readonly property string name: "bronze"

    // BRIGHTNESS PASS — 2026-05-08
    // Mirrors the Editorial Dark brightness pass: lift text + status
    // values ~5-10% in HSV value while preserving the warm-bronze hue
    // family.  Verdigris accent kept distinct from sage positive.
    readonly property var color: QtObject {
        readonly property var surface: QtObject {
            readonly property string canvas: "#0d0c0a"
            readonly property string base: "#1c1a12"
            readonly property string raised: "#27241d"
        }
        // See EditorialDark.qml for the `default` -> `regular` rename rationale.
        readonly property var border: QtObject {
            readonly property string subtle: "#2e2920"
            readonly property string regular: "#463e2b"
            readonly property string emphasis: "#665a3e"
        }
        readonly property var brand: QtObject {
            readonly property string primary: "#b58c6e"
            readonly property string accent: "#6a9a8c"
            // Pre-computed hover/pressed states for color.brand.accent (verdigris).
            // Verdigris has more headroom than oxblood; hover steps visibly lighter.
            readonly property string accentHover: "#82b5a5"
            readonly property string accentPressed: "#578073"
        }
        readonly property var text: QtObject {
            readonly property string primary: "#e8dcc5"
            readonly property string secondary: "#b89e7c"
            readonly property string muted: "#a59b86"
            readonly property string disabled: "#433b2b"
            // onBrand: high-contrast dark text on top of color.brand.accent
            // (verdigris).  Near-canvas reads as "stamped into the metal,"
            // which fits the Bronze workshop story.
            readonly property string onBrand: "#0d0c0a"
            // onCritical: high-contrast dark text rendered on top of
            // status.negative (rust) for the `critical` Button variant.
            readonly property string onCritical: "#19170f"
        }
    }

    readonly property var status: QtObject {
        // Bronze status.positive must NOT equal brand.accent (verdigris) —
        // a paper-row's accent bar would otherwise be the same hue as its
        // own status pill.  Sage borrowed from Editorial Dark family.
        readonly property string positive: "#a8c4ab"
        readonly property string warning: "#d5a566"
        readonly property string negative: "#d97550"
        // negativeHover: status.negative lightened for danger button hover/pressed border
        // and for the `critical` button variant filled-background hover.
        readonly property string negativeHover: "#e88862"
        // negativePressed: status.negative darkened for `critical` filled-background pressed.
        readonly property string negativePressed: "#b85e3d"
        readonly property string info: "#7a98b2"
    }
}

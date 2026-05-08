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
            // Bumped from #7e7565 (3.95:1 — failed AA) to #948a76 (5.26:1)
            // so muted body text on surface.base is legible.
            readonly property string muted: "#948a76"
            readonly property string disabled: "#3d3525"
            // onBrand: high-contrast dark text on top of color.brand.accent
            // (verdigris #5e8b7e).  Verdigris is light enough that DARK text
            // gives the cleanest contrast (5.09:1) — the prior warm-near-white
            // landed at 3.29:1, below AA.  A near-canvas value reads as
            // "stamped into the metal," which fits the Bronze workshop story.
            readonly property string onBrand: "#0d0c0a"
            // onCritical: high-contrast text rendered on top of
            // status.negative (rust #cd6038) for the `critical` Button
            // variant.  Bronze rust is light enough that DARK text gives
            // 4.55:1 (AA-passing), matching the workshop-nameplate
            // approach used for onBrand.
            readonly property string onCritical: "#19170f"
        }
    }

    readonly property var status: QtObject {
        // Bronze status.positive must NOT equal brand.accent (#5e8b7e
        // verdigris) — a paper-row's accent bar would otherwise be the
        // same hue as its own status pill.  Sage borrowed from Editorial
        // Dark stays inside the palette, hits 8.32:1 on surface.base, and
        // is clearly distinct from verdigris.
        readonly property string positive: "#9bb89e"
        readonly property string warning: "#c4965a"
        // Bumped from #a04020 (2.77:1 — failed AA) to #cd6038 (4.55:1).
        readonly property string negative: "#cd6038"
        // negativeHover: status.negative lightened for danger button hover/pressed border
        // and for the `critical` button variant filled-background hover.
        readonly property string negativeHover: "#df7548"
        // negativePressed: status.negative darkened for `critical` filled-background pressed.
        readonly property string negativePressed: "#ae512f"
        readonly property string info: "#6c89a3"
    }
}

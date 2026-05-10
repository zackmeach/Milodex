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
            // surface.base / surface.raised widened from a 5% step (which
            // failed to read as elevation on cream) so cards visibly lift
            // off the canvas and elevated panels visibly lift off cards.
            readonly property string base: "#ebe2cc"
            readonly property string raised: "#dccfb0"
        }
        // See EditorialDark.qml for the `default` -> `regular` rename rationale.
        readonly property var border: QtObject {
            readonly property string subtle: "#ddd2bc"
            readonly property string regular: "#c2b596"
            readonly property string emphasis: "#9a8967"
        }
        readonly property var brand: QtObject {
            // brand.primary is a deep oxblood-tobacco — distinct from
            // text.primary (deep brown #2a2218) and from brand.accent
            // (oxblood #722f37) so display headings carry brand identity
            // without colliding with body text.  Hits ~9:1 on canvas.
            readonly property string primary: "#5d2a30"
            readonly property string accent: "#722f37"
            // Pre-computed hover/pressed states for color.brand.accent (oxblood).
            // Light theme has less headroom for lightening; hover uses a modestly
            // lighter oxblood, pressed uses a slightly darker shade.
            readonly property string accentHover: "#8a3a45"
            readonly property string accentPressed: "#5d262d"
        }
        readonly property var text: QtObject {
            readonly property string primary: "#2a2218"
            // In Light, the natural type hierarchy keeps secondary darker
            // (more emphasis) and muted lighter, both still hitting WCAG
            // AA on surface.base #ebe2cc.
            readonly property string secondary: "#5e5238"
            readonly property string muted: "#6e5f3f"
            readonly property string disabled: "#bbae8c"
            // onBrand: high-contrast cream for text rendered on top of
            // color.brand.accent (oxblood).  Same value as Editorial Dark —
            // the oxblood accent is unchanged between themes.
            readonly property string onBrand: "#f5e6c4"
            // onCritical: high-contrast text rendered on top of
            // status.negative (deep rust #a04020) for the `critical`
            // Button variant.  Cream hits 5.23:1 — AA-passing.
            readonly property string onCritical: "#f5e6c4"
        }
    }

    readonly property var status: QtObject {
        // status.* tightened so each role hits >= 4.5:1 on the new
        // surface.base #ebe2cc (StatusPill renders the role color as
        // label text on a 12% tint of itself; effective contrast is
        // role-vs-surface).
        readonly property string positive: "#3d6b40"
        readonly property string warning: "#7a560d"
        readonly property string negative: "#a04020"
        // negativeHover: status.negative lightened for danger button hover/pressed border
        // and for the `critical` button variant filled-background hover.
        readonly property string negativeHover: "#c04d28"
        // negativePressed: status.negative darkened for `critical` filled-background pressed.
        readonly property string negativePressed: "#88361b"
        readonly property string info: "#3a5474"
    }

    readonly property var stage: QtObject {
        readonly property string idle: "#6f6a5c"
        readonly property string backtest: "#7a98b2"
        readonly property string paper: "#a8c4ab"
        readonly property string microLive: "#d5a566"
        readonly property string live: "#7d3540"
    }
}

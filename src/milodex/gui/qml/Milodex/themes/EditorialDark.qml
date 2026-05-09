// EditorialDark.qml — concrete color values for the Editorial Dark theme.
//
// Source of truth: docs/DESIGN_SYSTEM.md sections 3.1 and 6.1.
// Do not edit values without a corresponding doc update.
//
// This file declares only theme-varying tokens (color + status).  Type
// scale, spacing, motion, and radius are theme-invariant and live
// inline in Theme.qml per ADR 0035 Decision 2.
//
// Implementation note: nested token bags use `property var` rather than
// `property QtObject` because QML's strict-type checker on registered
// types (per qmldir) does not accept inline `QtObject { ... }` literals
// for `QObject*` properties.  `var` accepts the inline literal and
// preserves dot-access semantics for consumers.

import QtQuick

QtObject {
    readonly property string name: "editorial-dark"

    // BRIGHTNESS PASS — 2026-05-08
    // Original palette read as flat / hard-to-use from across the room.
    // This pass lifts text + status values ~5-10% in HSV value while
    // preserving hue family, so the surface keeps its editorial-print
    // restraint but reads with more snap.  Rust/oxblood separation
    // explicitly preserved (rust = system-declined, oxblood = brand-
    // commitment) — both bumped, but along their own hue axes.
    readonly property var color: QtObject {
        readonly property var surface: QtObject {
            readonly property string canvas: "#0a0907"
            readonly property string base: "#13100a"
            readonly property string raised: "#1a1611"
        }
        // Note: DESIGN_SYSTEM.md names the middle tier "border.default", but
        // `default` is a QML/JS reserved word.  We expose it as `regular`
        // here (and in EditorialLight, Bronze, and Theme.qml).  Components
        // bind to `Theme.color.border.regular` for the spec's
        // `color.border.default` token.
        readonly property var border: QtObject {
            readonly property string subtle: "#241f15"
            readonly property string regular: "#33291c"
            readonly property string emphasis: "#544532"
        }
        readonly property var brand: QtObject {
            readonly property string primary: "#ecd6a5"
            readonly property string accent: "#7d3540"
            // Pre-computed hover/pressed states for color.brand.accent (oxblood).
            // accentHover: accent lightened ~20% for primary button hover.
            // accentPressed: accent darkened ~15% for primary button pressed.
            readonly property string accentHover: "#9a4350"
            readonly property string accentPressed: "#622b34"
        }
        readonly property var text: QtObject {
            readonly property string primary: "#e4d2a8"
            readonly property string secondary: "#c4a880"
            readonly property string muted: "#9c8c6c"
            readonly property string disabled: "#43392c"
            // onBrand: high-contrast cream for text rendered on top of
            // color.brand.accent (oxblood).  Per DESIGN_SYSTEM.md §3.1.
            readonly property string onBrand: "#f5e6c4"
            // onCritical: high-contrast text rendered on top of
            // status.negative (rust) for the `critical` Button variant.
            // Editorial Dark's rust is light enough that DARK text gives
            // the cleanest AA contrast — cream lands well below AA.
            readonly property string onCritical: "#19170f"
        }
    }

    readonly property var status: QtObject {
        readonly property string positive: "#a8c4ab"
        readonly property string warning: "#d5a566"
        readonly property string negative: "#df805e"
        // negativeHover: status.negative lightened for danger button hover/pressed border
        // and for the `critical` button variant filled-background hover.
        readonly property string negativeHover: "#e89472"
        // negativePressed: status.negative darkened for `critical` filled-background pressed.
        readonly property string negativePressed: "#bd6c4f"
        readonly property string info: "#7a98b2"
    }
}

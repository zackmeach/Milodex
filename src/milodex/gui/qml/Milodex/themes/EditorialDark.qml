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

    readonly property var color: QtObject {
        readonly property var surface: QtObject {
            readonly property string canvas: "#0a0907"
            readonly property string base: "#100d09"
            readonly property string raised: "#14110d"
        }
        // Note: DESIGN_SYSTEM.md names the middle tier "border.default", but
        // `default` is a QML/JS reserved word.  We expose it as `regular`
        // here (and in EditorialLight, Bronze, and Theme.qml).  Components
        // bind to `Theme.color.border.regular` for the spec's
        // `color.border.default` token.
        readonly property var border: QtObject {
            readonly property string subtle: "#1f1a12"
            readonly property string regular: "#2a2218"
            readonly property string emphasis: "#4a3d28"
        }
        readonly property var brand: QtObject {
            readonly property string primary: "#e6cf99"
            readonly property string accent: "#722f37"
        }
        readonly property var text: QtObject {
            readonly property string primary: "#d8c5a3"
            readonly property string secondary: "#a89070"
            readonly property string muted: "#6b5d44"
            readonly property string disabled: "#3d342a"
        }
    }

    readonly property var status: QtObject {
        readonly property string positive: "#9bb89e"
        readonly property string warning: "#c4965a"
        readonly property string negative: "#d97757"
        readonly property string info: "#6c89a3"
    }
}

// Theme.qml — Milodex design-token singleton.
//
// Per ADR 0035 Decision 4 + DESIGN_SYSTEM.md section 9.1, components
// bind to `Theme.<token>` and never to raw values.  The singleton
// delegates theme-varying tokens (color, status) to whichever theme
// object the `Milodex.ThemeManager` singleton names; theme-invariant
// tokens (typography, space, motion, radius, ease) are inline below
// per ADR 0035 Decision 2.
//
// Companion: `Milodex.ThemeManager` (registered via
// milodex.gui.qml_setup.register_qml_types).  When ThemeManager is
// not registered (e.g. inspecting Theme.qml in isolation) the
// singleton falls back to Editorial Dark.

pragma Singleton

import QtQuick
import Milodex 1.0 as Milodex
import "themes" as Themes

QtObject {
    id: theme

    // ---------------------------------------------------------------
    // Active theme selection
    // ---------------------------------------------------------------

    // Read the current theme name from the registered ThemeManager
    // singleton.  The expression tolerates the singleton being absent
    // (e.g. when inspecting Theme.qml without first calling
    // register_qml_types) and falls back to Editorial Dark.
    readonly property string themeName: {
        try {
            return Milodex.ThemeManager.theme;
        } catch (e) {
            return "editorial-dark";
        }
    }

    // Concrete theme objects.  Each is a QtObject defined in
    // themes/<Name>.qml.  We instantiate all three so theme switching
    // is a pointer-swap rather than a re-instantiation.
    // Each themed value-bag is `property var` rather than
    // `property QtObject` because QML's strict type system treats
    // user-defined QtObject-rooted components (EditorialDark, etc.)
    // as not directly assignable to a `QtObject*` property; `var`
    // accepts them and lets components dot-access their nested
    // properties identically.
    readonly property var editorialDark: Themes.EditorialDark { }
    readonly property var editorialLight: Themes.EditorialLight { }
    readonly property var bronze: Themes.Bronze { }

    // ---------------------------------------------------------------
    // HOW TO ADD A NEW THEME — touch ALL FIVE of these locations:
    //
    //  1. src/milodex/gui/theme_manager.py
    //       Add the new name to KNOWN_THEMES.
    //
    //  2. src/milodex/gui/qml/Milodex/qmldir
    //       Register the new QML element, e.g.:
    //         singleton NewTheme 1.0 themes/NewTheme.qml
    //
    //  3. src/milodex/gui/qml/Milodex/themes/NewTheme.qml
    //       Create the theme file with color and status tokens.
    //
    //  4. THIS FILE (Theme.qml) — TWO sub-steps, both required:
    //       a. Add a `readonly property var newTheme: Themes.NewTheme { }`
    //          instance alongside editorialDark, editorialLight, and bronze.
    //       b. Add a case to the `activeTheme` ternary below so the new
    //          name maps to the new instance.
    //
    //  5. docs/DESIGN_SYSTEM.md
    //       Update §1.2 (theme catalog) and the relevant §3.x color tables.
    //
    // FOOTGUN — silent fallback:
    //   The `activeTheme` ternary below falls through to `editorialDark` for
    //   any unrecognised themeName.  If you add a new theme to KNOWN_THEMES
    //   and create the QML file but forget step 4b, everything will compile
    //   without errors — QML will silently render Editorial Dark instead of
    //   the new theme.  Always verify by calling set_theme("new-theme") and
    //   checking that Theme.color.brand.primary changes.
    // ---------------------------------------------------------------
    readonly property var activeTheme:
        themeName === "editorial-light" ? editorialLight
        : themeName === "bronze"        ? bronze
                                        : editorialDark

    // ---------------------------------------------------------------
    // Theme-varying token delegation
    // ---------------------------------------------------------------

    readonly property var color: activeTheme.color
    readonly property var status: activeTheme.status

    // ---------------------------------------------------------------
    // Theme-invariant tokens
    // ---------------------------------------------------------------

    // Spacing scale — DESIGN_SYSTEM.md section 4.  Numeric keys are
    // JS-friendly: use bracket access (`Theme.space[4]`) from QML
    // expressions when needed; the keys here are strings so JS object
    // semantics treat both `Theme.space[4]` and `Theme.space["4"]`
    // identically.
    readonly property var space: ({
        "0": 0,
        "1": 4,
        "2": 8,
        "3": 12,
        "4": 16,
        "5": 24,
        "6": 32,
        "7": 48,
        "8": 64
    })

    // Border radius — DESIGN_SYSTEM.md section 4.
    readonly property var radius: QtObject {
        readonly property int sm: 3
        readonly property int md: 4
        readonly property int lg: 6
        readonly property int xl: 8
        readonly property int full: 9999
    }

    // Motion durations — DESIGN_SYSTEM.md section 5.1.
    readonly property var motion: QtObject {
        readonly property int fast: 120
        readonly property int standard: 220
        readonly property int deliberate: 400
    }

    // Easing curves — DESIGN_SYSTEM.md section 5.2.  Bezier control
    // points expressed as four-element arrays so consumers can spread
    // them onto `easing.bezierCurve` (which expects an 8-element
    // array — two control points plus the implicit (0,0) and (1,1)
    // endpoints duplicated for Qt's 8-point form).  Components that
    // need an 8-element form should compose locally via
    // `easing.bezierCurve: [...Theme.ease.standard, 1, 1]` or use
    // `easing.type: Easing.BezierSpline`.
    readonly property var ease: QtObject {
        readonly property var standard: [0.4, 0, 0.2, 1]
        readonly property var editorial: [0.32, 0.72, 0, 1]
    }

    // Type roles — DESIGN_SYSTEM.md section 2.  Each role is a
    // QtObject carrying family / size / lineHeight / weight /
    // letterSpacing / uppercase / italic so components can read the
    // properties individually onto a Text node:
    //
    //     Text {
    //         font.family: Theme.typography.body.md.family
    //         font.pixelSize: Theme.typography.body.md.size
    //         font.weight: Theme.typography.body.md.weight
    //     }
    //
    // The token namespace is `typography` rather than `type` because
    // `type` is a reserved-ish identifier in some QML/JS contexts;
    // `typography` is unambiguous and reads naturally.
    readonly property var typography: QtObject {

        readonly property var display: QtObject {
            readonly property var xl: QtObject {
                readonly property string family: "Newsreader"
                readonly property int size: 64
                readonly property real lineHeight: 1.05
                readonly property int weight: Font.Medium
                readonly property bool italic: false
            }
            readonly property var lg: QtObject {
                readonly property string family: "Newsreader"
                readonly property int size: 36
                readonly property real lineHeight: 1.10
                readonly property int weight: Font.Medium
                readonly property bool italic: false
            }
            readonly property var md: QtObject {
                readonly property string family: "Newsreader"
                readonly property int size: 24
                readonly property real lineHeight: 1.20
                readonly property int weight: Font.Medium
                readonly property bool italic: false
            }
            readonly property var sm: QtObject {
                readonly property string family: "Newsreader"
                readonly property int size: 18
                readonly property real lineHeight: 1.30
                readonly property int weight: Font.Medium
                readonly property bool italic: false
            }
            readonly property var smItalic: QtObject {
                readonly property string family: "Newsreader"
                readonly property int size: 18
                readonly property real lineHeight: 1.30
                readonly property int weight: Font.Normal
                readonly property bool italic: true
            }
        }

        // Editorial deck and marginalia — italic Newsreader at body size.
        // Roles: section sub-headers ("kickers"), inline editor's-note-style
        // callouts (lifecycle-exempt, flagged-not-retired), and any text that
        // reads as commentary rather than data.
        //
        // Sized to mirror body.md (14px) so it sits at body weight in a column
        // next to data.md mono cells; italic + serif distinguishes commentary
        // from data.
        //
        // Single-line role — no `lineHeight` property; if multi-line use is
        // added later, define `lineHeight` then and wire it at every consumer.
        readonly property var deck: QtObject {
            readonly property string family: "Newsreader"
            readonly property int size: 14
            readonly property int weight: Font.Normal
            readonly property bool italic: true
        }

        readonly property var body: QtObject {
            readonly property var lg: QtObject {
                readonly property string family: "Public Sans"
                readonly property int size: 15
                readonly property real lineHeight: 1.50
                readonly property int weight: Font.Normal
                readonly property bool italic: false
            }
            readonly property var md: QtObject {
                readonly property string family: "Public Sans"
                readonly property int size: 14
                readonly property real lineHeight: 1.50
                readonly property int weight: Font.Normal
                readonly property bool italic: false
            }
            readonly property var sm: QtObject {
                readonly property string family: "Public Sans"
                readonly property int size: 13
                readonly property real lineHeight: 1.45
                readonly property int weight: Font.Normal
                readonly property bool italic: false
            }
        }

        readonly property var label: QtObject {
            readonly property var xs: QtObject {
                readonly property string family: "Public Sans"
                readonly property int size: 12
                readonly property real lineHeight: 1.40
                readonly property int weight: Font.Medium
                readonly property real letterSpacing: 0.12
                readonly property bool uppercase: true
                readonly property bool italic: false
            }
        }

        // data.* was bumped in PR D.6 to mirror body.* spacing (each
        // step 1px apart) — prior to the bump, the post-D.5 12px floor
        // had data.sm and data.xs both at 12px, distinguishable only by
        // line-height (invisible in single-line table cells).
        readonly property var data: QtObject {
            readonly property var md: QtObject {
                readonly property string family: "JetBrains Mono"
                readonly property int size: 14
                readonly property real lineHeight: 1.60
                readonly property int weight: Font.Normal
                readonly property var features: ["tnum"]
                readonly property bool italic: false
            }
            readonly property var sm: QtObject {
                readonly property string family: "JetBrains Mono"
                readonly property int size: 13
                readonly property real lineHeight: 1.60
                readonly property int weight: Font.Normal
                readonly property var features: ["tnum"]
                readonly property bool italic: false
            }
            readonly property var xs: QtObject {
                readonly property string family: "JetBrains Mono"
                readonly property int size: 12
                readonly property real lineHeight: 1.55
                readonly property int weight: Font.Normal
                readonly property var features: ["tnum"]
                readonly property bool italic: false
            }
        }
    }

    // Column widths — DESIGN_SYSTEM.md section 4.1.  Domain-specific layout
    // dimensions for tabular surfaces (Strategy Bank, attribution tables,
    // anywhere a multi-cell row appears).  Theme-invariant: declared inline.
    readonly property var column: ({
        pill: 96,         // accommodates "blocked" pill + padding
        metric: 64,       // "+1.19" or "-0.27" right-aligned
        chips: 200,       // gate chips + optional "— flagged, not retired" marginalia
                          //   (max case: 2 chips + flagged marginalia ~185px; 200 leaves headroom)
        tradeCount: 88    // "433 trades" or "27 trades" right-aligned
    })
}

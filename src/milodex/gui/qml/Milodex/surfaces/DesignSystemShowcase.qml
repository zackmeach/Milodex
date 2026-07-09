// DesignSystemShowcase.qml -- Design-system integration surface.
//
// Proves the full token/component stack is wired correctly and gives the
// operator a live theme-preview tool.  Per ADR 0035 Consequences and
// PHASE5_PLANNING.md §9, this surface doubles as the integration test for
// the application shell PR (PR D).
//
// Sections:
//   1. Theme switcher   -- row of buttons, one per theme
//   2. Typography       -- all type-scale roles with live token binding
//   3. Color tokens     -- swatch grid for every color token
//   4. Components       -- Button, StatusPill, StrategyRow in all variants
//   5. Footer           -- version + doc reference
//
// Token-binding contract: every visual property references a Theme token.
// No hardcoded hex values. No literal pixel values except 1px hairlines and
// swatch-specific structural constants (swatch rectangle height = 48 is
// structural -- not a spacing token -- and column minWidth = 140 controls
// grid layout, not spacing semantics).
//
// Flickable (from QtQuick) is used for scrolling rather than ScrollView
// (QtQuick.Controls) to avoid native-style QQuickStyleItem issues on
// Windows with Qt 6.11's strict module-type cache (same constraint as
// StatusPill.qml and Surface.qml).

import QtQuick
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Inline component: color swatch cell
    //
    // Each cell: an Item with a background Rectangle (surface.base color,
    // border.subtle border) containing a colored swatch (48px) and labels.
    // Uses Item root rather than Surface to avoid the Qt 6.11 strict
    // module-type-cache issue with registered types as inline component bases
    // (same constraint documented in StatusPill.qml and Surface.qml).
    // ------------------------------------------------------------------

    component ColorSwatchCell: Item {
        id: swatchRoot

        property string tokenName: ""
        property color  swatchColor: "transparent"

        implicitWidth:  140
        implicitHeight: swatchRect.height + swatchLabels.implicitHeight + Theme.space[3] + Theme.space[5] * 2

        // Background panel
        Rectangle {
            anchors.fill: parent
            color:        Theme.color.surface.base
            radius:       Theme.radius.lg
            border.color: Theme.color.border.subtle
            border.width: 1
        }

        // Colored swatch rectangle
        Rectangle {
            id: swatchRect
            anchors.top:         parent.top
            anchors.left:        parent.left
            anchors.right:       parent.right
            anchors.topMargin:   Theme.space[5]
            anchors.leftMargin:  Theme.space[5]
            anchors.rightMargin: Theme.space[5]
            height: 48
            radius: Theme.radius.md
            color:  swatchRoot.swatchColor
            border.color: Theme.color.border.subtle
            border.width: 1
        }

        // Token name + hex value
        Column {
            id: swatchLabels
            anchors.top:          swatchRect.bottom
            anchors.left:         parent.left
            anchors.right:        parent.right
            anchors.topMargin:    Theme.space[2]
            anchors.leftMargin:   Theme.space[5]
            anchors.rightMargin:  Theme.space[5]
            spacing: Theme.space[1]

            Text {
                width: parent.width
                text:  swatchRoot.tokenName
                color: Theme.color.text.primary
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                elide: Text.ElideRight
            }

            Text {
                width: parent.width
                text:  swatchRoot.swatchColor.toString().toLowerCase()
                color: Theme.color.text.muted
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                elide: Text.ElideRight
            }
        }
    }

    // ------------------------------------------------------------------
    // Flickable scroll container (avoids native-style ScrollView issues)
    // ------------------------------------------------------------------

    Flickable {
        id: flickable
        anchors.fill: parent
        contentWidth: width
        contentHeight: mainColumn.implicitHeight + Theme.space[6] * 2
        clip: true

        Column {
            id: mainColumn
            width: flickable.width
            padding: Theme.space[6]
            spacing: Theme.space[6]

            // ==============================================================
            // Section 1: Theme switcher
            // ==============================================================

            Column {
                width: parent.width - Theme.space[6] * 2
                spacing: Theme.space[3]

                Text {
                    text: "Themes"
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.display.md.family
                    font.pixelSize: Theme.typography.display.md.size
                    font.weight:    Theme.typography.display.md.weight
                }

                // Theme switcher reads as a tab-bar — active theme has a
                // subtle filled background + 2px left accent bar, inactive
                // themes are ghost text.  Three side-by-side primary buttons
                // would scream "destructive action" rather than "current
                // view," so we use the tab idiom instead.  Per ADR 0035 the
                // theme swap itself isn't animated; only hover/active state
                // changes use motion.fast (state changes are honest signal).
                Row {
                    spacing: Theme.space[2]

                    component ThemeTab: Item {
                        id: tabRoot
                        property string label: ""
                        property string themeId: ""
                        // Launch scope: initial release is Editorial Dark only.
                        // Editorial Light and Bronze remain as architectural
                        // themes (Theme.qml swap mechanism, full token sets,
                        // contrast tests) but the launch UI does not let the
                        // operator switch to them. The tabs render in a quiet
                        // disabled state with a "(post-launch)" suffix so the
                        // intent is legible.
                        // TODO(post-launch): remove `tabEnabled` gating once
                        // Editorial Light + Bronze parity work lands and the
                        // launch-readiness gate explicitly opens those themes.
                        property bool   tabEnabled: true
                        readonly property bool _active: ThemeManager.theme === tabRoot.themeId

                        implicitWidth:  tabText.implicitWidth + Theme.space[5] * 2 + 2
                        implicitHeight: tabText.implicitHeight + Theme.space[3] * 2

                        Rectangle {
                            id: tabBg
                            anchors.fill: parent
                            color: tabRoot._active
                                    ? Theme.color.surface.raised
                                    : (tabRoot.tabEnabled && tabMouse.containsMouse
                                        ? Theme.color.surface.base
                                        : "transparent")
                            radius: Theme.radius.md
                            Behavior on color {
                                ColorAnimation { duration: Theme.motion.fast }
                            }
                        }

                        // 2px left accent bar — visible only when active
                        Rectangle {
                            anchors.left:   parent.left
                            anchors.top:    parent.top
                            anchors.bottom: parent.bottom
                            width: 2
                            color: tabRoot._active
                                    ? Theme.color.brand.accent
                                    : "transparent"
                            Behavior on color {
                                ColorAnimation { duration: Theme.motion.fast }
                            }
                        }

                        Text {
                            id: tabText
                            anchors.centerIn: parent
                            text: tabRoot.label + (tabRoot.tabEnabled ? "" : "  (post-launch)")
                            color: !tabRoot.tabEnabled
                                    ? Theme.color.text.disabled
                                    : (tabRoot._active
                                        ? Theme.color.text.primary
                                        : (tabMouse.containsMouse
                                            ? Theme.color.text.primary
                                            : Theme.color.text.secondary))
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.md.size
                            font.weight:    Font.Medium
                            Behavior on color {
                                ColorAnimation { duration: Theme.motion.fast }
                            }
                        }

                        MouseArea {
                            id: tabMouse
                            anchors.fill: parent
                            hoverEnabled: tabRoot.tabEnabled
                            cursorShape: tabRoot.tabEnabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: {
                                if (tabRoot.tabEnabled) ThemeManager.set_theme(tabRoot.themeId)
                            }
                        }
                    }

                    ThemeTab { label: "Editorial Dark";  themeId: "editorial-dark" }
                    ThemeTab { label: "Editorial Light"; themeId: "editorial-light"; tabEnabled: false }
                    ThemeTab { label: "Bronze";          themeId: "bronze";          tabEnabled: false }
                }
            }

            // Divider
            Rectangle {
                width: parent.width - Theme.space[6] * 2
                height: 1
                color: Theme.color.border.subtle
            }

            // ==============================================================
            // Section 2: Typography
            // ==============================================================

            Column {
                width: parent.width - Theme.space[6] * 2
                spacing: Theme.space[3]

                Text {
                    text: "Typography"
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.display.md.family
                    font.pixelSize: Theme.typography.display.md.size
                    font.weight:    Theme.typography.display.md.weight
                }

                // display.xl
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "display.xl -- Newsreader 64px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Strategy Bank"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.display.xl.family
                        font.pixelSize: Theme.typography.display.xl.size
                        font.weight:    Theme.typography.display.xl.weight
                    }
                }

                // display.lg
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "display.lg -- Newsreader 36px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Strategy Bank"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.display.lg.family
                        font.pixelSize: Theme.typography.display.lg.size
                        font.weight:    Theme.typography.display.lg.weight
                    }
                }

                // display.md
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "display.md -- Newsreader 24px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Strategy Bank"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.display.md.family
                        font.pixelSize: Theme.typography.display.md.size
                        font.weight:    Theme.typography.display.md.weight
                    }
                }

                // display.sm
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "display.sm -- Newsreader 18px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Strategy Bank"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.display.sm.family
                        font.pixelSize: Theme.typography.display.sm.size
                        font.weight:    Theme.typography.display.sm.weight
                    }
                }

                // display.smItalic
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "display.smItalic -- Newsreader 18px italic"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Strategy Bank"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.display.smItalic.family
                        font.pixelSize: Theme.typography.display.smItalic.size
                        font.weight:    Theme.typography.display.smItalic.weight
                        font.italic:    Theme.typography.display.smItalic.italic
                    }
                }

                // body.lg
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "body.lg -- Public Sans 15px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Per-strategy attribution since paper open"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.body.lg.family
                        font.pixelSize: Theme.typography.body.lg.size
                        font.weight:    Theme.typography.body.lg.weight
                    }
                }

                // body.md
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "body.md -- Public Sans 14px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Per-strategy attribution since paper open"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.md.size
                        font.weight:    Theme.typography.body.md.weight
                    }
                }

                // body.sm
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "body.sm -- Public Sans 13px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Per-strategy attribution since paper open"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.weight:    Theme.typography.body.sm.weight
                    }
                }

                // label.xs
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "label.xs -- Public Sans 12px uppercase"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "Strategy Bank"
                        color: Theme.color.text.primary
                        font.family:         Theme.typography.label.xs.family
                        font.pixelSize:      Theme.typography.label.xs.size
                        font.weight:         Theme.typography.label.xs.weight
                        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }
                }

                // data.md
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "data.md -- JetBrains Mono 14px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "regime.daily.sma200_rotation.spy_shy.v1   +1.19  27t"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.data.md.family
                        font.pixelSize: Theme.typography.data.md.size
                        font.weight:    Theme.typography.data.md.weight
                        font.features:  Theme.typography.data.md.features
                    }
                }

                // data.sm
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "data.sm -- JetBrains Mono 13px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "regime.daily.sma200_rotation.spy_shy.v1   +1.19  27t"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.data.sm.family
                        font.pixelSize: Theme.typography.data.sm.size
                        font.weight:    Theme.typography.data.sm.weight
                        font.features:  Theme.typography.data.sm.features
                    }
                }

                // data.xs
                Column {
                    width: parent.width
                    spacing: Theme.space[1]
                    Text {
                        text: "data.xs -- JetBrains Mono 12px"
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                    Text {
                        text: "regime.daily.sma200_rotation.spy_shy.v1   +1.19  27t"
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.data.xs.family
                        font.pixelSize: Theme.typography.data.xs.size
                        font.weight:    Theme.typography.data.xs.weight
                        font.features:  Theme.typography.data.xs.features
                    }
                }
            }

            // Divider
            Rectangle {
                width: parent.width - Theme.space[6] * 2
                height: 1
                color: Theme.color.border.subtle
            }

            // ==============================================================
            // Section 3: Color tokens
            // ==============================================================

            Column {
                width: parent.width - Theme.space[6] * 2
                spacing: Theme.space[3]

                Text {
                    text: "Color Tokens"
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.display.md.family
                    font.pixelSize: Theme.typography.display.md.size
                    font.weight:    Theme.typography.display.md.weight
                }

                // Swatch grid -- four columns
                Grid {
                    columns: 4
                    spacing: Theme.space[3]
                    width: parent.width

                    ColorSwatchCell {
                        tokenName: "surface.canvas"
                        swatchColor: Theme.color.surface.canvas
                    }
                    ColorSwatchCell {
                        tokenName: "surface.base"
                        swatchColor: Theme.color.surface.base
                    }
                    ColorSwatchCell {
                        tokenName: "surface.raised"
                        swatchColor: Theme.color.surface.raised
                    }
                    ColorSwatchCell {
                        tokenName: "border.subtle"
                        swatchColor: Theme.color.border.subtle
                    }
                    ColorSwatchCell {
                        tokenName: "border.regular"
                        swatchColor: Theme.color.border.regular
                    }
                    ColorSwatchCell {
                        tokenName: "border.emphasis"
                        swatchColor: Theme.color.border.emphasis
                    }
                    ColorSwatchCell {
                        tokenName: "brand.primary"
                        swatchColor: Theme.color.brand.primary
                    }
                    ColorSwatchCell {
                        tokenName: "brand.accent"
                        swatchColor: Theme.color.brand.accent
                    }
                    ColorSwatchCell {
                        tokenName: "text.primary"
                        swatchColor: Theme.color.text.primary
                    }
                    ColorSwatchCell {
                        tokenName: "text.secondary"
                        swatchColor: Theme.color.text.secondary
                    }
                    ColorSwatchCell {
                        tokenName: "text.muted"
                        swatchColor: Theme.color.text.muted
                    }
                    ColorSwatchCell {
                        tokenName: "status.positive"
                        swatchColor: Theme.status.positive
                    }
                    ColorSwatchCell {
                        tokenName: "status.warning"
                        swatchColor: Theme.status.warning
                    }
                    ColorSwatchCell {
                        tokenName: "status.negative"
                        swatchColor: Theme.status.negative
                    }
                    ColorSwatchCell {
                        tokenName: "status.info"
                        swatchColor: Theme.status.info
                    }
                    ColorSwatchCell {
                        tokenName: "stage.idle"
                        swatchColor: Theme.stage.idle
                    }
                    ColorSwatchCell {
                        tokenName: "stage.backtest"
                        swatchColor: Theme.stage.backtest
                    }
                    ColorSwatchCell {
                        tokenName: "stage.paper"
                        swatchColor: Theme.stage.paper
                    }
                    ColorSwatchCell {
                        tokenName: "stage.microLive"
                        swatchColor: Theme.stage.microLive
                    }
                    ColorSwatchCell {
                        tokenName: "stage.live"
                        swatchColor: Theme.stage.live
                    }
                }
            }

            // Divider
            Rectangle {
                width: parent.width - Theme.space[6] * 2
                height: 1
                color: Theme.color.border.subtle
            }

            // ==============================================================
            // Section 4: Components
            // ==============================================================

            Column {
                width: parent.width - Theme.space[6] * 2
                spacing: Theme.space[5]

                Text {
                    text: "Components"
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.display.md.family
                    font.pixelSize: Theme.typography.display.md.size
                    font.weight:    Theme.typography.display.md.weight
                }

                // -- Buttons -------------------------------------------------
                Column {
                    width: parent.width
                    spacing: Theme.space[2]

                    Text {
                        text: "Button"
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.weight:    Font.Medium
                    }

                    Row {
                        spacing: Theme.space[3]

                        Button {
                            variant: "critical"
                            text: "Trigger Kill Switch"
                        }
                        Button {
                            variant: "primary"
                            text: "Run Backtest"
                        }
                        Button {
                            variant: "secondary"
                            text: "View Details"
                        }
                        Button {
                            variant: "ghost"
                            text: "Cancel"
                        }
                        Button {
                            variant: "danger"
                            text: "Delete Row"
                        }
                    }
                }

                // -- StatusPill ----------------------------------------------
                Column {
                    width: parent.width
                    spacing: Theme.space[2]

                    Text {
                        text: "StatusPill"
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.weight:    Font.Medium
                    }

                    Row {
                        spacing: Theme.space[3]

                        StatusPill { variant: "paper";    text: "paper" }
                        StatusPill { variant: "backtest"; text: "backtest" }
                        StatusPill { variant: "blocked";  text: "blocked" }
                        StatusPill { variant: "killed";   text: "killed" }
                    }
                }

                // -- StrategyRow ---------------------------------------------
                Column {
                    width: parent.width
                    spacing: Theme.space[2]

                    Text {
                        text: "StrategyRow"
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.weight:    Font.Medium
                    }

                    Text {
                        text: "Illustrative — demonstrates StrategyRow component variants (paper / blocked). Not a live view of the bank; the actual Strategy Bank surface lands in a subsequent Phase 5 PR."
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        font.weight:    Theme.typography.body.sm.weight
                        font.italic:    true
                        wrapMode:       Text.WordWrap
                        width:          parent.width
                    }

                    Column {
                        width: parent.width
                        spacing: Theme.space[2]

                        StrategyRow {
                            width: parent.width
                            strategyId:  "regime.daily.sma200_rotation.spy_shy.v1"
                            stage:       "paper"
                            metricValue: "+1.19"
                            tradeCount:  27
                        }
                        StrategyRow {
                            width: parent.width
                            strategyId:  "breakout.daily.atr_channel.sector_etfs.v1"
                            stage:       "paper"
                            metricValue: "+0.64"
                            tradeCount:  433
                            metricsProvenance: "read-model snapshot — not reconstructed"
                        }
                        StrategyRow {
                            width: parent.width
                            strategyId:  "meanrev.daily.bbands_lowerband.curated_largecap.v1"
                            stage:       "paper"
                            metricValue: "+0.52"
                            tradeCount:  361
                        }
                        StrategyRow {
                            width: parent.width
                            strategyId:  "momentum.daily.dual_absolute.gem_weekly.v1"
                            stage:       "blocked"
                            metricValue: "0.83"
                            tradeCount:  20
                        }
                        StrategyRow {
                            width: parent.width
                            strategyId:  "seasonality.daily.turn_of_month.spy.v1"
                            stage:       "blocked"
                            metricValue: "-0.27"
                            tradeCount:  40
                        }
                    }
                }
            }

            // Divider
            Rectangle {
                width: parent.width - Theme.space[6] * 2
                height: 1
                color: Theme.color.border.subtle
            }

            // ==============================================================
            // Section 5: Footer
            // ==============================================================

            Surface {
                width: parent.width - Theme.space[6] * 2
                height: footerText.implicitHeight + Theme.space[5] * 2

                Text {
                    id: footerText
                    anchors.centerIn: parent
                    text: "Milodex Design System v0.1 -- see docs/DESIGN_SYSTEM.md for the canonical spec."
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.body.sm.family
                    font.pixelSize: Theme.typography.body.sm.size
                }
            }

            // Bottom padding
            Item { height: Theme.space[6] }
        }
    }
}

// GateTable.qml — Reusable gate-evaluation table.
//
// Renders a list of gate-pass-or-fail rows with current/required values.
// Used by the BlockedPromotionModal in the Bench surface, and (later) by
// the Strategy Detail drill-in to show the same evidence in "current
// state" framing.
//
// The "passes / fails" column intentionally uses LANGUAGE rather than
// iconography (no ✓ / ✕) — language is the editorial-print convention
// and degrades gracefully if a font ever fails to render.
//
// Tokens consumed (DESIGN_SYSTEM.md §3, §4):
//   color.text.primary    — current/required values
//   color.text.secondary  — gate-name (italic Newsreader)
//   color.text.muted      — column headers, label captions
//   color.border.regular  — header underline rule
//   color.border.subtle   — inter-row hairlines
//   status.positive       — passes language
//   status.negative       — fails language
//   typography.body.sm    — italic gate name, "passes"/"fails" caption
//   typography.data.sm    — current/required mono values
//   typography.label.xs   — column headers
//   space[1, 3]           — vertical/horizontal padding
//
// Data expected:
//   rows : array of { name: string, current: string, required: string, passes: bool }
//
// Implementation note: Item-rooted (not Rectangle) to avoid the Qt 6.11
// strict module-cache issue documented across StatusPill / Surface /
// StrategyRow.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property var rows: []

    // ------------------------------------------------------------------
    // Layout — driven by a Column of header + per-row RowLayouts
    // ------------------------------------------------------------------

    implicitHeight: layout.implicitHeight

    Column {
        id: layout
        anchors.left:  parent.left
        anchors.right: parent.right
        spacing: 0

        // Column headers — label.xs uppercase letter-spaced
        Item {
            width:  parent.width
            height: headerRow.implicitHeight + Theme.space[3]

            RowLayout {
                id: headerRow
                anchors.left:           parent.left
                anchors.right:          parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.space[3]

                Text {
                    text: "Gate"
                    color: Theme.color.text.muted
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                    Layout.fillWidth: true
                }
                Text {
                    text: "Current"
                    color: Theme.color.text.muted
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: 88
                }
                Text {
                    text: "Required"
                    color: Theme.color.text.muted
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: 88
                }
                Item { Layout.preferredWidth: 72 }  // result column header is implicit
            }
        }

        // Header rule
        Rectangle {
            width: parent.width
            height: 1
            color: Theme.color.border.regular
        }

        // Data rows — Repeater over the rows array
        Repeater {
            model: root.rows
            delegate: Item {
                width:  layout.width
                height: rowLayout.implicitHeight + Theme.space[3] * 2

                Rectangle {
                    anchors.bottom: parent.bottom
                    width:  parent.width
                    height: 1
                    color:  Theme.color.border.subtle
                }

                RowLayout {
                    id: rowLayout
                    anchors.left:           parent.left
                    anchors.right:          parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Theme.space[3]

                    // Gate name — italic Newsreader
                    Text {
                        text: modelData.name
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.md.size
                        font.italic:    true
                        Layout.fillWidth: true
                    }

                    // Current value — mono tabular
                    Text {
                        text: modelData.current
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.data.sm.family
                        font.pixelSize: Theme.typography.data.sm.size
                        font.features:  Theme.typography.data.sm.features
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: 88
                    }

                    // Required value — mono tabular
                    Text {
                        text: modelData.required
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.data.sm.family
                        font.pixelSize: Theme.typography.data.sm.size
                        font.features:  Theme.typography.data.sm.features
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: 88
                    }

                    // Result — language not iconography
                    Text {
                        text: modelData.passes ? "passes" : "fails"
                        color: modelData.passes ? Theme.status.positive
                                                : Theme.status.negative
                        font.family:        Theme.typography.label.xs.family
                        font.pixelSize:     Theme.typography.label.xs.size
                        font.weight:        modelData.passes ? Font.Medium : Font.DemiBold
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: 72
                    }
                }
            }
        }
    }
}

// BenchModal.qml — Shared surface treatment for the three Bench modals.
//
// Three Bench modal flavors (per the brief §8):
//
//   Pattern A — Blocked promotion (gate failure)
//       Top-border:  status.negative (rust)  -- "system declined"
//   Pattern B — Typed-confirm to/from live
//       Top-border:  brand.accent (oxblood)  -- "brand-level commitment"
//   Pattern C — Consequence-confirm (re-test, demote not crossing live)
//       Top-border:  status.negative (rust)
//
// All three share: full-page overlay, centered modal box, eyebrow,
// Newsreader title, italic prose paragraph, body slot, action footer
// with hairline separator above.  This component owns the shared chrome
// and exposes two slots (body + actions) for callers to populate.
//
// Public API:
//   topBorderColor : color    — top accent color (rust or oxblood)
//   eyebrowText    : string   — small uppercase eyebrow above the title
//   eyebrowColor   : color    — eyebrow color (default text.muted)
//   titleText      : string   — Newsreader display title
//   proseText      : string   — italic Newsreader paragraph; "" hides
//   default property alias bodyContent  → bodyColumn.children   (vertical stack)
//   property alias        actionContent → actionRow.children    (horizontal row)
//   signal dismissed()                  — overlay click

import QtQuick
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property color  topBorderColor: Theme.status.negative
    property string eyebrowText: ""
    property color  eyebrowColor: Theme.color.text.muted
    property string titleText: ""
    property string proseText: ""

    default property alias bodyContent:   bodyColumn.children
    property alias         actionContent: actionRow.children

    signal dismissed()

    // ------------------------------------------------------------------
    // Overlay — full-screen tinted surface; click outside box dismisses
    // ------------------------------------------------------------------

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(Theme.color.surface.canvas.r,
                       Theme.color.surface.canvas.g,
                       Theme.color.surface.canvas.b,
                       0.82)

        MouseArea {
            anchors.fill: parent
            onClicked: root.dismissed()
        }
    }

    // ------------------------------------------------------------------
    // Modal box — centered, max-width 620, contents-driven height
    // ------------------------------------------------------------------

    Rectangle {
        id: box
        anchors.centerIn: parent
        width:  Math.min(parent.width  - Theme.space[6] * 2, 620)
        // Height = content + top/bottom modal padding + footer separator + footer + footer padding
        height: contentBlock.implicitHeight
              + footerBlock.implicitHeight
              + Theme.space[6] * 2

        color:  Theme.color.surface.base
        radius: Theme.radius.lg
        border.color: Theme.color.border.regular
        border.width: 1

        // Swallow clicks on the box (don't dismiss)
        MouseArea {
            anchors.fill: parent
        }

        // -- Top accent border (semantic color) --
        Rectangle {
            anchors.top:    parent.top
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.topMargin:    1
            anchors.leftMargin:   1
            anchors.rightMargin:  1
            height: 2
            color:  root.topBorderColor
        }

        // -- Top content block (eyebrow / title / prose / body) --
        Column {
            id: contentBlock
            anchors.top:     parent.top
            anchors.left:    parent.left
            anchors.right:   parent.right
            anchors.topMargin:    Theme.space[6]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[6]
            spacing: Theme.space[3]

            Text {
                visible: root.eyebrowText.length > 0
                width:   parent.width
                text:    root.eyebrowText
                color:   root.eyebrowColor
                font.family:        Theme.typography.label.xs.family
                font.pixelSize:     Theme.typography.label.xs.size
                font.weight:        Theme.typography.label.xs.weight
                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                font.capitalization: Font.AllUppercase
            }

            Text {
                visible: root.titleText.length > 0
                width:   parent.width
                text:    root.titleText
                color:   Theme.color.text.primary
                font.family:    Theme.typography.display.lg.family
                font.pixelSize: 28
                font.weight:    Font.Medium
                wrapMode:       Text.WordWrap
            }

            Text {
                visible:  root.proseText.length > 0
                width:    parent.width
                text:     root.proseText
                color:    Theme.color.text.secondary
                font.family:    Theme.typography.body.md.family
                font.pixelSize: Theme.typography.body.md.size + 1
                font.italic:    true
                wrapMode:       Text.WordWrap
            }

            // Body slot — caller populates via the default property alias
            Column {
                id: bodyColumn
                width:   parent.width
                spacing: Theme.space[4]
            }
        }

        // -- Footer block (separator + action row) --
        Item {
            id: footerBlock
            anchors.bottom: parent.bottom
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.bottomMargin: Theme.space[5]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[6]
            implicitHeight: actionRow.implicitHeight + Theme.space[4] + 1
            visible: actionRow.children.length > 0

            Rectangle {
                id: footerSeparator
                anchors.top:   parent.top
                anchors.left:  parent.left
                anchors.right: parent.right
                height: 1
                color:  Theme.color.border.regular
            }

            Row {
                id: actionRow
                anchors.top:   footerSeparator.bottom
                anchors.right: parent.right
                anchors.topMargin: Theme.space[4]
                spacing: Theme.space[2]
            }
        }
    }
}

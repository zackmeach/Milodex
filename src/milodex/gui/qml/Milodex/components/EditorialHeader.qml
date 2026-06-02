// EditorialHeader.qml — the shared editorial masthead for a surface.
//
// The recurring header idiom: an uppercase eyebrow (small-caps label), a
// display title with a brand-accent period ornament, an italic standfirst/
// deck, and a hairline rule below. Extracted from LedgerSurface's re-roll.
// DeskSurface keeps its bespoke inline title|standfirst RowLayout (a distinct
// idiom not shared elsewhere); FrontSurface keeps its bespoke session-dot /
// date-as-title masthead (display.xl). Both still adopt ScrollSurface.
//
// Public API:
//   eyebrow : string        — uppercase kicker above the title (label.xs muted)
//   title   : string        — display title (display.lg, brand.primary)
//   standfirst : string     — italic deck (body.lgPlus, text.secondary)
//   ornament : string       — accent glyph after the title (default ".")
//   ornamentColor : color   — ornament color (default brand.accent)
//   hairlineBelow : bool    — render a border.regular hairline rule (default true)
//   spacing : real          — vertical rhythm between eyebrow/title/standfirst
//                             (default Theme.space[5] = 24, matching the master
//                             Ledger masthead). When hairlineBelow, a
//                             Theme.space[3] (12) pre-hairline gap is added so
//                             the standfirst→hairline distance is 24+12+24 = 60,
//                             pixel-identical to master.
//   standfirstWidthFactor : real — fraction of width the standfirst occupies
//                             (default 1.0; Ledger uses 0.78 for measure).
//
//   default eyebrowSlot     — content slot in the eyebrow row, for surfaces
//                             that inject a richer eyebrow (e.g. a session dot
//                             + computed line) alongside or instead of plain
//                             `eyebrow` text. Plain-text callers leave it empty
//                             and set `eyebrow`.
//
// Tokens consumed:
//   color.brand.primary / .accent — title / ornament
//   color.text.muted / .secondary — eyebrow / standfirst
//   color.border.regular          — hairline rule
//   typography.label.xs           — eyebrow
//   typography.display.lg         — title + ornament
//   typography.body.lgPlus        — standfirst

import QtQuick
import Milodex 1.0

Column {
    id: root

    property string eyebrow: ""
    property string title: ""
    property string standfirst: ""
    property string ornament: "."
    property color ornamentColor: Theme.color.brand.accent
    property bool hairlineBelow: true
    property real standfirstWidthFactor: 1.0

    default property alias eyebrowSlot: eyebrowSlotArea.children

    width: parent ? parent.width : implicitWidth
    spacing: Theme.space[5]

    // -- Eyebrow row: plain text and/or an injected slot.
    Row {
        width: parent.width
        spacing: Theme.space[2]
        visible: root.eyebrow !== "" || eyebrowSlotArea.children.length > 0

        Text {
            visible: root.eyebrow !== ""
            text: root.eyebrow
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }

        Item {
            id: eyebrowSlotArea
            anchors.verticalCenter: parent.verticalCenter
            implicitWidth:  childrenRect.width
            implicitHeight: childrenRect.height
        }
    }

    // -- Title + accent ornament.
    Row {
        spacing: 0

        Text {
            text:  root.title
            color: Theme.color.brand.primary
            font.family:    Theme.typography.display.lg.family
            font.pixelSize: Theme.typography.display.lg.size
            font.weight:    Theme.typography.display.lg.weight
            font.letterSpacing: -0.6
        }
        Text {
            visible: root.ornament !== ""
            text:  root.ornament
            color: root.ornamentColor
            font.family:    Theme.typography.display.lg.family
            font.pixelSize: Theme.typography.display.lg.size
            font.weight:    Theme.typography.display.lg.weight
        }
    }

    // -- Standfirst (stacked under the title).
    Text {
        visible: root.standfirst !== ""
        width: parent.width * root.standfirstWidthFactor
        text: root.standfirst
        color: Theme.color.text.secondary
        font.family:    Theme.typography.body.lgPlus.family
        font.pixelSize: Theme.typography.body.lgPlus.size
        font.italic:    true
        wrapMode:       Text.WordWrap
        lineHeight:     1.5
    }

    // -- Pre-hairline gap. With the Column at space[5] rhythm this makes the
    // standfirst→hairline distance 24 + 12 + 24 = 60, matching the master
    // Ledger masthead (which placed an Item{height: space[3]} before its rule).
    Item {
        visible: root.hairlineBelow
        width:  parent.width
        height: Theme.space[3]
    }

    // -- Optional hairline rule below the masthead.
    Rectangle {
        visible: root.hairlineBelow
        width:  parent.width
        height: 1
        color:  Theme.color.border.regular
    }
}

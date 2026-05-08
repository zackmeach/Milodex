// StrategyRow.qml — Milodex strategy-bank list row.
//
// Tokens consumed (DESIGN_SYSTEM.md §7.3):
//   color.surface.base        — default background
//   color.surface.raised      — hover background
//   color.border.subtle       — default border
//   color.border.regular      — hover border
//   color.brand.accent        — selected left-accent bar (2px)
//   color.text.muted          — trade-count muted color + marginalia color
//   color.text.primary        — strategy ID + metric text
//   status.negative           — gate-failure chip color
//   typography.data.md        — strategy ID + metric text
//   typography.data.sm        — trade count text
//   typography.data.xs        — gate-failure chip text + audit asterisk
//   typography.deck           — inline editorial marginalia (note, flagged-not-retired)
//   space[1], space[2], space[3], space[5] — padding + inter-column gap
//   radius.md                 — row corner radius
//   radius.sm                 — gate-failure chip radius
//   motion.fast               — hover transition
//
// StatusPill consumes: status.positive / .info / .warning / .negative,
//   typography.label.xs, radius.sm, space[1], space[2]
//
// Data expected:
//   strategyId         : string   — dotted strategy identifier (e.g. "momentum.breakout.v1")
//   stage              : string   — "paper" | "backtest" | "blocked" | "killed"
//   metricValue        : string   — pre-formatted metric string (e.g. "+1.19")
//   tradeCount         : int      — trade count (e.g. 433)
//   selected           : bool     — selected state (default false)
//   note               : string   — optional inline italic serif marginalia after strategyId
//                                   (e.g. "lifecycle exempt").  Lower-case; reads as commentary.
//   gateFailures       : var      — optional list of gate-code strings (e.g. ["S", "D"]);
//                                   each code renders as a single capital letter inside a
//                                   framed chip (no literal brackets — the chip frame is the bracket)
//   auditFlag          : bool     — optional; when true renders a "*" superscript after strategyId
//   flagFailingNotRetired : bool  — optional; when true renders inline italic serif marginalia
//                                   "flagged, not retired" alongside gate chips
//   signal clicked()
//
// MOTION DISCIPLINE (DESIGN_SYSTEM.md §5.3, §8):
//   Numeric metric / trade-count values must NOT animate from one value
//   to another.  Row background/border may animate; values update instantly.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property string strategyId: ""
    property string stage: "paper"
    property string metricValue: ""
    property int    tradeCount: 0
    property bool   selected: false

    // Optional extensions — existing call sites that omit these see no change.
    // note: optional inline marginalia rendered after the strategy ID
    //   (and after the audit asterisk if present) in italic Newsreader at
    //   body size.  Lower-case text, text.muted color.  Used for editor's-note
    //   callouts like "lifecycle exempt" — should read as commentary, not data.
    property string note: ""
    // gateFailures: list of short gate-code strings rendered as inline chips
    //   between the metric and tradeCount columns.  Each chip renders the code
    //   letter inside a framed pill — no literal brackets; the chip frame is
    //   the bracket.
    //   Typical use: ["S", "D", "N"] for blocked strategies (ADR 0009).
    property var    gateFailures: []
    // auditFlag: when true, renders a "*" superscript to the right of strategyId.
    //   Signals a manual audit trail event (ADR 0032).
    property bool   auditFlag: false
    // flagFailingNotRetired: when true, renders inline italic serif marginalia
    //   "— flagged, not retired" alongside the gate chips.  Used for
    //   dual_absolute.gem_weekly per governance callout in STRATEGY_BANK.md —
    //   strategy is kept at backtest pending a methodology decision, not retired.
    //   Renders as editorial commentary (deck token), not as a warning badge.
    property bool   flagFailingNotRetired: false

    signal clicked()

    // ------------------------------------------------------------------
    // Sizing
    // ------------------------------------------------------------------

    implicitWidth:  400
    implicitHeight: rowLayout.implicitHeight + Theme.space[2] * 2

    // ------------------------------------------------------------------
    // Background rectangle (child of Item)
    // ------------------------------------------------------------------

    Rectangle {
        id: bg
        anchors.fill: parent
        color:  (mouseArea.containsMouse || root.selected) ? Theme.color.surface.raised
                                                           : Theme.color.surface.base
        radius: Theme.radius.md
        border.color: mouseArea.containsMouse ? Theme.color.border.regular
                                              : Theme.color.border.subtle
        border.width: 1

        Behavior on color {
            ColorAnimation { duration: Theme.motion.fast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.motion.fast }
        }
    }

    // MouseArea for hover + click
    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        onClicked:    root.clicked()
    }

    // ------------------------------------------------------------------
    // Selected left accent bar (2px, color.brand.accent)
    // ------------------------------------------------------------------

    Rectangle {
        id: selectedAccent
        visible:       root.selected
        width:         2
        anchors.left:         parent.left
        anchors.top:          parent.top
        anchors.bottom:       parent.bottom
        anchors.topMargin:    Theme.radius.md
        anchors.bottomMargin: Theme.radius.md
        color:  Theme.color.brand.accent
        // If the bar isn't visually appearing on selected state, verify:
        // (a) the parent row's bg Rectangle isn't overdrawing it — z-order
        //     is correct here: bg is the first child, selectedAccent is later,
        //     so selectedAccent naturally paints on top.
        // (b) brand.accent has sufficient contrast with surface.raised on
        //     Editorial Dark (brand.accent = #C9A85C mustard vs surface.raised
        //     = near-black — should be clearly visible).
    }

    // ------------------------------------------------------------------
    // Row layout — RowLayout for fixed-width column alignment
    // ------------------------------------------------------------------

    RowLayout {
        id: rowLayout
        anchors.left:           parent.left
        anchors.right:          parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin:     Theme.space[3]
        anchors.rightMargin:    Theme.space[3]
        spacing: Theme.space[5]

        // Strategy ID column — mono, fills remaining width with elision.
        // Uses an inner Row so the strategy ID, audit asterisk, and inline note
        // flow left-to-right based on actual content widths.  An anchor-based
        // layout here previously had a silent overflow vector (idText.right
        // resolved to parent.right regardless of glyph content, sending noteText
        // past the column boundary into the adjacent StatusPill column).
        //
        // `clip: true` on the column is a backstop: even if a future change
        // reintroduces an over-wide child, the overflow stays inside this column.
        Item {
            id: idColumn
            Layout.fillWidth: true
            implicitHeight: idRow.implicitHeight
            clip: true

            Row {
                id: idRow
                anchors.left:           parent.left
                anchors.right:          parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 0

                Text {
                    id: idText
                    // Explicit width so elision triggers when the column is narrow.
                    // Reserve space for the audit asterisk and the inline note when
                    // they're visible; otherwise consume all available width.
                    width: Math.min(
                        implicitWidth,
                        idRow.width
                            - (auditAsterisk.visible ? auditAsterisk.implicitWidth : 0)
                            - (noteText.visible ? noteText.implicitWidth + Theme.space[2] : 0)
                    )
                    text:  root.strategyId
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.data.md.family
                    font.pixelSize: Theme.typography.data.md.size
                    font.weight:    Theme.typography.data.md.weight
                    font.features:  Theme.typography.data.md.features
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }

                // Audit-trail asterisk (ADR 0032). Rendered as a "*" after the
                // strategy ID when auditFlag is true. Hover/tooltip is out of
                // scope for PR E.
                Text {
                    id: auditAsterisk
                    visible:        root.auditFlag
                    text:           "*"
                    color:          Theme.color.text.muted
                    font.family:    Theme.typography.data.xs.family
                    font.pixelSize: Theme.typography.data.xs.size
                }

                // Inline editorial marginalia — italic Newsreader at body size,
                // prefixed with em-dash. Used for "lifecycle exempt" and similar
                // editor's-note callouts. Lower-case is intentional (commentary,
                // not bureaucratic label). See DESIGN_SYSTEM.md §6.5.
                Text {
                    id: noteText
                    visible:     root.note !== ""
                    leftPadding: Theme.space[2]
                    text:        "— " + root.note
                    color:       Theme.color.text.muted
                    font.family:    Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.weight:    Theme.typography.deck.weight
                    font.italic:    Theme.typography.deck.italic
                    elide:          Text.ElideRight
                }
            }
        }

        // Stage pill column — fixed-width slot, pill itself sizes to its label
        // so the capsule shape (DESIGN_SYSTEM.md §6.3) is preserved. Wrapping
        // the StatusPill in an Item lets RowLayout allocate the column width
        // (Theme.column.pill) without forcing the pill background to stretch
        // across the whole slot.
        Item {
            Layout.preferredWidth: Theme.column.pill
            Layout.fillHeight: true

            StatusPill {
                id: stagePill
                variant: root.stage
                text:    root.stage
                anchors.left:           parent.left
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        // Primary metric — data.md mono, right-aligned
        Text {
            id: metricText
            text:  root.metricValue
            color: Theme.color.text.primary
            font.family:    Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.weight:    Theme.typography.data.md.weight
            font.features:  Theme.typography.data.md.features
            verticalAlignment:   Text.AlignVCenter
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.metric
        }

        // Gate-failure chips (ADR 0009) + optional "flagged, not retired" marginalia.
        // Rendered between metric and tradeCount columns when gateFailures is
        // non-empty.  Each chip renders a single capital letter (S, D, or N) inside
        // a framed pill — the chip frame is the bracket; no literal [/] characters.
        // The flagFailingNotRetired text sits inline in the same Row so that the
        // dual_absolute "flagged, not retired" governance note is always adjacent
        // to the failure codes.
        Row {
            id: chipsRow
            visible: root.gateFailures.length > 0 || root.flagFailingNotRetired
            spacing: Theme.space[1]
            Layout.preferredWidth: visible ? implicitWidth : 0
            // Vertical centering inside RowLayout uses Layout.alignment, NOT
            // a verticalAlignment property — Row has no such property and
            // assigning it errors at QML load (silent: chipsRow's parent
            // StrategyRow becomes "Type unavailable", invalidating every
            // surface that imports it).
            Layout.alignment: Qt.AlignVCenter

            Repeater {
                model: root.gateFailures
                delegate: Item {
                    // Horizontal padding uses space[2] (8px each side = 16px total)
                    // so a single-letter chip reads at a deliberate width — dropping
                    // the literal [/] brackets means the chip now holds one character,
                    // and space[1]*2 (4px/side) was too narrow for a framed pill.
                    // Vertical padding stays at space[1]*2 — letters don't need the
                    // same horizontal breathing room.
                    implicitWidth:  chipLabel.implicitWidth + Theme.space[2] * 2
                    implicitHeight: chipLabel.implicitHeight + Theme.space[1] * 2
                    anchors.verticalCenter: parent.verticalCenter

                    Rectangle {
                        anchors.fill: parent
                        radius: Theme.radius.sm
                        color:        Qt.rgba(Theme.status.negative.r,
                                              Theme.status.negative.g,
                                              Theme.status.negative.b, 0.12)
                        border.color: Qt.rgba(Theme.status.negative.r,
                                              Theme.status.negative.g,
                                              Theme.status.negative.b, 0.30)
                        border.width: 1
                    }

                    Text {
                        id: chipLabel
                        anchors.centerIn: parent
                        // The chip frame is the bracket — no literal [/] characters.
                        // The single uppercase letter sits centred inside the pill.
                        text:  modelData
                        color: Theme.status.negative
                        font.family:    Theme.typography.data.xs.family
                        font.pixelSize: Theme.typography.data.xs.size
                        font.features:  Theme.typography.data.xs.features
                    }
                }
            }

            // "flagged, not retired" marginalia — italic Newsreader (deck token),
            // prefixed with an em-dash.  Shares the same editorial-commentary
            // treatment as the lifecycle-exempt note on the strategy-ID column:
            // both are editor's-note callouts, not data fields or warning badges.
            // The mustard warning chip was replaced here because it read as scolding
            // (a bright warning widget on a row that the PM deliberately chose to
            // keep rather than retire) — italic serif reads as "editorial aside."
            Text {
                visible: root.flagFailingNotRetired
                anchors.verticalCenter: parent.verticalCenter
                text:  "— flagged, not retired"
                color: Theme.color.text.muted
                font.family:    Theme.typography.deck.family
                font.pixelSize: Theme.typography.deck.size
                font.weight:    Theme.typography.deck.weight
                font.italic:    Theme.typography.deck.italic
                leftPadding: Theme.space[2]
            }
        }

        // Trade count — data.sm mono, muted, right-aligned
        Text {
            id: tradeCountText
            text:  root.tradeCount + " trades"
            color: Theme.color.text.muted
            font.family:    Theme.typography.data.sm.family
            font.pixelSize: Theme.typography.data.sm.size
            font.weight:    Theme.typography.data.sm.weight
            font.features:  Theme.typography.data.sm.features
            verticalAlignment:   Text.AlignVCenter
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.tradeCount
        }

        // (badge column removed in PR E polish pass — lifecycle-exempt callout
        //  moved to inline italic Newsreader marginalia in the idColumn via the
        //  `note` property; see noteText above.  The 96px fixed column created
        //  row-width asymmetry and read as a bureaucratic stamp rather than
        //  editorial commentary.)
    }
}

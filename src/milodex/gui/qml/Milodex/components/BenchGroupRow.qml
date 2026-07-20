// BenchGroupRow.qml — template-group rollup row for the Bench vertical ledger.
//
// Group-rollup read side (founder IA decision): a bench row is a TEMPLATE
// GROUP — every instance sharing {family}.{template} rolls up into one row
// (e.g. meanrev.rsi2.intraday groups its per-ETF variants; a 1-instance
// group reads like today's flat row). The row shows the group display name,
// a family badge, the instance count, a stage-mix summary, and headline
// stats from the best instance at the group's stage. All values arrive
// pre-computed from the Python read model (bench_grouping.py) — no rollup
// logic is re-derived here.
//
// The group row is presentation-only:
//   - clicking anywhere on it emits toggleRequested(); BenchSurface expands
//     or collapses the instance roster (per-session state, default collapsed);
//   - it carries NO action menu — Initiate Backtest / Open Evidence / promote
//     affordances stay attached to roster instances (BenchRow delegates);
//   - no model mutation of any kind.
//
// Column geometry mirrors BenchRow.qml's STABLE COLUMN GEOMETRY CONTRACT:
// fixed Theme.column.* widths, right-anchored chain from the row's right
// edge inward, strategy block fills the residual. The same widths run in the
// BenchSurface section header — do not change one file without the others.

import QtQuick
import Milodex 1.0

Item {
    id: root

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    property string displayName: ""
    property string groupKey: ""
    property string family: ""
    property string stage: "backtest"
    property int instanceCount: 0
    property string stageMixLabel: ""

    // Headline metrics — formatted strings from the best instance at the
    // group's stage (formatted by BenchSurface via the shared Formatters
    // paths; read-model-snapshot provenance, same as BenchRow).
    property string sharpe: "-"
    property string maxDD: "-"
    property string tradeCount: "-"

    // Roster expansion state — owned by BenchSurface (per-session only).
    property bool expanded: false

    signal toggleRequested()

    readonly property bool _isIdle: stage === "idle"
    readonly property bool _isLive: stage === "live"

    // -----------------------------------------------------------------------
    // Sizing
    // -----------------------------------------------------------------------

    implicitHeight: 78

    // -----------------------------------------------------------------------
    // LIVE lead-story treatment — 5%-alpha brand.accent wash + 2px left
    // border, matching BenchRow (bench-brief §5).
    // -----------------------------------------------------------------------

    Rectangle {
        id: liveBorder
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: _isLive ? 2 : 0
        color: Theme.color.brand.accent
    }

    Rectangle {
        anchors.fill: parent
        anchors.leftMargin: liveBorder.width
        color: {
            if (_isLive) {
                return Qt.rgba(0.49, 0.21, 0.25, 0.05)   // brand.accent ≈ #7d3540 at ~5%
            }
            if (groupClickArea.containsMouse) {
                return Qt.rgba(
                    Theme.color.surface.raised.r,
                    Theme.color.surface.raised.g,
                    Theme.color.surface.raised.b,
                    0.42
                )
            }
            return "transparent"
        }
        Behavior on color { ColorAnimation { duration: Theme.motion.fast } }
    }

    // Whole-row click toggles the roster. No geometric exclusion needed —
    // a group row has no drag handle and no per-row menu.
    MouseArea {
        id: groupClickArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggleRequested()
    }

    // -----------------------------------------------------------------------
    // Gutter (col 1): disclosure glyph — same quiet treatment as the
    // BenchRow drag-handle glyph (mono, text.muted, opacity states).
    // -----------------------------------------------------------------------

    Item {
        id: discloseSlot
        width: Theme.space[5]
        anchors.left: parent.left
        anchors.leftMargin: liveBorder.width
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        Text {
            anchors.centerIn: parent
            text: root.expanded ? "−" : "+"
            color: Theme.color.text.muted
            opacity: groupClickArea.containsMouse || root.expanded ? 0.65 : 0.30
            font.family: Theme.typography.data.sm.family
            font.pixelSize: Theme.typography.data.sm.size
            font.features: Theme.typography.data.sm.features
            Behavior on opacity { NumberAnimation { duration: Theme.motion.fast } }
        }
    }

    // -----------------------------------------------------------------------
    // Col 7 (rightmost): roster toggle slot — keeps group rows on the shared
    // column grid. Folio-style 1-px rule + quiet uppercase hint on hover.
    // No menu here: actions belong to roster instances.
    // -----------------------------------------------------------------------

    Item {
        id: actionSlot
        anchors.right: parent.right
        anchors.rightMargin: Theme.space[3]
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.column.benchAction
        height: 78   // matches BenchGroupRow.implicitHeight

        opacity: groupClickArea.containsMouse ? 0.45 : (root.expanded ? 0.45 : 0)
        Behavior on opacity { NumberAnimation { duration: Theme.motion.fast } }

        Rectangle {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 1
            color: Theme.color.border.regular
        }

        Text {
            anchors.centerIn: parent
            text: root.expanded ? "collapse" : "expand"
            color: Theme.color.text.muted
            font.family: Theme.typography.label.xs.family
            font.pixelSize: Theme.typography.label.xs.size
            font.weight: Theme.typography.label.xs.weight
            font.letterSpacing: Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
    }

    // -----------------------------------------------------------------------
    // Col 6: instance count + stage-mix summary (fixed, right-anchored)
    // -----------------------------------------------------------------------

    Item {
        id: statusCol
        anchors.right: actionSlot.left
        anchors.rightMargin: Theme.space[4]
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.column.benchStatus
        height: statusContent.implicitHeight

        Column {
            id: statusContent
            width: parent.width
            spacing: 3

            Text {
                width: parent.width
                text: root.instanceCount === 1
                      ? "1 instance"
                      : root.instanceCount + " instances"
                color: Theme.color.text.secondary
                font.family: Theme.typography.deck.family
                font.pixelSize: Theme.typography.deck.size
                font.italic: true
                elide: Text.ElideRight
            }

            Text {
                width: parent.width
                text: root.stageMixLabel
                color: Theme.color.text.muted
                font.family: Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features: Theme.typography.data.xs.features
                elide: Text.ElideRight
                visible: root.stageMixLabel.length > 0
            }
        }
    }

    // ---- col 5: Trade count (fixed, right-anchored) -------------------
    Text {
        id: tradesText
        anchors.right: statusCol.left
        anchors.rightMargin: Theme.space[4]
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.column.benchMetric
        text: root.tradeCount
        color: root._isIdle
               ? Theme.color.text.disabled
               : Theme.color.text.primary
        font.family: Theme.typography.data.md.family
        font.pixelSize: Theme.typography.data.md.size
        font.features: Theme.typography.data.md.features
        horizontalAlignment: Text.AlignRight
    }

    // ---- col 4: Max drawdown (fixed, right-anchored) ------------------
    Text {
        id: maxDDText
        anchors.right: tradesText.left
        anchors.rightMargin: Theme.space[4]
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.column.benchMetric
        text: root.maxDD
        color: root._isIdle
               ? Theme.color.text.disabled
               : Theme.color.text.primary
        font.family: Theme.typography.data.md.family
        font.pixelSize: Theme.typography.data.md.size
        font.features: Theme.typography.data.md.features
        horizontalAlignment: Text.AlignRight
    }

    // ---- col 3: Sharpe (fixed, right-anchored) ------------------------
    Text {
        id: sharpeText
        anchors.right: maxDDText.left
        anchors.rightMargin: Theme.space[4]
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.column.benchMetric
        text: root.sharpe
        color: root._isIdle
               ? Theme.color.text.disabled
               : Theme.color.text.primary
        font.family: Theme.typography.data.md.family
        font.pixelSize: Theme.typography.data.md.size
        font.features: Theme.typography.data.md.features
        horizontalAlignment: Text.AlignRight
    }

    // ---- col 2: Group block (fills remaining space) -------------------
    Item {
        id: strategyCol
        anchors.left: discloseSlot.right
        anchors.leftMargin: Theme.space[4]
        anchors.right: sharpeText.left
        anchors.rightMargin: Theme.space[4]
        anchors.verticalCenter: parent.verticalCenter
        height: groupContent.implicitHeight

        Column {
            id: groupContent
            width: parent.width
            spacing: 4

            Text {
                width: parent.width
                text: root.displayName
                color: root._isIdle
                       ? Theme.color.text.secondary
                       : Theme.color.text.primary
                font.family: Theme.typography.display.sm.family
                font.pixelSize: Theme.typography.display.sm.size
                font.weight: Theme.typography.display.sm.weight
                elide: Text.ElideRight
            }

            // Key line — family badge chip + dotted group key (mono).
            Row {
                width: parent.width
                spacing: Theme.space[2]

                // Family badge — same quiet bordered-pill treatment as the
                // BenchRow archetype chip.
                Rectangle {
                    id: familyChip
                    visible: root.family.length > 0
                    anchors.verticalCenter: parent.verticalCenter
                    radius: Theme.radius.sm
                    color: "transparent"
                    border.color: Theme.color.border.regular
                    border.width: 1
                    implicitWidth: familyLabel.implicitWidth + Theme.space[2] * 2
                    implicitHeight: familyLabel.implicitHeight + Theme.space[1]

                    Text {
                        id: familyLabel
                        anchors.centerIn: parent
                        text: root.family.toUpperCase()
                        color: Theme.color.text.secondary
                        font.family: Theme.typography.label.xs.family
                        font.pixelSize: Theme.typography.label.xs.size
                        font.weight: Theme.typography.label.xs.weight
                        font.letterSpacing: Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }
                }

                Text {
                    width: parent.width - (familyChip.visible
                                           ? familyChip.width + parent.spacing : 0)
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.groupKey
                    color: Theme.color.text.muted
                    font.family: Theme.typography.data.xs.family
                    font.pixelSize: Theme.typography.data.xs.size
                    font.features: Theme.typography.data.xs.features
                    elide: Text.ElideRight
                }
            }
        }
    }

    // Hairline separator below each row (border.subtle per brief §4)
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.color.border.subtle
    }
}

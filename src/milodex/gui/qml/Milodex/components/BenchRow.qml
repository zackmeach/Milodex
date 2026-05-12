// BenchRow.qml — sparse row for the full-width Bench vertical ledger.
//
// Anatomy (bench-brief §4, PR F visual reconciliation):
//   col 1: drag handle (gutter, left of row)
//   col 2: strategy block (name + dotted ID)
//   col 3: Sharpe (right-aligned mono)
//   col 4: Max-DD (right-aligned mono)
//   col 5: Trade count (right-aligned mono)
//   col 6: status prose — italic Newsreader sentence with one inline colored
//           signal word; smaller mono meta line below
//   col 7: Action slot — reserved visual placeholder; behavior wired in PR G
//
// Visual escalation by stage (bench-brief §5):
//   IDLE       — strategy name in text.secondary; numerics in text.disabled (em-dash)
//   BACKTEST   — full-strength typography; numerics rendered
//   PAPER      — full-strength typography; numerics rendered
//   MICRO LIVE — full-strength; meta line gains capital-deployed detail
//   LIVE       — full-strength + 5%-alpha oxblood row background wash +
//                2px solid brand.accent left border
//
// PR G will wire the Action button menu items from compute_menu_items().
// PR H will enable within-section drag reorder.
// The drag handle is rendered but drag behavior is not wired in PR F.

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as QQC2
import Milodex 1.0

Item {
    id: root

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    property string strategyName: ""
    property string strategyId: ""
    property string stage: "paper"

    // Numeric metrics — pass formatted string ("-" for IDLE em-dash)
    property string sharpe: "-"
    property string maxDD: "-"
    property string tradeCount: "-"

    // Status prose column
    property string statusWord: ""         // signal word (colored inline)
    property string statusWordColor: ""    // color for statusWord; falls back to text.secondary
    property string statusProse: ""        // prose tail after the signal word
    property string metaLine: ""           // mono meta line below prose

    // Action slot — Button variant communicates friction (bench-brief §6).
    // PR G will populate this based on compute_menu_items() output.
    // For PR F, the button is a visual placeholder only; clicking is a no-op.
    property string actionVariant: "ghost"

    // Drag support (PR H will wire; handle rendered but no-op in PR F)
    property bool dragging: false
    signal moveRequested(real localY)

    // Action menu items — list of {label, verbClass, targetStage} dicts
    // produced by compute_menu_items() in bench_v1.py via read_models.py.
    // Populated by the parent BenchSurface delegate from modelData.actions.
    property var actionItems: []

    // Action slot signal — emitted when the Action button is clicked.
    // Connected to open the menu popup below.
    signal actionClicked()

    // -----------------------------------------------------------------------
    // Internal state
    // -----------------------------------------------------------------------

    readonly property bool _isIdle: stage === "idle"
    readonly property bool _isLive: stage === "live"

    // Effective signal-word color — resolved from statusWordColor or
    // fallback to text.secondary when none provided
    readonly property string _signalColor: {
        if (statusWordColor && statusWordColor.length > 0) return statusWordColor
        return Theme.color.text.secondary
    }

    // -----------------------------------------------------------------------
    // Sizing
    // -----------------------------------------------------------------------

    implicitHeight: 78
    z: dragging ? 20 : 0

    // -----------------------------------------------------------------------
    // LIVE row treatment — 5%-alpha brand.accent wash + 2px left border
    // (bench-brief §5 "lead story" treatment)
    // -----------------------------------------------------------------------

    Rectangle {
        id: liveBorder
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: _isLive ? 2 : 0
        color: Theme.color.brand.accent
    }

    // Row background — transparent by default; LIVE gets oxblood wash;
    // hover gets a surface.raised tint at reduced opacity
    Rectangle {
        anchors.fill: parent
        anchors.leftMargin: liveBorder.width

        // LIVE: 5%-alpha oxblood wash; hover: surface.raised at 0.42;
        // dragging: surface.raised at 0.72
        color: {
            if (_isLive) {
                return Qt.rgba(0.49, 0.21, 0.25, 0.05)   // brand.accent ≈ #7d3540 at ~5%
            }
            if (mouseArea.containsMouse || root.dragging) {
                return Qt.rgba(
                    Theme.color.surface.raised.r,
                    Theme.color.surface.raised.g,
                    Theme.color.surface.raised.b,
                    root.dragging ? 0.72 : 0.42
                )
            }
            return "transparent"
        }
        Behavior on color { ColorAnimation { duration: Theme.motion.fast } }
    }

    // Passive hover capture — propagate clicks so column's MouseAreas can fire
    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        propagateComposedEvents: true
        onClicked: (mouse) => mouse.accepted = false
    }

    // -----------------------------------------------------------------------
    // Drag handle (col 1 — gutter)
    // PR H wires the actual drag; rendered but no-op in PR F.
    // -----------------------------------------------------------------------

    Item {
        id: handleSlot
        width: Theme.space[5]
        anchors.left: parent.left
        anchors.leftMargin: liveBorder.width
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        Text {
            anchors.centerIn: parent
            // Six-dot grip glyph — displayed as "::" rotated; PR H may refine
            text: "::"
            color: Theme.color.text.muted
            // Visible at 0.65 on row hover; 0.30 default per brief §4
            opacity: mouseArea.containsMouse || root.dragging ? 0.65 : 0.30
            font.family: Theme.typography.data.sm.family
            font.pixelSize: Theme.typography.data.sm.size
            font.letterSpacing: 1.2
            rotation: 90
            Behavior on opacity { NumberAnimation { duration: Theme.motion.fast } }
        }
    }

    // -----------------------------------------------------------------------
    // Row content layout (cols 2–7)
    // -----------------------------------------------------------------------

    RowLayout {
        id: rowLayout
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: handleSlot.right
        anchors.right: parent.right
        anchors.rightMargin: Theme.space[3]
        spacing: Theme.space[4]

        // ---- col 2: Strategy block (name + ID) ----------------------------
        Column {
            spacing: 4
            Layout.fillWidth: true
            Layout.minimumWidth: 200

            Text {
                width: parent.width
                text: root.strategyName
                // IDLE: text.secondary; all others: text.primary (brief §5)
                color: root._isIdle
                       ? Theme.color.text.secondary
                       : Theme.color.text.primary
                font.family: Theme.typography.display.sm.family
                font.pixelSize: Theme.typography.display.sm.size
                font.weight: Theme.typography.display.sm.weight
                elide: Text.ElideRight
            }

            Text {
                width: parent.width
                text: root.strategyId
                color: Theme.color.text.muted
                font.family: Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features: Theme.typography.data.xs.features
                elide: Text.ElideRight
            }
        }

        // ---- col 3: Sharpe --------------------------------------------------
        Text {
            text: root.sharpe
            // IDLE: text.disabled (em-dash); others: text.primary (brief §5)
            color: root._isIdle
                   ? Theme.color.text.disabled
                   : Theme.color.text.primary
            font.family: Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features: Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        // ---- col 4: Max drawdown -------------------------------------------
        Text {
            text: root.maxDD
            color: root._isIdle
                   ? Theme.color.text.disabled
                   : Theme.color.text.primary
            font.family: Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features: Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        // ---- col 5: Trade count --------------------------------------------
        Text {
            text: root.tradeCount
            color: root._isIdle
                   ? Theme.color.text.disabled
                   : Theme.color.text.primary
            font.family: Theme.typography.data.md.family
            font.pixelSize: Theme.typography.data.md.size
            font.features: Theme.typography.data.md.features
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: Theme.column.benchMetric
        }

        // ---- col 6: Status prose -------------------------------------------
        // Italic Newsreader sentence with one inline colored signal word;
        // smaller mono meta line below (bench-brief §4 "load-bearing element")
        Column {
            spacing: 3
            Layout.fillWidth: true
            Layout.minimumWidth: 180

            // Prose line: signal word in status color inline with italic tail.
            // Using a single Text with inline HTML-style color span would need
            // textFormat: Text.RichText which can break font sizing. Instead
            // we use a RowLayout so each segment keeps its own color and the
            // prose tail elides cleanly.
            RowLayout {
                width: parent.width
                spacing: 0
                clip: true

                Text {
                    id: signalWordText
                    text: root.statusWord
                    color: root._signalColor
                    font.family: Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.weight: Font.Medium
                    font.italic: true
                    visible: root.statusWord.length > 0
                    Layout.preferredWidth: implicitWidth
                }

                Text {
                    text: {
                        if (root.statusWord.length > 0 && root.statusProse.length > 0)
                            return " " + root.statusProse
                        return root.statusProse
                    }
                    color: Theme.color.text.secondary
                    font.family: Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.italic: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    visible: root.statusProse.length > 0
                }
            }

            // Meta line — mono 11-12px, text.muted
            Text {
                width: parent.width
                text: root.metaLine
                color: Theme.color.text.muted
                font.family: Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features: Theme.typography.data.xs.features
                elide: Text.ElideRight
                visible: root.metaLine.length > 0
            }
        }

        // ---- col 7: Action slot -------------------------------------------
        // PR G: Action button now opens a per-row menu populated from
        // compute_menu_items() output (bench_v1.py → read_models.py →
        // modelData.actions → actionItems).  Clicking a menu item is a
        // visual-prototype no-op per ADR 0049 — no backend mutation occurs.
        // PR I will wire confirmation modals and Evidence modal.
        Item {
            Layout.preferredWidth: Theme.column.benchAction
            Layout.alignment: Qt.AlignVCenter
            implicitHeight: actionButton.implicitHeight

            Button {
                id: actionButton
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                variant: root.actionVariant
                text: "Action"
                onClicked: {
                    root.actionClicked()
                    actionMenu.open()
                }
            }

            // Action menu — visual-prototype only (ADR 0049).
            // Items are rendered in the order returned by compute_menu_items()
            // (directional → invocation → informational floor).
            // Clicking any item is a no-op in v1.  PR I will wire handlers.
            QQC2.Menu {
                id: actionMenu
                // Anchor below-right of the Action button
                x: actionButton.x + actionButton.width - width
                y: actionButton.y + actionButton.height + 2

                background: Rectangle {
                    color: Theme.color.surface.elevated
                    border.color: Theme.color.border.regular
                    border.width: 1
                    radius: 4
                }

                Repeater {
                    model: root.actionItems

                    delegate: QQC2.MenuItem {
                        required property var modelData

                        // Separator line above Open Evidence (the informational
                        // floor) so it is visually distinct from the verbs above it.
                        // Per bench-brief §7, Open Evidence is always last.
                        QQC2.MenuSeparator {
                            parent: parent
                            width: parent.width
                            visible: modelData.verbClass === "informational"
                            // Positioned above the item text area
                            y: 0
                        }

                        text: modelData.label
                        font.family: Theme.typography.label.xs.family
                        font.pixelSize: Theme.typography.label.xs.size

                        // Color: directional = brand.accent; invocation = text.primary;
                        // informational (Open Evidence) = text.secondary.
                        contentItem: Text {
                            text: parent.text
                            font: parent.font
                            color: {
                                if (modelData.verbClass === "directional")
                                    return Theme.color.brand.accent
                                if (modelData.verbClass === "invocation")
                                    return Theme.color.text.primary
                                return Theme.color.text.secondary
                            }
                            leftPadding: 12
                            rightPadding: 12
                            verticalAlignment: Text.AlignVCenter
                        }

                        background: Rectangle {
                            implicitHeight: 32
                            color: parent.highlighted ? Theme.color.surface.raised : "transparent"
                        }

                        // v1 visual-prototype: clicking is intentionally a no-op.
                        // PR I will dispatch to confirmation / evidence modals.
                        onTriggered: {
                            // no-op per ADR 0049 Decision 2 (no backend mutation in v1)
                        }
                    }
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

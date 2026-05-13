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
// PR G: Action button menu items wired from compute_menu_items().
// PR H: Within-section drag reorder wired.
//   - dragHandle MouseArea uses cursor-delta tracking (no drag.target).
//     This keeps the dragged row's y fully declarative via BenchSurface's
//     binding; drag.target would imperatively overwrite y and break the binding.
//   - Emits dragStarted(), moveRequested(delta), dragEnded() so BenchSurface
//     can own the reorder math and rowOrder array mutation.
//     moveRequested(delta): delta is the cumulative Y offset from press.
//   - Drag activation is threshold-gated (Qt.styleHints.startDragDistance or 4px)
//     to avoid a visual blip on plain handle clicks.
//   - The whole-row mouseArea remains a passive hover capture only.

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

    // Drag support (PR H — within-section visual reorder only)
    property bool dragging: false
    // dragStarted: emitted once the drag threshold is crossed (not on raw press).
    signal dragStarted()
    // moveRequested(delta): emitted on position change while drag is active.
    // delta is the cumulative Y offset from the press position.
    // BenchSurface uses delta to compute dragYOffset and targetIndex.
    signal moveRequested(real delta)
    // dragEnded: emitted on mouse-release when drag was active; BenchSurface
    // commits the new order.
    signal dragEnded()

    // Action menu items — list of {label, verbClass, targetStage} dicts
    // produced by compute_menu_items() in bench_v1.py via read_models.py.
    // Populated by the parent BenchSurface delegate from modelData.actions.
    property var actionItems: []

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

    // Shadow — visible only while dragging; sits below the row background
    Rectangle {
        anchors.fill: parent
        anchors.topMargin: 2
        anchors.leftMargin: 2
        color: Qt.rgba(0, 0, 0, 0.25)
        visible: root.dragging
    }

    // Row background — transparent by default; LIVE gets oxblood wash;
    // hover gets a surface.raised tint at reduced opacity;
    // dragging: opaque surface.raised with 1-px border
    Rectangle {
        anchors.fill: parent
        anchors.leftMargin: liveBorder.width

        // LIVE: 5%-alpha oxblood wash; hover: surface.raised at 0.42;
        // dragging: opaque surface.raised.
        // ORDER MATTERS: `root.dragging` MUST be checked before `_isLive`.
        // Every dragged row — LIVE included — must paint opaque so neighbors
        // do not ghost through the paper strip. If the LIVE branch wins first,
        // dragged LIVE rows render at 5% alpha and the drag-visual contract
        // breaks. A static test in tests/milodex/gui/test_qml_load_smoke.py
        // guards this ordering; do not reshuffle the branches.
        color: {
            if (root.dragging) {
                return Theme.color.surface.raised
            }
            if (_isLive) {
                return Qt.rgba(0.49, 0.21, 0.25, 0.05)   // brand.accent ≈ #7d3540 at ~5%
            }
            if (mouseArea.containsMouse || rowClickArea.containsMouse) {
                return Qt.rgba(
                    Theme.color.surface.raised.r,
                    Theme.color.surface.raised.g,
                    Theme.color.surface.raised.b,
                    0.42
                )
            }
            return "transparent"
        }
        border.color: Theme.color.border.regular
        border.width: root.dragging ? 1 : 0
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

    // Row-body click — opens the action menu when the user clicks anywhere on
    // the row except the drag handle.  Handle is excluded geometrically
    // (anchors.left: handleSlot.right), not by z-order.
    // Movement-threshold gating prevents accidental menu-open on press/move/release.
    MouseArea {
        id: rowClickArea
        anchors.left: handleSlot.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        cursorShape: Qt.PointingHandCursor
        // hoverEnabled must be set explicitly so containsMouse is reliable.
        // Without this, the PointingHandCursor may still register hover
        // internally (implicitly enabling it) but containsMouse won't update
        // consistently, and the cursor intercepts mouseArea's hover events.
        hoverEnabled: true

        property real _pressX: 0
        property real _pressY: 0
        property bool _movedTooFar: false

        onPressed: (mouse) => {
            _pressX = mouse.x
            _pressY = mouse.y
            _movedTooFar = false
        }
        onPositionChanged: (mouse) => {
            if (!pressed) return
            var threshold = (typeof Qt.styleHints !== "undefined" &&
                             Qt.styleHints.startDragDistance > 0)
                            ? Qt.styleHints.startDragDistance : 4
            if (Math.abs(mouse.x - _pressX) >= threshold ||
                Math.abs(mouse.y - _pressY) >= threshold) {
                _movedTooFar = true
            }
        }
        onReleased: {
            if (!_movedTooFar) actionMenu.open()
        }
    }

    // -----------------------------------------------------------------------
    // Drag handle (col 1 — gutter)
    // PR H: dragHandle MouseArea initiates Y-axis drag; emits dragStarted,
    // moveRequested, and dragEnded so BenchSurface owns the reorder math.
    // -----------------------------------------------------------------------

    Item {
        id: handleSlot
        width: Theme.space[5]
        anchors.left: parent.left
        anchors.leftMargin: liveBorder.width
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        Text {
            id: handleGlyph
            anchors.centerIn: parent
            // Six-dot grip glyph — displayed as "::" rotated
            text: "::"
            color: Theme.color.text.muted
            // 0.80 while actively dragging from handle; 0.65 on row hover; 0.30 default
            opacity: dragHandle.pressed ? 0.80
                     : (mouseArea.containsMouse || rowClickArea.containsMouse || root.dragging ? 0.65 : 0.30)
            font.family: Theme.typography.data.sm.family
            font.pixelSize: Theme.typography.data.sm.size
            font.letterSpacing: 1.2
            rotation: 90
            Behavior on opacity { NumberAnimation { duration: Theme.motion.fast } }
        }

        // Drag handle hit area — covers the full handleSlot.
        // Uses cursor-delta tracking rather than drag.target so the dragged
        // row's y remains fully declarative (bound in BenchSurface).
        // drag.target would imperatively overwrite root.y and break the binding.
        MouseArea {
            id: dragHandle
            anchors.fill: parent
            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.SizeVerCursor

            // Internal drag-tracking state.
            property real _pressMouseY: 0
            property bool _activeDrag: false

            onPressed: {
                _pressMouseY = mouseY
                _activeDrag = false
                // Do NOT set root.dragging or emit dragStarted yet —
                // wait for the threshold check in onPositionChanged.
            }
            onPositionChanged: {
                if (!pressed) return
                var delta = mouseY - _pressMouseY
                var threshold = (typeof Qt.styleHints !== "undefined" &&
                                 Qt.styleHints.startDragDistance > 0)
                                ? Qt.styleHints.startDragDistance : 4
                if (!_activeDrag) {
                    if (Math.abs(delta) >= threshold) {
                        _activeDrag = true
                        root.dragging = true
                        root.dragStarted()
                    }
                }
                if (_activeDrag) {
                    root.moveRequested(delta)
                }
            }
            onReleased: {
                if (_activeDrag) {
                    root.dragging = false
                    root.dragEnded()
                }
                _activeDrag = false
            }
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
        // PR I: Folio mark affordance — a quiet column-rule + glyph that
        // appears on hover.  Clicking the mark (or anywhere on the row body
        // via rowClickArea) opens the per-row action menu.
        Item {
            id: actionSlot
            Layout.preferredWidth: Theme.column.benchAction
            Layout.alignment: Qt.AlignVCenter
            implicitHeight: 78   // matches BenchRow.implicitHeight

            // Folio mark — 1-px vertical hairline at right edge of slot,
            // plus a small action-count glyph.  No fill, no border-radius.
            Item {
                id: folioMark
                anchors.fill: parent

                // Opacity driven by row hover / menu-open state
                opacity: {
                    if (actionMenu.opened || root.activeFocus) return 1.0
                    if (mouseArea.containsMouse || rowClickArea.containsMouse) return 0.45
                    return 0
                }
                Behavior on opacity { NumberAnimation { duration: Theme.motion.fast } }

                // 1-px column rule at the right edge of the slot
                Rectangle {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: 1
                    color: Theme.color.border.regular
                }

                // Action-count glyph centered in the slot
                Text {
                    anchors.centerIn: parent
                    text: "· " + root.actionItems.length + " ·"
                    color: (actionMenu.opened || root.activeFocus)
                           ? Theme.color.text.secondary
                           : Theme.color.text.muted
                    font.family: Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size
                }

                // Folio mark hit area — opens the same menu as the row-body click
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: actionMenu.open()
                }
            }

            // Action menu — visual-prototype only (ADR 0049).
            // Items are rendered in the order returned by compute_menu_items()
            // (directional → invocation → informational floor).
            // Clicking any item is a no-op in v1.  PR I will wire handlers.
            QQC2.Menu {
                id: actionMenu
                width: 240
                // Anchor below-right of the action slot
                x: actionSlot.width - width
                y: actionSlot.height + 2

                // Warm-dark surface to match ledger aesthetic.
                // surface.raised (#1a1611) is the highest defined surface token;
                // border.regular (#33291c) gives a thin brass/brown hairline;
                // radius 4 keeps the editorial softness.
                background: Rectangle {
                    color: Theme.color.surface.raised
                    border.color: Theme.color.border.regular
                    border.width: 1
                    radius: Theme.radius.md
                }

                // Instantiator is required here — QQC2.Menu uses addItem()/removeItem()
                // internally and does NOT pick up children created by a Repeater.
                // onObjectAdded/onObjectRemoved wire each dynamically-created MenuItem
                // into the Menu's managed item list so they actually appear.
                Instantiator {
                    model: root.actionItems

                    delegate: QQC2.MenuItem {
                        required property var modelData

                        text: modelData.label
                        implicitWidth: 240
                        implicitHeight: 36
                        font.family: Theme.typography.label.xs.family
                        font.pixelSize: Theme.typography.label.xs.size

                        // Color: directional verbs use brand.accentHover (#9a4350) —
                        // a half-step brighter than brand.accent (#7d3540) — which
                        // lifts contrast against surface.raised while preserving the
                        // oxblood signal.  Invocation and informational items use
                        // text.onBrand (#f5e6c4) — the warmest cream token — so all
                        // non-directional items including Open Evidence read clearly
                        // against the dark warm surface without looking disabled.
                        // Visual separation between invocation and informational floor
                        // is handled by the border.regular hairline on the item.
                        contentItem: Text {
                            text: parent.text
                            font: parent.font
                            color: modelData.verbClass === "directional"
                                   ? Theme.color.brand.accentHover
                                   : Theme.color.text.onBrand
                            leftPadding: 12
                            rightPadding: 12
                            verticalAlignment: Text.AlignVCenter

                            // Full-width hairline separator above the informational floor
                            // (Open Evidence).  border.regular (#33291c) is more visible
                            // than border.subtle (#241f15) against surface.raised.
                            Rectangle {
                                visible: modelData.verbClass === "informational"
                                anchors.top: parent.top
                                anchors.left: parent.left
                                anchors.right: parent.right
                                // Extend into left/right padding so the rule spans the full
                                // menu width rather than just the text content area.
                                anchors.leftMargin: -12
                                anchors.rightMargin: -12
                                height: 1
                                color: Theme.color.border.regular
                            }
                        }

                        // Hover: shift to surface.base (#13100a) — one step darker than
                        // surface.raised — for a subtle, non-flashy active indicator.
                        background: Rectangle {
                            implicitWidth: 240
                            implicitHeight: 36
                            color: parent.highlighted ? Theme.color.surface.base : "transparent"
                        }

                        // v1 visual-prototype: clicking is intentionally a no-op.
                        // PR I will dispatch to confirmation / evidence modals.
                        onTriggered: {
                            // no-op per ADR 0049 Decision 2 (no backend mutation in v1)
                        }
                    }

                    onObjectAdded: (index, object) => actionMenu.insertItem(index, object)
                    onObjectRemoved: (index, object) => actionMenu.removeItem(object)
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

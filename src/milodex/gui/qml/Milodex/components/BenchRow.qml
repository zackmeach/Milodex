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
    // Stable coordinate frame for drag-delta calculation. BenchSurface MUST
    // set this to the rowsContainer Item (the parent that holds all rows in a
    // section) so the dragged row's own motion does not feed back into mouseY.
    // See the dragHandle MouseArea below for the full rationale.
    property Item dragCoordinateItem: null
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

    // Raw as_qml() row dict for this strategy. Set by BenchSurface via
    // `rowData: modelData`. Read by BenchEvidenceModal when the operator
    // selects the informational floor item.
    property var rowData: ({})

    // PR J: emitted when the operator selects the Open Evidence floor item.
    // BenchSurface owns the (one and only) BenchEvidenceModal instance and
    // listens for this signal. No other menu item dispatches anything in v1.
    signal evidenceRequested(var rowData)

    // PR K: emitted when the operator selects any state-changing menu item
    // (directional or invocation). BenchSurface owns the (one and only)
    // BenchConfirmationModal instance and listens for this signal. The
    // confirmation modal is a *visual preview only* in v1 — its primary
    // action is disabled and labelled "Not wired in v1". No backend
    // command is dispatched anywhere along this path.
    signal actionPreviewRequested(var rowData, var actionData)

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
            font.features: Theme.typography.data.sm.features
            font.letterSpacing: 1.2
            rotation: 90
            Behavior on opacity { NumberAnimation { duration: Theme.motion.fast } }
        }

        // Drag handle hit area — covers the full handleSlot.
        // Uses cursor-delta tracking rather than drag.target so the dragged
        // row's y remains fully declarative (bound in BenchSurface).
        // drag.target would imperatively overwrite root.y and break the binding.
        //
        // STABLE-COORDINATE MAPPING: delta is computed against
        // root.dragCoordinateItem (set by BenchSurface to rowsContainer), NOT
        // against this MouseArea's local mouseY. The row itself moves during
        // drag, so the row-local coordinate frame moves under the cursor — a
        // naive `mouseY - _pressMouseY` produces a negative-feedback oscillation
        // (row moves → local mouseY shifts inversely → delta collapses → row
        // snaps back → jitter / visual overlap on commit). Mapping into a
        // stable parent frame via dragHandle.mapToItem cancels the row's own
        // motion. Do NOT replace this with mouseY-based math.
        MouseArea {
            id: dragHandle
            anchors.fill: parent
            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.SizeVerCursor

            // Internal drag-tracking state — _pressPointerY is in
            // root.dragCoordinateItem's coordinate frame.
            property real _pressPointerY: 0
            property bool _activeDrag: false

            // Single source of truth for stable-frame pointer mapping.
            // Falls back to row-local mouse.y only if the coordinate item
            // was never wired (safety net — won't oscillate because the
            // fallback is consistent between press and move, but BenchSurface
            // MUST wire dragCoordinateItem: rowsContainer in production).
            function pointerYInDragFrame(mouse) {
                if (root.dragCoordinateItem === null) return mouse.y
                return dragHandle.mapToItem(
                    root.dragCoordinateItem, mouse.x, mouse.y).y
            }

            onPressed: (mouse) => {
                _pressPointerY = pointerYInDragFrame(mouse)
                _activeDrag = false
                // Do NOT set root.dragging or emit dragStarted yet —
                // wait for the threshold check in onPositionChanged.
            }
            onPositionChanged: (mouse) => {
                if (!pressed) return
                var currentPointerY = pointerYInDragFrame(mouse)
                var delta = currentPointerY - _pressPointerY
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
    // Row content layout (cols 2–7) — STABLE COLUMN GEOMETRY CONTRACT
    //
    // Every column except `strategyCol` has a fixed width drawn from
    // Theme.column.*.  Columns are right-anchored in a chain from the row's
    // right edge inward; `strategyCol` fills the remainder between
    // `handleSlot.right` and `sharpeText.left`.  This is intentional: a
    // per-row RowLayout with two `Layout.fillWidth` participants (strategy +
    // status) lets the layout solver pick different widths for different
    // rows based on each row's implicitWidth, and after a `rowOrder` splice
    // the delegates get rebound to different modelData → different implicit
    // widths → columns visibly shift.  Explicit anchors + fixed widths make
    // column geometry invariant to row content and identical across header
    // and rows.  The same widths are used in BenchSurface's section header;
    // do not change one without changing the other.
    // -----------------------------------------------------------------------

    // ---- col 7 (rightmost): Action slot --------------------------------
    Item {
        id: actionSlot
        anchors.right: parent.right
        anchors.rightMargin: Theme.space[3]
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.column.benchAction
        height: 78   // matches BenchRow.implicitHeight

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
        QQC2.Menu {
            id: actionMenu
            width: 240
            x: actionSlot.width - width
            y: actionSlot.height + 2

            background: Rectangle {
                color: Theme.color.surface.raised
                border.color: Theme.color.border.regular
                border.width: 1
                radius: Theme.radius.md
            }

            Instantiator {
                model: root.actionItems

                delegate: QQC2.MenuItem {
                    required property var modelData

                    text: modelData.label
                    implicitWidth: 240
                    implicitHeight: 36
                    font.family: Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size

                    contentItem: Text {
                        text: parent.text
                        font: parent.font
                        color: modelData.verbClass === "directional"
                               ? Theme.color.brand.accentHover
                               : Theme.color.text.primary
                        leftPadding: 12
                        rightPadding: 12
                        verticalAlignment: Text.AlignVCenter

                        Rectangle {
                            visible: modelData.verbClass === "informational"
                            anchors.top: parent.top
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.leftMargin: -12
                            anchors.rightMargin: -12
                            height: 1
                            color: Theme.color.border.regular
                        }
                    }

                    background: Rectangle {
                        implicitWidth: 240
                        implicitHeight: 36
                        color: parent.highlighted ? Theme.color.surface.base : "transparent"
                    }

                    onTriggered: {
                        // PR J: the informational floor item ("Open Evidence",
                        // verbClass: "informational") opens the read-only
                        // BenchEvidenceModal owned by BenchSurface.
                        // PR K: every other menu item opens the visual
                        // confirmation preview owned by BenchSurface. Neither
                        // path dispatches a backend command in v1.
                        if (modelData.verbClass === "informational" &&
                                modelData.label === "Open Evidence") {
                            root.evidenceRequested(root.rowData)
                        } else {
                            root.actionPreviewRequested(root.rowData, modelData)
                        }
                    }
                }

                onObjectAdded: (index, object) => actionMenu.insertItem(index, object)
                onObjectRemoved: (index, object) => actionMenu.removeItem(object)
            }
        }
    }

    // ---- col 6: Status prose (fixed-width, right-anchored) ------------
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

            // Prose line: signal word in status color inline with italic tail.
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

    // ---- col 2: Strategy block (fills remaining space) ----------------
    Item {
        id: strategyCol
        anchors.left: handleSlot.right
        anchors.leftMargin: Theme.space[4]
        anchors.right: sharpeText.left
        anchors.rightMargin: Theme.space[4]
        anchors.verticalCenter: parent.verticalCenter
        height: strategyContent.implicitHeight

        Column {
            id: strategyContent
            width: parent.width
            spacing: 4

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

// BenchSurface.qml — full-width vertical strategy ledger.
//
// SUPERSESSION (ADR 0051, 2026-05-30): the "no backend mutation (ADR 0049)"
// and "visual-prototype no-op" PR-scope notes below are historical markers from
// PR F / PR G; ADR 0051 narrowly supersedes ADR 0049 for the wired Bench
// command families. See ADR 0051 for the authoritative current behavior.
//
// Visual model (bench-brief §2): five promotion stages rendered as vertical
// sections stacked top-to-bottom. Section height is determined by strategy
// count — the funnel shape (many strategies early, few at live) is the design.
//
// Section anatomy (bench-brief §3):
//   roman numeral · STAGE NAME · count     [right-aligned caption]
//   ─────────────────────────────────────  (hairline; 2px brand.accent for LIVE)
//   [column header row]
//   [BenchRow …]
//
// Row anatomy (bench-brief §4) handled by BenchRow.qml:
//   drag handle | strategy block | Sharpe | Max-DD | Trades | status prose | Action
//
// PR F scope: visual reconciliation only.
//   - Within-section drag handle rendered but not wired; PR H wires drag.
//   - Evidence modal and confirmation modals deferred to PR I.
//   - No backend mutation (ADR 0049 Decision 2).
//   - No legacy "View Evidence" primary button rail.
//   - No kanban column markup.
//
// PR G: Action menu wired.
//   - BenchRow.actionItems populated from modelData.actions (compute_menu_items
//     output via read_models._compute_bench_action_menu).
//   - BenchRow.actionVariant derived from actual item verbClasses, not statusKind.
//   - Clicking a menu item is a visual-prototype no-op (ADR 0049 Decision 2).
//   - PAPER section uses "secondary" (outlined) variant to avoid visual over-emphasis.
//
// PR H: Within-section visual drag reorder.
//   - Rows rendered in an explicit-Y Item (rowsContainer) instead of a Column so
//     each BenchRow can be positioned by index during drag.
//   - Drag lives in BenchRow.qml (handle MouseArea); BenchSurface owns reorder math.
//   - Order is session-local only (sectionRoot.rowOrder); not persisted (ADR 0049).
//   - Cross-stage drag is structurally impossible: drag bounds are clamped to the
//     section's rowsContainer; clip:true prevents visual escape.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // Sections from the BenchState read model.
    // Each section: { stage, stageRoman, stageName, stageCaption, strategies[] }
    // Each strategy: as_qml() output from _StrategyRow (read_models.py)
    property var benchData: BenchState.sections
    property bool benchHasData: BenchState.lastRefreshedAt !== ""

    // True while any section has a row drag in progress.
    // Used to gate keyboard scroll so arrow keys don't fight the drag.
    property bool anyDragging: false

    // Watchdog: if benchData is rebound mid-drag (e.g. BenchState emits a new
    // sections snapshot), delegate destruction prevents onDragEnded from firing
    // and anyDragging would remain true, permanently disabling keyboard scroll.
    // Force-reset it whenever the data reference changes.
    onBenchDataChanged: anyDragging = false

    // Modal state. Single-valued enum makes mutual exclusion structural — the
    // surface can only display one overlay at a time. Payload properties
    // (evidenceModalRow, confirmationPreviewRow/Action) carry per-modal data
    // and are set together with the activeModal transition via the helpers
    // below. External writers (capture_bench_interactive.py) set activeModal
    // directly via setProperty.
    property string activeModal: "none"   // "none" | "evidence" | "confirmation"
    property var    evidenceModalRow:        ({})
    property var    confirmationPreviewRow:    ({})
    property var    confirmationPreviewAction: ({})

    function openEvidenceModal(row) {
        evidenceModalRow = row
        activeModal = "evidence"
    }
    function openConfirmationModal(row, action) {
        confirmationPreviewRow = row
        confirmationPreviewAction = action
        activeModal = "confirmation"
    }
    function closeAllModals() { activeModal = "none" }

    // -----------------------------------------------------------------------
    // Formatting helpers
    // -----------------------------------------------------------------------

    // Delegate to Formatters singleton (PR10).
    // formattedTrades is intentionally NOT repointed: it returns "—" for
    // tradeCount===0, which diverges from Formatters.count's "0".
    function formattedSharpe(row) { return Formatters.sharpe(row.sharpe) }
    function formattedMaxDD(row)  { return Formatters.pct1(row.maxDrawdownPct) }

    function formattedTrades(row) {
        if (!row.tradeCount || row.tradeCount === 0) return "—"
        return "" + row.tradeCount
    }

    // Resolve status word color from statusKind string
    function statusWordColor(kind) {
        if (kind === "positive") return Theme.status.positive
        if (kind === "warning")  return Theme.status.warning
        if (kind === "negative") return Theme.status.negative
        return Theme.status.info
    }

    // Determine the Action button variant for a row (bench-brief §6, §7).
    // Derived from the verbClass of items in row.actions (compute_menu_items()
    // output from bench_v1.py).
    //
    // Variant logic (post-PR G polish):
    //   secondary — default for all rows with at least one state-changing verb
    //               (directional or invocation), including promote-eligible rows.
    //               Renders as outlined border.regular + text.primary, which
    //               keeps the right-side Action column visible but not a
    //               dominant repeated rail.
    //   ghost     — Open-Evidence-only rows (informational floor only).
    //
    // The filled-oxblood treatment (variant: "primary") is no longer assigned
    // here.  BenchRow promotes the button to "primary" transiently on hover or
    // while its action menu is open — see BenchRow.qml.
    function actionVariant(row) {
        var actions = row.actions || []
        var hasStateChanging = false
        for (var i = 0; i < actions.length; ++i) {
            if (actions[i].verbClass === "directional" || actions[i].verbClass === "invocation") {
                hasStateChanging = true
                break
            }
        }
        if (hasStateChanging) return "secondary"
        return "ghost"
    }

    // Format the meta line for the status column.
    // MICRO LIVE rows gain capital-deployed detail per brief §5.
    function metaLine(row) {
        var parts = []
        if (row.metaLine && row.metaLine.length > 0) return row.metaLine
        if (row.stage === "micro_live" || row.stage === "live") {
            parts.push("session: " + (row.sessionState || "not_running"))
        }
        return parts.join(" · ")
    }

    // -----------------------------------------------------------------------
    // Surface background
    // -----------------------------------------------------------------------

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.canvas
    }

    // -----------------------------------------------------------------------
    // Scrollable ledger
    // -----------------------------------------------------------------------

    Flickable {
        id: scroller
        anchors.fill: parent
        contentWidth: width
        contentHeight: pageColumn.implicitHeight + Theme.space[7] * 2
        clip: true
        flickableDirection: Flickable.VerticalFlick
        interactive: false
        focus: true
        // When loaded inside a Loader/StackView the parent is not necessarily
        // an active focus scope, so focus:true alone leaves arrow keys dead until
        // the user clicks.  forceActiveFocus() requests the active focus chain
        // immediately, ensuring keyboard scroll works from first render.
        Component.onCompleted: scroller.forceActiveFocus()

        Keys.onPressed: (event) => {
            if (root.anyDragging) return
            // PR J / PR K: when either the Evidence modal or the
            // Confirmation preview modal is open it owns the focus chain;
            // arrow / Page / Home / End must not bleed through to scroll the
            // ledger underneath. Eat them here so they neither scroll nor
            // re-route. Escape is handled by the modal itself.
            if (root.activeModal !== "none") {
                event.accepted = true
                return
            }
            var max = Math.max(0, scroller.contentHeight - scroller.height)
            if (event.key === Qt.Key_Down) {
                scroller.contentY = Math.max(0, Math.min(max, scroller.contentY + 40))
                event.accepted = true
            } else if (event.key === Qt.Key_Up) {
                scroller.contentY = Math.max(0, Math.min(max, scroller.contentY - 40))
                event.accepted = true
            } else if (event.key === Qt.Key_PageDown) {
                scroller.contentY = Math.max(0, Math.min(max, scroller.contentY + scroller.height * 0.9))
                event.accepted = true
            } else if (event.key === Qt.Key_PageUp) {
                scroller.contentY = Math.max(0, Math.min(max, scroller.contentY - scroller.height * 0.9))
                event.accepted = true
            } else if (event.key === Qt.Key_Home) {
                scroller.contentY = 0
                event.accepted = true
            } else if (event.key === Qt.Key_End) {
                scroller.contentY = max
                event.accepted = true
            }
        }

        WheelHandler {
            target: null
            onWheel: (event) => {
                // PR J / PR K: don't scroll the ledger while either modal is
                // open; each modal owns its own wheel handler/overlay.
                if (root.activeModal !== "none") {
                    event.accepted = true
                    return
                }
                var max = Math.max(0, scroller.contentHeight - scroller.height)
                var step = event.angleDelta.y / 120 * 40
                scroller.contentY = Math.max(0, Math.min(max, scroller.contentY - step))
                event.accepted = true
            }
        }

        Column {
            id: pageColumn
            width: scroller.width
            topPadding: Theme.space[7]
            bottomPadding: Theme.space[7]
            leftPadding: Theme.space[7]
            rightPadding: Theme.space[7]
            spacing: Theme.space[7]

            // ---- Page header -----------------------------------------------
            Column {
                width: parent.width - Theme.space[7] * 2
                spacing: Theme.space[3]

                Text {
                    text: "Milodex · Strategy Bench"
                    color: Theme.color.text.muted
                    font.family: Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size
                    font.weight: Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                // "THE BENCH." headline — Newsreader display with oxblood period
                Row {
                    spacing: 0
                    Text {
                        text: "The Bench"
                        color: Theme.color.text.primary
                        font.family: Theme.typography.display.lg.family
                        font.pixelSize: Theme.typography.display.lg.size
                        font.weight: Theme.typography.display.lg.weight
                    }
                    Text {
                        text: "."
                        color: Theme.color.brand.accent
                        font.family: Theme.typography.display.lg.family
                        font.pixelSize: Theme.typography.display.lg.size
                        font.weight: Theme.typography.display.lg.weight
                    }
                }

                // Standfirst — italic Newsreader deck
                Text {
                    width: parent.width
                    text: "Every strategy on the ladder, top to bottom — what is working, what is blocked, and what is waiting at the next gate."
                    color: Theme.color.text.secondary
                    font.family: Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.italic: true
                    wrapMode: Text.WordWrap
                }

                // Metrics-provenance caption (D-8 deferral / M2 item c) — quiet,
                // eyebrow-style label so the operator sees at a glance that the
                // Sharpe / Max-DD / Trades columns below are a read-model
                // snapshot, not a reconstructed or authoritative gate verdict.
                // Same label.xs muted-caption treatment as the eyebrow above.
                Text {
                    text: "Sharpe · Max-DD · Trades — read-model snapshot, not reconstructed"
                    color: Theme.color.text.muted
                    font.family: Theme.typography.label.xs.family
                    font.pixelSize: Theme.typography.label.xs.size
                    font.weight: Theme.typography.label.xs.weight
                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                }

                // Hairline below header
                Rectangle {
                    width: parent.width
                    height: 1
                    color: Theme.color.border.regular
                }

                // Section status — loading / error / no-data-yet for the
                // BenchState read model (PR-8 GUI surface honesty).
                SectionStatus {
                    width: parent.width
                    status: BenchState.dataStatus
                    errorMessage: BenchState.dataErrorMessage
                    hasData: root.benchHasData
                }
            }

            // ---- Async completion fallback banner --------------------------
            // P18 durable fallback for the async start/stop/backtest commands.
            // The confirmation modal's _handleAsyncSubmitCompleted is the happy
            // path (immediate inline feedback) but it DROPS the outcome if the
            // operator closes the modal mid-spawn. This banner binds to
            // BenchCommandBridge.recentCompletions on the SURFACE, so it is not
            // torn down when the modal closes — the outcome surfaces here even
            // when the modal listener missed it. Read-only: the dismiss control
            // only removes the display notice, it does not ack/re-issue a command.
            //
            // Visual pattern mirrors the KillSwitchResetModal broker-error banner
            // (border + status color + glyph; collapsed when empty).
            Column {
                id: completionBanner
                width: parent.width - Theme.space[7] * 2
                spacing: Theme.space[2]
                visible: BenchCommandBridge.recentCompletions.length > 0

                // status → tone color: submitted→positive, error→negative,
                // anything else (blocked)→warning.
                function _toneColor(status) {
                    if (status === "submitted") return Theme.status.positive
                    if (status === "error")     return Theme.status.negative
                    return Theme.status.warning
                }

                Repeater {
                    model: BenchCommandBridge.recentCompletions

                    delegate: Rectangle {
                        required property var modelData
                        width: completionBanner.width
                        implicitHeight: noticeRow.implicitHeight + Theme.space[3] * 2
                        color: Theme.color.surface.base
                        radius: Theme.radius.md
                        border.width: 1
                        border.color: completionBanner._toneColor(modelData.status)

                        Row {
                            id: noticeRow
                            anchors {
                                left: parent.left
                                right: parent.right
                                top: parent.top
                                margins: Theme.space[3]
                            }
                            spacing: Theme.space[2]

                            // Status glyph dot.
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 8
                                height: 8
                                radius: Theme.radius.full
                                color: completionBanner._toneColor(modelData.status)
                            }

                            Text {
                                width: parent.width - 8 - dismissControl.width - Theme.space[2] * 2
                                text: (modelData.message || "")
                                color: completionBanner._toneColor(modelData.status)
                                font.family:    Theme.typography.body.md.family
                                font.pixelSize: Theme.typography.body.md.size
                                wrapMode: Text.WordWrap
                                verticalAlignment: Text.AlignVCenter
                            }

                            // Dismiss control — display-only. Removes this
                            // notice from the bridge's read-only record; issues
                            // NO command (no submit, no re-dispatch).
                            Text {
                                id: dismissControl
                                anchors.verticalCenter: parent.verticalCenter
                                text: "Dismiss"
                                color: Theme.color.text.muted
                                font.family: Theme.typography.label.xs.family
                                font.pixelSize: Theme.typography.label.xs.size
                                font.weight: Font.DemiBold
                                font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                font.capitalization: Font.AllUppercase

                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: BenchCommandBridge.dismissCompletion(
                                        modelData.proposalId)
                                }
                            }
                        }
                    }
                }
            }

            // ---- Stage sections --------------------------------------------
            // Repeater over sections from BenchState.sections
            Repeater {
                model: root.benchData

                delegate: Item {
                    id: sectionRoot
                    width: pageColumn.width - Theme.space[7] * 2
                    height: sectionCol.implicitHeight

                    property var sectionData: modelData

                    // Session-only row order (within-section reorder; non-persisting per ADR 0049).
                    property var rowOrder: []

                    // ---- PR H drag state ----------------------------------------
                    // rowHeight must match BenchRow.implicitHeight (78px).
                    readonly property int rowHeight: 78
                    // Index of the row currently being dragged (-1 = none).
                    property int draggingIndex: -1
                    // Target landing index as the drag moves (clamped to valid range).
                    property int targetIndex: -1
                    // Y offset of the dragged row relative to its resting slot.
                    property real dragYOffset: 0

                    // shiftFor(index): how many px a non-dragged row should shift
                    // to open the landing slot for the dragged row.
                    // Returns +rowHeight (shift down) or -rowHeight (shift up) or 0.
                    function shiftFor(idx) {
                        if (draggingIndex < 0 || idx === draggingIndex) return 0
                        var lo = Math.min(draggingIndex, targetIndex)
                        var hi = Math.max(draggingIndex, targetIndex)
                        if (idx < lo || idx > hi) return 0
                        // Dragging downward: rows between source and target shift up
                        // Dragging upward: rows between target and source shift down
                        return draggingIndex < targetIndex ? -rowHeight : rowHeight
                    }

                    function syncRows() {
                        // Guard: never rebuild row order while a drag is in progress —
                        // doing so would destroy the delegate under the cursor.
                        if (draggingIndex >= 0) return
                        rowOrder = (sectionData && sectionData.strategies)
                                   ? sectionData.strategies.slice()
                                   : []
                    }

                    Component.onCompleted: syncRows()
                    onSectionDataChanged: { if (draggingIndex < 0) syncRows() }

                    Column {
                        id: sectionCol
                        width: parent.width
                        spacing: 0

                        // ---- Section rule (2px oxblood for LIVE, 1px for others) ----
                        Rectangle {
                            width: parent.width
                            height: sectionData.stage === "live" ? 2 : 1
                            color: sectionData.stage === "live"
                                   ? Theme.color.brand.accent
                                   : Theme.color.border.regular
                        }

                        // ---- Section header (bench-brief §3) -------------------
                        Item {
                            id: stageHeaderRow
                            width: parent.width
                            height: 54

                            // Left cluster: roman numeral · STAGE NAME · count
                            Row {
                                anchors.left: parent.left
                                anchors.bottom: stageHeaderRow.bottom
                                anchors.bottomMargin: Math.round(stageHeaderRow.height * 0.35)
                                spacing: Theme.space[2]

                                // Roman numeral — Newsreader italic, text.muted
                                Text {
                                    text: sectionData.stageRoman
                                    color: Theme.color.text.muted
                                    font.family: Theme.typography.deck.family
                                    font.pixelSize: Theme.typography.display.sm.size  // 18px; nearest token, 1px under original
                                    font.italic: true
                                }

                                // Stage name — Public Sans, letter-spaced uppercase, weight 600
                                Text {
                                    text: sectionData.stageName.toUpperCase()
                                    color: Theme.color.text.primary
                                    font.family: Theme.typography.label.xs.family
                                    font.pixelSize: Theme.typography.label.xs.size
                                    font.weight: Font.DemiBold
                                    font.letterSpacing: Theme.typography.label.xs.letterSpacing
                                }

                                // Mid-dot separator
                                Text {
                                    text: "·"
                                    color: Theme.color.text.muted
                                    font.family: Theme.typography.label.xs.family
                                    font.pixelSize: Theme.typography.label.xs.size
                                }

                                // Strategy count — JetBrains Mono, 2-digit zero-padded
                                Text {
                                    text: {
                                        var n = sectionRoot.rowOrder.length
                                        return n < 10 ? "0" + n : "" + n
                                    }
                                    color: Theme.color.text.muted
                                    font.family: Theme.typography.data.sm.family
                                    font.pixelSize: Theme.typography.data.sm.size
                                    font.features: Theme.typography.data.sm.features
                                }
                            }

                            // Right: section caption — Newsreader italic, text.secondary
                            Text {
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                text: sectionData.stageCaption
                                color: Theme.color.text.secondary
                                font.family: Theme.typography.deck.family
                                font.pixelSize: Theme.typography.deck.size
                                font.italic: true
                                elide: Text.ElideRight
                                maximumLineCount: 1
                            }
                        }

                        // Hairline below section header (border.subtle per brief §3)
                        Rectangle {
                            width: parent.width
                            height: 1
                            color: Theme.color.border.subtle
                        }

                        // ---- Empty-state (bench-brief §3 empty-state treatment) -----
                        Item {
                            visible: sectionRoot.rowOrder.length === 0
                            width: parent.width
                            height: 64

                            Text {
                                anchors.centerIn: parent
                                text: "no strategies in this stage"
                                color: Theme.color.text.muted
                                font.family: Theme.typography.deck.family
                                font.pixelSize: Theme.typography.deck.size
                                font.italic: true
                            }
                        }

                        // ---- Column header row (bench-brief §4) ----------------
                        // STABLE COLUMN GEOMETRY CONTRACT: this header MUST use
                        // the same explicit anchor chain as BenchRow.qml row
                        // content. A per-row RowLayout with two fillWidth
                        // participants would solve widths differently on
                        // different rows, causing columns to shift after a
                        // `rowOrder` reorder. Explicit anchors + fixed Theme
                        // tokens guarantee header and every row resolve to
                        // identical x positions regardless of content.
                        Item {
                            visible: sectionRoot.rowOrder.length > 0
                            width: parent.width
                            height: 32

                            // Right-anchored chain (rightmost first).
                            ColHeader {
                                id: headerAction
                                text: "Action"
                                alignRight: true
                                anchors.right: parent.right
                                anchors.rightMargin: Theme.space[3]
                                anchors.verticalCenter: parent.verticalCenter
                                width: Theme.column.benchAction
                            }
                            ColHeader {
                                id: headerStatus
                                text: "Status"
                                anchors.right: headerAction.left
                                anchors.rightMargin: Theme.space[4]
                                anchors.verticalCenter: parent.verticalCenter
                                width: Theme.column.benchStatus
                            }
                            ColHeader {
                                id: headerTrades
                                text: "Trades"
                                alignRight: true
                                anchors.right: headerStatus.left
                                anchors.rightMargin: Theme.space[4]
                                anchors.verticalCenter: parent.verticalCenter
                                width: Theme.column.benchMetric
                            }
                            ColHeader {
                                id: headerMaxDD
                                text: "Max-DD"
                                alignRight: true
                                anchors.right: headerTrades.left
                                anchors.rightMargin: Theme.space[4]
                                anchors.verticalCenter: parent.verticalCenter
                                width: Theme.column.benchMetric
                            }
                            ColHeader {
                                id: headerSharpe
                                text: "Sharpe"
                                alignRight: true
                                anchors.right: headerMaxDD.left
                                anchors.rightMargin: Theme.space[4]
                                anchors.verticalCenter: parent.verticalCenter
                                width: Theme.column.benchMetric
                            }
                            // Strategy column header — fills remaining space.
                            // Gutter (handleSlot width) sits to its left.
                            ColHeader {
                                id: headerStrategy
                                text: ""
                                anchors.left: parent.left
                                anchors.leftMargin: Theme.space[5] + Theme.space[4]
                                anchors.right: headerSharpe.left
                                anchors.rightMargin: Theme.space[4]
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        // ---- Strategy rows (PR H: explicit-Y container) ------
                        // Rows are positioned by index so drag can reposition
                        // individual rows without reflowing the whole column.
                        // clip:true keeps rows visually inside this section.
                        Item {
                            id: rowsContainer
                            width: parent.width
                            height: sectionRoot.rowOrder.length * sectionRoot.rowHeight
                            clip: true
                            visible: sectionRoot.rowOrder.length > 0

                            Repeater {
                                model: sectionRoot.rowOrder

                                delegate: BenchRow {
                                    id: benchRowDelegate
                                    required property var modelData
                                    required property int index

                                    width: rowsContainer.width

                                    // Y positioning: fully declarative.
                                    // The dragged row's y is driven by dragYOffset
                                    // (set via the delta-based onMoveRequested handler);
                                    // non-dragged rows receive a shift to open the slot.
                                    y: sectionRoot.draggingIndex === index
                                       ? (index * sectionRoot.rowHeight + sectionRoot.dragYOffset)
                                       : (index * sectionRoot.rowHeight + sectionRoot.shiftFor(index))

                                    // Smooth neighbor shifts; disabled for the dragged
                                    // row so it tracks the mouse without lag.
                                    Behavior on y {
                                        enabled: sectionRoot.draggingIndex !== index
                                        NumberAnimation { duration: Theme.motion.fast }
                                    }

                                    stage: sectionData.stage
                                    strategyName: modelData.name || modelData.displayName || ""
                                    strategyId: modelData.strategyId || ""
                                    sharpe: root.formattedSharpe(modelData)
                                    maxDD: root.formattedMaxDD(modelData)
                                    tradeCount: root.formattedTrades(modelData)
                                    // Status prose column (bench-brief §4)
                                    statusWord: modelData.statusWord || ""
                                    statusWordColor: root.statusWordColor(modelData.statusKind || "info")
                                    statusProse: modelData.statusTail || ""
                                    metaLine: root.metaLine(modelData)
                                    // Action slot — wired in PR G.
                                    actionItems: modelData.actions || []
                                    actionVariant: root.actionVariant(modelData)

                                    // PR J: hand the raw row dict to the
                                    // delegate so it can be forwarded to
                                    // BenchEvidenceModal on Open Evidence.
                                    rowData: modelData

                                    onEvidenceRequested: (row) => {
                                        // Mutual exclusion is structural via
                                        // the activeModal enum — the helper
                                        // simply transitions state.
                                        root.openEvidenceModal(row)
                                    }

                                    // PR K: every menu item except Open
                                    // Evidence routes here. Open the
                                    // confirmation preview shell. No backend
                                    // dispatch ever happens on this path.
                                    onActionPreviewRequested: (row, action) => {
                                        root.openConfirmationModal(row, action)
                                    }

                                    // Stable coordinate frame for drag-delta math.
                                    // rowsContainer does NOT move during drag (only its
                                    // child rows reposition), so dragHandle.mapToItem
                                    // returns a pointer-Y that is invariant to the
                                    // dragged row's own motion. Required to prevent the
                                    // negative-feedback oscillation that row-local mouseY
                                    // produces. Do not point this at `root` or at the
                                    // delegate itself.
                                    dragCoordinateItem: rowsContainer

                                    // Drag signal handlers (PR H).
                                    onDragStarted: {
                                        sectionRoot.draggingIndex = index
                                        sectionRoot.targetIndex = index
                                        sectionRoot.dragYOffset = 0
                                        root.anyDragging = true
                                    }
                                    onMoveRequested: (delta) => {
                                        // delta is cumulative offset from press.
                                        // Clamp so the dragged row cannot leave the section.
                                        var maxY = (sectionRoot.rowOrder.length - 1) * sectionRoot.rowHeight
                                        var absY = Math.max(0, Math.min(maxY, index * sectionRoot.rowHeight + delta))
                                        sectionRoot.dragYOffset = absY - (index * sectionRoot.rowHeight)
                                        sectionRoot.targetIndex = Math.max(0,
                                            Math.min(sectionRoot.rowOrder.length - 1,
                                                     Math.round(absY / sectionRoot.rowHeight)))
                                    }
                                    onDragEnded: {
                                        // ORDER MATTERS: snapshot indices, reset all
                                        // drag state, THEN mutate rowOrder. Mutating
                                        // rowOrder first causes the Repeater to tear
                                        // down/recreate delegates — including the one
                                        // whose handler is still executing — which
                                        // invalidates the delegate's QML context and
                                        // makes outer-scope ids (sectionRoot, root)
                                        // unresolvable for the rest of this handler.
                                        // Symptom: "ReferenceError: sectionRoot is
                                        // not defined" on the post-splice reset lines.
                                        var fromIdx = sectionRoot.draggingIndex
                                        var toIdx = sectionRoot.targetIndex
                                        sectionRoot.draggingIndex = -1
                                        sectionRoot.targetIndex = -1
                                        sectionRoot.dragYOffset = 0
                                        root.anyDragging = false
                                        if (toIdx !== fromIdx && fromIdx >= 0 && toIdx >= 0) {
                                            var newOrder = sectionRoot.rowOrder.slice()
                                            var item = newOrder.splice(fromIdx, 1)[0]
                                            newOrder.splice(toIdx, 0, item)
                                            sectionRoot.rowOrder = newOrder
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ---- Page footer -----------------------------------------------
            Column {
                width: parent.width - Theme.space[7] * 2
                spacing: Theme.space[3]

                Rectangle {
                    width: parent.width
                    height: 1
                    color: Theme.color.border.regular
                }

                Text {
                    width: parent.width * 0.78
                    text: "Promotion to paper requires walk-forward gate-pass on every window. "
                        + "Promotion beyond paper requires explicit human review per ADR 0004. "
                        + "Capital-bearing stages remain locked. Kill switch is a global affordance on the Anchor view."
                    color: Theme.color.text.muted
                    font.family: Theme.typography.deck.family
                    font.pixelSize: Theme.typography.deck.size
                    font.italic: true
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // PR J: Evidence modal overlay — single instance shared across all rows.
    // Renders above the Flickable so it covers the ledger when open. Visible
    // is gated on `activeModal === "evidence"`; the modal itself handles its
    // own Escape/outside-click/✕ close semantics.
    // -----------------------------------------------------------------------
    BenchEvidenceModal {
        id: evidenceModal
        anchors.fill: parent
        open:    root.activeModal === "evidence"
        rowData: root.evidenceModalRow
        onCloseRequested: root.closeAllModals()
    }

    // -----------------------------------------------------------------------
    // PR K: Confirmation preview modal overlay — single instance shared
    // across all rows. Strictly visual; its primary action is disabled and
    // labelled "Not wired in v1". No backend command dispatch.
    // -----------------------------------------------------------------------
    BenchConfirmationModal {
        id: confirmationPreview
        anchors.fill: parent
        open:       root.activeModal === "confirmation"
        rowData:    root.confirmationPreviewRow
        actionData: root.confirmationPreviewAction
        onCloseRequested: root.closeAllModals()

        // ADR 0051 Phase C2: the modal now emits `submitted` after a
        // successful demotion. The bridge already refreshes the BenchState
        // read model on the Python side; this handler just dismisses the
        // preview state so the next operator click sees a clean modal.
        onSubmitted: (result) => {
            BenchCommandBridge.dismissCompletion(result.proposal_id)
            root.closeAllModals()
        }
    }

    // -----------------------------------------------------------------------
    // Internal component: column header label (num-label style per brief §4)
    // 10px Public Sans uppercase letter-spaced, text.muted
    // -----------------------------------------------------------------------
    component ColHeader: Text {
        property bool alignRight: false
        color: Theme.color.text.muted
        font.family: Theme.typography.label.xs.family
        font.pixelSize: 10  // intentionally below label.xs (11px) — column headers read as meta, not content
        font.weight: Font.Medium
        font.letterSpacing: Theme.typography.label.xs.letterSpacing
        font.capitalization: Font.AllUppercase
        horizontalAlignment: alignRight ? Text.AlignRight : Text.AlignLeft
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}

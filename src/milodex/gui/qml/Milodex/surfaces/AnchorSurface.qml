// AnchorSurface.qml -- the operator's first-frame operational-state surface.
//
// Renders four panels bound to OperationalState's Q_PROPERTYs:
//   1. Kill switch (with manual-reset confirmation per ADR 0005)
//   2. Market clock (current time, market open/closed, trading mode)
//   3. Account snapshot (equity / cash / buying power)
//   4. Open positions (count + secondary detail)
//
// Token-binding contract: every visual property references a Theme token.
// No raw hex; literal pixel values only for 1px hairlines and the 2px
// accent bar (selection-state convention from PR C).
//
// Architectural pattern (PR D.6): this is the FIRST observability surface
// to bind to a live Python service.  Subsequent surfaces (Strategy Bank,
// Attribution, Paper Status) follow the same structure:
//   - Singleton state object registered via qml_setup.register_qml_types
//   - Q_PROPERTYs wired through Q_PROPERTY notify signals
//   - Threaded data acquisition on a worker, main-thread property updates
//     via QueuedConnection
// Diverging from this pattern requires a follow-up ADR.

import QtQuick
import QtQuick.Window  // local-time formatting helpers
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Stale-window heuristic (DESIGN_SYSTEM.md §5.3 / OperationalState
    // refreshes every 15s).  If the broker hasn't refreshed in >60s we
    // show a 'stale' affordance even when broker_status reads 'connected'.
    // ------------------------------------------------------------------

    readonly property bool _staleData: {
        if (OperationalState.brokerStatus === "stale") return true
        if (OperationalState.lastRefreshedAt === "")    return false
        var refreshed = Date.parse(OperationalState.lastRefreshedAt)
        if (isNaN(refreshed)) return false
        return (Date.now() - refreshed) > 60000
    }

    readonly property bool _brokerError: OperationalState.brokerStatus === "error"

    readonly property string _emDash: "—"

    // ------------------------------------------------------------------
    // Money formatter -- mirrors cli/_shared.format_money exactly so the
    // GUI and CLI never disagree on display ("$1,234.56").
    // ------------------------------------------------------------------

    function _formatMoney(amount) {
        if (root._brokerError) return root._emDash
        var sign = amount < 0 ? "-$" : "$"
        var abs = Math.abs(amount)
        var whole = Math.floor(abs)
        var fraction = Math.round((abs - whole) * 100)
        if (fraction === 100) { whole += 1; fraction = 0 }
        var fracStr = fraction < 10 ? ("0" + fraction) : ("" + fraction)
        // Insert thousands separators in the integer part.
        var wholeStr = "" + whole
        var withCommas = ""
        for (var i = 0; i < wholeStr.length; i++) {
            if (i > 0 && (wholeStr.length - i) % 3 === 0) withCommas += ","
            withCommas += wholeStr[i]
        }
        return sign + withCommas + "." + fracStr
    }

    // ------------------------------------------------------------------
    // Local-time clock for the market panel.  Updated once per second.
    // ------------------------------------------------------------------

    property string _localTimeStr: ""

    function _updateClock() {
        var d = new Date()
        function pad(n) { return n < 10 ? "0" + n : "" + n }
        root._localTimeStr =
            pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds())
    }

    Timer {
        interval: 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root._updateClock()
    }

    // ------------------------------------------------------------------
    // Layout: broker-error banner + 4-column grid of Surface panels,
    // stacked in a Column so the banner collapses (visible: false) cleanly
    // without requiring conditional anchor targets.
    // ------------------------------------------------------------------

    Column {
        anchors {
            top:    parent.top
            left:   parent.left
            right:  parent.right
            margins: Theme.space[6]
        }
        spacing: Theme.space[3]

        // ==============================================================
        // Broker-error / stale-data banner (Item 4).
        //
        // Full-width indicator so the Account panel's three em-dashes are
        // never silent — the operator sees one clear message regardless of
        // which panel they're looking at.  Banner replaces the inline
        // error text previously shown only in the Positions panel.
        // ==============================================================

        Item {
            id: brokerBanner
            width: parent.width
            height: bannerRect.implicitHeight
            visible: root._brokerError || root._staleData

            Rectangle {
                id: bannerRect
                anchors.left: parent.left
                anchors.right: parent.right
                implicitHeight: bannerRow.implicitHeight + Theme.space[3] * 2
                color: Theme.color.surface.base
                radius: Theme.radius.md
                border.width: 1
                border.color: root._brokerError ? Theme.status.negative : Theme.status.warning

                Row {
                    id: bannerRow
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: parent.top
                        margins: Theme.space[3]
                    }
                    spacing: Theme.space[2]

                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 8
                        height: 8
                        radius: Theme.radius.full
                        color: root._brokerError ? Theme.status.negative : Theme.status.warning
                    }

                    Text {
                        text: root._brokerError
                                ? ("Broker unreachable: " + OperationalState.brokerErrorMessage)
                                : ("Stale data — last refresh "
                                   + (OperationalState.lastRefreshedAt || "(never)"))
                        color: root._brokerError ? Theme.status.negative : Theme.status.warning
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.md.size
                        wrapMode: Text.WordWrap
                        width: parent.width - 8 - Theme.space[2]
                    }
                }
            }
        }

        // ==============================================================
        // Panel grid
        // ==============================================================

        Grid {
            id: panelGrid
            width: parent.width
            columns: Math.max(1, Math.floor((root.width - Theme.space[6] * 2 + Theme.space[3])
                                            / (260 + Theme.space[3])))
            spacing: Theme.space[3]

            // ============================================================
            // Panel 1: Kill switch
            // ============================================================

            Surface {
                id: killSwitchPanel
                width: 260
                // Item 1: when the kill switch is active, grow to 220px so
                // the 5-stacked items (label / status / reason / triggered-at
                // / Reset button) fit without overflowing the panel.  160px
                // holds the inactive 3-item column.  No animation — per
                // DESIGN_SYSTEM.md §5.3, layout shifts on state change are
                // instant; animation here would obscure operational urgency.
                height: OperationalState.killSwitchActive ? 220 : 160

                // Background tint: when the kill switch is fired, the panel
                // adopts a low-opacity rust wash so the surface itself reads
                // as "this is a problem" before any text is parsed.
                Rectangle {
                    anchors.fill: parent
                    color: OperationalState.killSwitchActive
                            ? Qt.rgba(Theme.status.negative.r,
                                      Theme.status.negative.g,
                                      Theme.status.negative.b, 0.10)
                            : "transparent"
                    radius: Theme.radius.lg
                }

                Column {
                    anchors.fill: parent
                    spacing: Theme.space[2]

                    Text {
                        text: "KILL SWITCH"
                        color: Theme.color.text.muted
                        font.family:         Theme.typography.label.xs.family
                        font.pixelSize:      Theme.typography.label.xs.size
                        font.weight:         Theme.typography.label.xs.weight
                        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }

                    // Status indicator + label
                    Row {
                        spacing: Theme.space[2]

                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width:  10
                            height: 10
                            radius: Theme.radius.full
                            color: OperationalState.killSwitchActive
                                    ? Theme.status.negative
                                    : Theme.status.positive
                        }

                        Text {
                            text: OperationalState.killSwitchActive
                                    ? "KILL SWITCH FIRED"
                                    : "OPERATIONAL"
                            color: OperationalState.killSwitchActive
                                    ? Theme.status.negative
                                    : Theme.color.text.primary
                            font.family:    Theme.typography.body.md.family
                            font.pixelSize: Theme.typography.body.md.size
                            font.weight:    Font.Medium
                        }
                    }

                    // Caption / reason
                    Text {
                        width: parent.width
                        text: OperationalState.killSwitchActive
                                ? ("Reason: " + (OperationalState.killSwitchReason || "(unspecified)"))
                                : "Manual reset required if triggered."
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                        wrapMode:       Text.WordWrap
                    }

                    // Triggered-at + reset button (only when active)
                    Text {
                        visible: OperationalState.killSwitchActive
                        width:   parent.width
                        text: OperationalState.killSwitchTriggeredAt !== ""
                                ? ("Triggered: " + OperationalState.killSwitchTriggeredAt)
                                : ""
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.data.xs.family
                        font.pixelSize: Theme.typography.data.xs.size
                        font.features:  Theme.typography.data.xs.features
                        elide:          Text.ElideRight
                    }

                    Button {
                        visible: OperationalState.killSwitchActive
                        variant: "critical"
                        text:    "Reset (manual confirm)"
                        onClicked: confirmDialog.visible = true
                    }
                }
            }

            // ============================================================
            // Panel 2: Market clock
            // ============================================================

            Surface {
                width: 260
                height: 160

                Column {
                    anchors.fill: parent
                    spacing: Theme.space[2]

                    Text {
                        text: "MARKET"
                        color: Theme.color.text.muted
                        font.family:         Theme.typography.label.xs.family
                        font.pixelSize:      Theme.typography.label.xs.size
                        font.weight:         Theme.typography.label.xs.weight
                        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }

                    // Market clock time stays Mono — digital-clock convention;
                    // serif clocks read wrong at small scale.
                    Text {
                        text: root._localTimeStr
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.data.md.family
                        font.pixelSize: Theme.typography.display.md.size
                        font.features:  Theme.typography.data.md.features
                    }

                    Row {
                        spacing: Theme.space[2]

                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width:  8
                            height: 8
                            radius: Theme.radius.full
                            color: root._brokerError
                                    ? Theme.color.text.muted
                                    : (OperationalState.marketOpen
                                        ? Theme.status.positive
                                        : Theme.color.text.muted)
                        }

                        Text {
                            text: root._brokerError
                                    ? root._emDash
                                    : (OperationalState.marketOpen ? "Open" : "Closed")
                            color: Theme.color.text.secondary
                            font.family:    Theme.typography.body.sm.family
                            font.pixelSize: Theme.typography.body.sm.size
                        }
                    }

                    Text {
                        text: "Mode: " + OperationalState.tradingMode
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                }
            }

            // ============================================================
            // Panel 3: Account snapshot
            // ============================================================

            Surface {
                width: 260
                height: 160

                Column {
                    anchors.fill: parent
                    spacing: Theme.space[2]

                    Text {
                        text: "ACCOUNT"
                        color: Theme.color.text.muted
                        font.family:         Theme.typography.label.xs.family
                        font.pixelSize:      Theme.typography.label.xs.size
                        font.weight:         Theme.typography.label.xs.weight
                        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }

                    // Hero numeric: Newsreader serif per editorial direction.
                    // Newspapers set hero numerics in display serif — it reads
                    // as "the number that matters on this panel."  The tabular
                    // detail rows below (Cash / Buying) stay Mono for column
                    // alignment; that split is intentional.
                    Text {
                        text: root._formatMoney(OperationalState.equity)
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.display.md.family
                        font.pixelSize: Theme.typography.display.md.size
                        font.weight:    Theme.typography.display.md.weight
                    }

                    Text {
                        text: "Cash    " + root._formatMoney(OperationalState.cash)
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.data.sm.family
                        font.pixelSize: Theme.typography.data.sm.size
                        font.features:  Theme.typography.data.sm.features
                    }

                    Text {
                        text: "Buying  " + root._formatMoney(OperationalState.buyingPower)
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.data.sm.family
                        font.pixelSize: Theme.typography.data.sm.size
                        font.features:  Theme.typography.data.sm.features
                    }
                }
            }

            // ============================================================
            // Panel 4: Open positions
            // ============================================================

            Surface {
                width: 260
                height: 160

                Column {
                    anchors.fill: parent
                    spacing: Theme.space[2]

                    Text {
                        text: "POSITIONS"
                        color: Theme.color.text.muted
                        font.family:         Theme.typography.label.xs.family
                        font.pixelSize:      Theme.typography.label.xs.size
                        font.weight:         Theme.typography.label.xs.weight
                        font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                        font.capitalization: Font.AllUppercase
                    }

                    // Hero numeric: Newsreader serif per editorial direction
                    // (same rationale as Account equity above — the "how many
                    // positions?" read is the single fact this panel surfaces).
                    Text {
                        text: root._brokerError
                                ? root._emDash
                                : (OperationalState.openPositionsCount === 0
                                    ? "0"
                                    : "" + OperationalState.openPositionsCount)
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.display.md.family
                        font.pixelSize: Theme.typography.display.md.size
                        font.weight:    Theme.typography.display.md.weight
                    }

                    Text {
                        text: root._brokerError
                                ? "Broker unreachable"
                                : (OperationalState.openPositionsCount === 0
                                    ? "No open positions."
                                    : (OperationalState.openPositionsCount === 1
                                        ? "1 open position"
                                        : (OperationalState.openPositionsCount + " open positions")))
                        color: Theme.color.text.secondary
                        font.family:    Theme.typography.body.sm.family
                        font.pixelSize: Theme.typography.body.sm.size
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Confirmation dialog for kill-switch reset (ADR 0005).
    //
    // Type-to-confirm prevents muscle-memory double-click and rate-limits
    // resets to typing speed; per round-2 reviewer feedback.  The operator
    // must type the value of OperationalState.resetKillSwitchToken before
    // the "Yes, reset" button becomes enabled.
    //
    // Implemented as a self-contained modal Item rather than a Dialog
    // (QtQuick.Controls Dialog brings the QQuickStyleItem cache issue
    // documented across the design-system components).  The Item is
    // overlaid on top of all panels, fills the surface, and intercepts
    // clicks via a transparent MouseArea.
    // ------------------------------------------------------------------

    Item {
        id: confirmDialog
        anchors.fill: parent
        visible: false

        // Clear the confirmation input each time the dialog closes so a
        // re-open always starts empty (no lingering text from a prior attempt).
        onVisibleChanged: {
            if (!visible) confirmInput.text = ""
        }

        // Block click-through behind the dialog
        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            // Prevent clicks reaching the panels behind.
            onClicked: { /* swallow */ }
        }

        Rectangle {
            anchors.fill: parent
            color: Qt.rgba(0, 0, 0, 0.55)
        }

        Surface {
            anchors.centerIn: parent
            width:  Math.min(parent.width  - Theme.space[6] * 2, 480)
            height: Math.min(parent.height - Theme.space[6] * 2, 280)

            Column {
                anchors.fill: parent
                spacing: Theme.space[3]

                Text {
                    width: parent.width
                    text: "Reset kill switch?"
                    color: Theme.color.text.primary
                    font.family:    Theme.typography.display.sm.family
                    font.pixelSize: Theme.typography.display.sm.size
                    font.weight:    Theme.typography.display.sm.weight
                }

                Text {
                    width: parent.width
                    text: "This will allow the kill switch to be cleared. " +
                          "Trading will resume only when explicitly re-enabled. " +
                          "Continue?"
                    color: Theme.color.text.secondary
                    font.family:    Theme.typography.body.md.family
                    font.pixelSize: Theme.typography.body.md.size
                    wrapMode:       Text.WordWrap
                }

                // Type-to-confirm input
                Text {
                    width: parent.width
                    text: "Type “" + OperationalState.resetKillSwitchToken + "” to enable Reset"
                    color: Theme.color.text.muted
                    font.family:    Theme.typography.body.sm.family
                    font.pixelSize: Theme.typography.body.sm.size
                }

                Rectangle {
                    width: parent.width
                    height: confirmInput.implicitHeight + Theme.space[2] * 2
                    color: Theme.color.surface.base
                    border.color: Theme.color.border.regular
                    border.width: 1
                    radius: Theme.radius.md

                    // Placeholder hint -- shown when the input is empty.
                    Text {
                        anchors {
                            left: parent.left
                            right: parent.right
                            verticalCenter: parent.verticalCenter
                            leftMargin: Theme.space[2]
                            rightMargin: Theme.space[2]
                        }
                        visible: confirmInput.text === ""
                        text: OperationalState.resetKillSwitchToken
                        color: Theme.color.text.muted
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.md.size
                    }

                    TextInput {
                        id: confirmInput
                        anchors {
                            left: parent.left
                            right: parent.right
                            verticalCenter: parent.verticalCenter
                            leftMargin: Theme.space[2]
                            rightMargin: Theme.space[2]
                        }
                        color: Theme.color.text.primary
                        font.family:    Theme.typography.body.md.family
                        font.pixelSize: Theme.typography.body.md.size
                        clip: true
                    }
                }

                Row {
                    spacing: Theme.space[3]
                    anchors.right: parent.right

                    Button {
                        variant: "secondary"
                        text:    "Cancel"
                        onClicked: confirmDialog.visible = false
                    }
                    Button {
                        id: confirmResetButton
                        variant: "critical"
                        text:    "Yes, reset"
                        // Enabled only when the operator has typed the exact token.
                        // Prevents muscle-memory click-through; the Button disables
                        // its MouseArea so no click fires unless the condition holds.
                        enabled: confirmInput.text === OperationalState.resetKillSwitchToken
                        // The critical variant sets background to transparent when
                        // disabled (Button.qml _bgColor fallback), which would make the
                        // button invisible.  Override opacity so the button remains
                        // visually present but clearly inactive.
                        opacity: enabled ? 1.0 : 0.5
                        onClicked: {
                            OperationalState.reset_kill_switch(OperationalState.resetKillSwitchToken)
                            confirmDialog.visible = false
                        }
                    }
                }
            }
        }
    }
}

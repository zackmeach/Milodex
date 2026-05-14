// RightRailDossier.qml — right-anchored evidence / reference dossier rail.
//
// Per DESIGN.md v0.2 §5.11 and DESIGN_SYSTEM.md v0.2 §7.6, reference and
// browse content (Evidence Snapshot, history detail, configuration views)
// belongs in a right-rail dossier rather than a centered modal. The rail
// accompanies the originating row — the operator reads it next to the row
// it describes, with surrounding context still visible.
//
// Contract distinctions from a confirmation modal:
//
//   - No scrim. The Bench (or other host surface) underneath stays
//     visible and interactive — scrolling, opening another row's action
//     menu, switching tabs. The rail is a column of the page, not a
//     floating box.
//   - No outside-click dismissal. Ordinary Bench interactions do not
//     accidentally close the dossier. Close paths: explicit CLOSE button
//     in the rail header, or Escape. This is the "carefully defined"
//     outside-click semantic specified in the PR-4 brief.
//   - No card chrome inside the rail. Sections are bounded by hairline
//     rules per the Definition Block pattern (DESIGN_SYSTEM.md §7.7).
//   - Close affordance is the word "CLOSE" set in label.xs tracked
//     uppercase, not an X glyph (DESIGN_SYSTEM.md §7.6 contract).
//
// Public API:
//   property bool   open               — show / hide the rail
//   property string headerLabel        — eyebrow small-caps above title
//   property string headerTitle        — display-serif title
//   property string footerNote         — italic muted footer text (optional)
//   signal closeRequested()            — emitted on CLOSE or Escape
//   default property alias content     — body slot (a vertical Column)
//
// The host (e.g. BenchSurface) owns exactly one instance per dossier role
// and toggles `open` in response to a row's request signal.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property bool   open: false
    property string headerLabel: ""
    property string headerTitle: ""
    property string footerNote: ""

    signal closeRequested()

    // Body slot — content children attach to the bodyHolder Column inside
    // the rail's scroll area. Children are laid out vertically with
    // Theme.space[2] spacing.
    default property alias content: bodyHolder.children

    // ------------------------------------------------------------------
    // Behavior
    // ------------------------------------------------------------------

    visible: open
    focus:   open

    onOpenChanged: {
        if (open) forceActiveFocus()
    }

    Keys.onEscapePressed: (event) => {
        if (open) {
            event.accepted = true
            root.closeRequested()
        }
    }

    // ------------------------------------------------------------------
    // Rail container — right-anchored, full host height, no scrim
    // ------------------------------------------------------------------

    Rectangle {
        id: rail
        anchors.right:  parent.right
        anchors.top:    parent.top
        anchors.bottom: parent.bottom
        width: Math.min(420, Math.max(360, parent.width * 0.40))

        color:        Theme.color.surface.base
        border.color: Theme.color.border.subtle
        border.width: 1
        // Editorial register: sharp vertical leading edge, not a rounded
        // sheet. Per DESIGN_SYSTEM.md §7.6: 2–4px max radius anywhere;
        // 0 here reads as a column of the page.
        radius: 0

        // Swallow clicks on the rail itself. Without onClicked, clicks on
        // the rail surface do nothing — they don't propagate to the host
        // surface underneath, and they don't dismiss the rail. Outside-
        // click dismissal is intentionally absent (see file header).
        MouseArea {
            anchors.fill: parent
            hoverEnabled: false
            preventStealing: true
        }

        // -- Header: eyebrow + title + CLOSE button --
        Item {
            id: headerBlock
            anchors.top:    parent.top
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.topMargin:    Theme.space[6]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[5]
            implicitHeight: headerContent.implicitHeight

            Column {
                id: headerContent
                anchors.left:        parent.left
                anchors.right:       closeButton.left
                anchors.rightMargin: Theme.space[3]
                spacing: Theme.space[1]

                Text {
                    visible: root.headerLabel !== ""
                    text:    root.headerLabel
                    color:   Theme.color.text.muted
                    font.family:         Theme.typography.label.xs.family
                    font.pixelSize:      Theme.typography.label.xs.size
                    font.weight:         Theme.typography.label.xs.weight
                    font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                    font.capitalization: Font.AllUppercase
                }

                Text {
                    width:   parent.width
                    visible: root.headerTitle !== ""
                    text:    root.headerTitle
                    color:   Theme.color.text.primary
                    font.family:    Theme.typography.display.sm.family
                    font.pixelSize: Theme.typography.display.sm.size
                    font.weight:    Theme.typography.display.sm.weight
                    wrapMode:       Text.NoWrap
                    elide:          Text.ElideRight
                }
            }

            // CLOSE — text label, not an X glyph. Per DESIGN_SYSTEM.md
            // §7.6 contract: "close-affordance is a ghost button with
            // typography.label.xs 'CLOSE', not an X glyph."
            Text {
                id: closeButton
                anchors.right:          parent.right
                anchors.verticalCenter: headerContent.verticalCenter
                text:  "CLOSE"
                color: closeMa.containsMouse
                       ? Theme.color.text.primary
                       : Theme.color.text.muted
                font.family:         Theme.typography.label.xs.family
                font.pixelSize:      Theme.typography.label.xs.size
                font.weight:         Theme.typography.label.xs.weight
                font.letterSpacing:  Theme.typography.label.xs.letterSpacing
                font.capitalization: Font.AllUppercase

                Behavior on color { ColorAnimation { duration: Theme.motion.fast } }

                MouseArea {
                    id: closeMa
                    anchors.fill: parent
                    anchors.margins: -8
                    hoverEnabled: true
                    cursorShape:  Qt.PointingHandCursor
                    onClicked:    root.closeRequested()
                }
            }
        }

        // Hairline below header — Definition Block pattern
        Rectangle {
            id: headerRule
            anchors.top:    headerBlock.bottom
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.topMargin:    Theme.space[4]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[6]
            height: 1
            color:  Theme.color.border.subtle
        }

        // -- Scrollable body --
        Flickable {
            id: scrollArea
            anchors.top:    headerRule.bottom
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.bottom: footerBlock.top
            anchors.topMargin:    Theme.space[4]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[6]
            anchors.bottomMargin: Theme.space[3]

            contentWidth:  width
            contentHeight: bodyHolder.implicitHeight
            clip: true
            flickableDirection: Flickable.VerticalFlick

            // Wheel scroll. Wheel-event absorption is scoped to the rail
            // surface (the rail's outer MouseArea has hoverEnabled:false
            // and preventStealing:true so wheel events outside the rail
            // continue to scroll the host surface).
            WheelHandler {
                target: null
                onWheel: (event) => {
                    var max = Math.max(0, scrollArea.contentHeight - scrollArea.height)
                    var step = event.angleDelta.y / 120 * 40
                    scrollArea.contentY = Math.max(0, Math.min(max, scrollArea.contentY - step))
                    event.accepted = true
                }
            }

            // Body slot — `default property alias content: bodyHolder.children`
            // attaches user-supplied children here as siblings.
            Column {
                id: bodyHolder
                width: parent.width
                spacing: Theme.space[2]
            }
        }

        // -- Optional footer (e.g. mandatory disclaimer) --
        Item {
            id: footerBlock
            anchors.bottom: parent.bottom
            anchors.left:   parent.left
            anchors.right:  parent.right
            anchors.bottomMargin: Theme.space[5]
            anchors.leftMargin:   Theme.space[6]
            anchors.rightMargin:  Theme.space[6]
            visible: root.footerNote !== ""
            implicitHeight: visible ? (footerRule.height + Theme.space[3] + footerText.implicitHeight) : 0

            Rectangle {
                id: footerRule
                anchors.top:    parent.top
                anchors.left:   parent.left
                anchors.right:  parent.right
                height: 1
                color:  Theme.color.border.subtle
            }

            Text {
                id: footerText
                anchors.top:        footerRule.bottom
                anchors.left:       parent.left
                anchors.right:      parent.right
                anchors.topMargin:  Theme.space[3]
                text:  root.footerNote
                color: Theme.color.text.muted
                font.family:    Theme.typography.deck.family
                font.pixelSize: Theme.typography.deck.size
                font.italic:    true
                wrapMode:       Text.WordWrap
            }
        }
    }
}

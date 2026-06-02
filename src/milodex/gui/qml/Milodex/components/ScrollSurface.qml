// ScrollSurface.qml — the shared Flickable scaffold for editorial surfaces.
//
// Extracts the per-surface re-roll of:
//   Flickable { contentWidth: width; contentHeight: col.implicitHeight + space[7]*2;
//               clip: true; flickableDirection: VerticalFlick }
//   + the centered-max-width (Front/Ledger) OR full-width-with-margins (Desk)
//     content column with space[7] top padding.
//
// Public API:
//   default content        — slotted child (the surface's page Column). Its
//                            implicitHeight drives contentHeight.
//   interactive : bool     — native Flickable property (default true). Desk
//                            passes false for deterministic wheel-only scroll.
//   maxContentWidth : real — 0 (default) = full-width with space[7] side
//                            margins (Desk); >0 = centered column capped at
//                            this width (Front 720 prose, Ledger 1280 table).
//   contentHeight : real   — native Flickable property; a surface binds
//                            captureContentHeight to it (same contract as the
//                            legacy `scroller.contentHeight`).
//
// The slotted content is given a `width` and `x` by this scaffold; surfaces
// should bind their page column to `parent.width` as usual and NOT set their
// own x/width. Top padding of space[7] is applied via the holder's `y`.
//
// Contract: slotted content MUST derive its height from implicit/content
// height (a `Column`/implicit-height item), NOT via `anchors.fill` or an
// anchored height. The holder sizes to `childrenRect.height`, so an anchored
// child collapses childrenRect.height — and therefore contentHeight — to 0.

import QtQuick
import Milodex 1.0

Flickable {
    id: scroller

    property real maxContentWidth: 0

    // Slot: the surface's page column lands inside `holder`. Its height is the
    // single driver of contentHeight (matching the legacy per-surface formula:
    // column implicitHeight + space[7] * 2).
    default property alias content: holder.children

    anchors.fill: parent
    contentWidth: width
    contentHeight: holder.childrenRect.height + Theme.space[7] * 2
    clip: true
    flickableDirection: Flickable.VerticalFlick

    // Content holder. Width is either centered-and-capped (maxContentWidth > 0)
    // or full-width inset by space[7] each side (maxContentWidth === 0). Top
    // padding of space[7] is applied via `y`.
    Item {
        id: holder
        y: Theme.space[7]
        height: childrenRect.height
        width: scroller.maxContentWidth > 0
               ? Math.min(scroller.width - Theme.space[7] * 2, scroller.maxContentWidth)
               : scroller.width - Theme.space[7] * 2
        x: scroller.maxContentWidth > 0
           ? (scroller.width - width) / 2
           : Theme.space[7]
    }
}

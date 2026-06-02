// SurfaceBase.qml — the duck-typed surface contract root.
//
// Every top-level surface (Front / Ledger / Desk / …) shares two contract
// members that Main.qml's Loader + QQuickView (SizeRootObjectToView) read
// back across the Loader boundary:
//
//   captureContentHeight : real — the scroll extent Main.qml measures.
//                                 A surface binds this to its ScrollSurface's
//                                 contentHeight.
//   sessionBag           : var  — the per-session state map Main.qml threads
//                                 in on surface load (timeFormat, slice
//                                 selections, …). null when unparameterized.
//
// SurfaceBase is intentionally minimal: a plain fill-parent Item so it
// behaves exactly like the hand-rolled `Item { id: root }` roots it
// replaces. It adds no chrome, no background, no layout — surfaces compose
// those on top via ScrollSurface.

import QtQuick

Item {
    id: root

    // Scroll extent Main.qml measures (bound to ScrollSurface.contentHeight).
    property real captureContentHeight: 0

    // Per-session state map threaded in by Main.qml on surface load.
    property var sessionBag: null
}

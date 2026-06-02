// SafetyBanner.qml — Sober typeset stamp band for the Bench confirmation
// modal's COMMAND DRAFT PREVIEW section. Extracted from
// BenchConfirmationModal.qml (PR 13 decompose).
//
// NOT a yellow alert bar and NOT a rotated rubber-stamp graphic. Per
// DESIGN_SYSTEM.md v0.2 §7.6's confirmation-modal interior contract:
// "Safety banner (when applicable): small-caps typography.label.xs red set
// in a band bounded by hairline rules above and below, color status.negative.
// Never a yellow alert bar and never a decorative rubber-stamp graphic."
//
// Used to render "NOT SUBMITTABLE" / "SUBMIT CAPABLE" as a typeset stamp
// band before the verbatim banner copy.
//
// This is deliberately NOT ElevatedPostureBanner — that component is the
// risk-posture chrome on the operating surfaces; this is a one-line
// hairline-bounded stamp for the modal interior.

import QtQuick
import Milodex 1.0

Column {
    id: safetyBannerRoot
    property string text: ""
    width: parent ? parent.width : 0
    spacing: 0

    Rectangle {
        width:  parent.width
        height: 1
        color:  Theme.status.negative
    }

    Item {
        width:  parent.width
        implicitHeight: bannerText.implicitHeight + Theme.space[3] * 2

        Text {
            id: bannerText
            anchors.centerIn: parent
            text:  safetyBannerRoot.text
            color: Theme.status.negative
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Font.DemiBold
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing + 0.6
            font.capitalization: Font.AllUppercase
        }
    }

    Rectangle {
        width:  parent.width
        height: 1
        color:  Theme.status.negative
    }
}

// ElevatedPostureBanner.qml — persistent oxblood band when Aggressive profile is active.
//
// Sits below the top chrome (topBar), above the surface Loader in Main.qml.
// Visible only when activeProfile === "aggressive".  Height animates from 0 to
// Theme.space[4] so the band slides in rather than popping.  The text is italic
// editorial copy: "ELEVATED POSTURE · AGGRESSIVE PROFILE ACTIVE".
//
// Design rationale (ADR 0054 §8 "visibly active" doctrine): elevated posture must
// be unmistakable to the operator at a glance on any surface.  The oxblood band
// across the full viewport width achieves this without the modal interruption that
// would be needed if the banner were surface-local.
//
// Tokens consumed:
//   color.brand.accent   — oxblood fill
//   color.text.onBrand   — cream text on oxblood
//   typography.label.sm  — banner text scale (fallback to label.xs if sm absent)
//   space[4]             — expanded height

import QtQuick
import Milodex 1.0

Rectangle {
    id: root

    // activeProfile: bound from Main.qml._activeProfile.
    // When === "aggressive" the banner is visible.
    property string activeProfile: ""

    readonly property bool _elevated: activeProfile === "aggressive"

    visible: _elevated || height > 0   // keep in layout during collapse animation
    height: _elevated ? Theme.space[4] : 0
    color: Theme.color.brand.accent

    Behavior on height {
        NumberAnimation { duration: 150; easing.type: Easing.OutCubic }
    }

    Text {
        anchors.centerIn: parent
        visible: root._elevated
        text: "ELEVATED POSTURE · AGGRESSIVE PROFILE ACTIVE"
        color: Theme.color.text.onBrand
        font.family:        Theme.typography.label.xs.family
        font.pixelSize:     Theme.typography.label.xs.size
        font.italic:        true
        font.weight:        Font.DemiBold
        font.letterSpacing: Theme.typography.label.xs.letterSpacing + 0.4
        font.capitalization: Font.AllUppercase
    }
}

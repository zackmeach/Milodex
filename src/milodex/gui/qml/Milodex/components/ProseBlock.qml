// ProseBlock.qml — Multi-line italic prose block used by the Bench
// confirmation modal's INTENT PACKET, SAFETY BOUNDARY, and operator-input
// sections. Extracted from BenchConfirmationModal.qml (PR 13 decompose).

import QtQuick
import Milodex 1.0

Text {
    width: parent ? parent.width : 0
    color: Theme.color.text.secondary
    font.family:    Theme.typography.deck.family
    font.pixelSize: Theme.typography.deck.size
    font.italic:    true
    wrapMode:       Text.WordWrap
}

// SectionRule.qml — Section separator hairline for the Bench confirmation
// modal body. Extracted from BenchConfirmationModal.qml (PR 13 decompose).
// Consistent with PR K body separators (border.subtle, 1px).

import QtQuick
import Milodex 1.0

Rectangle {
    width: parent ? parent.width : 0
    height: 1
    color: Theme.color.border.subtle
}

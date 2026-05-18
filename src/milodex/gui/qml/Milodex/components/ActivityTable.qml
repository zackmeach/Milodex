// ActivityTable.qml — Scrollable activity event table with filter.
//
// Tokens consumed:
//   color.surface.base      — background
//   color.border.subtle     — row separator
//   color.text.primary      — primary cell text
//   color.text.muted        — secondary / filtered-state text
//   color.text.secondary    — de-emphasised metadata
//   status.positive         — tone "positive"
//   status.negative         — tone "negative"
//   status.warning          — tone "warning"
//   typography.data.sm      — data cells (mono)
//   typography.body.sm      — subject / label cells
//   typography.label.xs     — column header labels (uppercase)
//   space[2], space[3]      — padding
//   radius.sm               — empty-state container
//
// Each row in `rows` is expected to have:
//   { ts: string, kind: string, subject: string, detail: string, tone: string }
// Tone → token mapping is identical to RollupCell (spec §5 — centralised here).
//
// MOTION DISCIPLINE: no animations anywhere in this component.
//
// Public API:
//   rows       : var    — array of row objects
//   filter     : string — free-text filter applied to kind + subject + detail
//   kindFilter : string — exact kind match ("order"|"rejection"|"signal"|"fill"|"")

import QtQuick
import Milodex 1.0

Item {
    id: root

    property var    rows:       []
    property string filter:     ""
    property string kindFilter: ""

    // Tone → editorial token mapping (centralised per spec §5).
    // keep in sync with RollupCell.qml's tone→color mapping
    function _toneColor(tone) {
        if (tone === "positive") return Theme.status.positive
        if (tone === "negative") return Theme.status.negative
        if (tone === "warning")  return Theme.status.warning
        if (tone === "muted")    return Theme.color.text.muted
        // "data" and anything else → primary text
        return Theme.color.text.primary
    }

    // Filter rows in JS — purely computed, no read-model coupling.
    // Two independent predicates ANDed:
    //   1. kindFilter — exact case-sensitive equality on row.kind ("" = pass all)
    //   2. filter     — free-text substring on kind+subject+detail ("" = pass all)
    readonly property var _filteredRows: {
        var result = []
        var lower = root.filter === "" ? "" : root.filter.toLowerCase()
        for (var i = 0; i < root.rows.length; i++) {
            var r = root.rows[i]
            // Predicate 1: exact kind gate
            if (root.kindFilter !== "" && String(r.kind) !== root.kindFilter)
                continue
            // Predicate 2: free-text substring
            if (lower !== "") {
                var haystack = (r.kind    || "") + " " +
                               (r.subject || "") + " " +
                               (r.detail  || "")
                if (haystack.toLowerCase().indexOf(lower) === -1)
                    continue
            }
            result.push(r)
        }
        return result
    }

    implicitWidth:  400
    implicitHeight: 300

    // Column header row
    Item {
        id: headerRow
        anchors.top:   parent.top
        anchors.left:  parent.left
        anchors.right: parent.right
        height:        tsHeader.implicitHeight + Theme.space[1] * 2

        Text {
            id: tsHeader
            anchors.left:           parent.left
            anchors.leftMargin:     Theme.space[2]
            anchors.verticalCenter: parent.verticalCenter
            width:                  Theme.column.deskTs
            text:                   "TIME"
            color:                  Theme.color.text.muted
            font.family:            Theme.typography.label.xs.family
            font.pixelSize:         Theme.typography.label.xs.size
            font.weight:            Theme.typography.label.xs.weight
            font.letterSpacing:     Theme.typography.label.xs.letterSpacing
            font.capitalization:    Font.AllUppercase
        }

        Text {
            anchors.left:           tsHeader.right
            anchors.leftMargin:     Theme.space[2]
            anchors.verticalCenter: parent.verticalCenter
            width:                  Theme.column.deskKind
            text:                   "KIND"
            color:                  Theme.color.text.muted
            font.family:            Theme.typography.label.xs.family
            font.pixelSize:         Theme.typography.label.xs.size
            font.weight:            Theme.typography.label.xs.weight
            font.letterSpacing:     Theme.typography.label.xs.letterSpacing
            font.capitalization:    Font.AllUppercase
        }

        Text {
            anchors.left:           parent.left
            anchors.leftMargin:     Theme.column.deskSubject
            anchors.verticalCenter: parent.verticalCenter
            text:                   "SUBJECT / DETAIL"
            color:                  Theme.color.text.muted
            font.family:            Theme.typography.label.xs.family
            font.pixelSize:         Theme.typography.label.xs.size
            font.weight:            Theme.typography.label.xs.weight
            font.letterSpacing:     Theme.typography.label.xs.letterSpacing
            font.capitalization:    Font.AllUppercase
        }

        // Header bottom divider
        Rectangle {
            anchors.bottom: parent.bottom
            anchors.left:   parent.left
            anchors.right:  parent.right
            height: 1
            color:  Theme.color.border.subtle
        }
    }

    // Scrollable rows
    ListView {
        id: listView
        anchors.top:    headerRow.bottom
        anchors.left:   parent.left
        anchors.right:  parent.right
        anchors.bottom: parent.bottom
        clip:           true
        model:          root._filteredRows

        // Empty state
        Text {
            anchors.centerIn: parent
            visible:          listView.count === 0
            text:             (root.filter !== "" || root.kindFilter !== "") ? "No matching activity" : "No activity"
            color:            Theme.color.text.muted
            font.family:      Theme.typography.body.sm.family
            font.pixelSize:   Theme.typography.body.sm.size
            font.italic:      true
        }

        delegate: Item {
            id: delegateItem
            width:  listView.width
            height: subjectText.implicitHeight + Theme.space[2] * 2

            // Row bottom divider
            Rectangle {
                anchors.bottom: parent.bottom
                anchors.left:   parent.left
                anchors.right:  parent.right
                height: 1
                color:  Theme.color.border.subtle
            }

            // Timestamp
            Text {
                id: tsText
                anchors.left:           parent.left
                anchors.leftMargin:     Theme.space[2]
                anchors.verticalCenter: parent.verticalCenter
                width:                  Theme.column.deskTs
                text:                   modelData.ts || ""
                color:                  Theme.color.text.secondary
                font.family:            Theme.typography.data.sm.family
                font.pixelSize:         Theme.typography.data.sm.size
                font.weight:            Theme.typography.data.sm.weight
                font.features:          Theme.typography.data.sm.features
                elide:                  Text.ElideRight
            }

            // Kind
            Text {
                id: kindText
                anchors.left:           tsText.right
                anchors.leftMargin:     Theme.space[2]
                anchors.verticalCenter: parent.verticalCenter
                width:                  Theme.column.deskKind
                text:                   modelData.kind || ""
                color:                  root._toneColor(modelData.tone || "data")
                font.family:            Theme.typography.data.sm.family
                font.pixelSize:         Theme.typography.data.sm.size
                font.weight:            Theme.typography.data.sm.weight
                font.features:          Theme.typography.data.sm.features
                elide:                  Text.ElideRight
            }

            // Subject
            Text {
                id: subjectText
                anchors.left:           parent.left
                anchors.leftMargin:     Theme.column.deskSubject
                anchors.right:          parent.right
                anchors.rightMargin:    Theme.space[2]
                anchors.verticalCenter: parent.verticalCenter
                text:                   (modelData.subject || "") +
                                        (modelData.detail ? "  " + modelData.detail : "")
                color:                  Theme.color.text.primary
                font.family:            Theme.typography.body.sm.family
                font.pixelSize:         Theme.typography.body.sm.size
                font.weight:            Theme.typography.body.sm.weight
                elide:                  Text.ElideRight
            }
        }
    }
}

// BenchEvidenceModal.qml — Read-only Evidence snapshot for a Bench row.
//
// PR 4 of the post-DESIGN.md-v0.2 narrow UI sequence: this surface was
// the canonical centered modal that DESIGN.md v0.2 §5.11 and
// DESIGN_SYSTEM.md v0.2 §7.6 explicitly forbid for reference content.
// It now renders as a RightRailDossier — anchored to the right edge of
// the Bench, no scrim, no outside-click dismissal, originating row stays
// visible and selected.
//
// File name is preserved for narrow-PR discipline; the component is no
// longer literally a modal. A doctrine-clean rename (e.g.
// BenchEvidenceDossier.qml) can land in a separate cleanup PR.
//
// ADR 0049 (Decision 2): Bench v1 is a visual prototype. This surface is
// strictly read-only — it surfaces what the GUI read-model already exposes
// for a strategy, with no event-store reconstruction, no freshness
// computation, no mutation. The mandatory footer disclaimer must remain
// verbatim so the operator never mistakes this snapshot for an
// authoritative gate result.
//
// Public API (unchanged from the v0.1 modal — host call sites need no edit):
//   property bool open                — show/hide the dossier
//   property var  rowData             — as_qml() dict from _StrategyRow
//   signal closeRequested()           — emitted on CLOSE or Escape
//
// BenchSurface owns exactly one instance and toggles `open` in response
// to BenchRow's `evidenceRequested` signal. BenchRow MUST NOT instantiate
// this component per-row.

import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    property bool open: false
    property var  rowData: ({})

    signal closeRequested()

    // Prefer the normalized evidencePacket when present; fall back to flat
    // rowData fields so the dossier remains resilient if a future read-
    // model rebuild omits the packet. The packet is read-only and never
    // mutated here.
    readonly property var _packet:        (rowData && rowData.evidencePacket) || ({})
    readonly property var _pktMetrics:    _packet.metrics  || ({})
    readonly property var _pktEvidence:   _packet.evidence || ({})
    readonly property var _pktGate:       _packet.gate     || ({})
    readonly property var _pktStatus:     _packet.status   || ({})
    readonly property var _pktSession:    _packet.session  || ({})
    readonly property var _pktJob:        _packet.job      || ({})
    readonly property var _pktSource:     _packet.source   || ({})

    readonly property string _strategyName:
        root._packet.strategyName
         || root.rowData.name
         || root.rowData.displayName
         || root._packet.strategyId
         || root.rowData.strategyId
         || "(unnamed strategy)"

    // ------------------------------------------------------------------
    // Rail container — content composes inline below
    // ------------------------------------------------------------------

    RightRailDossier {
        id: rail
        anchors.fill: parent

        open: root.open
        headerLabel: "EVIDENCE SNAPSHOT"
        headerTitle: root._strategyName
        // MANDATORY verbatim ADR 0049 disclaimer — do not reword.
        footerNote: "Bench v1 evidence is read-only and sourced from the current GUI read-model snapshot. Real event-derived freshness and gate reconstruction are deferred."

        onCloseRequested: root.closeRequested()

        // ---- IDENTITY ----
        EvidenceSection { label: "IDENTITY" }
        EvidenceField {
            label: "Strategy ID"
            value: root._or(root._packet.strategyId || root.rowData.strategyId)
        }
        EvidenceField {
            label: "Stage"
            value: root._or(root._packet.currentStage || root.rowData.stage)
        }
        EvidenceField { label: "Family";   value: root._or(root.rowData.family) }
        EvidenceField { label: "Template"; value: root._or(root.rowData.template) }
        EvidenceField {
            label: "Description"
            value: root._or(root.rowData.description)
            multiLine: true
        }

        // ---- SOURCE ----
        EvidenceSection { label: "SOURCE" }
        EvidenceField { label: "Kind";          value: root._or(root._pktSource.kind) }
        EvidenceField { label: "Authoritative"; value: root._fmtBool(root._pktSource.authoritative) }
        EvidenceField {
            label: "Source note"
            value: root._or(root._pktSource.note)
            multiLine: true
        }

        // ---- GATE METRICS ----
        EvidenceSection { label: "GATE METRICS" }
        EvidenceField {
            label: "Sharpe"
            value: root._fmtSharpe(root._pktMetrics.sharpe !== undefined
                                   ? root._pktMetrics.sharpe
                                   : root.rowData.sharpe)
        }
        EvidenceField {
            label: "Max drawdown"
            value: root._fmtPct(root._pktMetrics.maxDrawdownPct !== undefined
                                ? root._pktMetrics.maxDrawdownPct
                                : root.rowData.maxDrawdownPct)
        }
        EvidenceField {
            label: "Trade count"
            value: root._fmtInt(root._pktMetrics.tradeCount !== undefined
                                ? root._pktMetrics.tradeCount
                                : root.rowData.tradeCount)
        }
        EvidenceField {
            label: "Evidence run"
            value: root._or(root._pktEvidence.runId || root.rowData.evidenceRunId)
        }
        EvidenceField {
            label: "Gate failures"
            value: root._fmtList(root._pktGate.failures || root.rowData.gateFailures)
            multiLine: true
        }
        EvidenceField {
            label: "Freshness"
            value: root._or(root._pktGate.freshness)
        }
        EvidenceField {
            label: "Gate result"
            value: root._or(root._pktGate.gateResult)
        }

        // ---- EVIDENCE TIMESTAMPS ----
        EvidenceSection { label: "EVIDENCE TIMESTAMPS" }
        EvidenceField {
            label: "Meta label"
            value: root._or(root._pktEvidence.label || root.rowData.metaEvidenceLabel)
        }
        EvidenceField {
            label: "Meta at"
            value: root._or(root._pktEvidence.observedAt || root.rowData.metaEvidenceAt)
        }
        EvidenceField {
            label: "Promoted at"
            value: root._or(root._pktEvidence.promotedAt || root.rowData.promotedAt)
        }
        EvidenceField {
            label: "Promotion type"
            labelWidth: 116
            value: root._or(root._pktEvidence.promotionType || root.rowData.promotionType)
        }

        // ---- STATUS ----
        EvidenceSection { label: "STATUS" }
        EvidenceField {
            label: "Status word"
            value: root._or(root._pktStatus.word || root.rowData.statusWord)
        }
        EvidenceField {
            label: "Status detail"
            value: root._or(root._pktStatus.tail || root.rowData.statusTail)
            multiLine: true
        }

        // ---- SESSION & JOB ----
        EvidenceSection { label: "SESSION & JOB" }
        EvidenceField {
            label: "Session state"
            value: root._or(root._pktSession.state || root.rowData.sessionState)
        }
        EvidenceField {
            label: "Session detail"
            labelWidth: 116
            value: root._or(root._pktSession.detail || root.rowData.sessionDetail)
            multiLine: true
        }
        EvidenceField {
            label: "Job status"
            value: root._or(root._pktJob.status || root.rowData.jobStatus)
        }
        EvidenceField {
            label: "Job action"
            value: root._or(root._pktJob.actionType || root.rowData.jobActionType)
        }
        EvidenceField {
            label: "Job detail"
            value: root._or(root._pktJob.detail || root.rowData.jobDetail)
            multiLine: true
        }

        // ---- AVAILABLE BENCH ACTIONS (informational only in v1) ----
        EvidenceSection { label: "AVAILABLE BENCH ACTIONS" }

        Text {
            width: parent ? parent.width : 0
            text:  "These are the menu items the Bench currently offers for this row. "
                 + "In v1 only Open Evidence has wired behavior; the rest are informational placeholders."
            color: Theme.color.text.muted
            font.family:    Theme.typography.deck.family
            font.pixelSize: Theme.typography.deck.size
            font.italic:    true
            wrapMode:       Text.WordWrap
        }

        Repeater {
            model: root.rowData.actions || []

            delegate: EvidenceField {
                required property var modelData
                label: modelData.verbClass || "—"
                value: modelData.label || "—"
            }
        }

        Item { width: 1; height: Theme.space[3] }
    }

    // ------------------------------------------------------------------
    // Formatting helpers (pure JS — no side effects)
    // ------------------------------------------------------------------

    function _or(v) {
        if (v === undefined || v === null) return "—"
        if (typeof v === "string" && v.length === 0) return "—"
        return v
    }
    function _fmtSharpe(v) {
        if (v === undefined || v === null) return "—"
        return ("+" + Number(v).toFixed(2)).replace("+-", "-")
    }
    function _fmtPct(v) {
        if (v === undefined || v === null) return "—"
        return Number(v).toFixed(1) + "%"
    }
    function _fmtInt(v) {
        if (v === undefined || v === null) return "—"
        if (v === 0) return "0"
        return "" + v
    }
    function _fmtList(v) {
        if (!v || !v.length) return "—"
        return v.join(", ")
    }
    function _fmtBool(v) {
        if (v === undefined || v === null) return "—"
        return v ? "true" : "false"
    }

    // ------------------------------------------------------------------
    // Inline sub-components — section header and field row
    // ------------------------------------------------------------------

    component EvidenceSection: Item {
        property string label: ""
        width: parent ? parent.width : 0
        implicitHeight: sectionLabel.implicitHeight + Theme.space[2]

        Text {
            id: sectionLabel
            anchors.bottom: parent.bottom
            text:  label
            color: Theme.color.brand.accent
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Font.DemiBold
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
    }

    component EvidenceField: Item {
        property string label: ""
        property string value: ""
        property bool   multiLine: false
        // `labelWidth` overrides the 96 px default for the few 14-char
        // labels ("Promotion type", "Session detail") that would otherwise
        // sit flush against the value column.
        property real   labelWidth: 96
        width: parent ? parent.width : 0
        implicitHeight: fieldRow.implicitHeight

        RowLayout {
            id: fieldRow
            width: parent.width
            spacing: Theme.space[3]

            Text {
                text:  label
                color: Theme.color.text.muted
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                Layout.preferredWidth: labelWidth
                Layout.alignment:      Qt.AlignTop
            }

            Text {
                text:  value
                color: Theme.color.text.secondary
                font.family:    Theme.typography.data.xs.family
                font.pixelSize: Theme.typography.data.xs.size
                font.features:  Theme.typography.data.xs.features
                Layout.fillWidth: true
                wrapMode: multiLine ? Text.WordWrap : Text.NoWrap
                elide:    multiLine ? Text.ElideNone : Text.ElideRight
            }
        }
    }
}

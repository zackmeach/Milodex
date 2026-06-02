// Formatters.qml — Pure value-formatter and tone→color singleton.
//
// Consolidates duplicated formatter functions that appeared in two or more
// QML files (P6 audit) and the tone→color resolver that carried an explicit
// "keep in sync" comment between RollupCell.qml and ActivityTable.qml (P14).
//
// GOVERNING RULE (PR10 audit): only formatters whose output is byte-identical
// at every repoint site are consolidated here. Sites that diverged in sentinel
// values or semantics are left in place; this file does not silently change
// any site's rendered output.
//
// Methods:
//   sharpe(v)              — "+N.NN" | "-N.NN" | "—" for null/undefined/non-finite
//   pct1(v)                — "N.N%" | "—"  (no sign, no ×100 scaling)
//   count(v)               — "" + v | "0" for v===0 | "—" for null/undefined
//   orDash(v)              — "—" for undefined/null/empty-string, else v
//   shortTime(iso,fmt)     — "HH:MM" (24h) | "H:MM AM/PM" (12h) | "—" for empty/null
//   money(value)           — sign + "$" + abs.toLocaleString(en_US, 'f', 2)
//   moneyParts(value)      — { sign, whole, cents }
//   toneColor(tone)        — QColor from Theme tokens (brand/positive/negative/warning/muted/data/default)
//   toneOf(value)          — "positive"|"negative"|"muted" from a numeric value
//
// NOT in scope (single-site or diverged semantics — see PR10 report):
//   - DeskSurface.fmtPct  (×100, toFixed(2), signed — different semantics)
//   - LedgerSurface.formatTs (returns "" for empty, not "—" — diverged sentinel)
//   - Main.qml.formatTimestamp (returns "" for empty — diverged sentinel)
//   - BenchSurface.formattedTrades (returns "—" for 0 — diverged from count's "0")
//   - AnchorSurface._formatMoney (CLI-contract, single-site)
//   - BenchEvidenceModal._fmtList/_fmtBool (single-site)
//   - Various single-site domain-specific color cascades (see report)

pragma Singleton

import QtQuick
import Milodex 1.0

QtObject {
    id: root

    // ------------------------------------------------------------------
    // P6: Value formatters
    // ------------------------------------------------------------------

    // sharpe(v) — "+N.NN" with +-→- replacement; "—" for null/undefined/non-finite.
    // Canonical source: BenchConfirmationModal._fmtSharpe + BenchSurface.formattedSharpe
    // (adds isFinite guard from BenchSurface — harmless for finite values at all sites).
    function sharpe(v) {
        if (v === undefined || v === null) return "—"
        var n = Number(v)
        if (!isFinite(n)) return "—"
        return ("+" + n.toFixed(2)).replace("+-", "-")
    }

    // pct1(v) — "N.N%" with one decimal place; no sign, no ×100 scaling; "—" for null/undefined.
    // Canonical source: BenchConfirmationModal._fmtPct / BenchEvidenceModal._fmtPct.
    function pct1(v) {
        if (v === undefined || v === null) return "—"
        return Number(v).toFixed(1) + "%"
    }

    // count(v) — stringified integer; "0" for zero; "—" for null/undefined.
    // Canonical source: BenchConfirmationModal._fmtInt / BenchEvidenceModal._fmtInt.
    // NOTE: BenchSurface.formattedTrades returns "—" for tradeCount===0 — that is
    // intentionally NOT repointed here due to the divergence.
    function count(v) {
        if (v === undefined || v === null) return "—"
        if (v === 0) return "0"
        return "" + v
    }

    // orDash(v) — pass-through; "—" for undefined/null/empty-string.
    // Canonical source: BenchConfirmationModal._or / BenchEvidenceModal._or.
    function orDash(v) {
        if (v === undefined || v === null) return "—"
        if (typeof v === "string" && v.length === 0) return "—"
        return v
    }

    // shortTime(iso, timeFormat) — parameterized HH:MM or H:MM AM/PM formatter.
    // Returns "—" for empty/null input (matches DeskSurface.shortTime sentinel).
    // Canonical source: DeskSurface.shortTime (body), Main.qml.formatTimestamp (param shape).
    // NOT repointed at LedgerSurface.formatTs — that function returns "" for empty input,
    // which diverges from this function's "—" sentinel.
    function shortTime(iso, timeFormat) {
        if (!iso) return "—"
        var d = new Date(iso)
        if (isNaN(d)) return iso
        var hh = d.getHours()
        var mm = d.getMinutes()
        if (timeFormat === "12h") {
            var ampm = hh >= 12 ? "PM" : "AM"
            var h12 = hh % 12; if (h12 === 0) h12 = 12
            return h12 + ":" + (mm < 10 ? "0" + mm : mm) + " " + ampm
        }
        // default 24h
        return (hh < 10 ? "0" + hh : hh) + ":" + (mm < 10 ? "0" + mm : mm)
    }

    // money(value) — sign + "$" + abs.toLocaleString(en_US, 'f', 2).
    // Canonical source: DeskSurface.fmtMoney.
    function money(value) {
        var n = Number(value || 0)
        var sign = n < 0 ? "-" : ""
        return sign + "$" + Math.abs(n).toLocaleString(Qt.locale("en_US"), "f", 2)
    }

    // moneyParts(value) — { sign, whole, cents } decomposition.
    // Canonical source: FrontSurface.moneyParts.
    function moneyParts(value) {
        var n = Number(value || 0)
        var abs = Math.abs(n)
        var whole = Math.floor(abs)
        var fraction = Math.round((abs - whole) * 100)
        if (fraction === 100) { whole += 1; fraction = 0 }
        return {
            sign: n >= 0 ? "+" : "-",
            whole: whole.toLocaleString(Qt.locale("en_US"), "f", 0),
            cents: "." + (fraction < 10 ? "0" + fraction : fraction)
        }
    }

    // ------------------------------------------------------------------
    // P14: Tone → color resolver (the one true keep-in-sync duplicate)
    //
    // Canonical source: RollupCell._valueColor (6-way) +
    //   ActivityTable._toneColor (5-way, same minus "brand").
    // The superset vocab is output-identical for both consumers because
    // ActivityTable never passes "brand".
    // ------------------------------------------------------------------

    // toneColor(tone) — maps a tone string to a Theme design-token color.
    // Vocab: brand | positive | negative | warning | muted | data | <default>
    function toneColor(tone) {
        if (tone === "brand")    return Theme.color.brand.primary
        if (tone === "positive") return Theme.status.positive
        if (tone === "negative") return Theme.status.negative
        if (tone === "warning")  return Theme.status.warning
        if (tone === "muted")    return Theme.color.text.muted
        // "data" and anything else → primary mono text
        return Theme.color.text.primary
    }

    // toneOf(value) — derives a tone string from a numeric value.
    // Returns "positive" | "negative" | "muted".
    // Canonical source: DeskSurface.toneOf.
    function toneOf(value) {
        if (value === null || value === undefined)
            return "muted"
        var n = Number(value)
        if (n > 0) return "positive"
        if (n < 0) return "negative"
        return "muted"
    }
}

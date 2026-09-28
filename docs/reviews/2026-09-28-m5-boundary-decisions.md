# M4/M5 boundary decisions — D-2, D-3, bounded D-8

**Date:** 2026-09-28. **Status:** founder adopted in the shareable-paper-v1 plan; independently reviewed. **Execution gate:** M4 remains open. Recording these choices does not open M5 or close the M4 founder walks, current-code F2 rehearsal, or boundary signature.

## D-2 — intraday config truth

Return all **32 unfrozen non-SPY intraday ETF configs** that declare `stage: paper` to `stage: backtest`. Retain the five already authorized, frozen SPY intraday canaries. Do not create promotion events or frozen manifests to make the 32 executable. The risk layer continues to refuse a paper strategy without a frozen manifest (`risk/evaluator.py`, `_check_manifest_drift`). The config edits belong to a separate, reviewed M5 PR and must not fabricate promotion history. A future load-time consistency check was proposed in the earlier D-2 brief but is outside this decision.

The [2026-06-22 D-2 brief](2026-06-22-D2-intraday-freeze-governance-brief.md) was framed before M1 closed; its header's M1 decision owner and its claim that all five SPY canaries require the exact 10:00 ET bar are stale. The benchmark uses that exact bar; the other canaries use entry windows. An operator must still start each selected runner early enough for its configured window.

## D-3 — operator-triggered starts

Use explicit GUI preview and start through the existing Bench proposal/submit path. Do not implement the failed one-shot schedule, auto-launch, or a self-renewing background supervisor for paper v1. A GUI start remains an operator action under [ADR 0012](../adr/0012-runtime-and-dual-stop.md); it does not promise unattended monitoring when the GUI is closed. M5 must prove supervised from-open and multi-session operation, missed-session disclosure, and restart/reconciliation. Manual start alone is not that proof.

## D-8 — bounded evidence reconstruction

Build a display read model only from existing durable event fields and current promotion policy. Show the source run, recorded time, authority, and known blockers. When current applicability cannot be established from those records, show **unknown**. A historical promotion row by itself is insufficient for current stage: `gui/query_helpers.py:_latest_promotions` excludes demotions and stage returns. A completed backtest and its age do not attest that code, config, methodology, or market data still match. Remove the action menu's hard-coded `Freshness.FRESH` claim without inventing a freshness age threshold, invalidation rule, or new promotion gate.

For paper v1, a recorded passing backtest may still expose **Promote to Paper as a proposal** when validity is unknown, with that uncertainty visible in the menu and confirmation. This preserves the current practical menu availability; it does not assert Fresh/Aging or authorize execution. Existing promotion checks revalidate on submission. Capital-stage locks stay in force. The [ADR 0050 paper-v1 addendum](../adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md) explicitly supersedes its Decision 5 menu rule for this bounded case; its full freshness state machine remains deferred.

## Independent dissent and disposition

An independent GPT-6 Sol review opened the relevant roadmap, D-2 and D-8 briefs, ADRs, GUI and risk paths, and strategy entry helpers, spending half its review challenging these choices. It supported the bounded scope but identified two failure modes: a manual start can miss the benchmark's exact entry bar, and a promotion query that excludes demotions cannot attest current stage. Both are explicit constraints above. The reviewer also found the older D-2 brief's all-five exact-bar statement false. No M5 execution or roadmap milestone-state update is authorized by this record; the M4 gate remains in force.

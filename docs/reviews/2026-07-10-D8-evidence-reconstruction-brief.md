# D-8 — Evidence reconstruction vs honest labeling — DECIDED 2026-07-10

**Status: DECIDED (founder, 2026-07-10) — Option A-amended.**
Protocol: §8 (framed → independent adversarial dissent → reconciled → founder decision → this record).

## Question

Before M2's operator-trust gate can close: does honest non-authoritative *labeling*
of GUI evidence surfaces satisfy closure, or is authoritative event-derived
reconstruction ([ADR 0050](../adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md)
v2 — the freshness state machine) required first?

## Options considered

- **A (as framed).** Labeling as shipped (#340) closes M2; ADR 0050 v2 stays its own
  deferred program.
- **A-amended (chosen).** A, plus honest labeling is extended to the **action-menu
  affordance** — the surface #340 did not touch (see the dissent finding below).
- **B.** Reconstruct trust-critical metric *values* from the event store now, label
  the rest. Rejected: the residual risk is on the *freshness* axis, not metric
  values — B costs more and does not close the actual gap.
- **C.** Full ADR 0050 v2 before M2 closes. Rejected: a multi-subsystem program
  (manifest-drift detection, age thresholds, methodology/data-source change tracking,
  divergence detection, evidence-transition event records) that would collapse M2
  into work the roadmap deliberately deferred.

## The dissent finding that amended A (verified, load-bearing)

The Evidence dossier honestly renders freshness as the sentinel
`"not_reconstructed_v1"` → "Deferred (v1) — not yet computed"
(`gui/bench_actions.py`). But the **action menu** derives its Promote affordance
from a *different* freshness representation hardcoded to `Freshness.FRESH`
(`gui/bench_actions.py` `_bench_evidence_by_stage`, wired via
`gui/snapshot_builders.py`), and `can_promote_to_next` treats FRESH+PASS as
promotable — so "Promote to Paper" is offered as if freshness were computed, on
evidence that may be arbitrarily old or config-drift-invalidated. Paper promotion is
GUI-submittable (only the capital stages are hard-locked per ADR 0004/0042). The
system simultaneously says "freshness is not computed" (dossier) and acts as if it
were Fresh (menu). The age datum already exists on the evidence packet
(`backtest_run_started_at`), so surfacing it is *labeling*, not v2 reconstruction.

**What stays true either way (verified):** the promotion gate re-derives its inputs
from the durable backtest run (`metrics_from_run(run_id)`; a proposal without a
`run_id` is refused), never from displayed numbers — so a wrong *displayed* metric
cannot corrupt the promotion decision. The precise residual is the affordance's
implied freshness claim, not gate-input integrity.

## Decision (A-amended)

1. **Honest labeling closes M2's operator-trust gate.** ADR 0050 v2 remains gated
   by its own future ADR; it is NOT pulled onto the M2 critical path.
2. **Labeling extends to the action surface** (the amendment): the Promote-to-Paper
   confirmation shows the evidence's age (from `backtest_run_started_at`) plus an
   explicit "freshness not computed (v1)" caveat. One small PR; display-only; no
   change to menu availability rules, `check_gate`, or any ADR 0050 enum.
3. **Tripwire, honestly worded:** any metric or affordance caught materially wrong
   is promoted to event-derived computation as same-milestone touch-it work (as
   #343 did for `count_paper_trades`). Stated plainly: the tripwire is not an
   independent control — the load-bearing control is periodic **adversarial audit**,
   and *unlabeled affordances* (menu verbs derived from placeholder evidence state)
   are explicitly in that audit's scope. Labels do not prevent wrong numbers; audits
   catch them.
4. **The menu-freshness gap is the standing v2 motivator.** Closing M2 on labeling
   does not retire the reconstruction obligation: the hardcoded-FRESH affordance is
   the concrete operator-visible defect that ADR 0050 v2 exists to fix. Revisit at
   the M4/M5 boundary review; cross-link from §10's "Authoritative evidence
   reconstruction" row.

## Why not more

- #343 (a 340× inflated `paper_trade_count` in evidence-package metadata) was cited
  by both sides. Adjudicated: it was a query-semantics bug in an event-store read,
  caught by adversarial audit — v2's freshness machinery would not have caught it,
  and no label did. It therefore supports "audit is the control," not "reconstruct
  everything."
- The roadmap's M2 outcome text always framed the bar as honest *labeling*; D-8
  existed to make that bar a deliberate founder decision rather than roadmap prose.

## Consequences

- One follow-up PR in M2: promote-affordance age + freshness caveat (display-only).
- Roadmap §2 D-8 entry → DECIDED; §8 paused-list updated; §10 reconstruction row
  gains the M4/M5-boundary revisit pointer.
- ADR 0050 unchanged (v2 remains "specified by a follow-up ADR").

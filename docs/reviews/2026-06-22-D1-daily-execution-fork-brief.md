# D-1 — Daily-execution fork (decision brief, framed at M0)

**Date:** 2026-06-22 · **Status:** framed (M0); **decided at M1** per
[`CURRENT_ROADMAP.md`](../CURRENT_ROADMAP.md) §8 decision-ownership map.
**Owner gate:** M1. **Reviewers:** primary (Opus) + independent (Opus) — see
§Independent review. **Founder decides.**

> This is a §8 decision brief. M0 *frames* it; no execution code is written for it
> in M0. **Any PR that touches `_check_market_open` without a preceding D-1 ADR is
> a doctrine violation** (roadmap §5 M1 risk seams).

## 1. The decision

A daily (`1D`) strategy structurally **cannot submit an order today.** Choose how
to resolve the contradiction:

- **Option A — Queue-at-open.** Lock the signal in at close; submit at the next
  open as a `TIF=day` order, gated behind a **mandatory morning re-validation**.
- **Option B — Relax `market_hours` for 1D.** Allow a daily post-close submit to
  pass the market-hours check.
- **Option C — Reclassify daily as decision-only.** Daily strategies *decide and
  explain* but never submit; remove every claim/affordance that says daily
  executes.

## 2. Current evidence (code-grounded at HEAD)

The contradiction is a **catch-22 across the runner and the risk layer**, and it
binds *all* 1D strategies including the lifecycle-proof regime strategy:

- **Runner no-ops while the market is open.**
  [`strategies/runner.py:272`](../../src/milodex/strategies/runner.py):
  `if is_daily_bar and market_open: return []` — a 1D runner makes no evaluation
  and no fetch during RTH (by design: the in-progress daily bar shares its
  timestamp with the post-close finalized bar).
- **Post-close, the runner evaluates the close bar once** (after the lockin
  stability window), producing a *decision* and possibly a BUY/SELL intent.
- **The risk layer then vetoes that intent.**
  [`risk/evaluator.py:428`](../../src/milodex/risk/evaluator.py) `_check_market_open`:
  `if not context.market_open:` → blocked, `reason_code="market_closed"`,
  message *"Market is closed; paper submit is blocked."* Post-close is exactly
  when a 1D strategy evaluates ⇒ every daily intent is vetoed.
- **No daily exemption and no decision-only/queue path exists** — this is
  structural, not a per-strategy bug.
- **Manifest is NOT the daily blocker.** All **6 daily strategies are frozen** at
  paper (authoritative `strategy_manifests`: `regime.daily.sma200_rotation`,
  `breakout.daily.atr_channel`, `breakout.daily.donchian_20_10`,
  `meanrev.daily.bbands_lowerband`, `meanrev.daily.pullback_rsi2`,
  `momentum.daily.tsmom`). They pass `no_frozen_manifest`; `market_closed` is
  their **sole** blocker. (Contrast D-2, where intraday non-SPY is manifest-blocked.)
- **The doc surface previously *implied daily executes*** — corrected in M0
  (`OPERATIONS.md` holding note), but the authoritative resolution is this fork.
- **Prior art:** the 2026-06-10 hardening roadmap proposed exactly Option A
  (DC-2 / HR-8 "queue-at-open") — **never built** (roadmap §3.3).
- **Constraint:** [ADR 0012](../adr/0012-runtime-and-dual-stop.md) forbids a
  daemon/scheduler in Phase 1, so any "submit at next open" needs either a manual
  pre-open step or the D-3 auto-launch decision. Manual pre-open deploy is the
  interim.

## 3. Options, trade-offs, risks

### Option A — Queue-at-open
- **Mechanism.** At close: persist the locked-in signal. Pre-open (or at next
  open): re-validate against fresh data (gap/halt/limit-state/staleness), then
  submit `TIF=day`. The morning re-validation is **mandatory** — a stale
  overnight signal must not auto-fire into a gapped open.
- **Pros.** Daily becomes a genuine fill path. Daily is where the *frozen,
  gate-relevant* strategies live; it does not depend on IEX-non-durable intraday.
  Honest: daily really does execute.
- **Cons / risk.** **Sacred-layer change** (risk policy + execution + runner +
  new persisted overnight-intent state). Requires an ADR, `risk-invariant-reviewer`,
  and either D-3 (auto-launch) or a disciplined manual pre-open step. Largest
  surface; most ways to get subtly wrong (overnight intent must not bypass the
  full evaluator at submit time).

### Option B — Relax `market_hours` for 1D
- **Mechanism.** Carve out the `market_closed` veto for 1D so a post-close submit
  passes; Alpaca queues it to the next open.
- **Pros.** Smallest code change.
- **Cons / risk.** **Weakens a sacred check for convenience** — the exact
  anti-pattern the risk doctrine forbids. A post-close submit skips the
  next-morning re-validation A provides, so an overnight signal fires blind into
  the open. Hardest to defend under `risk-invariant-reviewer`. **Disfavored.**

### Option C — Reclassify daily as decision-only
- **Mechanism.** Daily strategies evaluate, decide, and explain, but never
  submit. Remove/relabel every execution claim and affordance for 1D
  (OPERATIONS, the GUI archetype, any "deserving strategy will trade" language).
- **Pros.** Trust-sufficient by the closure definition (§4: *"daily is explicitly
  reclassified as decision-only rather than falsely appearing executable"*).
  Minimal sacred-layer churn (no `_check_market_open` change — it's a
  classification + surface-truth change). Honest about a real limitation.
- **Cons / risk.** Loads the **entire real-fill burden for M1 onto intraday**,
  which is IEX-non-durable (D-5), freeze-gated (D-2), and from-open-launch-
  dependent (D-3). If intraday-from-open fill proves fragile, M1 has no fallback
  fill path. Also concedes that the frozen daily strategies will never transact.

## 4. Recommendation (primary — to be tested by independent review)

**Conditional, not cheap:**

1. **Reject Option B.** Weakening `market_hours` for convenience violates the
   risk doctrine and removes the morning re-validation safety. Off the table
   unless the independent review surfaces a safety argument I've missed.

2. **Sequence C-then-A, decided against intraday reality at M1:**
   - Treat **Option C as the trust-floor**: it closes M1's "daily must not falsely
     appear executable" with minimal sacred-layer risk, and it is *honest*.
   - But **do not pick C merely because it is cheap** (roadmap keystone warning).
     If, at M1, intraday-from-open fill (D-2 freeze + D-3 launch) is **not**
     demonstrably reliable, the real-fill exit criterion has no margin — then
     **Option A** is the right investment so daily (the frozen, gate-relevant
     cohort) can actually fill.
   - Net: **recommend C as the M1 trust-closure path for daily, and stage A as a
     funded follow-on** if/when daily execution capability (not just trust) is
     wanted, or if intraday cannot carry the fill-proof alone.

3. Whichever is chosen, it lands behind an **ADR** and `risk-invariant-reviewer`;
   C still needs the ADR (it changes the promotion/archetype contract), even
   though it touches no risk-check code.

## 5. Independent review + reconciliation (Opus reviewer, 2026-06-22)

An independent Opus reviewer was given a neutral framing and asked to dissent. It
verified every code claim (all CONFIRMED at HEAD) and **DISSENTED on completeness
and framing** (not on the rejection of B). Material findings, each verified by the
primary before folding:

1. **Missed mechanism / missed option — `preview_only` (VERIFIED, load-bearing).**
   `preview()` already runs the *full* evaluator with `preview_only=True`, and
   `_check_market_open` exempts it
   ([`risk/evaluator.py:429`](../../src/milodex/risk/evaluator.py):
   *"Preview allowed outside market hours."*). Consequences:
   - **Option C is far cheaper than §3 claimed** — mechanically it is routing the
     daily runner through `preview()` instead of `submit_paper()`, not a deep
     classification rebuild. (It still needs an ADR for the *contract* change.)
   - **New Option D — decision-only durable queue.** Persist each daily post-close
     intent as an *explained, non-submitting* decision via the existing
     preview/explanation path. Strictly more than C (C discards the intent; D keeps
     an auditable "would-submit" trail) and strictly safer than A (nothing persists
     that can fire blind at the open). Built on code that already exists.

2. **C/D satisfy the *lifecycle* clause, NOT the *fill* clause (VERIFIED).** §4
   *Lifecycle correctness* blesses "daily reclassified as decision-only." But §4
   *Operational truth* + M1's exit demand a **named fill event** — to which C/D
   contribute **zero**, shoving 100% of M1's fill-proof onto intraday (IEX-
   non-durable D-5, freeze-gated D-2, from-open-dependent D-3). My §4 quote was
   selective; corrected here.

3. **"C-then-A" has no forcing gate for A (VERIFIED concern).** D-3 (the
   prerequisite for A) may itself defer to M5 (§8 map). If M1 closes on C/D, the
   milestone advances and **nothing ever requires A** — i.e. the 6 frozen,
   gate-relevant daily strategies (the only cohort that can produce
   promotion-grade statistical evidence) stay **permanently non-executing**. This
   must be put to the founder as a present choice, not a deferred "con."

4. **B rejection stands, with sharper teeth.** B doesn't just "skip morning
   re-validation" — removing the veto at submit means the kill-switch, data-
   staleness, disable-condition, and reconciliation checks never re-run against the
   gapped open; they evaluate stale post-close state. Reject B.

5. **A's headline risk was underplayed.** If A is built, the persisted overnight
   intent must re-enter the **full** `_evaluate(preview_only=False)` battery at the
   open (kill-switch, staleness, manifest-drift, reconciliation, sizing) — not a
   gap/halt subset — plus explicit halt / LULD / partial-fill handling. That is the
   whole risk, and it is the headline.

### Reconciled recommendation (supersedes §4)

- **Reject Option B.** Confirmed by both primary and reviewer.
- **Adopt Option D (decision-only durable queue) as the M1 trust-floor for daily**
  — it closes the lifecycle clause *honestly and auditably* at minimal sacred-layer
  risk, on the existing `preview()` path. (Plain C is the cheaper fallback if a
  durable intent trail is judged unnecessary.)
- **State the trade-off to the founder without softening:** D (or C) makes daily
  *non-executing by design*. M1's required fill then rests **entirely** on intraday;
  and because A has **no owning gate**, choosing D/C is in practice a decision to
  leave the frozen daily cohort permanently non-executing. **If the founder wants
  daily to ever fill, A must be explicitly funded now with its own gate** — it will
  not happen by default.
- **If Option A is chosen,** its acceptance criterion is the §5.5 safety headline:
  overnight intent re-runs the full evaluator at the open + halt/LULD/partial-fill
  handling, under an ADR + `risk-invariant-reviewer`.

**Decision still belongs to the founder at M1.** This brief is now review-complete.

# D-2 — Intraday freeze governance (decision brief, framed at M0)

**Date:** 2026-06-22 · **Status:** framed (M0); **decided at M1** per
[`CURRENT_ROADMAP.md`](../CURRENT_ROADMAP.md) §8. **Owner gate:** M1.
**Reviewers:** primary (Opus) + independent (Opus). **Founder decides.**

> Prerequisite for *any* intraday fill. Freezing/promoting is also the act
> governed by **D-4** (lifecycle-proof operational gate enforce-vs-document),
> decided in the same M1 window.

## 1. The decision

Whether — and which — of the **unfrozen, paper-staged non-SPY intraday ETF
replicas** to `promotion freeze`, so they can pass the manifest check and
transact. Equivalently: how to resolve the **config-stage vs authoritative-freeze
divergence** (a config-truth issue, not only a fill-enablement one).

## 2. Current evidence (code-grounded at HEAD)

- **The veto.** [`risk/evaluator.py:321`](../../src/milodex/risk/evaluator.py)
  `_check_manifest_drift`: for `effective_stage ∈ {paper, micro_live, live}` with
  `frozen_manifest_hash is None` → blocked, `reason_code="no_frozen_manifest"`,
  message *"… has no frozen manifest at stage 'paper'. Run 'milodex promotion
  freeze' …"*. (Backtest-stage is exempt — it returns "exempt from manifest
  drift".)
- **Authoritatively frozen intraday = 5, all SPY** (event store
  `strategy_manifests`): `benchmark.unconditional_intraday_long.spy.v1`,
  `breakout.orb.intraday.spy.v1`, `meanrev.rsi2.intraday.spy.v1`,
  `meanrev.vwap_reversion.intraday.spy.v1`, `momentum.vwap_trend.intraday.spy.v1`.
  These are the structurally-open intraday path (unexercised in the 2026-06-22
  live fire only because of the mid-session launch).
- **Config-stage vs authoritative divergence (key finding).** **32 intraday
  YAMLs declare `stage: paper` but have NO frozen manifest** — 16
  `bench_unconditional_intraday_long_<non-SPY>` + 16
  `meanrev_rsi2_intraday_<non-SPY>` (DIA, GLD, IWM, QQQ, TLT, and the 11 XL*
  sector ETFs). Launching any fires `no_frozen_manifest`. The roadmap's "16
  non-SPY replicas" is the *deployed* live-fire subset; the *config-level*
  exposure is **32**. **The YAML `stage:` field is not the authoritative
  promotion state** — and a config that says `paper` while unfrozen is both a
  footgun (silent veto on launch) and a truth defect.
- **Freeze mechanism.** [`promotion/manifest.py:34`](../../src/milodex/promotion/manifest.py)
  `freeze_manifest` snapshots the YAML (refuses `backtest` stage; appends to
  `strategy_manifests`; never skips on matching hash). So freezing requires the
  YAML to *already* sit at a promoted stage — which these 32 do (declaratively).
- **Evidence honesty (ties to D-5).** Every intraday verdict is structurally
  **non-durable on IEX** (ADR 0017). These replicas are **knowingly-losing nulls**
  (benchmark = always-long; rsi2 = negative-Sharpe canary). Freezing one to "make
  it executable" is a deliberate harness-validation act, **not** a claim of edge.
- **Daily is unaffected** — all 6 daily strategies are already frozen (see D-1);
  D-2 is purely the intraday side.

## 3. Options, trade-offs, risks

### Option A — Freeze the deployed non-SPY cohort (the 16 the live fire used)
- **Pros.** Directly unblocks the cohort the live fire exercised; gives intraday
  a broad multi-symbol fill surface for M1's fill-proof.
- **Cons/risk.** Promotes 16 knowingly-losing nulls to genuinely-executable; only
  meaningful if from-open launch (D-3) lands. Leaves the *other* 16 paper-staged
  configs still divergent (footgun persists).

### Option B — Freeze a deliberate minimal subset (e.g. 1–3 non-SPY symbols)
- **Pros.** Smallest authorized footprint that proves intraday fills on a non-SPY
  symbol; least governance surface; aligns with "prove the mechanic, not the
  fleet." Easiest to reason about under D-4.
- **Cons/risk.** Still leaves the broader YAML divergence unless paired with C.

### Option C — Demote the divergent YAMLs back to `stage: backtest`
- **Pros.** Removes the false `paper` claim and the launch footgun for the 32
  configs in one move; restores config-truth; costs nothing in the risk layer.
  Can be combined with A or B (freeze the few you want; demote the rest).
- **Cons/risk.** Concedes those symbols are not paper-deployed (which is the
  truth today). Pure config edit — but it touches `configs/*.yaml` stage fields,
  so it is a governance-visible change, not a silent fix.

### Option D — Freeze nothing; rely on the 5 SPY canaries as the only intraday path
- **Pros.** Zero new promotion. The SPY canaries are already the structurally-open
  path; exercise *them* from open (D-3) for M1's fill-proof.
- **Cons/risk.** SPY-only intraday fill; no multi-symbol breadth. The 32 divergent
  YAMLs remain a footgun unless C is also done.

## 4. Recommendation (primary — to be tested by independent review)

**Combine D + C, with B as the breadth option:**

1. **For M1's fill-proof, prefer Option D's spine:** exercise the **already-frozen
   5 SPY canaries from open** (pair with D-3). A single honest intraday fill on a
   frozen canary satisfies M1's "named fill event" without any new promotion —
   the lowest-risk path to execution truth.
2. **Do Option C regardless:** demote the 32 divergent `stage: paper`-but-unfrozen
   YAMLs back to `backtest`. This is config-truth hygiene that should not wait —
   it removes a live footgun and a false claim. (It is governance-visible; record
   it, don't slip it in.)
3. **Option B only if multi-symbol intraday breadth is wanted at M1:** freeze a
   small, named non-SPY subset deliberately, with the D-4 gate question answered
   first (these are lifecycle-exempt-class promotions of knowingly-losing nulls —
   the operational gate must be honest about that).
4. **Reject blanket Option A** for now: freezing all 16 promotes a fleet of nulls
   to executable for no trust-closure benefit beyond what D+B deliver, and
   enlarges the surface D-4 must govern.

Net: **D (exercise the 5 frozen SPY canaries) + C (demote the 32 divergent YAMLs)
is the trust-minimal path; add B only for deliberate breadth.** All freezes land
under D-4's answered gate.

## 5. Independent review + reconciliation (Opus reviewer, 2026-06-22)

An independent Opus reviewer was given a neutral framing and asked to dissent. It
**CONCURRED with dissent on emphasis** and code-grounded the spine. Material
findings, each verified by the primary:

1. **Spine confirmed.** The `no_frozen_manifest` veto (`evaluator.py:321`), the
   `freeze_manifest` mechanism (`manifest.py:34`, refuses backtest), the **32
   divergent count** (37 paper-stage intraday − 5 frozen = 16 bench + 16 rsi2
   non-SPY), and **11 frozen total** all CONFIRMED. *(Add the parenthetical: 37
   intraday YAMLs declare `stage: paper`; only 5 are frozen.)*
2. **Demote (C) is safe — VERIFIED.** Zero of the 32 divergent configs has a
   frozen *paper* manifest, so demoting them to `backtest` cannot orphan a frozen
   manifest. C is a clean, reversible config edit with no risk-layer surface.
3. **My "benchmark is always-long → fills from open" was WRONG (VERIFIED,
   load-bearing correction).** The benchmark fires BUY *exactly* at the 10:00-ET
   bar — `is_entry_signal_bar` is an exact-equality check
   ([`strategies/_session_intraday.py:113`](../../src/milodex/strategies/_session_intraday.py);
   [`bench_unconditional_intraday_long.py:115`](../../src/milodex/strategies/bench_unconditional_intraday_long.py)).
   All 5 SPY canaries are **one-shot, session-anchored** entries, not always-long.
   So **Option D yields a fill ONLY if a runner is alive and ticking before
   10:00 ET** — i.e. it depends entirely on **D-3** (from-open launch), which is
   unbuilt and ADR-0012-constrained. D is the lowest-*governance* path, not the
   lowest-*fill-risk* path; if D-3 slips, D produces **zero fills** and M1's
   named-fill exit is not met.
4. **Missed Option E (VERIFIED).** The loader does **not** couple `stage:` to
   frozen-manifest existence — a YAML can declare `stage: paper` with no manifest
   and load cleanly; the only feedback is a runtime veto. **E: make
   `stage: paper`-without-frozen-manifest a load-time error** (or a `promotion
   lint` cross-check). C cleans the current 32; E prevents recurrence — the durable
   config-truth fix.
5. **B is a real D-4 act, not a footnote.** Freezing a negative-Sharpe canary
   promotes it under the lifecycle gate that is `enforced=False` (`policy.py`), so
   the freeze records no statistical justification. **B is blocked on D-4, full
   stop.**

### Reconciled recommendation (supersedes §4)

- **C (demote the 32 divergent YAMLs) + E (load-time validation) is the genuine
  "do-regardless" config-truth fix.** Safe (verified zero collisions), reversible,
  no risk-layer surface. *Executed at M1 as part of D-2 (a governance-visible
  config change), recorded — **not** slipped into M0.*
- **D (exercise the 5 frozen SPY canaries) is gated HARD on D-3.** It is only a
  fill path if a from-open runner is alive before 10:00 ET. Do not present D as a
  near-certain fill; it is not.
- **Add B as fill-probability *insurance*, gated on D-4.** Because every SPY canary
  is a one-shot ~10:00 entry, M1's named-fill exit is fragile on D alone. Freezing a
  small, named non-SPY subset with **wider entry windows** (e.g. rsi2 dip-entries)
  materially raises the odds *something* fills on the target session — but only
  after D-4 answers the lifecycle-gate-honesty question.
- **Reject blanket A** (freeze all 16): enlarges the D-4 surface for no
  trust-closure benefit beyond D + B.

Net: **D (gated on D-3) + C + E**, with **B (gated on D-4)** as fill-probability
insurance if D-3 readiness is uncertain. **Decision belongs to the founder at M1.**
This brief is now review-complete.

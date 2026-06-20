# Intraday ETF Evidence — Phase 2 Tier-1 Gate Report

**Created:** 2026-06-20 (overnight, autonomous). **For:** operator review at the Tier-1 gate.
**Branch:** `intraday-etf-evidence-phase2` (worktree). **NOT merged to master.**

---

## TL;DR

Tier 0 + Tier 1 code + a perf fix are committed and reviewed; the **thermonuclear gate review is done**. The
architecture, risk layer, and the event-store change are **clean**. The review caught **two real statistical
bugs in the random-matched null** (boundary under-fire + stranded-exit) that the per-PR reviews missed. The
perf fix landed **33%** (not the predicted 70%) — the remainder is per-bar snapshot fsyncs (a "Fix C"). The
**working-lane run was deliberately NOT run overnight** — it needs Fix C to be fast (otherwise ~hours,
disk-contended) and the null should be fixed first so the evidence isn't computed on a flawed baseline.

**Your decisions are in §5.**

---

## 1. What landed overnight (committed, reviewed, green — suite 2975 passed)

- **Tier 0** (11 commits): cross-ETF fan-out (`single_symbol` guard + generator + 68 per-symbol configs),
  readiness coverage fix (distinct on-grid offsets), 2026 half-days, 5Min cache warmed for all 17 ETFs
  (2023-01-01→2026-06-18).
- **Tier 1**: E-PR3 (`baseline_ref` field, hash-exempt); E-PR2 (random matched-exposure null: strategy +
  per-symbol measured rates ~0.92–0.99 + 17 configs). Reviews: risk-invariant SAFE, ponytail clean.
- **Perf fix** (Fix A `EventStore.batched()` + Fix B session-date idiom): risk-invariant SAFE, suite green.

## 2. Perf fix: 33% verified, incomplete

Single SPY candidate walk-forward (full window): **24 min → 16.1 min (964s), ~33%.** The 3-month profile
predicted ~70%; the gap is **per-bar equity-snapshot writes** — Fix A batched *explanations* but snapshots
still fsync per bar (same pattern). A **"Fix C" batching snapshots the same way would likely recover the
rest** and, importantly, make the **parallel** working-lane run fast (15 concurrent backtests fsyncing
snapshots per-bar will otherwise re-contend the disk). Fix C is another event-store-seam change — your call,
not a 3am autonomous move.

## 3. Thermonuclear review — synthesis

**5 adversarial finders, code-grounded, at scale. No BLOCKERS.**

### Clean (verified, don't re-litigate)
- **Risk layer UNWEAKENED.** Only `execution/` touch is the `bufferable=True` flag on `record_no_action`
  (benign — it never reaches the broker/risk evaluator). risk/promotion/broker/kill-switch untouched.
- **Event-store batching safe at scale.** The parallel working-lane run does NOT re-introduce
  *explanation*-lock contention — the flush is a ~76 ms burst, measured 6 concurrent flushes = 0 errors,
  strictly better than the pre-fix per-bar commits. Live audit path byte-identical. (NB: this is about
  *explanations*; *snapshots* are the remaining per-bar fsync — see §2.)
- **config_hash / frozen-manifest integrity, registration (no collision, can't auto-trade/promote),
  DecisionReasoning golden test, SRS coverage generator** — all intact.
- **Fix B mask** provably bit-identical to `.date ==` (exhaustive DST check) — safe in the live trade path.
- **Coverage metric** bounded [0,100]%, immune to the pathological sessions constructed, 2026 half-days
  complete/correct — trustworthy to gate Tier-3 evidence verdicts on.
- **Null determinism** correct at scale (per-(symbol,session,seed) hashing; no global seed; one-entry guard
  survives the T+1 fill gap).

### Real bugs the per-PR reviews missed (statistical / integration)
| # | Sev | What | Effect | Fix |
|---|---|---|---|---|
| N1 | **MAJOR** | Null target draws `[30,330)` but the last firable 5-min bar is offset **325** | targets 326–329 (**1.55%**) never fire — on **every symbol**, not just thin ones — systematically under-counts the baseline vs the candidate | clamp the draw to the firable grid (max target 325): `rng.integers(OR, OR+EW - bar_minutes + 1)` |
| N2 | **MAJOR** (rare, thin-symbol) | Null exit is `is_time_stop_bar` = **exact 15:55 equality**; engine has no session-end force-flatten | if the 15:55 bar is absent (thin coverage) the position **strands overnight** → injects overnight exposure into an intraday null + drops a round-trip; asymmetric (candidate has stop/RSI exits) | exit on first present bar with offset **≥** time-stop (mirror the entry), OR engine force-flatten intraday positions at session end |
| N3 | MINOR | `baseline_ref` not rewritten by the fan-out generator | all 16 non-SPY random-matched configs claim `baseline_ref: meanrev.rsi2.intraday.spy.v1` — wrong candidate; **provenance only** (hash-exempt, consumed nowhere yet) | add baseline_ref to the per-symbol rewrite + regenerate |
| N4 | MINOR | `_max_intra_session_gap` gets the raw offsets set (off-grid included) | spurious 2nd gap-warning when an off-grid bar is the session min; **zero effect on coverage_pct** (uses on_grid) | pass `on_grid` to the gap scan |

The prompt's feared "thin-symbol breaks count-match" is the *least* of it (~2%, dominated by N1 which hits SPY
just as hard). N1+N2 are the real defects; both bias the null's count/exposure and should be fixed before the
null's per-symbol verdicts are trusted.

### Approved decision the review flagged as a "spec violation" — reconcile, don't panic
- **Hold-duration not matched.** A finder flagged that the brief says "match trade-count **+ hold-duration**"
  but the code matches count only (time-stop-only exit, held to close). **This was your explicit decision**
  (the "Baseline exit" question → "time-stop only, no stop"), made with the methodology reviewer's input
  (the candidate's 0.5% stop on random entries rigs the null; the RSI-revert exit can't be replicated). So
  it's **approved, not a silent inversion** — but: (a) the **brief should be updated** to drop the
  "hold-duration" clause, and (b) the finder's substantive point stands — *unmatched hold-duration
  re-injects an exposure confound* (the null holds longer than the candidate). For an IEX-exploratory
  milestone on a known-losing canary it's acceptable; if you want a tighter null, the alternative is to
  model the hold (draw from the candidate's empirical hold distribution). **Your re-confirmation wanted.**

### Observations (FYI, not defects)
- 32 generated configs inherit `stage: paper` (deployable, but nothing auto-launches them; caches unwarmed).
  Consider setting the fan-out configs to `stage: backtest` until warmed.
- `survivorship_corrected` shows `no` for the 16 inline-universe ETF baselines (display-only).
- Single labeled seed = a point estimate (no CI) — you chose this for Tier 1; G (Tier 3) adds the multi-seed
  band.
- The buffered-explanation sentinel returns `-1` (typed `int`); optional hardening = return `int | None`.

## 4. Why the working-lane run was deferred (not run overnight)

68 backtests (candidate + 3 trading baselines × 17). At 16 min each with snapshots still fsync-per-bar, a
parallel run would re-contend the disk → likely hours, uncertain. And running it now would compute the
milestone evidence on a **null with two known MAJOR bugs (N1, N2)**. So the right order is: **fix N1/N2 (+
decide N3/N4 and the hold-duration question) → Fix C (fast + parallel-clean) → then the working-lane run.**

## 5. Decisions waiting for you (the Tier-1 gate)

1. **Null fixes** — approve fixing N1 (boundary clamp) + N2 (≥-based exit or engine force-flatten — pick the
   scope) + N3/N4 (cheap MINORs). These should land before the working-lane evidence run. (N2's "engine
   force-flatten" option also fixes the same latent risk for the other time-stop baselines — your call on
   scope.)
2. **Hold-duration** — re-confirm "time-stop only / count-matched" (and I update the brief), or ask for a
   hold-matched null.
3. **Perf Fix C** (batch snapshot writes like explanations) — yes/no. Needed to make the working-lane run
   fast + parallel-clean.
4. **§6.1 — F/G/H build-now vs defer** — the gate decision, now with the working-lane *mechanism* proven
   (code) but the *evidence run* pending the above. Verdicts remain IEX-exploratory/non-durable regardless.
5. Then: **the working-lane run** (after 1+3), and the Tier-2 (F registry) / Tier-3 (G assembler) build per #4.

Branch is clean, all work committed, master untouched.

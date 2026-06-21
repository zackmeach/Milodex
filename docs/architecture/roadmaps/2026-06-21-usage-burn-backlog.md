# Usage-Burn Backlog — Repetitive / Long-Haul / Normally-Skipped Work

**Date:** 2026-06-21
**Origin:** 9-dimension fan-out audit (over-engineering, architecture-deepening, test-coverage, test-brittleness,
silent-failures, repetitive-boilerplate, todo-triage, doc-debt, deferred-work) → 46 raw candidates → deduped to 18.
**Lens:** tasks that are *repetitive* (mechanical sweeps) or *long-haul* (large effort), routinely deferred, and
**agent-friendly** — explicitly excluding work that weakens the sacred risk path or needs operator judgment (those live in
Appendix Z, gated). **Principle (Codex): burn model usage, not maintainability budget** — prefer work that proves the product or
pins a contract over code-motion for its own sake.
**Shape:** 6 executable tiers (A–F) + 1 operator-judgment appendix (Z). Every item is self-contained: a fresh session can pick
any single item and have everything it needs.
**Revised 2026-06-21** after a Codex second-opinion review (all 12 claims adopted — see "Codex second-opinion adjudication"
below): added product-facing **Tier F**, rescoped **E1/E2** as superseded-by-merged-work, parked the large internal refactors
(A2/A3/A5/B1/D3/D5), and reordered toward product validation + contract pins.

---

## HOW TO USE THIS FILE — read first

This file IS the state. There is no companion tool, script, or tracker. Update the file in place.

### Item lifecycle (every item starts at the verification gate)

```
            ┌─ REJECT ───► State: REJECTED   (work not needed; write why; stop; never delete the item)
UNVERIFIED ─┼─ RESCOPE ──► State: RESCOPED   (original scope obsolete; record the narrower residual, then treat that as the work)
            └─ CONFIRM ──► State: CONFIRMED-OPEN ─► (do the work) ─► State: DONE  (write what you did)
```

1. **Start at the Verification gate (§①).** Do NOT assume the finding is still real — re-run the gate's check. Code moves; an
   earlier session may have already fixed it; the claim may have been wrong. *The first job of every item is to decide whether
   the work even needs to be done.*
2. **Fill the Verdict (§②).** `CONFIRM` (real & worth doing as-scoped) / `REJECT` (not needed) / `RESCOPE` (the original task is
   obsolete but a narrower residual remains) + one-line rationale grounded in what you just checked.
3. **If REJECT** → set `State: REJECTED`, add a Session-log line, stop. The item stays in the file as a closed record.
4. **If RESCOPE** → set `State: RESCOPED`, write the narrower residual under §③, then either close it (if the residual is also
   empty) or do the residual work and log it. Use this when an item's premise has been overtaken (e.g. work merged elsewhere).
5. **If CONFIRM** → set `State: CONFIRMED-OPEN`, do the work, then set `State: DONE` and write the Outcome note (what changed,
   tests run + result, commit/PR).
6. **Session log is append-only.** Add a dated line; never overwrite history.
7. **Update the Index table** State column to match.
8. **Priority is separate from State.** The `Priority` column (`now` / `park` / `stretch`) is a sequencing hint, not a lifecycle
   state — a `park` item still gets verified when reached; it's just not in the recommended near-term order. Don't skip its gate.
9. **Date format:** absolute (`2026-06-21`), never "today".

### State legend (canonical tokens — keep machine-greppable)

| Token | Meaning |
|---|---|
| `UNVERIFIED` | Not yet triaged. The default. Run the gate next. |
| `CONFIRMED-OPEN` | Gate passed, work real & worth doing as-scoped, not yet done. |
| `DONE` | Work complete and verified. |
| `REJECTED` | Gate failed — work not needed (already done / not real / not worth it). |
| `RESCOPED` | Original scope obsolete (e.g. overtaken by merged work); a narrower residual is recorded under §③. |

### Recommended dispatch order (revised 2026-06-21 after Codex second-opinion review)

> Principle (Codex): **burn model usage, not maintainability budget.** Prefer work that proves the product or pins a contract
> over work that merely moves code around. The original "splits first" order was down-weighted accordingly.

1. **Verification sweep** — run every item's §① gate first. Cheap, parallelizable, and it stops a session from building
   already-merged or already-fixed work (it already caught E1/E2). This is itself a legitimate first burn.
2. **Low-risk pins & quick wins:** **D2** (doc-debt), **D4** (serializer contract tests), **A4** (`_decider_features` tests),
   **C3** (un-quarantine).
3. **A1** — but with **characterization/parity tests written first** (it's semantic, not mechanical — divergent `_atr` shapes).
4. **Tier F product validation** (**F2** lifecycle rehearsal, **F3** failure/recovery drills, **F1** clean-room install) —
   highest founder-intent value; proves Milodex is real/trustworthy/shareable. Usage-heavy and routinely skipped.
5. **D1** — with log dedup/rate-limiting (polling-storm guard).
6. **B3** — small, low-risk slot-dispatch collapse.
7. **C1 / C2** — **pilot one flow each** (trigger-and-observe, not just `findChild`) before any bulk migration.
8. **B2** — stretch only; large review surface, low user-visible value.

**Parked** (`Priority: park` — do only if smaller work reveals concrete pressure): **A2, A3, A5, B1, D3, D5.**
**Closed/superseded:** **E1, E2** (merged to master — see Tier E).

### Codex second-opinion adjudication (2026-06-21)

All 12 review claims verified and **adopted** (grounded against current `HEAD` = `ea12cc1`):
D4-before-D3 · A1-is-semantic · defer A2/A3 (validation-coupling + behavior-erasure risk) · defer B1 (review surface ≫ value) ·
pilot C1/C2 with trigger-and-observe · D1 logging must dedup (verified: `ActiveOpsState(PollingReadModel)` polls 30s) ·
split D2/D5 bundles · add `RESCOPED` state · fix the unmerged-vs-merged contradiction · E1/E2 already done on master
(`intraday_readiness.py` + `evidence_assembler.py` verified to cover the Workstream-D/E specs) · add product-facing Tier F
(grounded in FOUNDER_INTENT §276/§299 + 5 unverified LAUNCH_READINESS manual items). Sole nuance: **A2 is parked, not rejected**
— it retains the proven RM-006 daily-family precedent, so it's defensible if pressure arises.

### Risk rule (non-negotiable)

`risk_sensitivity: high` items are in Appendix Z and are **not autonomous burns** — surface to operator first. For Tier-B items
(`medium`, durable-state / promotion-adjacent) run the `risk-invariant-reviewer` agent on the diff before merge. No item in this
file may weaken, bypass, or relax the risk veto, the promotion gate, or the kill-switch semantics.

---

## Index

| ID | State | Pri | Title | Tier | Effort | Risk | Burn-fit |
|----|-------|-----|-------|------|--------|------|----------|
| A1 | `DONE` | now | Consolidate RSI/ATR/EMA onto `_indicators.py` (parity tests FIRST) → **PR #275** | A | small | low→med | high |
| A2 | `CONFIRMED-OPEN` | park | Extract shared session-intraday eval flow — gate verified (dup real), stays PARKED | A | large | med | high |
| A3 | `CONFIRMED-OPEN` | park | Consolidate 22× `_validated_parameters` — gate verified (dup real), stays PARKED | A | large | med | high |
| A4 | `DONE` | now | Unit-test `_decider_features.py` → **PR #275** | A | small | low | high |
| A5 | `CONFIRMED-OPEN` | park | strategies-test conftest factories — gate verified, stays PARKED | A | decent | low | high |
| B1 | `CONFIRMED-OPEN` | park | Split 82-method `EventStore` — gate verified (~2.8k-line class real), stays PARKED | B | large | med | high |
| B2 | `CONFIRMED-OPEN` | stretch | Decompose `bench.py` — gate verified, stays PARKED (stretch) | B | large | med | high |
| B3 | `REJECTED` | now | Collapse propose/submit Qt slots — ALREADY done (`_submit_sync`/`_submit_async` dispatch helpers exist) | B | small | low | high |
| C1 | `RESCOPED` | now | smoke source-pins → behavioral: PILOT delivered → **PR #279**; bulk = residual follow-up | C | large | low | high |
| C2 | `RESCOPED` | now | reachability source-pins → behavioral: PILOT delivered → **PR #279**; bulk = residual follow-up | C | decent | low | high |
| C3 | `DONE` | now | Un-quarantine showcase → **PR #278** (showcase un-skipped; 4 xfails KEPT — pollution unfixed, REJECT portion) | C | small | low | med |
| D1 | `DONE` | now | Deduped logging on 11 sqlite swallows → **PR #277** | D | small | low | high |
| D2 | `DONE` | now | Doc-debt sweep (D2a CLAUDE.md · D2b STRATEGY_BANK · D2c coverage) → **PR #282** | D | small | low | high |
| D3 | `RESCOPED` | park | De-dup reconciliation serializers — DEFERRED (layering violation / new-module cost; see PR #276) | D | small | low | high |
| D4 | `DONE` | now | Unit-test `cli/_shared` serializers → **PR #276** | D | small | low | high |
| D5 | `CONFIRMED-OPEN` | park | Finish GUI-hardening P9/P13 — gates verified (D5a own-Flickable; D5b bespoke fields), stays PARKED | D | small | low | high |
| E1 | `RESCOPED` | — | superseded by merged `intraday_readiness.py`; residual gap-check CLOSED (covers Workstream-D, no gap) | E | — | low | — |
| E2 | `RESCOPED` | — | superseded by merged `evidence_assembler.py`; residual gap-check CLOSED (covers Workstream-E, no gap) | E | — | low | — |
| F1 | `DONE` | now | Clean-room install audit → findings (**PR #282**); blocker fixed **PR #280**; 2 follow-up chips | F | decent | low | high |
| F2 | `DONE` | now | Paper lifecycle rehearsal → spine verified legible + operator checklist (**PR #282**) | F | large | med | high |
| F3 | `DONE` | now | Failure/recovery drills → TROUBLESHOOTING for 3 fault modes → **PR #281**; 2 code-fix chips | F | decent | med | high |
| Z1 | `REJECTED` | — | broker cancel/get-position silent-failure logging | Z | — | high | — |
| Z2 | `REJECTED` | — | Phase-2 live-mode stage-compat TODO | Z | — | high | — |
| Z3 | `REJECTED` | — | fractional-vs-whole-share sizing dispatcher | Z | — | med | — |
| Z4 | `REJECTED` | — | risk/promotion mutation-pin + test-efficacy rerun | Z | — | med | — |
| Z5 | `REJECTED` | — | Phase-2 evidence merge + experiment-registry governance | Z | — | — | — |

> **Pri** = sequencing hint, not lifecycle: `now` (recommended near-term) · `park` (defer unless smaller work reveals pressure) ·
> `stretch` (only if appetite remains). Parked/stretch items still get their gate run when reached.
> `REJECTED` for Z1–Z5 means **rejected as an autonomous burn**, not "never do" — they need operator judgment; re-open by hand.
> E1/E2 are `RESCOPED` (merged work overtook them) — see Tier E for the residual check.

---

## Execution outcomes — 2026-06-21 PM orchestration (authoritative disposition record)

A single PM-orchestrator session worked the whole file: ran the §① verification sweep on every item,
then executed/dispatched the `now`-tier work as a lineup of focused PRs (one isolated `git worktree`,
sub-agent per substantive item, gates verified before each commit). Every item's §① gate was run; this
consolidated table is the authoritative outcome record (it supersedes any per-item §②/③ placeholder
left un-rewritten below). **9 PRs opened (#275–#282), 4 follow-up chips filed, 0 changes to the
risk/promotion/broker money paths.**

| Item | Disposition | Evidence / PR |
|---|---|---|
| **A1** | DONE | Indicator consolidation onto `_indicators.py`, parity-pinned (atr/wilder_rsi/ema_value); ~85 net lines removed; 543 strategy tests green. **PR #275** |
| **A4** | DONE | `test_decider_features.py` — hand-computed coverage of the 5 pure decider functions + edge branches. **PR #275** |
| **D4** | DONE | `test_shared.py` — pins the cli/_shared JSON contract (15 tests, full key sets). **PR #276** |
| **D3** | RESCOPED→deferred | Only `format_money`+`account_to_dict` are truly identical; `operations/`→`cli/` import is a layering violation and a new module isn't worth a 1-liner + 5-key dict. Order serializers genuinely diverge. **PR #276** body. |
| **D1** | DONE | Rate-limited logging on 11 silent `sqlite3.Error` GUI read swallows (health→fault transition, no poll-storm). **PR #277** |
| **C3** | DONE (partial) | Showcase subprocess test un-quarantined (10/10 full-suite xdist green; root = bundled fonts). The 4 `xfail` inline-instantiation tests **KEPT** — proven (via `--runxfail`) to still fail under xdist (type-cache pollution unfixed). **PR #278** |
| **C1+C2** | RESCOPED (pilot) | One behavioral trigger-and-observe test each (modal-clamp geometry; kill-switch-reset reachability), both proven non-vacuous by mutation. ~35 source-pins intentionally left for a reviewed follow-up batch. **PR #279** |
| **F1** | DONE | Clean-room install audit: PASS-WITH-FRICTION. Fail-closed missing-state behavior confirmed. Found + **fixed** a real blocker (capture scripts passed a removed `register_qml_types` kwarg) → **PR #280**. Findings doc → **PR #282**. 2 chips (auth-label, dep-pins). |
| **F2** | DONE | Paper lifecycle rehearsal: backtest→evidence→promotion-proposal spine verified legible on current code; honest-uncertainty + promotion gating intact. Operator rehearsal checklist replaces stale §0. Findings doc → **PR #282**. |
| **F3** | DONE | Drilled 3 undocumented fault modes (corrupt/locked DB, broker 401, stale data) → TROUBLESHOOTING entries **PR #281**. 2 code-fix chips (sqlite CLI handling; broker read-path 401 translation — sacred-layer, operator-review). |
| **D2a** | DONE | CLAUDE.md: engine refs 922/978/1137→938/994/1153, cache.py:68→92, "5Min SPY-only"→17 ETF symbols, showcase gotcha updated. **PR #282** |
| **D2b** | DONE | STRATEGY_BANK §23: rewrote the stale "at most 3 runners" co-run limit — the launch guard was removed 2026-06-15 (`211d983`); co-run now allowed. **PR #282** |
| **D2c** | DONE | REQUIREMENTS_COVERAGE.md regenerated (`0a6734e`→`ea12cc1`; 14.5%→15.2%, no cross-citation corruption). **PR #282** |
| **B3** | REJECTED | Already done — `BenchCommandBridge` routes every propose/submit slot through `_submit_sync`/`_submit_async`/`_refresh_after_submit`. The collapse it proposed exists. |
| **A2, A3, A5, B1** | CONFIRMED-OPEN, parked | Gates verified — the duplication/god-class is real — but each stays parked per the file's own dispatch guidance (review surface ≫ value / safety-net erosion). Not autonomous burns. |
| **B2** | CONFIRMED-OPEN, parked (stretch) | Gate verified (`bench.py` ~2.7k lines). Stretch only. |
| **D5a, D5b** | CONFIRMED-OPEN, parked | Gates verified (BenchSurface rolls own Flickable; BenchEvidenceModal has bespoke fields). Low-yield polish; parked. |
| **E1, E2** | RESCOPED, residual CLOSED | Re-read `intraday_readiness.py` / `evidence_assembler.py`; they cover the Workstream-D/E specs. No genuine gap → residual closed; do not rebuild. |
| **Z1–Z5** | REJECTED (autonomous) | Unchanged — operator-judgment / sacred-path. Re-open by hand. |

**Follow-up chips filed this session:** broker-401 read-path translation (sacred broker layer, risk-review),
sqlite corrupt/locked CLI error message, pandas/pyarrow/numpy upper bounds, + the C1/C2 bulk-conversion batch.

---

# Tier A — Strategy-module sweeps (the 12.5k module, biggest mechanical surface)

### A1 — Consolidate 4-way RSI / 2-way ATR / EMA onto `_indicators.py`

- **State:** `CONFIRMED-OPEN`
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** A · small · **low→med** · high
- **Dimensions:** over-engineering, repetitive-boilerplate
- **⚠ This is a SEMANTIC change, not a mechanical sweep** (Codex). The copies are *not* byte-identical: `_atr` exists in two
  divergent shapes (`breakout_atr_channel:230` frame-shaped vs `breakout_donchian:260` series-shaped), and RSI exists scalar
  (`meanrev_rsi2_pullback`) vs series (`_indicators`, `meanrev_rsi2_intraday`). Consolidating *chooses a canonical shape and
  asserts numeric equivalence* — get that wrong and every dependent strategy's signal silently shifts. Treat it accordingly.
- **Locations:**
  - `src/milodex/strategies/_indicators.py:30` (canonical `wilder_rsi_series`), `:20` (`ema_series`)
  - `src/milodex/strategies/meanrev_rsi2_intraday.py:314` (`_wilder_rsi_series`, byte-identical)
  - `src/milodex/strategies/meanrev_rsi2_pullback.py:357` (`_wilder_rsi`, scalar)
  - `src/milodex/strategies/_decider_features.py:44` (`wilder_rsi`)
  - `src/milodex/strategies/breakout_atr_channel.py:226` (`_ema`), `:230` (`_atr`, frame-shaped)
  - `src/milodex/strategies/breakout_donchian.py:260` (`_atr`, series-shaped — divergent signature)
- **Task:** (1) **First** write characterization tests that pin the *current* numeric output of each local `_atr`/`_wilder_rsi`/
  `_ema`/`wilder_rsi` against fixed input series. (2) Add `atr()` (decide the canonical shape and adapt the divergent callers to
  it) and route EMA through `ema_series` in `_indicators.py`. (3) Repoint each strategy; delete the locals; the characterization
  tests must still pass bit-for-bit. A shared module already exists and the crypto strategies already import from it.
- **Payoff:** Removes ~120 lines of duplicated numeric code; kills 4-way drift risk (a fix to one RSI/ATR is currently *not* a
  fix to the others); pays off debt the code itself flagged at `_indicators.py:12`.
- **Dispatch:** One agent — **characterization tests before any deletion**, then add `atr`/`ema` to `_indicators.py`, repoint,
  delete locals; gate on the characterization parity tests + existing per-strategy tests + `ruff`. Run `risk-invariant-reviewer`
  is not required (below the risk layer) but a numeric-parity diff must be shown in the PR.

**① Verification gate:**
> Run `grep -rn "def .*wilder_rsi\|def _atr\|def _ema" src/milodex/strategies/`. **CONFIRM** if ≥2 RSI defs + ≥2 divergent
> `_atr` + a local `_ema` still exist outside `_indicators.py`. **REJECT** if already consolidated.

**② Verdict:** **CONFIRM** — verified 2026-06-21: 3 RSI copies (`_wilder_rsi_series`, `_wilder_rsi`, `wilder_rsi`) +
2 divergent `_atr` (frame vs series) + local `_ema` in `breakout_atr_channel`, all live alongside the canonical `_indicators`.

**③ Outcome:** _(OPEN — work not started)_

**Session log:**
- 2026-06-21 — audit + gate verified by grep; pre-confirmed as worked example. Work not yet started.

---

### A2 — Extract shared session-intraday evaluation flow (8 strategies)

- **State:** `UNVERIFIED`
- **Priority:** park
- **Tier / Effort / Risk / Burn-fit:** A · large · med · high
- **Dimensions:** architecture-deepening
- **Why parked (Codex):** a shared evaluation framework can quietly erase per-strategy exit nuance — high-cost, internal-only
  payoff, and the strategies are at paper stage. Do only if a future strategy reveals concrete pressure for the shared flow.
  *Nuance:* the daily family already did exactly this (RM-006 = `daily_cross_sectional`) without behavior loss, so the approach
  is proven — A2 is parked, not rejected. If revived, migrate one strategy at a time, each pinned to its existing test.
- **Locations:**
  - `src/milodex/strategies/gap_continuation_intraday.py:136`
  - `src/milodex/strategies/breakout_orb_intraday.py:80`
  - `src/milodex/strategies/meanrev_rsi2_intraday.py:133`
  - `src/milodex/strategies/daily_cross_sectional.py` (the daily-family precedent, RM-006)
- **Task:** 8 single-symbol session-intraday strategies repeat the identical skeleton: `open_qty>0` → exit-precedence ladder
  (`stop_loss` → `invalidation` → `time-stop`) → flat → entry-window gate → one-entry-per-session → sizing+affordability →
  BUY intent, plus copy-pasted `_exit_decision`/`_no_signal`. Create `strategies/session_intraday_evaluation.py` parameterized
  by per-strategy signal/exit predicates; migrate the 8.
- **Payoff:** Removes ~8× duplication; a new intraday strategy becomes signal-predicate-only; exit-precedence invariant
  enforced in one place instead of re-typed correctly 8 times.
- **Dispatch:** Build the shared module first, then one sub-agent per strategy to migrate + pin against its existing test.
  **strategies/ is below the risk layer, but preserve exit-ladder order exactly.**

**① Verification gate:**
> Open 3–4 of the listed intraday strategies. **CONFIRM** if the exit-ladder / entry-window / one-entry / sizing skeleton is
> duplicated across them AND no `session_intraday_evaluation`-style shared flow exists (note: `_session_intraday.py` holds
> *session timing* helpers, not the eval skeleton). **REJECT** if a shared eval flow already exists.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### A3 — Consolidate 22× `_validated_parameters` boilerplate onto a shared coercer

- **State:** `UNVERIFIED`
- **Priority:** park
- **Tier / Effort / Risk / Burn-fit:** A · large · med · high
- **Dimensions:** repetitive-boilerplate
- **Why parked (Codex):** the redundancy argument assumes **every** strategy-construction path flows through
  `loader.py:load_strategy_config` — but the in-strategy `_validated_parameters` is a *second, independent* guard that also fires
  on direct construction (tests, future code paths). Deleting it couples all parameter-correctness to the loader path remaining
  the sole entry point, which "may not remain true." Big mechanical surface, internal-only payoff, real safety-net erosion.
  Revive only with concrete pressure, and only after confirming no construction path bypasses the loader.
- **Locations:**
  - `src/milodex/strategies/meanrev_rsi2_pullback.py:136`
  - `src/milodex/strategies/breakout_donchian.py:165`
  - `src/milodex/strategies/loader.py:377` (spec-bound enforcement that already exists at config-load)
- **Task:** 22 strategy files define a local `_validated_parameters` with a byte-identical inner `required(name)` closure, many
  re-checking spec bounds (`>=2`, `>0`, `in (0,1]`) that `loader.py:377-425` already enforces from `parameter_specs`. Extract a
  shared coercer (`strategies/_params.py`) reading `parameter_specs`; drop the redundant re-checks; route genuine
  cross-parameter checks through `parameter_relations`.
- **Payoff:** Removes ~3k duplicated lines; the spec layer becomes the single source of truth for bounds; a new strategy
  becomes `parameter_specs` + `evaluate()`.
- **Dispatch:** Build `_params.py` + parity-test harness first, then one agent per strategy to migrate and **pin the exact
  `ValueError` messages** (medium-risk: error behavior must be preserved). Gate on suite + `ruff`.

**① Verification gate:**
> Run `grep -rln "_validated_parameters" src/milodex/strategies/` and open two. **CONFIRM** if ~20+ files carry the near-identical
> closure AND `loader.py` already enforces the same bounds. **REJECT** if already centralized into a shared helper.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### A4 — Unit-test the cross-sectional feature kit (`_decider_features.py`)

- **State:** `UNVERIFIED`
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** A · small · low · high
- **Note:** doubles as the characterization safety-net for A1's `wilder_rsi` consolidation — do it before or alongside A1.
- **Dimensions:** test-coverage
- **Locations:**
  - `src/milodex/strategies/_decider_features.py`
  - `tests/milodex/strategies/` (no `test_decider_features.py` today)
- **Task:** Dedicated suite for the 5 pure functions (`trailing_return`, `wilder_rsi`, `ma_distance`, `realized_vol`,
  `cross_sectional_zscore`): pin numeric math against hand-computed values; exercise every documented edge branch
  (None-on-insufficient-history, `reference<=0`/`sma<=0` divide-refusal, `avg_loss==0` → 50/100, `realized_vol` ddof=0,
  zscore empty/single/zero-std degeneracy). Verify `wilder_rsi` matches the meanrev RSI it replicates (ties into A1).
- **Payoff:** Locks the axis-3 decision-layer feature math (the project's stated thesis) against silent regression; a sign-flip
  or wrong-ddof currently survives. Pure functions = fast, deterministic, zero-fixture tests.
- **Dispatch:** One agent writes `tests/milodex/strategies/test_decider_features.py` with hand-computed expectations; gate on
  the new file passing.

**① Verification gate:**
> `ls tests/milodex/strategies/ | grep decider`. **CONFIRM** if no dedicated test file exists and the 5 functions are only
> exercised indirectly via decision-output assertions. **REJECT** if a dedicated suite already exists.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### A5 — Add a strategies-test conftest with shared barset/context factories

- **State:** `UNVERIFIED`
- **Priority:** park
- **Tier / Effort / Risk / Burn-fit:** A · decent · low · high
- **Dimensions:** repetitive-boilerplate
- **Why parked (Codex):** pure test-boilerplate churn touching 30 files for no behavior or coverage gain. Low-risk but low-yield;
  do only if already deep in the strategies test suite for another reason.
- **Locations:**
  - `tests/milodex/strategies/test_breakout_atr_channel.py`
  - `tests/milodex/strategies/test_momentum_daily_tsmom.py`
  - `tests/milodex/strategies/` (no `conftest.py` today)
- **Task:** 15 test files define a near-identical local `_barset`/`_make_barset` (`date_range freq=D tz=UTC` + ohlc/volume[1M]/
  vwap); `StrategyContext` is constructed inline 63× across 30 files. Create `conftest.py` with `barset(...)` and
  `strategy_context(...)` factories; replace per-file helpers + inline constructions.
- **Payoff:** New-strategy tests drop fixture boilerplate; a `BarSet`/`StrategyContext` shape change updates one factory
  instead of 15–30 files. Touches no production code.
- **Dispatch:** One agent adds the conftest, then migrates files in batches; gate on the strategies suite green after each batch.

**① Verification gate:**
> `ls tests/milodex/strategies/conftest.py` and `grep -rln "_barset\|_make_barset" tests/milodex/strategies/`. **CONFIRM** if no
> conftest exists and the helper is duplicated across many files. **REJECT** if a shared conftest already provides the factories.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

# Tier B — God-file splits (long-haul, facade-preserving)

### B1 — Split the 82-method `EventStore` god-class behind a stable facade

- **State:** `UNVERIFIED`
- **Priority:** park
- **Tier / Effort / Risk / Burn-fit:** B · large · med · high
- **Dimensions:** architecture-deepening
- **Why parked (Codex):** moving 82 durable-state methods is an enormous review surface for **zero user-visible value**, and
  nothing is actively blocked by the monolith. "Proving we can move code around is not the headline." Revive only if `EventStore`
  is concretely impeding development.
- **Locations:**
  - `src/milodex/core/event_store.py:423` (class start)
  - `tests/milodex/core/test_event_store.py` (the golden test that must stay byte-stable)
- **Task:** `EventStore` is one ~2782-line class, 82 methods across 14+ aggregates (explanations, trades, execution attempts,
  kill-switch, strategy runs, reconciliation, orchestration, backtest runs, promotions, experiments, manifests, snapshots) +
  15 standalone `_*_from_row` deserializers. SQL migrations are already externalized. Split the Python query/append methods +
  row-mappers into per-aggregate modules; keep `class EventStore` a thin composed facade so all ~32 callers and the golden test
  don't move.
- **Payoff:** Source-of-truth event store becomes navigable; per-aggregate modules independently testable; diff is mechanical
  relocation, not redesign.
- **Risk note (med):** `core/` is durable-state infra — **kill-switch persistence lives here.** A facade-preserving move cannot
  change semantics; run `risk-invariant-reviewer` on the diff. Do not alter any method body, only relocate.
- **Dispatch:** Write the facade scaffold + import map, then one agent per aggregate group to relocate methods + their
  `_from_row` mappers; gate on the golden test staying green.

**① Verification gate:**
> `wc -l src/milodex/core/event_store.py` + count `def ` in the class. **CONFIRM** if still one ~2.7k-line class with ~80
> methods. **REJECT** if already decomposed.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### B2 — Decompose 2766-line `bench.py` into per-action-family modules

- **State:** `UNVERIFIED`
- **Priority:** stretch
- **Tier / Effort / Risk / Burn-fit:** B · large · med · high
- **Dimensions:** over-engineering, architecture-deepening (corroborated)
- **Why stretch (Codex):** more defensible than B1 (it restores the ADR-0051 thin-facade contract on a `submit_*` path that grows
  organically), but still large code-motion with limited user-visible payoff. Do as a stretch project only after the `now`-tier
  work lands.
- **Locations:**
  - `src/milodex/commands/bench.py:283` (embedded `_DefaultWorkflowReadiness` verdict engine), `:515`
  - `tests/milodex/commands/test_bench_facade.py` (3420-line test that must not move)
- **Task:** `bench.py` (2766 lines) hosts six action families (each a `propose_*`/`submit_*` pair) + an embedded readiness
  verdict engine + intent-preview logic. ADR-0051 says `commands/` are thin orchestrators. Extract one module per family
  (`commands/bench/_backtest.py`, `_promotion.py`, `_runner.py`, …) with the facade staying as the dispatch shell holding
  shared helpers; keep `CommandProposal`/`CommandResult` shapes and `@Slot`-visible signatures byte-stable so the Qt bridge
  and the test file don't move.
- **Payoff:** Restores the ADR-0051 thin-facade contract; six focused modules; the readiness verdict engine gets a clear home.
- **Risk note (med):** `submit_*` paths trigger **real promotions / runner launches.** The move must be choreography-preserving
  and verified against the parity/readiness suite + QML smoke. Run `risk-invariant-reviewer`.
- **Dispatch:** Decide cut lines (`_DefaultWorkflowReadiness` vs orchestration helpers) first, then one agent per family.

**① Verification gate:**
> `wc -l src/milodex/commands/bench.py`. **CONFIRM** if ~2.7k lines hosting all action families + the embedded readiness engine.
> **REJECT** if already decomposed into per-family modules.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### B3 — Collapse 6 repeated propose/submit Qt slot pairs (RM-008)

- **State:** `UNVERIFIED`
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** B · small · low · high
- **Dimensions:** deferred-work, architecture-deepening (corroborated)
- **Locations:**
  - `src/milodex/gui/bench_command_bridge.py:485`
  - `docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md:575` (RM-008, "proposed" since 2026-05-21, all deps done)
- **Task:** `BenchCommandBridge` exposes six action families as near-identical `@Slot` `propose_`/`submit_` pairs (+`Async`
  variants); each propose does the same propose→cache→payload dance, each submit the same lookup-cached→dispatch→refresh dance.
  Shared helpers (`_submit_sync`, `_submit_async`, `_unknown_proposal_payload`, `_refresh_after_submit`) already exist. Extract
  a small internal dispatch helper, keeping `@Slot` names and `QVariantMap` payload shapes byte-stable.
- **Payoff:** Removes 6-way wiring duplication; a seventh action family becomes a one-liner registration. Pure Qt adapter
  (ADR-0051, no business rules).
- **Dispatch:** One agent extracts the dispatch helper; gate on the QML smoke test (pins slot-name literals) + bench-bridge
  tests.

**① Verification gate:**
> `grep -n "Slot.*propose_\|Slot.*submit_" src/milodex/gui/bench_command_bridge.py`. **CONFIRM** if ~6 near-identical pairs
> remain. **REJECT** if a dispatch helper already collapsed them.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

# Tier C — GUI test de-brittling (tedious, safe, *raises* real coverage)

### C1 — Convert ~28 source-substring pins in `test_qml_load_smoke.py` to behavioral

- **State:** `UNVERIFIED`
- **Priority:** now (**pilot one flow first**)
- **Tier / Effort / Risk / Burn-fit:** C · large · low · high
- **Dimensions:** test-brittleness
- **Pilot-first + trigger-and-observe (Codex):** do NOT bulk-migrate. Convert **one** flow first and prove the harness pattern,
  because `findChild(objectName)` only proves an item *exists* — not that the flow *works*. A real behavioral test must
  **trigger the action** (emit the signal / click via the recorded bridge) and **observe the result** (modal opens / bridge slot
  fires / state changes). If a pin can only be expressed as "the literal is present in source," it is not yet a behavioral test —
  flag it rather than dressing up a grep. Only after the pilot proves the trigger-and-observe pattern, batch the rest.
- **Locations:**
  - `tests/milodex/gui/test_qml_load_smoke.py:367` (and the `:1711` live-tree harness already in-file)
  - `tests/milodex/gui/test_bench_confirmation_modal_behavior.py:1` (the sanctioned behavioral harness, 9/9 passing)
- **Task:** The file has 45 test fns but only ~6 do a real subprocess QML load; the other ~28 read `.qml`/`.md` source as text
  (~50 `read_text` calls) and grep for substrings — breaking on any rename/reword without catching real regressions (a QML
  rename to `undefined` still "loads clean"). Migrate the source-pin tests to the behavioral/live-tree harness; delete pins
  already covered there.
- **Payoff:** Tests start failing on real behavior breakage and stop failing on cosmetic edits; cuts the suite's worst
  grep-on-source mass. Directly serves the CLAUDE.md gotcha about this file silently failing on pinned-literal edits.
- **Dispatch:** Agents in batches of ~6 functions, each rewriting a source-pin into a `QQuickView`/recorded-bridge behavioral
  check; gate on full gui suite green.

**① Verification gate:**
> `grep -c "read_text" tests/milodex/gui/test_qml_load_smoke.py`. **CONFIRM** if ~28+ source-pin tests remain (vs ~6 real
> subprocess loads). **REJECT** if already migrated to behavioral assertions.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### C2 — De-brittle 34 source-pins in the two reachability test files

- **State:** `UNVERIFIED`
- **Priority:** now (**pilot one flow first**)
- **Tier / Effort / Risk / Burn-fit:** C · decent · low · high
- **Dimensions:** test-brittleness
- **Pilot-first + trigger-and-observe (Codex):** these are *safety-flow* reachability tests (kill-switch reset, reconcile), so
  the bar is higher: a live-tree walk that finds the modal's `objectName` still does not prove the operator can *reach* it. The
  replacement must drive the actual entry affordance (trigger the RiskStrip/drawer signal) and observe the modal becomes
  reachable/visible. Pilot the kill-switch-reset flow first; only generalize once that one genuinely exercises reachability.
- **Locations:**
  - `tests/milodex/gui/test_kill_switch_reset_reachability.py` (25 `read_text` pins, 28 fns)
  - `tests/milodex/gui/test_reconcile_affordance_reachability.py` (9 pins, 12 fns)
- **Task:** Both assert reachability of safety-critical flows almost entirely by grepping QML source for literal signal
  names/ids/copy; the files' own docstrings admit they can't simulate events and defer real reachability to manual
  verification. Lift the load-bearing wiring into the subprocess `QQuickView` harness (walk live item tree, `findChild` by
  `objectName`, as `test_qml_load_smoke.py:1711` already does); drop redundant raw-source greps.
- **Payoff:** Turns 34 source-greps the docstrings concede don't prove reachability into actual live-tree reachability
  assertions for kill-switch reset + reconcile flows — higher *real* safety coverage, far less rename-brittleness.
- **Dispatch:** One agent per file: instantiate `Main.qml` in the subprocess harness, assert modal reachability via wired
  signals/`objectName`, delete redundant greps; gate on gui suite green.

**① Verification gate:**
> `grep -c "read_text" tests/milodex/gui/test_kill_switch_reset_reachability.py tests/milodex/gui/test_reconcile_affordance_reachability.py`.
> **CONFIRM** if ~30+ source-greps remain. **REJECT** if already converted to live-tree walks.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### C3 — Un-quarantine `test_design_system_showcase` + resolve 4 xfails

- **State:** `UNVERIFIED`
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** C · small · med · low→med (needs N-run determinism babysitting)
- **Dimensions:** test-brittleness
- **Locations:**
  - `tests/milodex/gui/test_app.py:366` (`@pytest.mark.skip` on `test_design_system_showcase_loads_without_errors_via_subprocess`)
  - `tests/milodex/gui/test_qml_components.py:358` (4 `@pytest.mark.xfail(strict=False)` whose reason says "If this test passes,
    remove the xfail marker")
  - `docs/KNOWN_FLAKY_TESTS.md:46`
- **Task:** Two stale quarantine items the 2026-06-17 conftest singleton fix likely already closed: (1) strip the `skip` from the
  design-system-showcase subprocess test after confirming N-run determinism, update `KNOWN_FLAKY_TESTS.md`; (2) confirm
  determinism of the 4 inline-instantiation xfails or convert them to subprocess-isolated tests.
- **Payoff:** Restores coverage of the full design-system component composition (currently dark); replaces 4 always-noisy
  never-protective xfails with deterministic pass/fail; clears stale quarantine docs.
- **Dispatch:** One agent runs the gui suite ~10× to confirm determinism, removes the markers (or converts xfails to subprocess
  isolation), updates `KNOWN_FLAKY_TESTS.md`.
- **CLAUDE.md note:** a lone full-suite non-pass is normally the *quarantined* showcase test (expected SKIP). After this item,
  that gotcha changes — update CLAUDE.md if the showcase test is un-skipped.

**① Verification gate:**
> Check the `skip`/`xfail` markers are still present, then run `pytest tests/milodex/gui` ~10×. **CONFIRM** (remove markers) if
> the suite is deterministic without them. **REJECT / keep quarantine** if it still flakes (root cause not actually fixed), or
> **REJECT** if markers already removed.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

# Tier D — Cheap hygiene sweeps (high leverage per line)

### D1 — Add **deduped** logging to ~11 silent `sqlite3.Error` swallows (GUI read layer)

- **State:** `UNVERIFIED`
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** D · small · low · high
- **Dimensions:** silent-failures
- **Locations:**
  - `src/milodex/gui/ledger_builders.py:31`
  - `src/milodex/gui/query_helpers.py:71`
  - `src/milodex/gui/_event_queries.py:136`
- **Task:** Every read helper in these files catches `sqlite3.Error` and returns `[]`/`{}` with NO log line, so a
  locked/corrupted/schema-drifted DB renders as "no trades / no history / $0" indistinguishable from an empty store. Add a
  warning at each swallow while keeping the empty-return contract.
- **⚠ Dedup the logging (Codex):** these helpers are called from **polling** read models — verified: `ActiveOpsState` is a
  `PollingReadModel` with `refresh_interval_ms=30_000`, and `activity_feed`/`attention`/`strategy_bank` states poll likewise. A
  persistent DB fault would otherwise emit a warning **every poll tick across every model = a log storm.** Log on the
  *health→fault transition* (and the recovery), not on every swallow: track last-error state per query (or per module) and only
  `logger.warning` when it changes, or rate-limit to once per N seconds. The goal is "a fault is visible in logs," not "the log
  fills with the same line."
- **Payoff:** A DB fault surfaces in logs instead of masquerading as empty data; "why is my ledger empty" becomes diagnosable
  without a repro — and without drowning the log.
- **Dispatch:** One agent: add a small dedup/transition helper + warning at each swallow matching the existing GUI logger
  convention; gate on suite green (returns unchanged). **Additive only — do not change control flow.**

**① Verification gate:**
> `grep -n "except sqlite3.Error" src/milodex/gui/*.py` and check each for a log line. **CONFIRM** if ~11 swallows return
> empty with no logging. **REJECT** if already logged.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### D2 — Doc-debt sweep (3 INDEPENDENT sub-items)

- **State:** `CONFIRMED-OPEN` (rolls up the sub-items; this item is DONE only when D2a+D2b+D2c are each DONE/REJECTED)
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** D · small · low · high
- **Dimensions:** doc-debt
- **Split rationale (Codex):** these are three unrelated docs with independent verdicts — track each separately so one stale
  sub-claim being already-fixed doesn't block the others. Do them in any order; each is its own outcome.

#### D2a — CLAUDE.md drifted line-refs + false "5Min SPY-only" claim
- **Location:** `CLAUDE.md`
- **Task:** Fix: `_simulate` is `engine.py:938` (doc says 922), `_simulate_daily` `:994` (978), `_simulate_intraday` `:1153`
  (1137); `ParquetCache._path` is `cache.py:92` (doc says 68); and the **"5Min cache is SPY-only" claim is false — 17 symbols
  cached** (`market_cache/v3/5Min/`: DIA, GLD, IWM, QQQ, SPY, TLT, XL*).
- **① Gate:** re-grep each ref against current `engine.py`/`cache.py`; `ls market_cache/v3/5Min/`. CONFIRM drifted refs; fix only those.
- **② Verdict:** **CONFIRM (verified 2026-06-21)** — engine refs off by ~15; 5Min cache holds 17 symbols.
- **③ Outcome:** _(OPEN — not started)_

#### D2b — STRATEGY_BANK.md states a removed co-run constraint
- **Location:** `docs/STRATEGY_BANK.md:23`
- **Task:** It still documents the removed eval-symbol co-run guard / 3-runner concurrency limit (commit `211d983` removed it) as
  a live constraint — falsely throttles soak testing. Rewrite per the ADR-0026 addendum.
- **① Gate:** check STRATEGY_BANK §23 vs commit `211d983` / ADR-0026 addendum. CONFIRM if the doc still asserts the limit.
- **② Verdict:** _(pending — verify before fixing)_
- **③ Outcome:** _(pending)_

#### D2c — Regenerate REQUIREMENTS_COVERAGE.md
- **Location:** `docs/REQUIREMENTS_COVERAGE.md:1`
- **Task:** Regenerate (stamped at an old commit) with the R-XXX-NNN cross-citation diff-check (memory: cross-citing an R-id in
  SRS prose corrupts the generated matrix — cite by ADR, regenerate, diff-check).
- **① Gate:** check the stamp vs HEAD; confirm `scripts/audit_requirements_coverage.py` (verify script name) regenerates cleanly.
- **② Verdict:** _(pending)_
- **③ Outcome:** _(pending)_

**Session log:**
- 2026-06-21 — split from the original bundled D2 per Codex review. D2a gate run + confirmed stale (engine refs + 5Min claim).
  D2b/D2c gates not yet run. No fixes applied.

---

### D3 — De-dup reconciliation serializers/money-formatter vs `cli/_shared`

- **State:** `UNVERIFIED`
- **Priority:** park (**do D4 first**)
- **Tier / Effort / Risk / Burn-fit:** D · small · low · high
- **Dimensions:** repetitive-boilerplate
- **Ordering (Codex):** consolidating serializers without first pinning their JSON contract risks silently shifting the shape
  during the merge. **D4 must land first** — its tests are the safety net that proves the dedup preserved every key. Parked
  until D4 is DONE.
- **Locations:**
  - `src/milodex/operations/reconciliation.py:1148` (`_account_to_dict`), `:1162` (`_format_money`), `_broker_order_to_dict`
  - `src/milodex/cli/_shared.py:121` (`format_money`), `:136` (`account_to_dict`), `order_to_dict`
- **Task:** `reconciliation._format_money` is identical to `_shared.format_money`; `_account_to_dict` returns the same 5-key
  dict as `account_to_dict`; `_broker_order_to_dict` overlaps `order_to_dict`. Collapse onto the canonical helpers (or a shared
  serialization module if a `cli`←`operations` import is unwanted), then sweep the 13+ scattered `*_to_dict` serializers
  (report.py, trade.py, experiment.py, walk_forward_runner.py) for the same overlap.
- **Payoff:** One money formatter and one account-serializer shape across CLI + reconciliation so the JSON contract can't
  silently drift between surfaces.
- **Dispatch:** One agent picks the shared home (decide import direction), repoints reconciliation + scattered callers; gate on
  suite green (shapes unchanged).

**① Verification gate:**
> Diff `reconciliation._format_money`/`_account_to_dict` against `_shared.format_money`/`account_to_dict`. **CONFIRM** if still
> duplicated. **REJECT** if already routed through the shared helpers.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### D4 — Unit-test `cli/_shared` JSON serializers & formatters

- **State:** `UNVERIFIED`
- **Priority:** now (**do before D3**)
- **Tier / Effort / Risk / Burn-fit:** D · small · low · high
- **Dimensions:** test-coverage
- **Ordering (Codex):** this is the contract pin that makes D3's consolidation safe. Land it first — then D3 can dedup with the
  key-set tests guarding against silent drift.
- **Locations:**
  - `src/milodex/cli/_shared.py`
  - `tests/milodex/cli/` (no test imports `milodex.cli._shared` today)
- **Task:** Pin the machine-readable JSON contract: `account_to_dict`, `position_to_dict`, `order_to_dict` (enum `.value`
  coercion, `filled_at` None→None vs isoformat), `performance_metrics_to_dict` (the R-ANA-006 "single JSON shape" full key set),
  `format_money`/`format_pct`, `parse_iso_date` valid + "Invalid date" `ValueError` branch, `error_result` shape,
  `command_name_from_args` dotted assembly.
- **Payoff:** Guards the R-CLI-009 / R-ANA-006 machine-readable contract so a serializer key change fails a test instead of
  breaking downstream consumers silently. Pairs with D3.
- **Dispatch:** One agent writes `tests/milodex/cli/test_shared.py` asserting full key sets + error branches; gate on the new
  file passing.

**① Verification gate:**
> `grep -rl "cli._shared\|cli import _shared" tests/`. **CONFIRM** if no test directly imports `_shared`. **REJECT** if a
> dedicated suite exists.

**② Verdict:** _(pending)_

**③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

### D5 — Finish GUI-hardening P9/P13 (2 INDEPENDENT sub-items)

- **State:** `UNVERIFIED`
- **Priority:** park
- **Tier / Effort / Risk / Burn-fit:** D · small · low · high
- **Dimensions:** deferred-work
- **Split rationale (Codex):** the surface recompose and the modal de-dup are unrelated QML changes with independent verdicts —
  track separately. Parked (low-yield polish), but **do alongside C1/C2 if doing the QML-smoke work anyway** — both touch the
  same smoke pins, so the behavioral-assert conversion is shared effort.

#### D5a — Recompose BenchSurface onto shared shells
- **Locations:** `src/milodex/gui/qml/Milodex/surfaces/BenchSurface.qml:162`
- **Task:** Migrate `BenchSurface` onto the shared `ScrollSurface`/`EditorialHeader`/`SurfaceBase` shells that Front/Ledger/Desk
  already use; convert the smoke-pinned `interactive:false` + bleed-through guard to behavioral checks.
- **① Gate:** open `BenchSurface.qml`. CONFIRM if it still rolls its own `Flickable`/header. REJECT if already on shared shells.
- **② Verdict:** _(pending)_   **③ Outcome:** _(pending)_

#### D5b — De-dup BenchEvidenceModal onto shared DetailRow
- **Locations:** `src/milodex/gui/qml/Milodex/components/BenchEvidenceModal.qml`, `…/components/DetailRow.qml`
- **Task:** De-dup `BenchEvidenceModal`'s bespoke `EvidenceSection`/`EvidenceField` by extending shared `DetailRow` with
  multiLine/alignment + reconciling colors, then repoint.
- **① Gate:** open `BenchEvidenceModal.qml`. CONFIRM if it still defines bespoke `EvidenceSection`/`EvidenceField`. REJECT if migrated.
- **② Verdict:** _(pending)_   **③ Outcome:** _(pending)_

**Session log:**
- 2026-06-21 — split from bundled D5 per Codex review; parked. Gates not run.

---

# Tier E — Intraday infra builds (RESOLVED — both superseded by merged work; kept as record)

> **RESOLVED 2026-06-21 (Codex review + verification): both E items are SUPERSEDED by already-merged work.** The finders' "deferred
> Workstream D/E" framing was stale — the intraday-evidence lean slice merged to master 2026-06-19 (`07b4cb4`) and Phase 2 merged
> 2026-06-21 (`ea12cc1`). I read the two modules and confirmed they cover the specs: `data/intraday_readiness.py` (E1) and
> `research/evidence_assembler.py` (E2). Both items are `RESCOPED` to a thin gap-check residual — **do not rebuild.** This tier is
> kept only as a record + the residual gates. See memory `project_intraday_etf_evidence_lean_slice` / `project_intraday_etf_evidence_phase2`.

### E1 — Intraday-aware data-readiness report (Workstream D) — SUPERSEDED

- **State:** `RESCOPED`
- **Tier / Effort / Risk / Burn-fit:** E · — · low · —
- **Dimensions:** deferred-work
- **Locations:**
  - ✅ `src/milodex/data/intraday_readiness.py` (326 lines, merged) — the build this item proposed
  - `docs/INTRADAY_ETF_EVIDENCE_HARDENING.md:186` (Workstream D spec)

**① Verification gate:** read `intraday_readiness.py` against the Workstream-D spec list.

**② Verdict:** **RESCOPE (verified 2026-06-21).** The build is done. `intraday_readiness.py` already implements the entire
Workstream-D checklist: per-session EXPECTED-bar-grid coverage (`SESSION_COVERAGE_FLOOR`), missing session-open/close bars,
zero-volume bars, intra-session gaps, stale-dataset-tail, deterministic order-invariant content hash, feed-quality label, and
IEX inward-price-bias cross-check. Its module docstring is explicit that it supersedes the daily-shaped `bar_quality.scan_*`.

**③ Outcome / residual:** Original "build it" is **closed**. Only residual worth a future session: confirm there are no genuine
*gaps* vs the spec (e.g. a standalone per-universe CLI *report* wrapper, if `bar_quality` is still daily-only for a reason). If a
real gap exists, open a new narrow item for exactly that; do **not** rebuild the scanner.

**Session log:**
- 2026-06-21 — read `intraday_readiness.py`; confirmed it covers the full Workstream-D spec. Rescoped from build → closed; residual = thin gap-check only.

---

### E2 — Reusable baseline/null framework (Workstream E) — SUPERSEDED

- **State:** `RESCOPED`
- **Tier / Effort / Risk / Burn-fit:** E · — · low · —
- **Dimensions:** deferred-work
- **Locations:**
  - ✅ `src/milodex/research/evidence_assembler.py` (731 lines, merged) — the framework this item proposed
  - `docs/INTRADAY_ETF_EVIDENCE_HARDENING.md:254`, `:909` (Workstream E spec)

**① Verification gate:** read `evidence_assembler.py` against the Workstream-E spec list.

**② Verdict:** **RESCOPE (verified 2026-06-21).** The framework is built. `evidence_assembler.py` joins a walk-forward
candidate against the canonical null families — `_BASELINE_KINDS` = (`unconditional_intraday_long`, `time_of_day_null`,
`random_matched_exposure.intraday`, `no_trade`) — produces per-symbol comparisons + a verdict + decisive-loss predicate, folds
in the readiness scan + a reproducibility manifest, and writes exactly one append-only experiment-registry row (with the
IEX-exploratory / non-durable coherence asserts). That is the Workstream-E spec.

**③ Outcome / residual:** Original "build it" is **closed**. Residual worth a future session only if a *specific* null from the
spec is missing (e.g. a "family-specific null attachment point" not yet wired) — open a narrow item for that one null. Do **not**
rebuild the assembler.

**Session log:**
- 2026-06-21 — read `evidence_assembler.py`; confirmed it joins all three+one null families, per-symbol comparisons, verdicts, registry rows. Rescoped from build → closed; residual = per-null gap-check only.

---

# Tier F — Product validation (the headline; added 2026-06-21 per Codex review)

> **Why this tier exists:** the original roadmap was 100% internal maintenance. Codex's strongest point — "the largest omission is
> product-facing work" — lands against FOUNDER_INTENT: the project's core purpose is to prove a *real, trustworthy, shareable*
> system with *functional end-to-end behavior* (FOUNDER_INTENT §13, §92 "Trust Through Lifecycle/Evidence/Preview/Veto", §276
> "First-Launch Experience", §299 "Audience and Shareability"). And LAUNCH_READINESS.md has **five unverified MANUAL-REQUIRED**
> items (§1.1 first-run, §1.5 showcase, §1.7 broker/env missing-state, §1.9 kill-switch visibility, §5.1 no-prior-data). These
> drills are *exactly* usage-heavy, routinely-skipped work that proves the product — and they close real launch blockers.
> "God-file splitting merely proves we can move code around — which is not the headline." This tier is.

### F1 — Clean-room install + first-launch audit

- **State:** `UNVERIFIED`
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** F · decent · low · high
- **Dimensions:** product-validation (FOUNDER_INTENT §276/§299; LAUNCH_READINESS §1.1, §1.2, §5.1)
- **Task:** From a clean checkout / fresh environment (no `.env`, no `data/`, no caches), walk the documented install path
  (`pip install -e ".[dev]"`, `.env.example` → `.env`) and first launch (CLI + GUI). Record every friction point, missing-state
  behavior, cryptic error, and undocumented step. Verify the system behaves like "a real product with care behind it," not "a lab
  experiment that only works on the creator's machine" (FOUNDER_INTENT §303). Produce a findings doc; file each gap as its own
  follow-up. Optionally drive the GUI with the webapp-testing / computer-use harness for first-launch screenshots.
- **Payoff:** Closes LAUNCH_READINESS §1.1/§5.1 (currently MANUAL-REQUIRED, unverified); proves shareability — the founder's
  stated primary meaning of the project.
- **Dispatch:** One agent in an isolated worktree/clean env runs the documented steps verbatim, logs divergence between docs and
  reality, captures first-run-with-no-data behavior. **Read-only on the real `data/` — never nuke the live DB; use a temp dir.**

**① Verification gate:**
> Confirm the install/first-run path is still **unverified** (LAUNCH_READINESS §1.1/§1.7/§5.1 still marked MANUAL-REQUIRED).
> **CONFIRM** if so. **REJECT** if a clean-room audit was already done and those items are marked PASS.

**② Verdict:** _(pending)_   **③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

### F2 — End-to-end paper lifecycle rehearsal

- **State:** `UNVERIFIED`
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** F · large · med · high
- **Dimensions:** product-validation (FOUNDER_INTENT §92 lifecycle/evidence/preview/veto; LAUNCH_READINESS §0 paper acceptance)
- **Task:** Drive the full operator lifecycle end-to-end and verify each handoff produces legible, reviewable output:
  **setup → data fetch → backtest → evidence assembly → promotion proposal → paper runner launch → explanation**. At each stage
  confirm the artifact a real operator would inspect exists and reads correctly (evidence package, proposal preview, runner
  explanations keyed by session). This is the "justified trust before capital" promise made concrete.
- **Payoff:** Proves the harness's central claim — that automated investing is "legible, reviewable, bounded, controlled"
  (FOUNDER_INTENT §109) — actually holds across the whole chain, not just per-unit-test.
- **Risk note (med):** exercises the promotion + paper-runner path. **Paper mode only; never live.** Do not promote to live, do
  not allocate real capital, do not reset a kill switch — those are Appendix-Z operator actions. Run `risk-invariant-reviewer` if
  any code change falls out of the rehearsal (the rehearsal itself should be observation, not modification).
- **Dispatch:** One agent runs the documented lifecycle against the paper account on a scratch DB, capturing each stage's output;
  files any broken handoff as a follow-up. Leans on the `fleet-ops` / `fleet-health` skills for runner launch/observe.

**① Verification gate:**
> Confirm there's no recent green end-to-end lifecycle rehearsal on the current code (LAUNCH_READINESS §0 was 2026-05-15, pre many
> merges). **CONFIRM** to run a fresh rehearsal. **REJECT** only if one was just done against `HEAD`.

**② Verdict:** _(pending)_   **③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

### F3 — Failure / recovery drills → actionable operator explanations

- **State:** `UNVERIFIED`
- **Priority:** now
- **Tier / Effort / Risk / Burn-fit:** F · decent · med · high
- **Dimensions:** product-validation, silent-failures (FOUNDER_INTENT trust; ties to D1; `docs/TROUBLESHOOTING.md`)
- **Task:** Inject the known fault modes and verify the operator gets an **actionable** explanation (not a silent empty state or a
  cryptic trace): **stale market data, locked/corrupt SQLite DB, broker outage / API error, dead or wedged runner.** For each:
  trigger it in a scratch environment, observe what the CLI/GUI/logs tell the operator, and confirm the message points at the fix
  (cross-check `TROUBLESHOOTING.md`). Where the system fails silently, file it (D1 is the first such fix already identified).
- **Payoff:** Trust under failure is where "feels trustworthy" is actually won or lost (FOUNDER_INTENT §232). Turns the
  silent-failure theme from a code-smell list into verified operator-facing behavior.
- **Risk note (med):** fault injection must be in a **scratch env** — never corrupt the real `data/milodex.db` or kill live
  runners. Simulate broker outage via the broker interface seam, not by touching real credentials.
- **Dispatch:** One agent per fault mode in isolation; record observed operator-facing output vs expected; cross-check
  `TROUBLESHOOTING.md`; file silent-failure gaps as follow-ups. Synergy with D1 (GUI sqlite swallows).

**① Verification gate:**
> Confirm these fault modes don't yet have a documented "operator sees X, does Y" verification. **CONFIRM** to run the drills.
> **REJECT** any fault mode already drilled + documented.

**② Verdict:** _(pending)_   **③ Outcome:** _(pending)_

**Session log:**
- _(none yet)_

---

# Appendix Z — Needs operator judgment (NOT autonomous burns)

These surfaced in the audit but were deliberately excluded from autonomous dispatch: each touches the sacred risk/promotion/
broker path or needs a verdict call only Zack can make. They are recorded so nothing is lost. **Do not auto-confirm.** To take
one on, Zack re-opens it by hand (change State to `UNVERIFIED` and add the decision to its log).

### Z1 — broker cancel/get-position silent-failure logging — `REJECTED (autonomous)`
- **Why gated:** edits the sacred broker money path (`cancel_order` in the kill-switch/shutdown cancel path). Even
  logging-only changes here need Zack's judgment. `risk_sensitivity: high`.

### Z2 — Phase-2 live-mode stage-compat TODO — `REJECTED (autonomous)`
- **Why gated:** gated on an ADR amendment + explicit human approval to relax the paper-only promotion guard. Actioning it
  weakens the sacred layer. Explicit do-not-touch.

### Z3 — fractional-vs-whole-share sizing dispatcher — `REJECTED (autonomous)`
- **Why gated:** introduces a new dispatcher over `execution/sizing.py` feeding order quantity; the whole-share floor is
  intentional for equities, so the seam needs design judgment, not a mechanical sweep. Real footgun, but not a safe burn.

### Z4 — risk/promotion mutation-pin + test-efficacy rerun — `REJECTED (autonomous)`
- **Why gated:** add-only test assertions on the sacred risk/promotion layer (`demote()`/kill-switch-string pins) plus a
  Windows-finicky mutation re-run. Valuable but needs careful per-assertion review — fold into a deliberate session, not a bulk
  burn.

### Z5 — experiment-registry governance — `REJECTED (autonomous)`
- **Why gated:** governance-subsystem work needing Zack's verdict — operator-driven by nature. (The Phase-2 evidence *merge* this
  originally bundled is **done**: merged to master 2026-06-21 `ea12cc1`; the infra slices E1/E2 are likewise merged/RESCOPED.
  Only the registry-governance verdict remains operator-owned.)

---

## Tiny dead-code bundle (do opportunistically, not as standalone burns)

Verified-dead, too small for an individual slot — sweep these in one PR *only if already doing adjacent GUI work*:
`delete-dead-stage-label-helper`, `delete-dead-kanban-read-model-chain`, `inline-data-freshness-alias`,
`yaml-manifest-skip-silent-continue` (tiny logging add), `timeframe-choices-30m-gap` (2-line, conditional on a 30-min lane).

## Conditional / re-check-first

- **RM-007 GUI polling-lifecycle consolidation** — needs a re-verify that the 2026-06-02 `PollingReadModel` base didn't already
  subsume it before any work. Spin off only after confirming residual duplication exists.

---

*Audit method: 9 parallel finder agents (one per dimension) → barrier → single high-effort synthesis agent that deduped and
ranked by usage-burn fit. Raw run: `wf_8992d1ae-443`. A1 + D2 (partial) verified by hand 2026-06-21 before this file was
written; all other gates are unrun — run them.*

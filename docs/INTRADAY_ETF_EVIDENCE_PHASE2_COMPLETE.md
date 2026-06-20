# Intraday ETF Evidence — Phase 2 Complete (Tiers 0–4)

**Branch:** `intraday-etf-evidence-phase2` — **NOT merged. Operator reviews and merges.**
Off master `b5962e0`. **38 commits, 170 files, +14,659 / −96.** Suite **3176 passed, 1 skipped, 4 xfailed**. Master untouched.

---

## What the lane can now do (definition of done — met)

Freeze the 17-ETF universe (`universe.liquid_etf_core.v1`) → warm its 5Min cache → produce a readiness report across all 17 → run a price-action candidate + the required baselines **per-symbol across all 17** (real fan-out, not `sorted(universe)[0]`) → compose an evidence report + an experiment-registry entry, every verdict explicitly labeled **IEX-exploratory / non-durable** (ADR 0017).

Proven end-to-end: the rsi2 intraday candidate was run, assembled, adjudicated, and registered (below).

## Phase map

| Tier | Delivered | Key commits | Review |
|---|---|---|---|
| **0** | cross-ETF fan-out + readiness fixes + 5Min cache warm (17 ETFs) | (pre-finalization) | ponytail |
| **1** | E-PR2 random-matched null + E-PR3 `baseline_ref`; **finalization** N1 clamp / N2 engine force-flatten / N3 / N4 / seed-warmup fix | `ccd6810` `75fe447` `120754c` `09e92c2` | N2 **risk-invariant SAFE** |
| **1 (run)** | 68-cell working-lane evidence run (candidate + 3 baselines × 17), 0 errors | `9d3084c` | — |
| **2 (F)** | experiment registry — migration 015 + store + CLI (append-only, no-delete) | `3819ab5` `a5fda91` | F-PR1 **risk-invariant SAFE** |
| **3 (G)** | evidence assembler + CLI — candidate-vs-baseline deltas + IEX-non-durable verdict policy | `4342c5c` `ffd83c9` | validated end-to-end |
| **4 (H)** | 3 new candidate strategies: gap-continuation, late-session momentum, opening-range-retest | `78d5be3` `f547215` `efd322a` | ponytail (dropped a redundant 4th) |
| **gate** | thermonuclear follow-ups: writer-invariant marker coherence, drop `--feed-label`, predicate boundary tests, gitignore | `0e0e623` | thermonuclear |

**Perf:** Fix A (explanation batching) + Fix B (date idiom) landed. **Fix C was confirmed a no-op** (measured: snapshots are once-per-sweep; cProfile shows zero per-bar fsync — the remainder is CPU-bound pandas tz-conversion, not batchable).

## First real evidence (governance proof)

`research evidence` over the 68-cell screen → verdict **`candidate_underperforms`**, terminal_status **`rejected`** (decisive-loss predicate fired: **17/17 symbols below all three nulls, tightest margin 4.90 Sharpe** vs the ≥2.0 threshold; median ΔSharpe −6.25 vs unconditional). Registered as `intraday-rsi2-etf-evidence-2026q2` with `durable=false`, `iex_exploratory=true`, `revisitable=true`. **The candidate is decisively worse than random on every symbol — a clean no-edge detection** (expected for a known-losing canary), labeled non-durable on IEX.

## Verdict policy (operator-decided, second-opinion-adjudicated)

`terminal_status` ∈ {`failed`, `rejected`, `inconclusive`}. **`rejected`** only when the candidate's Sharpe is below all three nulls on **≥14/17** symbols with the tightest qualifying margin **≥2.0**; a decisive *win* → `inconclusive` (IEX can overstate an edge). ADR-0017's non-durability is honored *structurally*: every row carries `feed="iex"` / `durable=false` / `revisitable=true` + the predicate evaluation, and the writer **refuses** to emit `rejected` unless the full IEX marker set is coherent on the report.

## Thermonuclear final gate

**Merge-ready, zero BLOCKER/MAJOR.** 7 finders → 10 findings → 8 verified (all MINOR/NIT), 2 overstated→NIT, 0 dismissed. Risk layer, promotion gates, kill switch, broker path: **untouched**. Migration 015 strictly additive (`MIN_COMPATIBLE` unchanged at 12). Folded-in fixes are in `0e0e623`.

### Deferred follow-ups (safe, post-merge — not blocking)

- **#3** Promote `durable`/`feed` to a queryable column (`migration 016`). *Mitigated:* `revisitable` IS a column (forced `True`), and `json_extract(evidence_json, '$.durable')` is queryable today.
- **#5** `research/fanout.py:123` `baseline_ref` rewrite uses substring `replace()` — collision-proof for all 17 real tickers; optional dotted-component hardening.
- **#7** `tests/.../test_research_evidence_command.py` single-write test name is misleading (assembler mocked); the real single-write guarantee is pinned by `test_single_registry_append` (un-mocked).

## Standing caveat

Every verdict on this surface is **IEX-exploratory / non-durable** until a SIP/consolidated provider exists (ADR 0017, deferred). The lane is *functional and governed*; its verdicts are *exploratory*. The 3 new H strategies are research candidates at `stage: backtest` — built and ready for evaluation, not promoted.

## How to review

Tests run against the worktree source via `$env:PYTHONPATH = "<worktree>\src"` (the `.venv` editable install points at master's `src`). `git diff b5962e0` is the full phase.

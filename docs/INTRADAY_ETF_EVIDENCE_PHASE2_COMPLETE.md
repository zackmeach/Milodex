# Intraday ETF Evidence — Phase 2 Complete (Tiers 0–4)

**Branch:** `intraday-etf-evidence-phase2` — **NOT merged. Operator reviews and merges.**
Off master `b5962e0`. Suite **3190 passed, 1 skipped, 4 xfailed**. Master untouched. **Post-review corrections (2026-06-20) below** — an outside review caught a BLOCKER + 4 MAJOR this gate's thermonuclear missed.

---

## Post-review corrections (2026-06-20) — an outside review found a BLOCKER the gate missed

An external adversarial review (Codex) found **1 BLOCKER + 4 MAJOR + 2 MINOR** that the thermonuclear
gate (which had called this "merge-ready, zero BLOCKER/MAJOR") missed. All seven were independently
re-derived against the code and **confirmed real**, then fixed on this branch.

| Finding | Sev | Fix |
|---|---|---|
| Nulls held **overnight** — a final-bar exit filled at next session's open, not at the close → "held-to-close intraday null" was false; asymmetric overnight-drift confound vs the intraday-exiting candidate | **BLOCKER** | `acd96a4` — engine realizes final-bar intraday exits at the session close; **risk-invariant SAFE** |
| 3 H strategies used `sorted(universe)[0]` on a 17-ETF base config → silently evaluated **DIA** while labeled SPY (fan-out doubled DIA, omitted SPY) | MAJOR | `05a5e80` — `single_symbol` guard + `spy_only` base configs + 17-symbol bijection test |
| Decisive-loss margin measured against the **strongest** null, not the nearest | MAJOR | `b53c6e2` — `min(nulls) − candidate` + boundary tests |
| ORB-retest accepted a **partial** opening range → manufactured breakouts on a missing bar | MAJOR | `05a5e80` — requires all on-grid opening-range bars |
| `research evidence` trusted screen-JSON metrics without provenance validation | MAJOR (MINOR blast radius — non-durable registry) | `b53c6e2` — run_id + date-consistency validation |
| Buffered no-action explanations mis-order in id-ordered reads | MINOR | `b53c6e2` — documented; no live reader affected |
| 2 unsorted import blocks (falsified the "clean lint" claim) | MINOR | `05a5e80` — ruff clean |

**Why the gate missed the BLOCKER:** the N2 docstring asserted the overnight hold was "by design,"
which disarmed the thermonuclear finders — they accepted a confident comment instead of challenging
the methodology. The gate was strong on *risk-layer safety* (Codex independently re-confirmed that
held — risk/promotion/broker/kill-switch untouched) and weak on *statistical/methodology correctness*.

**Corrected evidence (held-to-close engine + fixed predicate).** The 68-cell screen was re-run on the
corrected engine and re-assembled (experiment registry **row 2**, append-only — row 1 preserved).
Verdict still **`rejected`** (rsi2 below all three nulls on **17/17** symbols), but the decisiveness
was overstated: tightest margin **4.90 → 2.47 Sharpe** (still ≥ 2.0; the nulls dropped once their
overnight-drift return was removed — e.g. random-matched TLT −3.37 → −5.16). The no-edge conclusion
holds; its apparent margin was nearly 2× inflated. Evidence: `docs/reviews/working_lane_evidence_phase2.md`.

**Verification after fixes:** suite **3190 passed, 1 skipped, 4 xfailed**; ruff clean; engine fix
risk-invariant SAFE (attack log). The "merge-ready" verdict below is **superseded** by this section —
merge-readiness now rests on these fixes + the corrected rerun, not the original thermonuclear pass.

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

# Intraday ETF Evidence — Phase 2 Orchestration Brief

**For:** the orchestrator agent of a fresh session (run with **Ultracode ON**) that will drive the full-stop completion of the intraday ETF evidence next step.
**From:** the planning/review session that established the bearing below.
**Created:** 2026-06-19.
**Operator:** Zack — solo dev, Windows/PowerShell. Terse, blunt, forward-motion. Risk layer is sacred. Quality bar = his own; he reads every artifact.

---

## 0. Your role (read this first — it is the whole point)

You are the **orchestrator and final reviewer**, working *with* the operator across the whole session. You hold all the context: where we started, the plan, how execution is going, where we are headed. **You do NOT implement.** You call the shots:

- **Dispatch:** decide which subagent at which intelligence level handles each task (model tiering in §5).
- **Cadence:** decide when work gets committed / grouped into a PR.
- **Reviews:** decide which review runs on which PR (`/ponytail-review`, `risk-invariant-reviewer`, a thermonuclear review) — see §5.
- **Slop control:** every implementer you dispatch is **explicitly told to do the simplest thing that satisfies the spec + tests** (run them under ponytail; require `ponytail:` comments on deliberate simplifications). This is a controlled environment — the point is to keep the code from becoming slop, and to catch solutions implemented in less-manageable ways before they land.
- **Sync:** you check in with the operator at the tier boundaries and review gates in §4/§5 — surface decisions, don't guess them.

Load the operator's playbooks before dispatching: `~/.claude/playbooks/pm-mode-dispatch.md` (model selection, two-stage review per PR — this scope is ~12 PRs, squarely PM-mode) and `~/.claude/playbooks/reviewer-dispatch.md` (code-grounding + adversarial review prompts; a default reviewer prompt approves work a careful human would catch).

**Definition of done:** the *entire* reshaped phase (§3, Tiers 0–4) complete on a worktree — green, `/ponytail-review`'d, thermonuclear-passed at both gates — such that the lane can: freeze the 17-ETF universe → warm its data → produce a readiness report across all 17 → run a price-action candidate + required baselines **per-symbol across all 17** → compose an evidence report + experiment-registry entry, with every verdict explicitly labeled **IEX-exploratory / non-durable**. **Not merged to master** — the operator reviews and merges.

---

## 1. Current bearing (verified this session)

The **lean slice is already merged to master and green** (commits `12eeea3..b5962e0`; `2796 passed, 1 skipped, 4 xfailed`). It shipped: ADR-0016 instrument-eligibility denylist + `universe.liquid_etf_core.v1` (C-light), intraday-aware data-readiness scanner + CLI (D), the IEX price-fidelity gate (advisory), and two baselines (no-trade, time-of-day-null). An adversarial 5-lens review confirmed: scope held exactly (nothing in risk/execution/promotion/core/migrations; `loader.py` +6 is behavior-neutral), the load-bearing pieces have **mutation-confirmed behavioral tests** (not tautologies), and the a186bc8 bar-grid fix is algebraically exact. The build session's self-assessment is honest.

**The headline gap (dominant finding):** the lane's stated purpose — *evaluate an edge across 17 ETFs* — has **zero operational mechanism today**. Every single-symbol intraday strategy trades `sorted(universe)[0]` and **silently discards the other 16**. Pointed at `liquid_etf_core.v1` it trades **DIA only**, on a **cold cache (→ 0 trades → reads as "no edge")**. There is **no batch/fan-out path** (`milodex backtest` takes one strategy_id; the plan's "via the batch path" is fiction). So the *tooling* is built; the lane cannot yet do its job. Closing that is Tier 0 below.

Full detail: `docs/reviews/2026-06-19-intraday-etf-evidence-lean-slice-build.md` (honest self-assessment) and the lean-slice plan `docs/superpowers/plans/2026-06-19-intraday-etf-evidence-lean-slice.md` ("Review fold" section).

---

## 2. Foundation fixes the review pulled onto the critical path

These are **preconditions**, not parallel work. Verify each in source before building on it.

**Must-fix-first (the first PRs of Tier 0):**
- **Cross-ETF fan-out.** `bench_time_of_day_null.py:60`, `bench_unconditional_intraday_long.py:72`, `meanrev_rsi2_intraday.py:98`, `meanrev_vwap_reversion_intraday.py:97` all use `sorted(universe)[0]`; only `regime_spy_shy_200dma.py:87` guards multi-symbol. **Locked mechanism (operator decision #2): per-symbol runs** — generate 17 single-symbol configs per strategy (or a template + thin per-symbol loop over the existing `research screen` / `walk_forward_batch.run_batch` — which fans over **`strategy_ids`, not symbols**, `walk_forward_batch.py:97`, so the per-symbol configs must exist first), **plus** a loud `len(universe) > 1` guard so a single-symbol strategy can never silently trade `[0]` again. Do NOT build a multi-symbol strategy contract.
- **5Min cache warmup, 16 non-SPY ETFs.** Cache is SPY-only. `python -m milodex.cli.main data fetch-universe --universe-ref universe.liquid_etf_core.v1 --timeframe 5m --start <date> --end <today> --force`. A cold-cache 0-trade result is indistinguishable from a real negative verdict — it silently poisons the baseline comparison.

**Fix-alongside (must land before/with Tier 3–4, they corrupt evidence verdicts):**
- **Grid-validate the coverage metric.** `intraday_readiness.py:183-186` counts `len(session)` with no dedup / off-grid filter → a duplicate or off-grid bar pushes `coverage_pct` >100% and returns `'pass'` with zero warnings (empirically: dup → 101.28% pass). Add an off-grid (`offset % tf != 0`) / duplicate check; cap coverage at 100 or count distinct grid offsets. **Zero test coverage today** — add dup/off-grid/>100% tests.
- **Extend `US_MARKET_HALF_DAYS` through 2026.** `_session_intraday.py:43-55` max entry is `date(2025,12,24)`; a 2026 half-day (2026-11-27, 2026-12-24) scores as a full 390-min session → spurious `coverage_below_threshold` + `missing_session_close_bar` every such session.
- **`load_strategy_config` doesn't resolve/guard `universe_ref` configs.** It returns `((), ref)` (via `_load_universe`, `loader.py:519`) **without** running the eligibility guard; the guard fires only where a `universe_ref` is actually resolved (`resolve_universe_ref`, reached by `StrategyLoader.load` at `loader.py:101`, `data fetch-universe`, and `paper_runner_control`). So Phase-F/G code that reads configs via bare `load_strategy_config` gets an **unresolved empty universe**, not validated symbols — it MUST call `resolve_universe_ref(ref, config_path)` (or go through `StrategyLoader.load`) for the concrete list. (ADR 0016's enforcement section is already accurate — optionally add one sentence noting bare `load_strategy_config` does not resolve `universe_ref`; do not chase a "fires on each load" string, it isn't there.)

**Optional:**
- **Quarantine the IEX gate.** `consolidated_reference.py:43` floor `0.80` is **uncalibrated** (acknowledged in the module docstring `:22-26`); advisory-only, opt-in, gates nothing. Keep as scaffolding but **never let `iex_inward_price_bias` inform a verdict until calibrated** against a real IEX-vs-SIP sample; or trim it (one module + one CLI flag + one test, zero downstream consumers) — the clean first cut. Add a multi-session majority test (the one positive test uses `min_sessions=1`, bypassing the real default) + an end-to-end firing test. NOTE: the daily side keying by `t.date()` (`:150`) is **intentional** (a midnight-stamped daily bar's `.date()` IS its session date; the intraday side already uses `session_date_et` at `:102`) — do not "fix" it.

---

## 3. The reshaped phase (~12 PRs, critical-path ordered)

| Tier | PR | Size | Notes / key file:line |
|---|---|---|---|
| **0** | **Fan-out** (per-symbol config gen/template + thin run over `run_batch` + `len(universe)>1` guard) | decent | the dominant gap; locked to per-symbol (decision #2). **Order:** generate 17 single-symbol configs/strategy_ids → feed those into `run_batch`, which fans over **`strategy_ids`, NOT symbols** (`walk_forward_batch.py:97`) — it does no per-symbol expansion itself |
| **0** | **Readiness-fix bundle** (grid-validate coverage + dedup/off-grid + 2026 calendar + ADR wording) | small | all additive; `intraday_readiness.py`, `_session_intraday.py:43`, ADR 0016 |
| **0** | **Cache warmup** (operational + a verification readiness scan across 17) | op | not a code PR; gate before any candidate run |
| **1** | **E-PR2 random matched-exposure** | decent | **highest-risk PR. PREFER a config-parameter seed** (a seed in `parameters`, already a `Mapping` on the context — `base.py:100` — derived per-(symbol,session-date) inside the strategy): satisfies the spec with **zero change to the frozen `StrategyContext`**. Only if infeasible, mutate `StrategyContext` (`base.py:84-109`) — and note **`test_base_reasoning.py` does NOT guard `StrategyContext`** (it pins `DecisionReasoning`); the real break surface is every `StrategyContext(` call site (`loader.py:102` + runner + fixtures), so grep that. Determinism per-(symbol,session-date), not global (`walk_forward_runner.py:254-260`). Mandatory `risk-invariant-reviewer` + thermonuclear. "Matched exposure" = match candidate trade-count **only** (randomized entry, **time-stop-only exit held to close — deliberately NOT hold-duration**: the candidate's 0.5% stop on random entries rigs the null and its RSI-revert exit can't be replicated; operator decision 2026-06-20, re-confirmed at the Tier-1 gate), fixed recorded seed. Unmatched hold-duration is a known, accepted exposure confound for this IEX-exploratory milestone. |
| **1** | **E-PR3 family-null attachment** (optional `baseline_ref` loader/config field) | small | metadata until G consumes it |
| **2** | **F-PR1 experiment registry store** (migration 015 + `ExperimentEvent` + append/list/get/update, no-delete) | decent | clone of the promotions feature; migration 015 as head; **do NOT bump `MIN_COMPATIBLE`** (stays 12 — an additive table no older code reads doesn't meet the bump bar, `event_store.py:353-361`); bump the **7** `schema_version == 14` asserts → 15 atomically (`test_event_store.py:56,83,1118,1235` + `test_migrations.py:167,247,313`; leave `test_concurrency.py`'s `>= MIN_COMPATIBLE` asserts alone). Ship a **no-delete / no-mutate-in-place assertion test in-PR** + `risk-invariant-reviewer` (don't wait for the gate). R-PRM-011 / `PROMOTION_GOVERNANCE.md:137-149` |
| **2** | **F-PR2 experiment CLI** (create/list/update) | small | clone `cli/commands/promotion.py`; wire into `main.py` |
| **3** | **G-PR1 evidence-report assembler** (join run_manifest + EvidencePackage + baseline results + readiness report + registry id; candidate-vs-baseline delta is new) | decent | must call `resolve_universe_ref` for the concrete universe; must NOT gate on `coverage_pct` until grid-validated; E-PR4 delta-store folds in here |
| **3** | **G-PR2 CLI/bench wiring** | small | facade per ADR 0051 |
| **4** | **H ×4 candidate strategies** (opening-range, gap, late-session, price-extension) | small each | clone `breakout_orb_intraday.py` / `momentum_vwap_trend_intraday.py` + `_session_intraday`; **long-only**; per-symbol configs (Tier 0); do NOT add `DecisionReasoning` fields without `omit_if_default` (golden test `test_base_reasoning.py`) |

**Dependencies:** Tier 0 gates everything. E-PR3 is independent of E-PR2. F before G (G reads the registry id). H consumes the fan-out (Tier 0) and is the first real consumer of G. Each PR's tests green before the next.

**Carry this caveat to Tiers 2–4:** the IEX price-fidelity problem stands — even *correct* multi-symbol verdicts are **non-durable on IEX** (under-samples session extremes / misses auction prints; baselines can't detect feed bias since candidate + null share the same bars). Tier 0+1 make the merged lane *functional* and are unambiguously worth it; F/G/H are the heavier governance+strategy build whose verdicts must be labeled IEX-exploratory until a SIP/consolidated provider exists (ADR 0017, deferred Phase 1.2+, absent from code).

---

## 4. Operator checkpoints (sync, don't guess)

Pause and sync with the operator at: **end of Tier 0** (lane mechanically able to fan out + warmed), **end of Tier 1** (working lane: a real strategy evaluated across 17 ETFs with baselines — the natural reviewable milestone), and **before final merge-readiness**. Surface the open decisions in §6 at the relevant gate rather than picking for him. **The Tier-1 working-lane milestone needs neither F nor G** (it's fan-out + baselines + readiness) — so F/G slippage must not block the operator's natural review point, and the §6.1 "build F/G/H now vs defer" decision is taken with a *working* lane already in front of him.

---

## 5. Dispatch + review policy (your defaults — adjust with the operator)

**Model tiering** (set per `agent()`/Agent `model`):
- **Opus (`claude-opus-4-8`)** — design/risk PRs and all final reviews: the fan-out contract, **E-PR2 seed channel**, F registry schema, G assembler design; every thermonuclear review.
- **Sonnet (`claude-sonnet-4-6`)** — mechanical PRs: readiness-fix bundle, E-PR3, F CLI, G wiring, the 4 candidate-strategy clones, config generation.
- **Haiku (`claude-haiku-4-5`)** — trivial only (bulk config templating, doc edits); sparingly.

**Every implementer** is dispatched with the explicit directive: *do the simplest thing that satisfies the spec and tests; mark deliberate simplifications with `ponytail:` comments; surgical changes only — no adjacent refactors.*

**Review policy:**
- **Per PR:** `/ponytail-review` (operator's explicit ask) + full suite green (`python -m pytest`; a clean run is `… 1 skipped …`, never `1 failed`).
- **Near sacred seams** (loader, `StrategyContext`, event-store migration): `risk-invariant-reviewer` agent in addition.
- **Thermonuclear review** (heavy, multi-finding adversarial pass — see the operator's prior `THERMO_NUCLEAR` precedent) at **two gates**: end of Tier 0+1 (the working-lane milestone) and at final completion before "full stop."
- **Per tier:** a spec-level adversarial review *before* implementing (the lean-slice session's proven pattern: spec → adversarial review → fold → TDD per PR → ponytail-review).
- **Two correctness exceptions to "ponytail-review is enough"** (ponytail hunts over-engineering, not correctness bugs): (a) the **readiness-fix bundle** — write the dup / off-grid / >100% tests FIRST and give it an **Opus** final-review despite its small size; a wrong coverage fix (capping at 100 instead of counting distinct on-grid offsets) silently re-opens the evidence blind spot. (b) the **per-symbol config-gen** — add a smoke test that every generated config `load_strategy_config`s and resolves to exactly one eligible symbol; ponytail won't catch a malformed fan-out.

**Commit/PR cadence:** work on a dedicated worktree branch (`superpowers:using-git-worktrees`); per-PR commits; **never merged to master** — the operator merges after his own review. Watch the worktree-PYTHONPATH-shadow gotcha (the `.venv` editable install points at master's `src`; set `PYTHONPATH` to the worktree `src` when running tests — see the lean-slice build doc's "How to review").

---

## 6. Open decisions the operator still owns (surface, don't decide)

1. **IEX-non-durability for F/G/H.** He chose "the entire thing," so build it — but confirm at the Tier-1 gate that he still wants F/G/H *now* vs. deferring until a SIP/consolidated provider exists, given the verdicts are non-durable on IEX regardless.
2. **Trim vs keep+calibrate the IEX gate** (§2 optional).
3. **Exact registry fields (F) and evidence-report fields (G)** — design choices; propose, get sign-off.
4. **Fan-out is locked to per-symbol (decision #2)** — confirm still good before building Tier 0.
5. **E-PR2 seed mechanism** — config-parameter seed (no `StrategyContext` change) vs. a frozen-context field. Default to the config-parameter route unless a candidate genuinely cannot read a per-symbol seed from `parameters`.

---

## 7. Hard guardrails

- **Never merge to master autonomously.** The operator reviews and merges.
- **Risk layer sacred.** F adds a *new* table; it must not alter promotion gates or risk enforcement. Nothing in this phase weakens risk/execution/promotion.
- **E-PR2 is highest-risk** — **prefer the config-parameter seed** (no frozen-contract change). If it must touch `StrategyContext`: `test_base_reasoning.py` guards `DecisionReasoning`, **NOT** `StrategyContext` — grep `StrategyContext(` (`loader.py:102` + runner + fixtures) for the real break surface. risk-reviewer + thermonuclear mandatory; per-(symbol,session-date) determinism.
- **Surgical + ponytail throughout.** Every changed line traces to the spec; mark simplifications.
- **Migration 015** must be head; **do NOT bump `MIN_COMPATIBLE`** (additive table, stays 12 per `event_store.py:353-361`); bump the 7 `schema_version == 14` asserts → 15 atomically within the PR (leave `test_concurrency.py`'s `>= MIN_COMPATIBLE` asserts untouched).
- **If F edits `SRS.md`:** cite cross-references by ADR, never by `R-XXX-NNN` inside another requirement's prose — the coverage generator (`scripts/audit_requirements_coverage.py`) parses R-codes positionally and mis-attributes. Regenerate `docs/REQUIREMENTS_COVERAGE.md` and diff-check after any SRS edit.

---

## 8. Reference artifacts (read; do not duplicate)

- `docs/reviews/2026-06-19-intraday-etf-evidence-hardening.md` — the plan (workstreams A–H; lean slice was C-light+D+E-PR1).
- `docs/reviews/2026-06-18-intraday-etf-evidence-hardening-feedback.md` — first code-grounded review (what exists vs new; the IEX contradiction).
- `docs/reviews/2026-06-19-intraday-etf-evidence-lean-slice-build.md` — what shipped last night + honest self-flags.
- `docs/superpowers/plans/2026-06-19-intraday-etf-evidence-lean-slice.md` — the lean-slice plan + spec-review fold.
- `docs/GRILL_DECISIONS_2026-06-18.md` — founder direction.
- `docs/adr/0017-data-source-hierarchy.md` (IEX fallback; SIP/Massive deferred) · `docs/adr/0016-phase1-instrument-whitelist.md` · `docs/SRS.md:297` (R-PRM-011) · `docs/PROMOTION_GOVERNANCE.md:137-149` (Experiment Registry spec).
- `~/.claude/playbooks/pm-mode-dispatch.md` · `~/.claude/playbooks/reviewer-dispatch.md`.

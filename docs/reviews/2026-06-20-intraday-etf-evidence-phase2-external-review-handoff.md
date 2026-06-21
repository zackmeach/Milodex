# External Adversarial Review Handoff — Intraday ETF Evidence, Phase 2

**You are an independent reviewer. Trust nothing in this document or in the code's self-description. Re-derive every claim from source.** Your job is to find what's wrong, weak, unsound, or overstated across an entire multi-tier feature branch — design, correctness, governance, statistics, tests, and scope — and to challenge the decisions that produced it. A review that confirms "looks good" without having tried hard to break it is a failed review.

Milodex is a personal autonomous trading system (Python, src-layout, SQLite event store). **The risk layer is sacred** — strategy proposes, risk disposes; nothing may weaken or bypass it. This phase is research/backtesting tooling; it must not touch live-capital paths.

---

## 0. Setup (Windows machine — read carefully)

- **Worktree (all code is here):** `C:\Users\zdm80\Milodex\.worktrees\intraday-etf-evidence-phase2`
- **Branch:** `intraday-etf-evidence-phase2`, off master `b5962e0`. **The branch is NOT merged.** Review it as a merge candidate.
- **The full phase diff:** `git -C "C:/Users/zdm80/Milodex/.worktrees/intraday-etf-evidence-phase2" diff b5962e0 --stat` (170 files, +14,659/−96, 38 commits). Use `git -C <worktree> log --oneline b5962e0..HEAD` for the commit arc.
- **`git -C` is mandatory** — the shell's cwd is ambiguous on this box; always pass `git -C "<worktree>"`.
- **PYTHONPATH-shadow trap (critical):** the `.venv` editable install points at MASTER's `src`, so running tests naively tests master, not this branch. To test the branch you MUST set PYTHONPATH:
  ```powershell
  $env:PYTHONPATH = "C:\Users\zdm80\Milodex\.worktrees\intraday-etf-evidence-phase2\src"
  cd "C:\Users\zdm80\Milodex\.worktrees\intraday-etf-evidence-phase2"
  & "C:\Users\zdm80\Milodex\.venv\Scripts\python.exe" -m pytest -q
  ```
  venv python: `C:\Users\zdm80\Milodex\.venv\Scripts\python.exe`.
- **A clean full-suite run ends `... 1 skipped ...`** (a quarantined GUI test, `docs/KNOWN_FLAKY_TESTS.md`), NOT `1 failed`. Treat an actual `1 failed` as a real regression. Expected baseline: ~`3176 passed, 1 skipped, 4 xfailed`.
- **DB-contention trap:** if a backtest/screen is writing `data/milodex.db` concurrently, the autouse `_guard_real_event_store_untouched` fixture (`tests/conftest.py`) throws transient ERRORs on unrelated tests. Don't run a screen while running the suite; re-run an affected subset in isolation to confirm green.
- **Lint:** `& <venv-python> -m ruff check src/ tests/` (rules E,F,I,N,W,UP; line length 100).

**Reference docs to read for INTENT (then challenge the implementation against them — do not assume the code matches):**
- `docs/INTRADAY_ETF_EVIDENCE_PHASE2_ORCHESTRATION_BRIEF.md` (the plan / brief — in the *master* working tree, `C:\Users\zdm80\Milodex\docs\`)
- `docs/INTRADAY_ETF_EVIDENCE_PHASE2_COMPLETE.md` (the builder's completion summary — the thing you're auditing)
- `docs/INTRADAY_ETF_EVIDENCE_PHASE2_TIER1_GATE.md` (the Tier-1 gate report + resolution log)
- `docs/adr/0017-data-source-hierarchy.md` (IEX non-durability), `docs/adr/0016-phase1-instrument-whitelist.md`, `docs/PROMOTION_GOVERNANCE.md` "Experiment Registry", `docs/SRS.md` R-PRM-011.

---

## 1. What the phase claims to deliver (the claims you must verify or refute)

A cross-ETF intraday evidence lane: freeze a 17-ETF universe (`universe.liquid_etf_core.v1`) → warm a 5Min cache → produce a readiness report → run a candidate strategy + 3 null baselines **per-symbol across all 17** → assemble an evidence report with candidate-vs-baseline deltas → write one append-only experiment-registry entry, every verdict labeled **IEX-exploratory / non-durable** (ADR 0017).

It was demonstrated on the `meanrev.rsi2.intraday` candidate, which it adjudicated **`rejected`** (decisive loss) and registered. **Independently decide whether that verdict and the machinery producing it are sound.**

Tiers: **0** fan-out/readiness/cache; **1** the random-matched null + the working-lane run + a finalization pass (N1–N4, an engine force-flatten, a warmup fix); **2 (F)** the experiment registry (migration + store + CLI); **3 (G)** the evidence assembler + CLI; **4 (H)** 3 new candidate strategies.

---

## 2. Review dimensions — with adversarial probes and file anchors

For EACH finding: open the cited file, read the surrounding code AND its tests, and assign a severity (BLOCKER / MAJOR / MINOR / NIT) with a quoted line as evidence. **Spend at least half your effort trying to break the design, not just spot bugs.**

### A. Risk / sacred-seam invariants (highest priority)
The phase must NOT weaken risk enforcement, promotion gates, the kill switch, or how trades reach the broker.
- **N2 engine session-end force-flatten** — `BacktestSimulationKernel.liquidate_open_positions` (`src/milodex/backtesting/simulation_kernel.py`, ~line 478) called from `_simulate_intraday` (`src/milodex/backtesting/engine.py`, ~1322). Probe: does the flatten route through the SAME risk evaluator (`submit_backtest`) as a normal SELL, or reach the broker directly? Accounting parity with `drain_pending_orders` (cash/position/round-trip)? Does it ever fire on a daily backtest (it must not)? Under `RiskPolicy.ENFORCE`, what happens if risk vetoes the liquidation — silent re-strand, crash, or fail-safe? Is the equity-continuity claim honest (it fills with slippage; MTM uses raw close)?
- **Migration 015** (`src/milodex/core/migrations/015_experiment_registry.sql`) + the `EventStore` changes (`src/milodex/core/event_store.py`). Probe: strictly additive (no ALTER/DROP/UPDATE/DELETE of existing tables)? Is `MIN_COMPATIBLE_SCHEMA_VERSION` correctly UNCHANGED at 12 while head → 15? Were ALL `schema_version==14` asserts bumped and the `>= MIN_COMPATIBLE` floor asserts left alone?
- **The perf change:** `EventStore.batched()` + the `bufferable=True` flag on `record_no_action` (`src/milodex/execution/service.py`). Probe: can buffering drop/reorder a risk-relevant write, weaken the dual-ancestor rule, or lose audit rows on a mid-sim exception?
- **Grep the entire diff** for any change under `src/milodex/risk/`, `promotion/`, `broker/`, or kill-switch/liveness code. The claim is NONE (or behavior-neutral). Verify.

### B. Statistical null correctness (the floor the candidate is judged against)
- `src/milodex/strategies/bench_random_matched_exposure_long.py`. Probe: the N1 boundary clamp (`rng.integers(OR, OR + EW - _BAR_MINUTES + 1)`, ~line 163, `_BAR_MINUTES` ~70) — is the max drawable target EXACTLY the last firable on-grid in-window bar? Off-by-one? Determinism per-(symbol, session_date, seed) with NO global seed? Does the one-entry-per-session re-scan survive the T+1 fill gap (positions flat across the signal bar)?
- **The matched-exposure thesis itself:** the null matches the candidate's trade COUNT (a measured `session_entry_rate`), randomized entry, held-to-close (no stop, no signal exit). The builder dropped "hold-duration matching" on operator instruction. Probe: is a count-matched-but-not-hold-matched random null a *fair* floor? The thermonuclear noted `session_entry_rate ≈ 0.97` for rsi2 (near-daily) makes the count match nearly non-discriminating — does that undermine the verdict, or only the *exposure* axis (leaving return/Sharpe separation valid)? Is the whole "random-matched as a floor" methodology sound, or is there a better null?
- The warmup fix: `_warmup_calendar_days` (`engine.py`, ~1467) caps the heuristic at 3650 and the null declares `max_lookback_periods()=78`. Probe: does the cap silently truncate a legitimate strategy's warmup? Does 78 actually prevent the `seed` param being read as a lookback?
- `research/candidate_rates.py`: `rate = oos_round_trip_count / oos_trading_days` — correct numerator (round-trips, not 2× fills)?

### C. Evidence assembler correctness (`src/milodex/research/evidence_assembler.py`)
- **Delta math:** `delta = candidate − baseline` only when both non-None; `round_trips = trade_count // 2` (trade_count is 2× round-trips); never coerce a missing metric to 0.0; both `error is not None` AND `oos_sharpe is None` treated as missing. Verify each.
- **The decisive-loss predicate** (`_decisive_loss_predicate`, ~547): fires when candidate Sharpe below ALL THREE nulls on ≥14 symbols AND the *tightest* qualifying margin ≥2.0. Probe: is "below all" (`candidate < min(nulls)`) correct? Is `min(margins)` the right aggregation (requires EVERY qualifying symbol ≥2.0)? Behavior when <14 symbols have all four present? Off-by-one on the 14? Are these constants (14, 2.0) defensible, or arbitrary?
- **terminal_status** (`_derive_terminal_status`, ~591): failed / rejected / inconclusive (decisive WIN → inconclusive). **The writer invariant** (`_write_registry_row`, ~624; the guard ~652) refuses `rejected` unless `durable is False AND iex_exploratory is True AND feed == "iex"`. Probe: can a poisoned report (`dataclasses.replace`) still write a non-durable-violating row? Is the invariant complete?
- `coverage_pct` must be advisory and NEVER filter a symbol out of the deltas. `resolve_universe_ref` (not bare `load_strategy_config`); lowercase candidate ids vs uppercase resolved symbols (a case mismatch silently drops every cell — verify it can't happen).
- **Design challenge:** the assembler CONSUMES a prior `BatchResult` rather than re-running. Is that the right call (determinism/speed) or a correctness risk (the screen and the report could diverge)? Is emitting a sibling `IntradayEvidenceReport` instead of extending `EvidencePackage` justified?

### D. Governance policy soundness (challenge the core decision)
The operator-decided, second-opinion-adjudicated policy: an IEX verdict can be `rejected` on a decisive loss (not just `inconclusive`), because ADR-0017's non-durability contract protects the *promotion log*, not the *research registry* — with `feed`/`durable`/`revisitable` markers enforcing non-durability structurally. **Attack this.** Is "rejected on IEX, revisitable" honest research memory, or does it risk a future consumer keying on `terminal_status='rejected'` (an indexed column) and treating an IEX verdict as durable — blocking a SIP re-test? The markers are in `evidence_json` (queryable only via `json_extract`), not a column — is that structural enough? Is the decisive-WIN→inconclusive asymmetry right? Read `docs/adr/0017-data-source-hierarchy.md` and `docs/PROMOTION_GOVERNANCE.md` and decide if the policy honors them.
- Verify the registry is genuinely append-only / no-delete (R-PRM-011): no `DELETE` / in-place `UPDATE` path for `experiment_registry` anywhere. Nothing in this phase may write the promotion_log or alter a promotion gate.

### E. The 3 new strategies (likeliest place for a subtle bug)
`src/milodex/strategies/gap_continuation_intraday.py`, `momentum_late_session_intraday.py`, `breakout_opening_range_retest_intraday.py` + their tests.
- **One-entry-per-session:** the engine fills BUY at the NEXT bar's open, so `positions[symbol]` is 0.0 across the signal bar. Each needs an `_already_entered_this_session` prior-bar re-scan keyed on the *persistent* trigger. Construct the two-qualifying-bars case for each and verify the second yields no re-entry. A weak re-scan = repeated BUYs = corrupt round-trip counts.
- **gap_continuation:** reads the PRIOR session's close; `max_lookback_periods()` must be **156** (not 78 — 78 leaves `prior_close=None` → silently never enters). What if `df` has only one session? Is the gap-up-continuation direction the only long-only-expressible choice, and is it justified?
- **late-session** (`opening_range_minutes=300` → window [14:30,15:30)) and **retest** (3-phase breakout→retest→reclaim): index safety with few bars; can the retest false-fire on a single bar?
- All: long-only, half-day skip, exits in priority order (stop → invalidation → time-stop), reuse `_session_intraday` (no reinvented session logic), `DecisionReasoning` uses only existing fields (adding one breaks `tests/milodex/strategies/test_base_reasoning.py`). **Also challenge: should these 3 have been built at all, given every verdict is non-durable on IEX? Is one of the 3 still too close to an existing strategy?**

### F. Test quality (are the tests load-bearing or decorative?)
For the NEW test files (`test_experiment_registry.py`, `test_evidence_assembler.py`, `test_research_evidence_command.py`, `test_experiment_cli.py`, the 3 strategy tests, the N1/N2/N4 additions): would each test actually FAIL if the behavior regressed? Probe specifically: would the decisive-loss-predicate tests fail if `>=14` became `>14` or `2.0` became `1.5`? Would the one-entry re-scan tests fail if the re-scan were deleted? Would the N2 flatten test fail if the flatten were removed? Would the no-delete test fail if a `DELETE` were added? Flag tautological assertions (asserting a value the test itself set) and missing key assertions.

### G. Scope / surgical discipline
Every changed line should trace to: fan-out, the null, the perf fix, the N2 flatten, the registry (F), the assembler (G), or the 3 strategies. Flag drive-by refactors, reformatting of untouched code, deleted unrelated code, any committed scratch/large-artifact files, and any `TODO`/`FIXME`/`ponytail:` comment hiding an unfinished or known-broken path.

### H. The evidence + the lane's conclusion (does the science hold?)
- Read `docs/reviews/working_lane_evidence_phase2.md` (the 68-cell result; the `.json` sibling is gitignored scratch). The rsi2 candidate ranks worst on all 17 (Sharpe −5.6 to −10.6, below every null). Is the walk-forward methodology sound (window spans, OOS aggregation, min-trades)? Are the baselines a legitimate floor? Is "the candidate is worse than random everywhere" a valid conclusion, or an artifact (slippage model, IEX under-sampling, the matched-null saturation)? Is anything about the GLD nulls "passing" the statistical gate (gold's drift) handled honestly?
- **The standing caveat to verify is load-bearing:** is the IEX-exploratory/non-durable label actually present and unmissable on every verdict path, or can a code path emit a verdict without it?

---

## 3. What I (the builder) already claim was reviewed — RE-DERIVE, don't trust

- **N2 force-flatten** and **F-PR1 migration/store** each passed a `risk-invariant-reviewer` agent ("SAFE"). **Re-verify independently** — do not inherit that verdict.
- A **thermonuclear review** (7 finders → 10 findings → 8 verified MINOR/NIT, 2 overstated→NIT, 0 dismissed) declared the branch "merge-ready, zero BLOCKER/MAJOR." **Independently re-run that judgment.** Specifically re-examine the 2 findings it de-escalated (the `candidate_spy_id` `.v1` coupling at `evidence_assembler.py` ~205; the "tautological" gap/breakout entry tests) — was the de-escalation correct, or did it talk itself out of a real issue? And re-check the MINOR findings it folded into commit `0e0e623` (writer-invariant marker coherence, `--feed-label` removal, predicate boundary tests).
- **3 documented "safe" deferred follow-ups** (in `PHASE2_COMPLETE.md`): `durable` as a queryable column; `baseline_ref` substring-replace in `research/fanout.py` (~123); a CLI-test name. Decide if any is actually more than a follow-up.

## 4. Decisions to challenge directly (not just the code — the judgment)

1. **"Fix C is a no-op"** — the builder profiled and concluded the remaining backtest cost is CPU-bound pandas tz-conversion, not batchable DB fsync, so it skipped a planned perf fix. Re-profile or re-reason: correct, or a missed optimization?
2. **The terminal_status policy** (rejected-on-decisive-loss vs inconclusive-only) — D above.
3. **Dropping the planned 4th H strategy** ("price-extension" as redundant with `vwap_trend`) — sound de-duplication or scope-shirking?
4. **Building H at all** given non-durable verdicts — value vs effort.
5. **The consume-vs-rerun** assembler choice (C).
6. **The matched-exposure null methodology** (B) — is this the right way to build a floor?

---

## 5. Deliverable

Produce a blunt report:
1. **One-line verdict:** merge-ready / merge-ready-with-fixes / has-blockers.
2. **Findings table:** severity (BLOCKER/MAJOR first), file:line, the defect with a quoted line, and the minimal fix. Separate *confirmed* from *adjudicated-down*.
3. **Decision critiques:** for each of §4, agree/disagree with reasoning.
4. **What you tried to break and couldn't** (the attack log — this is how I know the review was adversarial, not decorative).
5. **Bottom line:** what must change before merge vs. safe follow-up.

Be specific, quote lines, and don't invent issues to pad. If it's genuinely clean, say so — but only after a real attempt to break it. Note that "IEX-exploratory / non-durable" is the *designed* policy, not a defect — critique whether it's *enforced*, not that it exists.

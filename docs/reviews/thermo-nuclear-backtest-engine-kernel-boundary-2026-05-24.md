# Thermo-Nuclear Code Quality Review: Backtest Engine vs Simulation Kernel Boundary

**Date:** 2026-05-24  
**Scope:** `src/milodex/backtesting/engine.py`, `src/milodex/backtesting/simulation_kernel.py`, RM-013 (`b372ed5`), related tests  
**Reviewer posture:** Strict maintainability / structural simplification (thermo-nuclear bar)  
**Verdict:** **Partial win on drift prevention; not a win on conceptual surface area.** The kernel extraction was the right *direction* and materially de-risked daily/intraday divergence on fills and audit, but `BacktestEngine` remains an oversized orchestration monolith with parallel simulation loops and ~370 lines of intraday-only helpers still welded to the engine file.

---

## Executive summary

| Question | Answer |
|----------|--------|
| Did RM-013 reduce concepts, or move mechanics sideways? | **Both.** ~650 lines left `engine.py` and landed in a testable `BacktestSimulationKernel`, which *does* delete one class of duplication (pending drain, broker sync, skip audit, stranded orders, ADR 0053 snapshot). Net repo size barely shrank (~2,257 → ~2,215 lines across the two files). The *reader* still holds two large loops plus a 1,277-line class. |
| Is the boundary credible for promotion evidence? | **Behaviorally yes** (same `ExecutionService` path, golden tests, explicit T+1 semantics). **Structurally fragile** — parallel daily/intraday orchestration and dual cash/position bookkeeping make future fill-timing or audit changes easy to apply in one path only. |
| Thermo-nuclear approval bar | **Not met** for “decomposition complete.” Met for “RM-013 done criteria” (shared testable interface, golden parity). Treat further decomposition as **presumptive follow-up**, not optional polish. |

---

## Metrics (current `main`-era tree)

| Artifact | Lines | Notes |
|----------|------:|-------|
| `engine.py` total | **1,803** | Still a single module carrying orchestration, daily loop, intraday loop, and bar-slicing utilities |
| `BacktestEngine` class only | **1,277** | Crosses the 1k-line smell threshold by ~28%; not waived by cohesion |
| `_simulate_daily` | **212** | Day-outer loop, bar slicing, evaluate, pending queue |
| `_simulate_intraday` | **257** | Day-outer + event timeline + evaluate + pending with `RETAIN` policy |
| Module-level helpers in `engine.py` | **369** | Predominantly intraday timeline/cursor/mark-to-market (`_build_intraday_event_timeline`, `_advance_cursors`, …) |
| `simulation_kernel.py` | **615** | Kernel + `PendingOrder` types + `compute_equity` / `day_to_dt` |

**RM-013 commit (`b372ed5`):** `engine.py` **2,257 → 1,600** lines; new `simulation_kernel.py` **+615**; dedicated `test_simulation_kernel.py` **+254**. Net ~**42 lines** removed repository-wide — a relocation with modest deduplication, not a conceptual shrink.

---

## What RM-013 actually achieved (credit where due)

The roadmap problem statement for RM-013 was accurate: daily and intraday paths **had been duplicating** pending-order lifecycle, skipped-order audit, broker sync, fill accounting, entry-state side effects on fill, and snapshot policy.

The extraction **did** collapse that duplication into `BacktestSimulationKernel`:

- `drain_pending_orders` — sell-then-buy, slippage-aware skip reasons, `MissingOpenPolicy` for intraday carry-over vs daily skip
- `sync_broker_state` — account/position injection for `ExecutionService`
- `record_stranded_orders`, `record_no_action`, `record_final_snapshot`
- `round_trip_count` via `sym_fills`

Tests moved with the behavior (`tests/milodex/backtesting/test_simulation_kernel.py`), which satisfies RM-013 “shared behavior has its own testable interface” and protects promotion-critical fill semantics from silent daily/intraday drift.

**This is real value.** A strict reviewer should not call RM-013 “cosmetic.” It addresses the highest-risk duplication called out in `docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md` RM-013.

---

## What RM-013 did *not* achieve (the sideways move)

### 1. The engine is still the conceptual home for *two simulators*

`BacktestEngine` docstring claims the kernel delegation means daily and intraday “cannot drift on those rules.” True for **kernel-owned** rules only.

The engine still owns **two full orchestration programs**:

```876:1086:src/milodex/backtesting/engine.py
    def _simulate_daily(
        ...
    ) -> _SimulationOutput:
        ...
        for day in trading_days:
            ...
            # drain → slice → gate → evaluate → enqueue → equity_curve
```

```1088:1343:src/milodex/backtesting/engine.py
    def _simulate_intraday(
        ...
    ) -> _SimulationOutput:
        ...
        for day in trading_days:
            ...
            for ts, meta in timeline:
                # advance → evaluate → drain (spec Correction 8)
```

Shared *mechanics* moved out; shared *orchestration shape* did not. A reader must still internalize:

- Daily: one evaluate per day, drain at day open, `MissingOpenPolicy.SKIP`, pending cleared each drain
- Intraday: evaluate on cursor advance, drain on fill events, `MissingOpenPolicy.RETAIN`, `pending = drain.remaining`

That is **two mental models**, not one simulation with a tempo parameter. RM-013 reduced *copy-paste on fills*; it did not reduce *concepts*.

### 2. Substantial copy-paste remains at the orchestration layer

These blocks appear in both paths with only scheduling differences:

| Concern | Daily | Intraday |
|---------|-------|----------|
| Universe / empty `trading_days` guard | ✓ | ✓ |
| `held_days` bump on `kernel.entry_state` | per day | per day |
| Primary symbol must not be substituted (`_empty_barset`) | ✓ | ✓ |
| `_evaluate_strategy` + intent enqueue | ✓ | ✓ |
| `record_no_action` only when primary has bars | ✓ | ✓ |
| Stranded pending + final `sync_broker_state` + `record_final_snapshot` | ✓ | ✓ |

Roughly **~40–50 lines of policy comments and guards are duplicated** per path. That is exactly the sort of “weird if statements in random places” debt the thermo-nuclear skill warns about — except here it is **duplicated** weirdness, so the next universe-calendar or primary-symbol audit change requires two edits and two test surfaces.

### 3. Intraday mechanics stayed in `engine.py`, not beside the intraday loop

~369 lines of module-level helpers (`_build_intraday_event_timeline`, `_advance_cursors`, `_build_visible_bars`, `_mark_to_market_at_day_end`, …) sit **below** a 1,277-line class. They are not used by daily simulation.

This is classic **spaghetti growth by file adjacency**: the engine module reads as “everything backtest” rather than “orchestration + daily” vs “intraday timeline engine.”

### 4. Dead weight: `_latest_opens`

`_latest_opens` is defined at `engine.py:1543` and **has no callers** in the repository. Likely leftover from an earlier fill refactor toward `_opens_on_day`. Small, but signals the file is not under line-budget discipline.

### 5. Dual bookkeeping in the kernel (pre-existing, now centralised)

`BacktestSimulationKernel.drain_pending_orders` calls `execution_service.submit_backtest`, then **manually** mutates `self.cash`, `self.positions`, and `entry_state` from fill prices. The design preserves “same execution path” while keeping parallel simulator state — correct for backtest, but **high coupling**: any change to how `submit_backtest` / `SimulatedBroker` reports fills must be mirrored in kernel bookkeeping.

Centralising this in the kernel **helps** (one place to fix). It also **cements** the pattern as the canonical backtest fill path. A code-judo alternative long-term: derive post-fill portfolio state from broker/account snapshots only, so the kernel does not re-implement fill economics. Not a blocker for RM-013; a maintainability flag for the next touch.

### 6. Loose contracts at the kernel boundary

- `PendingOrder.reasoning: object` — obscures the real type (`DecisionReasoning` or similar); increases cast/guess surface in audit code.
- Engine reaches into `kernel.entry_state` for `held_days` increments — orchestration mutating kernel internals instead of `kernel.tick_held_days()` (or holding `held_days` in a session object the engine owns).

---

## Boundary assessment: who should own what?

| Responsibility | Current owner | Assessment |
|----------------|---------------|------------|
| Strategy load, prefetch, coverage, manifests, run lifecycle | `BacktestEngine` | Correct layer, but **overloaded** into the same class as simulation |
| Tempo dispatch (`1D` vs intraday) | `_simulate` | Fine as a one-liner dispatch |
| Bar visibility / timeline / cursors | `engine.py` module helpers | **Wrong file** — intraday domain, not generic engine |
| T+1 drain, skips, broker sync, snapshots | `BacktestSimulationKernel` | **Correct** — this is the RM-013 win |
| `strategy.evaluate` context assembly | `_evaluate_strategy` on engine | Correct — strategy layer belongs with loaded strategy |
| Equity curve sampling policy | Split: daily MTM inline; intraday `_mark_to_market_at_day_end` | Acceptable product contract (ADR 0053 daily keys), but **two implementations** of “EOD equity” |

The boundary is **honest in documentation** (module docstring L22–27, class docstring L160–167) but **misleading in file structure**: a newcomer will still open `engine.py` and face ~1,800 lines.

---

## Code-judo opportunities (ordered by leverage)

### A. Extract `intraday_simulation.py` (or `backtesting/intraday/`)

Move `_simulate_intraday` and all timeline/cursor helpers (~520 lines) out of `engine.py`. **Deletes ~30% of engine file bulk** without behavior change. Makes the daily path scannable.

**Preserves:** single `BacktestEngine` public API (re-export or thin delegate), per intraday design spec non-goal of “no separate engine class.”

### B. Introduce a shared `SimulationDecisionStep` (name negotiable)

One function used by both paths:

```text
inputs: primary_bars, bars_by_symbol, kernel, session_id, db_run_id, universe
outputs: list[PendingOrder | IntradayPendingOrder] newly queued
behavior: sync → evaluate → no_action OR enqueue (primary-present guard)
```

**Deletes** the duplicated evaluate/no_action/enqueue block and encodes the “never substitute universe[0]” invariant once. This is the highest-value judo move for **promotion credibility** — that invariant is documented extensively in comments because it already corrupted regime analytics once.

### C. `BacktestSimulationSession` dataclass

Hold: `kernel`, `pending`, counters (`buy_count`, …), `session_id`, `db_run_id`. Methods: `bump_held_days()`, `drain_at_opens(...)`, `finalize_window(trading_days, ...)`.

Collapses identical post-loop stranded/snapshot/final-equity blocks in daily and intraday.

### D. Split `BacktestEngine` into orchestration vs simulation driver

`BacktestRunCoordinator` (prefetch, manifests, event store, `run()`, walk-forward hooks) + `BacktestSimulator` (only `_simulate_*` and helpers). **Not** two public engines — internal modules.

Targets the 1,277-line class smell without changing CLI/GUI contracts.

### E. Portfolio state from broker after fill (longer arc)

Reduce manual `cash`/`positions` mutation in `drain_pending_orders` if `SimulatedBroker` can become the single source of truth post-`submit_backtest`. Shrinks kernel line count and audit divergence risk.

---

## Promotion and daily/intraday gotcha lens

Milodex explicitly treats backtest credibility as promotion evidence (`CLAUDE.md` gotchas: daily-only engine history, intraday silent zero trades, T+1 fill model).

**Strengths after RM-013:**

- Fill timing and skip audit are **single-sourced** in the kernel — the right seam for golden tests.
- `simulate_window` + `timeframe_from_bar_size` dispatch means walk-forward and full runs share the same path selection as `_execute`.
- Intraday ordering (`advance → evaluate → drain`) is documented in-code with spec reference — good for forensic review.

**Residual risks:**

1. **Orchestration drift** — a fix applied only in `_simulate_daily` (e.g. universe gate, primary absent handling) may not reach intraday until a separate bug report. Comments are thorough; comments are not compilers.
2. **File intimidation** — reviewers and agents may avoid reading the full engine, missing path-specific bugs. Works against “measure twice” for promotion sign-off.
3. **Stale spec anchors** — `docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md` still cites pre-intraday line numbers (`engine.py:341`, `:682`). Documentation drift does not break tests but **erodes trust** in written promotion evidence.

---

## Thermo-nuclear findings (prioritized)

### 1. Structural — Presumptive blocker for “we’re done decomposing backtest”

`BacktestEngine` at **1,277 lines** and `engine.py` at **1,803 lines** violate the 1k default unless decomposed. RM-013 improved *internal* cohesion of fill mechanics but **increased module count without shrinking the reader’s conceptual load**. Next work should be filed as engineering debt with the same priority tier as intraday correctness, not backlog grooming.

### 2. Missed code-judo — Duplicated decision cycle

The primary-symbol guard + `_evaluate_strategy` + no-action vs pending enqueue is the same business rule in two 250-line loops. **Reframe** before adding another special case (e.g. half-day bars, extended hours).

### 3. Spaghetti growth — Intraday helpers in engine module tail

369 lines of intraday-only functions attached to a daily+orchestration class file is the wrong ownership boundary. **Move** with the intraday loop.

### 4. Boundary — Engine mutates `kernel.entry_state` directly

Encapsulate held-days (and eventually entry metadata) behind kernel or session API so orchestration does not depend on dict shape.

### 5. Type contract — `reasoning: object`

Narrow to the strategy decision reasoning type shared with `ExecutionService.record_no_action`.

### 6. Maintainability — Dual post-fill bookkeeping

Document as intentional invariant in kernel module docstring; add a single test asserting kernel cash/positions match `sim_broker` account after drain. Without that, refactors will regress silently.

### 7. Legibility — Dead `_latest_opens`

Delete or wire up; trivial hygiene.

---

## Test posture (brief)

| Layer | Coverage | Note |
|-------|----------|------|
| Kernel unit tests | `test_simulation_kernel.py` | Appropriately targets drain/skip/sync |
| Engine golden / intraday | `test_engine_intraday.py`, daily regressions | Still engine-integrated — correct for end-to-end promotion evidence |
| Perf equivalence | `test_engine_perf_equivalence.py` | Guards against accidental loop regressions |

**Gap:** No test that daily and intraday **share** identical no-action / primary-absent policy via a single helper (because the helper does not exist yet). Adding the shared helper would allow one contract test instead of two comment walls.

---

## Recommended next actions

| Priority | Action | Expected effect |
|----------|--------|-----------------|
| P1 | Extract intraday loop + helpers to `intraday_simulation.py` | `engine.py` under ~1,300 total; class under ~900 if combined with D |
| P1 | Shared `SimulationDecisionStep` for evaluate/enqueue/no-action | Removes highest-risk duplicated policy |
| P2 | `BacktestSimulationSession` for pending/counters/teardown | Single finalize path |
| P2 | `held_days` + `reasoning` typing cleanup on kernel API | Clearer boundary |
| P3 | Broker-aligned portfolio state after fill | Shrinks kernel complexity long-term |
| P3 | Refresh intraday spec line anchors | Doc trust for reviewers |

---

## Approval bar (thermo-nuclear)

| Criterion | Status |
|-----------|--------|
| No clear structural regression from RM-013 | **Pass** — regression would have been *more* duplication without RM-013 |
| No obvious missed dramatic simplification | **Fail** — shared decision step + file split are visible, low-risk judo moves |
| No unjustified file-size explosion | **Fail** — class still 1,277 lines; file 1,803 |
| No spaghetti special-case growth | **Warn** — duplicated guards across paths |
| Kernel abstraction earns its keep | **Pass** — not a thin wrapper; owns real mechanics |
| Logic in canonical layer | **Mixed** — kernel good; intraday timeline still wrong file |
| RM-013 roadmap done criteria | **Pass** — interface + golden parity |

**Bottom line:** RM-013 was a **necessary** extraction that protects fill/audit credibility. It was **not sufficient** to claim the backtest engine boundary is “clean.” Treat `engine.py` as still in **active decomposition**: the next PR should delete orchestration duplication and evict intraday machinery from the engine file, not add features to `_simulate_intraday` in place.

---

## References

- `docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md` — RM-005, RM-013
- `docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md` — intraday event-loop semantics
- `docs/reviews/backtest-rejection-analysis.md` — T+1 fill rationale
- Commit `b372ed5` — `refactor: extract backtest simulation kernel helpers`

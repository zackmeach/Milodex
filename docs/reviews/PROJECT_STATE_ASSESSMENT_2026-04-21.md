# Project State Assessment — 2026-04-21

**Reviewer:** AI pair (internal review)
**Scope:** Full repo snapshot — documentation layer, source tree, configs, tests.
**Audience:** Founder. No sugar, no cheerleading.

---

## Executive Summary

Milodex is **docs-heavy but no longer docs-only**. Phase 1.0 and 1.1 are genuinely complete: broker, data, execution, risk evaluation, kill-switch, SQLite event store, strategy base/loader, and the regime SPY/SHY strategy all exist as working, tested code. Phase 1.2 is **partially landed** — the runner and its SIGINT dual-stop dialog are written and unit-tested, but the `meanrev` strategy is still config-only, and the promised Phase 1.2 integration evidence (SC-3, SC-4) has not yet been produced against a real Alpaca paper account. Phase 1.3 (backtesting + analytics) and 1.4 (promotion pipeline, frozen manifests, JSON CLI) are effectively unstarted — `src/milodex/backtesting/` and `src/milodex/analytics/` are docstring-only packages.

The documentation layer is the strongest part of the project. It is internally coherent, normatively written, and ADR-backed (0001–0017). There are **small but real drift points** between the spec and reality (directory layout, the `risk/` module, the `quantity` semantics in the regime strategy) that should be fixed before Phase 1.3 begins, not after. Test coverage is respectable for what exists but has a specific and uncomfortable gap at the risk layer: 14 tests exercise `RiskEvaluator` indirectly through `ExecutionService`, yet several individual risk rules (daily-loss, single-position, total-exposure, concurrent-positions, order-value) have no direct assertion coverage. Given the "risk layer is sacred" rule, this gap is the most important one to close.

Bottom line: the project is closer to the roadmap's self-reported **55–65% of Phase 1** than to a vanity number in either direction. The honest blockers to finishing Phase 1.2 are small and specific. The blockers to starting 1.3 are real and mostly about building new code, not cleaning up old code.

---

## Documentation Maturity

### Strengths

- The doc set is **internally coherent**. `VISION.md`, `FOUNDER_INTENT.md`, `SRS.md`, `ROADMAP_PHASE1.md`, and the domain companions (`RISK_POLICY.md`, `OPERATIONS.md`, `REPORTING.md`, `CLI_UX.md`, `PROMOTION_GOVERNANCE.md`, `ENGINEERING_STANDARDS.md`, `DISTRIBUTION.md`, `strategy-families.md`) read like one project, not a pile of one-off notes. The authority ranking in `docs/adr/README.md` and the explicit "companion to SRS" framing of each domain doc is working — each doc knows its role.
- **`FOUNDER_INTENT.md` is doing real work.** It shows up by reference in every companion doc, and the tone of the specs (honesty-over-certainty, "feel alive", "trust from evidence not hype") clearly traces back to it.
- **ADRs 0001–0017 exist** and are indexed. The presence of ADRs for config fingerprinting (0015), SQLite event store (0011), and the dual-stop dialog (0012) shows the important decisions have been written down before being implemented — which is the right order.
- **Normative vs. descriptive is kept straight.** `strategy-families.md` explicitly refuses to let YAML redefine semantics. `SRS.md` carries requirement codes that the roadmap references. `ENGINEERING_STANDARDS.md` names the rule about "scaffolded vs implemented" distinction that the docstring-only packages are relying on.

### Gaps and Drift

- **`SRS.md` R-XC-006 vs. reality — directory layout.** The SRS specifies a `state/` directory (`state/milodex.db`, `state/kill_switch.json`, `state/strategies/`, `state/locks/`). The actual implementation uses `data/milodex.db` for the event store and a legacy `logs/kill_switch_state.json` (migrated into the event store on first access). This is a live inconsistency — the SRS is wrong, or the code is wrong. Given the event store already subsumes kill-switch state and strategy run state, the code is defensible, but the SRS needs to be updated or an ADR written.
- **`CLAUDE.md` / `AGENTS.md` claim "seven modules" but `src/milodex/risk/` is a docstring.** The risk layer physically lives in `src/milodex/execution/risk.py`. `ENGINEERING_STANDARDS.md` acknowledges this ("risk enforcement lives inside `src/milodex/execution/` for Phase 1 by design"), but neither `CLAUDE.md` nor `AGENTS.md` says so. A new contributor (or future AI assistant) will look for `src/milodex/risk/` first and bounce off.
- **`R-CLI-009` (JSON output on every command) is unstarted** and this is not flagged as a hard-to-land item in the roadmap, even though `CLI_UX.md` and `REPORTING.md` both treat the JSON contract as foundational. The Phase 1.3 milestone will hit this the moment it becomes real.
- **`docs/reviews/2026-04-14-ui-state-review.md` is now stale** in specific ways: it says "most core modules are docstring-only" and "CLI is stubbed" — both were true a week ago but have been superseded by the Phase 1.1/1.2 work landed since. Leaving stale reviews in place is fine (they document history), but a one-line "superseded by" header on that file would prevent someone citing it as the current state.
- **`docs/REPORTING.md` and `docs/CLI_UX.md` specify commands that do not exist** (`report`, `reconcile`, `preview` in its trust-report sense, `promote`/`demote`, `run daily`). This is not drift per se — the roadmap agrees these are future — but someone reading `CLI_UX.md` alone would believe they exist today. A short "Status: Phase 1.3/1.4" marker at the top of the sections that describe unimplemented surfaces would match the posture taken in `PROMOTION_GOVERNANCE.md` ("future, Phase 2+").
- **No doc discusses what actually runs today end-to-end.** The roadmap lists what exists at a module level; `CLI_UX.md` describes the future ideal. There is no "as of today, here is what `milodex` will do if you type commands at a shell" doc. The README is close but predates the runner.

### Overall Documentation Verdict

Mature for a personal project. Unusually so, in fact — the doc layer is the artifact that best demonstrates the founder-intent goal of "credible, not just a polished shell". The drift points above are small-to-medium, and all are fixable in under a day of editing. None of them block Phase 1.2 work.

---

## Codebase vs. Documentation Alignment

Where the two agree, and where they don't.

### Aligned

| Area | Doc says | Code does |
|---|---|---|
| Broker interface (Alpaca) | `broker/` module, typed client, model layer | `src/milodex/broker/` with `client.py`, `alpaca_client.py`, `models.py`, `exceptions.py`; tested |
| Market data + Parquet cache | `data/` module with provider + cache | `src/milodex/data/` with `provider.py`, `alpaca_provider.py`, `cache.py`, `models.py`; tested |
| Execution service | preview + paper submit, gated by risk | `src/milodex/execution/service.py`; tested |
| Risk evaluator | 11 checks per `RISK_POLICY.md` | `src/milodex/execution/risk.py` has exactly 11 `_check_*` methods matching the policy |
| Kill switch + manual reset | event-store-backed, no auto-reset | `src/milodex/execution/state.py::KillSwitchStateStore` backed by `EventStore` with legacy JSON migration |
| SQLite event store | ADR 0011; tables for explanations, trades, kill-switch events, strategy runs; forward migrations | `src/milodex/core/event_store.py` implements all of this |
| Strategy base class + loader + config hash | ADR 0015 foreshadowing; hash every explanation record | `src/milodex/strategies/base.py`, `loader.py`; SHA-256 canonicalized config hash present |
| Regime strategy | `strategy-families.md` normative definition | `src/milodex/strategies/regime_spy_shy_200dma.py`; golden-output test present |
| CLI scope | `status`, `positions`, `orders`, `data bars`, `config validate`, `trade preview/submit/cancel/order-status`, `trade kill-switch`, `strategy run` | Matches `src/milodex/cli/main.py` argparse surface |
| Configs | strategy + risk YAMLs + universe | `configs/spy_shy_200dma_v1.yaml`, `meanrev_daily_rsi2pullback_v1.yaml`, `universe_phase1_v1.yaml`, `risk_defaults.yaml` |

### Not Aligned

| Area | Doc says | Code does | Severity |
|---|---|---|---|
| `src/milodex/risk/` | CLAUDE.md/AGENTS.md list it as one of seven modules | `__init__.py` is a docstring only; all risk logic lives in `execution/risk.py` | Low; ENGINEERING_STANDARDS.md acknowledges this, but CLAUDE.md/AGENTS.md do not |
| `src/milodex/backtesting/` | SRS Domain 5; ROADMAP 1.3 | `__init__.py` is a docstring only | Expected per roadmap; low |
| `src/milodex/analytics/` | SRS Domain 6; ROADMAP 1.3 | `__init__.py` is a docstring only | Expected per roadmap; low |
| State directory | `SRS.md` R-XC-006: `state/milodex.db`, `state/kill_switch.json`, `state/strategies/`, `state/locks/` | `data/milodex.db`, kill-switch inside event store, no strategy files, no locks dir | Medium — SRS needs updating |
| JSON output on every CLI command | `R-CLI-009` | Not implemented | Medium — Phase 1.3 assumes this exists |
| `meanrev` strategy | Family defined in `strategy-families.md`; config exists at `configs/meanrev_daily_rsi2pullback_v1.yaml` | No `src/milodex/strategies/meanrev_rsi2_pullback.py` | Expected per roadmap (Phase 1.2 open item) |
| Sizing semantics in regime strategy | `allocation_pct` is a percentage of equity | `allocation_pct` is used as raw share quantity | **High — correctness bug; see Top Findings** |
| `strategy run` command discovery | Roadmap § 4.1.5 lists the CLI command | Command is registered in `main.py` and backed by `StrategyRunner` | Aligned — marking here for completeness |

---

## Phase 1 Readiness

Against `ROADMAP_PHASE1.md § 2 "Phase 1 Success Criteria — Gating Checklist"`:

| Criterion | Status | Evidence |
|---|---|---|
| **SC-1** Both strategies defined entirely in `configs/*.yaml` | Config side ✅; runtime side partial (meanrev runtime missing) | Configs present; `regime_spy_shy_200dma.py` implements regime; no `meanrev_rsi2_pullback.py` |
| **SC-2** Each strategy backtestable from the CLI over a multi-year range | ❌ | `src/milodex/backtesting/` is docstring-only; no `backtest` CLI command |
| **SC-3** Each strategy runs in paper mode against Alpaca and submits orders when its rule fires | Pending — runner code exists, real paper run not yet done | Unit-tested against stubs; no recorded real paper session |
| **SC-4** `RiskEvaluator` has rejected at least one real attempted trade in development | Pending — exercised in tests, not against a real account | `test_service.py` verifies rejection paths; roadmap calls for non-synthetic evidence |
| **SC-5** Kill switch has been triggered in practice, verified to halt, verified to require manual reset | Pending — exercised in tests | Event-store-backed; legacy JSON migration covered; no real-world trigger event recorded |
| **SC-6** Operator can answer "is this strategy making or losing money vs. SPY" from CLI alone | ❌ | No analytics, no report command |

Readiness by phase:

- **Phase 1.0, 1.1: Done.** Evidence is the shipped code and tests; matches the roadmap's claim.
- **Phase 1.2: ~70% done.**
  - Event store: done and tested.
  - Strategy base, loader, config hashing: done and tested.
  - Regime strategy: done and tested.
  - Runner + dual-stop dialog: done and unit-tested with stubs. Phase 1.2 Definition-of-Done requires a **real paper session** end-to-end and a **real observed risk rejection** — neither is on file.
  - Meanrev strategy: not implemented.
- **Phase 1.3 (Analytics & Reporting): not started.** `backtesting/` and `analytics/` packages are empty.
- **Phase 1.4 (Promotion Pipeline): not started.**

### What is blocking Phase 1.2 completion

1. **`meanrev_rsi2_pullback.py` does not exist.** This is the only remaining net-new code item in 1.2.
2. **No real paper session has been run.** SC-3, SC-4, SC-5 Definition-of-Done requires non-synthetic evidence. Also, the **quantity bug** in the regime strategy (see Top Findings) means if you ran the regime strategy today against Alpaca paper, it would attempt a 1-share buy, not a 100% allocation.
3. **Test coverage target.** Phase 1.2 DoD says `≥80% across strategies/ and core/event_store.py`. This has not been measured or asserted in the repo.

### What is blocking Phase 1.3 start

1. **The backtest engine.** No code; large scope.
2. **JSON output contract.** `R-CLI-009` is unmet. Reporting commands will arrive in 1.3 and should ship with JSON parity from day one.
3. **Ledger schema for backtest trades.** Event store has a `trades` table; the roadmap calls for a `source=backtest` tag and a `backtest_runs` row — neither exists yet. This is a small schema extension, but it should go in as a migration, not a silent column add.

---

## Risk Layer Status

The docs are emphatic: the risk layer is sacred. This is the most important section of this review.

### What the risk layer is, today

`src/milodex/execution/risk.py` implements `RiskEvaluator.evaluate(context)` that runs eleven named checks, in this order, producing a `RiskDecision`:

1. `_check_kill_switch`
2. `_check_trading_mode`
3. `_check_strategy_stage`
4. `_check_market_open`
5. `_check_data_staleness`
6. `_check_daily_loss`
7. `_check_order_value`
8. `_check_single_position_limit`
9. `_check_total_exposure`
10. `_check_concurrent_positions`
11. `_check_duplicate_order`

These map cleanly onto `RISK_POLICY.md`. The evaluator is invoked by `ExecutionService.preview()` and `ExecutionService.submit_paper()` — both code paths are funneled through it. I did not find any path that bypasses it.

### What's good

- **Single choke point.** `ExecutionService` is the only caller; `StrategyRunner` submits through `ExecutionService`, not around it. The "strategy proposes, risk disposes" rule is actually enforced by the code.
- **Explanation records.** Every preview and submit writes an explanation row to the event store with the result of each risk check. That is a real audit trail — not a log line that gets rotated out.
- **Kill switch is event-store-backed with no auto-reset.** The only way to clear it is `reset()`, which requires an explicit CLI action. The legacy JSON path is migrated on first use. Good.
- **Strategy-stage gate works.** Paper submit is gated on `strategy_stage in {backtest, paper}`; test coverage confirms rejection paths.

### What's weak

- **The module name lies about its location.** The risk layer lives in `execution/`, not `risk/`. `ENGINEERING_STANDARDS.md` says this is by design and the logical boundary is preserved. That's defensible, but the empty `src/milodex/risk/__init__.py` is a literal trap — a contributor adding a new risk rule is at least 50/50 to put it in the wrong place. Either populate `risk/` with the evaluator and leave a thin re-export in `execution/`, or delete `risk/` and update CLAUDE.md/AGENTS.md/VISION.md to stop listing it as a module.
- **Risk rules are tested only through the execution service, and only a subset.** There is no `tests/milodex/risk/test_*.py`. The rules with **direct coverage** via `test_service.py`:
  - Kill switch (activation, enforcement)
  - Trading mode (paper-only in Phase 1)
  - Strategy stage gating
  - Market closed blocks submit but not preview
  - Stale data
  - Duplicate order
- Rules with **no direct test assertions**, only fixture values:
  - **`_check_daily_loss`**
  - **`_check_order_value`**
  - **`_check_single_position_limit`**
  - **`_check_total_exposure`**
  - **`_check_concurrent_positions`**
- Five of the eleven rules — including every exposure/loss cap — have zero behavioral tests. This is the most important test debt in the project.
- **No property or fuzz tests** on the risk evaluator. Given the stated sacredness, something like "no input context ever produces a decision without an explanation record" or "if kill_switch_state.active is True, decision is always reject regardless of everything else" belong as property tests.
- **No negative integration test** where two or more rules both fail. The order of rule evaluation matters for which reason the operator sees; there's nothing verifying that behavior.

### Summary

The risk layer is implemented, wired through a single choke point, and event-logged. That is the right architecture. The test coverage has a specific hole that matters: the exposure/loss rules are untested in the suite that the docs claim is sacred. Closing that hole is a 2–4 hour job and should happen before the first real paper session.

---

## Test Coverage Reality Check

Total test files: 14 (excluding `__init__.py`).

### What is tested

| Area | Location | Notes |
|---|---|---|
| Broker client + models | `tests/milodex/broker/` | Good coverage for the happy paths and some error paths |
| Data provider + cache + models | `tests/milodex/data/` | Parquet cache behavior, Alpaca provider, model round-trips |
| Config loading | `tests/milodex/test_config.py` | Env + path resolution |
| CLI argparse | `tests/milodex/cli/test_main.py`, `test_config_validation.py` | Surface-level, not end-to-end |
| Event store | `tests/milodex/core/test_event_store.py` | Schema, round-trip, migrations |
| Execution service + risk (indirect) | `tests/milodex/execution/test_service.py` | 14 tests exercising preview/submit, kill-switch events, strategy-stage gating, duplicate order, market hours, data staleness, session-id stamping |
| Strategy loader | `tests/milodex/strategies/test_loader.py` | Rejects unknown ids and missing params; config hash determinism |
| Regime strategy | `tests/milodex/strategies/test_regime_spy_shy_200dma.py` | Golden output on a fixed bar window; loader resolution |
| Strategy runner | `tests/milodex/strategies/test_runner.py` | Signal → submit path; kill-switch shutdown; session id |

### What is not tested

| Area | Reason | Severity |
|---|---|---|
| `_check_daily_loss` | No direct behavior test; fixtures configure the cap but no scenario trips it | **High — risk rule** |
| `_check_order_value` | Same | **High — risk rule** |
| `_check_single_position_limit` | Same | **High — risk rule** |
| `_check_total_exposure` | Same | **High — risk rule** |
| `_check_concurrent_positions` | Same | **High — risk rule** |
| Meanrev strategy | Strategy is not implemented yet | Expected |
| Backtesting | Module is not implemented yet | Expected |
| Analytics | Module is not implemented yet | Expected |
| Real-account paper smoke test | No integration harness or fixture flag | Medium — needed for SC-3/4/5 |
| Property tests on `RiskEvaluator` (invariants) | Not present | Medium — given the stated sacredness |
| Kill-switch migration path from legacy JSON | Touched by `state.py`, not asserted | Low-medium |
| `strategies/base.py::StrategyContext` behavior beyond resolution | Partial | Low |
| Config fingerprint as stable across reorderings of keys | Hash determinism is tested on identical input; canonicalization robustness is not | Low-medium |

### Coverage Reality

The project is **competently tested where it's implemented**, with the single glaring exception of the five untested risk rules. Given `FOUNDER_INTENT.md`'s insistence on credibility through evidence, this gap is the loudest thing a reviewer will notice.

---

## Top Findings

Prioritized, concrete, actionable. Each is labeled with a confidence marker only where I am making a specific bug or defect claim.

### 1. `allocation_pct` is used as a raw share quantity in the regime strategy — **correctness bug, high confidence**

`src/milodex/strategies/regime_spy_shy_200dma.py` line 72 constructs the BUY `TradeIntent` with `quantity=allocation_pct`. The config at `configs/spy_shy_200dma_v1.yaml` sets `allocation_pct: 1.00`, documented (and named) as a percentage of account equity. The runtime would therefore submit a **1-share BUY** of SPY or SHY, not a 100%-of-equity allocation. The golden-output test in `tests/milodex/strategies/test_regime_spy_shy_200dma.py` asserts `quantity == 1.00` which is why the bug does not fail CI.

This is the single highest-severity item in the repo. It silently invalidates any real paper run of the regime strategy, which is the very strategy the roadmap relies on for SC-3/SC-6 evidence.

**Fix:** introduce a sizing step (equity × allocation_pct ÷ last close, rounded down) either in the strategy or, preferably, in a shared sizing utility in `execution/` that consumes `sizing_rule` and `per_position_notional_pct` / `allocation_pct` uniformly for both families. The `meanrev` family config already anticipates this with `sizing_rule: fixed_notional` — build it once, use it twice. Update the golden-output test to reflect the new quantity derivation.

### 2. Five risk rules have no direct test coverage — **test debt, sacred-layer impact**

See the Risk Layer section. The rules covered indirectly by fixtures but not exercised in any assertion:
- `_check_daily_loss`
- `_check_order_value`
- `_check_single_position_limit`
- `_check_total_exposure`
- `_check_concurrent_positions`

**Fix:** add `tests/milodex/execution/test_risk_rules.py` (or `tests/milodex/risk/test_rules.py` if #3 is done first). One parametrized test per rule: pass-case, just-under threshold, just-over threshold, malformed input. This is 2–4 hours of work and closes the most important credibility gap.

### 3. `src/milodex/risk/` is a misleading empty module

`__init__.py` is a docstring only. All risk logic lives in `src/milodex/execution/risk.py`. `ENGINEERING_STANDARDS.md` acknowledges this as deliberate, but `CLAUDE.md`, `AGENTS.md`, and the module overview in `VISION.md` still describe `risk/` as one of seven top-level modules. A future contributor or automated assistant will mis-place new rules.

**Fix (two options, pick one):**

(a) Move `RiskEvaluator` and related types into `src/milodex/risk/evaluator.py`. Leave a thin re-export in `execution/risk.py` so `ExecutionService` doesn't change imports. Add an ADR describing the move. Update `ENGINEERING_STANDARDS.md`.

(b) Delete `src/milodex/risk/__init__.py`. Update `CLAUDE.md`, `AGENTS.md`, `VISION.md`, and the "seven modules" claim to state six physical modules plus an `execution/risk.py` submodule, matching `ENGINEERING_STANDARDS.md`.

Option (a) is more in keeping with the spirit of the docs; option (b) is cheaper.

### 4. SRS `state/` directory layout does not match implementation — **documentation drift**

`SRS.md` R-XC-006 specifies `state/milodex.db`, `state/kill_switch.json`, `state/strategies/<name>.json`, `state/locks/`. The code uses `data/milodex.db`, holds kill-switch state inside the event store, has no per-strategy JSON files, and has no locks directory.

**Fix:** either (a) migrate the code to a `state/` directory and add the missing artifacts, or (b) write an ADR explicitly superseding R-XC-006 and update the SRS. Given that the event store subsumes most of what the JSON files were meant to hold, (b) is the right path. A lock directory or equivalent advisory lock is still a genuine gap — single-process enforcement is not in place and should be.

### 5. `R-CLI-009` (`--json` on every command) is unimplemented and underweighted in the roadmap

`CLI_UX.md` and `REPORTING.md` both describe JSON output as the stable contract for future GUIs and automations. The current CLI produces human-readable output only. Phase 1.3 will add `report`/`analytics` commands that are meaningless without a JSON counterpart. This should be landed as a foundational concern, not a polish item.

**Fix:** add a `--json` flag to the shared argparse parent in `src/milodex/cli/main.py` and a `JsonEncoder` / renderer per command class. Build the JSON contract fields listed in `CLI_UX.md` ("command name, timestamp, success status, data, warnings, errors, schema_version"). Do this *before* Phase 1.3 starts so the reporting commands ship with JSON from day one.

### 6. Phase 1.2 Definition of Done is not yet met, but is closer than it looks

The roadmap's 1.2 DoD requires: (a) regime and meanrev strategies run end-to-end in paper for a full session, (b) explanation records per decision, (c) dual-stop dialog verified, (d) SC-3/SC-4 evidence, (e) ≥80% coverage on `strategies/` and `core/event_store.py`. Status:

- (a) regime is plausible pending fix #1; meanrev runtime doesn't exist.
- (b) explanation records are wired; a real session will produce them.
- (c) SIGINT dispatch exists and is unit-tested against stubs; in-process verification is still synthetic.
- (d) has no evidence artifact.
- (e) has not been measured.

**Fix:** treat 1.2 DoD as a single deliverable. After #1 and #2 are closed, (i) run coverage and address shortfalls, (ii) execute a real Alpaca paper session with the regime strategy for at least one open-to-close cycle, (iii) deliberately trip the risk layer with a fat-finger-style preview to collect SC-4 evidence, (iv) deliberately activate and reset the kill switch to collect SC-5 evidence, (v) implement meanrev, (vi) repeat (ii) for meanrev. Write the evidence into a short `docs/reviews/PHASE_1.2_EVIDENCE_<date>.md`.

### 7. `meanrev_rsi2_pullback.py` is the only remaining net-new strategy code for Phase 1.2

The family is fully specified in `strategy-families.md`. The config exists at `configs/meanrev_daily_rsi2pullback_v1.yaml`. The loader and runner are ready to accept it. This is a focused piece of work, not open-ended.

**Fix:** build it next, in the order (a) pure `evaluate()` implementation following `strategy-families.md`, (b) universe loader from `configs/universe_phase1_v1.yaml`, (c) golden-output test covering entry/exit/timeout/ranking, (d) wire into `strategies/loader.py`.

### 8. Backtest engine scope has a non-obvious integration: shared trade ledger — **scope risk**

Roadmap § 1.2 flags it, but it's worth surfacing: the backtest engine must write to the **same** `trades` table as live paper, tagged with `source=backtest` and linked to a `backtest_runs` row. This is a schema migration and an explanation-record discipline question. It is easy to regret later if the backtest ledger is built as a separate system "for convenience".

**Fix:** before starting Phase 1.3 coding, add the migration that introduces `trades.source` and `backtest_runs` to the existing event store, and add tests proving both live and backtest paths produce identical-shape trade records. Build the engine against that shape.

### 9. No process-level concurrency protection

`R-XC-006` implied locks. `OPERATIONS.md` says a daemon is Phase 2+ but that "light continuous monitoring" may be active. Today, nothing prevents the operator from running `milodex strategy run` in two terminals simultaneously. The second would share the same event store DB and kill-switch state — with predictable corruption.

**Fix:** a single-process advisory lock (e.g., a file-based lock under `data/` or `state/` with PID and start-time) that `StrategyRunner` and submit-capable CLI commands acquire on entry. Low-severity but trivially preventable.

### 10. Stale `docs/reviews/2026-04-14-ui-state-review.md`

Its Phase 1.1 findings are superseded by this review and by shipped code.

**Fix:** prepend a one-line "Superseded by `PROJECT_STATE_ASSESSMENT_2026-04-21.md` — retained for history." header. Do not delete — historical review cadence is itself a credibility signal.

---

## Recommended Next Actions

Ordered by the sequence that unblocks the most downstream work with the least throwaway.

1. **Fix the regime-strategy sizing bug (Finding #1).** Everything that depends on SC-3/SC-4/SC-6 evidence is compromised until this is done. Add the shared sizing utility; update the golden-output test.
2. **Close the five untested risk rules (Finding #2).** The next paper session has credibility only if the sacred layer has direct tests for every hard-stop rule.
3. **Resolve the `risk/` module ambiguity (Finding #3).** Pick option (a) or (b) and commit. This is cheap today, more expensive every week.
4. **Land `meanrev_rsi2_pullback.py` (Finding #7).** Closes the last code gap in Phase 1.2.
5. **Add the `--json` contract (Finding #5).** Do it before Phase 1.3 so the reporting surface inherits it for free.
6. **Run Phase 1.2 evidence cycle (Finding #6).** Real paper session, real risk-rejection, real kill-switch trip. Persist the evidence artifact under `docs/reviews/`.
7. **Update SRS R-XC-006 and add an ADR for the event-store subsumption of state files (Finding #4).** Align the spec with reality.
8. **Add the single-process advisory lock (Finding #9).** Before any long-running session is left unattended.
9. **Extend the event store with `trades.source` and `backtest_runs` (Finding #8).** Before writing any backtest engine code.
10. **Only then begin Phase 1.3 backtest-engine code.**

The critical path from "today" to "Phase 1.3 can start honestly" is steps 1, 2, 4, 6. Steps 3, 5, 7, 8, 9 are cleanup that should happen inside the same arc rather than after it.

---

## Appendix — Module-by-Module Notes

### `src/milodex/broker/`
- **Files:** `client.py`, `alpaca_client.py`, `models.py`, `exceptions.py`, `__init__.py`.
- **Status:** Phase 1 complete. Typed interface, Alpaca-backed implementation, named exceptions.
- **Tests:** `test_alpaca_client.py`, `test_models.py`.
- **Notes:** Single area worth revisiting in Phase 1.3 is retry/backoff policy under degraded broker connectivity, per `OPERATIONS.md`. Currently implicit.

### `src/milodex/data/`
- **Files:** `provider.py`, `alpaca_provider.py`, `cache.py`, `models.py`, `__init__.py`.
- **Status:** Phase 1 complete. Parquet cache per `ADR` direction.
- **Tests:** `test_alpaca_provider.py`, `test_cache.py`, `test_models.py`.
- **Notes:** Freshness checks exist inside the risk layer (`_check_data_staleness`). Cache invalidation policy is simple (freshness per latest bar timestamp) and fine for Phase 1.

### `src/milodex/execution/`
- **Files:** `service.py`, `risk.py` (11 checks), `state.py`, `config.py`, `models.py`, `__init__.py`.
- **Status:** Phase 1.1 complete. Single choke point. Event-store-backed kill switch with legacy migration. Risk logic physically here instead of in `risk/`.
- **Tests:** `test_service.py` (14 tests). Five risk rules have no direct coverage — see Finding #2.
- **Notes:** The `legacy_path` plumbing in `service.py` and `cli/main.py` is a one-time migration feature and should be kept at least until the next release boundary.

### `src/milodex/risk/`
- **Files:** `__init__.py` only (7 lines docstring).
- **Status:** Empty. See Finding #3.

### `src/milodex/strategies/`
- **Files:** `base.py`, `loader.py`, `regime_spy_shy_200dma.py`, `runner.py`, `__init__.py`.
- **Status:** Phase 1.2 partially complete. Regime strategy implemented. Runner implemented with dual-stop dialog. Meanrev missing. Sizing bug present (Finding #1).
- **Tests:** `test_loader.py`, `test_regime_spy_shy_200dma.py`, `test_runner.py`.
- **Notes:** `StrategyContext` carries config hash per ADR 0015 intent; frozen-manifest enforcement lands in 1.4.

### `src/milodex/backtesting/`
- **Files:** `__init__.py` only.
- **Status:** Unstarted. Phase 1.3 work item. Expected per roadmap.

### `src/milodex/analytics/`
- **Files:** `__init__.py` only.
- **Status:** Unstarted. Phase 1.3 work item. Expected per roadmap.

### `src/milodex/cli/`
- **Files:** `main.py`, `config_validation.py`, `__init__.py`.
- **Status:** Phase 1.1 complete. `strategy run` command present and dispatches through `StrategyRunner`. No `--json` yet.
- **Tests:** `test_main.py`, `test_config_validation.py` — surface coverage only.
- **Notes:** `R-CLI-009` is the next structural gap; future commands (`report`, `reconcile`, `promote`) should only land alongside their JSON contract.

### `src/milodex/core/`
- **Files:** `event_store.py` + migrations, `__init__.py`.
- **Status:** Phase 1.2 prerequisite complete. Dataclasses for `ExplanationEvent`, `TradeEvent`, `KillSwitchEvent`, `StrategyRunEvent`. Forward-only migrations under the same package.
- **Tests:** `test_event_store.py`.
- **Notes:** Extend with `trades.source` column and `backtest_runs` table ahead of Phase 1.3 (Finding #8).

### `configs/`
- **Files:** `spy_shy_200dma_v1.yaml`, `meanrev_daily_rsi2pullback_v1.yaml`, `universe_phase1_v1.yaml`, `risk_defaults.yaml`, `sample_strategy.yaml`.
- **Status:** All present. Fingerprint hashing works; frozen-manifest enforcement lands in Phase 1.4.

### `docs/`
- **Status:** The strongest part of the repo. Drift items are small; see Documentation Maturity section.
- **Open items:** stale review banner; SRS R-XC-006 update; status markers on unimplemented CLI surfaces in `CLI_UX.md` and `REPORTING.md`.

### `pyproject.toml` / env
- **Status:** Dependencies are minimal and justified (`alpaca-py`, `pandas`, `pyarrow`, `python-dotenv`, `pytz`, `PyYAML`). Ruff and pytest configured. `milodex` CLI entrypoint registered. `.env.example` present and matches documented vars.
- **Notes:** No backtest-engine-specific dependency yet (e.g., a numerics library beyond pandas/numpy). That decision should be deliberate when 1.3 begins.

---

*End of assessment. Next scheduled checkpoint: after Phase 1.2 evidence cycle (Finding #6). Date that review `docs/reviews/PHASE_1.2_EVIDENCE_<date>.md` when it exists.*

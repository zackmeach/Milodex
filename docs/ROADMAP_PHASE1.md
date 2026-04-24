# Phase 1 Completion Roadmap

**Live status:** see §2 (success criteria) and the checkboxes in §4–§6 for the authoritative completion state. This header is intentionally pointer-only so it cannot drift from the checklists below. As of the last major doc refresh (2026-04-24): Phase 1.0–1.3 complete end-to-end, Phase 1.4 slices 1–3 complete (frozen manifest, state machine + evidence, live-stage refusal); remaining work is the meanrev paper shakeout (§8 item 7), the SC-1/SC-2/SC-6 success-criteria walkthrough, and the cross-cutting items in §7.

This roadmap is the explicit, ordered plan to finish Phase 1. It is written against the authoritative scope in [VISION.md](VISION.md#detailed-roadmap), [SRS.md](SRS.md#phase-1-success-criteria), [FOUNDER_INTENT.md](FOUNDER_INTENT.md), and the ADRs. Requirement codes (`R-XX-NNN`) refer to entries in SRS.md.

---

## 1. Current State Snapshot

### 1.1 What Exists (Phase 1.0 + 1.1 — Complete)

| Layer | Location | Status |
|---|---|---|
| Broker interface + Alpaca impl | `src/milodex/broker/` | ✅ Complete; tested |
| Data provider + Parquet cache | `src/milodex/data/` | ✅ Complete; tested |
| Risk evaluator (11 checks) | `src/milodex/execution/risk.py` | ✅ Complete; tested |
| Execution service (preview/submit paper) | `src/milodex/execution/service.py` | ✅ Complete; tested |
| Kill switch + manual reset | `src/milodex/execution/state.py` | ✅ Complete; now event-store-backed |
| SQLite event store + migrations | `src/milodex/core/` | ✅ Complete; tested |
| Strategy base class + loader + config hashing | `src/milodex/strategies/` | ✅ Complete; tested |
| Regime strategy — SPY/SHY 200-DMA | `src/milodex/strategies/regime_spy_shy_200dma.py` | ✅ Complete; tested |
| CLI: `status`, `positions`, `orders`, `data bars`, `config validate`, `trade preview/submit/order-status/cancel`, `trade kill-switch status` | `src/milodex/cli/main.py` | ✅ Complete |
| Strategy configs | `configs/spy_shy_200dma_v1.yaml`, `configs/meanrev_daily_rsi2pullback_v1.yaml`, `configs/universe_phase1_v1.yaml` | ✅ Present |
| Risk defaults | `configs/risk_defaults.yaml` | ✅ Present |
| Phase-1-relevant docs and ADRs (0001–0017) | `docs/` | ✅ Present |

### 1.2 What's Missing (Phase 1.2 + 1.3 + 1.4)

| Module | Current state | Needed for |
|---|---|---|
| `src/milodex/strategies/` | Base contract + loader + regime + meanrev strategies landed (both with golden tests); runner still missing | Phase 1.2 |
| `src/milodex/backtesting/` | Docstring only (`__init__.py`, 7 lines) | Phase 1.2 / 1.3 |
| `src/milodex/analytics/` | Docstring only (`__init__.py`, 7 lines) | Phase 1.3 |
| SQLite event store | Landed in `src/milodex/core/`; strategy/backtest/promotion consumers still missing | 1.2 → 1.4 |
| Explanation records | Captured on manual `preview` / `submit_paper`; strategy runtime and backtest paths still missing | 1.2 onward |
| Promotion state machine | Stages validated at risk layer, no transition machinery | Phase 1.4 |
| CLI commands: `strategy run`, `backtest`, `analytics`/`report`, `promote`/`demote`, `reconcile` | Not in CLI | 1.2 → 1.4 |
| CLI `--json` output | Not implemented (`R-CLI-009` unmet) | 1.3 (supports reporting contract) |
| Config fingerprinting / frozen manifest (ADR 0015) | Slice 1 implemented (freeze + runtime drift check); state machine + evidence package pending | Phase 1.4 |

---

## 2. Phase 1 Success Criteria — Gating Checklist

From [SRS.md §Phase 1 Success Criteria](SRS.md#phase-1-success-criteria). Phase 1 is done when **all six** are simultaneously true. This roadmap is anchored to these.

- [ ] **SC-1.** Both strategies (SPY/SHY 200-DMA regime + meanrev RSI2 pullback) defined entirely in `configs/*.yaml`. — *Already ✅ for config and runtime; will close formally with the Phase 1.4 frozen-manifest work.*
- [ ] **SC-2.** Each strategy backtestable from the CLI over a multi-year range. Meanrev produces core metrics; regime produces deterministic output + explanation records per `R-XC-008`.
- [x] **SC-3.** Each strategy, unchanged, runs in paper mode against Alpaca and submits real paper orders when its rule fires. — *Regime evidenced 2026-04-23: session `09877e1e-aed0-4b6d-bc76-c4a1bce504fd` fired `BUY SPY x12 — allowed`, broker order `cfa3e348-35a` filled at $710.21. Audit row: `explanations#9320`. Meanrev half pending its own shakeout (§8 item 7).*
- [x] **SC-4.** `RiskEvaluator` has rejected at least one real attempted trade in development (non-synthetic evidence). — *Evidenced 2026-04-23: session `ac0a6620-1a22-4728-bc08-6c7a8f4551ae` attempted `BUY SPY x141` ($100k notional) and was rejected by four simultaneous risk checks (`max_order_value_exceeded`, `max_single_position_exceeded`, `max_total_exposure_exceeded`, `max_concurrent_positions_exceeded`). Audit row: `explanations#9319`. Driving config defect (`allocation_pct: 1.00`) fixed in commit `ca76985`.*
- [x] **SC-5.** Kill switch has been triggered in practice, verified to halt, verified to require manual reset. — *Evidenced 2026-04-23: activated via runner `k` shutdown (`kill_switch_events#3`), then a manual `trade submit SPY` was refused with reason `kill_switch_active` (`explanations#9322`), reset via the new `trade kill-switch reset --confirm` (`kill_switch_events#4`), and the same trade then succeeded (`explanations#9347`). The reset CLI was missing entirely until commit `a56de90`.*
- [ ] **SC-6.** Operator can answer *"is this strategy making or losing money, and how does it compare to SPY?"* from the CLI alone.

---

## 3. Sequencing Rationale

The order below is not arbitrary. Each sub-phase unblocks the next:

1. **SQLite event store lands first** (moved earlier than VISION's nominal 1.3 placement). Nothing else can honestly log explanation records (`R-XC-008`), backtest runs, or promotion decisions without it. Every later stage assumes it exists.
2. **Strategy runtime next.** Without it, the risk layer has nothing to vet and the backtest engine has nothing to replay.
3. **Backtest engine + analytics together.** They share the trade-ledger shape. Building them as a pair keeps the "same code, historical or live" guarantee in [VISION §Phase 1.2](VISION.md#phase-12--strategy-engine) honest.
4. **Promotion pipeline last.** It composes on top of everything else (backtest evidence, trade logs, config manifests).

Regime strategy precedes meanrev throughout: it's simpler (single-asset rotation, no ranking), exempt from Sharpe/30-trade thresholds per `R-PRM-004`, and is the intended **lifecycle-proof** path through the whole system.

---

## 4. Phase 1.2 — Strategy Engine

**Goal:** A manually-invoked, long-running foreground process (`milodex strategy run <name>`) that loads a strategy from YAML, subscribes to bars, evaluates signals, and pipes intents to `ExecutionService`. Per ADR 0012.

### 4.1 Work Items

#### 4.1.1 SQLite Event Store *(prerequisite — built)*
- [x] Create `src/milodex/core/event_store.py` with `EventStore` class exposing append-only writes + typed reads. Backed by SQLite at `data/milodex.db`. Per ADR 0011.
- [x] Schema (first cut; extend per later phases):
  - `explanations` — decision records per `R-XC-008` (preview/submit outcomes, risk reasons, config hash)
  - `trades` — executed and attempted orders with link to `explanations.id`
  - `kill_switch_events` — activation + reset log (migrate from `logs/kill_switch_state.json`)
  - `strategy_runs` — foreground session starts/stops, exit reason (controlled stop vs kill switch), session-id
- [x] Migration strategy: versioned schema with a `_schema_version` table; forward-only migrations under `src/milodex/core/migrations/`.
- [x] Wire `ExecutionService.preview()` and `submit_paper()` to record explanation + trade rows on every call. Unwire the file-only kill-switch state path.
- [x] Tests: schema migration, insert/query round-trip, explanation-record completeness assertion for every risk check code path.

#### 4.1.2 Strategy Base Class + Loader *(built)*
- [x] `src/milodex/strategies/base.py` — `Strategy` ABC with `evaluate(bars: BarSet, context: StrategyContext) -> list[TradeIntent]`. Pure function of inputs; no I/O.
- [x] `src/milodex/strategies/loader.py` — Resolves a `strategy.id` from `configs/*.yaml` to a concrete `Strategy` subclass. Validates config schema against strategy-declared parameter spec.
- [x] Add config-hash (SHA-256 over canonicalized YAML) to `StrategyContext` now, even though frozen-manifest enforcement lands in 1.4. Log it with every explanation record so the 1.4 work has data to consume. Per ADR 0015.
- [x] Tests: loader-rejects-unknown-strategy-id, loader-rejects-missing-required-params, identical configs hash identically.

#### 4.1.3 Regime Strategy — SPY/SHY 200-DMA *(built)*
- [x] `src/milodex/strategies/regime_spy_shy_200dma.py` implementing `Strategy`. Logic per [strategy-families.md](strategy-families.md): if SPY close > 200-DMA → hold SPY; else → hold SHY. Rebalance on crossover only.
- [x] Golden-output test: given a fixed historical bar window, signals match a hand-computed expected sequence exactly.
- [x] **Evidence target:** this strategy is how we validate SC-3 and SC-6 end-to-end without claiming edge.

#### 4.1.4 Meanrev Strategy — RSI(2) Pullback *(built)*
- [x] `src/milodex/strategies/meanrev_rsi2_pullback.py` implementing `Strategy`. Logic per [strategy-families.md](strategy-families.md) and the config at `configs/meanrev_daily_rsi2pullback_v1.yaml`: entries on RSI(2) < threshold with price > 200-DMA filter; exits on RSI(2) > exit threshold or N-day timeout; ranking rule when multiple candidates.
- [x] Universe loading from `configs/universe_phase1_v1.yaml`.
- [x] Golden-output test covering entry, exit, timeout, and multi-candidate ranking paths.

#### 4.1.5 Runner + Shutdown Dialog
- [x] `src/milodex/strategies/runner.py` — `StrategyRunner` class:
  - Loads strategy, subscribes to daily bar close via `DataProvider`
  - On each close, calls `strategy.evaluate(...)`, pipes intents to `ExecutionService.submit_paper(...)`
  - Records `strategy_runs` session row; stamps every explanation record with the session-id
- [x] SIGINT handler implementing the **dual-stop dialog** (ADR 0012):
  - First Ctrl-C → prompt: *"Controlled stop (c) or kill switch (k)?"*
  - Controlled → finish current evaluation, exit cleanly, no open-order cancel
  - Kill switch → activate `KillSwitchStateStore`, cancel all open orders via `BrokerClient`, exit
- [x] CLI command: `milodex strategy run <strategy_id>`. Refuses to start if any `trading_mode != paper` (Phase 1 is paper-only per ADR 0004).
- [x] Integration test: runner against a mock `DataProvider` with scripted bars + a stub `BrokerClient`, verifying signals → intents → submissions → explanation records end-to-end.

### 4.2 Phase 1.2 Definition of Done

- `milodex strategy run regime.daily.sma200_rotation.spy_shy.v1` runs cleanly end-to-end in paper mode against Alpaca for at least one full trading session with no manual intervention.
- `milodex strategy run meanrev.daily.pullback_rsi2.curated_largecap.v1` does the same.
- Both generate explanation records in the event store on every decision (fire or no-fire).
- `Ctrl-C` presents the dual-stop dialog. Both paths verified in dev.
- SC-3 and SC-4 met (real trade attempted; at least one real risk rejection observed).
- Test coverage ≥80% across `strategies/` and `core/event_store.py`.

---

## 5. Phase 1.3 — Analytics & Reporting

**Goal:** Operator can answer "is this strategy making money, and how does it compare to SPY?" from the CLI (SC-6).

### 5.1 Work Items

#### 5.1.1 Backtest Engine
- [x] `src/milodex/backtesting/engine.py` — `BacktestEngine`:
  - Replays historical bars day-by-day through the **same** `Strategy.evaluate()` code path used live. No divergent branches (per VISION §1.2). *Structural guarantee landed 2026-04-23: engine now rides `ExecutionService.submit_backtest()` with `SimulatedBroker` + `NullRiskEvaluator` injected; no parallel loop exists.*
  - Applies slippage (default 0.1% per [RISK_POLICY.md](RISK_POLICY.md)) and commission (0 for Phase 1 Alpaca per ADR 0016).
  - Writes backtest trades to the event store under `trades` with a `source=backtest` tag plus a `backtest_runs` row.
- [x] `src/milodex/backtesting/walk_forward.py` — rolling train/test window splitter per `R-BKT-002`. Parameters: window length, step size, holdout tail. *Orchestrator landed 2026-04-24 via `walk_forward_runner.py`: each OOS window runs an independent simulation and the reported Sharpe / maxDD / total return are OOS-aggregate, not whole-period. See [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md) — prior `[x]` claim covered only the splitter math, not the evaluation semantics.*
- [x] Minimum-trade enforcement per `R-BKT-003`: statistical metrics for meanrev require ≥30 trades; regime is exempt per `R-PRM-004`. *Implemented as a CLI-layer label (`insufficient evidence` / `evidence_basis=operational`), not an engine-side gate — presentation-layer concern.*
- [x] CLI command: `milodex backtest <strategy_id> --start YYYY-MM-DD --end YYYY-MM-DD [--walk-forward]`.
- [x] CLI command: `milodex research screen --configs <glob> --start ... --end ...` — batch walk-forward evaluator that runs the OOS harness across many strategy configs and prints a ranked comparison table (per-row gate status is advisory; promotion remains a separate operator action). Backed by `src/milodex/backtesting/walk_forward_batch.py`. See [CLI_UX.md](CLI_UX.md#research-screen).
- [x] Tests: walk-forward window math, per-window OOS simulation + aggregate stitching ([test_walk_forward_runner.py](../tests/milodex/backtesting/test_walk_forward_runner.py)), slippage applied correctly, regime strategy backtest matches hand-computed golden output ([test_engine_golden_regime.py](../tests/milodex/backtesting/test_engine_golden_regime.py)), minimum-trade gate produces a clearly-flagged low-evidence result instead of a garbage Sharpe.
- [x] R-XC-008 "triggering event / alternatives rejected / rule threshold" explanation fields — `Strategy.evaluate()` now returns `StrategyDecision(intents, reasoning)`; reasoning persists into `ExplanationEvent.context["reasoning"]` for both paper and backtest paths, plus a no-trade row per non-firing cycle. Closed by plan [2026-04-23-r-xc-008-strategy-reasoning.md](superpowers/plans/2026-04-23-r-xc-008-strategy-reasoning.md) (commits through 2f20b51).

#### 5.1.2 Analytics & Metrics
- [x] `src/milodex/analytics/metrics.py` — pure functions over a trade ledger:
  - Total return, CAGR
  - Max drawdown, max drawdown duration
  - Sharpe ratio, Sortino ratio
  - Win rate, avg win / avg loss, profit factor
  - Avg holding period
  - Per [REPORTING.md](REPORTING.md). Each returns a value + a confidence label (`R-CLI-014`) tied to trade count.
- [x] `src/milodex/analytics/benchmark.py` — SPY benchmark comparison: fetches SPY bars over the same window, computes SPY total return + drawdown, returns delta. Per `R-ANA-003`.
- [x] `src/milodex/analytics/snapshots.py` — daily portfolio snapshots (positions, cash, equity) written to event store at session end. *Module + `portfolio_snapshots` migration + event-store helpers landed; wiring into `StrategyRunner` / `BacktestEngine` deferred to §5.2 (lifecycle) per plan-answer.*
- [x] `src/milodex/analytics/reports.py` — assembles a "trust report" (per [REPORTING.md](REPORTING.md)): metrics, benchmark delta, uncertainty labels, open questions.

#### 5.1.3 CLI — Reporting Surface
- [x] `milodex analytics metrics <strategy_id>` — prints the trust-report metric set.
- [x] `milodex analytics trades <strategy_id>` — lists the trade ledger (paper + backtest, filterable).
- [x] `milodex analytics compare <strategy_id>` — strategy vs SPY over same window.
- [x] `milodex analytics export <strategy_id> --format {csv,json,md}` — per `R-ANA-006`.
- [x] `milodex reconcile` — compares local open-orders/positions against broker state, reports mismatches, per `R-OPS-004` and [OPERATIONS.md](OPERATIONS.md).
- [x] **`--json` flag on every read command.** Adds the CLI formatter abstraction per ADR 0014. Human text remains the default. Locks a stable JSON contract now, before any future GUI.
- [x] Tests: metric computation golden values, SPY benchmark fetch mocked, export format validators, reconcile flags deliberate state mismatches correctly.

**No surviving Phase 1.3 deferrals.** R-XC-008 closed 2026-04-23 via the `StrategyDecision` / `DecisionReasoning` interface change (see §5.1.1 entry).

### 5.2 Phase 1.3 Definition of Done

- `milodex backtest regime.daily.sma200_rotation.spy_shy.v1 --start 2015-01-01 --end 2024-12-31` returns deterministic output with full explanation records.
- `milodex backtest meanrev.daily.pullback_rsi2.curated_largecap.v1 --start 2015-01-01 --end 2024-12-31 --walk-forward` returns trust-report metrics with clearly-labeled confidence levels.
- `milodex analytics compare meanrev.daily.pullback_rsi2.curated_largecap.v1` shows strategy-vs-SPY delta at a glance.
- `milodex reconcile` runs cleanly in paper mode.
- SC-2 and SC-6 met.

---

## 6. Phase 1.4 — Promotion Pipeline

**Goal:** Formal `backtest → paper → micro_live → live` state machine with evidence gates and explicit operator approval, per ADR 0009 and [PROMOTION_GOVERNANCE.md](PROMOTION_GOVERNANCE.md).

### 6.1 Work Items

#### 6.1.1 Frozen Manifest (ADR 0015)
- [x] `src/milodex/promotion/manifest.py` — `freeze_manifest(config_path)` snapshots the canonicalized strategy YAML + SHA-256 hash at the strategy's declared stage, written to event store as `strategy_manifests` (append-only, keyed on `(strategy_id, stage)`). Landed 2026-04-23 via Phase 1.4 slice 1. Note: scope is strategy YAML only — universe/risk_defaults hashing is deferred per slice-1 decision.
- [x] Risk-layer check (`_check_manifest_drift`): refuses execution with `manifest_drift` or `no_frozen_manifest` reason codes when the runtime config hash differs from (or is missing) the frozen manifest at `paper`/`micro_live`/`live` stage. Closes the "operator edits YAML after promotion" escape per `R-STR-011`..`R-STR-014`.
- [x] CLI: `milodex promotion freeze <strategy_id>` + `milodex promotion manifest <strategy_id>`.

#### 6.1.2 Promotion State Machine
- [x] `src/milodex/promotion/state_machine.py` — legal transitions only: `backtest → paper → micro_live → live`. No skipping. No downgrades except to `disabled`. (Slice 2, 2026-04-23.)
- [x] Evidence gates per `R-PRM-001..007`:
  - `backtest → paper`: ≥30 trades in walk-forward (except regime), Sharpe > 0.5, max DD < 15% (except regime which uses operational-correctness gates: "ran cleanly for N sessions, zero unexplained errors")
  - `paper → micro_live`: ≥30 paper trades or ≥N weeks paper runtime; same statistical thresholds
  - `micro_live → live`: explicit operator approval + kill-switch reset-count zero during micro_live *(live-stage refusal hook deferred to slice 3 per R-PRM-006)*
- [x] Evidence-package assembly: bundles backtest metrics, paper-run trades, risk rejections, and explanation records into one promotion-decision record per `R-PRM-003`. (`promotion/evidence.py`, `promotions.evidence_json`.)

#### 6.1.3 CLI — Promotion Commands
- [x] `milodex promotion promote <strategy_id> --to <stage>` — runs gates, assembles evidence package, auto-freezes manifest, requires `--recommendation` + `--risk` (R-PRM-008) and `--confirm` when `--to live`. Writes `promotions` row with `manifest_id` + `evidence_json`. (Slice 2.)
- [x] `milodex promotion demote <strategy_id> --to {backtest,disabled}` — always allowed, records `reverses_event_id` chain per R-PRM-010.
- [x] `milodex promotion history <strategy_id>` — read-only evidence audit with `↩` reversal-chain rendering.
- [x] Tests: state machine transitions legal/illegal, evidence gate failures reported with specific reason codes, missing-evidence refusal. (429 tests green.)
- [x] Live-stage refusal hook (slice 3) — CLI-level refusal of `--to live` (and `--to micro_live`) during Phase 1 per ADR 0004. (Slice 3, 2026-04-23.)

#### 6.1.4 Live-Trading Gate (Paper-Only Safeguard)
- [x] Even with the state machine in place, Phase 1 remains **paper-only** per ADR 0004. The `live` stage is implemented-but-locked: attempting to promote to `live` (or `micro_live`) returns a clear refusal citing ADR 0004 / R-PRM-006 at the state-machine level, and R-EXE-007 remains as runtime defense-in-depth. (Slice 3, 2026-04-23.)

### 6.2 Phase 1.4 Definition of Done

- Regime strategy has been promoted backtest → paper via the CLI with a recorded evidence package.
- An attempted `promote --to live` produces a clean, logged refusal.
- SC-1 fully met (both strategies lifecycle-tracked and manifest-frozen).
- A YAML edit to a promoted strategy config, without a fresh manifest freeze, is refused by the risk layer at runtime.

---

## 7. Cross-Cutting Work (Threads Throughout All Sub-Phases)

- [ ] **Scaffolded-vs-implemented markers.** Per `R-XC-016`, anything partially done is tagged `# scaffolded:` in code and mirrored in CLI help. No tag may survive into the Phase 1 success-criteria test.
- [ ] **Test coverage ratcheting.** Each sub-phase adds tests; CI fails on coverage regression within the module being extended. Meaningful coverage — not line-count theater.
- [ ] **Documentation updates.** Each merged sub-phase updates [README.md](../README.md) Quickstart and the relevant `docs/*.md`. New ADRs when an architectural decision emerges mid-work.
- [ ] **No live-mode drift.** Every code path that could conceivably touch live is gated behind the paper-only check in `risk.py` (already present) plus the Phase 1.4 live-refusal. Never remove either.
- [ ] **Kill switch exercise.** At least once during 1.2 paper running, manually trigger the kill switch against a live paper session, verify order cancellation, verify manual reset requirement. Records SC-5 evidence.

---

## 8. Ordered Work Breakdown (Actionable Sequence)

A suggested linear path through the above, grouped into shippable units:

1. **SQLite event store + migration of kill-switch state.** (§4.1.1) — foundation. **Completed 2026-04-21.**
2. **Strategy base class + loader + config hashing.** (§4.1.2) — completed 2026-04-21.
3. **Regime strategy implementation + golden tests.** (§4.1.3) — completed 2026-04-21.
4. **Meanrev strategy implementation + golden tests.** (§4.1.4) — completed 2026-04-22.
5. **StrategyRunner + dual-stop dialog + `strategy run` CLI.** (§4.1.5) — completed 2026-04-22; per-cycle stdout streaming added 2026-04-23 (commit `9b0ecc1`).
6. **Regime strategy paper-session shakeout + kill-switch exercise.** (achieves SC-3, SC-4, SC-5 against the simpler strategy.) — completed 2026-04-23. Two findings landed during the shakeout: regime `allocation_pct` was 1.00 against a 0.10 global cap (commit `ca76985`); `trade kill-switch` had no operator-facing reset subcommand (commit `a56de90`). Adjacent test-isolation pollution leak fixed in commit `048d4fc`.
7. **Meanrev paper-session shakeout.** — next up.
8. **Backtest engine + walk-forward splitter + `backtest` CLI.** (§5.1.1)
9. **Analytics metrics + SPY benchmark + trust report + `analytics` CLI.** (§5.1.2 / §5.1.3)
10. **CLI `--json` formatter abstraction + `reconcile` command.** (§5.1.3 bottom)
11. **Frozen manifest + risk-layer config-drift check.** (§6.1.1)
12. **Promotion state machine + evidence gates + `promote`/`demote` CLI.** (§6.1.2 / §6.1.3)
13. **Live-stage lock confirmation.** (§6.1.4)
14. **Phase 1 success-criteria walkthrough** — run the SC-1..SC-6 checklist end-to-end with the operator, record evidence. Mark Phase 1 complete only when all six pass simultaneously.

---

## 9. What Is Explicitly *Not* in This Roadmap

Parked in the SRS Phase 2+ appendix, called out here so the line stays bright:

- Concurrent multi-strategy execution
- Daemon / supervisor runtime
- Crypto or alternative assets
- ML-driven signals
- Alternative / sentiment data
- Desktop GUI
- Alternative brokers
- Distributable installer / onboarding flow beyond the R-XC-017/R-XC-018 safe defaults

Any pressure to pull these forward gets refused under the same "two strategies, two purposes" discipline that gates Phase 1 scope (per [FOUNDER_INTENT.md](FOUNDER_INTENT.md) and [VISION §Out of Scope for Phase 1](VISION.md)).

---

## 10. Tracking This Roadmap

- This file is the single source of truth for Phase 1 completion state.
- As items are completed, check the box and link the merge commit in the same line.
- Reopen an item only if its definition of done regresses — don't quietly un-check.
- When all §2 success criteria are checked, Phase 1 is over. File an ADR closing it out, then (and only then) open the Phase 2 planning doc.

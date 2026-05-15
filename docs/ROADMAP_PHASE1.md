# Phase 1 Completion Roadmap

> **Phase 1 was formally closed on 2026-05-04 via [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md).** This document is now a historical record. The §7 cross-cutting items are classified there into a Phase 1.5 cleanup band, Phase 2 carry-forward, or closed-by-the-ADR. New planning belongs in the Phase 2 planning document.

**Live status:** see §2 (success criteria) and the checkboxes in §4–§6 for the authoritative completion state. This header is intentionally pointer-only so it cannot drift from the checklists below. As of 2026-04-28: Phase 1.0–1.4 complete end-to-end. **All six SCs closed** — meanrev SC-3 evidenced 2026-04-28 via session `2506708f-b7a3-49eb-827a-f6883cc0b4ff` firing simultaneous BUY GLD ×23 + BUY SLV ×152 on its first cycle (RSI(2) 6.51 / 7.00 ≤ 10.0 entry threshold, both filled). **Phase 1 is gating-complete and closed via ADR 0023 on 2026-05-04.** Remaining work is non-gating §7 cross-cutting items (kill-switch meanrev exercise, doc sweep, scaffolded markers, close-bar finalization race) — see ADR 0023 for classification.

This roadmap is the explicit, ordered plan to finish Phase 1. It is written against the authoritative scope in [VISION.md](VISION.md#detailed-roadmap), [SRS.md](SRS.md#phase-1-success-criteria), [FOUNDER_INTENT.md](FOUNDER_INTENT.md), and the ADRs. Requirement codes (`R-XX-NNN`) refer to entries in SRS.md.

---

## 1. Current State Snapshot

### 1.1 What Exists (Phase 1.0 + 1.1 — Complete)

| Layer | Location | Status |
|---|---|---|
| Broker interface + Alpaca impl | `src/milodex/broker/` | ✅ Complete; tested |
| Data provider + Parquet cache | `src/milodex/data/` | ✅ Complete; tested |
| Risk evaluator (11 checks) | `src/milodex/risk/evaluator.py` | ✅ Complete; tested |
| Execution service (preview/submit paper) | `src/milodex/execution/service.py` | ✅ Complete; tested |
| Kill switch + manual reset | `src/milodex/execution/state.py` | ✅ Complete; now event-store-backed |
| SQLite event store + migrations | `src/milodex/core/` | ✅ Complete; tested |
| Strategy base class + loader + config hashing | `src/milodex/strategies/` | ✅ Complete; tested |
| Regime strategy — SPY/SHY 200-DMA | `src/milodex/strategies/regime_spy_shy_200dma.py` | ✅ Complete; tested |
| CLI: `status`, `positions`, `orders`, `data bars`, `config validate`, `trade preview/submit/order-status/cancel`, `trade kill-switch status` | `src/milodex/cli/main.py` | ✅ Complete |
| Strategy configs | `configs/spy_shy_200dma_v1.yaml`, `configs/meanrev_daily_rsi2pullback_v1.yaml`, `configs/universe_phase1_v1.yaml` | ✅ Present |
| Risk defaults | `configs/risk_defaults.yaml` | ✅ Present |
| Phase-1-relevant docs and ADRs (0001–0017) | `docs/` | ✅ Present |

### 1.2 What Remains for Phase 1 Completion

Phase 1.2, 1.3, and 1.4 are landed end-to-end. The earlier "What's Missing" table tracked module-level scaffolding gaps that are now all closed and would have rotted into a misleading state otherwise — see `git log` on this file for the historical version.

What actually remains:

| Item | Where | Status |
|---|---|---|
| **SC-3 (meanrev half)** — live paper-session evidence for `meanrev.daily.pullback_rsi2.curated_largecap.v1` | §2 SC-3, §8 item 7 | ✅ Closed 2026-04-28 (session `2506708f-...` fired GLD/SLV on first cycle). |
| **SC-1** — formal close once both strategies have a manifest frozen at the stage they actually run at | §2 SC-1 | ✅ Closed 2026-04-26 (regime manifest frozen via `milodex promotion freeze`, config_hash `e9798c61…`; meanrev already frozen 2026-04-24). |
| **§7 cross-cutting items** — kill-switch dry-run on meanrev, walk-forward labeling, stage-source consistency, doc updates, coverage ratchet | §7 | ✅ All closed. Kill-switch exercise closed 2026-05-04. Walk-forward labeling (P-1), runner close-bar race (CI-1), `strategy_runs` startup (CI-2), and position-cap scope (CS-1) all closed 2026-05-04 via commit `199c2a0` (Phase 2 carry-list close per ADR 0025). Doc sweep and coverage ratchet closed per §7 entries. |

Anything else moves to a Phase 2+ ticket — the line stays bright per §9.

---

## 2. Phase 1 Success Criteria — Gating Checklist

From [SRS.md §Phase 1 Success Criteria](SRS.md#phase-1-success-criteria). Phase 1 is done when **all six** are simultaneously true. This roadmap is anchored to these.

- [x] **SC-1.** Both strategies (SPY/SHY 200-DMA regime + meanrev RSI2 pullback) defined entirely in `configs/*.yaml`. — *Closed formally 2026-04-26: regime manifest frozen via `milodex promotion freeze regime.daily.sma200_rotation.spy_shy.v1` (config_hash `e9798c61b7bd021f04d29dea61d819419527c4ae92ae2ea6585143dea584c534`, source `configs/spy_shy_200dma_v1.yaml`, frozen_by `operator`); meanrev manifest already frozen 2026-04-24 (config_hash `f531a076d8a7e51b75eb9963a1ab426ec7a64c522d07c10fe5e1508edcbbd228`, source `configs/meanrev_daily_rsi2pullback_v1.yaml`). Both strategies are now lifecycle-tracked at the `paper` stage — runtime drift checks will refuse execution if either YAML is edited without a fresh freeze (R-STR-011..R-STR-014).*
- [x] **SC-2.** Each strategy backtestable from the CLI over a multi-year range. Meanrev produces core metrics; regime produces deterministic output + explanation records per `R-XC-008`. — *Meanrev evidenced 2026-04-26: walk-forward run `54e71b30-3db5-4c62-97b3-0afdd18598d5` over 2015-01-01 → 2024-12-31, 4 OOS windows, 752 trades, OOS-aggregate Sharpe 0.33 / return +4.34% / maxDD 6.41%. Trust report flagged single-window dependency ("dropping the best-returning window flips the sign — treat as fragile"); strategy as configured therefore would NOT pass the 0.5 Sharpe promotion gate, exactly the honest signal the platform should produce. Regime evidenced 2026-04-26: walk-forward run `5f5b5398-7b3d-4827-9ec6-0daef4f255df` over the same window — the 200-day warmup leaves room for only 1 OOS window of 558 trading days / 31 trades, OOS-aggregate Sharpe 1.07 / return +2.80% / maxDD 0.96%. Regime is exempt from the Sharpe / 30-trade promotion gates per R-PRM-004, but the deterministic-replay golden-output test ([test_engine_golden_regime.py](../tests/milodex/backtesting/test_engine_golden_regime.py)) carries the determinism guarantee SC-2 actually demands.*
- [x] **SC-3.** Each strategy, unchanged, runs in paper mode against Alpaca and submits real paper orders when its rule fires. — *Regime evidenced 2026-04-23: session `09877e1e-aed0-4b6d-bc76-c4a1bce504fd` fired `BUY SPY x12 — allowed`, broker order `cfa3e348-35a` filled at $710.21. Audit row: `explanations#9320`. Meanrev evidenced 2026-04-28: session `2506708f-b7a3-49eb-827a-f6883cc0b4ff` fired simultaneous `BUY GLD x23 — allowed` (broker order `af288310-eb0`, audit `explanations#14135` / `trades#10660`, fill $419.57) and `BUY SLV x152 — allowed` (broker order `928377c1-6b8`, audit `explanations#14136` / `trades#10661`, fill $65.73) on its first cycle at 14:10:29Z. Strategy reasoning: "RSI 6.51 below entry threshold 10.0 and close above MA; buy 2 candidate(s): GLD, SLV"; 38 universe members rejected on the same cycle (mostly RSI-threshold, some 200-SMA-filter). All 12 risk checks PASS for both fires. Both passes through `ExecutionService.submit_paper` with `submitted_by=strategy_runner`, confirming the strategy → execution → broker → event-store path operates end-to-end without manual intervention.*
- [x] **SC-4.** `RiskEvaluator` has rejected at least one real attempted trade in development (non-synthetic evidence). — *Evidenced 2026-04-23: session `ac0a6620-1a22-4728-bc08-6c7a8f4551ae` attempted `BUY SPY x141` ($100k notional) and was rejected by four simultaneous risk checks (`max_order_value_exceeded`, `max_single_position_exceeded`, `max_total_exposure_exceeded`, `max_concurrent_positions_exceeded`). Audit row: `explanations#9319`. Driving config defect (`allocation_pct: 1.00`) fixed in commit `ca76985`.*
- [x] **SC-5.** Kill switch has been triggered in practice, verified to halt, verified to require manual reset. — *Evidenced 2026-04-23: activated via runner `k` shutdown (`kill_switch_events#3`), then a manual `trade submit SPY` was refused with reason `kill_switch_active` (`explanations#9322`), reset via the new `trade kill-switch reset --confirm` (`kill_switch_events#4`), and the same trade then succeeded (`explanations#9347`). The reset CLI was missing entirely until commit `a56de90`.*
- [x] **SC-6.** Operator can answer *"is this strategy making or losing money, and how does it compare to SPY?"* from the CLI alone. — *Evidenced 2026-04-26 via `milodex --json analytics metrics --strategy meanrev.daily.pullback_rsi2.curated_largecap.v1 --compare-spy`: meanrev (run `54e71b30`, 752 trades, win rate 65.6%, profit factor 1.51) vs SPY benchmark over the same 2015-01-01 → 2024-12-31 window (+115.78% total return, Sharpe 1.01, maxDD 25.4%). The `report strategy` trust-report surface assembles the same view including confidence label and "paper vs backtest" line. Two reporting weaknesses surfaced and tracked under §7: (i) walk-forward runs report `total_return_pct=0` / `sharpe=null` / `trading_days=0` against the trade-ledger metrics view because each OOS window resets equity — the report needs to label these as "walk-forward windowed" rather than "whole-period"; (ii) `report strategy` reads the latest `promotion_log` row for stage display while `promotion manifest` reads the active manifest, so the two endpoints disagree when a pre-Phase-1.4 promotion lacks a frozen manifest (see §7).*

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
  - Replays historical bars day-by-day through the **same** `Strategy.evaluate()` code path used live. No divergent branches (per VISION §1.2). *Structural guarantee landed 2026-04-23: engine now rides `ExecutionService.submit_backtest()` with `SimulatedBroker` and an explicit backtest risk policy; no parallel loop exists.*
  - Applies configurable, universe-aware slippage (3 bps for highly liquid ETF universes, 5 bps for mixed/large-cap universes and unknown fallback) and commission (0 for Phase 1 Alpaca per ADR 0016).
  - Writes backtest trades to the event store under `trades` with a `source=backtest` tag plus a `backtest_runs` row.
- [x] `src/milodex/backtesting/walk_forward.py` — rolling train/test window splitter per `R-BKT-002`. Parameters: window length, step size, holdout tail. *Orchestrator landed 2026-04-24 via `walk_forward_runner.py`: each OOS window runs an independent simulation and the reported Sharpe / maxDD / total return are OOS-aggregate, not whole-period. See [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md) — prior `[x]` claim covered only the splitter math, not the evaluation semantics.*
- [x] Minimum-trade enforcement per `R-BKT-004`: statistical metrics require the strategy's configured `backtest.min_trades_required` value (default 30); regime is exempt per `R-PRM-004`. *Implemented as a CLI-layer label (`insufficient evidence` / `evidence_basis=operational`), not an engine-side gate — presentation-layer concern.*
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
  - `backtest → paper`: paper-readiness evidence for research-target strategies (Sharpe > 0.0, max DD < 25%, configured trade-count floor), except regime which uses operational-correctness gates: "ran cleanly for N sessions, zero unexplained errors"
  - `paper → micro_live`: configured trade-count floor or ≥N weeks paper runtime; strict statistical thresholds
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

- [x] **Scaffolded-vs-implemented markers.** *Closed 2026-05-04.* The R-XC-016 verification clause is enforced continuously by CI test [tests/milodex/test_scaffolded_markers.py](../tests/milodex/test_scaffolded_markers.py): untracked markers fail, missing markers fail, markers landing on any of the 12 Phase-1 critical paths fail (`strategies/{base,loader,regime_spy_shy_200dma,meanrev_rsi2_pullback,runner}.py`, `risk/{evaluator,policy}.py`, `execution/{service,state}.py`, `core/event_store.py`, `backtesting/{engine,walk_forward}.py`). All 4 tests in that suite pass as of 2026-05-04 (`pytest tests/milodex/test_scaffolded_markers.py` → 4 passed in 0.09s). Two `# scaffolded:` markers remain in [`src/milodex/cli/commands/reconcile.py`](../src/milodex/cli/commands/reconcile.py) (lines 38 and 716): the deferred `filled_since_last_sync` / `canceled_since_last_sync` / `strategy_linkage` reconcile dimensions, and the submit-gate refusal-on-detected-drift wiring. Both are R-OPS-004 follow-up territory and carry to Phase 2 implicitly per the [ENGINEERING_STANDARDS Scaffolded Inventory](../ENGINEERING_STANDARDS.md#scaffolded-inventory) "Closes when" column. Neither is on a Phase-1 critical path, so neither blocks Phase 1 closure. The §7 requirement — "no tag may survive into the Phase 1 success-criteria test" — is satisfied because `test_no_scaffolded_marker_on_phase1_critical_paths` is the SC-walkthrough test and it passes.
- [x] **Test coverage ratcheting (global floor).** Configured 2026-04-26 via `pytest-cov` + `[tool.coverage.report] fail_under = 89` in `pyproject.toml` — current measured coverage is 89.6% with a one-way ratchet (raise `fail_under` by 1 whenever sustained coverage reaches `fail_under + 2`, never lower). See `docs/ENGINEERING_STANDARDS.md` §"Coverage Ratchet". Per-module floors are a deliberate follow-up.
- [ ] **Documentation updates.** Each merged sub-phase updates [README.md](../README.md) Quickstart and the relevant `docs/*.md`. New ADRs when an architectural decision emerges mid-work.
- [x] **No live-mode drift.** *Closed 2026-05-04.* Three defensive layers locked structurally by [tests/milodex/test_no_live_mode_drift.py](../tests/milodex/test_no_live_mode_drift.py) (10 tests, all passing): (1) `RiskEvaluator.evaluate` must invoke `_check_trading_mode` (verified by AST parse so a commented-out call is caught — regex would false-negative); the check refuses any non-`paper` `trading_mode` with reason code `paper_mode_required`. (2) `promotion.state_machine.PHASE_ONE_BLOCKED_STAGES` contains both `live` and `micro_live`, and `validate_stage_transition` raises for either target citing Phase 1. (3) `.submit_order(` calls appear only in `broker/{client,alpaca_client,simulated}.py` and `execution/service.py` — the chokepoint architecture from CLAUDE.md ("execution is the chokepoint from intent → trade") is now machine-verified, not norm-only. Mutation-verified for layers 1 (commenting out `self._check_trading_mode(context)` triggers Test 1 with a clear AST-based error) and 3 (a stray `.submit_order(` planted in `cli/commands/status.py` triggers Test 7 with an allow-list error pointing the offender at the right next step). The §7 requirement — "Never remove either" — is now structurally enforced. Closes the Phase 1.5 "live-mode-drift CI invariant" item from [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md).
- [x] **Kill switch exercise.** *Closed 2026-04-23 against regime (`kill_switch_events#3..#4`, `explanations#9322` refused with `kill_switch_active` / `#9347` allowed after reset) and 2026-05-04 against meanrev (`kill_switch_events#9..#10` from session `bde1b79f-5e31-4851-a36e-d765e1f7382b` / `strategy_runs#16` with `exit_reason=kill_switch`; `explanations#15000` NVDA SELL ×1 refused with reason `kill_switch_active` while all 11 other risk checks PASS / `#15001` same trade allowed after reset, broker order `7ce22a06-ba9f-42b5-997d-998b42b8194c` / `trades#10696`). Symmetric SC-5 evidence across both strategies, closing the Phase 1.5 symmetrization from [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md). Also surfaced two CLI ergonomics observations during the meanrev exercise: `trade submit` requires four flags (`--side`, `--quantity`, `--order-type`, `--paper`) before reaching the risk evaluator, and there is intentionally no `kill-switch activate` subcommand — both are deliberate friction-as-feature per ADR 0005, not bugs.*
- [x] **Walk-forward report labeling.** *Surfaced 2026-04-26.* `analytics metrics` against a walk-forward `run_id` reports `total_return_pct=0`, `sharpe=null`, `trading_days=0` because each OOS window resets equity — the trade-ledger metrics (win rate, profit factor, trade count) are still meaningful. The trust-report surface needs an explicit "walk-forward windowed" / "whole-period" label so an operator can't misread a -0% as "no movement" when the OOS-aggregate is +4.34%. See SC-6 evidence above. *Closed 2026-05-04 via option (a) per [PHASE2_PLANNING.md §3.3 P-1](PHASE2_PLANNING.md#p-1-walk-forward-report-labeling--closed-2026-05-04-via-option-a). Commit `199c2a0` (`feat(phase-2): close cleanup-first carry list + C-2 lock + ADR 0025`).*
- [x] **Stage-source consistency between `report strategy` and `promotion manifest`.** *Closed 2026-05-04 (verification of pre-existing fix from 2026-04-26).* Already resolved by commit `0f78e49` (`fix(report): use frozen-manifest stage as runtime source-of-truth`), which predates [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md)'s classification of this item as Phase 1.5 by 8 days — the §7 entry text was simply not updated when the fix landed. Resolution implemented: `_resolve_runtime_stage` in [`src/milodex/cli/commands/report.py`](../src/milodex/cli/commands/report.py) prefers the active frozen manifest's stage (option `a` from the original framing), falls back to the promotion log only when no manifest exists, and *also* surfaces a WARNING line when the two disagree (option `c` blended in for forensic transparency). Verified live for meanrev: `report strategy` displays "Stage: paper (frozen manifest)" plus a warning citing the legacy `micro_live` promotion log entry — both endpoints agree on `paper` as the runtime authority, while the disagreement is preserved as forensic context. Behavior locked by tests at [tests/milodex/cli/test_report.py](../tests/milodex/cli/test_report.py) covering all three branches (`stage_source=="manifest"` with disagreement, `stage_source=="promotion_log"` without, and `stage_source=="default"` for un-promoted strategies). Auto-demotion (option `b` from the original framing) was deliberately not chosen — the warning text says "consider demoting + repromoting" rather than auto-doing it, because state changes need operator intent.
- [x] **Runner close-bar finalization race.** *Surfaced 2026-04-27.* `StrategyRunner.run_cycle` ([src/milodex/strategies/runner.py:87](../src/milodex/strategies/runner.py)) intentionally re-evaluates against the running 1D bar throughout the trading day and only advances `_last_processed_bar_at` once `is_market_open()` returns `False` — by design, so a single "lock-in" cycle records the canonical post-close evaluation against the finalized bar (see the inline docstring at runner.py:87-95 and the conditional at runner.py:116-117). The implementation has a subtle gap: `is_market_open()` flips False roughly at the 4:00 PM ET bell, but Alpaca's daily bar typically takes seconds-to-minutes to finalize after the closing auction settles. The lock-in cycle therefore captures the bar at the *moment* the broker reported the market closed — which may still be aggregating server-side — and advances the watermark to today's timestamp. Every subsequent cycle (which would have observed the truly finalized bar) is short-circuited by the same-timestamp `already_seen` check at runner.py:99-104 and never recorded. **Evidence from session `7e4b0315-371d-41b6-8060-248964b8c356` (2026-04-27):** 380 explanation rows during market hours, lock-in at 16:00:23 ET with `latest_bar_close = 266.24`, then 70 minutes of silent loop with `already_seen` short-circuiting every cycle until controlled stop at 17:09 ET. The recorded 266.24 is almost certainly not the official 4:00 PM closing print — divergence per session is unbounded and non-deterministic. Three fixes worth considering: (a) defer the lock-in — keep polling after `is_market_open()` returns False, only advance the watermark once two consecutive bar fetches return identical OHLCV (heuristic but data-source agnostic); (b) detect "bar finalized" via Alpaca's API surface if it distinguishes running vs. settled bars (provider-specific); (c) explicitly redocument the actual semantics ("intraday-running RSI through bell") in [strategy-families.md](strategy-families.md) and accept the divergence as designed. Non-gating for SC-3 — a fire would still produce a real broker order regardless — but the gap between *spec'd* and *actual* close-bar value is structurally present every session and worth resolving before any later-stage promotion. *Closed 2026-05-04 via option (a) per [PHASE2_PLANNING.md §3.1 CI-1](PHASE2_PLANNING.md#ci-1-close-bar-finalization-race--closed-2026-05-04-via-option-a). Commit `199c2a0` (`feat(phase-2): close cleanup-first carry list + C-2 lock + ADR 0025`).*
- [x] **`strategy_runs` row not written at runner startup.** *Surfaced 2026-05-04.* The runner records `explanations` rows immediately on each cycle but does not insert the corresponding `strategy_runs` row until shutdown. Verified via session `f73a5eb6-a72f-46a4-addc-90ca4404fdc6`: 28 cycle rows recorded under that `session_id` between 19:19:02 and 19:39:38 UTC while the latest row in `strategy_runs` was still `id=16` (from the kill-switch exercise that ended ~4 hours earlier). Operational consequence: the canonical "is a runner active?" query (`SELECT * FROM strategy_runs WHERE ended_at IS NULL`) returns zero rows even when a runner is actively recording cycles — operators and audit tooling cannot enumerate active sessions from the event store directly. The `explanations` table can be reverse-queried (latest-cycle timestamp grouped by `session_id`) but that's an indirect proxy. Three resolution paths: (a) **insert at startup** — `StrategyRunner` writes the `strategy_runs` row with `ended_at=NULL` on init or first cycle; the existing shutdown path UPDATEs the row with `ended_at` and `exit_reason`. Cleanest semantics; small change in [`src/milodex/strategies/runner.py`](../src/milodex/strategies/runner.py). (b) **separate active-sessions tracker** — keep write-at-shutdown for the audit trail and maintain a separate index (table or process registry) of active sessions. More plumbing, leaves the existing audit shape unchanged. (c) **document the constraint** — accept `strategy_runs` as shutdown-only and update operations docs to use the explanation-table reverse query as the canonical "is anything running?" check. Cheapest; weakest operability. Non-gating for Phase 1 (closed) — Phase 2 design call. Distinct from the close-bar finalization race (different aspect of the same module). *Closed 2026-05-04 via option (a) per [PHASE2_PLANNING.md §3.1 CI-2](PHASE2_PLANNING.md#ci-2-strategy_runs-row-not-written-at-runner-startup--closed-2026-05-04-via-option-a). Commit `199c2a0` (`feat(phase-2): close cleanup-first carry list + C-2 lock + ADR 0025`).*
- [x] **Strategy-level position caps vs account-scoped risk enforcement.** *Surfaced 2026-05-04.* Regime session `a140da6c-a50d-4bdb-98e9-fc2b20e2ed1f` validated [ADR 0022](adr/0022-strategy-rotation-scope-is-the-declared-universe.md) end-to-end: regime proposed exactly one intent (`BUY SPY x12`) with zero rogue SELL legs against meanrev's AVGO / GLD / SLV positions — the universe-scope fix held in production paper. However, every cycle was rejected with reason code `max_concurrent_positions_exceeded`: regime declares `max_positions: 1` in [configs/spy_shy_200dma_v1.yaml:41](../configs/spy_shy_200dma_v1.yaml) (correct in isolation — regime should hold *either* SPY or SHY at any time, never both), but the risk layer's `concurrent_positions` check counts *all* broker positions regardless of strategy origin. With meanrev's three leftover positions present, projected open count is `1 + 3 = 4 > 1` and every entry is blocked. Both behaviors are individually correct: ADR 0022 keeps strategies in their lane on the *intent* side; the "risk layer is sacred" principle in [CLAUDE.md](../CLAUDE.md) requires account-level enforcement on the *execution* side. The conflict is schema-level — `max_positions` is overloaded between "strategy-internal invariant" and "account-wide brake fed to the risk evaluator." Three resolution paths worth evaluating: (a) **per-strategy position accounting** — `concurrent_positions` counts only positions whose `submitted_by` (or strategy-attribution chain) matches the proposing strategy; cleanest semantics, largest code surface, requires position-attribution at write time; (b) **split the schema** — strategy YAML declares an informational `expected_concurrent_positions` while `risk_defaults.yaml` retains the sole binding `max_concurrent_positions`; keeps account-scoped enforcement intact, loses per-strategy ceiling expressiveness; (c) **document the constraint and accept** — multi-strategy paper accounts must size global limits to the *sum* of strategies' expected positions; mark single-account multi-strategy operation as a Phase-1 limitation, revisit in Phase 2 when concurrent multi-strategy execution becomes a formal goal per VISION's Out-of-Scope list. Non-gating for Phase 1 (gating-complete) — a real Phase-2 design call. The platform's honest-signal property is intact: every blocked cycle records one specific reason code with a clean risk-check breakdown. *Closed 2026-05-04 via option (c) per [PHASE2_PLANNING.md §3.2 CS-1](PHASE2_PLANNING.md#cs-1-strategy-level-position-caps-vs-account-scoped-risk-enforcement--closed-2026-05-04-via-option-c). No code changes — ADR 0024 codifies account-scoped enforcement as authoritative. Commit `199c2a0` (`feat(phase-2): close cleanup-first carry list + C-2 lock + ADR 0025`).*

---

## 8. Ordered Work Breakdown (Actionable Sequence)

A suggested linear path through the above, grouped into shippable units:

1. **SQLite event store + migration of kill-switch state.** (§4.1.1) — foundation. **Completed 2026-04-21.**
2. **Strategy base class + loader + config hashing.** (§4.1.2) — completed 2026-04-21.
3. **Regime strategy implementation + golden tests.** (§4.1.3) — completed 2026-04-21.
4. **Meanrev strategy implementation + golden tests.** (§4.1.4) — completed 2026-04-22.
5. **StrategyRunner + dual-stop dialog + `strategy run` CLI.** (§4.1.5) — completed 2026-04-22; per-cycle stdout streaming added 2026-04-23 (commit `9b0ecc1`).
6. **Regime strategy paper-session shakeout + kill-switch exercise.** (achieves SC-3, SC-4, SC-5 against the simpler strategy.) — completed 2026-04-23. Two findings landed during the shakeout: regime `allocation_pct` was 1.00 against a 0.10 global cap (commit `ca76985`); `trade kill-switch` had no operator-facing reset subcommand (commit `a56de90`). Adjacent test-isolation pollution leak fixed in commit `048d4fc`.
7. **Meanrev paper-session shakeout.** — *completed 2026-04-28.* Backtest + analytics half evidenced 2026-04-26 (closes SC-2 and SC-6); live paper session evidenced 2026-04-28 via session `2506708f-b7a3-49eb-827a-f6883cc0b4ff` firing simultaneous BUY GLD ×23 + BUY SLV ×152 on its first cycle when RSI(2) ≈ 6.5 dropped both below the 10.0 entry threshold (closes SC-3 — see §2 SC-3 evidence above for full audit chain). One ticket landed during the shakeout: the runner close-bar finalization race in `StrategyRunner.run_cycle` was surfaced and tracked under §7. Pre-flight checklist preserved below for reference (operator must run during US market hours, ideally a Mon–Thu daily-bar close so positions don't sit over a weekend on first run):
    - **A. Confirm paper mode and account.** `milodex status` — expect `Trading mode: paper` and a healthy buying-power line. The Alpaca paper account currently holds two leftover positions from a prior session (BAC ×191, JPM ×32 — both in the meanrev universe). Decide before starting whether to (a) liquidate them via `trade submit SELL` so the runner starts flat, or (b) leave them and let meanrev manage them via its RSI exit rule. Either is defensible; option (a) is cleaner for a first shakeout.
    - **B. Confirm clean kill switch.** `milodex trade kill-switch status` — expect `Active: no`. If active, reset with `milodex trade kill-switch reset --confirm` and log the reason.
    - **C. Confirm frozen manifest at paper stage.** `milodex promotion manifest meanrev.daily.pullback_rsi2.curated_largecap.v1` — expect `stage: paper` and a recent `frozen_at`. (Note: `report strategy` may still display `stage: micro_live` due to a pre-Phase-1.4 legacy promotion event — this is a reporting inconsistency tracked under §7. The runtime drift check uses the *manifest's* stage, so safety is intact, but the operator should be aware of the discrepancy before reading reports.)
    - **D. Confirm config validates and matches the frozen hash.** `milodex config validate configs/meanrev_daily_rsi2pullback_v1.yaml` — expect no errors. The runtime drift check enforces hash parity at evaluation time; a mismatch here would surface as `manifest_drift` in the explanation record on every cycle.
    - **E. Start the runner.** `milodex strategy run meanrev.daily.pullback_rsi2.curated_largecap.v1`. Per-cycle stdout streaming will show each evaluation; explanation records land in the event store regardless of fire/no-fire.
    - **F. Watch one or more daily-bar closes.** Each close should produce one `explanations` row per cycle (fire OR no-fire per R-XC-008). If the strategy fires, watch for the broker order ID and confirm a fill (or rejection if risk vetoes).
    - **G. Exercise the dual-stop dialog at least once.** First Ctrl-C → choose `c` (controlled stop) on a benign session, OR `k` (kill switch) on a session you intend to halt forcibly. Both paths must be exercised before SC-5's meanrev half is fully evidenced — though SC-5 is already closed against the regime strategy, doing the same against meanrev keeps the lifecycle-proof + research-target rails symmetric.
    - **H. Capture evidence inline in this roadmap.** When the runner fires its first real meanrev order, fill in the SC-3 line with the session UUID, broker order ID, fill price, and `explanations#NNNN` audit row — same pattern as the regime evidence on 2026-04-23.
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

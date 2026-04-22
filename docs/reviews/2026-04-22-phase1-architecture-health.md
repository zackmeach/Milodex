---
title: Phase 1 Architecture Health Check
date: 2026-04-22
reviewer: AI architect (internal review)
scope: src/milodex/ module boundaries and CLAUDE.md invariants
audience: Founder
status: Advisory — findings + completion roadmap to Phase 1.4 gate
---

# Phase 1 Architecture Health Check — 2026-04-22

Companion to `PROJECT_STATE_ASSESSMENT_2026-04-21.md`. That review covered docs, tests, and phase progress. This review is narrower: **does the code graph actually match the architectural invariants stated in `CLAUDE.md`?** Where it doesn't, what is the smallest set of changes that makes it true before Phase 1.4 opens.

No cheerleading. If a finding is "low priority / cosmetic" it says so.

---

## Executive summary

The invariants in `CLAUDE.md` — *risk is sacred*, *strategies are config-driven*, *promotion pipeline is mandatory*, *kill switch requires manual reset* — are **substantively enforced** in the code today. There is no path by which a strategy can submit a trade without passing the 11 risk checks in `RiskEvaluator`. The promotion gate refuses stage-skipping and downgrades. The kill switch is event-store-backed and never auto-resets.

The drift is **structural, not behavioral**. Three items stand out:

1. The `risk/` module imports its own output types (`RiskDecision`, `RiskCheckResult`) from `execution/models.py`. The dependency arrow points the wrong way relative to the stated invariant.
2. `CLAUDE.md` says "seven modules" but the tree has **nine** — `core/` and `execution/` are real, load-bearing, and undocumented at the top-level architecture description.
3. `cli/main.py` is 1621 lines and will be the landing pad for Phase 1.4's `report`, `reconcile`, `promote`, `demote`, `run daily`. It needs to be split before those commands land, not after.

None of these block Phase 1.3 evidence (which per `PHASE_1.3_EVIDENCE_2026-04-22.md` already has backtests and analytics running end-to-end against real Alpaca data). All of them should be cleaned up before Phase 1.4 starts, because Phase 1.4 will entrench them.

---

## Module map: claimed vs. actual

| `CLAUDE.md` claims | Actually exists |
|---|---|
| `broker/` | `broker/` ✅ |
| `strategies/` | `strategies/` ✅ |
| `risk/` | `risk/` ✅ (see Finding 1) |
| `backtesting/` | `backtesting/` ✅ |
| `data/` | `data/` ✅ |
| `analytics/` | `analytics/` ✅ |
| `cli/` | `cli/` ✅ (see Finding 4) |
| *(not mentioned)* | **`core/`** — event store, advisory lock, schema migrations (693 + 239 LoC) |
| *(not mentioned)* | **`execution/`** — orchestration service, sizing, state (~700 LoC) |

`core/` and `execution/` are not secondary utilities — they are where the event store and the trade-submit orchestration live. A new contributor reading `CLAUDE.md` will not know they exist.

---

## Findings

Ordered by priority. Each lists: symptom → why it matters → concrete remediation.

### Finding 1 — Risk layer dependency inversion (priority: **high**)

**Symptom.** `CLAUDE.md` states *"Every trade passes through `risk/` before execution. Strategy proposes, risk disposes."* That is an invariant in which risk sits **above** execution. The import graph says the opposite:

```python
# src/milodex/risk/evaluator.py, lines 19–23
from milodex.broker.models import AccountInfo, Order, OrderSide, OrderStatus, Position
from milodex.data.models import Bar
from milodex.execution.config import RiskDefaults, StrategyExecutionConfig
from milodex.execution.models import ExecutionRequest, RiskCheckResult, RiskDecision, TradeIntent
from milodex.execution.state import KillSwitchState
```

The output types of the risk layer — `RiskDecision`, `RiskCheckResult`, `EvaluationContext` — live in `execution/models.py`. `RiskDefaults` (the global risk-limit dataclass) lives in `execution/config.py`. `risk/evaluator.py` depends on `execution/` for **four of its five first-class types**. The historical reason is obvious: the evaluator was born inside `execution/` and was later lifted into `risk/` with a back-compat shim (`execution/risk.py`, 13 lines, currently still live). The lift was partial.

**Why it matters.** A code change to `execution/models.py` silently alters the shape of the "sacred" layer. ADR 0008 ("risk layer veto architecture") calls risk a distinct layer; the imports say it is downstream of execution. New contributors cannot reason about the module boundary by looking at the imports — they have to already know the history.

**Remediation.** Mechanical refactor, ~200 LoC move, behavior-preserving:

1. Move `RiskDecision`, `RiskCheckResult`, `EvaluationContext`, `RiskDefaults` from `execution/` to `risk/`.
2. `execution/config.py` retains `StrategyExecutionConfig` (execution-specific) and imports `RiskDefaults` from `risk/`.
3. `execution/models.py` retains `ExecutionRequest`, `ExecutionResult`, `ExecutionStatus`, `TradeIntent` and imports `RiskDecision`/`RiskCheckResult` from `risk/`.
4. Delete `execution/risk.py` shim once the dust settles.
5. Add an ADR (proposed 0019) recording the decision so this doesn't get undone by a future refactor that notices "risk imports execution types — must be wrong".

**Cost.** ~0.5 day. No behavior change. Test suite should pass unchanged.

---

### Finding 2 — `CLAUDE.md` module list is incomplete (priority: **high**, trivial fix)

**Symptom.** `CLAUDE.md`'s architecture section says *"src-layout Python package (`src/milodex/`). Seven modules."* The tree has nine top-level packages. `core/` and `execution/` are both load-bearing and both missing.

**Why it matters.** `CLAUDE.md` is the file Claude and any future contributor reads first. The event store — the source of truth per ADR 0011 — is invisible in the stated architecture.

**Remediation.** Edit `CLAUDE.md` architecture section. Add two lines:

```
- **core/** — Shared infrastructure: SQLite event store (ADR 0011), advisory locks, schema migrations. Source of truth for trade/explanation/kill-switch/strategy-run history.
- **execution/** — Trade orchestration service. Sits between strategies and broker, invokes the risk layer, records explanations. Never submits without risk approval.
```

Update "Seven modules" → "Nine modules".

**Cost.** 15 minutes. Do this today.

---

### Finding 3 — CLI is a single 1621-line file (priority: **medium**, but time-sensitive)

**Symptom.** `cli/main.py` is 1621 lines — the largest file in the project by ~3×. `docs/CLI_UX.md` and `docs/REPORTING.md` describe `report`, `reconcile`, `promote`, `demote`, `run daily`, and a full JSON-output contract (R-CLI-009) — none of which exist yet. Landing all of that in the current file comfortably pushes it past 2500 lines.

**Why it matters.** Phase 1.4's entire surface is new CLI commands. If this isn't split *before* 1.4, it will be split *during* 1.4, and the split will compete for attention with real feature work.

**Remediation.** Introduce a command-module layout before Phase 1.4 opens:

```
src/milodex/cli/
├── __init__.py
├── main.py              # argparse dispatcher only (~150 LoC)
├── formatter.py         # already exists
├── config_validation.py # already exists
└── commands/
    ├── __init__.py
    ├── status.py        # status, positions, orders
    ├── data.py          # data bars, data cache
    ├── trade.py         # preview, submit, cancel, order-status, kill-switch
    ├── strategy.py      # strategy run, strategy list
    ├── config.py        # config validate
    ├── backtest.py      # backtest run, backtest list
    └── analytics.py     # analytics metrics
```

Each command module owns its argparse subparser registration plus the command body. `main.py` imports and wires them together. No behavior change — this is pure reorganization, and argparse makes the split natural because each subcommand already has its own function.

**Cost.** ~1 day. Pays back the first time a Phase 1.4 command lands.

---

### Finding 4 — Promotion thresholds live in code, not config (priority: **medium**, decision needed)

**Symptom.** `CLAUDE.md` states *"Strategies are config-driven."* The promotion gate's thresholds are hardcoded:

```python
# src/milodex/strategies/promotion.py, lines 25–27
MIN_SHARPE: float = 0.5
MAX_DRAWDOWN_PCT: float = 15.0
MIN_TRADES: int = 30
```

**Why it matters.** These are **governance** thresholds, not strategy tuning. Two defensible answers, and the project needs to pick one and document it:

- **(A) Keep in code.** They are project-level invariants per SRS (R-PRM-001/002/003). Code makes them harder to silently loosen ("I'll just change this YAML for a minute…"). Git history is the audit log. Add an ADR calling them invariants.
- **(B) Move to `risk_defaults.yaml`.** They become auditable as part of the config fingerprint (ADR 0015). Git history is still the audit log. Matches the "config-driven" posture literally.

Current state is *neither* — the `CLAUDE.md` rule says one thing, the code does the other, no ADR explains the split.

**Recommendation.** **Option A**, with an ADR. Governance thresholds belong in code; *tunable* thresholds belong in YAML. The risk-defaults YAML is already where per-strategy caps live and where loosening is most dangerous — adding promotion thresholds into the same file makes them feel tunable, which they should not be.

**Cost.** ~1 hour to write the ADR. Zero code change.

---

### Finding 5 — Kill-switch activation is split across three call sites (priority: **medium**)

**Symptom.** Kill-switch activation happens in three places:

1. `ExecutionService._maybe_activate_kill_switch` — fires when the risk evaluator reports `kill_switch_threshold_breached` ([src/milodex/execution/service.py:227-229](src/milodex/execution/service.py#L227-L229)).
2. `StrategyRunner.shutdown(mode="kill_switch")` — fires on operator-requested kill via the SIGINT dual-stop dialog ([src/milodex/strategies/runner.py:121-125](src/milodex/strategies/runner.py#L121-L125)).
3. `KillSwitchStateStore.activate(reason)` — the raw API called by both of the above.

All three eventually route through the store, which is correct. But each caller constructs its own `reason` string ("Daily loss exceeded kill switch threshold." vs "Operator requested kill switch.") and there is no single entry point an operator can read to understand *"when does Milodex kill itself?"*.

**Why it matters.** As kill-switch triggers multiply (they will: consecutive-loss streaks, data-staleness-during-live, broker-reject storms), a fan-out of callers each calling `.activate(...)` with a bespoke reason becomes hard to audit. The risk evaluator *detects* but the service *acts* — that split is correct and worth preserving; it just needs one authoritative actor.

**Remediation.** Add `ExecutionService.trigger_kill_switch(reason: str, *, source: str)` and route both call sites through it. `source` ∈ {`"risk_threshold"`, `"operator_sigint"`, …} is recorded on the event. The raw `KillSwitchStateStore.activate` stays for tests only.

**Cost.** ~0.5 day.

---

### Finding 6 — Runner and service both write explanation events (priority: **low**)

**Symptom.** `ExecutionService._record_execution` writes `ExplanationEvent` + `TradeEvent` for `preview` and `submit` ([src/milodex/execution/service.py:231-322](src/milodex/execution/service.py#L231-L322)). `StrategyRunner._record_no_action` writes `ExplanationEvent` directly for hold decisions ([src/milodex/strategies/runner.py:231-262](src/milodex/strategies/runner.py#L231-L262)). Two constructions of the same event type, nearly identical.

**Why it matters.** Drift risk on the explanation schema. When a new column is added to `ExplanationEvent`, both sites need to be updated; one will be missed.

**Remediation.** Expose `ExecutionService.record_no_action(intent, latest_bar, strategy_config, session_id)`. Runner delegates. One write path.

**Cost.** ~2 hours.

---

### Finding 7 — `core/event_store.py` is 693 lines (priority: **low**, Phase 2)

**Symptom.** `event_store.py` handles explanations, trades, kill-switch events, strategy runs, backtest runs, schema migrations, all in one file. 693 lines today; will grow.

**Why it matters.** Not a Phase 1 problem. Flagging so it doesn't become a Phase 2 surprise.

**Remediation (Phase 2).** Split by event family: `core/events/explanation.py`, `core/events/trade.py`, etc., with `core/event_store.py` becoming a thin facade over a per-family writer.

**Cost.** ~1 day, *after* Phase 1 closes. Not blocking anything today.

---

### Previously resolved

- **`state/` vs `data/` directory drift** — resolved by **ADR 0018** (2026-04-21). The `state/` directory in the working tree is a vestige to be cleaned up, but the authoritative decision is recorded. Follow-up: delete the empty `state/` directory from the working tree and confirm nothing writes to it.

---

## What is structurally healthy

A fair review has to record what's working. These are the parts that are not just passing tests but are **architecturally right**:

- **The risk chokepoint is real.** `ExecutionService._evaluate` is the single path from intent → trade, and it invokes `RiskEvaluator` unconditionally. There is no bypass, no "unsafe_submit" escape hatch, no flag to skip checks. ADR 0008's veto model is enforced, not just documented.
- **11 risk checks match `RISK_POLICY.md` one-to-one.** `RiskEvaluator.evaluate` runs exactly the 11 rules the policy names, in the order the policy lists them. This is rare and valuable — the evaluator reads like the policy.
- **Backtest bypasses risk *by design* and documents why.** [src/milodex/backtesting/engine.py:14-16](src/milodex/backtesting/engine.py#L14-L16) explicitly states the invariant (risk is enforced at promotion, not simulation). This is exactly the right posture and it's written in the place where a future contributor would look.
- **Config fingerprinting + event store compose correctly.** Every explanation event carries a SHA-256 hash of the strategy config (ADR 0015), and every explanation is stored in the event store (ADR 0011). Reproducibility is a property of the architecture, not a promise in a doc.
- **Lifecycle-exempt vs statistical promotion.** The `promotion_type` split is the only way the 30-trade/Sharpe rule survives contact with a regime strategy. The split is centralized in `promotion.check_gate` and documented inline.
- **Stage-transition validation is authoritative.** `promotion.validate_stage_transition` refuses skips, downgrades, and same-stage no-ops. No other path in the codebase mutates strategy stage.

---

## Roadmap to Phase 1.4 gate

Ordered. Each item is costed and self-contained. Total: **~3 engineer-days** of work before Phase 1.4 formally opens.

### P0 — do before any Phase 1.4 code lands

| # | Item | Cost | Blocking? |
|---|---|---|---|
| 1 | Update `CLAUDE.md` to list 9 modules; add `core/` and `execution/` descriptions (Finding 2) | 15 min | No |
| 2 | Lift risk types into `risk/`; delete `execution/risk.py` shim; ADR 0019 (Finding 1) | 0.5 day | No, but entrenched if deferred |
| 3 | Split `cli/main.py` into `cli/commands/*.py` (Finding 3) | 1 day | Yes — Phase 1.4 is CLI-heavy |

### P1 — do during early Phase 1.4

| # | Item | Cost | Notes |
|---|---|---|---|
| 4 | Write ADR 0020: promotion thresholds are code-level invariants (Finding 4) | 1 hr | Decision-only, no code |
| 5 | Consolidate kill-switch activation in `ExecutionService.trigger_kill_switch` (Finding 5) | 0.5 day | Prepares for future kill-switch triggers |
| 6 | Move runner's `_record_no_action` into `ExecutionService.record_no_action` (Finding 6) | 2 hr | Schema-drift prevention |
| 7 | Delete vestigial `state/` directory; confirm ADR 0018 is fully realized | 15 min | Cleanup after ADR 0018 |

### P2 — after Phase 1 closes

| # | Item | Cost |
|---|---|---|
| 8 | Split `core/event_store.py` by event family (Finding 7) | ~1 day |

---

## Parking lot — not findings, but worth noting

- `RiskEvaluator.evaluate` runs 11 checks as a **hardcoded list**. A rule registry (decorator or explicit list in config) would let live-only rules be added without editing the evaluator. Not needed in Phase 1 — the 11 rules are fixed by policy. Revisit if the rule set grows past ~15 or starts branching by stage.
- `RiskEvaluator` imports broker models (`AccountInfo`, `Order`, `Position`) directly. A thin `RiskInput` DTO would decouple risk from broker internals. Not worth the abstraction cost today — Phase 1 has one broker and one trading mode.
- `R-CLI-009` (JSON output on every command) is still unstarted. Not raised as a finding because it's tracked in the roadmap, but worth re-flagging: the CLI split (P0 #3) should take `--json` into account in the new subparser layout so it isn't bolted on later.

---

## Verdict

The code obeys the rules it says it obeys. The structure around the code is half a milestone behind where it should be. Three days of cleanup before Phase 1.4 opens will pay for itself by the end of Phase 1.4.

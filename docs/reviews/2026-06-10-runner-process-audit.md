# Runner Process Audit — Config → Backtest → Paper, Top to Bottom

> **Resolution status (post-hardening-roadmap, 2026-06-13):** P0 #1 (ADR 0055 ledger contaminated by backtest trades) is FIXED — HR-1 #221 (paper-scope ledger filter). P0 #2 (market_closed daily no-op) is known/by-design, tracked as DC-2 / HR-8 (post-close gate). Findings below are retained for the record; do NOT read them as live un-remediated hazards.

**Date:** 2026-06-10
**Scope:** The full runner pipeline: config loading/validation, the backtest leg, promotion, runner launch (CLI + Bench/GUI), the run-cycle cadence, what runners believe they can trade with, the risk layer's gating, execution → broker, and the closing/stop paths.
**Method:** Single-context read of every file on the pipeline (`runner.py`, `paper_runner_control.py`, `runner_status.py`, `orphan_reconciliation.py`, `service.py`, `evaluator.py`, `attribution.py`, `config.py` ×2, `sizing.py`, `loader.py`, `base.py`, `daily_cross_sectional.py`, `_session_intraday.py`, `advisory_lock.py`, `alpaca_client.py`, `alpaca_provider.py`, `reconciliation.py`, `state.py`, `policy.py` ×2, `state_machine.py`, `manifest.py`, `bench.py` runner sections, `event_store.py` runner/dedup sections, engine `run`/coverage path, representative configs), followed by read-only ground-truthing against `data/milodex.db` (strategy_runs, explanations, trades, manifests, reconciliation_runs, kill_switch_events).
**Posture:** Read-only. This report is the only artifact created. Probe scripts used for DB queries were deleted.
**Prior art:** [2026-05-29 truth & direction audit](2026-05-29-milodex-truth-and-direction-audit.md). Findings already covered there (liveness consolidation, in-flight order counting, doctrine overstatement of sector/correlation caps) are not re-litigated; several have since landed and are verified working below.

---

## Executive summary

The runner *infrastructure* is in genuinely good shape: advisory locking, liveness identity-verification, orphan reconciliation, exit-reason taxonomy, the TOCTOU-closed risk envelope, and the fail-closed risk evaluator all check out in code **and** in the live DB. What is broken is the **position-truth feed and the execution semantics on top of it**:

1. **(P0) The ADR 0055 per-strategy ledger is contaminated by backtest trades.** The attribution queries filter `status='submitted'` but never `source='paper'`, so every backtest run pollutes the paper runners' position view. Live evidence: `momentum.daily.tsmom` believes it holds NVDA ×1341 (~$269k), WMT ×884 (~$106k), and more — on a $101k account whose reconciliation reports *clean*. Every daily strategy in the bank carries phantom positions.
2. **(P0, known-gap, now fully mechanized) Daily strategies structurally cannot execute** — they evaluate post-close by design, and the risk layer unconditionally blocks post-close submission (`market_closed`), with the 300-second staleness check as a second independent blocker. All 36 blocked submissions in the last 7 days carry `market_closed`.
3. These two compound dangerously: the `market_closed` block is currently the **only** thing stopping the phantom exits from reaching Alpaca. The small ones (e.g. XOM ×84 ≈ $12.6k) pass every other check, and a SELL with no broker position opens a **short** on the margin account. **Fix #1 before touching #2.**
4. **(P1) Intraday runners have no bar watermark** — they re-evaluate the latest (often still-forming) bar every poll cycle, 24/7, writing ~10k no-signal explanations per runner-day and fetching from Alpaca every 10 s all night.

Everything else is smaller: dead risk plumbing (`risk.stop_loss_pct`, `max_trades_per_day`), the reducing-orders-during-kill-switch promise that YAML makes and code doesn't keep, multi-day runner sessions silently degrading to exit-only at the NY-date rollover, and assorted config-comment rot.

---

## Pipeline walkthrough (how a trade actually happens)

This section is the reference map; findings below anchor into it.

### 1. Config

- The real validation gate is `load_strategy_config` ([loader.py:182-280](../../src/milodex/strategies/loader.py)): required keys, `_VALID_STAGES` / `_VALID_BAR_SIZES` allowlists, id = `{family}.{template}.{variant}.v{version}` structural check, inline-universe XOR `universe_ref`.
- A second, narrower loader exists for the execution layer: `load_strategy_execution_config` ([execution/config.py:31-46](../../src/milodex/execution/config.py)) reads only `name/enabled/stage/risk.*`.
- Canonical hashing: `compute_config_hash` ([loader.py:307-319](../../src/milodex/strategies/loader.py)) canonicalizes parsed YAML (comments invisible, `display_name` excluded per [loader.py:456-471](../../src/milodex/strategies/loader.py)).
- Registry: `build_default_registry` ([loader.py:322-391](../../src/milodex/strategies/loader.py)) auto-discovers all `Strategy` subclasses, loud on import error and duplicate `(family, template)`.

### 2. Backtest

- `BacktestEngine.run` ([engine.py:402-502](../../src/milodex/backtesting/engine.py)) orphan-reconciles prior `backtest_runs`, writes a `running` row, executes, stamps `completed/failed` + metadata.
- Universe-coverage gate before simulation: `prefetch_bars` ([engine.py:508-567](../../src/milodex/backtesting/engine.py)), threshold from strategy risk → `risk_defaults.yaml backtesting:` → 0.80.
- Risk policy seam: `RiskPolicy.BYPASS` → `NullRiskEvaluator`, `ENFORCE` → `BacktestStructuralRiskEvaluator` ([engine.py:1306-1310](../../src/milodex/backtesting/engine.py), [risk/policy.py:46-86](../../src/milodex/risk/policy.py)). Backtest fills route through the same `ExecutionService` and write the same `trades`/`explanations` tables with `source='backtest'` — this shared table is the root of P0-1 and P1-4 below.

### 3. Promotion

- Gate thresholds: `ACTIVE_PROMOTION_POLICY` ([promotion/policy.py:138-159](../../src/milodex/promotion/policy.py)) — paper gate (Sharpe > 0.0, DD < 25%, ≥30 trades), capital gate (Sharpe > 0.5, DD < 15%). Lifecycle gate is **defined but not enforced** ([policy.py:54-67](../../src/milodex/promotion/policy.py), documented ADR 0052 gap); `--lifecycle-exempt` short-circuits `check_gate` to allowed ([state_machine.py:119-127](../../src/milodex/promotion/state_machine.py)).
- Transition is atomic in the right order: manifest + promotion event in one DB transaction, **then** the YAML `stage:` line rewrite; a YAML-rewrite failure leaves drift the next cycle catches ([state_machine.py:137-238](../../src/milodex/promotion/state_machine.py)).
- Phase-1 live-lock: `PHASE_ONE_BLOCKED_STAGES` ([state_machine.py:52](../../src/milodex/promotion/state_machine.py)) + the runtime `_check_trading_mode` ([evaluator.py:169-179](../../src/milodex/risk/evaluator.py)) dual-lock — both verified present.
- **Verified live:** all 11 paper-stage strategies' current YAML hashes MATCH their latest frozen paper manifests (DB probe, 2026-06-10). No drift anywhere.

### 4. Launch

- CLI: `strategy run` ([cli/commands/strategy.py:150-243](../../src/milodex/cli/commands/strategy.py)) — paper-mode check, stage-vs-mode compatibility (`stage_compat`), eval-symbol collision scan (`live_runner_eval_symbols`, [paper_runner_control.py:65-93](../../src/milodex/strategies/paper_runner_control.py)), then per-strategy `AdvisoryLock` acquire with heartbeat wiring ([strategy.py:195-211](../../src/milodex/cli/commands/strategy.py)).
- GUI: `BenchCommandFacade.propose_start_paper_runner` ([bench.py:956-1089](../../src/milodex/commands/bench.py)) mirrors all CLI preconditions pre-spawn (incl. the PR #218 eval-symbol blocker, [bench.py:2392-2436](../../src/milodex/commands/bench.py)); `submit_start_paper_runner` ([bench.py:1715-1843](../../src/milodex/commands/bench.py)) revalidates, spawns via `PaperRunnerControl.start` (interpreter probe of the real entrypoint, O_EXCL child lock as backstop, per-launch log file — [paper_runner_control.py:255-367](../../src/milodex/strategies/paper_runner_control.py)).
- Runner `__init__` ([runner.py:46-129](../../src/milodex/strategies/runner.py)): loads strategy, **snapshots the risk envelope** (`expected_stage/max_positions/max_position_pct/daily_loss_cap_pct`), orphan-reconciles its own prior open `strategy_runs` rows, appends its open row.

### 5. Run cycle and cadence

- Loop: heartbeat → stop-request check → `run_cycle()` → stop-request check → `sleep(poll_interval)` ([runner.py:168-209](../../src/milodex/strategies/runner.py)). Poll interval: explicit arg > YAML `tempo.poll_interval_seconds` > bar-size default (1D=60s, 5Min=10s) ([runner.py:574-591](../../src/milodex/strategies/runner.py)).
- `run_cycle` ([runner.py:211-303](../../src/milodex/strategies/runner.py)):
  - Daily + market open → no-op (by design).
  - Market closed + watermark advanced → cheap no-op (no fetch). **Intraday never arms this gate — see P1-1.**
  - Fetch universe bars over `max(365, max_int_param×3)` days ([runner.py:492-499](../../src/milodex/strategies/runner.py)); `already_seen` dedup against `_last_processed_bar_at`; prior-session-bar decline for daily ([runner.py:247-252](../../src/milodex/strategies/runner.py)).
  - Build context: **positions and entry_state from the strategy-scoped event-store ledger** (`strategy_positions` / `strategy_open_lots`, [runner.py:475-502](../../src/milodex/strategies/runner.py)) — ADR 0055; account equity from broker.
  - Evaluate; daily post-close intents gated behind the two-identical-fetches lockin stability window ([runner.py:381-421](../../src/milodex/strategies/runner.py)).
  - Per-bar intent dedup (`_processed_intent_keys`, key = bar-ts + symbol + side); submit each intent via `ExecutionService.submit_paper` with the bound envelope stamped onto the `TradeIntent` ([runner.py:504-528](../../src/milodex/strategies/runner.py)).

### 6. Risk gate

- Single chokepoint: `ExecutionService._evaluate` ([service.py:301-450](../../src/milodex/execution/service.py)) assembles `EvaluationContext` — broker positions/orders/account, reconciliation readiness, kill-switch state, **active operator risk profile** (`load_active_risk_profile`, ADR 0054 ceilings at [risk/config.py:45-51](../../src/milodex/risk/config.py)), frozen-manifest hash keyed off the runner-bound stage ([service.py:361-387](../../src/milodex/execution/service.py)).
- 14 checks, fail-closed wrapper ([evaluator.py:98-157](../../src/milodex/risk/evaluator.py)): kill_switch, trading_mode, reconciliation readiness (with reducing-sell exemption), strategy stage, manifest drift (RuntimeError-loud on missing plumbing), market hours, data staleness, daily loss (incl. kill-switch threshold), order value, single position, total exposure (counts in-flight BUY notional), account-scoped concurrent positions (counts in-flight BUYs), per-strategy concurrent positions (via trades-history attribution), duplicate order (broker window + durable event-store backstop).
- Per-strategy caps clamp `min(global, per-strategy)` from the runner-bound envelope ([evaluator.py:678-710](../../src/milodex/risk/evaluator.py)); the per-strategy slot cap deliberately unclamped (ADR 0029 D6).

### 7. Execution → broker

- Allowed → `broker.submit_order`; `OrderRejectedError`/`InsufficientFundsError` caught → REJECTED result, runner survives (fix `1ab44c8`, verified in code at [service.py:155-174](../../src/milodex/execution/service.py); the 2026-06-03 `crashed:OrderRejectedError` run #104 predates it).
- Market-only enforcement at normalize time ([service.py:461-462](../../src/milodex/execution/service.py)); every decision (submit/blocked/preview/no-action) writes an `ExplanationEvent` + `TradeEvent` pair ([service.py:535-640](../../src/milodex/execution/service.py)).

### 8. Close / stop

- Exit paths all funnel through `shutdown(mode=...)` ([runner.py:305-353](../../src/milodex/strategies/runner.py)): controlled stop (file-based request, BOM-tolerant parse, invalid-preserved-as-`.invalid` — [paper_runner_control.py:101-165](../../src/milodex/strategies/paper_runner_control.py)), SIGINT dialog (c/k/n; double-SIGINT = kill switch), kill switch (cancels all orders + activates store), crash (`crashed:<repr>` stored verbatim), orphan recovery (startup self-reconcile + GUI bootstrap reaper with double TOCTOU guard — [orphan_reconciliation.py:78-157](../../src/milodex/strategies/orphan_reconciliation.py)).
- **Verified live:** all-time exit taxonomy is clean — `controlled_stop` 65, orphan variants 35, `kill_switch` 4, crashes 3, open 3 (the currently-running fleet). The taxonomy works in practice.

---

## Findings

Severity: P0 = wrong trades / position-truth corruption; P1 = systemic operational defect; P2 = correctness/semantics gap, bounded; P3 = hygiene.

### P0-1 — Backtest trades contaminate the per-strategy position ledger (ADR 0055)

**Where:** [risk/attribution.py:245-260](../../src/milodex/risk/attribution.py) (`_fetch_submitted_trade_rows_for_strategy`) and [attribution.py:269-291](../../src/milodex/risk/attribution.py) (`_fetch_submitted_trade_rows`). Both filter `WHERE strategy_name = ? AND status = 'submitted'` — **no `source = 'paper'` predicate**. The backtest engine writes `trades` rows with `source='backtest'`, `status='submitted'`, and the real `strategy_name` through the same `ExecutionService` ([service.py:613-640](../../src/milodex/execution/service.py)), into the same `data/milodex.db`.

**Effect chain:** `strategy_positions`/`strategy_open_lots` fold backtest fills as if they were live paper holdings → the runner's `context.positions` and `entry_state` ([runner.py:475-502](../../src/milodex/strategies/runner.py)) are fiction → daily strategies emit huge phantom SELL exits every close, and because exits short-circuit entry evaluation ([daily_cross_sectional.py:134-144](../../src/milodex/strategies/daily_cross_sectional.py)), they **never reach entry logic at all**. `attribute_position` is equally contaminated, so the per-strategy concurrent-positions risk check ([evaluator.py:518-620](../../src/milodex/risk/evaluator.py)) reasons over polluted ownership.

**Live evidence (DB, 2026-06-10):**
- `momentum.daily.tsmom.curated_largecap.v1` blocked submits at 20:01 UTC: `sell NVDA x1341` (est. $269,044 on $101,154 equity), `sell WMT x884` ($106,575), `sell PG x360`, `sell SLV x744`, `sell SMH x38`, `sell XOM x84`.
- The contaminating rows are unambiguous: `trades` rows for tsmom/NVDA dated 2026-05-05 with `source=backtest, status=submitted, broker_status=filled`.
- Every daily strategy in the bank carries phantom nets (atr_channel: XLC ×3092; donchian: XLU ×1319; rsi2pullback: NFLX ×1714, VZ ×858; 52w_high: MDLZ ×1126; etc.). ~40,438 `status='submitted'` rows exist, the overwhelming majority backtest fills.
- Reconciliation stays **clean** through all of this because *its* fold filters `trade.source != "paper"` ([reconciliation.py:650-651](../../src/milodex/operations/reconciliation.py)) — the two folds disagree by construction.

**The dangerous interaction:** today the phantom exits are stopped only by `market_closed` (and, for the big ones, `max_order_value_exceeded`). A phantom exit small enough to pass the 15% order-value cap — XOM ×84 at $12.6k vs the $15.2k limit — fails **only** `market_closed`. If P0-2 is fixed first, these sells reach Alpaca, and a SELL without a broker position opens a short on the margin (multiplier=2) paper account. **Sequence the fixes: this one first.**

**Fix shape:** mirror `fold_positions` semantics in the attribution queries — `AND source = 'paper'`, plus latest-status-per-`broker_order_id` reversal so corrective terminal rows appended by `sync_local_only_orders` ([reconciliation.py:382-403](../../src/milodex/operations/reconciliation.py)) actually close ledger lots (today the strategy fold ignores them: a submitted-then-cancelled order stays a phantom fill forever). Note `meanrev.rsi2.intraday.spy.v1`'s SPY net=13 is the *separate, documented* ADR 0055 sibling-sell divergence, not this bug — the fix must not "solve" that one accidentally without a decision.

### P0-2 — The daily pipeline structurally cannot execute (known gap; mechanism now fully mapped)

**Where:** three mutually-reinforcing rules.
1. Daily runners are a no-op while the market is open ([runner.py:230-231](../../src/milodex/strategies/runner.py)) and evaluate only post-close after lockin — by design.
2. `_check_market_open` blocks any non-preview submit when the market is closed ([evaluator.py:328-338](../../src/milodex/risk/evaluator.py)).
3. Even if (2) were relaxed: `_check_data_staleness` caps bar age at `max_data_staleness_seconds: 300` ([evaluator.py:340-365](../../src/milodex/risk/evaluator.py), [risk_defaults.yaml:61](../../configs/risk_defaults.yaml)), and the bar it checks is `get_latest_bar` — the latest IEX **minute** bar ([alpaca_provider.py:272-288](../../src/milodex/data/alpaca_provider.py)) — so anything later than ~16:05 ET fails staleness too.

**Live evidence:** last 7 days: 36 blocked submits, **all** carrying `market_closed`; 0 daily-strategy fills; 30 submitted trades all from intraday strategies. This matches the 2026-06-10 memory note, and the audit adds: most of what is being blocked is P0-1 phantoms anyway — the *legitimate* daily flow has both an execution-timing problem and a position-truth problem stacked.

**Fix shape (design decision, not a patch):** daily post-close decisions need an execution semantic — either (a) queue intents for next-open submission (TIF=day order submitted pre-open, with a re-validation pass at submit time), or (b) evaluate near the close (e.g. 15:50 ET on the forming daily bar) accepting close-approximation risk. Either way the staleness check needs a tempo-aware policy (300 s is an intraday number applied to a daily pipeline). Both touch the risk layer's market-hours stance — per the risk-layer rules, this is an operator-approved policy change, not a convenience bypass.

### P1-1 — Intraday runners have no bar watermark: per-cycle evaluation, 24/7 fetch churn, explanation bloat

**Where:** `_last_processed_bar_at` is only ever set inside `_maybe_advance_lockin_watermark` ([runner.py:406,418](../../src/milodex/strategies/runner.py)), which is only called on the daily path ([runner.py:264-269](../../src/milodex/strategies/runner.py)). Consequences for any intraday runner:
- The `already_seen` short-circuit ([runner.py:240-243](../../src/milodex/strategies/runner.py)) never fires → full fetch + evaluate + record every poll cycle (10 s for 5Min).
- The closed-market early-out ([runner.py:232-235](../../src/milodex/strategies/runner.py)) requires a non-None watermark → **never fires** → the runner fetches Alpaca, calls `get_account()` (twice per no-action cycle: [runner.py:254](../../src/milodex/strategies/runner.py) + [service.py:265](../../src/milodex/execution/service.py)), and writes a `no_signal` explanation every 10 s all night, all weekend.
- Each cycle's fetch re-reads and (when new bars exist) rewrites the full ~19k-row 365-day parquet ([alpaca_provider.py:243-254](../../src/milodex/data/alpaca_provider.py)) — `_history_window_days` has no bar-size awareness ([runner.py:492-499](../../src/milodex/strategies/runner.py)).

**Live evidence:** 27,508 `no_trade/no_signal` explanations in the last 7 days — consistent with the 6/8→6/9 28-hour soak (ORB at 10 s ≈ 10k cycles alone) plus daily runners pre-lockin. That is audit-noise drowning the signal the explanations table exists to provide, plus unbounded DB growth and a four-API-calls-per-10s overnight burn per runner.

**Fix shape:** advance a per-bar watermark on the intraday path (the bar timestamp is already in hand), and arm the closed-market early-out for intraday once the session's last bar is processed. The in-memory `_processed_intent_keys` dedup remains as the submission backstop.

### P1-2 — Intraday strategies evaluate the *forming* bar: live/backtest semantics diverge

**Where:** the provider always re-fetches today ([alpaca_provider.py:142-145](../../src/milodex/data/alpaca_provider.py)), so the latest 5Min bar the strategy sees mid-window is the in-progress bar (start-of-bar timestamped, mutating until the window closes). With P1-1's per-cycle evaluation, a strategy can fire on an intra-bar transient; the intent dedup key is the bar timestamp ([runner.py:441-448](../../src/milodex/strategies/runner.py)), so once fired it cannot be retracted when the bar finishes differently. The backtest engine, by contrast, decides strictly on **completed** bars with T+1-open fills (advance→evaluate→drain, `intraday_simulation.py`). Daily got the lockin stability window precisely for this problem; intraday got nothing.

**Fix shape:** evaluate only bars whose window has closed (timestamp + bar_size ≤ now), which composes naturally with the P1-1 watermark. This makes live intraday behavior match the simulated contract the strategies were promoted on.

### P1-3 — Multi-day runner sessions silently degrade to exit-only at the NY-date rollover

**Where:** `latest_readiness` requires the latest persisted reconciliation run to be from **today's** NY trading day ([reconciliation.py:581-592](../../src/milodex/operations/reconciliation.py)); the runner reconciles exactly once per session ([runner.py:355-362](../../src/milodex/strategies/runner.py)); `_check_reconciliation_readiness` fails closed for exposure-increasing intents ([evaluator.py:181-209](../../src/milodex/risk/evaluator.py)). Meanwhile the advisory-lock layer explicitly blesses leaving runners up all day/overnight ([advisory_lock.py:34-61](../../src/milodex/core/advisory_lock.py)). Day 2 of any runner session: every BUY is blocked `reconciliation_stale`; sells pass. Nothing re-runs reconciliation automatically.

**Fix shape:** the runner re-runs reconciliation when the NY trading day of its last run differs from today (cheap check at cycle top, reusing `_ensure_startup_reconciliation` machinery).

### P1-4 — Concurrent backtests can spuriously veto live paper submissions (duplicate-order backstop unscoped)

**Where:** `count_recent_submitted_orders` ([event_store.py:856-876](../../src/milodex/core/event_store.py)) counts `status='submitted'` rows with **no `source` filter**. Backtest fills are stamped `recorded_at = wall-clock now`, so a backtest running on (say) SPY while an SPY intraday runner is live makes the runner's duplicate-order check ([evaluator.py:648-674](../../src/milodex/risk/evaluator.py)) see thousands of "recent submitted orders" inside the 60 s window → legitimate paper intents blocked `duplicate_order_window`. Same root cause as P0-1 (runtime queries against the shared `trades` table without `source='paper'` scoping). Secondary: the method loads *all* submitted rows for the symbol and filters in Python — that is a per-intent full-partition scan that grows with every backtest.

**Fix shape:** `AND source = 'paper'` plus pushing the time window into SQL.

### P2-1 — `risk.stop_loss_pct` is dead plumbing; the live stop is a different field, checked only at bar cadence

- The loader **requires** `risk.stop_loss_pct` in every strategy YAML ([loader.py:218-224](../../src/milodex/strategies/loader.py)); it is loaded into `StrategyExecutionConfig.stop_loss_pct` ([execution/config.py:27,44](../../src/milodex/execution/config.py)) and then **consumed by nothing** (repo-wide grep: zero runtime readers).
- The stop that actually fires is `parameters.stop_loss_pct`, evaluated inside each strategy on the latest close (e.g. [meanrev_rsi2_pullback.py:289-302](../../src/milodex/strategies/meanrev_rsi2_pullback.py)). Two same-named fields in every YAML can silently diverge; today they happen to match.
- Semantics worth stating plainly: for a daily strategy the stop is checked **once per day on the close** — intraday drawdown through a stop level is invisible until the close, and no broker-side stop order exists (Phase 1 is market-only, ADR 0013). That may be acceptable Phase-1 policy, but it should be a documented decision, not an artifact.
- **Action:** either make the risk layer consume `risk.stop_loss_pct` (e.g. as a runner-bound envelope cross-check against the parameter) or remove it from the required-key set and the configs; document bar-cadence stop semantics in RISK_POLICY.

### P2-2 — `reducing_allowed_during_kill_switch: true` is YAML fiction

[risk_defaults.yaml:63-68](../../configs/risk_defaults.yaml) promises "Sells that close or shrink positions remain allowed even during an active kill switch." The code disagrees: `_check_kill_switch` ([evaluator.py:159-167](../../src/milodex/risk/evaluator.py)) blocks unconditionally when active — only the *reconciliation* gate has the reducing-sell exemption ([evaluator.py:188-193](../../src/milodex/risk/evaluator.py)). `_check_market_open` and `_check_order_value` also apply symmetrically to exits (the order-value cap blocking a *legitimate* large exit is a real failure mode — it is blocking the phantom exits today, [§P0-1 evidence]). Same doctrine-vs-code class the 2026-05-29 audit flagged; this instance is in the machine-readable config itself. **Action:** decide the policy (kill switch as absolute halt is defensible; the YAML comment is not) and make YAML, RISK_POLICY R-EXE-016, and code agree.

### P2-3 — Per-strategy `daily_loss_cap_pct` measures the *account's* daily P&L

`_check_daily_loss` computes loss from `account.daily_pnl` ([evaluator.py:367-394](../../src/milodex/risk/evaluator.py); `equity − last_equity`, [alpaca_client.py:256-270](../../src/milodex/broker/alpaca_client.py)) and compares it to `min(global, per-strategy)` ([evaluator.py:678-690](../../src/milodex/risk/evaluator.py)). With N strategies on one account, a strategy with a 2% cap is blocked when the *account* is down 2% — even if that strategy is flat. Conservative direction, but the per-strategy cap does not mean what its name says, and no per-strategy P&L attribution exists to make it mean that. **Action:** document the actual semantics now; per-strategy P&L attribution is a capital-gate item.

### P2-4 — `max_trades_per_day` is loaded but enforced nowhere

`RiskDefaults.max_trades_per_day` ([risk/config.py:79,101](../../src/milodex/risk/config.py)) has no consumer — there is no trades-per-day check in `RiskEvaluator._CHECKS` ([evaluator.py:98-113](../../src/milodex/risk/evaluator.py)). Unlike the sector/correlation caps, this one is **not** in RISK_POLICY's "Known limitations" list ([RISK_POLICY.md:229-236](../../docs/RISK_POLICY.md)) — it reads as enforced. The "prevents runaway logic" rationale ([risk_defaults.yaml:50-51](../../configs/risk_defaults.yaml)) is exactly the failure mode P1-1/P1-2 make more plausible. **Action:** enforce it (the durable trades table makes the count a one-query check) or add it to the known-limitations list.

### P2-5 — Risk-triggered kill switch does not cancel open orders; threshold only fires on attempted trades

- The operator SIGINT `k` path cancels all orders then activates ([runner.py:326-327](../../src/milodex/strategies/runner.py)); the risk-threshold path (`_maybe_activate_kill_switch`, [service.py:531-533](../../src/milodex/execution/service.py)) only activates the store. Phase-1 market-only makes lingering open orders rare, but the asymmetry contradicts "halt all trading."
- The daily-loss/kill-switch threshold is only evaluated inside `evaluate()` — a catastrophic drawdown with no intent in flight trips nothing until the next submission attempt. The runner has no kill-switch awareness mid-session either: it keeps polling and gets per-intent blocks. Acceptable for paper; should be on the capital-gate checklist.

### P2-6 — Bench start has a built-in false-error race on audit linkage

`submit_start_paper_runner` queries for the child's open `strategy_runs` row immediately after `Popen` ([bench.py:1795-1826](../../src/milodex/commands/bench.py)) with no grace window; the child needs seconds of interpreter+pandas+alpaca imports before it writes that row. Result: a healthy launch can return `status="error", runner_audit_link_missing` while the runner comes up fine (tests pin this behavior: [test_bench_facade.py:1091-1105](../../tests/milodex/commands/test_bench_facade.py)). The operator sees a failed start that didn't fail. **Action:** bounded retry (e.g. up to ~15 s, matching the interpreter-probe timeout) before declaring the linkage missing.

### P3 — Hygiene cluster

1. **Contradictory config comments.** [meanrev_daily_rsi2pullback_v1.yaml:53-59](../../configs/meanrev_daily_rsi2pullback_v1.yaml): comment says "Demoted from paper → backtest … frozen v1 manifest still references universe.phase1.curated.v1" directly above `stage: "paper"` — and the DB shows the manifest re-frozen 2026-05-07 and MATCHing. [meanrev_rsi2_intraday_spy_v1.yaml:14](../../configs/meanrev_rsi2_intraday_spy_v1.yaml): header says "STAGE = backtest. NOT a paper candidate" above `stage: "paper"`. Comments aren't hashed, so drift checks can't catch this — only an operator can.
2. **Dead validation branches.** `_normalize_intent` raises `UnsupportedOrderTypeError` for anything non-market ([service.py:461-462](../../src/milodex/execution/service.py)), making the limit/stop price checks at [service.py:464-475](../../src/milodex/execution/service.py) unreachable.
3. **Daily eval window closes at UTC midnight.** `_is_current_session_bar` compares the bar's UTC date to `_now().date()` UTC ([runner.py:367-379](../../src/milodex/strategies/runner.py)): from 00:00 UTC (20:00 ET during EDT) today's close bar reads as "prior session" and is declined. A daily runner started between ~8 pm and midnight ET silently skips that day's evaluation. Related inconsistency: `_fetch_bars_by_symbol`/`_build_entry_state` use local `date.today()` ([runner.py:465,481](../../src/milodex/strategies/runner.py)) while the session check uses UTC.
4. **Per-intent disk/config I/O.** Each evaluation re-reads the strategy YAML twice (`compute_config_hash` + `_load_strategy_config`) and the risk-defaults+profile overlay YAMLs ([service.py:317,349-352,408-411](../../src/milodex/execution/service.py)). Irrelevant at daily tempo; measurable at intraday cadence. Cache-by-mtime would do.
5. **`_estimate_unit_price` uses `max()` of candidates** ([service.py:518-524](../../src/milodex/execution/service.py)) — conservative for BUY caps, but inflates SELL order values toward the fat-finger cap (interacts with P2-2's exit-blocking concern).
6. **IEX-only data fidelity.** All live and cached bars ride `DataFeed.IEX` ([alpaca_provider.py:198,276](../../src/milodex/data/alpaca_provider.py)) — a small fraction of consolidated tape volume. The per-bar `vwap` column and volume-derived signals (session VWAP strategies compute from typical-price×volume, [_session_intraday.py:238-282](../../src/milodex/strategies/_session_intraday.py)) systematically deviate from consolidated VWAP. Acceptable for paper; a stated data-fidelity caveat for any intraday promotion case.
7. **`collect_runner_statuses`/orphan sweep load the full `strategy_runs` table** ([runner_status.py:188](../../src/milodex/strategies/runner_status.py), [orphan_reconciliation.py:67-68](../../src/milodex/strategies/orphan_reconciliation.py)) — fine at 110 rows; one `ORDER BY id DESC LIMIT` away from immune.

---

## What's solid (verified, leave alone)

- **Advisory lock stack** ([advisory_lock.py](../../src/milodex/core/advisory_lock.py)): O_EXCL acquire, per-cycle heartbeat, 12 h age fallback with recycled-PID rationale, process-start-time identity verification, single shared `live_lock_holder` consumed by CLI/GUI/reaper alike. The 2026-05-29 audit's liveness-consolidation item is landed and real.
- **Exit taxonomy + orphan handling**: every exit path funnels through idempotent `shutdown()`; startup self-reconcile and the double-guarded GUI reaper both verified in DB history (35 orphan closures, correct reasons, zero stuck rows today).
- **TOCTOU envelope binding**: stage and risk caps bound once at runner start, stamped per-intent, consumed by manifest-drift/stage/caps checks end-to-end ([runner.py:504-528](../../src/milodex/strategies/runner.py) → [evaluator.py:61-73,228,284](../../src/milodex/risk/evaluator.py) → [service.py:374-387](../../src/milodex/execution/service.py)).
- **Fail-closed evaluator** wrapper, in-flight BUY accounting in exposure/slot caps, the durable duplicate backstop *concept* (modulo P1-4's scoping), manifest freeze/drift integrity (11/11 MATCH), the promotion state machine's durable-log-first ordering, and the controlled-stop request hardening (PR #217) all check out.
- **Bench propose→revalidate→submit** pattern with structured blockers, and the pre-spawn eval-symbol collision mirror (PR #218).

---

## Recommended action order

| # | Action | Size | Finding |
|---|--------|------|---------|
| 1 | Scope attribution + duplicate-backstop queries to `source='paper'`; add terminal-status reversal to the strategy fold | small | P0-1, P1-4 |
| 2 | Intraday bar watermark + completed-bar-only evaluation + closed-market early-out | small | P1-1, P1-2 |
| 3 | Daily execution semantics design (queue-at-open vs near-close eval; tempo-aware staleness) — **after #1** | decent, needs a design doc | P0-2 |
| 4 | NY-day rollover re-reconciliation in the runner loop | tiny | P1-3 |
| 5 | Kill-switch/reducing-order policy reconciliation (YAML ↔ RISK_POLICY ↔ code) | tiny | P2-2, P2-5 |
| 6 | Dead-plumbing sweep: `risk.stop_loss_pct`, `max_trades_per_day`, `_normalize_intent` branches, stale config comments | tiny | P2-1, P2-4, P3-1/2 |
| 7 | Bench start linkage grace window | tiny | P2-6 |
| 8 | UTC-midnight daily-eval window + local/UTC date consistency | tiny | P3-3 |

Items 1–2 restore position-truth and cadence sanity for the running fleet without any policy decisions. Item 3 is the only one requiring an operator-approved risk-policy change and should ride its own design doc.

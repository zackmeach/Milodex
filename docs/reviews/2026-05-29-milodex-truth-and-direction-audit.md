# Milodex — Fresh-Eyes Truth & Direction Audit

**Date:** 2026-05-29
**Scope:** End-to-end truth audit and direction audit of the Milodex repository at `master` (`f3063a1`).
**Method:** Fresh-eyes reconstruction of purpose/safety/runtime/data models from the repo, followed by a five-domain deep review (runtime, risk/execution, data/reconciliation, bench/GUI, docs/ADR/test), adversarial verification of every S3/S4 finding, and a cross-cutting synthesis. The two most severity-determining claims (the intraday fleet's symbol universe; the absence of sector/correlation checks) and the single most load-bearing invariant (no broker order without risk evaluation) were re-verified by hand against source.
**Posture:** Read-only. No runtime code, config, migration, or state was modified. This report is the only artifact created.

---

## Operator Decision Summary

**1. Is Milodex directionally sound?** Yes, qualified. For its actual goal — a credible, legible, single-operator paper-and-research harness that earns *justified trust* before any capital — the architecture is sound and appropriately scaled. The primitive set (broker-as-arbiter, per-process runners, per-strategy advisory lock, append-only SQLite event store, single risk chokepoint) is the minimal correct one, and the team has repeatedly hardened existing seams rather than building an orchestrator or swapping the database after incidents. The qualification is two-sided: the system is being soak-tested with an intraday GUI-spawned fleet whose premises strain ADR 0026's accepted concurrency tradeoff, and the doctrine layer (RISK_POLICY.md / SRS) overstates enforcement in two places. Both are seam-strengthening and doc-trimming jobs, not redesigns.

**2. Overbuilt, underbuilt, or appropriately built?** **Mixed, leaning appropriate.** The architectural skeleton is appropriately built with no architecture theater and consciously bounded scope. Two seams ADR 0026 explicitly deferred are *underbuilt*: (a) process-liveness is implemented three times at three different rigor levels and the operator-trust surfaces ride the weakest one; (b) the risk caps count only filled positions, not in-flight orders. One subsystem (the ADR 0040 durable job ledger) is mildly *over-claimed* relative to what it actually does.

**3. Top 5 to harden next** (detail in Part 10):
1. Consolidate process-liveness into one shared identity-verified helper; route the bench stop path, ActiveOps badge, and duplicate-start check through it.
2. Make pending/open broker orders consume concurrent-position and total-exposure slots (a same-process correctness gap today, no concurrency required).
3. Bound the `ActivityFeedState` SELECTs in SQL (`ORDER BY … LIMIT`) and index the paper-scope predicate — kill the re-appearing OOM anti-pattern.
4. Tighten the orphan reaper's holder-recheck→unlink window so one snapshot atomically guards both the row-close and the lock unlink.
5. Trim the doctrine layer to what the code enforces (sector/correlation caps, strategy-level kill switch, ADR 0008's check count).

**4. Top 5 NOT to build yet:**
1. An inter-process execution lock / position-reservation table for the **paper** path.
2. Postgres or any database swap.
3. A single-process multi-tenant runner / supervisor / control plane.
4. A per-strategy kill switch.
5. A caching layer / materialized view for read models.

**5. Hard gates before micro_live / live:**
- Close the evaluate→submit cap race for the **capital-bearing path only** (per-account lock around read→submit; paper stays lock-free).
- Pending/open orders **must** consume position and exposure slots.
- ADR 0026 addendum that re-litigates the accepted race for the intraday / GUI-async-spawn mode and states the bound explicitly.
- Doc-vs-code reconciliation complete — no RISK_POLICY/SRS invariant advertised as an enforced hard stop unless it fires in code.
- Controlled-stop and duplicate-start use process-identity-verified liveness.
- The micro_live/live dual-lock (`state_machine.py` `PHASE_ONE_BLOCKED_STAGES` + `evaluator.py` `_check_trading_mode`) stays enforced and tested throughout.

**6. Biggest false alarm / stop worrying:** The "no lock across evaluate→submit" item read as a smoking gun but is **not** an in-process bug — the chokepoint holds (`service.py:131`→`:144` is the sole non-broker `submit_order` callsite). The real exposure is the *cross-process, same-symbol* window, which is paper-only and self-correcting on the next cycle. Relatedly, the reaper residual-1 TOCTOU guard and the shared (account-scoped) kill switch are correct-by-design, not gaps.

**7. Biggest direction-level risk:** Silently inheriting ADR 0026's daily-tempo race acceptance into micro_live. Its three premises — *daily tempo, manually-attended, evaluations happen sequentially when the operator starts each runner* (ADR 0026:67) — are all weakened by an intraday ~10s-cadence fleet and ADR 0051 Phase F GUI async-spawn. Paper-safe only because the dual live-lock holds; it becomes real overexposure the moment that lock lifts.

**8. Biggest implementation-level risk:** The three-way liveness divergence with no single owner. `bench._peek_runner_lock` returns a bare `current_holder()` with no PID-existence or start-time-identity check, while `orphan_reconciliation` owns the strongest discriminator. The operator-trust surfaces ride the weakest, so "Stop Trading" against a hard-killed-but-lock-present runner can return a confidently wrong "submitted."

**9. Biggest test gap:** Concurrency-as-behavior. Coverage is heavy on rule-units and thin on the incident class — no test for two evaluators sharing pre-fire broker state and both submitting, none pinning pending-order-counts-toward-cap, no intraday-fleet bounded-memory soak, none for the reaper recheck→unlink window, and controlled-stop-against-dead-runner is untested. The wrong posture to carry into a hardening phase a concurrency incident triggered.

**10. Overall confidence:** Architecture direction **high** · Runtime lifecycle **high** · Risk correctness under concurrency **medium** · Data/reconciliation **high** · GUI/operator safety **medium** · Promotion readiness toward micro_live/live **high**.

> **One-line verdict:** Milodex is pointed in the right direction and built at the right altitude for a paper harness; the work that remains is to harden the two concurrency seams ADR 0026 deferred and to make the docs tell the truth the code already lives — *before*, not after, the live lock is ever considered for lifting.

---

## Part 1 — First-Principles Direction Audit

### 1. What is Milodex actually trying to become?

Reconstructed from the repo, not assumed: Milodex is an **operator-governed automation harness for investment techniques** (`docs/FOUNDER_INTENT.md`), explicitly *not* "an AI trading bot." The decision layer (a rule, config, ML model, or agent) owns *what to trade*; the harness owns *whether it is allowed, under what evidence, with what risk veto, with what human approval*. The founder's priority order is fixed (`VISION.md`): (1) build something real, functional, trustworthy; (2) demonstrate AI-assisted engineering capability; (3) accessibility; (4) shareability; (5) profitability as validation. Within that, the system must be excellent at **evaluating** strategies first, **monitoring** second, **executing** third, **discovering** last. The Phase 1 win condition is *not* "the strategy makes money" — it is "the platform can evaluate, gate, monitor, and manage a strategy in a disciplined, trustworthy way end-to-end." Live capital is gated behind human approval at every consequential transition; paper-only is the current operating envelope (ADR 0004), and micro_live/live are *locked in code*, not just convention.

This goal frame matters because it changes what "right-sized" means. The correct success criterion is **legibility, safety, and credibility**, not throughput or latency or platform breadth. Judged against that, simplicity that an operator can `ps` and read is a feature, not a deficiency.

### 2. Designing from scratch today, would you choose the current approach?

Subsystem by subsystem, with the goal above as the yardstick:

| Choice | Verdict |
|---|---|
| Python + PySide6/QML desktop | Strong; matches local-first, single-operator, audit-heavy register. |
| SQLite event store | Strong; correctly tuned (WAL + `busy_timeout=30000` + `BEGIN EXCLUSIVE` migrations). |
| Append-only event/audit model | Strong; intact for the rows that matter (live trades, explanations, promotions). |
| Per-process strategy runners | Strong for paper; the more inspectable model (ADR 0026). |
| Broker-as-live-source-of-truth | Correct for paper; **questionable for capital** at the evaluate→submit seam. |
| Local-first architecture | Strong; consistent with intent. |
| YAML strategy configs | Strong; config/code separation holds (ADR 0003). |
| Bench/GUI command bridge | Strong; "GUI never mutates business state directly" holds. |
| Promotion pipeline backtest→paper→micro_live→live | Strong; dual-enforced live lock is the hardest gate in the system. |
| Research/execution/operator-UI separation | Strong; clean seams. |
| Reconciliation model (explicit) | Strong; operator-owns-recovery is the right posture. |
| Risk evaluator as pre-broker chokepoint | **Strong and verified** — sole non-broker `submit_order` callsite. |

### 3. Classification of major directional choices

- **Strong; keep and double down:** the risk chokepoint, the dual-enforced live lock, the per-process runner model, SQLite-as-event-store, the GUI command-bridge boundary, the explicit reconciliation + liveness-gated bookkeeping reaper.
- **Reasonable tradeoff; keep but document limits:** broker-as-arbiter for caps (covers position/exposure, does *not* cover in-flight orders or the read→submit window).
- **Correct for paper, questionable for live:** the evaluate→submit concurrency acceptance (ADR 0026) and pending-order-blindness in the caps.
- **Becoming strained; needs targeted hardening (not redesign):** process-liveness, currently three implementations with no single owner.
- **Wrong direction; stop investing:** none found.
- **Unknown; needs evidence:** the intraday fleet's behavior under sustained concurrency (the soak is the experiment; instrument it rather than guess).

### 4. Local optimization traps

One real trap exists in the risk layer: every mitigation added since ADR 0026 (durable dedup backstop, runner-bound TOCTOU envelope, per-strategy attribution) hardens the **same-symbol** and **config-drift** surfaces while leaving the **cross-process different-symbol cap race** and **pending-order blindness** untouched — the two surfaces that actually scale badly with concurrency. The layer is being polished where it was already strong. (Verified that the intraday fleet is SPY-only, which keeps even this trap paper-bounded today — see Part 5.)

### 5. Architecture theater

Almost none. The ADR record is unusually disciplined (every phase closed with an ADR, every drift named, addenda appended rather than rewritten). The one place where the *framing* outruns the *substance* is ADR 0040's durable job ledger: the ADR frames `orchestration_jobs` as the audit spine for "every bench gesture," but in practice it journals only backtest and runner submits, best-effort (exceptions swallowed), and the three state-mutating governance families (demote/freeze/promote) write no job row. The governance event rows still carry the authoritative audit, so this is over-claim, not a hole — but a reader trusting ADR 0040 would over-estimate coverage.

### 6. Missing core primitives

One genuinely missing primitive: **process-identity-verified liveness** ("a lock holder reported as live is start-time-verified"). It exists today as `orphan_reconciliation._has_live_runner` (PID-existence + process-start-time identity), but `paper_runner_control._existing_live_runner` (PID + exists only) and `bench._peek_runner_lock` (bare `current_holder()`, no liveness) re-implement weaker versions, and no single layer owns the invariant. This is a *consolidation*, not a new layer — see Part 9.

### 7. Premature platform work

None observed. The team has actively resisted premature platform-building: ADR 0026 rejected the multi-tenant supervisor; the OOM incident was answered by bounding reads, not by Postgres; the bench facade re-uses existing governance callees rather than forking a parallel GUI lifecycle. This restraint is one of the project's strongest signals.

### 8. Future live-capital blockers

Two design choices acceptable for paper become unsound before micro_live: (a) caps that count only filled positions (in-flight orders become real overexposure once fills are real money), and (b) the unsynchronized read→submit window across processes. Both are listed as hard gates. A third is doctrinal: any RISK_POLICY/SRS invariant advertised as enforced but absent in code (sector/correlation caps) must not be carried into a live-readiness argument.

---

## Direction Verdict

**A. Is Milodex directionally sound?** Yes, qualified. Right shape, right altitude for a single-operator paper harness aiming at justified trust.

**B. Overbuilt, underbuilt, or appropriately built?** Mixed, leaning appropriate. Skeleton appropriate; two concurrency seams underbuilt; one ledger over-claimed.

**C. Patching around fundamentally poor decisions?** No. The patches (streaming reads, runner-bound envelope, durable dedup backstop, residual-1 reaper guard) are correct seam-strengthening of a sound design. The only "patch smell" is that risk hardening keeps landing on the already-strong surfaces.

**D. What to stop building:** the five "do not build yet" items — paper-path execution lock, Postgres, multi-tenant supervisor, per-strategy kill switch, read-model cache.

**E. What to simplify:** collapse three liveness implementations into one; replace the Python-side feed cap with SQL `LIMIT`; trim doctrine to enforced reality.

**F. What to harden before expanding:** pending-order accounting, the cross-process capital-path race, the reaper unlink window, and the concurrency test battery.

**G. What to preserve because it works:** the risk chokepoint, the dual live-lock, the per-process model, SQLite tuning, the reconciliation/reaper design, the GUI mutation boundary, and the ADR discipline itself.

**H. If starting over today, keep/change/delete:** **Keep** essentially the whole skeleton. **Change** the caps to count in-flight orders and add a capital-path read→submit lock; consolidate liveness. **Delete** nothing structural; delete the *doc claims* that overstate enforcement.

---

## Starting-Over Comparison Table

| Subsystem | Current | Alternative | Why current may be right | Why current may be wrong | Migration cost | Recommendation now | Evidence required before changing |
|---|---|---|---|---|---|---|---|
| Runtime | Per-process runners; operator/launcher supervises; per-strategy advisory lock; liveness-gated reaper (ADR 0026) | Single-process multi-tenant supervisor / orchestrator | Minimal correct primitive set; per-process isolation; `ps`-inspectable; scales to N terminals | Liveness implemented 3× at different rigor; operator-trust surfaces ride the weakest | low | **Keep.** Consolidate liveness into one helper; do not orchestrate. | An invariant the per-process model provably can't protect with a file-lock + broker change (none surfaced) |
| Storage | One SQLite event store; WAL + `busy_timeout=30000` + `BEGIN EXCLUSIVE` migrations (ADR 0011) | Postgres with real concurrent writers | Write serialization proven by cross-process tests; OOM was unbounded reads, not engine limits | Nothing the evidence shows; the two data gaps are an unindexed predicate and an unbounded poller — both fixable in SQLite | high | **Keep firmly.** No evidence justifies a swap. | Concurrent-writer contention or query latency surviving indexing + bounded reads at realistic volume |
| Risk chokepoint | Single `ExecutionService._submit`; `_evaluate` precedes `submit_order` unconditionally; fail-closed evaluator; durable dedup backstop | Distributed pre-trade checks / reservation service | Genuine single chokepoint, code-grounded and tested; broker-as-arbiter inspectable | Covers position/exposure but **not** in-flight-order occupancy or the cross-process read→submit window | medium | **Keep the chokepoint.** Add pending-order accounting now; add per-account lock on read→submit for the capital path at the micro_live gate. | A bypass of the chokepoint itself (none found) |
| Truth model | Broker authoritative for live state (positions/orders/balance); event store authoritative for audit/history (ADR 0010) | Event store authoritative for positions too | Avoids a second source-of-truth to reconcile; broker is the real arbiter | Broker reporting lag (PENDING precedes FILLED) is the basis of pending-order blindness | medium | **Keep.** The lag is a caps-accounting fix, not a truth-model change. | A reconciliation class the hybrid model provably can't express |
| Reconciliation | Explicit reconcile + liveness-gated reaper (periodic QTimer + bootstrap + on-demand CLI); compaction/VACUUM under runtime lock, manual | Fully automatic continuous reconciliation + auto position-sync + auto-restart | Operator-owns-recovery (ADR 0026); reaper automates only bookkeeping, never restarts; residual-1 guard sound | Reaper has a residual recheck→unlink window; strongest liveness check not reused | low | **Keep explicit.** Tighten unlink atomicity; resist auto-restart and auto-VACUUM. | A phantom/orphan class the liveness-gated reaper can't close without continuous auto-recon |
| Bench | QML → BenchCommandBridge → BenchCommandFacade → existing governance callees; revalidate-at-submit; forbidden-token QML tests (ADR 0049/0051) | Direct QML/read-model mutation | "GUI never mutates business state directly" holds and keeps QML thin | Job ledger partial/best-effort vs ADR 0040 framing; stale "no mutation" comments; cross-process start race the facade can't close | low | **Keep the facade.** Fix doc drift, broaden journaling, move start-runner to a pre-spawn atomic guard. | A mutation path reaching business state bypassing the facade (none found) |
| Promotion gates | Dual-enforced micro_live/live lock: `PHASE_ONE_BLOCKED_STAGES` (promotion-time) + `_check_trading_mode`/`_check_strategy_stage` (runtime); typed policy (ADR 0052) | Single-point or runtime-only enforcement | Defense-in-depth: a promotion-time bug alone can't reach live; thresholds are typed code, not docs | Nothing structural; risk is doctrine over-claiming enforcement that could be mistaken for a promotion guarantee | low | **Keep both points.** Trim doctrine before citing it as a live-readiness gate. | A promotion path reaching a live order with only one check present (none — both verified) |
| Research/execution split | Shared event-store tables, scoped by `backtest_run_id IS NULL` / `source` predicates | Separate research and execution stores | One store, one audit spine; scoping predicate is cheap and shared | A forgotten predicate leaks backtest rows into paper (the 2026-05-29 leak) | medium | **Keep shared + scoped.** Add a regression test asserting backtest rows never reach a paper read model. | A leak class the shared predicate provably can't prevent |
| Orchestration ledger | `orchestration_jobs` durable record (backtest + runner submits, best-effort) (ADR 0040) | A real control plane that drives execution | Carries real backtest/runner progress no other table holds | Over-claimed as the spine for "every gesture"; not a control plane | low | **Keep as a record.** Document the scope; don't promote it to a control plane. | A recovery requirement the governance event rows can't already satisfy |

---

## Part 2 — System Map

Confidence is the reviewer's assessment after opening the cited files. "Not fully audited" is marked where a file was read but its test suite was not exhaustively traced.

### Strategy runner (run lifecycle)
- **Purpose:** Foreground per-strategy loop — heartbeat the lock, evaluate on a new daily/intraday close, dedup intents by `(bar_ts, symbol, side)`, dual-stop, always close the `strategy_runs` row on a non-SIGKILL exit.
- **Source of truth:** `strategy_runs` (`session_id`, `ended_at`, `exit_reason`) + an in-memory watermark.
- **Forbidden writes:** never calls the broker directly; never bypasses `ExecutionService`.
- **Key invariants:** orphan-reconcile precedes its own row append; daily stale-bar guard (`_is_current_session_bar`); per-cycle in-process dedup.
- **Files:** `src/milodex/strategies/runner.py`, `paper_runner_control.py`. **Tests:** `tests/milodex/strategies/test_paper_runner_control.py`. **Confidence:** high. **Gap:** SIGKILL leaves the row open by design (handled by the reaper).

### ExecutionService (execution chokepoint)
- **Purpose:** The single intent→trade path — evaluate risk, submit to the broker only if allowed, record explanation + trade.
- **Source of truth:** `ExecutionService._submit` / `_evaluate` (`service.py:122-173`, `:279-428`).
- **Key invariant:** **no `broker.submit_order` without a prior `_evaluate`** — verified, sole non-broker callsite at `service.py:144`.
- **Files:** `src/milodex/execution/service.py`. **Tests:** `test_service*.py`. **Confidence:** high. **Gap:** no lock across `_evaluate`(`:131`)→`submit_order`(`:144`); trade row recorded after submit returns.

### RiskEvaluator (the sacred layer)
- **Purpose:** Veto power over every trade — 14 checks (`_CHECKS`, `evaluator.py:98-113`): kill-switch, trading-mode, reconciliation-readiness, stage, manifest-drift, market-open, data-staleness, daily-loss, order-value, single-position, total-exposure, concurrent-positions, per-strategy-positions, duplicate-order.
- **Key invariants:** fail-closed on any check exception (`:115-148`); `allowed = all(passed)`; both account and per-strategy caps must pass.
- **Files:** `src/milodex/risk/evaluator.py`, `attribution.py`, `exposure.py`, `config.py`. **Tests:** `tests/milodex/risk/test_risk_rules.py`, `tests/milodex/execution/test_risk_rules.py`. **Confidence:** high. **Gap:** concurrent-positions (`:448`) and total-exposure (`:426`) count only filled positions; sector/correlation caps documented but absent.

### Broker client / model
- **Purpose:** Abstract broker; the Alpaca file is the only `alpaca-py` importer; `SimulatedBroker` for backtest.
- **Key invariants:** positions returned only after fill (PENDING precedes FILLED); `get_orders` truncates at `limit=100`; sole SDK `submit_order` at `alpaca_client.py:188`.
- **Files:** `src/milodex/broker/{client,alpaca_client,simulated,models}.py`. **Tests:** `test_alpaca_client.py`. **Confidence:** high. **Gap:** `get_positions` reporting lag vs `submit_order` return is the basis of pending-order blindness.

### EventStore + migrations
- **Purpose:** Append-only SQLite store — trades, explanations, kill-switch, strategy/backtest runs, promotions, manifests, snapshots, reconciliation runs/adjustments, orchestration ledger.
- **Key invariants:** WAL + `busy_timeout=30000` + `foreign_keys=ON` per connection (`event_store.py:1701-1702`, `:1953-1991`); exactly-once migrations under `BEGIN EXCLUSIVE` with in-lock version re-read; dual-ancestor enforcement for strategy-run explanations.
- **Files:** `src/milodex/core/event_store.py`, `core/migrations/001..013`. **Tests:** `tests/milodex/core/test_concurrency.py`, `test_event_store_manifests.py`. **Confidence:** high. **Gap:** "append-only" is upheld by convention + compaction predicate, not a trigger; no composite index on the dashboard predicate.

### Reconciliation
- **Purpose:** Compare broker truth vs local folds; record deduped incidents; audited `resolve-position` / `sync-orders`; emit the durable readiness verdict the risk gate consumes.
- **Key invariants:** latest-status-per-order-wins folds; streaming `iter_trades` not `list_trades` (`reconciliation.py:199-204`); single-row incident dedup; sync appends *below* `ExecutionService`, never via risk.
- **Files:** `src/milodex/operations/reconciliation.py`. **Tests:** `tests/milodex/operations/test_reconciliation.py`, `tests/milodex/execution/test_state.py`. **Confidence:** high. **Gap:** `snapshot_broker` bounds open-orders at `limit=100` (`:609`) — a fleet edge case.

### Advisory locks
- **Purpose:** Serialize "same strategy cannot run twice"; allow different strategies concurrently; reclaim stale locks.
- **Key invariants:** `O_EXCL` create is the single winner; live holder heartbeats `mtime` so the 12h age fallback never steals a live lock; reclaim is loud.
- **Files:** `src/milodex/core/advisory_lock.py`, `cli/commands/strategy.py:120-160`. **Tests:** `test_advisory_lock.py`, `test_concurrency.py`. **Confidence:** high.

### Orphan reconciliation / reaper
- **Purpose:** Close phantom `strategy_runs` rows left by hard-killed runners; unlink their stale locks; never reap a live runner.
- **Key invariants:** liveness = PID exists AND process-start-time identity; residual-1 re-check before close+unlink; lock-precedes-row ordering makes the single skip guard both mutations.
- **Files:** `src/milodex/strategies/orphan_reconciliation.py`, `gui/orphan_reaper_controller.py`, `cli/commands/maintenance.py`. **Tests:** `test_orphan_reconciliation.py`, `test_orphan_reaper_controller.py`. **Confidence:** high. **Gap:** recheck→unlink window (Finding R-5); no-ctypes fallback (residual-2, documented).

### Kill switch / PaperRunnerControl / controlled-stop
- **Purpose:** Two distinct stop semantics — controlled (graceful boundary exit, state untouched) vs kill (cancel orders + persist halt + manual-reset-only).
- **Key invariants:** account-scoped, reset-only kill switch (`execution/state.py`); single `trigger_kill_switch` entry point; controlled stop leaves kill-switch/positions/orders untouched.
- **Files:** `execution/state.py`, `strategies/paper_runner_control.py`, `runner.py`, `commands/bench.py`. **Tests:** `test_kill_switch_migration.py`. **Confidence:** high. **Gap:** bench controlled-stop uses a bare lock read for liveness (Finding RT-1).

### BenchCommandFacade / BenchCommandBridge / GUI read models / QML
- **Purpose:** Propose/submit facade reusing CLI/governance callees; bridge is the only `gui/` importer of the facade; read models are 30s-polled read-only QVariant state.
- **Key invariants:** QML carries no broker/event-store/runner/YAML tokens (forbidden-token smoke tests); read models carry no command keys; revalidate-at-submit; backtest rows excluded from paper scope; one sanctioned GUI write (the reaper).
- **Files:** `src/milodex/commands/bench.py`, `gui/bench_command_bridge.py`, `gui/read_models.py`, `gui/*_state.py`, `gui/qml/**`. **Tests:** `test_bench_facade.py`, `test_bench_command_bridge.py`, `test_read_models.py`, `test_qml_load_smoke.py`. **Confidence:** high (facade/bridge), medium (job ledger).

### Orchestration ledger
- **Purpose:** `orchestration_jobs` rows recording operator work-requests + progress.
- **Key invariants:** best-effort (submit proceeds if journaling fails); scoped to backtest + runner submits.
- **Files:** `commands/bench.py:2024-2142`, ADR 0040. **Confidence:** medium. **Gap:** demote/freeze/promote do not journal; swallow-on-failure is silent.

### Backtesting engine / Promotion pipeline / Frozen manifests / Risk profiles
- **Backtest engine:** dispatches on `tempo.bar_size`; T+1 fills by construction; backtests run through `SimulatedBroker` with `NullRiskEvaluator`/`BacktestStructuralRiskEvaluator`. **Confidence:** medium (not fully traced this pass).
- **Promotion pipeline:** `PHASE_ONE_BLOCKED_STAGES` blocks micro_live/live at promotion time; typed thresholds in `promotion/policy.py` (ADR 0052). **Confidence:** high.
- **Frozen manifests:** `_check_manifest_drift` refuses on hash mismatch at promoted stages; exemption keyed off runner-bound `expected_stage` (closes the 2026-05-06 TOCTOU). **Confidence:** high.
- **Risk profiles / defaults:** `load_active_risk_profile` for live paths, base `risk_defaults` for backtest (ADR 0054). **Confidence:** high.

---

## Part 3 — Invariant / Guarantee Table

| Guarantee | Classification | Evidence | Known gap | Severity if false | Next step |
|---|---|---|---|---|---|
| No broker order without risk evaluation | **Proven (code + tests)** | Two `.submit_order(` callsites total: `service.py:144` (preceded by `_evaluate` at `:131`, early-return on block) and `alpaca_client.py:188` (SDK). | None at the call-graph level. | S4 | Add a grep-based CI guard failing if a new `submit_order` callsite appears outside `ExecutionService`. |
| Account cap cannot be exceeded | **False under concurrency** (paper-accepted) | `_check_concurrent_positions`/`_check_total_exposure` count only the `context.positions` snapshot (`evaluator.py:448,426`); no lock across `:367`→`submit:144`; ADR 0026:67 accepts the daily-tempo breach. | Cross-process distinct-symbol or single-process burst can over-fill; pending orders not counted. **Intraday fleet is SPY-only**, so the distinct-symbol breach is unreachable there. | S3 (live) / S2 (paper) | Count open orders as slots; per-account lock on read→submit for the capital path; ADR 0026 addendum. |
| Per-strategy cap cannot be exceeded | **Proven (code), weakly tested** | `_check_strategy_concurrent_positions` reads `expected_max_positions` directly (`:508,562`); attribution from `status='submitted'` rows. | Same cross-process / pending-order race; tests cover semantics, not the race. | S3 (live) | Same pending-order + lock fix; race regression test. |
| Same strategy cannot run twice | **Proven (code + tests)** | Per-strategy `O_EXCL` lock (`strategy.py:121-128`, `advisory_lock.py`); multiprocess contention test. | None. | S2 | None. |
| Different strategies can run concurrently | **Proven (code + tests)** | Disjoint lock namespaces (`runner_lock_name`); ADR 0026 addendum tests. | None. | S2 | None. |
| Kill switch is account-scoped, manual reset | **Proven (code + tests)** | `KillSwitchStateStore` keys nothing on `strategy_id`; consumed for every intent (`service.py:380`); no auto-reset path. | None. | S3 | None. |
| GUI never mutates business state directly | **Proven (code + tests)** | Forbidden-token QML smoke tests; bench is the only facade importer; sole sanctioned GUI write is the liveness-gated reaper. | None at the mutation boundary. | S3 | Keep the forbidden-token allowlist tight as new families wire. |
| Reconciliation readiness blocks unsafe starts | **Proven (code), weakly tested** | `latest_readiness` fail-closed with distinct reason codes; re-checked at submit; 6dd8574 surfaces all blockers. | No end-to-end bridge-level test that a drift/stale verdict blocks each submit family. | S3 | Add the bridge-level blocker assertion. |
| Orphan `strategy_runs` don't persist as live forever | **Proven (code + tests)** | Three triggers (bootstrap/periodic/CLI); liveness-gated close+unlink; dead/recycled/mixed-cohort tests. | Self-healing latency ≈ reap interval + poll. | S2 | None — incident substantially closed. |
| Backtest rows cannot leak into paper dashboards | **Proven (code + tests)** | `backtest_run_id IS NULL` in the shared `_dashboard_scope` predicate, applied in every paper read model inspected; the 2026-05-29 leak is fixed and regression-tested. | Non-GUI analytics filter in Python (`source=='paper'`) rather than the shared predicate — divergent but no mislabel reachable. | S2 | Add a single end-to-end exclusion regression test. |
| Event store append-only assumptions hold | **Documented, not enforced** | No trigger prevents UPDATE/DELETE; compaction DELETEs only backtest explanations with no linked trade (`backtest_run_id IS NOT NULL` + `id NOT IN (SELECT explanation_id FROM trades)`); lifecycle tables UPDATE by design. | A future predicate change could silently violate it; no test pins "compaction never deletes a NULL-backtest row." | S2 | Add the compaction-preserves-live-rows regression test. |
| Operator/manual actions are auditable | **Proven (code), weakly tested** | Every submitted `CommandResult` carries `audit_event_id` + `durable_refs`; `proposal_id` threads propose→submit→result. | `orchestration_jobs` doesn't cover demote/freeze/promote; journaling silently best-effort. | S2 | Document the ledger scope or broaden journaling; raise visibility of swallowed failures. |
| Broker authoritative for live positions | **Proven (code + tests)** | ADR 0010; reconciliation folds detect drift, never override; `resolve_position` requires a live broker delta. | None material. | S3 | None. |
| Event store authoritative for audit/history | **Proven (code), weakly tested** | Append-only with FK integrity under concurrency; dual-ancestor rule. | Compaction audit-completeness asserted only by the predicate. | S2 | Same compaction regression test. |
| Frozen manifests prevent promoted-config drift | **Proven (code + tests)** | `_check_manifest_drift` refuses on hash mismatch; exemption keyed off runner-bound `expected_stage`; 2026-05-06 TOCTOU regression test. | Manual/backtest exempt by design. | S3 | None — closed and pinned. |
| Controlled stop and kill switch have distinct semantics | **Proven (code + tests)** | Different file/path (`.controlled_stop.json` sentinel vs kill-switch events); SIGINT hard fallback is kill. | Distinctness enforced by routing, not types. | S3 | Keep forbidden-token/perimeter tests as the guard. |
| Duplicate-order checks work under high volume | **Proven (code + tests)** | Broker-side `recent_orders` check + untruncated durable `count_recent_submitted_orders` backstop that fails closed on error (`evaluator.py:605-631`). | Backstop row written after submit returns — sub-second cross-process gap, only material above 100 orders/window (not the current workload). Keyed on `(symbol, side)` not strategy (over-blocks safely). | S2 | Document the write-after-submit ordering; no code change for current volume. |
| SQLite write serialization safe under expected concurrency | **Proven (code + tests)** | WAL race-tolerant setup (`:1953-1991`); `busy_timeout=30000` first on every connection (`:1701`); exactly-once migrations under `BEGIN EXCLUSIVE`. | `vacuum()` connection sets no `busy_timeout` but runs under the runtime lock; GUI `mode=ro` readers don't block on WAL writers. | S2 | Optional: set `busy_timeout` on the vacuum connection as defense-in-depth. |
| Paper-only mechanics cannot silently affect live paths | **Proven (code), weakly tested** | `trading_mode='paper'` enforced; backtest uses Null/Structural evaluator + `source='backtest'`; live is locked. | The backtest/paper scope boundary lacks one dedicated regression test. | S3 | Add the backtest-isolation regression test before micro_live. |
| Pending/open orders consume risk slots | **Documented, not enforced** | `recent_orders` (incl. pending) fetched (`service.py:374`) but consumed only by `_check_duplicate_order`; caps read `context.positions` only. | Within one process a burst of distinct-symbol BUYs before any fill over-submits. | S3 (live) / S2 (paper) | Union pending open BUYs into the symbol set and notional inside the two checks (data already in context). |
| micro_live / live are locked | **Proven (code + tests)** | Dual-enforced: `PHASE_ONE_BLOCKED_STAGES` (`state_machine.py:94`) + `_check_trading_mode`/`_check_strategy_stage` (`evaluator.py:169,229`). | The ADR 0052 lifecycle-operational gate is defined-but-unenforced, but orthogonal to the stage lock. | S0 (holds) | None — hardest gate in the system; it holds. |

---

## Part 4 — ADR / Code Drift Analysis

| ADR / Doc claim | Current code behavior | Classification | Severity | Smallest correction |
|---|---|---|---|---|
| **ADR 0008** — risk veto; "the eleven enforced checks are the ones in R-EXE-004" (`:21`) | `_CHECKS` runs fourteen (`evaluator.py:98-113`); chokepoint, no-skip-flag, in-evaluator enforcement all still accurate | Documentation drift | S1 | Replace "eleven" with a pointer to `_CHECKS` as the live source of truth (mirror ADR 0052's point-at-code pattern). |
| **ADR 0011** — SQLite event store, append-only | Matches; WAL + `busy_timeout` + `BEGIN EXCLUSIVE`; append-only holds for live rows; lifecycle tables UPDATE by design as the ADR allows | Accepted evolution | S0 | None. |
| **ADR 0012** — runtime + dual stop | Matches; controlled-stop vs kill-switch distinct by routing; per-strategy lock preserves the foreground-process model | Sound | S0 | None. |
| **ADR 0024 / 0029** — account + per-strategy caps | Implemented (`_check_concurrent_positions`, `_check_strategy_concurrent_positions`); per-strategy reads `expected_max_positions` un-clamped per ADR 0029 Decision 6 | Sound | S0 | None. |
| **ADR 0026** — per-process supervisor; race "operationally rare for daily tempo" (`:67`) | Matches structurally; **acceptance premises (daily tempo / manually-attended / sequential starts) are weakened by the intraday ~10s fleet + ADR 0051 Phase F GUI async-spawn**; the 2026-05-29 addendum revisited the reaper TOCTOU but not the cap race | **Unresolved contradiction** (assumption gone stale) | S2 (paper) → hard gate before live | Append an addendum re-litigating the race for intraday/GUI-spawn; state the bound; declare cross-process cap-serialization a micro_live gate. |
| **RISK_POLICY.md `:24-25,142-153` / SRS R-EXE-004** — sector (20%) + correlation (max 2) caps as "absolute hard stops" | **No `sector`/`correlat` symbol anywhere in `src/milodex/risk`** (verified by grep — zero matches) | **Documentation overstating enforcement** | S2 | Mark sector/correlation caps not-yet-implemented, or implement the two checks. Doc-trim is the surgical fix in long-only Phase 1. |
| **RISK_POLICY.md `:65-70`** — "supports both strategy-level and account-level kill switches" | Code implements only the account-scoped `KillSwitchStateStore` (no `strategy_id` key) | Documentation drift (safe direction — the missing feature is the *less* safe one) | S2 | State that only the account-scoped switch exists today; align with ADR 0005/0026. |
| **ADR 0040** — orchestration job ledger as the audit spine | Journals only backtest + runner submits, best-effort; demote/freeze/promote write no job row | Accepted evolution but over-claimed framing | S2 | Document the ledger scope (or broaden journaling); raise visibility of swallowed journaling failures. |
| **ADR 0049** — "Bench v1 is a visual prototype with NO backend mutation" | Six action families now submit through the bridge | **Accepted evolution** — explicitly and narrowly amended by ADR 0051; not creep | S0 (but see modal doc-drift, RT-7) | None for the ADR; fix the stale in-code comments that still say "no state is mutated." |
| **ADR 0052** — lifecycle-operational gate | Defined with `enforced=False`; orthogonal to the live stage-lock | Named accepted gap | S0 | None (the live lock is independent and holds). |

The ADR record is a genuine asset: most "drift" here is doctrine lagging code, not architecture rot. The one drift that is direction-relevant (ADR 0026) is called out as the biggest direction risk; the two that violate FOUNDER_INTENT's "real > impressive shell" priority (sector/correlation caps, strategy-level kill switch) are doc-trim jobs that must complete before any of that text is cited as a live-readiness guarantee.

---

## Part 5 — Concurrency Threat Model

The system is now being soak-tested under an intraday fleet — the regime ADR 0026 deferred. Each threat below is assessed against *current* code and the *current* (SPY-only, paper-locked) operating state.

| Threat | Current protection | Sufficient? | Severity | Test coverage | Smallest hardening | Paper-only or live-relevant |
|---|---|---|---|---|---|---|
| Multiple runners firing on the same bar | Per-cycle in-process dedup `(bar,symbol,side)`; durable cross-process dedup on `(symbol,side)` with fail-closed backstop | Yes for duplicate *orders* | S0 | `test_duplicate_order_*` | None | both |
| **evaluate→submit race, cross-process, different symbols** | None — no lock across `service.py:131`→`:144`; broker-as-arbiter only sees pre-fire state | No for capital; **unreachable for the intraday fleet** (SPY-only, verified) so paper-bounded today | S2 (paper) → S3 (live) | None | Per-account lock on read→submit for the capital path only | live-capital gate |
| **evaluate→submit race, same symbol** | `_check_concurrent_positions` collapses same-symbol to +1; per-symbol `_check_single_position_limit` still binds each leg | Position-count invariant immune; exposure can transiently overshoot | S2 (paper) → S3 (live) | None | Move the durable dedup record to pre-submit, or the same read→submit lock | live-capital gate |
| **Pending orders don't consume risk slots** | Caps count only filled `context.positions` | No — a single-process distinct-symbol burst over-submits even without concurrency | S2 (paper) → S3 (live) | None | Union open orders into count + exposure (data already in `context.recent_orders`) | live-capital gate |
| Broker positions lagging fills | Broker `get_orders` reflects the order the instant `submit_order` returns; dedup leans on that | Yes for dedup; the lag is exactly the pending-order gap above | covered above | partial | covered above | both |
| Duplicate-order checks under volume | Broker check + untruncated durable backstop, fail-closed | Yes | S0 | yes | None | both |
| SQLite writer serialization | WAL + `busy_timeout=30000` + `BEGIN EXCLUSIVE` migrations | Yes — proven by cross-process tests | S0 | `test_concurrency.py` | None | both |
| WAL / busy_timeout assumptions | Set first on every connection; VACUUM runs under the runtime lock | Yes | S0 | yes | Optional `busy_timeout` on the vacuum connection | both |
| Advisory-lock staleness | 12h age fallback with live-holder heartbeat; never steals a live lock | Yes | S0 | `test_advisory_lock.py` | None | both |
| PID reuse | Process-start-time identity vs `holder.started_at` (the 2026-05-19 fix) | Yes, except a stripped no-ctypes/no-`/proc` env degrades to bare PID-existence (residual-2, documented, loud warning) | S0 in normal env | yes | Add `psutil` or keep the documented deferral | both |
| **GUI QTimer reaper × spawning runner** | Residual-1 re-check threads one holder snapshot from classify to mutate; skip on `started_at` change | Row-close half sound (lock-precedes-row ordering); **unlink half has a residual window** — holder appearing after the re-check but before the unlink | S2 | `test_recheck_guard_*` covers the re-check point only | Re-confirm the holder atomically with the unlink | paper-only |
| GUI reaper × CLI reaper | Both idempotent close-if-closed + `missing_ok` unlink | Yes — double-reap is a no-op | S2 if false | not unit-tested | None | paper-only |
| **Bench controlled-stop × dead-but-lock-present runner** | `_peek_runner_lock` bare `current_holder()` — no liveness | No — returns false "submitted" against a dead/recycled-PID runner | S2 | none | Route through the identity-verified liveness helper | paper-only |
| **Double-click / GUI-vs-CLI start race** | Read-only pre-check then out-of-process `O_EXCL` acquire in the child | Single-runner correctness held (child `O_EXCL`); loser spawns and dies → phantom row the reaper mops up | S2 | none | Pre-spawn atomic guard or per-`strategy_id` submit serialization in the bridge | paper-only |
| Kill-switch propagation across processes | Account-scoped event consumed by every intent | Yes | S0 | `test_kill_switch_migration.py` | None | both |
| Reconciliation drift while runners active | Readiness verdict re-read each evaluation; exposure-increasing intents fail closed on drift | Yes | S0 | yes | None | both |
| Backtest + paper rows sharing tables | `backtest_run_id IS NULL` scope predicate | Yes where applied | S2 if false | regression-tested for risk-throughput | One end-to-end exclusion test across all read models | both |
| GUI pollers reading during writes | WAL readers don't block writers; pollers fail soft | Yes | S0 | yes | None | both |
| **Unbounded read in a 30s poller** | None — `ActivityFeedState` does `fetchall` over full paper history, caps in Python | No — the same OOM anti-pattern, paper-scoped (smaller constant) | S2 | none | `ORDER BY recorded_at DESC LIMIT N` in SQL + a partial index | paper-only (growth) |
| Maintenance racing GUI/runners | Compaction runs under the `milodex.runtime` advisory lock | Yes — mutual exclusion by construction | S0 | partial | None | both |

The headline: **broker-as-arbiter covers position/exposure caps but not (a) in-flight-order occupancy and (b) the read→submit window.** Both are paper-tolerable today and become hard gates before micro_live. Everything else in the concurrency surface is either sound or a bounded paper-only nuisance.

---

## Part 6 — Recent Incident Class Review (2026-05-29 concurrent-fleet soak)

| Incident | Root cause | Caused by or merely exposed by concurrency | Current fix sufficient? | Regression test that should exist | Status |
|---|---|---|---|---|---|
| **OOM / fleet freeze** from unbounded explanation/trade reads | `run_reconciliation` loaded the whole `trades` table twice; incident-dedup loaded all explanations — every runner hit this at startup | Exposed by concurrency (each runner multiplied the cost) | **Yes** for the hot startup path: streaming `iter_trades` (`reconciliation.py:199-204`), single-row `latest_reconcile_incident_hash`; both regression-guarded by monkeypatching the unbounded methods to raise | Extend the guard to the concurrent case (N threads, bounded memory) | **Solved** (startup path); the *same anti-pattern* survives in `ActivityFeedState` (Finding D-1) |
| **Paper-order drift / local-only open orders** | The order fold never removed a closed order — every submitted order stayed "locally open" forever, arming the readiness veto | Pre-existing fold bug, surfaced by the fleet | **Yes** — latest-status-per-`broker_order_id`-wins fold (`reconciliation.py:672-700`) + audited `sync-orders` | A fold test that a `submitted→cancelled` order closes to zero positions | **Solved** |
| **Phantom `strategy_runs`** | Hard-killed runners leave `ended_at IS NULL` rows the ActiveOps model renders as live | Concurrency made it routine (fleet of detached runners) | **Substantially** — three-trigger liveness-gated reaper + residual-1 guard; self-healing within ~reap interval | The recheck→unlink-window test (Finding R-5); controlled-stop-against-dead-runner test (Finding RT-1) | **Partially solved** — the *row* is reaped; the *operator-trust surfaces* (stop path, "held" badge) still ride the weakest liveness check |
| **Readiness blockers not visible to operator** | Confirmation modal surfaced only the first blocker | Independent of concurrency | **Yes** — 6dd8574 surfaces all blockers via `_blockerSummary`, pinned by `test_qml_load_smoke` | A bridge-level test that a drift/stale verdict blocks each submit family | **Solved** at the facade level; add the bridge-level assertion |
| **DB bloat / missing FK index** | Cascade-delete on `trades.explanation_id` was un-indexed | Independent of concurrency | **Yes** — migration 013 indexed it; compaction prunes only cascade-safe backtest rows in batched commits | Compaction-preserves-live-and-trade-linked-rows regression test | **Solved** |
| **Backtest rows leaking into paper read models** | Missing `backtest_run_id IS NULL` clause (69,251 vs 358 rows) | Independent of concurrency | **Yes** — shared `_dashboard_scope` predicate, regression-tested for risk-throughput | One end-to-end exclusion test across *all* paper read models | **Solved**, one consolidation test short |
| **evaluate→submit duplicate-fill / over-cap race** | No lock across read→submit; caps see only filled positions | Concurrency-native | **Partial** — duplicate *orders* are blocked (durable backstop); the *cap* race is unaddressed but **paper-bounded** (intraday fleet SPY-only; live locked) and ADR-accepted for daily tempo | Two evaluators share pre-fire state and both submit; pending-order-counts-toward-cap | **Partially solved → deferred to the micro_live gate**; the deeper signal is that ADR 0026's acceptance premises no longer hold for the new operating mode |

**Does the incident class indicate a deeper direction problem?** No — it indicates a *sound design entering a stress regime its founding ADR explicitly deferred.* Every fix landed by strengthening an existing seam (streaming reads, latest-wins folds, a liveness-gated reaper, a shared scope predicate). The correct read is "hardening phase," not "redesign." The one place to be honest: the cap race is not "solved," it is *deferred with paper safety*, and that deferral must be re-litigated (not silently inherited) before micro_live.

---

## Part 7 — Test Gap Analysis

Coverage is heavy on strategy units, promotion, formatter, and risk-rule units; thin on concurrency-as-behavior. The gaps below are ordered by how much they protect safety, truth, and operator confidence.

| Test | Layer | Fixture shape | Failure it catches | Type | Required before micro_live? | Smallest useful version |
|---|---|---|---|---|---|---|
| `test_two_distinct_symbol_intents_before_fill_respect_account_cap` | risk/execution | Broker stub: `submit_order`→PENDING, `get_positions` still pre-fill | Pending-order blindness — second distinct-symbol BUY passing the cap | integration | **Yes** | Submit two distinct-symbol BUYs at cap-1; assert the second is BLOCKED once pending orders count |
| `test_pending_order_counts_toward_total_exposure` | risk | `EvaluationContext` with one PENDING BUY, empty positions | Exposure cap ignoring in-flight notional | unit | **Yes** | Assert `_check_total_exposure` includes the pending notional after the fix |
| `test_concurrent_processes_evaluate_submit_distinct_symbols_do_not_both_breach_cap` | execution/concurrency | Two threads, shared store, serializing broker stub, barrier between evaluate and submit | The capital-path cross-process cap race | integration | **Yes** | At cap-1, assert at most one fills under the live-path lock; paper may intentionally allow both |
| `test_over_cap_concurrent_fill_self_corrects` | execution/concurrency | Two `ExecutionService` against a shared FakeBroker at cap-1 | Pins the ADR-accepted bound: overshoot exactly +1, next cycle refused | integration | Yes | Both pass+submit; a third evaluation post-fill is refused with `max_concurrent_positions_exceeded` |
| `controlled_stop_against_dead_but_lock_present_runner_is_blocked` | bench/runtime | Open `strategy_runs` row + lock file for a dead/recycled PID | The false "submitted" stop (Finding RT-1) | unit | No (paper) | Assert `status='blocked'`, `reason_code='no_active_runner'`, not "submitted" |
| `concurrent_start_paper_runner_spawns_at_most_one` | bench | Two back-to-back `submit_start_paper_runner` | Start-race spawning a doomed duplicate (Finding RT-3) | integration | Yes | Assert one process spawned OR the second returns `advisory_lock_held` before spawn |
| `reaper_unlink_window_holder_after_recheck` | strategies/orphan | `current_holder` returns `[None, None, fresh]` | Reaper deleting a freshly-spawned live lock (Finding R-5) | regression | No (paper) | Guard passes (None==None) but a holder exists by unlink time; assert lock NOT unlinked, row NOT closed |
| `activity_feed_bounded_memory_under_large_history` | gui/read_models | Seed N≫200 paper explanations | Unbounded fetchall (Finding D-1) | regression | No (paper) | Assert SQL returns ≤`_FEED_CAP` rows (LIMIT in SQL, not the Python slice) — fails today |
| `backtest_source_isolation_from_paper_read_models` | gui/read_models | One backtest-sourced + one paper trade/explanation | Backtest leak into any paper read model | regression | **Yes** | Every paper-scope read model returns only the paper row |
| `compaction_preserves_live_and_trade_linked_rows` | core/maintenance | Paper explanation (NULL) + backtest explanation with linked trade + backtest no-trade | A future prune predicate deleting a live or trade-linked row | regression | No | Assert only the unlinked backtest no-trade row is deleted |
| `sector_correlation_cap_enforced_or_doc_trimmed` | risk / docs | A position set concentrated >20% in one sector | Doc claims an enforced hard stop that does not fire | unit | **Yes** | Either block with a sector reason code, or assert RISK_POLICY's hard-stop list contains only checks present in `_CHECKS` |
| `intraday_fleet_startup_soak_bounded_memory` | operations/reconciliation | Large seeded store, K worker threads on the startup path | The OOM class at the behavioral (concurrent) level | soak | No | Assert no `MemoryError` and the unbounded list methods are never called on the hot path |
| `drift_verdict_blocks_each_submit_family_through_bridge` | bench | Facade with a `reconciliation_drift` readiness verdict | A hidden readiness blocker suppressing the affordance without an operator-visible refusal | integration | Yes | Drive propose+submit via the bridge; assert blockers non-empty and a "Blocked — not submitted" summary |

---

## Part 8 — Non-Issues and Accepted Tradeoffs

This section exists to *reduce* unnecessary anxiety and prevent overbuilding. Each item looked concerning and is, on the evidence, fine.

| Item | Why it looked concerning | Why it is safe enough | Support | What would make it real | Disposition |
|---|---|---|---|---|---|
| **No lock across evaluate→submit (in-process)** | Reads like a textbook race | The chokepoint holds; the in-process path is sequential; the real exposure is cross-process same-symbol, which is paper-only + self-correcting | `service.py:131-144`; ADR 0026:33,67 | Live unlocks without the capital-path read→submit lock | Document; monitor |
| **Two daily runners both fill, briefly over cap** | Cap breach under concurrency | Explicitly ADR-accepted for daily tempo; broker is the arbiter on each evaluate; the named Phase 4+ upgrade path exists; **intraday fleet is SPY-only so the distinct-symbol breach is unreachable there** | ADR 0026:67; configs `*intraday*` → `universe.spy_only.v1` | Intraday fleet gains multi-symbol configs, or live unlocks | Document |
| **Reaper residual-1 TOCTOU guard** | Reaper could wipe a fresh runner's lock | One holder snapshot threaded classify→mutate; `started_at` comparison; sound because lock-acquire precedes row-append (`strategy.py:126` < `runner.py:116`); unit-tested | `orphan_reconciliation.py:145-160` | Reversing the lock-precedes-row ordering | Monitor |
| **Shared (account-scoped) kill switch** | One strategy's kill halts both | Correct by design (ADR 0005) — the conservative direction; manual-reset-only | `execution/state.py`; ADR 0026:65 | n/a — this is the intended behavior | Ignore |
| **Periodic reaper loads full `strategy_runs` each tick** | The OOM incident was unbounded reads | `strategy_runs` is one row per session (low cardinality), not per decision; cost negligible; the asymmetry vs the streamed `trades` path is appropriate | `event_store.py:934`; `reconciliation.py:199-204` | `strategy_runs` becoming per-decision (it will not) | Document |
| **GUI start pre-check is read-only, not a held lock** | Two starts could both pass the pre-check | Correctness rests on the child's `O_EXCL` acquire before any row append; the loser dies cleanly; multiprocess race test covers it | `paper_runner_control.py:198-226`; `test_concurrency.py:379-415` | Removing the child's exclusive acquire | Document |
| **Compaction DELETE + VACUUM vs "append-only"** | Physically deletes rows and mutates the file | Predicate excludes every live (NULL `backtest_run_id`) and every trade-linked row, so cascade can't fire; operator-run under the runtime lock; writes a backup first | `event_store.py:842-878`; `maintenance.py:75` | A predicate change dropping the backtest-only guard | Document (+ regression test) |
| **`vacuum()` connection has no `busy_timeout`** | Concurrent writer → "database is locked" | Runs under the runtime lock that excludes runners and the GUI, so no concurrent writer exists by construction | `event_store.py:880-898`; `maintenance.py:75-79` | Compaction ever running without the lock | Document |
| **GUI `mode=ro` readers have no `busy_timeout`** | Reader hits "database is locked" during a commit | WAL readers don't block on writers; the only exclusive op (VACUUM) excludes the GUI; pollers fail soft | `event_store.py:1953-1991`; `read_models.py:1270` | Leaving WAL mode | Monitor |
| **Non-GUI report paths use unbounded `list_*`** | Same unbounded-read shape as the incident | One-shot operator commands, not 30s pollers; the OOM was the concurrent-startup path, which was fixed | `reports.py:171`; `evidence.py:136-150` | Wiring a report into a timer | Monitor |
| **Bench modal genuinely submits despite ADR 0049 "no mutation"** | Looks like mutation crept in | ADR 0051 explicitly + narrowly amends ADR 0049 for exactly six families along one facade; routed through governance callees, never QML-direct | ADR 0051:18-37; `bench_command_bridge.py:35-46` | A new mutation appearing outside the facade | Document |
| **OrphanReaperController writes from the GUI** | GUI mutating the event store | Bookkeeping-only (closes phantom rows), liveness-gated, residual-1 guarded, reuses the same reconciler the CLI uses, interval-clamped | `orphan_reaper_controller.py:79-91` | Wiring it to mutate UI logic or trade state | Monitor |
| **Reconciliation-readiness gate is asymmetric (sells exempt)** | An exposure-reducing SELL bypasses the gate | Intentional per R-OPS-004 — let the operator always flatten risk; only exposure-increasing actions fail closed | `exposure.py:10-28`; `evaluator.py:188-209` | Allowing exposure-increasing sells through | Ignore |
| **Backtest path can't bind to live mode/manifest** | Replay could couple to today's state or reach the broker | `is_backtest` short-circuits manifest-drift + reconciliation, forces `trading_mode='backtest'`, base risk_defaults; submits via `SimulatedBroker` | `service.py:287-408`; `policy.py:46-85` | A backtest using the live broker | Ignore |
| **Attribution clamps net-short to zero** | A SELL exceeding prior BUYs could drop a position from its strategy's cap | Deliberate, documented; a net-short balance is a data artifact (system can't go short); account cap still counts the position regardless | `attribution.py:99-127` | The system gaining real short capability | Document |

---

## Part 9 — Rewrite Temptation Check

Each candidate that implies new architecture is held to the bar: name the invariant, why no existing layer can own it, the smaller alternative, the new failure modes, and why debugging improves. None of the temptations clear the bar; all reduce to seam-strengthening.

**Consolidate liveness into one `live_lock_holder(strategy_id, locks_dir) -> LockHolder | None`.**
- *Invariant:* "a lock holder reported as live is process-identity-verified."
- *Why no existing layer owns it:* it is implemented three times (`paper_runner_control._existing_live_runner`, `orphan_reconciliation._has_live_runner`, `bench._peek_runner_lock`) at three rigor levels, and the strongest is not reused.
- *Smaller alternative than a new layer:* this *is* the smaller alternative — one shared function the three callers route through; no new process, table, or daemon.
- *New failure modes:* essentially none; it strictly strengthens the two weak callers.
- *Why debugging improves:* one place to reason about "is this runner alive," instead of three subtly different answers.
- **Verdict: do it. Not a new layer — a consolidation.**

**Pending-order accounting in the caps.**
- *Invariant:* caps bound *real economic exposure*, including in-flight orders.
- *Why no existing layer owns it:* the caps read only `context.positions`; the pending data (`recent_orders`) is already in `context` but unused by the count/exposure checks.
- *Smaller alternative:* union open BUYs into the symbol set and notional inside the two existing checks — no new broker call, no new structure.
- **Verdict: do it inside the existing evaluator.**

**Per-account lock around read→submit for the capital path.**
- *Invariant:* at most one of N concurrent evaluations may fill into the last cap slot.
- *Why no existing layer owns it:* broker-as-arbiter only sees pre-fire state; nothing serializes the window.
- *Smaller alternative than a cross-process reservation table:* reuse the existing per-account advisory lock (`core/advisory_lock.py`, already held by `reconcile`/`trade submit`) **only** for micro_live/live; paper stays lock-free to preserve ADR 0026 inspectability.
- *New failure modes:* a lock-hold across a broker call adds latency and a stuck-lock surface — acceptable on the capital path, which is why paper is excluded.
- **Verdict: defer to the micro_live gate. Do NOT build it for paper.** A cross-process position-reservation table is explicitly rejected — the broker remains the arbiter; the lock only serializes the window.

**Postgres / DB swap, multi-tenant supervisor, per-strategy kill switch, read-model cache.**
- None can name an invariant the current model provably cannot protect with a smaller change. SQLite serialization is proven by cross-process tests; the per-process model scales to N terminals and is more inspectable; the account-scoped kill switch is correct by design; the read-model gaps are an `ORDER BY … LIMIT` and an index. **Verdict: do not build.**

**Default posture confirmed:** every actionable item is a seam-strengthening or a doc-trim. No new layer, service, daemon, orchestrator, or database is warranted by the evidence.

---

## Part 10 — Prioritized Backlog

### A. Hard gates before micro_live / live
| Title | Sev | Invariant | Files | Size | Smallest useful PR | Reviewer | Opus review? |
|---|---|---|---|---|---|---|---|
| Pending/open orders consume risk slots | S2→S3 | Caps bound real exposure | `risk/evaluator.py` | small | Union open BUYs into `_check_concurrent_positions` + `_check_total_exposure`; the two unit tests | Risk | **Yes** |
| Per-account read→submit lock, capital path only | S2→S3 | At most one fills the last slot | `execution/service.py`, `core/advisory_lock.py` | decent | Lock `_evaluate`→`submit` for micro_live/live; paper lock-free; cross-process test | Risk/concurrency | **Yes** |
| ADR 0026 addendum re-litigating the intraday race | S2 | Doctrine matches operating mode | `docs/adr/0026-*.md` | tiny | Addendum naming the bound + declaring cross-process serialization a micro_live gate | Docs | Yes |
| Doc-vs-code reconciliation (sector/correlation, strategy-kill, ADR 0008 count) | S2/S1 | "real > impressive shell" | `RISK_POLICY.md`, `SRS.md`, `docs/adr/0008-*.md` | small | Trim to enforced reality or implement; doc-consistency test | Docs/Risk | Yes |
| Backtest-isolation regression test | S2 | Paper-only can't show backtest rows | `tests/.../gui` | tiny | One end-to-end exclusion test across all paper read models | Data | No |

### B. Should fix before more intraday-fleet testing
| Title | Sev | Files | Size | Smallest PR | Reviewer | Opus? |
|---|---|---|---|---|---|---|
| Consolidate liveness into one helper; fix bench stop + duplicate-start | S2 | `core/advisory_lock.py`, `commands/bench.py`, `strategies/paper_runner_control.py`, `gui/active_ops_state.py` | decent | Shared `live_lock_holder`; route 3 callers; stop-against-dead-runner test | Runtime | Yes |
| Tighten reaper recheck→unlink atomicity | S2 | `strategies/orphan_reconciliation.py` | small | Re-confirm holder immediately before unlink; window test | Runtime | Yes |
| Concurrency-as-behavior test battery | S2 | `tests/.../execution`, `operations`, `strategies` | decent | Over-cap concurrent fill, N-runner soak, dual-reaper interleave | Concurrency | Yes |

### C. Paper-only operational cleanup
| Title | Sev | Files | Size | PR |
|---|---|---|---|---|
| Bound `ActivityFeedState` SELECTs in SQL + partial index | S2 | `gui/activity_feed_state.py`, new migration | small | `ORDER BY recorded_at DESC LIMIT N`; `idx_explanations_paper_scope` partial index; growth test |
| Fix bench "no mutation" doc-comments | S1 | `gui/qml/.../BenchConfirmationModal.qml`, `gui/read_models.py` | tiny | Update header + docstring (mind the QML smoke-test substring assertion) |
| `busy_timeout` on the vacuum connection (defense-in-depth) | S1 | `core/event_store.py` | tiny | One line; removes a latent footgun |

### D. Observability / reporting
| Title | Sev | Files | Size | PR |
|---|---|---|---|---|
| Surface swallowed orchestration-journaling failures | S2 | `commands/bench.py` | tiny | Keep the swallow; raise log level / add a metric |
| Broaden `orchestration_jobs` to governance submits (or document the scope) | S2 | `commands/bench.py`, `docs/adr/0040-*.md` | small | Journal demote/freeze/promote, or write the scope into BENCH_BOUNDARY |

### E. Documentation-only clarifications
- ADR 0026 addendum (also a gate, listed above). Confirm the start-runner required-readiness set (no `data_freshness`) is intentional and document it in-code.

### F. Explicitly defer / do not build yet
- Paper-path execution lock; Postgres; multi-tenant supervisor; per-strategy kill switch; read-model cache. (Part 9.)

### G. Needs experiment before decision
- The `paper-scope-predicate-unindexed` finding: run `EXPLAIN QUERY PLAN` on the paper-scope COUNT queries first — the `backtest_run_id IS NULL` filter may already be index-assisted; only add the composite index if the plan shows a residual scan.
- Intraday-fleet sustained-concurrency behavior: the soak *is* the experiment. Instrument cap-overshoot frequency and memory before deciding whether the capital-path lock needs to predate micro_live by more than the gate.

---

## Part 11 — Final Decision Page

### Recommended next 10 PRs
1. Add a shared `live_lock_holder(strategy_id, locks_dir) -> LockHolder | None` (PID-existence + process-start-time identity); route `bench._peek_runner_lock`, `active_ops_state`, and `paper_runner_control._existing_live_runner` through it. (Consolidation; no behavior change beyond bench now seeing liveness.)
2. Fix bench controlled-stop and duplicate-start to use the consolidated liveness so "Stop Trading" against a dead-but-lock-present runner is honestly blocked; add the regression test.
3. Make pending/open broker orders consume slots in `_check_concurrent_positions` and `_check_total_exposure`; add `test_pending_order_counts_toward_total_exposure` and `test_two_distinct_symbol_intents_before_fill_respect_account_cap`. (Same-process correctness — no concurrency needed.)
4. Bound the two `ActivityFeedState` SELECTs with `ORDER BY recorded_at DESC LIMIT N`; add the bounded-memory test.
5. Add a composite/partial index on the paper-scope predicate *after* confirming the query plan needs it; document the bounded-read convention.
6. Tighten the periodic orphan reaper so one holder snapshot atomically guards both row-close and lock-unlink; add the recheck→unlink-window test and a test pinning lock-acquire-before-row-append.
7. Write the ADR 0026 addendum: the intraday + GUI-async-spawn case invalidates the daily-tempo/sequential-start premise; state the accepted-race bound; declare cross-process cap-serialization a micro_live hard gate (not paper).
8. Doc-vs-code reconciliation: trim sector/correlation caps and the strategy-level kill switch from RISK_POLICY.md / SRS R-EXE-004 to what the evaluator enforces; fix ADR 0008's "eleven checks" → fourteen.
9. Fix bench doc-drift comments ("no state is mutated" / "executable MUST stay False"); broaden `orchestration_jobs` journaling to governance submits and stop swallowing journaling exceptions silently.
10. Add the concurrency-as-behavior test battery: two evaluators share pre-fire state and both submit (over-cap, asserting the new pending-order accounting bounds it), N-runner bounded-memory soak, concurrent CLI + GUI reaper on one DB.

### Hard gates before micro_live / live
- Pending/open orders consume position + exposure slots.
- Per-account lock around read→submit for the capital path (paper stays lock-free).
- ADR 0026 addendum re-litigating the race for the new operating mode.
- No RISK_POLICY/SRS invariant advertised as enforced unless it fires in code.
- Controlled-stop and duplicate-start use process-identity-verified liveness.
- The dual live-lock stays enforced and tested throughout.

### Should not build yet
- Paper-path execution lock · Postgres / DB swap · multi-tenant supervisor / control plane · per-strategy kill switch · read-model cache.

### Things Milodex is doing right
- The risk chokepoint is a genuine single point (sole non-broker `submit_order` callsite) and fail-closed.
- The micro_live/live lock is dual-enforced in code, not docs — the hardest gate in the system, and it holds.
- SQLite is correctly tuned (WAL + `busy_timeout` + `BEGIN EXCLUSIVE` migrations) — proven by cross-process tests.
- Incidents were answered by strengthening seams (streaming reads, latest-wins folds, liveness-gated reaper, shared scope predicate), never by reaching for new architecture.
- The GUI mutation boundary holds; the bench facade owns no business rules.
- ADR discipline is a real asset — every phase closed, every drift named, addenda appended not rewritten.

### Things Milodex should simplify
- Three liveness implementations → one identity-verified helper.
- Python-side feed cap → SQL `LIMIT`.
- Doctrine → trimmed to enforced reality.

### Things Milodex should stop doing
- Carrying ADR 0026's daily-tempo race acceptance forward silently under an intraday fleet.
- Advertising sector/correlation caps and a strategy-level kill switch the code does not implement.
- Hardening the already-strong same-symbol/config-drift risk surfaces while the cross-process and pending-order surfaces stay untouched.

### Biggest direction-level risk
Inheriting ADR 0026's daily-tempo race acceptance into micro_live without re-litigation. Paper-safe only because the dual live-lock holds; becomes real overexposure the moment that lock lifts.

### Biggest implementation-level risk
The three-way liveness divergence with no single owner — the operator-trust surfaces ride the weakest check, so a stop/start decision against a dead or recycled-PID runner can be confidently wrong.

### Biggest test gap
Concurrency-as-behavior — no test exercises two evaluators sharing pre-fire state, pending-order-counts-toward-cap, the intraday-fleet bounded-memory soak, the reaper recheck→unlink window, or controlled-stop-against-a-dead-runner.

### Confidence ratings
- Architecture direction: **high**
- Runtime lifecycle: **high**
- Risk correctness under concurrency: **medium**
- Data/reconciliation correctness: **high**
- GUI/operator safety: **medium**
- Promotion readiness toward micro_live/live: **high**

---

*Method note: five domain reviewers deep-read their subsystems and returned structured findings; every S3/S4 finding was adversarially verified by an independent skeptic that opened the cited files and tried to refute it (two headline S3s were downgraded to S2 on verification — the intraday fleet is SPY-only, and live is code-locked). The lead auditor independently re-verified by hand the single most load-bearing invariant (no broker order without risk evaluation), the SQLite tuning, the evaluator's fail-closed structure, the reconciliation streaming fix, the SPY-only fleet universe, and the absence of sector/correlation checks. Where a line citation came from the domain pass and was not re-opened by the lead, the finding's confidence reflects that. Severities are stated in the dual register the rubric implies: an item is "S2 today" in the current paper-locked state and "an S3 gate" the moment the live lock is considered for lifting.*

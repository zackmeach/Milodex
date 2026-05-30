# ADR 0026 — Concurrent Multi-Strategy Uses Per-Process Supervisor

**Status:** Accepted
**Date:** 2026-05-05
**Relates to:** [ADR 0008](0008-risk-layer-veto-architecture.md) (risk layer veto), [ADR 0012](0012-runtime-and-dual-stop.md) (runtime model and dual-stop), [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) (account-scoped position caps), [ADR 0025](0025-phase-2-is-closed-and-phase-3-may-open.md) (Phase 3 authorization), [docs/PHASE3_PLANNING.md §4.1.iii](../PHASE3_PLANNING.md)

## Context

[Phase 3 §4.1.iii](../PHASE3_PLANNING.md) opened concurrent multi-strategy execution as scope. Two regime/research-target combinations now run in the same paper account: regime SPY/SHY 200-DMA + meanrev RSI(2) (Phase 1's two strategies, never concurrently exercised) and now regime + momentum.daily.tsmom (Phase 3's second research-target, refused promotion at backtest → paper per `995e31f`).

[ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) prepared the safety contract: the risk evaluator's `concurrent_positions` check is account-authoritative regardless of which strategy proposed the intent. Strategy YAML `risk.max_positions` is informational. Multi-strategy paper accounts must size `max_concurrent_positions` to the sum of strategies' expected concurrent positions.

What ADR 0024 deliberately did not name was the **runtime model** for actually running two strategies side-by-side. Two shapes were available:

1. **Per-process supervisor.** Each strategy runs in its own foreground process — a separate `milodex strategy run <name>` invocation per strategy, in a separate terminal or spawned by a launcher script. The runner is unchanged. The "supervisor" is whatever the operator uses to manage the processes: bare terminal sessions, `tmux`, a small shell wrapper, or the OS task manager.

2. **Single-process supervisor.** A new `milodex strategy run-multi <name1> <name2> ...` command hosts multiple strategies in one process via threads or asyncio. The runner is rewritten as a multi-tenant host. [ADR 0012](0012-runtime-and-dual-stop.md)'s "manually-invoked, long-running foreground process" framing is replaced by a multi-strategy variant. The dual-stop dialog (controlled stop / kill switch) becomes ambiguous — does Ctrl-C target the process or one of its hosted strategies?

This ADR adopts (1).

## Decision

**Per-process supervisor is the authoritative concurrency model.** Each strategy runs in its own process via the existing `milodex strategy run <strategy_id>` command. The operator (or a small launcher script the operator owns) is the supervisor.

Specifically:

1. **The runner is unchanged.** No multi-tenancy, no internal threading, no supervisor process. [ADR 0012](0012-runtime-and-dual-stop.md) stays in force as written: each runner is a manually-invoked, long-running foreground process with the dual-stop dialog (controlled stop / kill switch) keyed to that single process.

2. **Operator workflow is two terminals OR a launcher.** The simplest pattern is two terminals, one running each strategy. An operator who wants programmatic control may write a small shell or Python script that spawns the two processes — that script is operator-owned and lives outside the Milodex codebase.

3. **The account is shared, the per-strategy state is not.** Both runners write to `data/milodex.db` via separate `session_id` values. Each runner's `strategy_runs` row is independent. Each runner's kill-switch state is shared (per [ADR 0005](0005-kill-switch-manual-reset.md) — the kill switch is account-wide, not per-strategy), so a kill in one runner halts both.

4. **[ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) is the safety mechanism.** When two strategies independently propose intents that together would exceed `max_concurrent_positions`, the risk evaluator's account-scoped check refuses whichever intent is evaluated second. There is no inter-process lock — the risk evaluator queries the live broker positions on each `evaluate_intent` call, and the broker is the account-state arbiter. Race conditions where two intents pass risk simultaneously and both fill are not architecturally precluded but are operationally rare for daily-tempo strategies.

5. **Per-strategy position attribution is not introduced.** [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)'s "Phase 2 §4.1.iii carries a known upgrade path: option (a) becomes the right next move" remains the future option. Phase 3 declines to take up that work because the simpler per-process model with account-scoped enforcement satisfies the engineering-capability test (does the harness handle a second research thread?) without requiring it.

## Rationale

- **Preserves [ADR 0012](0012-runtime-and-dual-stop.md) without supersession.** The dual-stop dialog, the `strategy_runs` lifecycle, the controlled-stop / kill-switch semantics — none change. [ADR 0012](0012-runtime-and-dual-stop.md) was carefully designed for human-supervised paper trading; rewriting it for multi-tenant hosting on a goal as narrow as "exercise two strategies concurrently in paper" would be over-investment.

- **Each strategy gets its own log, its own session, its own process boundary.** Operationally, when something goes wrong with one strategy, the other keeps running. A Python exception in the meanrev runner does not kill the regime runner. Forensic debugging is per-process and per-`session_id` — no thread interleaving in logs to untangle.

- **The risk layer's safety guarantee is unchanged.** [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)'s account-scoped enforcement runs against the broker's authoritative position state on every `evaluate_intent` call. It does not rely on inter-process coordination — it relies on the broker. Whichever model (single- or multi-process), the safety layer is the same.

- **Single-process supervisor would supersede [ADR 0012](0012-runtime-and-dual-stop.md).** That is a significant architectural movement. Phase 3's scope is scoped to (i)+(iii) — second research-target plus concurrency — and Phase 3 §4.1's decision rationale was explicitly "smallest scope expansion that exercises the platform's growth." A new runtime model exceeds that scope discipline.

- **Per-process supervisor scales to N strategies trivially.** Three terminals, four terminals, etc. Single-process supervisor scales until the host process becomes a contention point. For Phase 3's two-strategy case, the difference is invisible; for Phase 4+ if more strategies open, the per-process model already works without further architectural change.

- **The operator workflow matches FOUNDER_INTENT priority #1 (trustworthy).** "Open two terminals" is mechanically obvious. There is no hidden orchestration. An operator can `ps` and see exactly which strategies are running; can kill one without affecting the other; can replay either strategy's session by `session_id` from the event store. The simpler model is the more inspectable model.

## Alternatives considered

**(b) Single-process supervisor.** A `milodex strategy run-multi` command hosting multiple strategies via threads or asyncio. Rejected because (i) it supersedes [ADR 0012](0012-runtime-and-dual-stop.md) for marginal benefit; (ii) the dual-stop dialog (Ctrl-C → controlled stop / kill switch) becomes ambiguous in a multi-tenant process; (iii) Python's GIL makes threading offer little for our IO-bound workload, while asyncio adds complexity disproportionate to the use case; (iv) per-process is the simpler and more defensible default.

**(c) Per-strategy position attribution (option (a) from ADR 0024).** Implement strategy-attributed `concurrent_positions` counting at the risk evaluator. Rejected for Phase 3 because (i) it requires position-to-strategy reconciliation against broker positions (which carry no strategy tag) — nontrivial and reliability-sensitive; (ii) ADR 0024's account-scoped enforcement is already sufficient for safety; (iii) Phase 3 §4.1's scope discipline says "smallest expansion." Per-strategy attribution remains a Phase 4+ candidate if and when an operational need surfaces.

## Consequences

- **No code changes to the runner.** The existing `StrategyRunner` and `milodex strategy run <name>` CLI work as-is for one or two concurrent processes. Tests covering the runner (the 24 tests in `tests/milodex/strategies/test_runner.py`) are unchanged.

- **No new CLI command in this commit.** A `milodex strategy run-many` (or similar) launcher could be added in a future commit if the operator wants it, but it would be a thin wrapper over `subprocess.Popen` calls — not architectural.

- **`docs/OPERATIONS.md` may add a "Running Multiple Strategies" section.** That documentation is operator-experience material and lands under the OPERATIONS doc, not in an ADR. (Deferred: the doc update is a separate, small task; this ADR is the architectural decision.)

- **The kill switch is shared across processes.** Per [ADR 0005](0005-kill-switch-manual-reset.md) the kill switch is account-scoped and stored in the event store. When one runner activates the kill switch, the other runner's next `evaluate_intent` call refuses with `kill_switch_active`. This is the intended behavior — the kill switch is a circuit breaker for the account, not for a strategy.

- **Race conditions are operationally rare but architecturally possible.** Two daily-tempo strategies firing at the same close evaluation could both emit BUY intents, both pass `evaluate_intent` against the broker's pre-fire position state, and both fill — pushing the account briefly above `max_concurrent_positions`. For Phase 3's daily tempo with manually-attended runners, this is acceptable: the two evaluations happen sequentially when the operator starts each runner. In practice the runners check at slightly different times. Phase 4+ may revisit if a tighter guarantee is needed.

- **C-2 of [Phase 3 §5](../PHASE3_PLANNING.md) is architecturally satisfied.** The per-process supervisor model is documented; account-scoped enforcement (ADR 0024) is the safety mechanism; existing runner tests cover the per-process behavior. The remaining ≥30 paper-trading-day runtime evidence is an operator-side, calendar-time activity — it cannot be manufactured in commit time. Phase 3 closes on architectural sufficiency; ongoing runtime evidence accumulates naturally as the operator runs the system in paper.

## Non-goals

- This ADR does **not** introduce a multi-tenant runner.
- This ADR does **not** add per-strategy position attribution at the risk layer (option (a) from [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) remains a future Phase 4+ candidate).
- This ADR does **not** alter [ADR 0012](0012-runtime-and-dual-stop.md)'s runtime model or [ADR 0005](0005-kill-switch-manual-reset.md)'s account-scoped kill-switch.
- This ADR does **not** add a launcher CLI command. If one is added later, it is a wrapper around `subprocess.Popen`, not an architectural change.
- This ADR does **not** address what happens when one process crashes mid-cycle — recovery is operator-driven (restart that process) and forensic via the per-`session_id` event-store records.

## Addendum (2026-05-05) — Runner lock scoping correction

The §Consequences claim "**No code changes to the runner.** The existing `StrategyRunner` and `milodex strategy run <name>` CLI work as-is for one or two concurrent processes" was **incorrect at acceptance time**. The runner CLI in `src/milodex/cli/commands/strategy.py` was acquiring a single global advisory lock named `"milodex.runtime"`, which is also held by `reconcile` and `trade submit`. A second `milodex strategy run <strategy_id>` invocation against any `strategy_id` therefore failed with `advisory_lock_held` identifying the first runner — defeating the per-process supervisor model this ADR is intended to authorize.

The operator surfaced the bug shortly after this ADR was accepted by attempting the documented two-terminal pattern with regime + meanrev and receiving the lock error. Investigation confirmed:

- R-EXE-013 ([SRS:135](../SRS.md)) — the Phase 1 "at most one strategy runs at a time" invariant — was still enforced in code via the global lock name.
- The Phase 2+ appendix entry ([SRS:376](../SRS.md)) had marked this restriction as "lifted" since Phase 2, but the lift never actually shipped.
- Today's [PHASE3_PLANNING.md §C-2](../PHASE3_PLANNING.md) declared concurrent multi-strategy "architecturally satisfied" pointing at this ADR, without verifying the enforcement layer.

The fix:

1. **Code.** [`src/milodex/cli/commands/strategy.py`](../../src/milodex/cli/commands/strategy.py) now acquires `f"milodex.runtime.strategy.{args.strategy_id}"` instead of the global `"milodex.runtime"`. `reconcile` and `trade submit` keep the global lock; the two namespaces are disjoint by design (cross-namespace safety is the broker's responsibility per ADR 0024, not file locks').
2. **Tests.** Two new tests in `tests/milodex/cli/test_main.py` pin the invariant from both sides — `test_strategy_run_refuses_second_invocation_of_same_strategy` (same `strategy_id` still refuses) and `test_strategy_run_allows_concurrent_different_strategies` (different `strategy_id`s coexist).
3. **Docs.** [SRS R-EXE-013](../SRS.md) was rewritten to codify the per-strategy lock invariant and preserve the Phase 1 history. [OPERATIONS.md §Concurrency Model](../OPERATIONS.md) was updated to describe the two disjoint namespaces.

This addendum is appended rather than the §Consequences claim being silently corrected, so the original mistake (an architectural decision that didn't verify its enforcement-layer assumption) stays visible in the ADR record. The intent of the original decision — per-process supervisor, runner unchanged in shape, dual-stop dialog preserved, broker-as-arbiter — is unchanged. What was missed was a single-line lock-name change and the doc cleanup that accompanies it.

## Addendum (2026-05-29) — Automated orphan bookkeeping recovery

§Non-goals stated this ADR "does not address what happens when one process crashes mid-cycle — recovery is operator-driven." That posture is **narrowed, not reversed**: the operator is still the supervisor, and a crashed strategy is still **never auto-restarted**. What is now automated is only the *bookkeeping* — closing the orphaned `strategy_runs` row a hard-killed runner leaves behind (`ended_at IS NULL`), which the active-ops read model would otherwise render as a confidently-wrong "live" phantom runner.

Driven by the 2026-05-29 concurrent-fleet soak test, which exposed phantom rows accumulating across an intraday fleet, the existing liveness-gated reaper (`reconcile_orphaned_runs_on_bootstrap`) — previously fired only at GUI bootstrap and same-strategy runner start — is now also triggered:

1. **Periodically** by a main-thread `QTimer` in the GUI (`OrphanReaperController`), at a default 60s interval, configurable via the RUNNER HEALTH preset in the Risk Office drawer and persisted durably via QSettings.
2. **On demand** via `milodex maintenance reap-orphans` (`--dry-run` to preview). Lock-free by design — the reaper is liveness-gated and skips strategies with a live runner — so it does not take the `milodex.runtime` lock and is safe to run alongside live runners.

Critically, the reaper now **re-checks the advisory-lock holder immediately before the close+unlink** and skips the strategy if a holder appeared, or its `started_at` changed, since classification. This closes residual-1 (the bootstrap-reconcile-vs-concurrent-spawn TOCTOU deferred in `docs/reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md`), which periodic reaping plus the GUI's worker-thread async spawn (`bench_command_bridge.py` `QThreadPool` → `subprocess.Popen`) makes first-class rather than the rare CLI-during-bootstrap case the original deferral assumed. The single skip guards both the row-close and the unlink, sound because the spawning subprocess acquires its lock before appending its open row (`strategy.py` enters `with runner_lock:` before `StrategyRunner.__init__` appends the row) — an ordering invariant that must not be reversed.

Design: `docs/superpowers/specs/2026-05-29-periodic-orphan-reconciliation-design.md`. The per-process supervisor model and the operator-owns-recovery posture of this ADR are otherwise unchanged.

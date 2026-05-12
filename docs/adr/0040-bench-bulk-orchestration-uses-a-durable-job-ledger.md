# ADR 0040 — Bench bulk orchestration uses a durable job ledger

**Status:** Accepted · 2026-05-10
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-G (bulk-action wiring), [ADR 0039](0039-stage-session-and-bench-section-are-distinct.md) (stage/session/section separation), [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) (backtest sandbox), [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) (per-process strategy runner), [ADR 0009](0009-promotion-pipeline-stage-model.md) (promotion stage model), [ADR 0005](0005-kill-switch-manual-reset.md) (manual reset and human review), [PHASE6_BENCH_PREP.md](../PHASE6_BENCH_PREP.md)

## Context

[ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) locks a Bench surface. The prototype implied per-card progress copy such as `bt 74% · 2014-2024`, smart-skip counts, cancellation, and error reporting. Q-G asks how those verbs map onto production mechanics.

Production already has two run ledgers:

- **`backtest_runs`** records individual backtest / walk-forward executions. A walk-forward run appends a row with `status='running'`, then updates to `completed` or `failed`; orphan recovery marks stale rows `orphan_recovered`. Research screen uses `run_batch()` and can dispatch per-strategy work in parallel through `ProcessPoolExecutor`.
- **`strategy_runs`** records foreground paper-runner sessions. A row opens at runner startup, remains active while `ended_at IS NULL`, and closes with `ended_at` / `exit_reason`.

[ADR 0039](0039-stage-session-and-bench-section-are-distinct.md) deliberately keeps promotion stage, runtime session state, and Bench section separate. This ADR answers the next question: where does Bench-requested *work* live before it has produced a `backtest_runs` or `strategy_runs` row, and how does the GUI observe progress/cancel/failure without pretending a section move is itself execution?

The existing CLI batch path is not enough by itself. `run_batch()` is a synchronous command helper: it returns a ranked table and writes individual `backtest_runs`. It has no durable parent batch, no queued state before a child process begins, no cancellation request surface, and no place to record per-card progress outside each backtest's metadata. Stretching `backtest_runs` to mean "queued Bench request" would blur an execution record with an operator work request. Stretching `strategy_runs` to mean "queued paper session" would do the same.

Bulk actions, if/when they surface, are triggered from the `Action` menu (e.g. a section-level "Run all in BACKTEST"), not from a toolbar. Bulk is not in Phase 6 v1; the architecture below is forward-facing.

## Decision

1. **Bench bulk actions create durable orchestration jobs before execution begins.** A future implementation introduces a small job ledger for GUI/CLI orchestration requests. The ledger records operator intent, queue state, progress, cancellation requests, and the link to the eventual execution record.

2. **The job ledger has a parent/child shape.**

   - A parent `orchestration_batches` row represents one operator bulk action, such as "run backtests for these four strategies."
   - A child `orchestration_jobs` row represents one strategy/action item inside the batch.
   - Each job links to at most one concrete execution record: `backtest_runs.run_id` for backtest work, or `strategy_runs.session_id` for foreground strategy-run work.

3. **Supported action types are explicit and narrow.**

   - `backtest_walk_forward`: run a walk-forward backtest for a strategy in the BACKTEST section.
   - `paper_session_start`: start a foreground paper session for a strategy in the PAPER section.
   - `micro_live_session_start`: reserved for a future ADR that opens micro-live; invalid while [ADR 0004](0004-paper-only-phase-one.md) remains in force.

   Promotion, demotion, kill-switch reset, and live deployment are not bulk orchestration jobs. They remain separately human-reviewed actions governed by their existing ADRs.

4. **Job status vocabulary is shared across action types.** Jobs move through:

   `queued → starting → running → completed`

   Terminal alternatives are:

   `failed`, `cancelled`, `blocked`, `orphan_recovered`

   `cancel_requested` is a timestamp/flag, not a terminal status. A job becomes `cancelled` only after the worker actually observes the request and stops before producing further side effects.

5. **Backtest bulk actions map onto existing walk-forward mechanics, with a durable wrapper.** A `backtest_walk_forward` job eventually calls the same code path as `milodex research screen` / `run_walk_forward`, not a new evaluator. Its `backtest_runs.run_id` is written to the job once the run row exists. Progress is derived from planned/completed walk-forward windows and stored on the job as compact JSON for the GUI. The authoritative performance evidence remains in `backtest_runs.metadata_json`.

6. **Paper-session bulk actions are launch requests, not background-daemon authorization.** A `paper_session_start` job may spawn or request a foreground runner process using the existing `milodex strategy run` contract and per-strategy advisory lock. The resulting `strategy_runs.session_id` becomes the job's execution link. This ADR does not authorize unattended overnight service behavior, daemon runtime, or automatic restart. Any persistent background supervisor requires a future ADR.

7. **Cancellation is cooperative and best-effort.** The operator can request cancellation of queued or running jobs. Queued jobs can be cancelled before start. Running backtests check the cancel flag at safe boundaries, at minimum between walk-forward windows; they must not interrupt a database transaction mid-write. Running paper sessions follow the existing controlled-stop / kill-switch distinction: normal cancellation requests controlled stop; kill-switch remains the separate critical action from [ADR 0005](0005-kill-switch-manual-reset.md).

8. **Smart-skip counts come from job/status queries, not from visual section counts.** Counts reflect queued/eligible strategies that do not already have a non-terminal job for the same action. A card already `queued`, `starting`, or `running` for that action is skipped. A card blocked by stage/gate rules contributes to blocked copy, not to the count.

9. **No promotion or stage mutation happens as a side effect of a completed job.** A completed backtest may update eligibility verdicts and surface a "gate passing" state. The operator must still promote deliberately through the promotion path. A completed paper-session start means a runner started; it does not advance `paper → micro_live`.

10. **Workers revalidate authority at start time.** `requested_stage` records the stage observed when the operator queued the job; it is audit context, not permission to execute later. Before a job starts, the worker must re-read current config, promotion state, manifests, kill-switch state, advisory locks, and any relevant non-terminal jobs/sessions. A stale job becomes `blocked` with clear copy rather than running under old assumptions.

## Minimal schema contract

Exact migration names and column order belong to implementation, but the durable model must carry these concepts.

```text
orchestration_batches
  id
  batch_id              # stable uuid/string shown to the operator
  action_type           # backtest_walk_forward | paper_session_start | ...
  requested_by
  requested_at
  status                # queued | running | completed | failed | cancelled | partial
  metadata_json

orchestration_jobs
  id
  job_id                # stable uuid/string shown to the operator
  batch_id              # FK/reference to orchestration_batches.batch_id
  strategy_id
  action_type
  requested_stage       # promotion stage observed/requested at queue time
  status
  queued_at
  started_at
  ended_at
  cancel_requested_at
  execution_ref_type    # backtest_run | strategy_run | null
  execution_ref         # backtest_runs.run_id or strategy_runs.session_id
  progress_current
  progress_total
  progress_label
  error_code
  error_message
  metadata_json
```

The implementation may normalize enum validation in Python rather than SQLite CHECK constraints if that fits existing migration style. The key is that the GUI can recover the board state after a restart without consulting process memory.

## Rationale

**Operator intent deserves its own durable record.** A backtest run is evidence that execution happened. A strategy run is evidence that a runner process lived. A Bench bulk action job is evidence that the operator asked for work to happen. Those are related, but not identical. Keeping the job ledger separate preserves the meaning of existing tables while giving the GUI a place to show queued and cancelled work.

**A parent batch is necessary for bulk UX.** One operator action may create N child jobs. Without a parent row, the UI cannot answer "what did I just start?", cannot summarize partial completion, and cannot cancel the remaining queued children as a group. The batch row is the audit spine for the visible bulk gesture.

**Progress has to exist before performance evidence exists.** `backtest_runs.metadata_json` is good final evidence, and walk-forward already records planned windows. But the GUI needs to show that a job is queued, starting, 1/4 windows complete, or cancellation requested. Some of those states happen before a `backtest_runs` row exists or before metadata is final. The job row is the right place for transient-but-durable progress.

**Cooperative cancellation matches Milodex's safety posture.** Hard-killing workers is tempting for UI responsiveness, but it risks half-written records and ambiguous audit state. Safe-boundary cancellation is slower and cleaner. For paper runners, the existing controlled-stop vs kill-switch distinction stays intact: "cancel this session" is not the same as "activate the kill switch."

**This ADR avoids accidentally authorizing a daemon.** A Bench that can start sessions can feel like a background control plane. Milodex's current runtime model is still manually-invoked foreground strategy processes. This ADR permits a durable launch request and visible job state; it does not authorize always-on autonomous supervision or restart behavior.

## Consequences

- **ADR 0036 Q-G is answered.** Bench bulk actions use a durable parent/child job ledger and link to existing execution records.
- **A future implementation PR adds migrations and EventStore APIs** for orchestration batches/jobs before wiring Action menu bulk triggers.
- **Bench read models consume jobs alongside stage/session data.** Stage comes from promotion/config state, session liveness from `strategy_runs`, backtest evidence from `backtest_runs`, and queued/running orchestration state from the new job tables.
- **`run_batch()` remains useful but becomes a worker implementation detail.** The Bench does not call it as an opaque synchronous UI action; it uses the same lower-level walk-forward mechanics behind a durable job wrapper.
- **Cancellation UI can ship before hard process termination exists.** Queued jobs cancel immediately; running jobs cancel at safe boundaries. The UI must say "cancel requested" until the worker confirms `cancelled`.
- **No promotion, demotion, or live-boundary behavior is authorized.** Completed jobs produce evidence and liveness state only. Stage transitions remain explicit human-reviewed actions.
- **Bench bulk actions do not bypass advisory locks.** Existing per-strategy locks remain the guard against duplicate same-strategy runners. A locked strategy job becomes `blocked` or `failed` with clear copy, not a second runner.

## Non-goals

- Does not implement the job ledger.
- Does not choose the process model for the worker loop beyond durable jobs and cooperative cancellation.
- Does not authorize a daemon, auto-restart, or unattended overnight running.
- Does not authorize micro-live or live trading.
- Does not implement Action menu transitions or any stage mutation.
- Display-name provenance is decided separately by [ADR 0041](0041-bench-display-names-are-presentation-metadata.md).
- Live/micro-live eligibility is decided separately by [ADR 0042](0042-live-and-micro-live-eligibility-is-locked-and-evidence-based.md).
- Demotion action security is decided separately by [ADR 0043](0043-bench-demotion-actions-open-a-governance-flow.md).
- Action availability is decided separately by [ADR 0047](0047-bench-action-availability-is-the-validation-surface.md).
- Responsive layout is decided separately by [ADR 0048](0048-bench-uses-vertical-stage-sections-with-natural-scroll.md).
- Stage-hue token reconciliation is decided separately by [ADR 0046](0046-bench-stage-hues-extend-production-tokens.md).

# ADR 0039 — Stage, session, and Bench section are distinct

**Status:** Accepted · 2026-05-10
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-B (stage / session decoupling), [ADR 0038](0038-phase-5-is-closed-and-phase-6-may-open.md) (Phase 6 may open), [ADR 0009](0009-promotion-pipeline-stage-model.md) (promotion stage model), [ADR 0015](0015-strategy-identifier-and-frozen-manifest.md) (manifest discipline), [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) (per-process runner model), [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) (backtest sandbox), [PHASE6_OPERATOR_KANBAN_PREP.md](../PHASE6_OPERATOR_KANBAN_PREP.md), [DESIGN.md](../DESIGN.md)

## Context

[ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) locks the visual spec for the Phase 6 operator Bench and explicitly defers its mechanics. Its Q-B asks how to distinguish "stage" from "session." The v0.3 prototype uses a `paused` flag and visible `running` / `idle` copy, but those terms are demo-state conveniences, not production semantics.

Production already has two durable concepts that must not be collapsed:

- **Promotion stage** lives in strategy config, frozen manifests, and promotion events. It answers: *what environment is this strategy allowed to operate in?*
- **Strategy session** lives in `strategy_runs`. It answers: *is a foreground runner process currently alive, and how did the most recent session end?*

Phase 2's CI-2 cleanup made `strategy_runs` useful as a liveness surface: runners create a row at startup, leave `ended_at=NULL` while active, and set `ended_at` / `exit_reason` at shutdown. PR #54 later hardened crash and orphan handling. The existing canonical query is therefore direct: `strategy_runs WHERE ended_at IS NULL` enumerates active sessions.

The Bench adds a third concept that looks similar but is not the same: a **stage section**. ADR 0036's IDLE section means "configured, not currently queued for an action on this board." It is not a promotion stage in [ADR 0009](0009-promotion-pipeline-stage-model.md), and it is not the same thing as a stopped runner.

Without a decision here, Phase 6 could accidentally make the most common workflow mistake in operational UI design: treating a noun about eligibility ("paper-stage") as if it were a verb about process liveness ("paper session running"). That would make the Bench feel alive while quietly corrupting what the operator thinks the board means.

## Decision

1. **Promotion stage, runtime session state, and Bench section are three separate axes.**

   - **Promotion stage** is durable eligibility/governance state: `backtest`, `paper`, `micro_live`, `live`.
   - **Session state** is process/runtime state derived from runner and job records: `not_running`, `running`, `stopped`, `failed`, plus future orchestration states such as `queued` or `canceling` when those mechanisms exist.
   - **Bench section** (read-model field: `stage_section`) is the board placement / intended next-action grouping. It may include stage sections such as `idle`, `backtest`, `paper`, `micro_live`, and `live`, but section placement does not by itself promote, demote, start, stop, or authorize a strategy.

2. **Promotion stage remains the authority for what a strategy may do.** Stage is sourced from config/manifests/promotion events, and transitions continue through the promotion state machine. The Bench cannot change a strategy's stage by writing a section value. Any Action menu gesture that requests a stage transition must call the same promotion/demotion path the CLI uses, with the same gate and human-review constraints.

3. **`strategy_runs` remains the canonical session-liveness surface for foreground strategy runners.** A runner is active when the latest relevant `strategy_runs` row has `ended_at IS NULL`. A runner is stopped when `ended_at` is populated and `exit_reason` is non-failing. A runner is failed when `exit_reason` records a crash, interrupt, kill-switch, orphan recovery, or other failure-class end reason. The GUI may summarize this as "running", "stopped", "failed", or "not running", but it must not infer promotion eligibility from it.

4. **No new `sessions` table lands for Q-B.** The current `strategy_runs` table is sufficient for runner liveness and audit history. A new table is justified only if a later ADR for bulk orchestration or background jobs proves that `strategy_runs` and `backtest_runs` cannot represent the needed lifecycle without ambiguity.

5. **Backtest execution state is not forced into `strategy_runs`.** Backtests are sandbox research/evaluation jobs per [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md). Historical backtest evidence belongs in `backtest_runs`; future bulk backtest progress/cancellation belongs to the bulk-orchestration ADR, not this stage/session decision. The Bench may show backtest progress, but it must not represent a backtest worker as a live paper runner.

6. **`idle` is a section label, not a stage and not a session state.** A strategy can be at promotion stage `backtest` and still appear in the Bench IDLE section if no backtest action is queued or running. A strategy can be at promotion stage `paper` and have session state `not_running`. Those are normal, not contradictory.

## Required read-model shape

Phase 6 Bench read models should expose the axes separately. A card-shaped object should have fields equivalent to:

```text
strategy_id
display_name
promotion_stage      # backtest | paper | micro_live | live
stage_section        # idle | backtest | paper | micro_live | live
session_state        # not_running | queued | running | stopped | failed | canceling
session_id           # present only for a concrete runner/job session
session_detail       # exit reason, progress copy, or last activity summary
eligibility_verdict  # gate-ready | blocked | not_evaluated | locked | unknown
```

The exact names can adapt to the implementation language/QML bridge. The separation cannot be collapsed.

## Rationale

**The operator needs to know both "where is it allowed to be?" and "is anything alive right now?"** These questions often have different answers. A paper-stage strategy may be idle because the runner is closed for the weekend. A backtest-stage strategy may have a backtest job running. A live-stage strategy, if a future ADR opens that boundary, may be deliberately stopped. The board must make those combinations legible instead of flattening them.

**Existing mechanics already solve runner liveness.** Phase 2 fixed the original gap where `strategy_runs` rows were written only at shutdown. The current contract is exactly what the GUI needs: startup creates an open row, shutdown closes it, crash/orphan reconciliation prevents permanent phantom sessions. Adding another sessions table now would duplicate the source of truth and invite drift.

**The Bench IDLE section is useful, but dangerous if over-literal.** ADR 0036's IDLE section is an operator-planning affordance: "nothing is queued here." It is not part of the promotion ladder. Treating `idle` as a promotion stage would conflict with [ADR 0009](0009-promotion-pipeline-stage-model.md). Treating it as session state would make stopped paper strategies look like pre-backtest strategies. Both would damage trust.

**This decision keeps Phase 6 implementation incremental.** The first Bench can render promotion stage and session state side by side before write mechanics exist. Later ADRs can add queue semantics, bulk orchestration, Action menu transitions, and demotion confirmations without changing the core vocabulary.

## Consequences

- **ADR 0036 Q-B is answered.** Stage and session do not share a field, table, or source of truth.
- **Bench cards must reserve UI space for both stage and session.** A stage chip answers eligibility; a session/activity chip answers liveness or job state.
- **Action menu transitions cannot be implemented as "move card to section and save section."** A section move is only a request. Durable promotion still goes through the promotion path and writes the existing promotion/manifest records.
- **Bulk backtest/session orchestration remains a separate ADR.** That later ADR decides queued/progress/cancel semantics and whether any new job table is needed.
- **The Phase 5 BENCH surface remains view-only and compatible.** It can keep rendering stage-centered evidence. Phase 6 Bench extends the read model by adding section/session axes; it does not reinterpret Phase 5 rows.
- **No live boundary moves.** This ADR is vocabulary and source-of-truth architecture only. It does not authorize `micro_live`, `live`, or any capital-affecting action.

## Non-goals

- Does not implement the Bench surface.
- Display-name provenance is decided separately by [ADR 0041](0041-bench-display-names-are-presentation-metadata.md).
- Live/micro-live eligibility is decided separately by [ADR 0042](0042-live-and-micro-live-eligibility-is-locked-and-evidence-based.md).
- Demotion action security is decided separately by [ADR 0043](0043-bench-demotion-actions-open-a-governance-flow.md).
- Action availability is decided separately by [ADR 0047](0047-bench-action-availability-is-the-validation-surface.md).
- Responsive layout is decided separately by [ADR 0048](0048-bench-uses-vertical-stage-sections-with-natural-scroll.md).
- Bulk orchestration and progress/cancellation storage are decided separately by [ADR 0040](0040-bench-bulk-orchestration-uses-a-durable-job-ledger.md).
- Stage-hue token reconciliation is decided separately by [ADR 0046](0046-bench-stage-hues-extend-production-tokens.md).
- Does not implement Action menu transitions.

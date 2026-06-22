# ADR 0049 — Phase 6 Bench v1 is a visual prototype with no backend mutation

**Status:** Accepted (2026-05-12) — **amended in part by [ADR 0051](0051-bench-command-infrastructure-v1.md)**. The six Bench command families ADR 0051 opened do mutate backend state; for every path ADR 0051 did **not** open, this ADR's no-backend-mutation perimeter remains binding. Amended, **not** superseded.
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) (Bench visual spec), [ADR 0047](0047-bench-action-availability-is-the-validation-surface.md) (Action menu), [ADR 0048](0048-bench-uses-vertical-stage-sections-with-natural-scroll.md) (vertical layout), [ADR 0050](0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md) (evidence freshness), [ADR 0012](0012-runtime-and-dual-stop.md) (dual-stop semantics), [ADR 0040](0040-bench-bulk-orchestration-uses-a-durable-job-ledger.md) (bulk orchestration), [ADR 0004](0004-paper-only-phase-one.md) (paper-only lock), [ADR 0005](0005-kill-switch-manual-reset.md) (kill switch), [ADR 0051](0051-bench-command-infrastructure-v1.md) (command infrastructure v1 — partial amendment of this ADR)

## Context

The Bench's layout decisions (vertical stage sections, Action-menu-driven state changes, per-row priority reorder, hidden-when-unavailable actions) are established across ADRs 0036, 0047, and 0048. The verb grammar and read-model shape are established in ADR 0050. Implementation is the next program of work.

Before any implementation PR begins, the **scope** of the v1 implementation needs to be locked. The Bench surface touches paths that, if wired naively, could call the broker, write to the event store, initiate backtest jobs, alter strategy priority, or trigger a kill-switch transition. None of those paths are appropriate to wire while the interaction model is still being validated by use.

The v1 surface must be exercised before its write paths open. A wired-then-validated approach inverts the risk: bugs discovered post-wiring touch persistent state and require rollback ceremony. A validated-then-wired approach lets the menu, the modals, the drag mechanic, and the read-model derivations be tested for *feel* and *correctness of computed state* without exposing operator actions to consequence.

## Decision

1. **v1 ships as a visual prototype.** The Bench renders the full interaction model: vertical stage sections, per-row Action menu, hidden-when-unavailable items, evidence modals, within-section drag for priority reorder. Every menu item is reachable; every modal opens; every interaction completes. No interaction mutates backend state.

2. **The following are explicitly forbidden in v1.** Any contributor who finds a Bench path requiring one of these must escalate before proceeding:
   - Promotion writes — no operator-driven stage transition is persisted
   - Demotion writes — same
   - Broker calls — the Alpaca client is not invoked from any Bench code path
   - Backtest execution — no actual backtest job is created or queued
   - Trading-session start/stop — no `strategy_runs` row is opened or closed by Bench paths
   - Persisted priority reorder — within-section drag survives only the lifetime of the current QML view
   - Event-store writes from Bench code paths — no operator-action ledger records are written
   - Kill-switch triggers — Bench paths do not interact with kill-switch state

3. **Stage transitions split into two classes.**
   - **Operator-driven** (`Promote to X`, `Demote to X`, `Return to X`) — explicit Action menu items. v1 renders the menu and the confirmation modal but does not commit any state change.
   - **System-driven** (e.g. `IDLE → BACKTEST` on backtest job acceptance per ADR 0050) — no operator verb. Not exercised in v1 because no backtest jobs are accepted.

4. **`Stop Trading` maps to controlled-stop semantics, not kill switch.** When the menu's `Stop Trading` modal eventually wires (post-v1), it will call the runner's `shutdown(mode="controlled")` path which produces `exit_reason="controlled_stop"` (see [`src/milodex/strategies/runner.py`](../../src/milodex/strategies/runner.py) and ADR 0012). This finishes the current cycle, closes the `strategy_runs` row cleanly, and does **not** cancel open orders. The kill switch remains a separate, global operator affordance owned by the Anchor view; it is not a Bench Action menu item.

5. **v1 fixture data must exercise the full menu state space.** The demo strategy collection covers, at minimum:
   - Strategies at every promotion stage (IDLE, BACKTEST, PAPER, MICRO LIVE, LIVE)
   - Each `Freshness` value (Missing, Fresh, Aging, Stale, Invalidated) per relevant stage record
   - Each `GateResult` value (Pass, Fail, Pending; NotApplicable for LIVE)
   - At least one fixture row that exercises every menu rule defined in ADR 0047 and ADR 0050
   - `Open Evidence` is verifiable as the menu's empty-menu floor on every fixture

## Rationale

**The hard line at "no backend mutation" is enforceable.** A softer line ("limited writes," "low-risk writes only") is not — every contributor draws it differently and the prototype accumulates partial wirings that the v2 ADR has to unwind. A binary scope is the only one that survives multi-PR drift.

**Validating feel before wiring inverts the right risk.** Bugs found in v1 are bugs in QML rendering, menu computation, fixture data, or read-model derivation. None of those touch persistent state. Bugs found post-wiring touch the event store, the broker, or strategy lifecycle records, and require investigation against real history. v1 buys cheap iteration on the surface that will eventually carry the most consequence.

**Mapping `Stop Trading` to controlled-stop preserves the existing dual-stop semantic.** ADR 0012 distinguishes controlled stop (finish cycle, close cleanly) from kill switch (cancel orders, hard halt). Conflating `Stop Trading` with kill switch would erode that distinction at exactly the surface where the operator's intent is most ambiguous. Keeping the kill switch on the Anchor view (where it already lives) preserves the principle that emergency-stop is a global affordance, not a per-strategy menu item.

**Separating operator-driven from system-driven transitions pays off in audit design.** When v2 wires writes, operator events name an actor and an explicit verb; system events name an event source and an automatic trigger. Fusing both classes into "stage transitions" loses that distinction, which is exactly what an audit reader needs to reconstruct what happened.

**Fixture coverage of the full state space is what makes the prototype useful.** A prototype that renders one row per stage cannot exercise the menu's branching behavior. The interaction model's correctness is exactly the conditional rendering of menu items as a function of `(current_stage, evidence_by_stage)`. Fixtures that span the state space let the prototype be evaluated by inspection rather than by argument.

## Consequences

- **The v1 implementation may consume the read-model schema from ADR 0050 but does not implement the freshness computation.** Fixture data assigns `Freshness` and `GateResult` values directly. Real computation (manifest drift, age thresholds, divergence detection) is v2 work.
- **Action menu modals render confirmation copy and dismiss cleanly.** They do not call the broker, the event store, the runner control surface, or the backtest harness. The modal copy may use placeholder phrasing such as "this would have done X" where helpful.
- **Within-section drag reorders rows visually for the lifetime of the QML view.** A page navigation, view switch, or app restart loses the reorder. No persistence layer is wired in v1.
- **Bench code paths must not import or call kill-switch state, broker clients, backtest executors, or `EventStore` write methods.** The read-model layer is read-only for v1; it consumes the existing event store via existing read paths only. New write paths require a fresh ADR before they open.
- **Tests for v1 assert menu visibility, modal content, fixture-driven state derivation, and visual rendering.** Any test that asserts persistent state mutation as the result of a Bench interaction is testing the wrong surface for v1.
- **v2 (the wiring program) will supersede this ADR with a follow-up that names which write paths open and under what gates.** That follow-up ADR is expected to land alongside the first wiring PR, not before it. The list of forbidden interactions in Decision 2 becomes the v2 ADR's checklist of paths to open deliberately, one at a time.
- **The existing Phase 6 Bench code in the repo (`BenchSurface`, `BenchRow`, prototype Action menu, in-section reorder) is reconciled to this ADR rather than rebuilt.** Any code in those modules that performs a write the v1 scope forbids is removed or stubbed before downstream PRs proceed.

## Non-goals

- Does not specify the read-model schema (see [ADR 0050](0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md)).
- Does not specify Action menu computation rules (see [ADR 0047](0047-bench-action-availability-is-the-validation-surface.md)).
- Does not specify visual treatment, modal copy, or section header markup.
- Does not authorize any live or micro-live action.
- Does not change ADR 0004's paper-only lock.
- Does not retire or reshape the kill-switch surface; that remains the Anchor view's concern.

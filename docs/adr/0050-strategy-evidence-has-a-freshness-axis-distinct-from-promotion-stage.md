# ADR 0050 — Strategy evidence has a freshness axis distinct from promotion stage

**Status:** Accepted - 2026-05-12
**Related:** [ADR 0047](0047-bench-action-availability-is-the-validation-surface.md) (Action menu computation), [ADR 0049](0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) (v1 prototype scope), [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) (Bench visual spec), [ADR 0009](0009-promotion-pipeline-stage-model.md) (promotion stage model), [ADR 0011](0011-sqlite-event-store.md) (event store), [ADR 0015](0015-strategy-identifier-and-frozen-manifest.md) (manifest discipline), [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) (backtest exploratory), [ADR 0042](0042-live-and-micro-live-eligibility-is-locked-and-evidence-based.md) (eligibility evidence)

## Context

The Bench read-model needs to represent a strategy's evidence at each stage as a first-class concept, distinct from the strategy's current promotion stage. Stage alone cannot answer the questions the Action menu must compute.

IDLE is the **inactive shelf**, not an evaluation state. A strategy on the shelf may carry evidence from prior stages — it might have been at MICRO LIVE before being shelved for reasons unrelated to its performance. The operator's `Return to X` verbs need to know whether the prior-stage evidence is still trustworthy, which `current_stage` cannot tell them.

Two further pressures push toward an explicit evidence axis:
- **Failure isn't staleness.** A backtest that just completed and failed the gate has fresh evidence and a fail verdict. Conflating freshness with gate-pass produces a state space that cannot represent that case.
- **Invalidation is event-driven.** Manifest drift, code change, config change, risk-policy change, methodology change, data-source change, and fee/slippage assumption change all invalidate evidence regardless of age. A purely time-based staleness model misses this class of invalidation entirely.

The right model is two orthogonal axes attached to a per-stage evidence record set: **freshness** (when did we last observe this and is it still trustworthy?) and **gate_result** (did the observation meet criteria?).

## Decision

1. **Strategy evidence is a per-stage record set, not a global value.** The Bench read-model surfaces `evidence_by_stage: dict[Stage, EvidenceRecord]`. Each record represents the strategy's evaluation history at that stage and is consulted independently by the menu rules. The shelf case (IDLE strategy with prior PAPER and MICRO LIVE history) is represented by populating the relevant per-stage records.

2. **Each `EvidenceRecord` carries two orthogonal axes.**
   - `freshness: Freshness` — `Missing | Fresh | Aging | Stale | Invalidated`
   - `gate_result: GateResult` — `Pass | Fail | Pending | NotApplicable`

   Freshness asks *when*; gate_result asks *what*. They do not depend on each other. A given record can be in any combination of the two values that the invariants below permit.

3. **Freshness state semantics (locked).**
   - **Missing** — no completed usable evidence exists for this stage. Always pairs with `gate_result=Pending` (no verdict has been produced). Whether a backtest run is currently in flight at this stage is **operational run state**, not evidence — it lives on a separate per-stage map on the strategy read model (`runs_in_flight: dict[Stage, bool]`), not inside the `EvidenceRecord`. The menu rule consults that map alongside the evidence record (see Decision 5). The pair (`Missing+Pending`, `runs_in_flight[stage]=True`) represents an accepted run that has not completed; (`Missing+Pending`, `runs_in_flight[stage]=False`) represents "no run started yet."
   - **Fresh** — recent and accepted; current. No claim about gate result.
   - **Aging** — older than the "fresh" threshold but younger than the "stale" threshold. The freshness axis is still usable for `Promote` and `Return` paths *if* `gate_result == Pass`; the modal warns about approaching staleness. With `gate_result == Fail`, Aging behaves like a Fail evidence record and triggers `Initiate Backtest` per Decision 5 — the freshness gradient is preserved in the audit trail, not in the verb offered.
   - **Stale** — past the "stale" threshold. Cannot be used for state-changing verbs. Operator must `Refresh Backtest` (when prior gate was a Pass) or `Initiate Backtest` (otherwise).
   - **Invalidated** — explicitly killed by an event (manifest drift, code change, config change, risk-policy change, backtest methodology change, data-source change, fee/slippage assumption change). Cannot be used. Operator must `Initiate Backtest`.

4. **GateResult value semantics (locked).**
   - **Pass** — gate criteria met.
   - **Fail** — gate criteria not met.
   - **Pending** — evaluation accepted, in flight, or otherwise without a verdict.
   - **NotApplicable** — only valid for LIVE-stage evidence. LIVE has no further promotion gate; the value records that fact rather than encoding it as record absence.

5. **Menu computation rules consume both axes.** The Action menu's per-row visibility is computed in the Python read-model layer per ADR 0047. The rules:

   ```python
   def can_promote_to_next(strategy):
       """Is the operator allowed to Promote to the next stage from current?"""
       ev = strategy.evidence[strategy.current_stage]
       return ev.freshness in {Fresh, Aging} and ev.gate_result == Pass

   def can_return_to(strategy, target_stage):
       """Is the operator allowed to Return to a previously-evaluated stage?

       Return verbs (other than Return to Idle) are the leave-IDLE affordance:
       a shelved strategy (current_stage == IDLE) bringing prior evidence back
       into use. They are not available from active stages, where Promote and
       Demote are the directional operators.
       """
       if target_stage == strategy.current_stage:
           return False
       if target_stage == IDLE:
           # Return to Idle is the to-shelf affordance; available from any
           # active stage. No freshness check — IDLE is the inactive shelf,
           # not an evaluated state.
           return strategy.current_stage != IDLE
       if strategy.current_stage != IDLE:
           # Return to an active stage from another active stage is not a
           # Bench operator verb; use Promote or Demote instead.
           return False
       ev = strategy.evidence[target_stage]
       if ev.freshness not in {Fresh, Aging}:
           return False
       if target_stage == LIVE:
           return ev.gate_result in {Pass, NotApplicable}
       return ev.gate_result == Pass

   def re_run_verb(evidence, is_run_in_flight: bool):
       """Which re-run verb (if any) should appear for this evidence record?

       `is_run_in_flight` is **operational run state**, not evidence. It is
       sourced from the strategy read-model's `runs_in_flight: dict[Stage,
       bool]` map at the relevant stage. When True, no re-run verb appears
       regardless of evidence state — Open Evidence carries the monitoring
       affordance.
       """
       if is_run_in_flight:
           return None  # don't offer a second run while one is in flight
       if evidence.freshness in {Aging, Stale} and evidence.gate_result == Pass:
           return "Refresh Backtest"
       if evidence.freshness == Missing:
           # No completed evidence and nothing in flight — start one.
           return "Initiate Backtest"
       if evidence.freshness == Invalidated:
           return "Initiate Backtest"
       if evidence.gate_result == Fail and evidence.freshness in {Aging, Stale}:
           return "Initiate Backtest"
       # Fresh+Pass, Fresh+Fail, and any LIVE-stage evidence (gate_result
       # == NotApplicable) do not surface a re-run verb by default. For the
       # Fresh cases, an invalidating change (config edit, risk-policy
       # update, etc.) must transition the evidence to Invalidated first;
       # only then does Initiate Backtest appear. This enforces "do
       # something different before retrying" rather than allowing blind
       # re-runs.
       return None
   ```

   **Demote and the menu rules.** `Demote to Backtest` is not in the pseudocode above because its availability is governance-gated by [ADR 0043](0043-bench-demotion-actions-open-a-governance-flow.md), not evidence-gated. The verb is available whenever `current_stage` is at PAPER or higher; the modal it opens is responsible for the typed-confirmation friction for capital-affecting demotions. The read-model layer implements `can_demote` as a separate function alongside the three above.

6. **`IDLE → BACKTEST` is system-driven on backtest job acceptance.** When the operator invokes `Initiate Backtest` on an IDLE strategy, the system transitions the row to BACKTEST when the backtest job is accepted/created — not when it completes. There is no `Promote to Backtest` operator verb. Leaving an actively-running backtest at IDLE would produce a stale read-model; the moment work is taken is the moment the row should reflect that. (The system-driven transition class is described in ADR 0049 Decision 3.)

7. **Verb grammar (locked).** The Action menu's verbs are grouped into three classes:
   - **Directional (operator-driven)** — `Promote to Paper`, `Promote to Micro Live`, `Promote to Live`, `Demote to Backtest`, `Return to Paper`, `Return to Micro Live`, `Return to Live`, `Return to Idle`. The `Return to <active stage>` verbs (`Return to Paper`, `Return to Micro Live`, `Return to Live`) are the leave-IDLE affordance and surface only when the target stage's evidence is `Fresh|Aging` + `Pass` (or `NotApplicable` for LIVE) per the `can_return_to` rule. `Return to Idle` is the to-shelf affordance and surfaces from any active stage. Use `Return to Idle`; do not use `Send to Idle`. There is no `Promote to Backtest` verb.
   - **Invocation (operator-driven)** — `Initiate Backtest`, `Refresh Backtest`, `Start Trading`, `Stop Trading`. `Stop Trading` maps to controlled-stop semantics, not kill switch (see ADR 0049 Decision 4).
   - **Informational (empty-menu floor)** — `Open Evidence`. Always present per ADR 0047 Decision 5.

   Stage transitions and action invocations do not fuse. A re-test of a PAPER strategy is two clicks: `Demote to Backtest` (operator-driven), then `Initiate Backtest` on the resulting BACKTEST row. The auditable trail names both events.

8. **v1 implements the schema only; freshness computation is deferred.** The Bench read-model dataclass gains `evidence_by_stage` and the `Freshness` / `GateResult` enums in v1. Fixture data assigns values directly per ADR 0049's fixture-coverage requirement. Real freshness computation — manifest-drift detection, age-threshold enforcement, methodology/data-source change tracking, paper/live divergence detection, event-store invalidation events — is v2 work and will be specified by a follow-up ADR alongside the wiring program.

## Rationale

**Per-stage records match the existing event-store grain.** Backtest runs, strategy runs (paper / micro-live / live), and frozen manifests are all already per-stage in the event store (ADR 0011, ADR 0015). A per-strategy evidence axis is a derived view over data that already exists per-stage. A global `evidence_status` field would impose a different grain on the read-model than the underlying truth, requiring lossy aggregation.

**Orthogonal freshness and gate_result let the read-model represent states that a fused axis cannot.** A Fresh+Fail run (just-completed backtest that didn't pass) and a Stale+Fail run (old failing evidence) demand different operator action. Conflating them loses the distinction. The orthogonality also accommodates the Missing case cleanly: freshness records "no completed evidence" and gate_result records "no verdict" (`Pending`) without either axis having to model run-in-flight semantics. The in-flight-vs-not-yet-started distinction is operational run state, kept separately on the strategy read model as `runs_in_flight: dict[Stage, bool]` so the evidence-axis enums stay purely about historical evidence.

**The five freshness states preserve forensic distinctions even where menu effects collapse.** Missing and Invalidated produce the same operator menu (`Initiate Backtest`) but mean different things in the audit trail: "no backtest has ever run" vs. "a prior backtest was invalidated by event X on date Y." Aging and Stale share the Pass-with-aging gradient — Aging surfaces a warning band where the evidence is still usable but approaching staleness; Stale is the cliff where it stops being usable. Forensic distinctions are cheap at the schema level and become load-bearing once the audit-trail design lands in v2.

**The four gate_result values keep evaluation status orthogonal to evaluation history.** Pending captures "in flight, no verdict," distinct from Missing's "no completed evidence." `NotApplicable` explicitly marks LIVE's terminal status as a value rather than as record absence — encoding it as absence would make LIVE-stage evidence invisible to the read-model, which is wrong.

**The `NotApplicable` wildcard is restricted to LIVE-stage Return because LIVE alone has no promotion gate.** Allowing `NotApplicable` at any stage would create a convenience escape that bypasses gate semantics. The asymmetry tracks a real terminal-state distinction in the pipeline.

**The `Refresh` vs `Initiate` boundary is pass-or-fail-aware.** `Refresh` carries the connotation of "this works, but it's been a while" — that fits Aging+Pass and Stale+Pass exactly, and badly fits any *Fail variant. `Initiate` produces new evidence from a non-usable baseline: `Invalidated` evidence at any age, `(Aging|Stale)+Fail` evidence, and Missing evidence with no in-flight run. The semantic boundary keeps the audit trail honest: a `Refresh` event implies prior usable evidence existed; an `Initiate` event does not.

**Workflow discipline is enforced by hide-don't-disable.** Fresh+Pass and Fresh+Fail produce no re-run verb by default. To re-run, the operator must change something (config, parameters, methodology) which transitions the evidence to Invalidated, which then surfaces `Initiate Backtest`. The system does not block the re-run path; it requires the operator to express intent through a real change before the path appears. This matches the broader Milodex pattern of governing through visible affordances rather than disabled-with-tooltip explanations.

**Splitting v1 schema from v1 computation lets the prototype exercise the full state space via fixtures without building the manifest-drift / age-threshold / divergence-detection subsystems first.** Each of those is its own program of work touching event-store schema, runtime monitoring, and (in the divergence case) live-trading correlation. v1 needs the menu's behavior across the state space to be inspectable, not the freshness rules to be live.

## Consequences

- **The Bench read-model dataclass gains two new fields.**
  - `evidence_by_stage: dict[Stage, EvidenceRecord]` — historical evidence. `EvidenceRecord` carries `freshness: Freshness` and `gate_result: GateResult`, plus stage-specific metric snapshots and timestamps to be specified in the read-model implementation PR.
  - `runs_in_flight: dict[Stage, bool]` — **operational run state**, separate from evidence. Each entry signals whether an accepted-but-not-completed backtest run exists at that stage. Sourced from the open-backtest-runs view in the event store. Consulted by `re_run_verb` to suppress the verb during in-flight runs.

  The split keeps the evidence axes pure (history only) and the operational state visible (current activity). Implementations that put `is_run_in_flight` inside `EvidenceRecord` have the layering wrong.
- **`Freshness` and `GateResult` are Python enums** in the strategies / Bench module. The five freshness values and four gate-result values are the complete enumerations; future additions require an ADR amendment.
- **v1 fixture data must populate evidence records for every relevant `(strategy, stage)` pair** — at minimum every stage the demo strategy has reached, plus the relevant target stages for any `Return` verb the prototype needs to exercise. Fixtures must collectively span the menu's state space per ADR 0049 Decision 5.
- **Menu computation lives in the Python read-model layer** per ADR 0047. QML consumes the precomputed per-row action set; QML does not own the rules. Implementations that compute action visibility in QML have the rules in the wrong place.
- **The Action menu's verb list is closed.** New verbs require an ADR amendment to this document. Renames similarly. The locked vocabulary covers every interaction the v1 menu exposes.
- **`Return to Idle` and `Return to <active stage>` follow different rules.** The first is the to-shelf affordance and is always available from active stages. The second is the leave-IDLE affordance and requires the target's evidence to be Fresh/Aging + Pass (or NotApplicable for LIVE). Both spell as `Return to X`; the rule asymmetry is intentional and reflects IDLE's non-evaluated nature.
- **Demote to Backtest and Promote to <next stage> are not exposed by these rules** beyond the freshness / gate_result preconditions on `can_promote_to_next`. Demote is available whenever current_stage is at PAPER or higher and is governed by the modal-level governance flow per ADR 0043, not by evidence-state checks. Promote is governed by `can_promote_to_next` above.
- **v2 will add: freshness state-machine triggers** (manifest drift detection per ADR 0015, age-threshold enforcement, methodology/data-source change tracking, paper/live divergence detection); event-store records for evidence-state transitions; the audit trail of operator action invocations. v2 is gated by its own ADR and the lifting of the no-backend-mutation constraint in ADR 0049.
- **Tests for v1 read-model logic must cover the menu rules across the state space.** Coverage should include at least one fixture for every menu rule that returns a verb, plus the empty-menu floor case (every state-changing verb hidden, `Open Evidence` still present per ADR 0047 Decision 5).
- **`Stop Trading` is a session control, not an evidence operator.** It does not consult the evidence axes; it consults the strategy's current `session_state` (running vs idle). The verb appears when a strategy has an active session and disappears when it does not. (When wired in v2, it routes to the runner's controlled-stop path per ADR 0049 Decision 4.)

## Non-goals

- Does not implement the freshness computation. Real triggers (manifest drift, age thresholds, divergence detection, methodology change tracking) are v2.
- Does not specify exact metric snapshots, timestamp fields, or evidence-record provenance metadata. Those are read-model implementation concerns specified in the schema-extension PR.
- Does not specify event-store schema for evidence-state transition records. v2 work.
- Does not authorize any state-mutating Bench code path. The no-backend-mutation constraint is governed by ADR 0049.
- Does not change promotion thresholds (ADR 0009, ADR 0020) or risk rules.
- Does not change the kill-switch surface or its semantics (ADR 0005). `Stop Trading` is distinct from kill switch (ADR 0049 Decision 4).

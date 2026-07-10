# Phase 6 Bench Prep

**Status:** Planning artifact only — **partially superseded (2026-07-10 pointer).**
Phase 5 is closed; Phase 6 implementation may begin only against the ADR decisions
below. This document's blanket "no backend mutation" plan is overtaken **only** for
the six action families [ADR 0051](adr/0051-bench-command-infrastructure-v1.md)
opened (Bench command infrastructure v1); for every path ADR 0051 did not open,
[ADR 0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md)'s
no-mutation perimeter remains binding — see ADR 0049's status line ("Accepted —
amended in part") and its eight-item forbidden-mutation list. This document is not
fully dead: its decision register below remains the design record for the Bench
surface.

**Design context:** [ADR 0036](adr/0036-operator-kanban-surface-for-promotion-pipeline.md)
accepts the Bench visual spec. The Bench implements the **BENCH** role in the
four-surface narrative ([DESIGN.md section 4](DESIGN.md)) and must respect the operative
principles in [DESIGN.md section 5](DESIGN.md): three voices never crossed, status colors
as nouns, no greetings/congratulations/recommendations, and column-reservation alignment.
Stage-hue tokens introduced by ADR 0036 Decision 3 are a separate ladder-location axis
from [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) status colors. [ADR 0046](adr/0046-bench-stage-hues-extend-production-tokens.md)
settles the reconciliation: production tokens remain canonical, stage hues land as a
separate token namespace, and the existing parchment-dot texture may be reused through
QML-accessible tokens rather than raw prototype CSS. The implementation surface is
additionally governed by [ADR 0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md)
(v1 prototype scope and eight forbidden mutation paths) and
[ADR 0050](adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md)
(evidence schema, `Freshness`/`GateResult` axes, and locked verb grammar).

## Decision Register

- **v1 scope — visual prototype, no backend mutation:** decided by [ADR 0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md). v1 renders the full interaction model (vertical stage sections, per-row Action menu, hidden-when-unavailable items, evidence modals, within-section drag) but commits no backend state. Eight mutation paths are explicitly forbidden; see ADR 0049 Decision 2. `Stop Trading` maps to controlled-stop semantics (cross-ref [ADR 0012](adr/0012-runtime-and-dual-stop.md)), not kill switch; the kill switch remains a separate global affordance on the Anchor view. **This is the meta-decision: every entry below is implemented under v1's no-mutation constraint.**
- **Display-name provenance:** decided by [ADR 0041](adr/0041-bench-display-names-are-presentation-metadata.md). Add optional `strategy.display_name`; keep `strategy_id` as durable identity; expose display-name provenance in read models.
- **Stage versus session semantics:** decided by [ADR 0039](adr/0039-stage-session-and-bench-section-are-distinct.md). Promotion stage, runtime session state, and Bench section are distinct axes. `strategy_runs` remains the canonical foreground-runner liveness surface; `idle` is the **inactive shelf** — a stage section label, not "untested." A strategy on the shelf may carry prior-stage evidence (PAPER, MICRO LIVE, LIVE) whose freshness governs whether `Return to X` verbs surface (see ADR 0050).
- **Live/micro-live eligibility windows:** decided by [ADR 0042](adr/0042-live-and-micro-live-eligibility-is-locked-and-evidence-based.md). ADR 0004 remains authoritative; calendar countdowns are rejected; eligibility copy stays evidence-based and locked for capital-bearing stages.
- **Demotion security:** decided by [ADR 0043](adr/0043-bench-demotion-actions-open-a-governance-flow.md). An Action menu demotion opens a governance modal; no single action ever mutates durable state without confirmation; stop, kill, disable, and demote remain separate verbs.
- **Action availability surface:** decided by [ADR 0047](adr/0047-bench-action-availability-is-the-validation-surface.md). Per-row Action menu items are computed from the read-model state at render time; unavailable actions are hidden rather than disabled. `Open Evidence` is the menu's empty-menu floor per ADR 0047 Decision 5 — it is always present regardless of any other menu computation input; the Action menu is never empty.
- **Evidence schema and verb grammar:** decided by [ADR 0050](adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md). Per-stage evidence surfaces as `evidence_by_stage: dict[Stage, EvidenceRecord]`. Each `EvidenceRecord` carries two orthogonal axes: `freshness: Freshness` (`Missing | Fresh | Aging | Stale | Invalidated`) and `gate_result: GateResult` (`Pass | Fail | Pending | NotApplicable`). Operational run state is separate: `runs_in_flight: dict[Stage, bool]` lives on the strategy read model, not inside `EvidenceRecord`. Verb grammar is locked (per ADR 0050 Decision 7): directional verbs are `Promote to Paper`, `Promote to Micro Live`, `Promote to Live`, `Demote to Backtest`, `Return to Paper`, `Return to Micro Live`, `Return to Live`, `Return to Idle`; invocation verbs are `Initiate Backtest`, `Refresh Backtest`, `Start Trading`, `Stop Trading`; `Open Evidence` is the informational floor. No `Send to Idle`; no `Promote to Backtest`.
- **Responsive layout:** decided by [ADR 0048](adr/0048-bench-uses-vertical-stage-sections-with-natural-scroll.md). The Bench uses vertical stage sections (idle → backtest → paper → micro-live → live) stacked in native scroll; no stable-width columns inside a horizontal scroll region.
- **Bulk orchestration:** decided by [ADR 0040](adr/0040-bench-bulk-orchestration-uses-a-durable-job-ledger.md). Bulk actions create durable parent/child orchestration jobs that link to `backtest_runs.run_id` or `strategy_runs.session_id`; cancellation is cooperative and no promotion/stage mutation happens as a side effect. Bulk is not in Phase 6 v1.
- **Token reconciliation:** decided by [ADR 0046](adr/0046-bench-stage-hues-extend-production-tokens.md). Stage hues extend production tokens; status colors remain outcome nouns; prototype hex drift does not replace production palette.

## Implementation-ready Phase 6 Path

Phase 6 v1 is a six-PR reconciliation program (PR D–I). Each PR refines existing
code against the ADR pack rather than building greenfield. The repo already has
`BenchSurface`, `BenchRow`, a prototype Action menu, prototype modals, and
in-section reorder; the PRs bring those into compliance.

- **PR D** — Read-model schema extension: `Freshness` and `GateResult` enums; `EvidenceRecord` dataclass; `evidence_by_stage: dict[Stage, EvidenceRecord]` and `runs_in_flight: dict[Stage, bool]` on the strategy read model. Schema only — no fixtures, no QML changes. Decent.
- **PR E** — Fixture data set spanning the menu state space per ADR 0049 Decision 5: strategies at every promotion stage, every `Freshness` value at relevant stages, every `GateResult` value, and at least one row exercising every menu rule in ADR 0047 and ADR 0050. `Open Evidence` verifiable as the empty-menu floor on every fixture. Small.
- **PR F** — `BenchSurface` + `BenchRow` QML reconciled to the ADR pack. Existing code refined, not rebuilt. Decent.
- **PR G** — Action menu wiring: uniform `Action` button label per bench-brief §6, per-row menu items computed in Python from `(current_stage, evidence_by_stage, runs_in_flight)` per ADR 0047, hide-don't-disable per ADR 0047 Decision 2, `Open Evidence` floor always present per ADR 0047 Decision 5. Existing prototype refined. Decent. **Major checkpoint.**
- **PR H** — Within-section drag for priority reorder (visual only, non-persisting per ADR 0049 Decision 2). Small.
- **PR I** — Evidence modal + per-action confirmation modals (visual only, no mutation). Decent.

## Out of Scope

### Forbidden in v1 (per ADR 0049 Decision 2)

- Promotion writes — no operator-driven stage transition is persisted
- Demotion writes — same
- Broker calls — the Alpaca client is not invoked from any Bench code path
- Backtest execution — no actual backtest job is created or queued
- Trading-session start/stop — no `strategy_runs` row is opened or closed by Bench paths
- Persisted priority reorder — within-section drag survives only the lifetime of the current QML view
- Event-store writes from Bench code paths — no operator-action ledger records are written
- Kill-switch triggers — Bench paths do not interact with kill-switch state

### Deferred past v1

- Bulk orchestration actions (ADR 0040 architecture preserved; not surfaced in v1)
- Automatic promotion after a completed backtest or session job
- `micro_live` or `live` authorization, session start (ADR 0004 lock remains)
- Daemon, auto-restart, or unattended service behavior

## Future Work Register

Items for the v2 wiring program. Each requires its own ADR before implementation begins.

- **Real freshness computation** — manifest-drift detection (ADR 0015), age-threshold enforcement, methodology/data-source change tracking, paper/live divergence detection. v1 fixtures assign `Freshness` values directly; v2 derives them from event-store signals.
- **Event-store records for evidence-state transitions** — the audit trail for `Missing → Fresh`, `Fresh → Stale`, `Fresh → Invalidated`, etc. Named per ADR 0050's deferred items.
- **Audit trail of operator action invocations** — operator events naming an actor and explicit verb, separate from system-driven transition events.
- **Write-path wiring per ADR 0049's eight forbidden paths** — each path opened deliberately, one ADR per path, in the v2 wiring program.
- **Bulk orchestration v1** (if/when surfaced) — ADR 0040's durable job-ledger architecture is preserved and ready; the surface decision requires a separate ADR.
- **Real `Stop Trading` wiring** — routes to the runner's `shutdown(mode="controlled")` per [ADR 0012](adr/0012-runtime-and-dual-stop.md); produces `exit_reason="controlled_stop"`, closes `strategy_runs` cleanly, does not cancel open orders.

## Closing

v1 ships the Bench as a visual prototype per ADR 0049: the surface renders the full
interaction model — Action menu, evidence modals, within-section drag — but commits no
backend state. v2 (a future ADR) will explicitly open the write paths one at a time.

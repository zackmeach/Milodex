# Promotion Governance/Evidence Interface Exploration - 2026-05-21

Status: accepted exploration
Roadmap item: RM-002
Decision owner: `milodex.promotion`

## Context

CLI promotion and the Bench command facade now agree on paper-gate policy, but
both callers still know too much of the promotion sequence:

1. load the strategy config,
2. validate the target stage,
3. resolve backtest metrics,
4. evaluate the stage-specific promotion gate,
5. compute the post-update manifest hash,
6. assemble the evidence package,
7. call the governance transition.

The current helper modules are useful. `promotion.run_evidence.metrics_from_run`
preserves the walk-forward OOS aggregate rule from ADR 0021, and
`promotion.evidence` owns evidence validation and event-store derivations. The
remaining issue is interface depth: CLI and Bench still choreograph these pieces
instead of asking promotion governance for a decision.

This exploration compares three shapes and names the smallest safe follow-up
implementation slice. It does not change runtime behavior.

## Option A - Shared Promotion Orchestrator Service

Create a domain-owned entrypoint under `milodex.promotion`, for example
`prepare_and_record_promotion(request, event_store)`, that owns the full
promotion choreography for a single stage advancement. CLI and Bench would keep
their public payloads and operator-facing wording, but both would call this
entrypoint for the governance work.

Proposed internal request fields:

- `strategy_id`, `config_path`, `to_stage`
- `run_id`, `lifecycle_exempt`
- `recommendation`, `known_risks`, `approved_by`, `notes`
- optional `now` for deterministic tests

Proposed internal result shape:

- success: from/to stage, promotion type, manifest hash, manifest id, promotion
  id, evidence package, metrics snapshot
- blocked: stable reason code, gate failures or validation message, metrics
  snapshot when available
- error: unexpected durable-write or config inconsistency details, without
  leaking partial writes as success

Depth:

- Highest. The promotion package owns the sequence and callers stop duplicating
  gate/evidence/manifest choreography.

Locality:

- Strong. Governance behavior stays in `milodex.promotion`; CLI and Bench become
  adapters around user input and output formatting.

Caller burden:

- Lowest after migration. Callers supply operator intent and render the returned
  result.

Test migration:

- Moderate. Existing CLI and Bench behavior tests should stay, but direct
  choreography expectations move to promotion-level tests. RM-001 paper-gate
  parity tests remain guardrails during the extraction.

Risks:

- The orchestrator could become too large if it absorbs UI wording or Bench
  workflow-readiness rules. It must stay limited to promotion governance:
  transition legality, metrics lookup, gate evaluation, manifest/evidence
  assembly, and durable transition dispatch.

## Option B - Evidence/Gate Helper Extraction Only

Extract one narrower helper, for example `build_promotion_evidence_decision()`,
that resolves metrics, evaluates `check_gate`, computes the manifest hash, and
returns an evidence package. CLI and Bench would still call
`state_machine.transition()` themselves.

Depth:

- Medium. It removes some duplication but leaves callers responsible for the
  split between decision preparation and durable transition dispatch.

Locality:

- Improved, but incomplete. The promotion package owns evidence preparation;
  callers still coordinate final governance mutation.

Caller burden:

- Medium. CLI and Bench still need to understand when to validate stages, how to
  interpret failed gates, and when to dispatch the transition.

Test migration:

- Small. Most existing tests can remain with helper-level additions.

Risks:

- This preserves the shallow seam that RM-002 is meant to address. It would make
  duplication smaller, not eliminate choreography knowledge from callers.

## Option C - Analytics-Metrics Boundary Extraction First

Move `metrics_for_run` out of `milodex.cli.commands.analytics` into a non-CLI
analytics module, then update `promotion.run_evidence.metrics_from_run` to use
that module directly. Defer the broader promotion choreography refactor.

Depth:

- Low for governance, high for one layering violation. It removes the remaining
  `promotion -> cli` lazy import but does not reduce caller choreography.

Locality:

- Strong for metrics ownership. Promotion evidence would no longer depend on a
  CLI command module.

Caller burden:

- Unchanged. CLI and Bench still perform the same promotion sequence.

Test migration:

- Small to moderate. Tests that monkeypatch
  `milodex.cli.commands.analytics.metrics_for_run` must move to the new analytics
  import path.

Risks:

- This is useful cleanup but not the best next implementation slice for
  governance depth. It can be tracked separately and done after the orchestrator
  contract is clear.

## Decision

Adopt Option A as the target shape: a shared promotion-orchestrator service owned
by `milodex.promotion`.

The smallest safe implementation slice is to extract a domain-owned
paper-promotion choreography entrypoint that both CLI and Bench can call while
preserving their public payloads, flags, QML slots, blocker shapes, and
operator-facing text. Limit the first implementation to `backtest -> paper`;
later capital-stage promotion remains phase-locked by ADR 0004/R-PRM-006 and
should not be widened in the same slice.

## Future Interface Contract

Ownership:

- `milodex.promotion` owns stage-transition governance, gate/evidence
  choreography, manifest hash derivation, and transition dispatch.
- CLI owns argument parsing, command result rendering, and CLI-specific errors.
- Bench owns proposal/submit lifecycle, workflow-readiness blockers, and
  `CommandProposal`/`CommandResult` serialization.

Failure behavior:

- Invalid stage transition returns or raises the same semantic refusal currently
  surfaced by CLI and Bench.
- Missing or invalid evidence inputs remain refused before durable mutation.
- Gate failure returns a structured blocked result and writes nothing.
- Missing backtest run returns a structured refusal/error and writes nothing.
- Durable transition success remains atomic: manifest and promotion append
  together before YAML stage update, matching current `transition()` semantics.

Migration path:

1. Add promotion-level request/result types and tests that cover the current CLI
   and Bench paper-promotion paths.
2. Move the existing paper-promotion sequence into the orchestrator without
   changing `check_gate`, `assemble_evidence_package`, or `transition()`.
3. Migrate CLI promotion-to-paper to the orchestrator and keep existing CLI tests
   green.
4. Migrate Bench `submit_promote_to_paper` to the same orchestrator while keeping
   RM-001 and RM-003b tests green.
5. In a separate slice, move single-period analytics metrics out of CLI internals
   into a non-CLI analytics boundary.

## Follow-Up Roadmap

- RM-010: implement the shared paper-promotion choreography entrypoint.
- RM-011: move `metrics_for_run` out of CLI internals into a non-CLI analytics
  boundary.

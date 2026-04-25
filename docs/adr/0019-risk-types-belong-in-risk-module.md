# ADR 0019 — Risk Types Belong in `risk/`, Not `execution/`

**Status:** Implemented
**Date:** 2026-04-22
**Implemented:** 2026-04-24
**Relates to:** ADR 0008 (risk layer has veto over execution)

## Implementation note (2026-04-24)

Verified during the 2026-04-24 architecture review. The refactor is in effect:

- `RiskDecision` — `src/milodex/risk/models.py:26`
- `RiskCheckResult` — `src/milodex/risk/models.py:16`
- `RiskDefaults` — `src/milodex/risk/config.py:18`
- `EvaluationContext` — `src/milodex/risk/evaluator.py:33` (kept inline with the evaluator; not extracted to its own module)
- `execution/risk.py` — does not exist; the 13-line back-compat shim referenced in §18 was either never created or already removed. No callers depend on the legacy path.

The dependency arrow now points `execution/` → `risk/` only. `risk/` imports nothing from `execution/`. Verified by import audit across `src/` and `tests/`.

## Context

ADR 0008 establishes risk as a distinct layer with veto power over execution. `CLAUDE.md` reinforces this: *"Risk layer is sacred. Every trade passes through `risk/` before execution. Strategy proposes, risk disposes."*

The code partially reflects this. `RiskEvaluator` lives in `src/milodex/risk/evaluator.py`. But its first-class types — the evaluator's inputs and outputs — live in `execution/`:

- `RiskDecision` — the evaluator's return type — lives in `execution/models.py`.
- `RiskCheckResult` — the per-rule verdict — lives in `execution/models.py`.
- `EvaluationContext` — the evaluator's input bundle — is defined in `risk/evaluator.py` but imports five of its six fields from `execution/` (`ExecutionRequest`, `TradeIntent`, `RiskDefaults`, `StrategyExecutionConfig`, `KillSwitchState`).
- `RiskDefaults` — the global risk-limit dataclass — lives in `execution/config.py`.

The historical reason is that the evaluator was born inside `execution/` and was later lifted into `risk/`, with a back-compat shim at `execution/risk.py` (13 lines, still live). The lift moved the class; it did not move the types.

The dependency graph now points the wrong way. `risk/evaluator.py` depends on `execution/` for four of its five non-broker, non-data imports. A code change to `execution/models.py` silently alters the shape of the "sacred" layer. A contributor reading the imports cannot tell that risk is architecturally above execution — the imports say the opposite.

The 2026-04-22 architecture health review flags this as the highest-priority structural drift in the Phase 1 codebase.

## Decision

Risk-layer types live in `risk/`. Execution-layer types live in `execution/`. The dependency arrow points from `execution/` to `risk/`, never the reverse.

Specifically:

1. **Move to `risk/`**: `RiskDecision`, `RiskCheckResult`, `EvaluationContext`, `RiskDefaults`.
2. **Keep in `execution/`**: `TradeIntent`, `ExecutionRequest`, `ExecutionResult`, `ExecutionStatus`, `StrategyExecutionConfig`, `KillSwitchState`.
3. **`execution/` imports from `risk/`** for `RiskDecision`, `RiskCheckResult`, `RiskDefaults`. `risk/` never imports from `execution/`.
4. **`EvaluationContext`** stays in `risk/` but holds execution-layer types by reference (it is the evaluator's input, not the evaluator's output — execution-layer types as input is correct).
5. **Delete `src/milodex/execution/risk.py`** (the 13-line back-compat shim). Any remaining callers are updated to import from `milodex.risk` directly.

## Rationale

- **Import graph matches the invariant.** ADR 0008's "risk has veto" is a claim about architectural layering. Layering is only real if it shows up in the dependency graph. After this ADR, the graph says what the invariant says.
- **Drift-resistant.** If a future change to `execution/models.py` tries to alter `RiskDecision`'s shape, the compiler refuses — the type is no longer in that file. The invariant is enforced by the module system, not by discipline.
- **Reads correctly to a new contributor.** Someone opening `risk/` sees the evaluator and its types together. Someone opening `execution/` sees `ExecutionService` importing from `risk/`, which matches the mental model that execution calls risk.
- **Removes a latent shim.** `execution/risk.py` exists only because the lift was partial. Completing the lift removes the shim. Fewer files with fewer reasons to exist is the preferred end state.
- **Zero behavior change.** This is a mechanical refactor. Every test should pass unchanged; no runtime code path moves.

## Consequences

- One-time refactor: ~200 LoC move across `risk/`, `execution/models.py`, `execution/config.py`, and a handful of import sites. Estimated 0.5 day.
- The `milodex.execution.risk` import path is removed. Any external tooling (there shouldn't be any — this is a personal project) would need to update. Internal callers already use `milodex.risk` per the shim's own docstring guidance.
- Future risk rules (e.g. live-only rules in Phase 2) are added inside `risk/` without needing to touch `execution/`. Adding a check becomes a one-module change.

## Non-goals

- This ADR does **not** introduce a rule registry, a plugin system, or any abstraction over the check list. `RiskEvaluator.evaluate` remains a hardcoded sequence of 11 checks. Pluggability is a future concern if the rule set grows past ~15 or starts branching by stage (see 2026-04-22 health review parking lot).
- This ADR does **not** change the broker-model coupling. `risk/evaluator.py` still imports `AccountInfo`, `Order`, `Position` from `broker/`. Introducing a `RiskInput` DTO to decouple risk from broker internals is not justified at Phase 1 scale.
- This ADR does **not** relocate `StrategyExecutionConfig`. Per-strategy execution caps are execution-layer configuration; the evaluator consumes them by reference and that is the correct direction.

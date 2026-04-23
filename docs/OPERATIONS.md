# Operations & Runtime

Companion to `docs/SRS.md` Domain 9 (Runtime & Operations). This document defines how Milodex runs day-to-day: the daily schedule, what startup and shutdown must always do, mandatory broker/local reconciliation, behavior under degraded connectivity or data conditions, concurrency rules, command-safety classification, idempotency guarantees, and the audit-trail contents for previews and submits.

Phase 1 intent: Milodex aligns to a **daily market rhythm**, not an intraday decision rhythm. The operator runs it as a scheduled daily workflow with light continuous monitoring — not as an always-on autonomous service.

---

## Operating Model

Milodex is a **scheduled daily workflow** with **light continuous monitoring** in Phase 1. The core trading cycle runs once per trading day around the end-of-day and next-session execution windows. Optional background monitoring may remain active for status, reconciliation, and incident detection, but there is no always-on autonomous service requirement. A daemon/supervisor runtime is explicitly Phase 2+ (per the SRS Phase 2 appendix).

---

## Daily Schedule

| Window | Purpose |
|---|---|
| **Pre-market check** | Verify broker and data health, reconcile local vs broker state, confirm readiness before the open. |
| **Market open window** | Monitor any eligible next-open executions and reconcile resulting order state. |
| **Post-close analysis window** | Ingest finalized daily data, evaluate signals, run previews, prepare the next cycle. |
| **End-of-day reporting window** | Persist summaries, update audit records, surface incidents or review items. |

These windows are workflow concepts, not fixed clock times — their exact offsets from market open/close are configurable. The important invariant is that Milodex evaluates, previews, and prepares on a daily cadence, and that every window produces durable artifacts rather than only transient in-memory state.

---

## Startup

Every Milodex startup, regardless of entrypoint, must:

1. Load environment, config, and frozen manifests.
2. Verify config fingerprints and schema compatibility.
3. Connect to required services (broker, market data, database) and test availability.
4. Reconcile local state with broker state (see "State Reconciliation" below).
5. Refresh current strategy, approval, and kill-switch status.
6. Validate data freshness for the current workflow.
7. Detect any unresolved incidents or halted states from the prior session.
8. Write a startup event to the audit log.
9. Surface a concise status summary before allowing any sensitive action.

Startup is the moment Milodex establishes whether the system is **safe and coherent enough to proceed**. Commands that could affect execution state must not be available until startup completes successfully.

---

## Shutdown

Every shutdown — whether initiated by controlled stop (R-EXE-011), kill switch (R-EXE-012), or operator exit — must:

1. Flush pending logs and audit events.
2. Persist any in-memory workflow state required to survive restart.
3. Record unresolved orders, incidents, or warnings.
4. Write a shutdown event with timestamp and operator context.
5. Cleanly close broker, data, and database connections.
6. Leave the system in a restart-safe state.

Shutdown must never silently discard operationally important context. A kill-switch shutdown has weaker state-flush guarantees than controlled stop (per R-STR-010) — that asymmetry is acceptable; silent loss of audit or incident context is not.

---

## State Reconciliation

Local state and Alpaca must be reconciled at startup, at the start of each workflow window, and on demand. At minimum, reconciliation covers:

- open orders
- filled orders since last sync
- canceled or rejected orders
- current positions
- account equity and buying power relevant to policy
- order identifiers and local submission records
- strategy-to-order linkage
- any halt- or incident-relevant discrepancies

If local and broker state disagree on any execution-critical fact, Milodex must:

- surface the mismatch with both values shown,
- log the disagreement as an incident,
- **block exposure-increasing actions** until reconciliation is resolved.

This is consistent with R-XC-010 (split source-of-truth) and R-EXE-004 (broker-vs-local reconciliation check).

---

## Degraded Modes

Milodex fails safe, not open. The three degraded-mode cases:

### Broker down
If broker connectivity fails mid-workflow, Milodex must:

1. Stop any new exposure-increasing actions immediately.
2. Preserve current workflow state.
3. Mark the workflow as degraded.
4. Log the connectivity failure as an incident.
5. Continue read-only analysis where safe.
6. Retry according to a conservative retry policy.
7. Require reconciliation before resuming submit-capable actions.

### Data available, broker down
Research, reporting, and preview generation may continue. All submit-capable actions are blocked. Outputs must be clearly marked **non-executable** until broker connectivity is restored and reconciliation passes. This is an **analysis-only degraded mode**, not permission to operate normally.

### Broker up, data stale
All exposure-increasing decisions that depend on the stale data are blocked. Reconciliation, reporting, and other broker-safe inspection tasks may continue. Milodex must not generate or submit new actionable decisions from untrustworthy inputs. If data freshness is a hard requirement for the current workflow, the workflow moves into a **blocked or review-required state**.

In all three cases: the degraded mode itself is an incident and must be surfaced to the operator, not silently tolerated.

---

## Concurrency Model

Phase 1 uses a **single-operator, serialized-critical-action** concurrency model:

- **Concurrent OK:** data fetches, reporting, non-critical background checks, read-only inspections.
- **Serialized (lock-guarded):** submit, reconcile, promote, demote, kill-switch handling, config-changing operations, and any state-changing governance event.

Concurrency guards are implemented via file locks under `state/locks/` (per R-XC-006). The goal is to avoid race conditions and keep runtime behavior easy to audit. This also dovetails with R-EXE-013 (single strategy runs at a time in Phase 1).

---

## Command Safety Classification

Commands fall into two classes. The distinction is not only market hours — it is **whether the command can materially affect live or paper execution state**.

### Safe anytime

- `status`
- health checks
- config inspection
- backtests
- report generation
- experiment registry review
- audit log inspection
- non-executable previews
- reconciliation checks
- startup/shutdown-safe diagnostics

### Requires market-hours relevance or workflow readiness

- executable previews tied to current session conditions
- submit-capable trade actions
- next-open execution preparation tied to the live cycle
- manual exposure-changing overrides

Commands in the second class must check workflow-readiness (reconciliation clean, no active kill switch at the relevant scope, data fresh, broker reachable) and fail with a structured error when preconditions are not met.

---

## Idempotency Guarantees

The following operations must be idempotent — running them twice must either produce the same safe result or explicitly refuse the second action without corrupting state:

- startup initialization routines
- state reconciliation
- health and status commands
- report generation for the same underlying run
- preview generation for the same frozen inputs
- incident logging (duplicate-emission guards)
- order submission protection logic (duplicate-order policy, R-EXE-009)
- kill-switch activation
- promotion / demotion artifact creation guards (R-PRM-007, R-PRM-010)
- any command that could otherwise accidentally create duplicate orders or duplicate governance events

Idempotency is enforced by keying durable writes on content hashes, request identifiers, or explicit "only one per (strategy, window)" constraints — never by relying on the operator to remember.

---

## Audit Trail: Previews and Submits

Every preview and every submit writes an audit record (an explanation record per R-XC-008). The record must make it possible, later and with confidence, to answer: **what did Milodex intend to do, why did it intend to do it, what checks were performed, and what actually happened?**

### Preview audit record — required fields

- timestamp
- operator identity (if applicable)
- strategy instance ID and config fingerprint (per R-STR-011, R-STR-012)
- workflow stage
- symbols considered
- signal inputs used
- decisions proposed
- constraints checked (per-check pass/fail, consistent with R-CLI-007)
- warnings and blockers encountered
- data freshness state
- broker connectivity state
- preview artifact reference or summary hash

### Submit audit record — all of the above, plus

- exact order intent
- symbol, side, quantity, and target exposure
- order timing context
- approval state and approval reference (if the action was gated)
- broker request payload reference
- broker response / order ID
- duplicate-order check result (per R-EXE-009)
- pre-submit risk check results (per R-EXE-004)
- whether the order increased or reduced exposure (per R-EXE-016)
- final local state transition result

Submit records are linked by ID back to the preview they originated from when one exists, so the path from "considered" → "proposed" → "approved" → "submitted" → "filled" is reconstructable end-to-end from durable state alone.

### Strategy reasoning payload (`context.reasoning`)

Every audit record now includes a `context.reasoning` JSON object populated by `Strategy.evaluate()` — the strategy-internal "why" that previously never left the evaluator. The shape is the `asdict()` of `milodex.strategies.base.DecisionReasoning`:

- `rule` — canonical rule ID the strategy fired (`regime.ma_filter_cross`, `regime.hold`, `meanrev.rsi_entry`, `meanrev.rsi_exit`, `meanrev.stop_loss`, `meanrev.max_hold`, or `no_signal` for a cycle that proposed nothing).
- `narrative` — one-sentence operator-readable summary (e.g. `"latest close 450.12 above 200-DMA 432.05 → rotate to SPY"`).
- `triggering_values` / `threshold` — the inputs the rule compared and the threshold side.
- `ranking` / `rejected_alternatives` — for cross-sectional families, the scored candidate list and per-candidate rejection reasons.
- `extras` — strategy-specific debugging fields.

Non-firing cycles (empty `intents`) also write a single `decision_type="no_trade"`, `status="no_signal"` row carrying the same payload, so a backtest that fires once in 250 days still has 249 auditable "why not" rows. Legacy records from before this change retain an empty `context.reasoning` — the contract is forward-only.

Operators can surface the reasoning via `milodex analytics trades --json` (the full `context` dict is in the trade rows) or via the trust report's `recent_decisions` section.

### Frozen manifests and runtime drift (`manifest_drift`, `no_frozen_manifest`)

Strategies at `paper`, `micro_live`, or `live` stage must have a frozen manifest recorded in the event store. The manifest is a hash of the strategy's YAML captured at the moment the operator ran `milodex promotion freeze`. On every evaluation the risk layer compares the runtime YAML's hash against the latest frozen hash for the strategy's current stage and emits one of two blocking reason codes when they disagree:

- `no_frozen_manifest` — the strategy is at paper+ stage but has never been frozen. Remedy: run `milodex promotion freeze <strategy_id>` once. (`backtest`-stage strategies and manual operator trades are exempt.)
- `manifest_drift` — the live YAML has changed since the last freeze. Remedy: revert the edit, or re-freeze intentionally if the new config is the one you want to promote. The audit trail keeps every freeze event, so re-freezing is append-only and auditable.

Both codes are hard blocks, not warnings — the refuse-by-default posture matches the kill-switch philosophy. The intent is that config edits to a promoted strategy require an explicit human freeze action, so the evidence behind the promotion decision stays tied to the config the strategy actually ran under.

Operators onboarding a strategy for the first time run the freeze command once at the current stage; subsequent stage changes (slice 2) will freeze automatically as part of the promote transition.

### `milodex promotion` operator surface

- `milodex promotion freeze <strategy_id>` — snapshot the strategy's current YAML at its declared stage into the event store. Refuses `backtest` stage (nothing to freeze). Supports `--frozen-by <name>` for attribution.
- `milodex promotion manifest <strategy_id>` — read-only; print the active frozen manifest for the strategy's current stage, or "No active manifest" if none exists.

Both commands honor the global `--json` flag for scripting (ADR 0014).

---

## Relationship to SRS and Other Docs

- `docs/SRS.md` Domain 9 contains the testable requirements (R-OPS-*) that enforce the rules above.
- `docs/RISK_POLICY.md` owns the numeric defaults and kill-switch triggers referenced here.
- `docs/PROMOTION_GOVERNANCE.md` owns the append-only governance-event model that incident-reversal records follow.
- `configs/risk_defaults.yaml` is the machine-readable source of truth for thresholds invoked during the windows above.

# ADR 0051 — Bench Command Infrastructure v1

**Status:** Accepted · 2026-05-14
**Related:** [ADR 0049](0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) (amended in part — see Decision 1), [ADR 0004](0004-paper-only-phase-one.md) (paper-only lock), [ADR 0005](0005-kill-switch-manual-reset.md) (kill switch, manual reset), [ADR 0008](0008-risk-layer-veto-architecture.md) (risk veto), [ADR 0009](0009-promotion-pipeline-stage-model.md) (promotion stages), [ADR 0012](0012-runtime-and-dual-stop.md) (controlled-stop semantics), [ADR 0015](0015-strategy-identifier-and-frozen-manifest.md) (manifest freeze), [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) (per-strategy advisory lock), [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) (backtest sandbox), [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) / [ADR 0043](0043-bench-demotion-actions-open-a-governance-flow.md) / [ADR 0047](0047-bench-action-availability-is-the-validation-surface.md) (Bench verb model), [ADR 0050](0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md) (evidence freshness axis), [BENCH_BOUNDARY.md](../BENCH_BOUNDARY.md), [PROMOTION_GOVERNANCE.md](../PROMOTION_GOVERNANCE.md), [OPERATIONS.md](../OPERATIONS.md), [LAUNCH_READINESS.md](../LAUNCH_READINESS.md).

## Context

ADR 0049 established that Bench v1 ships as a visual prototype with no backend mutation: every menu item reachable, every modal openable, no event-store write, no broker call, no runner construction. That decision was correct for v1. Validating *feel* on the surface that will eventually carry the most consequence — promotion, demotion, runner control — bought cheap iteration on rendering and read-model derivation before any of those paths could touch persistent state.

The launch bar has now changed. Milodex must not launch as a polished read-only GUI. The launch requirement is **GUI-controlled strategy lifecycle operation through paper trading and walk-back, without the operator needing to drop into the CLI for the normal path that defines the product**. Concretely: an operator should be able to sit at the Bench and take a strategy from idle/backtest-ready, through a backtest run, through evidence review, through promotion to paper, through paper runner operation, through controlled stop, and through demotion/walk-back, with every step previewed, gated, audited, and durably logged.

ADR 0049's binary "no backend mutation" rule is the binding contract that this work now needs to amend — narrowly, deliberately, and only along an explicitly named command path. The rule itself was the right ceiling for v1: a softer line accumulates partial wirings that a v2 ADR has to unwind. The same posture applies to v2: open the wiring along *one* named facade, action family by action family, with the existing CLI/governance behaviors as the substrate, not as parallel implementations.

This ADR is architecture and docs only. No production code lands with it. Command wiring lands in subsequent PRs, narrow, one action family per PR, with each PR carrying its own scoped test and boundary-doc update.

## Decision

### 1. ADR 0049 is amended in part; Bench v1's "no backend mutation" stays the default ceiling

ADR 0049 Decision 2 is **amended** by this ADR for the explicit set of action families named in Decision 2 below, and **only** along the command facade defined in Decision 4. For every path not opened by this ADR, ADR 0049 remains the binding contract: no broker call, no event-store write, no runner construction, no config mutation. The amendment is narrow on purpose. ADR 0049's reasoning — that "a softer line accumulates partial wirings the v2 ADR has to unwind" — applies recursively to this ADR. Any path not in §2 stays preview-only until a successor ADR opens it.

ADR 0049's historical value is preserved. The validated-then-wired sequence is what allowed v2 to start from a known-correct surface. This ADR is the wiring program that follows.

### 2. Bench becomes the primary operator surface for the launch lifecycle

The Bench is now responsible, at launch, for the following action families. Every other Bench-reachable verb remains preview-only under ADR 0049.

- **Backtest** — request a backtest run for a strategy (single-period or walk-forward), watch its progress, see its result and evidence-package summary.
- **Evidence review** — inspect a strategy's evidence packet for the selected stage and the next-target stage, including manifest freshness and gate-check posture.
- **Manifest freeze** — freeze the strategy's current YAML at its current stage when required by the promotion path, producing a `frozen_manifest_v1` event.
- **Promote to paper** — advance a strategy from `backtest` to `paper`, gated by the existing promotion machinery (`validate_stage_transition`, `check_gate`, `assemble_evidence_package`, `transition`).
- **Start paper runner** — start a foreground paper-trading session for a `stage: paper` strategy, holding the per-strategy advisory lock `milodex.runtime.strategy.<strategy_id>`.
- **Stop paper runner (controlled)** — issue a controlled-stop request that finishes the current cycle and closes the `strategy_runs` row cleanly. This is **not** the kill switch.
- **Demote / walk-back** — record a demotion to `backtest` or `disabled`, with reason and approver, through the existing `promotion.state_machine.demote` path.
- **Blocked-action visibility** — when preconditions fail (gate fails, manifest missing, stage mismatch, advisory lock held, runner already running, kill switch active, broker down, data stale, reconciliation drift), the GUI receives a **structured blocker** and renders the operator-readable reason without executing the action.

Out of scope, explicitly: micro-live, live, broker-live execution, kill-switch automation, autonomous-agent trading, strategy-owned risk policy, weakening of risk gates, broad visual redesign, Light/Bronze theme parity, and any "AI trader" behavior. ADR 0004 stays in force. ADR 0005 stays in force.

### 3. Command lifecycle: propose → validate → preview → confirm → submit → audit → refresh

Every action family in §2 follows the same seven-step lifecycle. The lifecycle is the contract; the action-family-specific machinery rides on top of it.

1. **Select.** The operator picks an action from the Bench Action menu (or row CTA). Selection alone changes nothing.
2. **Propose.** QML calls the corresponding `propose_*` method on the backend command facade (Decision 4). The facade returns a `CommandProposal` describing the action, the current state it was validated against, the preconditions it checked, the projected outcome, and a `blockers: list[Blocker]` field (empty when admissible).
3. **Validate.** Validation is a property of the proposal, not of submit. A proposal is *admissible* (no blockers) or *blocked* (one or more `Blocker` records, each with a stable `reason_code`, an operator-readable `message`, and any structured context the GUI may render).
4. **Preview.** QML renders the proposal in `BenchConfirmationModal` (or its successor surface for action families not currently routed through that modal). The preview shows the projected outcome, the evidence that justifies it, and any warnings. A blocked proposal renders the blocker list and does **not** offer a submit affordance.
5. **Confirm.** The operator confirms the preview. For capital-bearing or runner-affecting actions, confirmation rules from ADR 0043 apply (typed confirmation where the existing CLI requires it; explicit checkboxes for the same fields the CLI requires non-blank).
6. **Submit.** QML calls the matching `submit_*` method on the facade, passing the same `CommandProposal` that was previewed. The facade re-validates (a proposal can go stale between preview and submit — manifest drift, kill-switch trip, broker down). If the re-validation passes, the facade dispatches to the existing CLI/governance path. The facade returns a `CommandResult` with `status ∈ {submitted, blocked, error}`, the durable record identifiers (event IDs, run IDs, session IDs, promotion IDs), and any structured warnings.
7. **Audit + refresh.** The facade has already written or linked the durable audit record on the submit path (Decision 8). QML triggers a read-model refresh on completion so the next state the operator sees is the post-submit state, not the pre-submit one.

A proposal is **stateless from the GUI's perspective** — the GUI does not hold a write lock, does not "reserve" a runner slot, does not pre-allocate event IDs. The proposal carries enough information to be re-validated at submit time against the live world. This keeps the facade tolerant of GUI restarts, multi-process operators, and stale previews.

### 4. Backend command facade lives in `src/milodex/commands/bench.py`

A single Python module, `src/milodex/commands/bench.py`, exposes the facade. The location is deliberately *outside* `src/milodex/gui/` so the facade is reusable from CLI tooling, tests, and future surfaces; it is *not* a QML-specific helper and may not import from `PySide6` or any QML construct. QML reaches it through a thin Qt bridge object registered in `qml_setup.py`; the bridge translates Q_INVOKABLE calls to facade calls and translates `CommandProposal` / `CommandResult` dataclasses to QVariant payloads.

The facade exposes the following methods. The names below are normative; per-action arguments are illustrative (the exact argument list will be fixed by the wiring PR for each family).

```python
# src/milodex/commands/bench.py

class BenchCommandFacade:
    """Single entry point for Bench-initiated lifecycle commands.

    Re-uses existing CLI/governance modules. Owns no business rules of its own:
    every decision is delegated to milodex.promotion, milodex.strategies.runner,
    milodex.backtesting, milodex.core.advisory_lock, and milodex.risk.
    """

    def propose_backtest(self, strategy_id, start, end, *, walk_forward, ...) -> CommandProposal: ...
    def submit_backtest(self, proposal: CommandProposal) -> CommandResult: ...

    def propose_freeze_manifest(self, strategy_id) -> CommandProposal: ...
    def submit_freeze_manifest(self, proposal: CommandProposal) -> CommandResult: ...

    def propose_promote_to_paper(self, strategy_id, *, recommendation, known_risks,
                                 run_id, approved_by, lifecycle_exempt) -> CommandProposal: ...
    def submit_promote_to_paper(self, proposal: CommandProposal) -> CommandResult: ...

    def propose_demote(self, strategy_id, *, to_stage, reason, approved_by,
                       evidence_ref) -> CommandProposal: ...
    def submit_demote(self, proposal: CommandProposal) -> CommandResult: ...

    def propose_start_paper_runner(self, strategy_id) -> CommandProposal: ...
    def submit_start_paper_runner(self, proposal: CommandProposal) -> CommandResult: ...

    def propose_stop_paper_runner(self, strategy_id) -> CommandProposal: ...
    def submit_stop_paper_runner(self, proposal: CommandProposal) -> CommandResult: ...
```

`CommandProposal` and `CommandResult` are frozen dataclasses. They are serialisable to dict for QML transit and to JSON for audit-record fields. Their shape is:

```python
@dataclass(frozen=True)
class Blocker:
    reason_code: str          # stable; tested
    message: str              # operator-readable
    context: dict[str, Any]   # structured detail (gate failures, lock holder PID, ...)

@dataclass(frozen=True)
class CommandProposal:
    action_family: str               # "backtest" | "freeze_manifest" | "promote_to_paper" | ...
    strategy_id: str
    inputs: dict[str, Any]           # action-specific args, canonical
    state_snapshot: dict[str, Any]   # what the facade observed during propose()
    preconditions: list[dict[str, Any]]  # per-check pass/fail, mirrors R-CLI-007
    projected_outcome: dict[str, Any]    # human/JSON-renderable summary
    blockers: list[Blocker]
    proposed_at: datetime
    proposal_id: str                 # UUID; appears on the audit record

@dataclass(frozen=True)
class CommandResult:
    proposal_id: str
    action_family: str
    status: str                      # "submitted" | "blocked" | "error"
    durable_refs: dict[str, str]     # {"promotion_id": "...", "run_id": "...", ...}
    blockers: list[Blocker]
    warnings: list[str]
    submitted_at: datetime | None
    audit_event_id: str | None
```

The facade is constructed once per process with the same `CommandContext`-shaped dependencies the CLI uses (event store, config dir, locks dir, broker factory, trading mode). Tests construct it directly with fakes.

### 5. The GUI stays thin; QML may not own business rules

QML's responsibilities, additive to ADR 0049's read-model surface, are bounded:

- Display state from read models.
- Invoke `propose_*` on the facade-bridge.
- Render the resulting `CommandProposal` — admissible or blocked — in a confirmation surface.
- Collect operator confirmation (typed confirmation where required).
- Invoke `submit_*` with the previewed proposal.
- Render `CommandResult` status (submitted, blocked-late, error) and any warnings.
- Trigger a read-model refresh on completion.

QML may **not**:

- Read or write YAML directly.
- Read or write the event store directly.
- Call broker clients directly.
- Construct `StrategyRunner` or any runtime object directly.
- Bypass any function in `milodex.promotion`, `milodex.strategies.runner`, `milodex.backtesting`, `milodex.risk`, `milodex.execution`.
- Decide whether a promotion is admissible, whether a runner may start, whether a kill switch is engaged, or whether a manifest is fresh. These are facade or backend-module decisions; QML renders them.
- Hold proposal state for longer than the lifetime of a single confirmation modal.

The Qt bridge object that wraps the facade lives in `src/milodex/gui/` and is the *only* place QML is allowed to reach the facade. The bridge has no business logic — it serialises arguments, calls the facade, serialises results.

### 6. Reuse existing CLI / governance paths; do not build a parallel GUI lifecycle

The facade is a thin orchestrator over modules that already exist. Every submit must route through the existing function the CLI already calls; this ADR does not authorise any new business logic, new gate definition, or new audit shape.

| Action family | Reused backend |
|---|---|
| Backtest | `milodex.backtesting.engine.BacktestEngine.run` / `milodex.backtesting.walk_forward_runner.run_walk_forward` (the same callees as `cli/commands/backtest.py`) |
| Freeze manifest | `milodex.promotion.freeze_manifest` |
| Promote to paper | `milodex.promotion.validate_stage_transition`, `milodex.promotion.check_gate`, `milodex.promotion.assemble_evidence_package`, `milodex.promotion.state_machine.transition` |
| Demote / walk-back | `milodex.promotion.state_machine.demote` |
| Start paper runner | `milodex.core.advisory_lock.AdvisoryLock` (per-strategy namespace, ADR 0026), stage-compatibility check identical to `cli/commands/strategy.py:_check_stage_compatibility`, `milodex.strategies.runner.StrategyRunner.run` |
| Stop paper runner (controlled) | `milodex.strategies.runner.StrategyRunner.shutdown(mode="controlled")` (ADR 0012, `exit_reason="controlled_stop"`) — **distinct from kill switch, which remains on the Anchor view** (ADR 0049 Decision 4) |

Phase 1 micro-live/live refusals stay where they already live. `cli/commands/strategy.py:99` raises `"strategy run is paper-only in Phase 1."` for any non-paper trading mode; the facade's `submit_start_paper_runner` inherits this by routing through the same guard. The facade does not duplicate it. Likewise the promotion machinery refuses `to_stage="live"` without `--confirm`; the facade `propose_promote_to_paper` is scoped to `to_stage="paper"` and does not expose live or micro-live targets at launch.

The risk layer is not bypassed, weakened, or re-implemented by any path in this ADR. The runner that the facade starts is the same runner that goes through the risk-evaluator chokepoint per ADR 0008.

### 7. Safety invariants

These invariants are non-negotiable and the facade and bridge must preserve them. Any test that weakens any of them must be updated by an explicit ADR amendment, not by a quiet allow-list edit.

- **Preview before action.** Every action family in §2 requires a successful `propose_*` call and an operator confirmation before `submit_*` is reachable.
- **Evidence before promotion.** `propose_promote_to_paper` requires a non-blank recommendation and at least one known risk per R-PRM-008 (same contract as `cli/commands/promotion.py:_require_evidence_inputs`). Missing evidence produces a structured blocker; it does not silently insert a placeholder.
- **Risk veto before execution.** The runner that the facade starts uses the existing risk-veto path per ADR 0008. The facade does not introduce a parallel risk evaluation.
- **Manual gates before capital.** Live and micro-live remain out of scope. Paper runner start requires `stage: paper` per the strategy.py stage-compatibility check; promotion to paper requires the existing gate (`check_gate`) or `lifecycle_exempt=True` per R-PRM-004.
- **No live or micro-live launch scope.** ADR 0004 stays in force. The facade exposes no `to_stage="live"` or `to_stage="micro_live"` route. A future ADR is required to open them.
- **No strategy, model, or agent may weaken risk policy.** Risk-policy decisions remain owned by `milodex.risk`. The facade does not accept overrides.
- **No silent config mutation.** The only config mutation that may occur from a facade submit is the same YAML stage-line update that `promotion.state_machine.transition` and `.demote` already perform for the existing CLI path. The facade does not edit any other field; the bridge does not edit YAML at all.
- **No hidden event-store writes.** Every event written by a facade submit is written by an existing `milodex.promotion`, `milodex.core.event_store`, or `milodex.strategies.runner` write path. No new write site is introduced in `src/milodex/gui/` or in the bridge.
- **Every dangerous action gets a confirmation preview.** The Bench has no path from menu click to submit without rendering the proposal in a confirmation surface.
- **Every blocked action returns a structured blocker reason.** `Blocker.reason_code` is stable and tested; the GUI renders the human-readable `message`; the audit record carries both.
- **Every submit writes or links to durable audit evidence.** A `CommandResult` with `status="submitted"` has a non-null `audit_event_id` and at least one populated `durable_refs` field appropriate to the action family.

### 8. Command safety and degraded states

Submit-capable Bench actions are subject to the workflow-readiness rules in [OPERATIONS.md §"Command Safety Classification"](../OPERATIONS.md) and the audit-trail required-field list in [OPERATIONS.md §"Audit Trail: Previews and Submits"](../OPERATIONS.md).

- **Safe anytime** (`propose_backtest`, evidence inspection, manifest inspection, history queries): the facade may produce these proposals and submit backtests regardless of broker reachability or market hours, matching the CLI's existing safe-anytime classification.
- **Requires workflow readiness** (`submit_promote_to_paper`, `submit_start_paper_runner`, `submit_stop_paper_runner`, `submit_demote` for an actively running strategy): the facade checks reconciliation cleanliness, kill-switch state, data freshness, and broker reachability during `propose_*`, and re-checks at `submit_*`. A failure at either point yields a structured blocker.
- **Degraded modes are visible, not silently tolerated.** When the broker is down, when data is stale, when reconciliation drift is detected, when a `manifest_drift` or `no_frozen_manifest` condition holds (OPERATIONS.md §"Frozen manifests and runtime drift"), the GUI renders the blocker; exposure-increasing actions are refused.
- **Preview and submit audit records are reconstructable.** Preview audit records (when the facade chooses to persist a preview — required for promotion and runner-control families per R-XC-008 / ADR 0043) carry the fields enumerated in OPERATIONS.md; submit records add the additional fields and link back to the preview by `proposal_id`.
- **Idempotency.** The submit guards listed in OPERATIONS.md §"Idempotency Guarantees" (R-PRM-007/R-PRM-010 promotion duplication guards, advisory-lock-held refusal, duplicate-order policy) remain in force. The facade does not introduce new duplicate-guard logic; it surfaces existing refusals as blockers.

### 9. Test and boundary updates

The tests and boundary docs that protect ADR 0049's perimeter must be updated **only along the paths this ADR opens**. The rest of the perimeter remains in force.

- **Forbidden-token lists** (`tests/milodex/gui/test_qml_load_smoke.py`) are extended to permit the facade and bridge's named tokens (`CommandProposal`, `CommandResult`, `Blocker`, `propose_*`, `submit_*`, `BenchCommandFacade`) **only in the explicitly approved files**: `src/milodex/commands/bench.py`, the Qt bridge module under `src/milodex/gui/`, and the QML files that invoke the bridge for an action family wired in the corresponding PR. Tokens remain forbidden everywhere else.
- **No-command-key walkers** (`tests/milodex/gui/test_read_models.py`) continue to reject `command*`, `proposal*`, `submit*`, `dispatch*`, `broker`, `eventStore` keys on the Evidence Packet and Action Intent Preview shapes. The read-model surface is unchanged; commands ride a separate path.
- **`BenchConfirmationModal.qml` copy** changes only when the submit path for a specific action family is actually wired in the same PR. The banner `_COPY_DRAFT_BANNER` — *"Milodex can render this draft for review, but Bench v1 cannot submit it."* — is replaced action-family-by-action-family with submit-aware copy. The pinned `"Not wired in v1"` primary button is replaced with a real `MouseArea`-bearing button **only for action families whose submit path has landed**. No "Not wired in v1" text remains for an action family that is submit-capable; no submit affordance appears for an action family that is still preview-only.
- **`docs/BENCH_BOUNDARY.md`** is updated to describe the new v2 boundary in the same forensic detail as the v1 boundary. The v1 text (Evidence Packet / Action Intent Preview / Command Draft Preview) is preserved as historical record; a new "v2: command facade" section documents the data flow `CommandProposal → operator confirmation → CommandResult → durable audit`, names the facade module, and lists the action families that are submit-capable as of the most recent wiring PR.
- **Test coverage required per action family.** For every action family in §2, the wiring PR must add tests for: (a) a successful proposal, (b) at least one structured-blocker proposal, (c) a successful submit, (d) a blocked-late submit (proposal admissible at propose-time, blocked at submit-time), (e) the persistence of the audit record with the required fields, (f) that QML cannot reach broker/event-store/config code without going through the bridge.
- **Coverage gate** (`pyproject.toml:72`, `fail_under = 89`) holds. The facade and bridge are covered by the action-family tests; new code that drops the gate is a no-go.

### 10. Implementation sequence after this ADR

The wiring is sequenced narrowly. Each phase is a separate PR (or a tight bundle of PRs); each phase preserves the safety invariants in §7; each phase's test and boundary updates land in the same PR as the wiring.

**Phase A — ADR only (this PR).** No production code changes. `docs/adr/0051-bench-command-infrastructure-v1.md` lands. `docs/BENCH_BOUNDARY.md` may receive a forward-pointer paragraph naming this ADR as the v2 program; the v1 contract text stays intact. No QML changes, no facade module yet, no test changes beyond what this ADR explicitly authorises.

**Phase B — backend command facade (no submit wiring).** Introduce `src/milodex/commands/bench.py` with `CommandProposal`, `CommandResult`, `Blocker`, and `BenchCommandFacade` skeletons. Implement `propose_*` methods only — no `submit_*` work yet. Tests cover proposal shapes, validation paths, and blocker enumeration against fakes for the event store, runner, broker. QML is untouched.

**Phase C — first submit-capable action: demote / walk-back.** Demotion lands first because it is safer to wire (no broker call, no runner start, no capital effect) and because it exercises the audit-record path end-to-end. Wire `submit_demote`. Update `BenchConfirmationModal` copy for the demotion action family only. Update `BENCH_BOUNDARY.md` v2 section to list demotion as submit-capable. Forbidden-token tests are extended for the demotion bridge file only. The "Not wired in v1" affordance disappears for demotion and remains for every other family.

**Phase D — manifest freeze and promotion to paper.** Wire `submit_freeze_manifest` and `submit_promote_to_paper`. Preserve evidence-input requirements (non-blank recommendation, at least one risk) and existing gate behaviour. Manifest-drift and missing-manifest paths surface as structured blockers. Promotion to non-paper stages remains absent from the facade.

**Phase E — backtest.** Wire `submit_backtest` (and walk-forward via `walk_forward=True`). GUI surfaces the run ID, the evidence-package summary, and the OOS-aggregate metrics. Long-running backtests run on a worker thread; the GUI receives status updates via the existing read-model refresh path.

**Phase F — paper runner controls.** Wire `submit_start_paper_runner` and `submit_stop_paper_runner`. The advisory lock `milodex.runtime.strategy.<strategy_id>` is acquired by the facade; a held-elsewhere lock surfaces as a `Blocker(reason_code="advisory_lock_held")` with the holder PID in `context`. Stop is controlled-stop only (`shutdown(mode="controlled")`); the kill switch remains on the Anchor view per ADR 0049 Decision 4 and is not conflated with `submit_stop_paper_runner`.

**Implementation status (live).**

* **Phase A — ADR landed** (this document) on `master` at commit `fee27fe`+1.
* **Phase B — facade skeleton landed.** `src/milodex/commands/bench.py` and `src/milodex/commands/__init__.py` ship six `propose_*` methods and six `submit_*` Phase-B stubs returning `not_submit_capable_phase_b`. 37 facade tests + 1 allowlist pin test.
* **Phase C1 — `submit_demote` wired backend-only.** Routes through `milodex.promotion.state_machine.demote`; YAML stage-line update on `to_stage="backtest"`, ledger-only on `"disabled"`; re-validates proposals before dispatch. 9 new tests; ADR-0049 allowlist unchanged.
* **Phase C2 — demotion wired end-to-end into the Bench UI.** Bridge at `src/milodex/gui/bench_command_bridge.py` exposes only `proposeDemote` and `submitDemote`. `BenchConfirmationModal` is action-aware: demote shows an inline reason input and a "Confirm demotion" submit affordance routed through the bridge; every other action family still renders the inert "Not wired in v1" primary and the `_COPY_DRAFT_BANNER` text. `BenchSurface` listens for the modal's `submitted` signal. Successful submits trigger `BenchState` refresh. No QML file imports the facade or any broker / runner / event-store module; the bridge is the command boundary. `_ADR_0051_COMMAND_INFRA_ALLOWLIST` was widened by exactly one entry — the bridge module path — to make the wiring visible in the perimeter source.
* **Phase C2 review followups (F1–F4).** The bridge passes `gui_submit=True` to `propose_demote` / `submit_demote`; the facade emits a `disabled_demote_not_gui_ready` blocker when `gui_submit=True` and `to_stage="disabled"`, so the GUI submit surface refuses ledger-only disabled demotion until runtime refusal lands (`promotion.state_machine` slice 3). CLI defaults to `gui_submit=False` and is unchanged. Operator identity is sourced backend-side by `_resolve_operator_identity()` in the bridge — QML does not include `approved_by` in the propose payload and any such key is ignored. The QML smoke test now registers a real `BenchCommandBridge` so QML references to `Milodex.BenchCommandBridge` resolve at load time; a probe-QML test asserts `submitCapableActionFamilies()` returns `["demote"]`. The bridge's single permitted private reach into `BenchState._kick_refresh` is documented in code and pinned by `test_submit_demote_logs_when_kick_refresh_raises`.
* **Phase D1 — `submit_freeze_manifest` wired end-to-end.** Routes through `milodex.promotion.manifest.freeze_manifest` — the same governance callee the CLI's `milodex promotion freeze` uses. Stage eligibility (`paper` / `micro_live` / `live` only) is owned by the governance layer; refusals surface as `stage_not_freezable` blockers. No GUI-only carve-out: the CLI and GUI surface the same eligibility, identity flow, and durable_refs shape (`strategy_id`, `stage`, `config_hash`, `config_path`, `frozen_by`, `frozen_at`, `manifest_event_id`). `frozen_by` is sourced backend-side via the same `_resolve_operator_identity()` helper as demote's `approved_by`. The bridge exposes `proposeFreezeManifest` / `submitFreezeManifest` slots and updates `submitCapableActionFamilies()` to return `["demote", "freeze_manifest"]`. The Bench action menu (`compute_menu_items`) adds `LABEL_FREEZE_MANIFEST` for `_FREEZE_MANIFEST_STAGES` rows. The confirmation modal is action-family-aware: the OPERATOR INPUT (reason input) section is now scoped to `_isDemoteSubmit` only — freeze has no reason concept — and a `"Confirm freeze"` button is added alongside the C2 `"Confirm demotion"` button via a single `_dispatchSubmit()` router. `_ADR_0051_COMMAND_INFRA_ALLOWLIST` was NOT widened: Phase D1 reuses the existing bridge file. Allowlist still at the three entries Phase C2 established.
* **Phase D2 — `submit_promote_to_paper` wired backend-only.** Routes through `milodex.promotion.state_machine.transition` — the atomic governance callee `milodex promotion promote --to paper` uses, which writes the manifest + promotion event + YAML stage update in a single event-store transaction. Inputs match the CLI surface: `recommendation` and `known_risks` (both required non-blank per R-PRM-008), `run_id` (required unless `lifecycle_exempt`), and `approved_by` (CLI default `"operator"`; the future GUI bridge will source it via `_resolve_operator_identity()`). Submit re-validates the proposal via `propose_promote_to_paper`, runs `check_gate` (Sharpe > 0.5, max drawdown < 15%, trades >= 30), assembles the evidence package via `assemble_evidence_package`, and dispatches to `transition()`. Refusals surface as structured blockers: `missing_recommendation`, `missing_known_risks`, `missing_run_id`, `wrong_source_stage`, `backtest_run_not_found`, `gate_check_failed` (one per failure reason), `invalid_stage_transition`, `governance_refused`, `event_store_unavailable`, `proposal_action_family_mismatch`. On success, `durable_refs` carries `strategy_id`, `from_stage`, `to_stage`, `promotion_type`, `approved_by`, `recorded_at`, `manifest_hash`, plus optional `promotion_id`, `manifest_id`, `backtest_run_id`, `sharpe_ratio`, `max_drawdown_pct`, `trade_count`. `audit_event_id` is the promotion event id. Phase D2 is **backend-only**: the bridge does NOT expose promote slots and `submitCapableActionFamilies()` still returns `["demote", "freeze_manifest"]`. GUI wiring (modal action-awareness, menu surfacing of submit, backend-sourced `approved_by`) lands in Phase D3. ADR 0051 allowlist unchanged. The facade now imports `_metrics_from_run` and `_compute_post_update_hash` from `milodex.cli.commands.promotion` (private helpers); this continues the Phase C2 review E1 pattern-debt and should be cleaned up by graduating both helpers to a public `milodex.promotion` surface.
* **Phase D3 — promote-to-paper GUI wiring.** Not yet started.
* **Phase E — backtest.** Not yet started.
* **Phase F — paper runner controls.** Not yet started.
* **Phase G — launch-readiness re-run.** Not yet started.

**Phase G — launch-readiness re-run.** Update [`docs/LAUNCH_READINESS.md`](../LAUNCH_READINESS.md). Walk the full lifecycle from the GUI without CLI use: idle/backtest → backtest run → evidence review → freeze (where required) → promote to paper → start paper runner → controlled stop → demote/walk-back. Verify durable logs, audit records, blocker rendering, and no broker/event-store reach-through from QML. Update the §1 manual-operator-walk steps. The launch acceptance criteria in §11 below are checked against the running system.

PRs that combine phases, or that skip a phase, are not allowed. The sequence exists so each step preserves a known-good substrate for the next.

### 11. Acceptance criteria for launch capability

The launch is GO when **all** of the following hold on `master`, against the launch commit:

- From the GUI, an operator can move a strategy through the normal paper lifecycle without dropping into the CLI: idle/backtest-ready → backtest → evidence review → freeze (where required) → promote to paper → start paper runner → controlled stop → demote/walk-back.
- Each action has visibly distinct preview, validation, confirmation, and result states.
- Blocked actions render a structured blocker with a stable `reason_code` and an operator-readable `message`.
- Promotion to paper uses the same evidence requirements, gate machinery, and frozen-manifest semantics as the CLI path. There is no parallel GUI-only promotion route.
- Paper runner start respects `stage: paper` and refuses non-paper stages with a structured blocker.
- A second start of the same strategy is refused with `advisory_lock_held` carrying the holder PID.
- Controlled stop produces `exit_reason="controlled_stop"` and is *not* conflated with the kill switch.
- Demotion / walk-back is auditable: a `promotion_event` row with `promotion_type="demotion"` is written, with reason and approver populated.
- Micro-live and live remain locked. The facade exposes no route to them. ADR 0004 is untouched.
- QML remains a thin surface over the facade-bridge. Forbidden-token tests pass; no broker, event-store, or YAML write site is reachable from QML.
- `pytest -q` passes; `ruff check src/ tests/` passes; coverage ≥ `fail_under` in `pyproject.toml`.

## Rationale

**A narrow amendment is safer than a broad rewrite.** ADR 0049's binary "no backend mutation" rule made v1 testable by inspection because every contributor drew the same line. A multi-axis amendment ("limited writes," "low-risk writes only") would re-introduce the partial-wiring failure mode ADR 0049 was written to prevent. Naming the action families in §2 and the facade in §4 — and binding everything else back to ADR 0049 in §1 — preserves the property that the perimeter is enforceable.

**A single facade keeps QML thin and the business rules in one place.** The temptation, with a GUI lifecycle program of this size, is to scatter "just one Python call" sites across QML modules. That scatter is exactly what makes a QML codebase grow business rules over time. A single facade with a single bridge means there is one perimeter to test against, one set of forbidden tokens to grep for, and one module a reviewer reads to understand what the GUI can submit.

**Reusing the CLI/governance modules avoids two implementations of the same lifecycle.** Every action family already has a callee path: `freeze_manifest`, `validate_stage_transition`, `check_gate`, `assemble_evidence_package`, `transition`, `demote`, `AdvisoryLock`, `_check_stage_compatibility`, `StrategyRunner.run`, `shutdown(mode="controlled")`. The facade is an orchestrator over these, not a re-implementation. If the GUI started forking these rules, the two implementations would drift, and the audit reader would have to learn two languages.

**Propose / submit separation is what makes the GUI feel like the CLI's preview/submit pattern.** R-CLI-007 and the OPERATIONS.md preview/submit audit-record shape already encode this separation. Mirroring it at the facade level — same fields, same `proposal_id` linking, same blocker structure — means the audit reader can reconstruct what happened from a GUI submit exactly the same way they can from a CLI submit. There is no "GUI flavour" of audit record.

**Demotion-first sequence inverts the right risk.** Demotion is the action family with the lowest immediate operational risk (no broker call, no runner start, no capital effect) and the highest audit-shape value (every audit field is exercised). Wiring it first proves the facade and bridge under low stakes before the higher-stakes runner-control wiring lands.

**Paper-only and kill-switch separation are non-negotiable for launch.** ADR 0004 and ADR 0005 are the floor of the product's safety posture, and the GUI is the surface most likely to muddle them. Reusing the stage-compatibility check from `strategy.py`, keeping the kill switch on the Anchor view, and routing stop only through `shutdown(mode="controlled")` ensures the GUI cannot, even by mistake, cross either line.

**The 89% coverage gate stays where it is.** The pre-launch run hit `fail_under` exactly. The facade and bridge inherit existing test infrastructure (CLI test patterns for proposal validation, runner shutdown tests for stop behaviour) so the per-action-family test budget is the new code's own coverage — not a redistribution of the existing budget.

## Consequences

- **`docs/BENCH_BOUNDARY.md` gains a v2 section.** The v1 text is preserved; the v2 section names the facade, the bridge, the action families that are submit-capable as of the most recent wiring PR, and the data flow `CommandProposal → confirmation → CommandResult → audit`. The v1 forbidden-key invariants on the Evidence Packet and Action Intent Preview are unchanged.
- **`docs/LAUNCH_READINESS.md` is re-run at Phase G.** The §1 first-run, kill-switch-visibility, paper-mode-safety, and CLI-smoke items are extended with corresponding GUI-driven checks. The "manual operator walk" steps in §5 are replaced with the lifecycle walk through the Bench.
- **`BenchConfirmationModal.qml` changes per action family, not in one sweep.** The `_COPY_DRAFT_BANNER` text and the `"Not wired in v1"` primary button are replaced family-by-family as each Phase C–F PR lands. At any commit on `master`, the modal's copy and affordance match the wiring state of each action family exactly.
- **Forbidden-token tests get **narrow** expansions, not allowlist widenings.** `tests/milodex/gui/test_qml_load_smoke.py` continues to reject `submitCommand`, `dispatchCommand`, and the broader mutation tokens. The newly permitted tokens (`CommandProposal`, `CommandResult`, `BenchCommandFacade`, `propose_*`, `submit_*`) are admitted only in the explicit allowlist of files for the action families that are wired. Any future PR that introduces a token outside the allowlist is rejected, on the same principle as ADR 0049.
- **A new module `src/milodex/commands/bench.py` exists.** It is reusable from CLI, tests, and future surfaces. It does not import `PySide6`. It owns no business rules; it orchestrates existing modules.
- **A Qt bridge module is added under `src/milodex/gui/`.** It is the only file in `src/milodex/gui/` that is allowed to call the facade. Read-model classes (`BenchState`, `OperationalState`, etc.) remain read-only and do not call the facade.
- **The kill switch stays on the Anchor view.** ADR 0049 Decision 4 is preserved verbatim: `submit_stop_paper_runner` is controlled-stop only and is not a kill-switch trigger.
- **The promotion pipeline remains the source of truth for stage transitions.** The GUI does not introduce an alternative promotion path; the facade calls into `milodex.promotion` for every stage transition.
- **The risk layer is untouched.** No new override, no new bypass, no new "GUI risk policy" knob. The runner that the facade starts is the same runner the CLI starts, with the same risk evaluator in the chokepoint.
- **The launch bar moves from "polished read-only GUI" to "GUI-controlled paper lifecycle."** The launch-readiness pass that closed conditional-GO on `fee27fe` is superseded by the Phase G launch-readiness pass against the post-Phase-F commit. Editorial Dark remains the only theme at launch; ADR-0051 wiring is the launch-blocking work, not theme parity.

## Non-goals

- Does **not** open live or micro-live trading. ADR 0004 stays in force.
- Does **not** authorise any GUI surface to trigger the kill switch. ADR 0005 and ADR 0049 Decision 4 stay in force.
- Does **not** introduce a strategy-owned, model-owned, or agent-owned risk policy. Risk-policy ownership remains with `milodex.risk`.
- Does **not** change the read-model schema or the freshness computation defined in ADR 0050.
- Does **not** change the Bench verb model, action-menu computation, or layout decisions established in ADRs 0036, 0043, 0044, 0045, 0046, 0047, 0048.
- Does **not** authorise QML business rules. QML's responsibilities remain bounded by Decision 5.
- Does **not** open broker-live execution from any GUI surface.
- Does **not** authorise Light/Bronze theme parity work as part of launch. Editorial Dark remains the launch scope per the 2026-05-14 launch-scope decision (`fee27fe`).
- Does **not** weaken or replace any forbidden-token test or no-command-key walker. Existing protections continue; this ADR is the *only* path by which any of them may be narrowly extended.
- Does **not** ship as a combined PR with code. Phase A is docs-only; code wiring begins at Phase B and proceeds one action family per PR.

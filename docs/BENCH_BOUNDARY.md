# Bench command boundary

> **Current submit-capable Bench actions (2026-05-15).** The GUI command
> bridge can submit demotion / walk-back, freeze manifest, canonical
> backtest evidence, promote-to-paper, and paper-runner start/controlled-stop.
> Promote-to-paper uses the
> existing promotion governance path, requires operator recommendation and
> known-risk text, uses the row's evidence run id, and sources `approved_by`
> backend-side. Start/stop paper runner controls are paper-only; Stop Trading
> writes a controlled-stop request and is not the kill switch.

**Scope:** Bench v1 implementation (PRs F–O, ADR 0049).
**Audience:** Any contributor who looks at the Bench code and wonders whether a Bench action can be wired to a real command path.
**Short answer:** Only through the ADR 0051 command facade and bridge. Everything else stays preview-only.

> **Forward pointer — ADR 0051 (Bench Command Infrastructure v1).** [ADR 0051](adr/0051-bench-command-infrastructure-v1.md) is now the binding command boundary for Bench submit-capable families. A narrow, named set of action families (backtest, freeze manifest, promote to paper, demote, start/stop paper runner) crosses from QML through the Qt bridge into a single Python facade at `src/milodex/commands/bench.py`. ADR 0049 remains binding for everything outside those named families: no direct broker calls, no direct event-store writes, no runner construction, and no YAML mutation from QML.
>
> **Implementation status — post-Phase F with durable readiness gate (2026-05-26).**
>
> * ADR 0049 remains binding for every Bench path not explicitly opened below. Forbidden-token tests on QML (`submitCommand`, `dispatchCommand`, `broker.`, `eventStore.`, `BenchState.demote`, `config.write`, …) remain in force across all QML files.
> * ADR 0051 command infrastructure exists: `src/milodex/commands/bench.py` ships `BenchCommandFacade` with six `propose_*` methods and `CommandProposal` / `CommandResult` / `Blocker` / `Precondition` dataclasses.
> * **Demotion / walk-back (`submit_demote`) is the first end-to-end GUI-submit-capable action family (Phase C2).** Wiring chain: `BenchConfirmationModal → BenchCommandBridge → BenchCommandFacade → milodex.promotion.state_machine.demote` — the same governance callee the CLI uses. The GUI submit surface accepts only `to_stage="backtest"` (YAML stage-line is rewritten). `to_stage="disabled"` is refused by the facade when `gui_submit=True` with a `disabled_demote_not_gui_ready` blocker until runtime refusal of disabled strategies lands (`promotion.state_machine` slice 3); the CLI demote path keeps allowing ledger-only `disabled` unchanged. Re-validation happens at submit time so a drifted proposal is refused before dispatch.
> * **Freeze-manifest (`submit_freeze_manifest`) is the second end-to-end GUI-submit-capable action family (Phase D1).** Wiring chain: `BenchConfirmationModal → BenchCommandBridge → BenchCommandFacade → milodex.promotion.manifest.freeze_manifest` — the same governance callee the CLI's `milodex promotion freeze` uses. The GUI submit surface honors the same stage eligibility as the CLI: a paper / micro_live / live strategy freezes; a backtest-stage strategy is refused with `stage_not_freezable` (matches the existing CLI behavior; no GUI-only carve-out). Stale-proposal revalidation runs at submit time. The freeze action has no reason input — the confirmation modal hides the OPERATOR INPUT section for `freeze_manifest`. Freeze is added to the Bench action menu for `_FREEZE_MANIFEST_STAGES` rows.
> * **The Bench command bridge** is `src/milodex/gui/bench_command_bridge.py`. It is the only file under `src/milodex/gui/` permitted to import the facade. The bridge exposes action-specific propose/submit slots for demote, freeze manifest, backtest, promote-to-paper, start paper runner, and stop paper runner, plus the introspection slot `submitCapableActionFamilies()`. Operator identity (`approved_by` / `frozen_by`) is sourced backend-side by `_resolve_operator_identity()` — QML does not decide identity.
> * **The confirmation modal is action-aware.** Demote renders a required reason and "Confirm demotion"; freeze renders "Confirm freeze"; backtest renders "Run backtest"; promote-to-paper renders recommendation/risk inputs and "Confirm promotion"; Start Trading renders "Start paper runner"; Stop Trading renders "Request stop." Return actions still render the inert "Not wired in v1" primary and the non-submit-capable draft banner.
> * **Promote-to-paper (`submit_promote_to_paper`) is end-to-end GUI-submit-capable (Phase D3).** The bridge exposes `proposePromoteToPaper` / `submitPromoteToPaper`, sources `approved_by` backend-side, and uses the same `milodex.promotion.state_machine.transition` governance callee as the CLI. The modal requires operator recommendation and known-risk text, passes the row's evidence run id, and surfaces gate, evidence, stale-stage, and unknown-run blockers from the facade.
> * **Paper runner controls (`submit_start_paper_runner`, `submit_stop_paper_runner`) are submit-capable (Phase F).** Start launches the existing `milodex strategy run <strategy_id>` path through a non-blocking runner-control boundary. Stop writes a controlled-stop request consumed by `StrategyRunner` between cycles; it does not trigger the kill switch.
> * **Start/promote readiness consults durable reconciliation readiness.** Bench reads the latest durable readiness verdict instead of the former `reconciliation_scaffolded` placeholder. Exposure-increasing paper paths require a clean reconcile for the current America/New_York date; deferred reconciliation dimensions remain warning-only in this slice.
> * **QML still cannot mutate state directly.** No QML file imports the facade, calls a broker client, opens a runner, writes the event store, or edits YAML. The bridge is the command boundary. Forbidden tokens (`BenchState.demote`, `broker.`, `eventStore.`, `config.write`, `submitCommand`, `dispatchCommand`, `CommandProposal`) are still rejected by `test_bench_pr_n_no_mutation_token_drift` in every Bench QML file.

## What Bench is

Bench is a **read-model / operator ledger surface with a narrow command bridge**. It renders strategy state from the GUI read-models and exposes a per-row Action menu that previews every action before any submit-capable family crosses into the Python facade.

There is still no direct QML mutation path. QML does not call brokers, event stores, runners, YAML writers, or kill-switch handlers directly; submit-capable actions go through `BenchCommandBridge -> BenchCommandFacade`. There is no row-order persistence and no Bench kill-switch interaction.

## The three read-only layers

Each layer is a normalized, read-only object. None of them is, or can become, an executable command without an explicit ADR change. The chain is:

```
Evidence Packet  →  Action Intent Preview  →  Command Draft Preview
```

### 1. Evidence Packet (`row.evidencePacket`, PR M)

A consolidated read-only snapshot of *what evidence-shaped data the GUI has for one strategy.* Lives on every Bench row, produced by `_evidence_packet()` in `src/milodex/gui/read_models.py`.

**Invariants enforced by tests:**

- `source.kind == "gui_read_model_snapshot"`
- `source.authoritative is False`
- `gate.freshness == "not_reconstructed_v1"`
- `gate.gateResult == "not_reconstructed_v1"`
- `gate.reconstructionDeferred is True`
- No `command*`, `proposal*`, `submit*`, `dispatch*`, `broker`, or `eventStore` keys at any depth

`gate.freshnessDisplay` / `gate.gateResultDisplay` (GUI audit finding #1 / M2 item c) are humanized operator-facing copy of the two sentinels above ("Deferred (v1) — not yet computed") — display-layer only, the raw machine sentinels are unchanged.

**What the Evidence Packet is NOT:** an authoritative gate verdict, a freshness reconstruction from the event store, a basis for an automated decision.

### 2. Action Intent Preview (`action.actionIntentPreview`, PR N)

A normalized, read-only preview attached to every action in `row.actions`. Produced by `_action_intent_preview()` in `src/milodex/gui/read_models.py`.

**Invariants enforced by tests:**

- `source.kind == "gui_read_model_preview"`
- `source.authoritative is False`
- `executable is False`
- `wired is False`
- No `command*`, `proposal*`, `submit*`, `dispatch*`, `broker`, or `eventStore` keys at any depth

**What the Action Intent Preview is NOT:** an executable verb, a command payload, a ready-to-submit object, an authoritative classification of whether the action would succeed.

### 3. Command Draft Preview (`commandDraftPreview`, PR O)

A **local, QML-only** composition of `evidencePacket + actionIntentPreview` rendered inside `BenchConfirmationModal.qml`. It is not a Python object; it is not on the read-model; it is not on the wire; it is not persisted. It exists only while the confirmation modal is open.

**Invariants enforced by tests:**

- `source.kind == "local_ui_draft_preview"`
- `source.authoritative is False`
- `executable` / `wired` mirror whether the selected action family is currently submit-capable
- `submissionState` / `validationState` distinguish submit-capable actions from preview-only actions
- Preview-only actions still use `submissionState: "not_submittable_v1"` and `validationState: "not_validated_v1"`
- Banner copy: `Milodex can render this draft for review, but Bench v1 cannot submit it.`
- For preview-only action families, the modal's primary button stays `Not wired in v1` with no MouseArea. Submit-capable action families route through `BenchCommandBridge` action-specific propose/submit slots.

**What the Command Draft Preview is NOT:** a `CommandProposal`, a submit handler, a dispatch path, a risk-approved payload, a backend integration point.

The name `commandDraftPreview` is the **single permitted `command*` token** in the Bench QML files. Every other `command*` token (`CommandProposal`, `submitCommand`, `dispatchCommand`, etc.) is rejected by the forbidden-token tests in `tests/milodex/gui/test_qml_load_smoke.py`. This is deliberate: any future PR that tries to graft a submit path onto this object must either rename the property and break the layout/safety tests, or introduce a new forbidden token — both routes surface the intent in code review.

## How to add a new submit-capable action family

Any future move from "preview" to "submit" must:

1. Amend the ADR 0051 action-family list, or open a new ADR if the action changes the safety model.
2. Land in a separate PR that routes through `BenchCommandBridge -> BenchCommandFacade`; no QML file may call a broker, event store, runner, YAML writer, or risk service directly.
3. Add proposal and submit slots with submit-time revalidation, including workflow-readiness checks when the action can affect paper execution state.
4. Update this document, the `_COPY_*` constants in `BenchConfirmationModal.qml`, and the forbidden-token tests so the visible banner and code contract match the new action family.

## What protects this boundary

- **`ADR 0049`** — the binding policy decision.
- **This document** — the data-shape walkthrough that operators and contributors can read.
- **Forbidden-token tests** in `tests/milodex/gui/test_qml_load_smoke.py` — narrow, intentional, and named for the PR that introduced each token.
- **No-command-key walkers** in `tests/milodex/gui/test_read_models.py` — recurse the Evidence Packet and Action Intent Preview at every depth and reject command/proposal/broker/eventStore keys.
- **Action-aware `executable` / `wired` invariants** — read-model previews remain non-executable, while the QML draft mirrors the selected action family's submit capability.
- **A pinned, inert "Not wired in v1" primary button for preview-only actions** — guarded by tests that reject generic `onSubmit`, `submitDraft`, `submit(`, `dispatch(`, `executeDraft`.

If you find yourself disabling or weakening any of the above, stop. Open a new ADR first.

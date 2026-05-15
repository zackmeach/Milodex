# Bench v1 — checkpoint

**Status:** Historical checkpoint. Bench is no longer purely read-only for the
Phase 1 paper lifecycle.
**Last update:** superseded on 2026-05-15 by ADR 0051 Phase E/F lifecycle
wiring.
**Binding policy:** [ADR 0051](../adr/0051-bench-command-infrastructure-v1.md)
for submit-capable lifecycle actions; [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md)
for all remaining preview-only boundaries.
**Architecture walkthrough:** [`docs/BENCH_BOUNDARY.md`](../BENCH_BOUNDARY.md).
**Operator workflow:** [`docs/PAPER_WORKFLOW.md`](../PAPER_WORKFLOW.md).

This document is kept for the design/boundary rationale from PRs F-P. Current
Bench behavior is: QML remains a read-model surface with no direct broker,
event-store, runner, or YAML write access, while specific lifecycle actions
submit through `BenchCommandBridge -> BenchCommandFacade`.

## What PRs F–P accomplished

| PR | Title | Shape added |
|----|------|-------------|
| F  | QML reconciliation to v1 vertical ledger | Vertical stage sections; v1 read-model shape |
| G  | Wire Action menu from `compute_menu_items` | `row.actions` driven by the pure menu engine |
| H  | Within-section visual drag reorder | Local-only priority reorder (not persisted) |
| I  | Folio-mark, row-body click, scroll/drag polish | Stable drag coordinate frame, stable columns |
| J  | Open Evidence read-only modal | First modal: `BenchEvidenceModal` |
| K  | Confirmation modal visual shells | Per-action preview shells with disabled primary |
| L  | Action Intent Packet previews | Six-section confirmation modal body |
| M  | Normalized read-only Evidence Packet | `row.evidencePacket` (Python read-model) |
| N  | Normalized read-only Action Intent Preview | `action.actionIntentPreview` (Python read-model) |
| O  | Local Command Draft Preview boundary section | `commandDraftPreview` (QML local), viewport-bounded modal |
| P  | Boundary hardening: docs + Python no-command guard | `docs/BENCH_BOUNDARY.md`; Python `class CommandProposal` etc. forbidden |

## Current Bench UX behavior

- **Layout:** Five vertical stage sections (Idle, Backtest, Paper, Micro Live, Live), each rendering its strategy rows from the read-model snapshot.
- **Row affordances:** Folio mark, row-body click selection, drag-to-reorder within a section, per-row Action menu derived from `compute_menu_items(state)`.
- **Action menu:** Promote / Demote / Return / Start Trading / Stop Trading / Initiate Backtest / Refresh Backtest / Open Evidence — items appear only when the menu engine deems them applicable. Open Evidence is the always-present informational floor.
- **Open Evidence:** Opens `BenchEvidenceModal` — a read-only snapshot of the row's `evidencePacket` (identity, source, gate metrics, evidence timestamps, status, session/job, available actions).
- **Non-Open-Evidence actions:** Open `BenchConfirmationModal`, which renders a seven-section Intent Packet preview. Submit-capable families use action-specific bridge slots; preview-only families keep the inert primary.
- **Modal layout:** Viewport-bounded with a scrollable body and a pinned footer (PR O follow-up). The primary action button reflects the selected action family: submit-capable lifecycle actions can be approved, while preview-only actions remain not wired.
- **Wheel/keyboard:** Modal scrolls its own body; wheel/arrow events do not leak to the Bench Flickable underneath. Escape, ✕, outside-click, and Cancel all close.
- **Drag reorder:** Survives the modal lifecycle but is not persisted across application restarts (PR H scope).

## Safety boundaries currently enforced

The full walkthrough lives in [`docs/BENCH_BOUNDARY.md`](../BENCH_BOUNDARY.md). The headline:

- **`CommandProposal` and `CommandResult` are allowed only inside the command boundary** (`src/milodex/commands/bench.py`, `src/milodex/gui/bench_command_bridge.py`, and tests/docs that pin the boundary).
- **No `submitCommand`, `dispatchCommand`, or `executeOrder` function or call** — enforced by forbidden-token guards across all Bench QML files.
- **No broker call, no event-store write, no read-model mutation, no config write, no `BenchState.<mutation>`** — same guards.
- **`evidencePacket.source.authoritative is False`**, `gate.reconstructionDeferred is True`, `freshness == "not_reconstructed_v1"`, `gateResult == "not_reconstructed_v1"` — enforced by Python tests.
- **`actionIntentPreview.executable` and `wired` mirror the action family.** Submit-capable actions route through the bridge; preview-only actions remain inert.
- **`commandDraftPreview` remains a QML-local review object.** It is not the persisted command, not the facade proposal, and not allowed to call business logic directly.
- **Modal primary button has no `MouseArea`** — guarded by tests rejecting `onSubmit`, `submitDraft`, `submit(`, `dispatch(`, `executeDraft`.
- **The string `commandDraftPreview` is the *single* allowed `command*` token in Bench QML.** Any rename or addition trips the layout/safety tests.

## What Bench still cannot do directly

- No direct QML command submission. Wired actions submit only through `BenchCommandBridge`.
- No QML broker call.
- No QML event-store write.
- No QML strategy runner construction.
- No QML config file write.

## What is explicitly not implemented yet

- No authoritative gate reconstruction. The `evidencePacket.gate` carries `not_reconstructed_v1` sentinels; freshness and pass/fail are deferred to a future ADR.
- No real freshness derivation from the event store.
- No persisted row-order. Drag reorder is local-session only.
- No capital-bearing micro-live/live submit path.
- No kill-switch trigger from Bench Stop Trading; stop is controlled-stop only.
- No ML/LLM trader lifecycle. Their contracts are deferred until trader mechanics and decision artifacts are specified.

## Recommended future PRs, ordered by safety

Each tier should fully land before the next begins. A new ADR is required at tier 3; a separate PR is required at tier 4 even after the ADR lands.

### Tier 1 — UX polish / readability only (safe, no new code paths)

- Action-menu copy review (verb tone, capitalization, deck strings).
- Confirmation-modal visual tightening (section spacing, label register, requirements-list rendering).
- Evidence-modal section ordering and field labels.
- Drag interaction polish (cursor states, drop indicator clarity).
- Accessibility pass (focus rings, screen-reader labels, keyboard nav through the action menu and modals).

Constraints: no new properties on read-models; no new QML helpers that touch state; forbidden-token guards remain untouched.

### Tier 2 — architecture planning for future command submission (no code)

- Design doc draft (not yet an ADR) describing the shape a `CommandProposal` would take, what `submitCommand` would have to validate, where the validation layer would sit, and which existing infrastructure (risk layer, kill switch, event store) it would call.
- Update `docs/BENCH_BOUNDARY.md` with a "next-step sketch" section that links to the draft.
- Decide whether the command submission layer lives in `src/milodex/gui/` (UI-adjacent) or in a new `src/milodex/commands/` (parallel to `risk/`, `execution/`).

Constraints: no Python code touching the proposed shape; the design doc is text only; tier-1 forbidden-token tests remain green throughout.

### Tier 3 — ADR draft for command execution (governance, not code)

- New ADR amending or superseding [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) Decision 2.
- ADR must specify: which Bench actions become executable, what their preconditions are, which existing safety layers (risk, kill switch, event store, manual approval per CLAUDE.md "Actions that always require explicit human approval") apply, how the audit trail is structured, and what the rollback path looks like.
- ADR must explicitly call out the per-action human-approval expectation for promotion to live, capital allocation, kill-switch reset, and broker live-trade permission per the project's autonomy boundary.

Constraints: ADR only; no code changes; existing forbidden-token tests still green; the ADR PR updates `docs/BENCH_BOUNDARY.md` and `docs/bench/README.md` (this file) to point at the new ADR.

### Tier 4 — command infrastructure prototype (only after ADR lands)

- A separate PR that introduces command infrastructure under the constraints set by the new ADR.
- The forbidden-token list must be edited as part of this PR, not as a side-effect — the diff should make the allowlist widening visible at code review.
- The disabled primary button stays disabled until a subsequent PR wires it; the prototype must be exercised in a paper/test path before any real button becomes clickable.

Constraints: nothing in this tier merges without an accepted Tier 3 ADR; the change must update `docs/BENCH_BOUNDARY.md` so the banner copy no longer says "Bench v1 cannot submit it."

## Where to find things

- **Production code:**
  - `src/milodex/gui/read_models.py` — `_StrategyRow.as_qml()`, `_evidence_packet()`, `_action_intent_preview()`.
  - `src/milodex/gui/bench_v1.py` — pure menu engine (`compute_menu_items`).
  - `src/milodex/gui/qml/Milodex/surfaces/BenchSurface.qml` — Bench surface, modal orchestration.
  - `src/milodex/gui/qml/Milodex/components/BenchRow.qml`, `BenchModal.qml`, `BenchEvidenceModal.qml`, `BenchConfirmationModal.qml`.
- **Tests:**
  - `tests/milodex/gui/test_read_models.py` — Evidence Packet and Action Intent Preview shape/safety tests.
  - `tests/milodex/gui/test_qml_load_smoke.py` — QML load smoke, forbidden-token guards, layout guards, boundary-doc guards.
  - `tests/milodex/gui/test_bench_v1.py`, `test_bench_action_menu_wiring.py` — menu-engine semantics.
- **Policy / docs:**
  - [`docs/adr/0049-...md`](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) — binding policy.
  - [`docs/BENCH_BOUNDARY.md`](../BENCH_BOUNDARY.md) — three-layer read-only architecture walkthrough.
  - This file — checkpoint summary and recommended future sequencing.

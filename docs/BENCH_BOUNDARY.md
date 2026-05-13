# Bench command boundary

**Scope:** Bench v1 implementation (PRs F–O, ADR 0049).
**Audience:** Any contributor who looks at the Bench code and wonders whether a Bench action can be wired to a real command path.
**Short answer:** No. Not without a new ADR and a new PR. This document explains why.

## What Bench is

Bench is a **read-model / operator ledger surface**. It renders strategy state from the GUI read-models and exposes a per-row Action menu that previews — but never executes — operator-driven stage transitions and session controls.

There is no Bench code path that mutates backend state. There is no broker call, no event-store write, no read-model write, no config write, no row-order persistence, no kill-switch interaction. ADR 0049 Decision 2 is the binding contract; this document is a layer on top describing the *data shape* that contract has acquired over PRs M / N / O.

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
- `executable: false`
- `wired: false`
- `submissionState: "not_submittable_v1"`
- `validationState: "not_validated_v1"`
- Banner copy: `Milodex can render this draft for review, but Bench v1 cannot submit it.`
- The modal's primary button stays `Not wired in v1` with no MouseArea

**What the Command Draft Preview is NOT:** a `CommandProposal`, a submit handler, a dispatch path, a risk-approved payload, a backend integration point.

The name `commandDraftPreview` is the **single permitted `command*` token** in the Bench QML files. Every other `command*` token (`CommandProposal`, `submitCommand`, `dispatchCommand`, etc.) is rejected by the forbidden-token tests in `tests/milodex/gui/test_qml_load_smoke.py`. This is deliberate: any future PR that tries to graft a submit path onto this object must either rename the property and break the layout/safety tests, or introduce a new forbidden token — both routes surface the intent in code review.

## How to wire real commands later

Any future move from "preview" to "submit" must:

1. Open a new ADR amending or superseding ADR 0049 Decision 2.
2. Land in a separate PR. No `CommandProposal` class, no `submitCommand` function, no broker call, no event-store write may be introduced piecewise inside an unrelated Bench refactor.
3. Update this document and the `_COPY_*` constants in `BenchConfirmationModal.qml` so the visible banner no longer says *"Bench v1 cannot submit it."*
4. Update the forbidden-token list in `tests/milodex/gui/test_qml_load_smoke.py` to reflect the new contract — do **not** silently widen the allowlist.

## What protects this boundary

- **`ADR 0049`** — the binding policy decision.
- **This document** — the data-shape walkthrough that operators and contributors can read.
- **Forbidden-token tests** in `tests/milodex/gui/test_qml_load_smoke.py` — narrow, intentional, and named for the PR that introduced each token.
- **No-command-key walkers** in `tests/milodex/gui/test_read_models.py` — recurse the Evidence Packet and Action Intent Preview at every depth and reject command/proposal/broker/eventStore keys.
- **`executable: false` / `wired: false` invariants** — checked in Python tests (preview) and grepped in QML tests (draft).
- **A pinned, inert "Not wired in v1" primary button** — guarded by tests that reject `onSubmit`, `submitDraft`, `submit(`, `dispatch(`, `executeDraft`.

If you find yourself disabling or weakening any of the above, stop. Open a new ADR first.

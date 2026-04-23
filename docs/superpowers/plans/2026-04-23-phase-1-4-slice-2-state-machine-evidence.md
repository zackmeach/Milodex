# Phase 1.4 Slice 2 — Promotion State Machine + Evidence Package

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the second of three Phase 1.4 slices — a governed promotion transition that (1) lives under `milodex.promotion`, (2) emits a structured evidence package into durable state per R-PRM-003 / `PROMOTION_GOVERNANCE.md`, (3) auto-freezes the manifest in the same transaction as the stage change, and (4) supports explicit demotion + read-only history. Closes ROADMAP §6.1.2. Does **not** implement the live-stage refusal hook (slice 3) or the paper→micro_live transition (R-PRM-006, Phase 2+).

**Architecture:** One module move (`strategies/promotion.py` → `promotion/state_machine.py`), one new evidence-package assembler (`promotion/evidence.py`), two schema migrations on `promotions` (add `manifest_id` FK + `reverses_event_id` + `evidence_json`), one CLI rewire (`milodex promote` becomes a thin wrapper that delegates to `promotion.state_machine.transition`), two new CLI subcommands (`milodex demote`, `milodex promotion history`). No changes to the risk layer, execution service, or strategy interface — slice 1's drift check does the runtime enforcement; this slice just extends the governance surface.

**Tech Stack:** Python 3.11+, pytest, existing SQLite event store, existing `milodex.promotion.freeze_manifest`, existing `milodex.strategies.promotion.check_gate` (about to move).

---

## 1. Context

### What slice 1 landed
- `strategy_manifests` table (append-only, keyed on `(strategy_id, stage)`) + `StrategyManifestEvent`.
- `milodex.promotion` module with `freeze_manifest`, `get_active_manifest_hash`, `resolve_strategy_config_path`.
- Risk-layer `_check_manifest_drift` fires `no_frozen_manifest` / `manifest_drift` at paper+ stages.
- Service wires `runtime_config_hash` + `frozen_manifest_hash` into `EvaluationContext`.
- CLI `milodex promotion freeze` and `milodex promotion manifest` (read-only show).

### What's already on master that slice 2 reuses
- `src/milodex/strategies/promotion.py` — `validate_stage_transition`, `check_gate`, `PromotionCheckResult`. The pure state-machine logic is here; slice 2 **moves** it to `promotion/` (not rewrites it) so ADR 0015's module boundary matches the code.
- `src/milodex/cli/commands/promote.py` — already handles `promote --to <stage>`, already writes `PromotionEvent`, already updates the YAML's `stage:` line. It **does not** freeze the manifest, does not assemble an evidence package, does not link a manifest_id. Slice 2 extends it.
- `src/milodex/core/event_store.py:103` — `PromotionEvent` has `strategy_id, from_stage, to_stage, promotion_type, approved_by, recorded_at, backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count, notes, id`. Slice 2 adds **three** columns: `manifest_id` (FK to `strategy_manifests.id`), `reverses_event_id` (nullable FK to prior `promotions.id`), `evidence_json` (the bundled package).
- `docs/PROMOTION_GOVERNANCE.md` — the authoritative spec for what belongs in each evidence package. Slice 2 implements the `backtest → paper` package fully; the `paper → micro_live` package stays specified but unimplemented (R-PRM-006).

### What's missing (the hole slice 2 closes)
1. **Governance-home mismatch.** ADR 0015 puts the state machine under `milodex.promotion`. Today it lives under `milodex.strategies.promotion` — a legacy of being written before the `promotion/` module existed. Moving it closes that seam and unblocks evidence code from importing cleanly (today `cli/commands/promote.py` imports from `milodex.strategies.promotion`, which crosses a module boundary that was supposed to point the other way).
2. **No auto-freeze on promote.** Operator runs `promotion freeze` separately from `promote`. This works — slice 1 verified it — but it's a two-command ceremony where the second step is easily forgotten. Slice 2 folds the freeze into the promote transaction.
3. **No evidence package.** `PromotionEvent` today carries three scalar metrics (Sharpe, drawdown, trade count). R-PRM-003 / PROMOTION_GOVERNANCE.md §"Evidence Package: backtest → paper" specifies ~9 fields including the manifest reference, run references, sensitivity results, and a written recommendation. Slice 2 captures the *machine-assemblable* subset as structured JSON in a new `evidence_json` column; the human-authored parts (recommendation, risks) come in via a `--notes`-like free-text flag.
4. **No `demote`.** `PROMOTION_GOVERNANCE.md` §"Demotion and Disablement" requires formal demotion as an explicit governance artifact. Today the only downgrade path is editing `stage:` in the YAML, which the drift check will refuse on the next cycle — a footgun, not a governance tool.
5. **No `promotion history`.** `list_promotions` exists on the event store; no CLI surfaces it. Operators cannot answer "why is this strategy at this stage?" from the CLI alone.

### The governance decision already made (2026-04-23 chat + ADR 0015)
- **Evidence package is machine-assembled where possible; human-authored where not.** The manifest hash, backtest-run references, metric snapshots, rejection counts, and kill-switch state snapshots are derived automatically from durable state. The written recommendation, known risks, and reason-for-reversal are passed in via CLI flags. Refuse promotion if any required flag is missing (R-PRM-008: "Missing fields shall cause the CLI promotion command to fail with a per-field pass/fail list.").
- **Auto-freeze is transactional with promotion.** A promote that fails gate checks writes *nothing* — no manifest row, no promotion row. A promote that succeeds writes both in the same `event_store._connect()` transaction, so partial state is impossible.
- **Demotion is always allowed; `demote` writes a reversal.** Unlike promote, demote does not run gate checks — operator intent is authoritative (the spec calls this out). Demote's `reverses_event_id` points at the most recent non-reversed promotion for that strategy. A demoted strategy can be re-promoted later, which writes a new (non-reversal) promotion event — the chain is preserved.
- **Live-stage transitions stay deferred.** Phase 1 does not permit `micro_live → live` per R-PRM-006. The state machine accepts `live` as a legal target in the type system but CLI refuses it with a clear error. Slice 3 implements the live-stage hook.

---

## 2. Architectural Decisions

### AD-1 — Move state-machine + gate-check to `promotion/state_machine.py`
The `strategies/promotion.py` module becomes a backward-compatibility shim that re-exports from the new location, then is removed in a follow-up cleanup. The move is a pure rename + import-path fix.

**Why.** ADR 0015 scopes promotion governance under `milodex.promotion`. Slice 1 established the module; slice 2 consolidates the logic. Leaving gate-check + validation in `strategies/` means `cli/commands/promote.py` and the new `promotion/evidence.py` both import *into* the strategies package for what is manifestly a governance concern. That's the wrong direction on the dependency graph.

**Shim lifetime:** removed in the same slice — there are two import sites (`cli/commands/promote.py`, `tests/milodex/strategies/test_promotion.py`), both updated in the same commit as the move. No external consumers.

### AD-2 — `promotions` table grows three columns via a forward-only migration
Migration `007_promotion_evidence.sql` adds:
- `manifest_id INTEGER REFERENCES strategy_manifests(id)` — FK to the manifest frozen as part of this promotion. Nullable for historical rows written before slice 2; new rows always populate it.
- `reverses_event_id INTEGER REFERENCES promotions(id)` — nullable. Populated only by `demote`. Reversal chains are reconstructable by walking this column (R-PRM-010).
- `evidence_json TEXT` — the full structured evidence package as JSON, stored verbatim. Nullable for historical rows.

Schema version bumps to 7.

**Why.** ADR 0011's event-store principle: append columns, never rewrite rows. The three new columns are nullable for backfill safety (old rows stay valid, readable, and indexable). Encoding the evidence package as a single JSON blob rather than a constellation of typed columns preserves schema flexibility — the governance spec will grow, and we don't want a migration per field-added.

### AD-3 — `EvidencePackage` is a typed dataclass that serializes to the JSON column
Shape:

```python
@dataclass(frozen=True)
class EvidencePackage:
    strategy_id: str
    from_stage: str
    to_stage: str
    manifest_hash: str
    backtest_run_id: str | None       # None for lifecycle-exempt or re-promote
    backtest_run_started_at: str | None
    paper_trade_count: int | None      # populated when to_stage in {micro_live, live}
    paper_rejection_count: int | None  # ditto
    kill_switch_trip_count: int | None # count of kill-switch trips in the eval window
    metrics_snapshot: dict[str, float | int | None]
        # sharpe_ratio, max_drawdown_pct, trade_count — snapshot as-of promote
    recommendation: str                # from --recommendation CLI flag (required)
    known_risks: list[str]             # from --risk flags (required, ≥1)
    promotion_type: str                # 'statistical' | 'lifecycle_exempt'
    gate_check_outcome: dict[str, Any] # per-gate pass/fail/waived
    assembled_at: str                  # ISO8601
```

**Why.** The `dict[str, Any]` would match the storage format but defeats type-checking inside the assembler. A dataclass with an `.as_dict()` method gives us mypy-friendly construction and a single `_dumps` point for the JSON column. The shape mirrors PROMOTION_GOVERNANCE.md's `backtest → paper` list item-by-item where machine-derivable; the items that require human judgment (`recommendation`, `known_risks`) are inputs, not outputs.

### AD-4 — Assembler lives in `promotion/evidence.py` as a pure function
```python
def assemble_evidence_package(
    *,
    strategy_id: str,
    from_stage: str,
    to_stage: str,
    manifest_hash: str,
    backtest_run_id: str | None,
    recommendation: str,
    known_risks: list[str],
    promotion_type: str,
    gate_check_outcome: dict[str, Any],
    event_store: EventStore,
    now: datetime | None = None,
) -> EvidencePackage: ...
```

The function reads paper-trade counts, rejection counts, and backtest metadata from the event store directly; the caller passes in user-supplied fields. No CLI coupling — assembler is unit-testable with a fixture event store.

**Why.** Keeps the `cli/commands/promote.py` handler thin — CLI's job is parsing + dispatch + formatting, not orchestration. Assembler testability matters because PROMOTION_GOVERNANCE.md is prose and the translation-to-code is opinionated; we want direct unit tests on each derived field.

### AD-5 — Promote transaction: freeze + append + update YAML, atomic at the event-store layer
Sequence inside a single `event_store._connect()` transaction:
1. Freeze the manifest at `to_stage` (inserts into `strategy_manifests`).
2. Insert the `PromotionEvent` row with `manifest_id` set to the just-inserted manifest id and `evidence_json` populated.
3. Commit.
4. *After* commit: update the YAML file's `stage:` line in-place (existing `_update_stage_in_config`).

Ordering rationale: the durable event is the source of truth. If step 4 fails (file permissions, disk full), we still have a coherent durable record — the next `promotion freeze` will re-freeze at the new stage declared in the (now-stale) YAML, and the drift check will surface the discrepancy on the next cycle. Putting the YAML edit *inside* the transaction would be wrong: a rollback can't undo an `fsync` on the config file, and we'd need to track filesystem state alongside DB state.

**Why not `BEGIN IMMEDIATE` + file-write inside?** Because a write to YAML is not reversible by the SQLite connection. The txn owns the DB, not the filesystem. The ordering above is the standard "durable log first, side effects after" pattern.

### AD-6 — Demote is a separate CLI command and a separate state-machine entry point
```python
def demote(
    *,
    strategy_id: str,
    to_stage: str,                        # 'paper' | 'disabled'
    reason: str,                          # required
    supporting_evidence_ref: str | None,  # linear ticket, incident ID, etc.
    approved_by: str,
    event_store: EventStore,
    config_path: Path,
    now: datetime | None = None,
) -> PromotionEvent: ...
```

Demote does **not** run `check_gate`. Demote **does** freeze the manifest at `to_stage` (so the drift check stays sensible after demotion) and writes a `PromotionEvent` with `promotion_type='demotion'`, `reverses_event_id` set to the most recent non-reversed promotion for this strategy at a stage ≥ `to_stage`.

**Why.** Per PROMOTION_GOVERNANCE.md §Demotion: "formal lifecycle-stage demotion must require explicit human confirmation and a governance artifact, the same way promotion does." Threading demote through `promote --to <lower>` would require relaxing `validate_stage_transition`, and the downgrade story deserves its own verb + required `--reason` flag anyway.

`disabled` is a legal demote target. The state machine accepts `disabled` as a "stage" for PromotionEvent purposes but never as a start-from stage (re-promotion from `disabled` requires re-running `backtest → paper`).

### AD-7 — CLI: one group, three verbs
Three commands under the existing `promotion` group (slice-1 already created it):
- `milodex promotion promote <strategy_id> --to <stage> --recommendation "..." --risk "..." [--risk "..."] [--run-id <uuid>] [--approved-by <name>] [--lifecycle-exempt] [--confirm]` — the new primary entry point. Auto-freezes, assembles evidence, writes promotion.
- `milodex promotion demote <strategy_id> --to {paper,disabled} --reason "..." [--evidence-ref "..."] [--approved-by <name>]` — always allowed, writes reversal.
- `milodex promotion history <strategy_id> [--limit N]` — read-only, prints stage transitions newest-first with manifest hashes + reversal chains.

The existing top-level `milodex promote` stays as a deprecated alias that forwards to `milodex promotion promote` and prints a single-line deprecation warning to stderr. **Removed in slice 3.**

**Why.** Slice 1 established `milodex promotion <verb>`. The existing `milodex promote` predates that and was written when `promotion/` didn't exist. Consolidating under one group matches the `milodex trade kill_switch <verb>` pattern and makes `promotion history` discoverable next to `promotion freeze`. Keeping `milodex promote` as an alias for one slice avoids breaking anyone's shell history mid-work.

### AD-8 — `--recommendation` and `--risk` are required, not defaulted
The CLI refuses promotion if `--recommendation` is absent or empty, and if no `--risk` is supplied. Per R-PRM-008: missing evidence fields must cause the command to fail with a per-field pass/fail list. The error message enumerates the missing fields.

**Why.** A defaulted "no risks identified" is the worst possible default — it silently satisfies a governance requirement that exists specifically to force the operator to *say* what they're worried about. Empty-string is not a risk. The spec is prescriptive; we follow.

### AD-9 — `promotion history` output includes reversal chain walk
Default output (human): one line per event, newest first, with a `↩` glyph marking reversals and an indented reference to the reversed event's id.

```
promotion_id  recorded_at         from_stage   to_stage     type              manifest
  42 (↩35)    2026-05-01 14:00Z   paper        backtest     demotion          d4f2e1...
  35          2026-04-23 10:12Z   backtest     paper        statistical       9a8b7c...
  18          2026-03-15 09:00Z   backtest     paper        statistical       6c5d4e...
```

JSON output carries the same tree structure.

**Why.** Walking the `reverses_event_id` chain is the point of the column — otherwise promotions and reversals read as independent events with no connective tissue.

---

## 3. File Inventory

Paths relative to `C:\Users\zdm80\Milodex`.

### Phase A — Schema + `PromotionEvent` extension

**Create:**
- `src/milodex/core/migrations/007_promotion_evidence.sql` — adds the three columns described in AD-2 to `promotions`, bumps `schema_version` to 7.

**Modify:**
- `src/milodex/core/event_store.py` — `PromotionEvent` grows `manifest_id: int | None`, `reverses_event_id: int | None`, `evidence_json: dict[str, Any] | None`. `append_promotion` inserts the three new columns. `list_promotions` / `_promotion_from_row` map them back. Add `get_promotion(promotion_id: int) -> PromotionEvent | None`.
- `tests/milodex/core/test_event_store.py` — update `schema_version == 7` expectation.

**Modify tests:**
- `tests/milodex/core/test_event_store.py` — round-trip test for the new columns.

### Phase B — Move state-machine to `promotion/`

**Create:**
- `src/milodex/promotion/state_machine.py` — copy of `strategies/promotion.py` minus the shim.
- `tests/milodex/promotion/test_state_machine.py` — copy of `tests/milodex/strategies/test_promotion.py` pointed at the new import path.

**Modify:**
- `src/milodex/promotion/__init__.py` — export `validate_stage_transition`, `check_gate`, `PromotionCheckResult`, `STAGE_ORDER`, `MIN_SHARPE`, `MAX_DRAWDOWN_PCT`, `MIN_TRADES`.
- `src/milodex/cli/commands/promote.py` — import from `milodex.promotion` (was `milodex.strategies.promotion`).

**Delete:**
- `src/milodex/strategies/promotion.py` — after import fix. All existing callers are inside the repo.
- `tests/milodex/strategies/test_promotion.py` — replaced by `tests/milodex/promotion/test_state_machine.py`.

### Phase C — Evidence package

**Create:**
- `src/milodex/promotion/evidence.py` — `EvidencePackage` dataclass + `assemble_evidence_package` function per AD-3, AD-4.
- `tests/milodex/promotion/test_evidence.py` — construction, JSON round-trip, paper-trade-count derivation, kill-switch-count derivation, missing-field rejection.

**Modify:**
- `src/milodex/promotion/__init__.py` — export `EvidencePackage`, `assemble_evidence_package`.

### Phase D — Transactional promote inside `promotion/state_machine.py`

**Modify:**
- `src/milodex/promotion/state_machine.py` — add `transition(...)` function that orchestrates AD-5's sequence: freeze + append-promotion + YAML-update. Takes an `EvidencePackage` as input; returns the written `PromotionEvent` with `id` populated.

**Modify:**
- `src/milodex/core/event_store.py` — add a `_connect_transaction()` context manager if one does not already exist, or document that `_connect()` already gives us a single-transaction handle that we can issue two INSERTs through. (Survey first — pick whichever matches existing conventions.)

**Create:**
- `tests/milodex/promotion/test_transition.py` — end-to-end: transition happy path writes both rows and updates YAML; gate-fail writes nothing; YAML-update-failure leaves durable state coherent (manifest + promotion present, YAML stale — drift check catches on next cycle per slice 1).

### Phase E — CLI `promotion promote` + deprecate top-level `promote`

**Modify:**
- `src/milodex/cli/commands/promotion.py` (slice 1's module) — add `promote` subcommand parser + handler. Handler is a thin wrapper: parse flags, call `promotion.state_machine.transition()`, format result.
- `src/milodex/cli/commands/promote.py` — shim: prints `DEPRECATION: 'milodex promote' will be removed in slice 3; use 'milodex promotion promote' instead.` to stderr, then forwards to the new handler. Functional for one slice.

**Create:**
- `tests/milodex/cli/test_promotion_promote.py` — happy path with all required flags, missing `--recommendation` refused with per-field message, missing `--risk` refused, gate-fail path, lifecycle-exempt path, `--json` output shape.

### Phase F — CLI `demote`

**Modify:**
- `src/milodex/cli/commands/promotion.py` — add `demote` subcommand parser + handler.
- `src/milodex/promotion/state_machine.py` — add `demote(...)` function per AD-6.

**Create:**
- `tests/milodex/cli/test_promotion_demote.py` — happy path to `paper`, happy path to `disabled`, missing `--reason` refused, reversal chain linking verified (the new row's `reverses_event_id` points at the correct prior row).

### Phase G — CLI `history`

**Modify:**
- `src/milodex/cli/commands/promotion.py` — add `history` subcommand.
- `src/milodex/core/event_store.py` — add `list_promotions_for_strategy(strategy_id, limit=None)` reader.

**Create:**
- `tests/milodex/cli/test_promotion_history.py` — human output format including `↩` glyph, JSON tree structure, empty case (strategy never promoted).

### Phase H — Docs

**Modify:**
- `docs/adr/0015-strategy-identifier-and-frozen-manifest.md` — status flip to "Implemented (runtime drift check, freeze CLI, state machine, evidence package) — live-stage hook pending slice 3."
- `docs/OPERATIONS.md` — new subsection "Promotion ceremony" describing the `milodex promotion promote` / `demote` / `history` workflow.
- `docs/PROMOTION_GOVERNANCE.md` — annotate which §"backtest → paper" evidence items are now machine-assembled vs. human-authored.
- `docs/ROADMAP_PHASE1.md` §6.1.2 — check off state machine + evidence package; §6.1.3 remains unchecked (slice 3).

---

## 4. Commit Sequence

Small commits, each self-contained and test-green. TDD pattern. Every commit must run `pytest` (full suite) + `ruff check` + `ruff format --check` green on touched files before proceeding.

### Commit 1 — `feat(core): add manifest_id, reverses_event_id, evidence_json to promotions`
- Migration 007, `PromotionEvent` field additions, reader/writer updates, `get_promotion` helper.
- Green: `pytest tests/milodex/core/test_event_store.py`.

### Commit 2 — `refactor(promotion): move state-machine from strategies/ to promotion/`
- Copy file, update imports at two call sites, delete old file + old test module, re-run suite.
- Green: full suite. No behavior change.

### Commit 3 — `feat(promotion): add EvidencePackage and assemble_evidence_package`
- New evidence module + tests. No wiring yet — pure additive.
- Green: `pytest tests/milodex/promotion/test_evidence.py` + full suite.

### Commit 4 — `feat(promotion): transactional transition (freeze + append + YAML update)`
- `transition()` function + tests. CLI still uses old path; this commit just adds the new entry point.
- Green: full suite.

### Commit 5 — `feat(cli): add 'milodex promotion promote' and deprecate top-level 'milodex promote'`
- New CLI handler calls `transition()`. Top-level `promote` becomes a forwarding shim.
- Green: full suite, including updated `test_promote.py` covering the deprecation warning path.
- **⚠ Operationally:** the next `milodex promote` invocation now prints a deprecation banner but still works. No runtime coupling — this is a dev-ergonomics commit.

### Commit 6 — `feat(cli): add 'milodex promotion demote' with reversal chain`
- `demote()` state-machine function + CLI handler + tests.
- Green: full suite.

### Commit 7 — `feat(cli): add 'milodex promotion history' with reversal-aware rendering`
- History CLI + `list_promotions_for_strategy` event-store reader + tests.
- Green: full suite.

### Commit 8 — `docs(adr,ops,governance,roadmap): close ADR 0015 slice-2 half`
- ADR status flip, OPERATIONS "Promotion ceremony" section, PROMOTION_GOVERNANCE annotations, ROADMAP §6.1.2 check-offs.

---

## 5. Manual Integration Verification

End-to-end using the lifecycle-exempt regime strategy (it's already at `paper` from slice 1; demote it to `backtest`, re-promote with evidence, then demote to `disabled`).

```bash
# 1. Starting state: strategy at paper, slice-1 manifest frozen.
./.venv/Scripts/python.exe -m milodex.cli.main promotion history \
  regime.daily.sma200_rotation.spy_shy.v1
# Expected: one pre-slice-2 promotion row (if any) + the slice-1 freeze; zero reversals.

# 2. Demote to backtest with a reason.
./.venv/Scripts/python.exe -m milodex.cli.main promotion demote \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --to backtest \
  --reason "slice-2 integration test — restaging for re-promotion"
# Expected: demotion row written; reverses_event_id points at the slice-1 promotion (or null if there wasn't one).

# 3. Confirm history reflects the reversal.
./.venv/Scripts/python.exe -m milodex.cli.main promotion history \
  regime.daily.sma200_rotation.spy_shy.v1
# Expected: demotion event with ↩ glyph pointing at the prior promotion.

# 4. Re-promote to paper with full evidence fields.
./.venv/Scripts/python.exe -m milodex.cli.main promotion promote \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --to paper \
  --lifecycle-exempt \
  --recommendation "Regime strategy re-validated after slice-2 integration verification." \
  --risk "Lifecycle-exempt: statistical thresholds not applied; operational correctness only." \
  --risk "Drift check relies on correct freeze at promote time."
# Expected: promotion row written, manifest_id set, evidence_json populated, YAML stage back to "paper".

# 5. Confirm the manifest was auto-frozen by checking the drift check does NOT block the next cycle.
./.venv/Scripts/python.exe -m milodex.cli.main promotion manifest \
  regime.daily.sma200_rotation.spy_shy.v1
# Expected: active manifest present, hash matches the current YAML.

# 6. Verify missing-evidence refusal.
./.venv/Scripts/python.exe -m milodex.cli.main promotion promote \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --to micro_live \
  --lifecycle-exempt
# Expected: non-zero exit, stderr lists the missing required fields (--recommendation, --risk).

# 7. Verify that 'milodex promote' still works with a deprecation notice.
./.venv/Scripts/python.exe -m milodex.cli.main promote \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --to paper --lifecycle-exempt --confirm
# Expected: stderr contains "DEPRECATION"; stdout contains the normal promotion output OR the already-at-stage error.
```

### Lint + format

```bash
./.venv/Scripts/python.exe -m ruff check src/milodex/ tests/milodex/
./.venv/Scripts/python.exe -m ruff format --check src/milodex/ tests/milodex/
```

### Production-DB guard

`_guard_real_event_store_untouched` in `tests/conftest.py` remains authoritative — it will fail if any refactor accidentally writes to `data/milodex.db` during the test suite.

---

## 6. Decisions Locked

Questions that were open during drafting, now resolved (2026-04-23 chat):

1. **`evidence_json` as one blob, not many columns.** Per AD-2. Rationale: PROMOTION_GOVERNANCE.md will grow; schema churn per field is unacceptable. The governance contract is the JSON shape, versioned inside the blob (`schema_version` key inside `evidence_json`).

2. **`--recommendation` and `--risk` required at the CLI.** Per AD-8. Refuse early rather than let the evidence package carry empty strings.

3. **Transactional ordering: durable log first, YAML file after.** Per AD-5. File edit outside the DB transaction is the correct direction — the drift check will catch the stale-YAML case on the next cycle.

4. **`promote` alias stays one slice; removed in slice 3.** Per AD-7. Single-slice deprecation window avoids a permanent alias we'd then need to maintain.

5. **`disabled` is a legal demote target but not a legal start-from stage.** Re-enabling a `disabled` strategy requires re-running `backtest → paper`. A `disabled → paper` shortcut would defeat the whole point of the state machine.

6. **Paper-trade / rejection / kill-switch counts derived from event-store queries, not passed as CLI flags.** Per AD-4. The whole point is that operators *shouldn't* be able to fudge these; they're facts of the durable record.

---

## 6a. Deferred to Slice 3

Intentionally **not** in this slice:

- **Live-stage refusal hook.** `micro_live → live` transitions hit a hard-stop code path independent of the state machine. Slice 3 adds that + the kill-switch clean-count gate.
- **Removal of top-level `milodex promote` alias.** Slice 3's cleanup commit.
- **`paper → micro_live` evidence package assembly.** Specified in PROMOTION_GOVERNANCE.md but Phase 1 does not enable micro_live (R-PRM-006). Slice 2's assembler accepts `to_stage='micro_live'` in the type system but the CLI will refuse it with a clear "Phase 1 does not permit micro_live transitions" error.
- **Experiment registry.** PROMOTION_GOVERNANCE.md §Experiment Registry is out-of-scope for Phase 1.4 entirely; it belongs to a later research-tooling phase.

---

## 7. Non-Goals

- **No risk-layer changes.** Slice 1's `_check_manifest_drift` is the runtime enforcement; slice 2 just makes freezing easier.
- **No execution-service changes.** Service already wires both hashes per slice 1.
- **No strategy-interface changes.** The `Strategy.evaluate()` surface is untouched.
- **No new event types on the event store beyond the three `promotions` columns and `StrategyManifestEvent` additions from slice 1.**
- **No broker or data-provider changes.**
- **No UI / dashboard changes.** CLI is the governance surface.
- **No `experiment_registry` table.**
- **No retroactive backfill.** Historical `promotions` rows stay with null `manifest_id` / `evidence_json`; readers tolerate nulls.
- **No demote gate-check.** Per AD-6, operator intent is authoritative; demotion never blocks.

---

## 8. Verification Before Completion

Task is done when:
- `pytest tests/milodex/` green. This plan adds ~25–30 new tests (migration round-trip, evidence assembly, transaction coherence, demote chain, history rendering, each CLI's happy + failure paths).
- `ruff check` + `ruff format --check` clean across files touched by this slice. (Pre-existing master drift outside touched files is not slice-2's to fix.)
- Manual §5 integration sequence passes end-to-end: demote → history shows reversal → re-promote with evidence → history shows new promotion → missing-evidence refusal works → deprecation alias works.
- `git log --oneline -8` shows the eight-commit sequence from §4.
- Production `data/milodex.db` mtime shifts *only* for the intentional §5 manual-verification writes (test suite must not touch it).
- `docs/ROADMAP_PHASE1.md` §6.1.2 checkboxes flipped; §6.1.3 remains unchecked (slice 3).
- `docs/adr/0015-*.md` status reflects "implemented (runtime drift check, freeze CLI, state machine, evidence package) — live-stage hook pending slice 3."
- `src/milodex/strategies/promotion.py` no longer exists (moved in commit 2).
- `milodex promote` prints a deprecation notice but still forwards correctly.

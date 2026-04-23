# Phase 1.4 Slice 1 — Frozen Manifest + Runtime Drift Check

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first of three Phase 1.4 slices — freezing a strategy's config at each promoted stage and refusing runtime execution when the live YAML drifts from the frozen version. Closes R-STR-011..014 and ADR 0015's implementation half. Does **not** introduce the promotion state machine, the `milodex promote` CLI, or evidence-package assembly — those are slices 2 and 3.

**Architecture:** One new module (`src/milodex/promotion/`), one new event-store table (`strategy_manifests`), one new risk-layer check (`manifest_drift`), one new CLI subcommand (`milodex promotion freeze`). No changes to the existing risk checks, no changes to the strategy interface, no changes to the execution service's public methods.

**Tech Stack:** Python 3.11+, pytest, existing SQLite event store, existing `milodex.strategies.loader.compute_config_hash`.

---

## 1. Context

### Why this slice first
Phase 1.4 decomposes into frozen manifest → state machine + CLI → live-stage lock. The roadmap's §8 ordering puts manifest first because:

1. **Self-contained.** One module drop, one schema migration, one risk check. No refactor of existing code paths.
2. **Closes a concrete hole today.** Without it, an operator can edit `configs/*.yaml` after a strategy is de-facto promoted and trades keep flowing under the edited rules with no audit trail. This is the exact loophole ADR 0015 was written to close.
3. **Unblocks slice 2.** The state machine needs manifest hashes as evidence (`evidence_package` per R-PRM-003 includes the frozen config reference). Landing manifest first means no refactor when the state machine arrives.

### What's already on master
- `src/milodex/strategies/loader.py:267` — `compute_config_hash(path: Path) -> str` returns SHA-256 over canonicalized YAML. The hash function exists; it just isn't anchored to anything.
- `src/milodex/core/event_store.py` — `PromotionEvent` + `promotions` table already live in the event store. Landed with the §4.1.1 migration sequence. Manifest freezing will add a sibling `strategy_manifests` table.
- `src/milodex/risk/evaluator.py` — `EvaluationContext` is a frozen dataclass with `strategy_config: StrategyExecutionConfig | None`. Every existing risk check is a `_check_*` method on `RiskEvaluator`. No event-store coupling in the risk module — we want to preserve that.
- `src/milodex/execution/service.py` — already computes `config_hash` inside `_record_execution`. The same computation lives in `ExecutionService._evaluate` before risk evaluation — that's the insertion point.
- `docs/adr/0015-strategy-identifier-and-frozen-manifest.md` — sets the contract. This plan implements it.

### The one governance decision already made (2026-04-23 chat)
**No-manifest behavior is refuse, not warn+allow.** A strategy at `paper+` stage with no frozen manifest blocks. The check is scoped to `paper`, `micro_live`, `live` — `backtest`-stage strategies are exempt because backtest has no meaningful "promoted" state. No bootstrap automation: the operator runs `milodex promotion freeze <strategy_id>` once per currently-running strategy. There is exactly one such strategy today (`regime.daily.sma200_rotation.spy_shy.v1`), so the migration cost is "run one command once."

Rationale from the chat: failure modes are asymmetric. Refuse fails loud and safe (next cycle halts with a clear error, operator freezes, unblocked). Warn-and-allow fails silent and unsafe (warning becomes noise, operator edits YAML, trades continue under an assumption that was never enforced). Matches kill-switch-manual-reset philosophy: halt by default, require explicit human action.

---

## 2. Architectural Decisions

### AD-1 — `strategy_manifests` is a sibling table, not a column on `promotions`
A separate table keyed on `(strategy_id, stage)` with one row per freeze event. Rows are append-only; the "active" manifest at a given stage is the most recent row for that `(strategy_id, stage)` pair.

**Why.** A manifest isn't a promotion event — it's a *snapshot consumed by* a promotion event. Slice 2's promotion-command will insert a `strategy_manifests` row AND a `promotions` row in the same transaction, linking the promotion to the manifest by `manifest_id`. Co-locating them on one table would force a schema churn at slice-2 time. A separate table also means re-freezes during development (slice 2 will offer a `--bump-manifest` flow) don't pollute the promotion log.

### AD-2 — The frozen hash, not the frozen YAML, is what the risk check compares
The row stores both the canonical hash and the full config JSON (so the evidence package in slice 2 can reproduce the exact config). But the runtime check compares hashes only — it does not re-parse or re-canonicalize YAML at evaluation time.

**Why.** Risk checks fire on every cycle; YAML parsing there would be wasteful. The hash function is already deterministic (`strategies/loader.compute_config_hash`), so hash-equality is a sufficient proxy for config-equality. Storing the full JSON is for *human* audit and for slice 2's evidence package, not for the runtime check.

### AD-3 — Risk module stays event-store-free; the service passes hashes in
`EvaluationContext` grows two optional fields: `runtime_config_hash: str | None` and `frozen_manifest_hash: str | None`. `ExecutionService._evaluate` looks up the frozen manifest for the strategy's current stage (via a new `get_active_manifest` event-store helper) and populates both fields before calling `risk_evaluator.evaluate`. The risk evaluator just compares two strings.

**Why.** The risk module today has no event-store import. Introducing one would tangle the dependency graph — risk would depend on core, and core's promotion surface would become risk-coupled. Keeping risk as a pure function of its `EvaluationContext` preserves the seam that makes `NullRiskEvaluator` (used in backtests) a trivial drop-in.

### AD-4 — The check is `_check_manifest_drift`, scoped to paper+ stages, skipped for manual trades
The new check:
- Returns `passed=True` if `strategy_config` is None (manual operator trade — no strategy, no manifest to compare against).
- Returns `passed=True` if `strategy_config.stage == "backtest"` (development/research work is exempt).
- Returns `passed=False` with reason `no_frozen_manifest` if `frozen_manifest_hash is None` at paper+ stage.
- Returns `passed=False` with reason `manifest_drift` if `runtime_config_hash != frozen_manifest_hash`.
- Returns `passed=True` otherwise.

**Why.** Scoping to paper+ mirrors ADR 0015's intent — "frozen at promotion." Backtest has no promotion, so no freeze. Manual trades are not strategy-governed, so no manifest. The two block-cases give distinguishable reason codes so the operator knows whether to run `freeze` for the first time (no_frozen_manifest) or investigate YAML drift (manifest_drift).

### AD-5 — Freezing is idempotent at the hash level but always-append at the row level
`freeze_manifest(strategy_id, stage)` writes a new row even if the hash hasn't changed from the last freeze at that stage. The row carries `frozen_at`, so the audit trail shows every freeze event; the "active" manifest is the latest row.

**Why.** Idempotent-by-hash would require read-then-write logic that adds complexity for no real benefit — the audit trail value of "operator ran freeze at 11:32 and it was a no-op because nothing changed" is real. Duplicate rows are cheap.

### AD-6 — No auto-freeze, no bootstrap migration
The operator runs `milodex promotion freeze <strategy_id>` once per existing paper+ strategy. There is one today. Future strategies will be frozen as part of slice 2's `milodex promote` command.

**Why.** Auto-freeze hides the action. The whole point of this slice is to make the "freeze" act explicit and recorded. Baking it into a migration would paper over the ceremony we just decided (in the 2026-04-23 chat) to make explicit. Operator inconvenience is minimal: one strategy, one command.

### AD-7 — CLI surface for this slice is minimal — just `freeze` + `manifest show`
Two subcommands under `milodex promotion`:
- `milodex promotion freeze <strategy_id>` — freezes the current YAML at the strategy's current stage. Errors if the strategy is at `backtest` stage (nothing to freeze).
- `milodex promotion manifest <strategy_id>` — read-only, prints the active manifest's hash + `frozen_at` + stage.

**Why.** No `milodex promote`, no `milodex demote`, no `milodex promotion history` in this slice — those arrive with the state machine. Just enough CLI surface to test the flow end-to-end and run the one-shot freeze for the currently-running strategy. `history` waits for slice 2 because it needs the state-transition story to be meaningful.

### AD-8 — Stage is read from the strategy config, not passed as a CLI arg
`milodex promotion freeze <strategy_id>` takes no `--stage` argument — it reads the strategy's `stage` from its YAML config. Slice 2's `milodex promote --to <stage>` will be the operation that *changes* a strategy's stage; this slice's `freeze` is purely a snapshot-at-current-stage operation.

**Why.** Two arguments for the same conceptual input (the stage lives in the YAML, and the CLI asks for it again) invites drift. The whole drift story is exactly what this slice is trying to prevent.

---

## 3. File Inventory

Paths relative to `C:\Users\zdm80\Milodex`.

### Phase A — Event-store schema + `StrategyManifestEvent`

**Modify:**
- `src/milodex/core/event_store.py` — add `StrategyManifestEvent` dataclass, schema migration adding the `strategy_manifests` table, `append_manifest` writer, `get_active_manifest_for_strategy(strategy_id, stage)` reader.

**Create:**
- `tests/milodex/core/test_event_store_manifests.py` — writer round-trip, latest-wins, separate-stages-don't-collide.

### Phase B — `promotion/` module

**Create:**
- `src/milodex/promotion/__init__.py` — exports `freeze_manifest`, `get_active_manifest_hash`.
- `src/milodex/promotion/manifest.py` — `freeze_manifest(strategy_id, loader, event_store) -> StrategyManifestEvent` and `get_active_manifest_hash(strategy_id, stage, event_store) -> str | None`.
- `tests/milodex/promotion/__init__.py` (empty).
- `tests/milodex/promotion/test_manifest.py` — freeze round-trip, stage read from config, backtest-stage rejection, hash matches `compute_config_hash`.

### Phase C — Risk layer check

**Modify:**
- `src/milodex/risk/evaluator.py` — add `runtime_config_hash` and `frozen_manifest_hash` to `EvaluationContext`, add `_check_manifest_drift` method, register it in the checks list.
- `src/milodex/risk/models.py` — no change (existing `RiskCheckResult` shape is sufficient).

**Modify tests:**
- `tests/milodex/risk/test_evaluator.py` — new cases: paper-stage with no manifest blocks, paper-stage with matching hash passes, paper-stage with drifted hash blocks, backtest-stage always passes regardless, manual trade (no strategy_config) passes.

### Phase D — Service plumbs the hashes in

**Modify:**
- `src/milodex/execution/service.py` — in `_evaluate`, before constructing the `EvaluationContext`, compute `runtime_config_hash` via `compute_config_hash(strategy_config_path)` and look up `frozen_manifest_hash` via `get_active_manifest_hash(strategy_name, stage, event_store)`. Pass both into `EvaluationContext`.

**Modify tests:**
- `tests/milodex/execution/test_service.py` — existing paper-submit tests will now need a frozen manifest seeded, OR they need to be at `backtest` stage, OR the test harness needs a freeze helper. See Phase E.

### Phase E — Test harness helper

**Create:**
- `tests/milodex/_helpers/promotion.py` — a `seed_frozen_manifest(event_store, strategy_id, stage, config_path)` fixture helper. Avoids each test re-implementing freeze plumbing. Used by `test_service.py`, `test_service_reasoning.py`, `test_service_backtest.py` (where they submit paper-stage trades).

**Alternative:** wire a pytest fixture. Either works; helper function is simpler and doesn't force fixture parameterization on unrelated tests.

### Phase F — CLI

**Modify:**
- `src/milodex/cli/main.py` — register a `promotion` subparser group.

**Create:**
- `src/milodex/cli/promotion.py` — `freeze_command` + `manifest_show_command` handlers. Thin wrappers over `promotion.manifest` functions with human/JSON output per the existing `--json` formatter abstraction (ADR 0014).
- `tests/milodex/cli/test_promotion.py` — `freeze` happy path, `freeze` on backtest-stage strategy exits with a clear error, `manifest` on unfrozen strategy prints "no active manifest."

### Phase G — Docs

**Modify:**
- `docs/adr/0015-strategy-identifier-and-frozen-manifest.md` — flip status from "accepted" (implementation pending) to "implemented"; link this plan's tail commit.
- `docs/OPERATIONS.md` — new paragraph under the audit-trail section documenting the `manifest_drift` / `no_frozen_manifest` reason codes and the `milodex promotion freeze` one-shot for operators onboarding a new strategy.
- `docs/ROADMAP_PHASE1.md` — check off §6.1.1 ("Frozen Manifest") and note that §6.1.2 (state machine) + §6.1.3 (CLI) remain for slice 2.

---

## 4. Commit Sequence

Small commits, each self-contained and test-green. TDD pattern. Every commit must run `pytest` (full suite) + `ruff check` + `ruff format --check` green on touched files before proceeding.

### Commit 1 — `feat(core): add strategy_manifests table and StrategyManifestEvent`
- Schema migration + dataclass + writer + reader.
- Green: `pytest tests/milodex/core/test_event_store_manifests.py`.

### Commit 2 — `feat(promotion): introduce freeze_manifest and get_active_manifest_hash`
- New `milodex.promotion` module. Depends on commit 1.
- Green: `pytest tests/milodex/promotion/`.

### Commit 3 — `feat(risk): add manifest_drift check scoped to paper+ stages`
- `EvaluationContext` grows the two hash fields. New `_check_manifest_drift` method. Existing risk tests stay green because the new fields default to `None` and the check skips when either is `None`.
- Green: full suite.

### Commit 4 — `feat(execution): wire runtime and frozen manifest hashes into risk context`
- Service looks up both hashes and passes them in. This is the commit where existing paper-submit tests go red — they'll need the seed-frozen-manifest helper from Phase E. Includes the test helper and the updated tests.
- Green: full suite.
- **⚠ Operational coupling with the running strategy:** the moment this commit lands, the next paper cycle of `regime.daily.sma200_rotation.spy_shy.v1` will halt with reason_code=`no_frozen_manifest` until the operator runs commit-5's `milodex promotion freeze` CLI. That halt is the feature — it's the governance ceremony we chose over warn+allow in the 2026-04-23 chat. But it means **commits 4 and 5 should be treated as a coupled pair in the operator's timeline**: land both before the next paper cycle, or pause the strategy before landing commit 4. Do **not** land commit 4 in the middle of a paper-trading day. The plan's TDD sequence keeps the test suite green between them; real-world-wise, commit 5 unblocks the runtime that commit 4 locks down.

### Commit 5 — `feat(cli): add 'milodex promotion freeze' and 'milodex promotion manifest' subcommands`
- Two new CLI handlers + registration + tests.
- Green: full suite.

### Commit 6 — `docs(adr,ops,roadmap): close ADR 0015; document manifest contract`
- ADR status flip, OPERATIONS paragraph, ROADMAP check-off.

---

## 5. Manual Integration Verification

End-to-end: freeze the running regime strategy, drift its YAML, confirm the risk layer blocks.

```bash
# 1. Confirm starting state — no frozen manifests exist.
./.venv/Scripts/python.exe -m milodex.cli.main promotion manifest \
  regime.daily.sma200_rotation.spy_shy.v1
# Expected: "No active manifest for this strategy."

# 2. Freeze the current config at the strategy's current stage (paper).
./.venv/Scripts/python.exe -m milodex.cli.main promotion freeze \
  regime.daily.sma200_rotation.spy_shy.v1
# Expected: "Frozen manifest <sha256-prefix> at stage 'paper' for regime.daily.sma200_rotation.spy_shy.v1"

# 3. Confirm the manifest is now visible.
./.venv/Scripts/python.exe -m milodex.cli.main promotion manifest \
  regime.daily.sma200_rotation.spy_shy.v1
# Expected: hash + frozen_at + stage=paper

# 4. Run a paper cycle — must pass risk evaluation.
./.venv/Scripts/python.exe -m milodex.cli.main strategy run \
  regime.daily.sma200_rotation.spy_shy.v1 --cycles 1
# Expected: normal no-action or submit, no manifest_drift reason code.

# 5. Drift the YAML — change ma_period from 200 to 201 in the config.
#    (Back it up first.)

# 6. Run another paper cycle.
./.venv/Scripts/python.exe -m milodex.cli.main strategy run \
  regime.daily.sma200_rotation.spy_shy.v1 --cycles 1
# Expected: risk decision blocked with reason_code='manifest_drift'.

# 7. Restore the YAML and verify cycle 7 passes again.
```

### Lint + format

```bash
ruff check src/milodex/ tests/milodex/
ruff format --check src/milodex/ tests/milodex/
```

### Production-DB guard

`_guard_real_event_store_untouched` in `tests/conftest.py` remains authoritative — it will fail if any refactor accidentally writes to `data/milodex.db`.

---

## 6. Decisions Locked

Questions that were open during drafting, now resolved (2026-04-23 chat):

1. **`config_json` content → canonicalized form.** Store the same representation that `compute_config_hash` feeds into SHA-256. "What you hashed is what you stored" is the compliance-critical invariant. Human-readable reconstruction (comments, field order) can live in slice 2's evidence package — that's the right surface for operator audit, not this row.

2. **Hash scope → strategy YAML only.** Does not include `risk_defaults.yaml` or universe config. Edits to `risk_defaults` don't force a re-freeze across every strategy. Risk-default drift is out of scope for this slice; revisit if it becomes a real concern.

3. **Re-freeze behavior → always append (AD-5 as written).** No `--force`, no "hash unchanged, skipping" path. The audit-trail value of every explicit freeze event is real; duplicate rows are cheap. Keeps the CLI's mental model one-line-simple.

4. **Stage check scope → fires on `paper`, `micro_live`, `live` only.** Everything else — `backtest`, `disabled`, unrecognized values — passes the check. The allow-list is closed; the deny-list is open.

5. **Test helper location → `tests/milodex/_helpers/promotion.py` as a plain function.** Used across at least three test modules (`test_service.py`, `test_service_reasoning.py`, `test_service_backtest.py`); a shared function is simpler than parameterizing a fixture on every consumer. Establishes a `_helpers` convention for future cross-module test utilities.

---

## 6a. Deferred to Slice 2

Intentionally **not** in this slice per 2026-04-23 chat:

- **`milodex promote` / `milodex demote` / `milodex promotion history`.** These need the state-machine's transition validation and evidence-package assembly. Pulling them forward would double the scope and couple manifest-landing to state-machine-landing. This slice ships just enough CLI (`freeze` + `manifest` show) to prove the risk-layer seam works; slice 2 layers the transition logic on top.

---

## 7. Non-Goals

- **No `milodex promote` command.** That's slice 2. This slice's CLI is just `freeze` + `manifest` (show).
- **No state machine.** `backtest → paper → micro_live → live` transition logic is slice 2.
- **No evidence-package assembly.** R-PRM-003's bundle is slice 2.
- **No live-stage refusal hook.** That's slice 3.
- **No retroactive manifest backfill.** The operator runs `freeze` once per existing paper+ strategy. There is one.
- **No `demote` command.** Slice 2.
- **No changes to `risk_defaults.yaml` drift.** Only strategy YAML drift is covered here (see open question 2).
- **No change to `Strategy.evaluate()` or any sacred-surface interface.** This slice is entirely below the strategy boundary.
- **No broker-side or data-provider changes.**

---

## 8. Verification Before Completion

Task is done when:
- `pytest tests/milodex/` green. This plan adds ~20–25 new tests (event-store round-trip, freeze/read, risk check matrix, CLI handlers, drift integration).
- `ruff check` + `ruff format --check` clean across `src/milodex/` and `tests/milodex/`.
- Manual §5 integration sequence passes: freeze → cycle ok → drift YAML → cycle blocked with `manifest_drift` → restore → cycle ok.
- `git log --oneline -6` shows the six-commit sequence from §4.
- Production `data/milodex.db` mtime unchanged across development.
- `docs/ROADMAP_PHASE1.md` §6.1.1 checkboxes flipped; §6.1.2 and §6.1.3 remain unchecked (slice 2's work).
- `docs/adr/0015-*.md` status reflects "implemented (runtime drift check, freeze CLI) — state machine pending slice 2."

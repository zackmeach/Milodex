# Phase 1.4 Slice 3 — Live-Stage Refusal Hook

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last open item from ADR 0015 by refusing `--to live` and `--to micro_live` at the promotion state machine during Phase 1 (R-PRM-006), with a clean CLI-visible error that cites ADR 0004. This completes Phase 1.4 governance: the R-EXE-007 *runtime* refusal at the risk layer stays as defense-in-depth, and the new *promotion-time* refusal closes the window where an operator could freeze a `live` manifest + write a `stage: "live"` YAML even though no live trade could ever actually submit.

**Architecture:** One constant (`PHASE_ONE_BLOCKED_STAGES = frozenset({"micro_live", "live"})`) + three added lines in `validate_stage_transition`. Because every promotion path — `promotion promote` (slice 2), the legacy `milodex promote` shim, the in-process `transition()` function — routes through that single validator, one change covers them all. Tests get pruned where they exercise phase-2 behavior and new refusal tests take their place. Two commits: one code+tests, one docs.

**Tech Stack:** Python 3.11+, pytest, existing `milodex.promotion.state_machine`.

---

## 1. Context

### What slices 1 and 2 landed
- **Slice 1** (2026-04-23, `phase-1-4-slice-1-frozen-manifest.md`): frozen manifest + runtime drift check + `milodex promotion freeze / manifest`.
- **Slice 2** (2026-04-23, `phase-1-4-slice-2-state-machine-evidence.md`): governed state machine under `milodex.promotion.state_machine`, transactional `transition()` that auto-freezes on promote, `EvidencePackage` with R-PRM-008 refusal on missing recommendation/risk, `milodex promotion promote / demote / history` CLIs, reversal chain via `reverses_event_id`. **429 tests passing.**

### The one hole left open
ADR 0015 status currently reads: *"Implemented (runtime drift check, freeze CLI, state machine, evidence package) — live-stage refusal hook pending slice 3."* The hole is concrete:

Today, `milodex promotion promote regime.daily.sma200_rotation.spy_shy.v1 --to live --confirm --lifecycle-exempt --recommendation "..." --risk "..."` will **succeed** — it writes a `strategy_manifests` row for `stage=live`, writes a `promotions` row with `to_stage=live`, and flips the YAML to `stage: "live"`. The R-EXE-007 runtime check at the risk layer would then refuse every actual trade, but by that point the ledger carries a governance decision that contradicts ADR 0004.

Same gap applies to `--to micro_live`. R-PRM-006 is unambiguous: *"Phase 1 shall not enable `micro_live` or `live` stages end-to-end."*

### The decisions already made (2026-04-23 conversation)

1. **Block both `micro_live` and `live`**, not just `live`. R-PRM-006 names both. Blocking only `live` leaves a window where someone promotes to `micro_live`, writes the ledger + freezes the manifest, and only hits the R-EXE-007 runtime stop later.
2. **Hardcoded constant, not config.** ADR 0004 says lifting the phase-one live-lock *"is a deliberate future ADR, not a silent config change."* A source-level `PHASE_ONE_BLOCKED_STAGES` that Phase 2's lifting ADR explicitly removes matches that principle.
3. **Refusal lives in `validate_stage_transition`, not the CLI handler.** One source of truth; all callers (new `promotion promote`, legacy `milodex promote`, `transition()`) covered automatically. Keeps the CLI handler thin.
4. **Error message cites the ADR + requirement.** A terse "blocked" is not enough — the operator needs to know *why* and *when it might lift*. Exact wording locked in AD-2 below.

### Ripple on existing tests (surveyed 2026-04-23)

The refusal breaks assertions in three existing files. All are counted into this plan's scope:

- **`tests/milodex/promotion/test_state_machine.py`** — the `test_valid_stage_transitions` parametrize list includes `("paper", "micro_live")` and `("micro_live", "live")`. These cases assert the transitions *succeed*. They must be removed from the "valid" list and moved into a new "refused in phase 1" test.
- **`tests/milodex/cli/test_promote.py`** — three tests exercise the legacy `milodex promote` path against `micro_live` and `live`:
  - `test_promote_sequential_promotions` (lines 256–272): promotes through `paper → micro_live`. Rewrite to stop at `paper` + assert that the `micro_live` step is refused.
  - `test_promote_to_live_requires_confirm` (lines 280–291): asserts `--confirm` is demanded. Now `--to live` is refused *before* the confirm check — rewrite to assert the phase-1 refusal.
  - `test_promote_to_live_with_confirm_succeeds` (lines 294–310): asserts a live promotion with `--confirm` writes a ledger row. Delete — the behavior it asserts is now forbidden.
- **All other `micro_live` / `"live"` usages** (surveyed; no further ripple):
  - `tests/milodex/execution/test_service.py` uses `stage: "live"` for R-EXE-007 runtime-refusal tests — doesn't touch the promotion validator. **No change.**
  - `tests/milodex/core/test_event_store_manifests.py` and `tests/milodex/promotion/test_manifest.py` freeze manifests at `micro_live` directly via the event store — bypasses `validate_stage_transition`. **No change** (a future ADR lifting the lock will let those paths reach `micro_live` normally again).
  - `tests/milodex/promotion/test_evidence.py` calls `assemble_evidence_package(to_stage="micro_live")` — the assembler is pure data-shape, doesn't validate transitions. **No change.**

---

## 2. Architectural Decisions

### AD-1 — Refusal lives in `validate_stage_transition`, not per-caller
Adding the check inside the existing validator means the four existing call sites (legacy `cli/commands/promote.py`, slice-2 `cli/commands/promotion.py::_promote`, slice-2 `promotion/state_machine.py::transition`, and future callers) all get the refusal with zero new code. The alternative — duplicating a check in each handler — invites drift.

**Ordering inside the validator.** The new check runs *after* the existing legality checks (unknown stage, same stage, downgrade, skip) so that a transition like `backtest → live` reports "Skipping stages" first (the structural error), rather than "blocked in Phase 1" (the policy error). The structural errors are closer to typos; the policy error is the substantive one. Reporting the mechanical error first lets operators correct the typo without having to first parse the policy message.

### AD-2 — Error message
Exact string (locked for test match):

```
Promotion to '<stage>' is blocked during Phase 1 (ADR 0004, R-PRM-006).
Paper-only period — the live-stage lock lifts via a future ADR, not a config edit.
```

**Why this wording.** Three pieces: (1) *what* is blocked (the specific stage), (2) *the authoritative source* (ADR 0004 + the SRS requirement), (3) *when it might change* (a future ADR, not a config toggle — matches ADR 0004's "deliberate future ADR, not a silent config change" clause verbatim in spirit). An operator reading this in stderr can immediately tell whether they can unblock themselves (no) and where the answer lives (ADR 0004).

### AD-3 — Constant name + location
`PHASE_ONE_BLOCKED_STAGES: frozenset[str] = frozenset({"micro_live", "live"})` at module scope in `src/milodex/promotion/state_machine.py`, immediately after `STAGE_ORDER`. `frozenset` signals "not meant to be mutated at runtime"; the phase-suffix in the name signals "this is a Phase 1 constraint, not a permanent one." When Phase 2's lifting ADR lands, the constant gets removed, not edited — removal is a one-line diff that is easy to review and hard to miss in a code search.

### AD-4 — Test organization
The existing `test_valid_stage_transitions` keeps only `("backtest", "paper")` after this slice. The two removed parametrize cases become a new test `test_phase_one_blocks_micro_live_and_live` with its own parametrize over `(from_stage, to_stage)` that exercises both blocked stages from their legal predecessor. That keeps the refusal tests grouped and the legality tests grouped, rather than scattering assertions.

---

## 3. File Inventory

Paths relative to `C:\Users\zdm80\Milodex`.

### Phase A — Refusal + test updates

**Modify:**
- `src/milodex/promotion/state_machine.py` — add `PHASE_ONE_BLOCKED_STAGES` constant + refusal block in `validate_stage_transition`.
- `tests/milodex/promotion/test_state_machine.py` — prune `test_valid_stage_transitions` parametrize list; add new `test_phase_one_blocks_micro_live_and_live`.
- `tests/milodex/cli/test_promote.py` — rewrite `test_promote_sequential_promotions`; rewrite `test_promote_to_live_requires_confirm` to assert phase-1 refusal; delete `test_promote_to_live_with_confirm_succeeds`.
- `tests/milodex/cli/test_promotion_promote.py` — add `test_promotion_promote_refuses_live_stage_in_phase_one` (end-to-end: no promotion row, no manifest row, stage-unchanged YAML).

### Phase B — Docs

**Modify:**
- `docs/adr/0015-strategy-identifier-and-frozen-manifest.md` — flip status to fully implemented.
- `docs/ROADMAP_PHASE1.md` — check off the remaining §6.1.3 live-stage refusal item; check off §6.1.4.
- `docs/OPERATIONS.md` — one-line note under the promote surface that `--to {micro_live, live}` are refused during Phase 1.

---

## 4. Commit Sequence

TDD where possible: failing refusal test → implementation → green. The affected legacy tests are rewritten in the same commit because leaving them failing between commits would make the suite red mid-slice.

### Commit 1 — `feat(promotion): refuse live + micro_live promotions during Phase 1 (R-PRM-006)`

Files touched (all four together — the existing tests would fail mid-commit otherwise):
- `src/milodex/promotion/state_machine.py`
- `tests/milodex/promotion/test_state_machine.py`
- `tests/milodex/cli/test_promote.py`
- `tests/milodex/cli/test_promotion_promote.py`

- [ ] **Step 1: Add the failing end-to-end CLI test first**

Append to `tests/milodex/cli/test_promotion_promote.py`:

```python
# ---------------------------------------------------------------------------
# Phase 1 live-stage refusal (R-PRM-006, ADR 0004)
# ---------------------------------------------------------------------------


def test_promotion_promote_refuses_live_stage_in_phase_one(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="micro_live")

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "live",
            "--recommendation",
            "ready for live",
            "--risk",
            "a risk",
            "--lifecycle-exempt",
            "--confirm",
        ],
        tmp_path,
    )

    assert exit_code != 0
    stderr = err.getvalue()
    assert "blocked during Phase 1" in stderr
    assert "ADR 0004" in stderr
    assert "R-PRM-006" in stderr

    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions() == []
    assert store.list_strategy_manifests() == []
    assert 'stage: "micro_live"' in config_path.read_text(encoding="utf-8")


def test_promotion_promote_refuses_micro_live_in_phase_one(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="paper")

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "micro_live",
            "--recommendation",
            "ready for micro_live",
            "--risk",
            "a risk",
            "--lifecycle-exempt",
        ],
        tmp_path,
    )

    assert exit_code != 0
    assert "blocked during Phase 1" in err.getvalue()
    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions() == []
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
./.venv/Scripts/python.exe -m pytest tests/milodex/cli/test_promotion_promote.py::test_promotion_promote_refuses_live_stage_in_phase_one tests/milodex/cli/test_promotion_promote.py::test_promotion_promote_refuses_micro_live_in_phase_one -v
```

Expected: both FAIL. `test_promotion_promote_refuses_live_stage_in_phase_one` will fail because the promotion currently succeeds (AssertionError on `store.list_promotions() == []`). `test_promotion_promote_refuses_micro_live_in_phase_one` will fail the same way.

- [ ] **Step 3: Add the constant + refusal in `validate_stage_transition`**

In `src/milodex/promotion/state_machine.py`, below the existing `STAGE_ORDER` constant (around line 35):

```python
STAGE_ORDER: list[str] = ["backtest", "paper", "micro_live", "live"]

# Phase 1 blocks promotion to these stages (ADR 0004, R-PRM-006). The risk
# layer's R-EXE-007 provides runtime defense-in-depth; this constant provides
# the promotion-time refusal. Remove this — and the check in
# validate_stage_transition — in the future ADR that lifts the live-lock.
PHASE_ONE_BLOCKED_STAGES: frozenset[str] = frozenset({"micro_live", "live"})
```

Then extend `validate_stage_transition` (append after the existing `to_idx != from_idx + 1` skip-stage check, before the function returns):

```python
    if to_stage in PHASE_ONE_BLOCKED_STAGES:
        msg = (
            f"Promotion to '{to_stage}' is blocked during Phase 1 "
            f"(ADR 0004, R-PRM-006). Paper-only period — the live-stage lock "
            f"lifts via a future ADR, not a config edit."
        )
        raise ValueError(msg)
```

- [ ] **Step 4: Run the new CLI tests to verify they pass**

```bash
./.venv/Scripts/python.exe -m pytest tests/milodex/cli/test_promotion_promote.py::test_promotion_promote_refuses_live_stage_in_phase_one tests/milodex/cli/test_promotion_promote.py::test_promotion_promote_refuses_micro_live_in_phase_one -v
```

Expected: both PASS.

- [ ] **Step 5: Run the full suite to find ripple failures**

```bash
./.venv/Scripts/python.exe -m pytest tests/milodex/ 2>&1 | tail -40
```

Expected: failures in `tests/milodex/promotion/test_state_machine.py::test_valid_stage_transitions` (2 parametrize cases) and in `tests/milodex/cli/test_promote.py::test_promote_sequential_promotions`, `::test_promote_to_live_requires_confirm`, `::test_promote_to_live_with_confirm_succeeds`. These are the ripple from §1 "Ripple on existing tests." Fix them in the next steps.

- [ ] **Step 6: Update `tests/milodex/promotion/test_state_machine.py`**

Replace the existing `test_valid_stage_transitions` parametrize + body with a single-case body (the only still-legal transition), and add the new refusal test:

```python
def test_backtest_to_paper_is_valid() -> None:
    validate_stage_transition("backtest", "paper")  # must not raise


@pytest.mark.parametrize(
    ("from_stage", "to_stage"),
    [
        ("paper", "micro_live"),
        ("micro_live", "live"),
    ],
)
def test_phase_one_blocks_micro_live_and_live(from_stage: str, to_stage: str) -> None:
    with pytest.raises(ValueError, match="blocked during Phase 1"):
        validate_stage_transition(from_stage, to_stage)
```

Delete the old `test_valid_stage_transitions` function and its parametrize decorator.

Leave `test_same_stage_raises`, `test_downgrade_raises`, `test_skip_stage_raises`, `test_skip_to_live_raises`, `test_unknown_from_stage_raises`, `test_unknown_to_stage_raises`, and `test_stage_order_is_complete` untouched. (`test_skip_to_live_raises` still asserts that `paper → live` raises `"Skipping stages"` — the structural check fires before the phase-1 check per AD-1, so this assertion still holds.)

- [ ] **Step 7: Rewrite `tests/milodex/cli/test_promote.py::test_promote_sequential_promotions`**

Old behavior: promotes `backtest → paper → micro_live` and asserts two rows written. New behavior: promotes `backtest → paper` only, then asserts `paper → micro_live` is refused with a phase-1 message and leaves the ledger at one row.

Replace the function body (around lines 256–272) with:

```python
def test_promote_sequential_promotions(tmp_path: Path) -> None:
    """Promote backtest→paper; the paper→micro_live step is blocked in Phase 1."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    _run_cli(["promote", _STRATEGY_ID, "--to", "paper", "--lifecycle-exempt"], tmp_path)

    exit_code, _, err = _run_cli(
        ["promote", _STRATEGY_ID, "--to", "micro_live", "--lifecycle-exempt"],
        tmp_path,
    )
    assert exit_code != 0
    assert "blocked during Phase 1" in err.getvalue()

    store = EventStore(tmp_path / "data" / "milodex.db")
    promotions = store.list_promotions()
    assert len(promotions) == 1
    assert promotions[0].to_stage == "paper"
```

- [ ] **Step 8: Rewrite `test_promote_to_live_requires_confirm` as a phase-1 refusal test**

The old test asserted that without `--confirm`, the live promotion fails for that reason. Now the phase-1 refusal fires first (AD-1 ordering puts the structural checks — including `--confirm` validation — before the policy check, but `--confirm` validation happens in the CLI handler *after* `validate_stage_transition` returns; since `validate_stage_transition` now raises for `live`, the `--confirm` check is unreachable). Rename and rewrite:

```python
def test_promote_to_live_refused_in_phase_one(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="micro_live")

    exit_code, _, err = _run_cli(
        ["promote", _STRATEGY_ID, "--to", "live", "--lifecycle-exempt", "--confirm"],
        tmp_path,
    )

    assert exit_code != 0
    assert "blocked during Phase 1" in err.getvalue()
    assert "ADR 0004" in err.getvalue()
```

- [ ] **Step 9: Delete `test_promote_to_live_with_confirm_succeeds`**

Remove the entire function (lines 294–310) and its preceding section-divider comment if it becomes orphaned. The behavior it asserted (live promotion succeeds with `--confirm`) is now forbidden.

- [ ] **Step 10: Run the full suite to verify all green**

```bash
./.venv/Scripts/python.exe -m pytest tests/milodex/ 2>&1 | tail -5
```

Expected: `429 passed` (the same count as slice-2 landing — two tests removed from the parametrize in `test_state_machine.py`, one test deleted from `test_promote.py`, two tests added to `test_promotion_promote.py`; net zero).

- [ ] **Step 11: Lint + format check**

```bash
./.venv/Scripts/python.exe -m ruff check src/milodex/promotion/state_machine.py tests/milodex/promotion/test_state_machine.py tests/milodex/cli/test_promote.py tests/milodex/cli/test_promotion_promote.py && ./.venv/Scripts/python.exe -m ruff format --check src/milodex/promotion/state_machine.py tests/milodex/promotion/test_state_machine.py tests/milodex/cli/test_promote.py tests/milodex/cli/test_promotion_promote.py
```

Expected: "All checks passed!" and "4 files already formatted" (or "ruff format" reformats them cleanly).

- [ ] **Step 12: Commit**

```bash
git add src/milodex/promotion/state_machine.py tests/milodex/promotion/test_state_machine.py tests/milodex/cli/test_promote.py tests/milodex/cli/test_promotion_promote.py
git commit -m "feat(promotion): refuse live + micro_live promotions during Phase 1 (R-PRM-006)

Promotion to micro_live or live now raises with a message that cites
ADR 0004 and R-PRM-006. The refusal lives inside validate_stage_transition
so every caller — promotion promote, legacy promote, and transition() —
gets it for free. R-EXE-007 at the risk layer remains defense-in-depth.

Existing tests that exercised phase-2 stages are rewritten as phase-1
refusal assertions or removed where the asserted behavior is now forbidden."
```

---

### Commit 2 — `docs(adr,roadmap,ops): close ADR 0015 with slice-3 live-stage refusal`

- [ ] **Step 1: Flip ADR 0015 status**

In `docs/adr/0015-strategy-identifier-and-frozen-manifest.md`, replace the status block:

```
**Status:** Implemented (runtime drift check, freeze CLI, state machine, evidence package, live-stage refusal) — ADR complete for Phase 1
**Date:** 2026-04-21
**Implementation:** Phase 1.4 landed across three slices on 2026-04-23. Slice 1 added the frozen manifest + runtime drift check + freeze CLI (`docs/superpowers/plans/2026-04-23-phase-1-4-slice-1-frozen-manifest.md`). Slice 2 added the transactional state machine + evidence package + promote/demote/history CLIs (`docs/superpowers/plans/2026-04-23-phase-1-4-slice-2-state-machine-evidence.md`). Slice 3 added the Phase 1 live-stage refusal hook per R-PRM-006 (`docs/superpowers/plans/2026-04-23-phase-1-4-slice-3-live-stage-refusal.md`). Lifting the live-lock is out of scope for Phase 1 and requires a future ADR per ADR 0004.
```

- [ ] **Step 2: Update ROADMAP_PHASE1.md**

In `docs/ROADMAP_PHASE1.md` §6.1.3, check the last unchecked item:

```
- [x] Live-stage refusal hook (slice 3) — CLI-level refusal of `--to live` (and `--to micro_live`) during Phase 1 per ADR 0004. (Slice 3, 2026-04-23.)
```

In §6.1.4, check the box:

```
#### 6.1.4 Live-Trading Gate (Paper-Only Safeguard)
- [x] Even with the state machine in place, Phase 1 remains **paper-only** per ADR 0004. The `live` stage is implemented-but-locked: attempting to promote to `live` (or `micro_live`) returns a clear refusal citing ADR 0004 / R-PRM-006 at the state-machine level, and R-EXE-007 remains as runtime defense-in-depth. (Slice 3, 2026-04-23.)
```

- [ ] **Step 3: Update OPERATIONS.md**

In `docs/OPERATIONS.md`, find the `milodex promotion` operator surface section and append to the `promotion promote` bullet (or add an adjacent note):

```
- **Phase 1 lock:** `--to micro_live` and `--to live` are refused by the state machine with a message citing ADR 0004 / R-PRM-006. The lock lifts only via a future ADR, not a config edit. R-EXE-007 at the risk layer is the runtime defense-in-depth.
```

- [ ] **Step 4: Sanity-check the docs didn't break anything**

```bash
./.venv/Scripts/python.exe -m pytest tests/milodex/ 2>&1 | tail -3
```

Expected: `429 passed`.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0015-strategy-identifier-and-frozen-manifest.md docs/ROADMAP_PHASE1.md docs/OPERATIONS.md
git commit -m "docs(adr,roadmap,ops): close ADR 0015 with slice-3 live-stage refusal

ADR 0015 status flipped to 'ADR complete for Phase 1'. ROADMAP §6.1.3
last item and §6.1.4 both checked off. OPERATIONS.md notes that
--to live and --to micro_live are refused by the state machine with
a message citing ADR 0004 / R-PRM-006, with R-EXE-007 as the runtime
defense-in-depth."
```

---

## 5. Manual Integration Verification

Run against the real `data/milodex.db`. The refusal writes nothing, so this is safe to run repeatedly.

```bash
# 1. The regime strategy is at paper after slice-2 verification. Attempt to
#    promote it to live — should refuse cleanly.
./.venv/Scripts/python.exe -m milodex.cli.main promotion promote \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --to live \
  --lifecycle-exempt \
  --confirm \
  --recommendation "attempting live" \
  --risk "should be refused"
# Expected: non-zero exit. Stderr contains "blocked during Phase 1",
# "ADR 0004", "R-PRM-006". No new row in history.

# 2. Attempt paper → micro_live. Should also refuse.
./.venv/Scripts/python.exe -m milodex.cli.main promotion promote \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --to micro_live \
  --lifecycle-exempt \
  --recommendation "attempting micro_live" \
  --risk "should be refused"
# Expected: non-zero exit. Same refusal message. No new row.

# 3. Confirm history shows exactly the slice-2 entries — no new rows from
#    the failed attempts.
./.venv/Scripts/python.exe -m milodex.cli.main promotion history \
  regime.daily.sma200_rotation.spy_shy.v1
# Expected: the two rows from slice-2 verification (id=4 lifecycle_exempt
# promotion, id=3 demotion). No additional rows from steps 1 and 2.

# 4. Legacy path also refused.
./.venv/Scripts/python.exe -m milodex.cli.main promote \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --to live --lifecycle-exempt --confirm
# Expected: stderr starts with "DEPRECATION:" banner, then "blocked during
# Phase 1". No new row.

# 5. Backtest → paper still works (the regime strategy is already at paper,
#    so we expect the "already at stage" error — confirming the validator's
#    structural checks still fire and the only policy addition is the
#    blocked-stage refusal).
./.venv/Scripts/python.exe -m milodex.cli.main promotion promote \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --to paper \
  --lifecycle-exempt \
  --recommendation "already at paper" \
  --risk "should fail on already-at-stage, not phase-one block"
# Expected: non-zero exit, stderr contains "already at stage 'paper'".
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

1. **Both `micro_live` and `live` are blocked, not just `live`.** R-PRM-006 names both. Blocking only `live` would leave a window where an operator promotes to `micro_live`, writes the ledger + freezes the manifest, and only hits R-EXE-007 later.
2. **Refusal lives in `validate_stage_transition`**, not per-handler. One source of truth; all callers covered.
3. **Constant is hardcoded, named `PHASE_ONE_BLOCKED_STAGES`**, `frozenset` typed. Phase 2's lifting ADR removes the constant and the check — not by editing either, but by deletion. That makes the lift a reviewable, atomic change.
4. **Error message format locked (AD-2)** — tests match `"blocked during Phase 1"` + `"ADR 0004"` + `"R-PRM-006"` substrings.
5. **Refusal ordering: structural checks first, policy check last.** A transition like `backtest → live` reports "Skipping stages" (structural), not "blocked in Phase 1" (policy), so typos are corrected before the operator has to parse the policy message.
6. **Scope is narrow by design.** No new CLI flags. No new event-store columns. No config. No risk-layer changes. The R-EXE-007 runtime refusal at the risk layer stays exactly as it is — it is defense-in-depth, not something this slice needs to touch.

---

## 7. Phase 1.4 Done After This Slice

With slice 3 committed, Phase 1.4 is complete:
- ADR 0015 fully implemented (slice 1 freeze + runtime drift, slice 2 state machine + evidence, slice 3 live-stage refusal).
- ROADMAP §6.1.1, §6.1.2, §6.1.3, §6.1.4 all checked.
- Phase 1.4 DoD items (§6.2) met: regime strategy promoted via CLI with an evidence package, `promote --to live` produces a clean logged refusal, SC-1 met, YAML drift refused at runtime.

The next open Phase 1 item becomes whatever is next in `docs/ROADMAP_PHASE1.md` §7 (cross-cutting work) or §8+ (later sub-phases).

# UI Readiness Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each PR section ends with a `git commit` step that establishes the PR boundary; **stop after each PR's final commit and request review before continuing to the next PR**.

**Goal:** Resolve 12 operator-reported issues across the Milodex GUI in 9 single-concern PRs (plus 2 new ADRs), delivered in independence order so the morning-readiness items ship first and the architectural items ship behind them without blocking each other.

**Architecture:** Five buckets, each containing 1+ PRs that share concerns but not files. PR-1 is subprocess-flag + a read-model regression repair (Python). PR-2 is a focused QML batch (no Python). PR-3 lifts two QML properties to a session-bag QtObject. PR-4 is a small data-ingest + rendering fix. PR-5 splits one durable table into two (in-framework SQL migration + helper-level writer redirect + read-path completeness + ADR 0053). PR-6 adds four new event sources to LedgerState + expands Section VII to include backtest results. PR-7a/b/c land Milodex's first risk-policy mutation surface — a real risk-profile system enforced by `execution/service.py`, an audit table + Slot/Signal bridge with refusal rules, and a side drawer with time-format toggle and clean-quit semantics (ADR 0054).

**Tech Stack:** Python 3.11 (src-layout package `milodex`), PySide6 6.x / Qt Quick (QML), SQLite (event store with version-gated migration framework at `event_store.py:1254`), pytest + ruff, YAML for risk config.

**Spec:** [`docs/superpowers/specs/2026-05-19-ui-readiness-batch-design.md`](docs/superpowers/specs/2026-05-19-ui-readiness-batch-design.md). Read it before starting any PR; the spec's decisions table, acceptance criteria, and Section 9 open verification steps are normative. The reviewer-fixed details (provenance-based quarantine, migration numbering, fallback-behavior asymmetry, runtime-consumer integration test, invariant-based acceptance) supersede any older mental model.

**Commands (Windows, project root `C:\Users\zdm80\Milodex`):**
- Full test suite: `python -m pytest -q`
- Single test: `python -m pytest tests/milodex/<path>::<name> -v`
- Lint: `python -m ruff check src/ tests/`
- Format: `python -m ruff format src/ tests/`
- Run GUI: `python -m milodex gui` (smoke checks only — do not start live runners during testing)

**Branch:** All PRs land on `codex/ui-wiring-stabilization`. PR-1 commits first; each subsequent PR rebases onto the latest before starting.

**Hard constraints (any violation rejects review):**
- **Risk layer is sacred** (CLAUDE.md). PR-7a/b/c are doctrine-bearing — see ADR 0054 sketched in the spec. No strategy, ML model, frontier agent, or feature may select/switch profiles. The active-profile loader is the only authorized risk-config path for runtime enforcement.
- **Migrations are in-framework only.** No standalone Python migration scripts. Use the SQL-file pattern modeled after [`008_explanations_backtest_run_id.sql`](src/milodex/core/migrations/008_explanations_backtest_run_id.sql). Confirm next available number immediately before writing (010 at spec-write time; verify via `ls src/milodex/core/migrations/`).
- **Forensic preservation.** Anomalous data rows get quarantined to a sibling table, never deleted, per the operator's `feedback_inspect_before_deciding` rule.
- **Test-first.** Every behavioral change starts with a failing test. Steps below enforce this rhythm.
- **Frequent commits.** Each task's final step is a commit. PR boundary commits use Conventional Commits prefixes.

---

## PR-0: In-flight work absorbed (already landed)

This plan was written assuming `codex/ui-wiring-stabilization` started clean off master. It did not. Two coherent features were in flight on the branch when implementation began and were folded in **before** PR-1 dispatch. Both are now committed; the plan is updated below to reflect the reduced scopes that result.

**Absorbed work (three commits):**

| Commit | Subject | Scope |
|---|---|---|
| `(merge)` | Merge `fix/orphan-reconcile-pid-reuse` | Brings in upstream PID-identity hardening: two-stage liveness check (PID + start-time) in `_has_live_runner`, stale-lock unlink in `reconcile_orphaned_runs_on_bootstrap`, + 170-line forensic write-up `docs/reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md`. Source: production incident, host reset left phantom paper runners with PID-reused live processes mis-classified as live. |
| `feat(promotion): add idle stage + return-to-idle action` | Bench-stage taxonomy extension | New `idle` stage between `disabled` and `backtest`; new `stage_return` promotion type with "RETURNED" ledger label; 3 strategy YAMLs moved `backtest → idle`; bench facade gains return-to-idle submit/preview; `_compute_bench_action_menu` replaces synthetic Fresh+Pass fallback with real evidence derivation from durable state. |
| `chore(bench-v1): reconcile docstring + fixture` | Test/doc reconciliation | Two stragglers from the idle-stage work: the `re_run_verb` docstring still listed Fresh+Fail under no-verb cases (now updated — Fresh+Fail surfaces Initiate so the operator can recover from a failed run without manufacturing an Invalidation); and a fixture-based test had the stale expectation. |

**Plan impact:**

- **PR-1 (Runner-Launch Hygiene):** the orphan-reconcile portion is **already done and stronger than this plan proposed**. The merged work uses start-time identity (catches post-reboot PID reuse) rather than only bare `_process_exists`. PR-1 reduces to: **CREATE_NO_WINDOW subprocess flag only**. Task 1 (repro `is_session_running` regression) and Task 3 (repair regression) are likely no-ops now — run Task 1 first; if the post-merge state no longer reproduces, mark Task 3 N/A in the commit message.
- **PR-6 (Ledger Taxonomy):** the `stage_return → RETURNED` ledger plumbing is **already done** (idle-stage commit modified `_ledger_entries` to emit `outcomeKind: returned` with "RETURNED" label, and `_latest_promotions` to exclude `stage_return` alongside `demotion`). PR-6's remaining scope: backtest_complete merge, kill_switch_events merge, session_start/session_stop merge, new_strategy merge, BACKTESTS filter chip on Section VII.
- **PR-2/3/4/5/7a/7b/7c:** no conflict.

**Verified state after PR-0:** full suite green (1754 passed, 2 skipped, 4 xfailed). PR-1 implementer starts from this baseline.

---

## File Structure

| File | Responsibility | PR | Change |
|---|---|---|---|
| `src/milodex/strategies/paper_runner_control.py` | Subprocess launch | PR-1 | Replace `DETACHED_PROCESS` with `CREATE_NO_WINDOW` in `creationflags` (lines 229-235). |
| `src/milodex/strategies/orphan_reconciliation.py` (if repro confirms) | Bootstrap reconcile | PR-1 | Touch only if Tier-1 repro confirms `_has_live_runner` is closing live rows. |
| `src/milodex/gui/read_models.py` | GUI read models | PR-1, PR-6 | (PR-1) Possibly tighten `_latest_session_states` if repro confirms; (PR-6) extend `_ledger_entries` to merge across 6 sources. |
| `src/milodex/gui/qml/Milodex/components/RunnerSelect.qml` | Runner dropdown | PR-2 | Add `surface.canvas` backing + 2026-05-19 amendment comment; add `dismissed` signal (issues 03/05). |
| `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` | 7-section dashboard | PR-2, PR-3 | (PR-2) Wrap DRAWDOWN/SPY/EXCESS in fixed-height `Item` (issue 04); (PR-3) bind `perfSlice` and `throughputSlice` to `Main.sessionBag`. |
| `src/milodex/gui/qml/Milodex/components/SectionHeader.qml` | Shared section header | PR-2 | Change right-slot anchor from `baseline` to `verticalCenter` (issue 08). |
| `src/milodex/gui/qml/Milodex/surfaces/BenchSurface.qml` | Bench stages | PR-2 | Bottom-bias stage labels via `anchors.bottomMargin: parent.height * 0.35` (issue 11). |
| `src/milodex/gui/qml/Milodex/Main.qml` | Top-level chrome | PR-2, PR-3, PR-7c | (PR-2) Add top-level outside-click overlay (issue 05); (PR-3) add `sessionBag` QtObject; (PR-7c) host `RiskOfficeDrawer` + persistent banner. |
| `src/milodex/gui/market_tape_state.py` | Market tape data | PR-4 | Likely no change to `SYMBOLS`; fix lies in data-ingest writer or null rendering. Confirm during investigation. |
| `src/milodex/data/<ingest>` | Data ingest (find during investigation) | PR-4 | Add VIX to ingest universe with Yahoo fallback if Alpaca free tier doesn't supply it. |
| `src/milodex/core/migrations/010_backtest_equity_snapshots.sql` | Schema split + quarantine | PR-5 | New migration file. |
| `src/milodex/core/event_store.py` | Event store API | PR-5, PR-7b | Add `append_backtest_equity_snapshot()` + `list_backtest_equity_snapshots_for_strategy()` methods (PR-5); migration 011 picked up automatically (PR-7b). |
| `src/milodex/analytics/snapshots.py` | Snapshot writer helpers | PR-5 | Rewrite docstring (broker-only contract); add `record_backtest_equity_snapshot()`. |
| `src/milodex/backtesting/engine.py` | Backtest engine | PR-5 | Swap `record_daily_snapshot()` call for `record_backtest_equity_snapshot()` in `_simulate`. |
| `src/milodex/analytics/reports.py` | Trust report | PR-5 | Redirect backtest-strategy reads from `list_portfolio_snapshots_for_strategy` to `list_backtest_equity_snapshots_for_strategy` (line ~103). |
| `src/milodex/gui/performance_state.py` | All-Paper read model | PR-5 | No SQL change required after quarantine migration; add a docstring sentence. |
| `docs/adr/0053-backtest-equity-snapshots-distinct-table.md` | ADR 0053 | PR-5 | New ADR file. |
| `src/milodex/gui/activity_feed_state.py` | Section VII feed | PR-6 | Add `backtest_runs` (completed) as a third event source. |
| `src/milodex/gui/qml/Milodex/surfaces/LedgerSurface.qml` | Ledger surface | PR-6 | Extend outcomeKind mapping + reorganize filters into two-row grouped layout. |
| `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` (again) | Section VII chip filters | PR-6 | Add BACKTESTS toggle to Section VII's filter set. |
| `configs/risk_profiles/conservative.yaml` | Conservative overlay | PR-7a | New file (tightens). |
| `configs/risk_profiles/standard.yaml` | Standard overlay | PR-7a | New file (identity overlay; empty `{}` or comment-only). |
| `configs/risk_profiles/aggressive.yaml` | Aggressive overlay | PR-7a | New file (loosens, bounded by code-level ceilings). |
| `src/milodex/risk/config.py` | Risk config loader | PR-7a | Add `_ABSOLUTE_CEILINGS` constants + `get_active_profile_name()` + `load_active_risk_profile()`. |
| `src/milodex/execution/service.py` | Execution path | PR-7a | Migrate `load_risk_defaults(self._risk_defaults_path)` call at line 376 → `load_active_risk_profile()`. |
| `docs/adr/0054-risk-profiles-bounded-operator-preferences.md` | ADR 0054 | PR-7a | New ADR file. |
| `src/milodex/core/migrations/011_risk_profile_changes.sql` | Audit table | PR-7b | New migration. |
| `src/milodex/gui/risk_profile_bridge.py` | Slot/Signal bridge | PR-7b | New file. |
| `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml` | Side drawer | PR-7c | New file. |
| `src/milodex/gui/qml/Milodex/components/ElevatedPostureBanner.qml` | Persistent banner | PR-7c | New file. |

**Build order rationale:** smallest, most-painful fixes first (PR-1, PR-2 ship the morning-readiness items). Architectural items behind them (PR-5 splits a table; PR-7a-c builds the risk-profile system on top of stable foundations). PR-7a must precede 7b and 7c.

---

## PR-1: Runner-Launch Hygiene (issues 01, 02)

**Goal:** No terminal popups on runner start; "Stop Trading" verb reappears for live paper-stage runners.

**Scope check:** Two issues, possibly one fix file (subprocess flag) + one regression repair (read model). Stay narrow.

**Scope update (post PR-0):** The orphan-reconciliation hardening is **already merged in** (see PR-0 above; commit 8bc472f). Task 2 (CREATE_NO_WINDOW) is the primary remaining work. Task 1 still runs as a pre-flight check: if the post-merge state no longer reproduces the `is_session_running` regression, mark Task 3 N/A and proceed to the boundary commit after Task 2.

---

### Task 1: Reproduce the `is_session_running` regression (before changing logic)

**Files:**
- Read-only: `src/milodex/strategies/orphan_reconciliation.py`
- Read-only: `src/milodex/gui/read_models.py` around line 1085 (`_latest_session_states`)
- Inspect: `data/milodex.db`

The spec's Section 9 step 1 mandates repro before changing logic. The hypothesis: orphan reconciliation may be closing `strategy_runs.ended_at` on still-live runners, so the bench menu falls through to "Start Trading" instead of "Stop Trading". This task confirms or refutes the hypothesis.

- [ ] **Step 1: Inspect orphan-reconciliation closures in the live DB**

Run:
```bash
python -c "
import sqlite3
conn = sqlite3.connect('file:C:/Users/zdm80/Milodex/data/milodex.db?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
print('--- rows closed by orphan reconciliation, newest first ---')
for r in conn.execute('''SELECT id, strategy_id, session_id, started_at, ended_at, exit_reason
                        FROM strategy_runs
                        WHERE exit_reason = 'orphaned_no_live_runner'
                        ORDER BY id DESC LIMIT 20''').fetchall():
    print(dict(r))
print()
print('--- any open paper sessions (ended_at IS NULL) ---')
for r in conn.execute('''SELECT id, strategy_id, session_id, started_at, exit_reason
                        FROM strategy_runs
                        WHERE ended_at IS NULL
                        ORDER BY id DESC LIMIT 10''').fetchall():
    print(dict(r))
"
```

- [ ] **Step 2: Decide based on output**

Two cases:
- **Repro confirmed**: closures correspond to runners the operator believes were live at that timestamp (started_at very close to ended_at; multiple closures shortly after app start). → Fix targets `_has_live_runner` or bootstrap timing in `orphan_reconciliation.py`. Document the specific failure mode in a comment block in this task before proceeding.
- **Repro refuted**: closures look like genuinely dead PIDs (large gap between started_at and ended_at, no live runner observed). → Hypothesis wrong; do NOT touch `orphan_reconciliation.py` or `_latest_session_states` in PR-1. The "Stop Trading missing" issue must be re-diagnosed; possibly the runner truly isn't running and the user's mental model is stale. Mark this task with a finding note and proceed to Task 2 with subprocess-flag fix only.

- [ ] **Step 3: Record the verdict**

Append a one-paragraph finding to the PR description (text file `pr-1-finding.md` at repo root, will be deleted before commit):
```
PR-1 repro finding (YYYY-MM-DD HH:MM):
- Closures examined: <N>
- Verdict: confirmed | refuted
- Evidence: <one or two sentences>
- Action: <touch orphan_reconciliation | leave alone>
```

This record is for the PR description; do NOT commit the file.

---

### Task 2: Add `CREATE_NO_WINDOW` subprocess flag (issue 02)

**Files:**
- Modify: `src/milodex/strategies/paper_runner_control.py` (around lines 229-235; verify line numbers before editing)
- Test: `tests/milodex/strategies/test_paper_runner_control.py`

- [ ] **Step 1: Locate the current `creationflags` block**

Run:
```bash
python -m grep "creationflags" src/milodex/strategies/paper_runner_control.py
```
Or:
```bash
python -c "
with open('src/milodex/strategies/paper_runner_control.py') as f:
    for i, line in enumerate(f, 1):
        if 'creationflags' in line or 'CREATE_NEW_PROCESS_GROUP' in line or 'DETACHED_PROCESS' in line or 'CREATE_NO_WINDOW' in line:
            print(f'{i}: {line.rstrip()}')
"
```

Confirm the lines that need changing.

- [ ] **Step 2: Write the failing test**

In `tests/milodex/strategies/test_paper_runner_control.py`, add:
```python
import subprocess
import sys
import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only flag combination")
def test_creationflags_uses_create_no_window_not_detached_process():
    """The subprocess launch flags must include CREATE_NO_WINDOW (the actual
    Windows console suppressor) and must NOT include DETACHED_PROCESS, which
    is mutually exclusive with CREATE_NO_WINDOW per MSDN and paradoxically
    creates a console for a console-subsystem child .exe."""
    from milodex.strategies import paper_runner_control as prc

    flags = prc._compute_creation_flags()  # helper to be introduced

    assert flags & subprocess.CREATE_NO_WINDOW, "CREATE_NO_WINDOW must be set"
    assert not (flags & subprocess.DETACHED_PROCESS), "DETACHED_PROCESS must NOT be set"
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP, "CREATE_NEW_PROCESS_GROUP preserved"
```

- [ ] **Step 3: Run the test, verify it FAILS**

Run: `python -m pytest tests/milodex/strategies/test_paper_runner_control.py::test_creationflags_uses_create_no_window_not_detached_process -v`

Expected: FAIL — `_compute_creation_flags` does not exist.

- [ ] **Step 4: Refactor the inline `creationflags` build into a helper**

In `src/milodex/strategies/paper_runner_control.py`, add at module level:
```python
def _compute_creation_flags() -> int:
    """Return the subprocess Popen creationflags for a detached paper runner.

    CREATE_NO_WINDOW (0x08000000) is the correct console-suppression flag on
    Windows. DETACHED_PROCESS is mutually exclusive with CREATE_NO_WINDOW per
    MSDN (ERROR_INVALID_PARAMETER on combine) and paradoxically creates a
    console for a console-subsystem child .exe. Use CREATE_NO_WINDOW for
    suppression + CREATE_NEW_PROCESS_GROUP to keep the child outside the
    parent's group (Ctrl-C isolation).
    """
    import subprocess
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    return flags
```

Replace the existing inline `creationflags` construction (where it OR'd `DETACHED_PROCESS`) with a call: `creationflags = _compute_creation_flags()`.

- [ ] **Step 5: Run the test, verify it PASSES**

Run: `python -m pytest tests/milodex/strategies/test_paper_runner_control.py::test_creationflags_uses_create_no_window_not_detached_process -v`

Expected: PASS.

- [ ] **Step 6: Run the full paper_runner_control suite, verify no regressions**

Run: `python -m pytest tests/milodex/strategies/test_paper_runner_control.py -v`

Expected: all PASS. If anything fails, investigate (a previous test may have asserted DETACHED_PROCESS).

- [ ] **Step 7: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/strategies/paper_runner_control.py tests/milodex/strategies/test_paper_runner_control.py
git -C C:/Users/zdm80/Milodex commit -m "fix(runner): use CREATE_NO_WINDOW instead of DETACHED_PROCESS

Windows: DETACHED_PROCESS and CREATE_NO_WINDOW are mutually exclusive per
MSDN, and DETACHED_PROCESS paradoxically creates a console for a console-
subsystem child .exe. The actual console suppressor is CREATE_NO_WINDOW
(0x08000000). Swap and add a regression test.

Closes one half of issue 02 (terminal popups on runner start)."
```

---

### Task 3: Repair `is_session_running` regression (only if Task 1 confirmed)

**Skip this task if Task 1's repro was refuted.** Update the PR description's findings note accordingly.

**Files (if confirmed):**
- Modify: `src/milodex/strategies/orphan_reconciliation.py` (specific lines depend on repro finding)
- Test: `tests/milodex/strategies/test_orphan_reconciliation.py`

- [ ] **Step 1: Document the regression mode**

From Task 1 finding, identify the specific failure mode (e.g., `_has_live_runner` returns False because PID start-time is N/A on this host; bootstrap sweep runs too early before the runner has its lock; etc.).

- [ ] **Step 2: Write the failing regression test**

In `tests/milodex/strategies/test_orphan_reconciliation.py`, add a test fixture that reproduces the failure mode identified above. The test must:
1. Create a `strategy_runs` row with `ended_at IS NULL` representing a live runner.
2. Set up the conditions that triggered the false-positive closure (per Task 1 finding).
3. Invoke the orphan-reconciliation routine.
4. Assert: the row's `ended_at` remains NULL (live runner preserved).

Concrete test skeleton — adapt to the actual failure mode:
```python
def test_orphan_reconciliation_preserves_live_runner_with_lock(tmp_path):
    """Repro from PR-1 Task 1 finding: <one-line summary>."""
    from milodex.strategies import orphan_reconciliation as orph
    # arrange: db with one open run + lock file present + pid alive
    # act: call orph.reconcile(...)
    # assert: ended_at is still None
    ...
```

- [ ] **Step 3: Run test, verify it FAILS**

Run: `python -m pytest tests/milodex/strategies/test_orphan_reconciliation.py::test_orphan_reconciliation_preserves_live_runner_with_lock -v`

Expected: FAIL (the regression).

- [ ] **Step 4: Fix `_has_live_runner` or the bootstrap timing**

Implement the minimum change to make the test pass. Resist scope expansion — fix only the specific failure mode confirmed in Task 1.

- [ ] **Step 5: Run test, verify it PASSES**

Run: `python -m pytest tests/milodex/strategies/test_orphan_reconciliation.py -v`

Expected: all PASS, including pre-existing tests.

- [ ] **Step 6: Smoke-verify end-to-end**

Start a paper runner via the GUI (`python -m milodex gui`, then start a paper strategy), observe Bench action menu — should show "Stop Trading."

Document the verification in the PR description.

- [ ] **Step 7: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/strategies/orphan_reconciliation.py tests/milodex/strategies/test_orphan_reconciliation.py
git -C C:/Users/zdm80/Milodex commit -m "fix(runner): preserve live runner during orphan reconciliation

<one-line failure mode summary from Task 1>

Closes issue 01 (Stop Trading button missing when paper runner is alive)."
```

---

### PR-1 boundary

- [ ] **Final step: Run full suite + lint**

```bash
python -m pytest -q
python -m ruff check src/ tests/
```

Expected: full suite PASS; no lint errors.

- [ ] **PR-1 ready for review.** Stop and request review before continuing.

---

## PR-2: Layout Discipline (issues 03, 04, 05, 08, 11)

**Goal:** Five focused QML diffs in one PR. Pure visual / interaction changes. No Python.

---

### Task 4: Issue 03 — solid backdrop on RunnerSelect dropdown + 2026-05-19 amendment comment

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/RunnerSelect.qml`

The spec note: the operator's "unreadable" feedback is the §3-III amendment to the prior 2026-05-18 operator-accepted decision. Don't silently override that comment.

- [ ] **Step 1: Update the design-decision comment block**

In `RunnerSelect.qml` around lines 6-11, locate the comment block beginning "DESIGN DECISION (operator-accepted): the open dropdown is intentionally borderless..." Replace it with:

```qml
// DESIGN DECISION (2026-05-19 amendment): the open dropdown carries a
// solid surface.canvas backing + border.regular outline. This overrides the
// 2026-05-18 "intentionally borderless" decision per operator feedback that
// the borderless dropdown was unreadable in practice when overlaying the
// KeyStat grid below (issue 03 in the 2026-05-19 UI readiness batch).
//
// The dropdown still closes on outside-click (issue 05 amendment, same date)
// and the CLOSE pill affordance is preserved.
```

- [ ] **Step 2: Add the backing Rectangle**

Locate the dropdown's outermost open-state container (around lines 96-156). As its first child (visually behind all other content), add:

```qml
Rectangle {
    anchors.fill: parent
    color: Theme.color.surface.canvas
    border.color: Theme.color.border.regular
    border.width: 1
    z: -1
}
```

If the existing children use a `Column` or `ColumnLayout` parent, ensure the Rectangle is a sibling at the parent level, not inside the Column.

- [ ] **Step 3: Smoke-verify in QML smoke test harness**

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

Expected: PASS. (This catches QML parse errors.)

- [ ] **Step 4: Visual verification (manual)**

Launch GUI (`python -m milodex gui`), open the runner dropdown on the Trading Desk. Verify text underneath is fully covered and the dropdown reads cleanly.

Document in PR description with a screenshot.

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/components/RunnerSelect.qml
git -C C:/Users/zdm80/Milodex commit -m "fix(desk): solid backdrop on runner dropdown (issue 03)

Amends the 2026-05-18 borderless decision per operator feedback that the
borderless dropdown was unreadable when overlaying the KeyStat grid below."
```

---

### Task 5: Issue 04 — reserve vertical space in Section II TODAY mode

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` (around lines 447-462; verify line numbers before editing)
- Test: `tests/milodex/gui/test_qml_load_smoke.py` (add height-invariance assertion if a structural-render harness exists in this file)

The spec note: `Layout.preferredHeight` does NOT work on a plain `Column` — `perfCol` is a `Column`, not `ColumnLayout`. Use an `Item { height: <constant> }` wrapper with a Loader for child activation.

- [ ] **Step 1: Locate the DRAWDOWN/SPY/EXCESS SubGrid**

Run:
```bash
python -c "
with open('src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml') as f:
    for i, line in enumerate(f, 1):
        if 'isToday' in line or 'DRAWDOWN' in line or 'EXCESS' in line:
            print(f'{i}: {line.rstrip()}')
" | head -40
```

Identify the SubGrid currently rendered with `visible: !perfCol.isToday`.

- [ ] **Step 2: Measure the SubGrid's expanded height**

Visually or via QML inspection, determine the SubGrid's full height when visible. This becomes the constant height of the wrapper. If it's tied to a theme token (e.g., `Theme.space[8]` or similar), use the token; else hard-pin to the measured pixel value.

- [ ] **Step 3: Wrap with a height-reserved Item**

Replace the existing SubGrid block:
```qml
SubGrid {
    visible: !perfCol.isToday
    // ... existing config ...
}
```

With:
```qml
Item {
    width: parent.width
    height: <constant; e.g. Theme.space[8] or measured value>
    Loader {
        anchors.fill: parent
        active: !perfCol.isToday
        sourceComponent: drawdownSpyExcessComponent
    }
}
Component {
    id: drawdownSpyExcessComponent
    SubGrid {
        // ... existing config ...
    }
}
```

- [ ] **Step 4: Smoke-verify QML loads**

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

Expected: PASS.

- [ ] **Step 5: Add a height-invariance test (if structural harness exists)**

If `tests/milodex/gui/test_qml_load_smoke.py` or sibling files (e.g., `test_desk_layout_regression.py`) already implement a QQuickView-based structural test, add:

```python
def test_section_ii_height_invariant_to_perf_slice(...):
    # Render DeskSurface twice: once with perfSlice='Today', once with 'Week'.
    # Find perfCol; assert bounding-box height is equal across both renders.
```

If no such harness exists, document the invariant in the PR description and rely on manual visual verification + smoke load test.

- [ ] **Step 6: Manual verification**

Launch GUI; switch Section II between TODAY and WEEK repeatedly. Verify zero vertical movement of Section III or any row below.

- [ ] **Step 7: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml tests/milodex/gui/test_qml_load_smoke.py
git -C C:/Users/zdm80/Milodex commit -m "fix(desk): reserve Section II height across perfSlice toggles (issue 04)

DRAWDOWN/SPY/EXCESS row no longer collapses in TODAY mode. Implemented via
fixed-height Item wrapper + conditional Loader; perfCol is a plain Column
so Layout.preferredHeight is a no-op and the wrapper pattern is required."
```

---

### Task 6: Issue 05 — outside-click + ESC dismiss for RunnerSelect dropdown

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/RunnerSelect.qml` (add `dismissed` signal, ESC handler)
- Modify: `src/milodex/gui/qml/Milodex/Main.qml` (add top-level outside-click overlay)
- Test: `tests/milodex/gui/test_qml_load_smoke.py`

Per spec: overlay lives at `Main.qml` root level, NOT as a sibling inside DeskSurface.

- [ ] **Step 1: Add `dismissed` signal + ESC handler to RunnerSelect**

In `RunnerSelect.qml`, add:
```qml
signal opened()
signal dismissed()

Keys.onEscapePressed: function(event) {
    if (root.expanded) {
        root.expanded = false
        root.dismissed()
        event.accepted = true
    }
}
```

When the dropdown opens (existing logic), emit `opened()`. When it closes (existing CLOSE-pill handler, or new outside-click handler), emit `dismissed()`.

- [ ] **Step 2: Add top-level outside-click overlay in Main.qml**

In `Main.qml` root level (not inside any surface), add:
```qml
property bool _dropdownOpen: false

MouseArea {
    id: dropdownOutsideClick
    anchors.fill: parent
    visible: _dropdownOpen
    z: <above page content; below dropdown bounds. Set to a high value like 9000>
    onClicked: function(mouse) {
        // Forward dismissal to the active dropdown (RunnerSelect on Desk).
        _dropdownOpen = false
        dropdownDismissedSignal()
    }
}

signal dropdownDismissedSignal()
```

Wire `RunnerSelect.opened` → set `_dropdownOpen = true`; `dropdownDismissedSignal` → call `RunnerSelect.dismiss()` (or set its `expanded = false`).

The wiring is via Connections or direct binding at the DeskSurface Loader level — exact mechanism depends on the existing page-instancing pattern in Main.qml. If unclear, add a TODO comment marking the wiring point and implement during smoke verification.

- [ ] **Step 3: Smoke-verify QML loads**

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

Expected: PASS.

- [ ] **Step 4: Add an outside-click test (if structural harness exists)**

If a QQuickView-based harness is available, add:
```python
def test_runner_dropdown_dismisses_on_outside_click(...):
    # Open dropdown; simulate click at (0,0) (outside dropdown bounds);
    # assert dropdown is closed AND dismissed signal was emitted.
```

If no harness exists, document manual test plan in PR description.

- [ ] **Step 5: Manual verification**

Launch GUI; open runner dropdown; click outside dropdown bounds → should close. Press ESC → should close. Click CLOSE pill → should close.

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/components/RunnerSelect.qml src/milodex/gui/qml/Milodex/Main.qml
git -C C:/Users/zdm80/Milodex commit -m "feat(desk): runner dropdown dismisses on outside-click and ESC (issue 05)

Outside-click overlay lives at Main.qml root level for top-level z-coverage.
ESC handled at RunnerSelect level. CLOSE pill affordance preserved."
```

---

### Task 7: Issue 08 — vertical-center the SectionHeader right-slot

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/SectionHeader.qml` (around line 69)

- [ ] **Step 1: Locate the right-slot anchor**

Open `SectionHeader.qml` and find the right-slot child anchor. It currently reads `anchors.baseline: titleText.baseline` (or similar). Verify by:
```bash
python -c "
with open('src/milodex/gui/qml/Milodex/components/SectionHeader.qml') as f:
    for i, line in enumerate(f, 1):
        if 'anchors.baseline' in line or 'rightSlot' in line:
            print(f'{i}: {line.rstrip()}')
"
```

- [ ] **Step 2: Change baseline anchor to verticalCenter**

Replace:
```qml
anchors.baseline: titleText.baseline
```
With:
```qml
anchors.verticalCenter: titleText.verticalCenter
```

If the right-slot is a default slot accepting external content, ensure the change applies to the slot's anchoring, not just the title.

- [ ] **Step 3: Smoke-verify QML loads**

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

Expected: PASS.

- [ ] **Step 4: Manual verification**

Launch GUI; verify Section II ("as of HH:MM"), Section III ("N runners"), Section VII ("200 events") right-notes are vertically centered against their title text rather than bottom-clipping.

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/components/SectionHeader.qml
git -C C:/Users/zdm80/Milodex commit -m "fix(ui): vertical-center SectionHeader right-slot content (issue 08)

Was baseline-aligned to title, which clipped descenders. Now verticalCenter
of the title for clean alignment across all 7 dashboard sections."
```

---

### Task 8: Issue 11 — bottom-bias Bench stage labels

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/surfaces/BenchSurface.qml` (around lines 250-310)

- [ ] **Step 1: Locate the stage-label anchoring**

Open `BenchSurface.qml`, find the per-stage header row that renders `i. ii. iii.` roman numerals + uppercase stage names. Identify the current vertical anchoring (likely baseline-aligned to top).

- [ ] **Step 2: Replace with bottom-bias**

Change the roman+name labels' anchoring to:
```qml
anchors.bottom: stageHeaderRow.bottom
anchors.bottomMargin: stageHeaderRow.height * 0.35
```

This places the baseline at approximately 65% from the top of the row — visually grounding the label in the column below it.

If `stageHeaderRow.height * 0.35` produces a non-integer pixel and rendering looks blurry, round to nearest integer via `Math.round(...)`.

- [ ] **Step 3: Smoke-verify QML loads**

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

Expected: PASS.

- [ ] **Step 4: Capture before/after screenshots**

Per the spec's acceptance: PR description must include before/after screenshots showing the alignment shift. Capture both via the running GUI before committing.

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/surfaces/BenchSurface.qml
git -C C:/Users/zdm80/Milodex commit -m "fix(bench): bottom-bias stage labels (issue 11)

Labels now sit at ~65% of row height (anchors.bottom + bottomMargin = height*0.35),
visually grounding them in the column they label rather than floating above."
```

---

### PR-2 boundary

- [ ] **Final step: Run full suite + lint**

```bash
python -m pytest -q
python -m ruff check src/ tests/
```

Expected: PASS.

- [ ] **PR-2 ready for review.** Stop and request review.

---

## PR-3: Session Persistence (issue 12)

**Goal:** Lift `perfSlice` / `throughputSlice` from DeskSurface-local to a `Main.qml` `sessionBag` QtObject. Session-only persistence.

---

### Task 9: Add sessionBag QtObject and bind DeskSurface to it

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/Main.qml` (add `sessionBag`)
- Modify: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` (lines 43-44; bind instead of local property)
- Test: `tests/milodex/gui/test_qml_load_smoke.py`

- [ ] **Step 1: Write a failing test (if structural harness available)**

In `tests/milodex/gui/test_qml_load_smoke.py` (or sibling), add:
```python
def test_perf_slice_persists_across_page_switch(...):
    # Render Main; navigate to Desk; set perfSlice='Today';
    # navigate to Ledger; navigate back to Desk;
    # assert perfSlice is still 'Today'.
```

If no QQuickView-based harness exists yet to support this test, document the manual test plan in PR description and skip Steps 1-2; proceed to Step 3.

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py::test_perf_slice_persists_across_page_switch -v`

Expected: FAIL.

- [ ] **Step 2: Add sessionBag QtObject to Main.qml**

Add to `Main.qml` root level (alongside the existing `activeSurface` property):
```qml
QtObject {
    id: sessionBag
    objectName: "sessionBag"
    property string perfSlice: "Week"
    property string throughputSlice: "Month"
}
```

- [ ] **Step 3: Bind DeskSurface properties to sessionBag**

In `DeskSurface.qml` lines 43-44 (or wherever `perfSlice` / `throughputSlice` are declared as local properties), replace:
```qml
property string perfSlice: "Week"
property string throughputSlice: "Month"
```
With:
```qml
// Persisted across page switches via Main.qml sessionBag (issue 12).
// Session-only — does not survive app restart.
property alias perfSlice: sessionBagRef.perfSlice
property alias throughputSlice: sessionBagRef.throughputSlice
property QtObject sessionBagRef: <reference to Main.qml's sessionBag, via context property or root-id lookup>
```

The reference mechanism depends on Main.qml's loader/page-instancing pattern. Standard QML approach: register `sessionBag` as a context property in `app.py` GUI bootstrap (`engine.rootContext().setContextProperty("sessionBag", ...)`) OR rely on QML id traversal via `parent.parent.sessionBag` (brittle; prefer context property).

- [ ] **Step 4: Run smoke test**

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

Expected: PASS.

- [ ] **Step 5: Manual verification**

Launch GUI; switch Section II to TODAY; navigate to Ledger; return to Desk → still TODAY. Switch Section IV to MONTH; same test. Quit and reopen → both reset to defaults (Week, Month).

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/Main.qml src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml tests/milodex/gui/test_qml_load_smoke.py
git -C C:/Users/zdm80/Milodex commit -m "feat(desk): persist perfSlice and throughputSlice across page switches (issue 12)

Session-scoped only (does not survive app restart). sessionBag QtObject
lives at Main.qml root; DeskSurface aliases its slices through. Default
values (Week, Month) preserved."
```

---

### PR-3 boundary

- [ ] **Run full suite + lint, then stop for review.**

---

## PR-4: VIX Investigation + Fix (issue 07)

**Goal:** Section VI Market Tape shows VIX (current close + % change) OR a clear "VIX · n/a" indicator.

---

### Task 10: Investigation — locate the root cause

**Files (read-only):**
- `src/milodex/gui/market_tape_state.py` (around line 49 — confirm `SYMBOLS`)
- `data/cache/<version>/` directory listing
- Data ingest writer (location TBD via grep)

- [ ] **Step 1: Confirm SYMBOLS includes VIX**

Run:
```bash
python -c "
with open('src/milodex/gui/market_tape_state.py') as f:
    for i, line in enumerate(f, 1):
        if 'SYMBOLS' in line or 'VIX' in line:
            print(f'{i}: {line.rstrip()}')
"
```

Expected: VIX in the SYMBOLS tuple. (Per Section A review, this is already true.)

- [ ] **Step 2: Inspect cache directory**

```bash
ls -la C:/Users/zdm80/Milodex/data/cache/ 2>&1
ls -la C:/Users/zdm80/Milodex/data/cache/*/VIX* 2>&1
ls -la C:/Users/zdm80/Milodex/data/cache/*/ 2>&1 | head -30
```

Determine whether a `VIX.parquet` file exists in the latest cache version.

- [ ] **Step 3: Find the data-ingest writer**

```bash
python -c "
import os
for root, dirs, files in os.walk('src/milodex/data'):
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            with open(path) as fh:
                content = fh.read()
                if 'SPY' in content and 'TLT' in content:
                    print(path)
                    for i, line in enumerate(content.splitlines(), 1):
                        if 'SPY' in line or 'TLT' in line or 'VIX' in line or 'fetch' in line.lower():
                            print(f'  {i}: {line.rstrip()}')
                    print()
"
```

Identify the writer file. Determine whether VIX is in the fetch universe.

- [ ] **Step 4: Record the diagnosis**

Three possible root causes, mutually exclusive:
- **Cause A**: VIX not in ingest universe → writer must add it (Yahoo Finance fallback if Alpaca free tier doesn't support).
- **Cause B**: VIX in ingest universe but fetch silently fails → fix the writer's error handling.
- **Cause C**: VIX in ingest universe and cache exists, but MarketTapeState reads from a stale cache version → fix the version resolution.

Record the cause in PR description before proceeding to fix.

---

### Task 11: Fix path A — add VIX to ingest with Yahoo fallback

**Skip this task and proceed to Task 12 if Task 10's cause was B or C.**

**Files:**
- Modify: the data-ingest writer identified in Task 10 (e.g., `src/milodex/data/cache.py` or `src/milodex/data/ingest.py`)
- Possibly add: a Yahoo Finance fetcher if one doesn't already exist
- Test: `tests/milodex/data/test_<writer>.py`

- [ ] **Step 1: Write the failing test**

In the relevant test file:
```python
def test_ingest_universe_includes_vix():
    from milodex.data import <writer_module>
    universe = <writer_module>.UNIVERSE  # or equivalent
    assert "VIX" in universe or "^VIX" in universe
```

- [ ] **Step 2: Run, verify FAIL**

`python -m pytest tests/milodex/data/test_<writer>.py::test_ingest_universe_includes_vix -v`

- [ ] **Step 3: Add VIX to the universe**

In the writer, add VIX with provider-specific symbol convention. If Alpaca uses `^VIX`, document. If Alpaca doesn't support VIX, add a Yahoo Finance fallback: try Alpaca, on failure fall back to `yfinance` (already in deps if listed in pyproject.toml; otherwise add).

- [ ] **Step 4: Run, verify PASS**

- [ ] **Step 5: Run ingest manually to confirm cache write**

```bash
python -m milodex data ingest  # or equivalent CLI command — check `python -m milodex --help`
ls -la C:/Users/zdm80/Milodex/data/cache/*/VIX*
```

Expected: VIX parquet file present.

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/data/ tests/milodex/data/
git -C C:/Users/zdm80/Milodex commit -m "feat(data): add VIX to ingest universe with Yahoo fallback (issue 07)

Alpaca free tier does not ship ^VIX. Yahoo Finance fetch on Alpaca failure.
Cache now includes VIX.parquet."
```

---

### Task 12: Ensure null-pct rendering shows "n/a"

**Files:**
- Inspect: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` Section VI (lines ~726-768)
- Possibly modify: `TapeRow` rendering

Even with the ingest fix, ensure the QML row template renders `pctChange === null` (or undefined) gracefully — a future provider outage shouldn't return to silent omission.

- [ ] **Step 1: Locate the TapeRow rendering**

```bash
python -c "
with open('src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml') as f:
    for i, line in enumerate(f, 1):
        if 'TapeRow' in line or 'pctChange' in line or 'MarketTape' in line:
            print(f'{i}: {line.rstrip()}')
" | head -20
```

- [ ] **Step 2: Inspect the rendering logic**

If `pctChange` is bound via `text: somefn(modelData.pctChange)` or similar, confirm the function returns "n/a" or "—" when input is null.

- [ ] **Step 3: Add or strengthen null handling**

If missing, change:
```qml
text: modelData.pctChange.toFixed(2) + "%"
```
To:
```qml
text: modelData.pctChange !== null && modelData.pctChange !== undefined
      ? modelData.pctChange.toFixed(2) + "%"
      : "n/a"
```

- [ ] **Step 4: Smoke verify**

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

- [ ] **Step 5: Manual verification**

Launch GUI; verify VIX row appears with current value (Task 11 success path) OR "VIX · n/a" (graceful fallback path).

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml
git -C C:/Users/zdm80/Milodex commit -m "fix(desk): Market Tape renders null pctChange as 'n/a' (issue 07)

Defends against future provider outages; current operator-visible bug for
VIX is resolved by the ingest fix in the previous commit."
```

---

### PR-4 boundary

- [ ] **Run full suite + lint, then stop for review.**

---

## PR-5: portfolio_snapshots Schema Split + ADR 0053 (issue 06)

**Goal:** Stop the +9865% misread by separating backtest equity from broker portfolio snapshots. Preserve forensic evidence via quarantine. Document the split as ADR 0053.

**Hard order:** ADR first (Task 13), then migration (Task 14), then writer redirect (Task 15-16), then read-path completeness (Task 17), then read-model docstring (Task 18), then tests (Task 19), then verification (Task 20).

---

### Task 13: Write ADR 0053

**Files:**
- Create: `docs/adr/0053-backtest-equity-snapshots-distinct-table.md`

- [ ] **Step 1: Inspect existing ADR format**

```bash
ls C:/Users/zdm80/Milodex/docs/adr/ | tail -10
head -50 C:/Users/zdm80/Milodex/docs/adr/0011*.md  # event store ADR — closest neighbor
```

Match the existing format (Status, Context, Decision, Consequences, Citations).

- [ ] **Step 2: Write the ADR**

Create `docs/adr/0053-backtest-equity-snapshots-distinct-table.md`:

```markdown
# ADR 0053 — Backtest equity snapshots are a distinct table from broker portfolio snapshots

**Status:** Accepted (2026-05-19)

## Context

`portfolio_snapshots` was originally specified (ADR 0011, `analytics/snapshots.py:1-19`)
as "broker-side account state" — one row per broker snapshot. Over time, a second
writer attached: `BacktestEngine._simulate` started calling `record_daily_snapshot()`
to record simulated equity per walk-forward window, persisting to the same table.

The two writers describe different concepts:
- **Broker portfolio snapshot** — one observation of the real Alpaca paper account.
- **Backtest equity sample** — one point on a simulated equity curve, scoped to a
  walk-forward window of a backtest run.

`PerformanceState._SQL_ALL_PAPER` dedups by `recorded_at` keeping the highest-id row
per timestamp. With both writers present, the earliest dedup-survivor was a
backtest's starting equity (~$1015) and the latest was today's broker equity
(~$101,148). The reported ALL-PAPER return was exactly `(101148.22 / 1015.02) - 1
= +9865.19%`. Peak-to-trough drawdown was -98.99%. Both numbers appeared on the
operator's primary trust surface.

The damage budget for repeating this class of error is zero: the operator's daily
trust check depends on this number being correct.

## Decision

1. `portfolio_snapshots` is now the **broker-side account-state ledger only**.
   The contract is reinstated in `analytics/snapshots.py` docstring.

2. A new table `backtest_equity_snapshots` holds simulated equity points. Schema
   parallels `portfolio_snapshots` plus a first-class `backtest_run_id INTEGER
   REFERENCES backtest_runs(id)` column. New backtests populate this column;
   migrated legacy rows leave it NULL (no reliable mapping from string `:wN`
   suffix).

3. The migration is in-framework SQL (migration 010), atomic via the framework's
   `BEGIN EXCLUSIVE` (see `event_store.py:1254`).

4. Anomalous rows whose provenance can't be cleanly attributed (notably the
   2024-12-31 / $149,315 / no-`:w` row) are **quarantined** to a new
   `portfolio_snapshots_quarantine` table, not deleted. Forensic preservation
   per the operator's principle that historical anomalies are evidence of past
   design failures and must remain inspectable.

5. Future code MUST NOT merge these tables. New writers needing equity snapshots
   for any third concept (live capital? micro-live?) get their own table.

## Consequences

- `BacktestEngine._simulate` swaps its writer call from `record_daily_snapshot`
  to `record_backtest_equity_snapshot`. Existing callers of `record_daily_snapshot`
  (only `StrategyRunner.shutdown`) are unaffected.

- `analytics/reports.py:build_trust_report()` is updated to read backtest equity
  from `list_backtest_equity_snapshots_for_strategy()`. Without this, trust reports
  for backtest strategies would silently lose their snapshot history.

- `PerformanceState._SQL_ALL_PAPER` requires no SQL change post-migration; the
  underlying table is clean by construction (quarantine handles the stray row).

- The `daily_pnl` column is preserved (nullable in the new table) — backtests
  don't track it the same way, but dropping the column would break
  `record_daily_snapshot`'s shared signature.

## Citations

- Complements ADR 0011 (event store as source of truth)
- Reaffirms `analytics/snapshots.py:1-19` docstring contract
- Operator principle: `feedback_inspect_before_deciding` (memory note)
```

- [ ] **Step 3: Run lint** (markdown lint optional; pytest doesn't cover .md files)

- [ ] **Step 4: Commit**

```bash
git -C C:/Users/zdm80/Milodex add docs/adr/0053-backtest-equity-snapshots-distinct-table.md
git -C C:/Users/zdm80/Milodex commit -m "docs(adr): ADR 0053 — backtest equity snapshots distinct table

Documents the schema split landing in migration 010, the rationale, the
forensic quarantine pattern, and the migration contract for future writers."
```

---

### Task 14: Write migration 010 (schema split + data migration + quarantine)

**Files:**
- Create: `src/milodex/core/migrations/010_backtest_equity_snapshots.sql`
- Reference: `src/milodex/core/migrations/008_explanations_backtest_run_id.sql` (the model)

**Pre-flight (Section 9 step 6 in spec):** Verify next migration number.

- [ ] **Step 1: Verify migration number is still 010**

```bash
ls C:/Users/zdm80/Milodex/src/milodex/core/migrations/
```

Confirm the highest existing number is 009. If a new migration landed between spec-write and now (e.g., 010 already exists), use 011 and update PR-7b's migration to 012 accordingly. **Block on this — do NOT use a duplicate number.**

- [ ] **Step 2: Run the anomaly survey (Section 9 step 3)**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('file:C:/Users/zdm80/Milodex/data/milodex.db?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
print('--- non-:w rows with anomalous equity or pre-broker-era dates ---')
for r in conn.execute('''SELECT id, recorded_at, session_id, strategy_id, equity
                        FROM portfolio_snapshots
                        WHERE session_id NOT LIKE \"%:w%\"
                          AND (equity > 500000 OR recorded_at < \"2026-04-27\")
                        ORDER BY recorded_at''').fetchall():
    print(dict(r))
"
```

Record each anomalous row found. The known stray is `session_id='1ee1399f-c393-4cbe-87b1-f38b96747a00'`. If additional rows surface, each gets its own quarantine INSERT/DELETE block with a documented reason.

- [ ] **Step 3: Verify the sub-second-timestamp assumption (Section 9 step 4)**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('file:C:/Users/zdm80/Milodex/data/milodex.db?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
print('--- rows sharing identical recorded_at ---')
rows = conn.execute('''SELECT recorded_at, COUNT(*) cnt, MIN(equity) min_eq, MAX(equity) max_eq
                       FROM portfolio_snapshots
                       WHERE session_id NOT LIKE \"%:w%\"
                       GROUP BY recorded_at
                       HAVING COUNT(*) > 1''').fetchall()
for r in rows:
    print(dict(r))
if not rows:
    print('(none — all live timestamps unique)')
"
```

If multiple rows share an identical `recorded_at` with **different equity values**, dedup-by-recorded_at picks an arbitrary one and is brittle. Document the finding in PR description; the migration itself doesn't need to handle this (it's a read-side concern), but a follow-up may be needed.

- [ ] **Step 4: Write the migration file**

Create `src/milodex/core/migrations/010_backtest_equity_snapshots.sql`:

```sql
-- 010_backtest_equity_snapshots.sql
--
-- ADR 0053: backtest equity snapshots get their own table; portfolio_snapshots
-- becomes broker-only. Migration runs in BEGIN EXCLUSIVE per the framework
-- contract at event_store.py:1254 — idempotent by schema_version gating,
-- atomic with rollback on any failure.

CREATE TABLE IF NOT EXISTS backtest_equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    session_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    backtest_run_id INTEGER REFERENCES backtest_runs(id),
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    portfolio_value REAL NOT NULL,
    daily_pnl REAL,
    positions_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_equity_session
    ON backtest_equity_snapshots (session_id);
CREATE INDEX IF NOT EXISTS idx_backtest_equity_strategy_time
    ON backtest_equity_snapshots (strategy_id, recorded_at);

-- Quarantine table for forensic preservation.
CREATE TABLE IF NOT EXISTS portfolio_snapshots_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    session_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    portfolio_value REAL NOT NULL,
    daily_pnl REAL NOT NULL,
    positions_json TEXT NOT NULL,
    quarantine_reason TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);

-- Move all walk-forward backtest rows.
INSERT INTO backtest_equity_snapshots
    (recorded_at, session_id, strategy_id, backtest_run_id,
     equity, cash, portfolio_value, daily_pnl, positions_json)
SELECT recorded_at, session_id, strategy_id, NULL,
       equity, cash, portfolio_value, daily_pnl, positions_json
FROM portfolio_snapshots
WHERE session_id LIKE '%:w%';

DELETE FROM portfolio_snapshots WHERE session_id LIKE '%:w%';

-- Quarantine the known stray pre-suffix-convention anomaly.
INSERT INTO portfolio_snapshots_quarantine
    (original_id, recorded_at, session_id, strategy_id,
     equity, cash, portfolio_value, daily_pnl, positions_json,
     quarantine_reason, quarantined_at)
SELECT id, recorded_at, session_id, strategy_id,
       equity, cash, portfolio_value, daily_pnl, positions_json,
       'pre-suffix-convention backtest anomaly; equity 149315 predates broker era 2026-04-27',
       datetime('now')
FROM portfolio_snapshots
WHERE session_id = '1ee1399f-c393-4cbe-87b1-f38b96747a00';

DELETE FROM portfolio_snapshots
WHERE session_id = '1ee1399f-c393-4cbe-87b1-f38b96747a00';

-- Additional quarantine blocks (added from Step 2 survey, if any):
-- [if survey found more anomalies, add their session_ids here as INSERT/DELETE pairs
--  with documented reasons]
```

- [ ] **Step 5: Write the migration test FIRST**

In `tests/milodex/core/test_migrations.py` (create if absent), add:

```python
import sqlite3
import pytest
from pathlib import Path
from milodex.core.event_store import EventStore


def _seed_pre_migration_db(db_path: Path):
    """Build a fixture DB representing pre-010 state: mixed :w and non-:w rows
    plus the known stray."""
    conn = sqlite3.connect(str(db_path))
    # Run migrations 001-009 manually (EventStore() can do this if pointed at
    # a fresh DB; mock the schema_version table to claim version 9).
    es = EventStore(db_path)  # runs all migrations up to current head
    # ... insert fixture rows: 3 :w-suffix rows, 2 plain UUID rows, 1 stray with the known session_id ...
    es.close()


def test_010_migration_splits_backtest_and_quarantines_stray(tmp_path):
    db = tmp_path / "milodex.db"
    _seed_pre_migration_db(db)

    es = EventStore(db)  # this triggers 010 if not already at v10
    es.close()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    # Invariant 1: no :w rows remain in portfolio_snapshots
    n = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots WHERE session_id LIKE '%:w%'").fetchone()[0]
    assert n == 0

    # Invariant 2: all :w rows moved to backtest_equity_snapshots
    n_bt = conn.execute("SELECT COUNT(*) FROM backtest_equity_snapshots").fetchone()[0]
    assert n_bt == 3  # matches fixture seed

    # Invariant 3: stray quarantined
    n_q = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots_quarantine WHERE session_id = '1ee1399f-c393-4cbe-87b1-f38b96747a00'").fetchone()[0]
    assert n_q == 1

    # Invariant 4: portfolio_snapshots clean of the stray
    n_stray = conn.execute("SELECT COUNT(*) FROM portfolio_snapshots WHERE session_id = '1ee1399f-c393-4cbe-87b1-f38b96747a00'").fetchone()[0]
    assert n_stray == 0

    conn.close()


def test_010_migration_idempotent(tmp_path):
    db = tmp_path / "milodex.db"
    _seed_pre_migration_db(db)
    EventStore(db).close()  # runs 010 once
    EventStore(db).close()  # second open should be a no-op (schema_version=10)
    # Assert row counts unchanged from single-run state.
    ...
```

- [ ] **Step 6: Run, verify FAIL**

`python -m pytest tests/milodex/core/test_migrations.py::test_010_migration_splits_backtest_and_quarantines_stray -v`

Expected: FAIL (migration file doesn't exist yet, or test setup fails).

- [ ] **Step 7: Place the migration file; framework picks it up automatically**

Confirm the file is in `src/milodex/core/migrations/` alongside the others. The framework discovers migrations by lexical sort, applying any with version > current `schema_version`.

- [ ] **Step 8: Run, verify PASS**

`python -m pytest tests/milodex/core/test_migrations.py -v`

Expected: both new tests PASS.

- [ ] **Step 9: Run the full suite (might surface other breakage)**

`python -m pytest -q`

Expected: any failures here are leaks from Tasks 15-17 not yet done. Note them; they should be addressed in subsequent tasks. If failures are in unrelated suites, investigate immediately.

- [ ] **Step 10: Commit (migration file + tests only — writer + reader changes in next tasks)**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/core/migrations/010_backtest_equity_snapshots.sql tests/milodex/core/test_migrations.py
git -C C:/Users/zdm80/Milodex commit -m "feat(core): migration 010 splits backtest_equity_snapshots, quarantines stray (ADR 0053)

Schema-only change. Subsequent commits redirect the writer and update the
read path. Migration is in-framework (BEGIN EXCLUSIVE) and idempotent by
schema_version gating."
```

---

### Task 15: Add EventStore methods for the new table

**Files:**
- Modify: `src/milodex/core/event_store.py` (add 2 methods)
- Test: `tests/milodex/core/test_event_store.py`

- [ ] **Step 1: Write the failing tests**

In `tests/milodex/core/test_event_store.py`:
```python
def test_append_backtest_equity_snapshot_inserts_row(tmp_path):
    es = EventStore(tmp_path / "milodex.db")
    es.append_backtest_equity_snapshot(
        recorded_at="2026-05-19T00:00:00+00:00",
        session_id="abc-123",
        strategy_id="momentum.daily.tsmom.v1",
        backtest_run_id=42,
        equity=100000.0, cash=80000.0, portfolio_value=100000.0,
        daily_pnl=None, positions_json="[]",
    )
    rows = es.list_backtest_equity_snapshots_for_strategy("momentum.daily.tsmom.v1")
    assert len(rows) == 1
    assert rows[0]["equity"] == 100000.0
    assert rows[0]["backtest_run_id"] == 42
    es.close()


def test_list_backtest_equity_returns_empty_for_unknown_strategy(tmp_path):
    es = EventStore(tmp_path / "milodex.db")
    assert es.list_backtest_equity_snapshots_for_strategy("nope.v1") == []
    es.close()
```

- [ ] **Step 2: Run, verify FAIL**

`python -m pytest tests/milodex/core/test_event_store.py::test_append_backtest_equity_snapshot_inserts_row -v`

Expected: FAIL — methods don't exist.

- [ ] **Step 3: Implement the methods**

In `src/milodex/core/event_store.py`, add (mirror the existing `append_portfolio_snapshot` and `list_portfolio_snapshots_for_strategy` patterns):

```python
def append_backtest_equity_snapshot(
    self,
    *,
    recorded_at: str,
    session_id: str,
    strategy_id: str,
    backtest_run_id: int | None,
    equity: float,
    cash: float,
    portfolio_value: float,
    daily_pnl: float | None,
    positions_json: str,
) -> None:
    """Append a backtest equity sample (ADR 0053). Distinct from broker snapshots."""
    with self._lock:
        self._conn.execute(
            """INSERT INTO backtest_equity_snapshots
               (recorded_at, session_id, strategy_id, backtest_run_id,
                equity, cash, portfolio_value, daily_pnl, positions_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (recorded_at, session_id, strategy_id, backtest_run_id,
             equity, cash, portfolio_value, daily_pnl, positions_json),
        )
        self._conn.commit()


def list_backtest_equity_snapshots_for_strategy(
    self, strategy_id: str
) -> list[dict[str, Any]]:
    """Return all backtest equity samples for the given strategy, oldest first."""
    cur = self._conn.execute(
        """SELECT * FROM backtest_equity_snapshots
           WHERE strategy_id = ?
           ORDER BY recorded_at""",
        (strategy_id,),
    )
    cur.row_factory = sqlite3.Row
    return [dict(r) for r in cur.fetchall()]
```

(Adapt the locking / row_factory pattern to match the file's conventions.)

- [ ] **Step 4: Run tests, verify PASS**

`python -m pytest tests/milodex/core/test_event_store.py -v`

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/core/event_store.py tests/milodex/core/test_event_store.py
git -C C:/Users/zdm80/Milodex commit -m "feat(core): EventStore.append_backtest_equity_snapshot + list (ADR 0053)

Parallels portfolio_snapshots methods; writes to the new backtest_equity_snapshots
table. Required by the analytics/snapshots.py helper redirect in next commit."
```

---

### Task 16: Redirect BacktestEngine writer at the helper level

**Files:**
- Modify: `src/milodex/analytics/snapshots.py` (rewrite docstring; add `record_backtest_equity_snapshot()`)
- Modify: `src/milodex/backtesting/engine.py` (swap helper call in `_simulate`)
- Test: `tests/milodex/analytics/test_snapshots.py`
- Test: `tests/milodex/backtesting/test_engine.py`

- [ ] **Step 1: Add the new helper**

In `src/milodex/analytics/snapshots.py`, add (parallel to `record_daily_snapshot`):

```python
def record_backtest_equity_snapshot(
    event_store: EventStore,
    *,
    recorded_at: datetime,
    session_id: str,
    strategy_id: str,
    backtest_run_id: int | None,
    equity: float,
    cash: float,
    portfolio_value: float,
    daily_pnl: float | None,
    positions: list[dict[str, Any]],
) -> None:
    """Record a simulated portfolio snapshot from a backtest engine.

    ADR 0053: backtest equity samples are persisted to backtest_equity_snapshots,
    NOT portfolio_snapshots. The two tables are owned by distinct concepts
    (simulated vs broker-actual) and MUST NOT be merged.
    """
    event_store.append_backtest_equity_snapshot(
        recorded_at=recorded_at.isoformat(),
        session_id=session_id,
        strategy_id=strategy_id,
        backtest_run_id=backtest_run_id,
        equity=equity,
        cash=cash,
        portfolio_value=portfolio_value,
        daily_pnl=daily_pnl,
        positions_json=json.dumps(positions),
    )
```

- [ ] **Step 2: Rewrite the module docstring**

Replace the existing docstring of `src/milodex/analytics/snapshots.py` (lines 1-19) with:

```python
"""Portfolio snapshot recorders.

ADR 0053: there are TWO distinct snapshot kinds, each with its own writer:

- ``record_daily_snapshot`` writes BROKER-side portfolio snapshots to the
  ``portfolio_snapshots`` table. Called by ``StrategyRunner.shutdown`` at
  session end. Captures the real Alpaca paper account state.

- ``record_backtest_equity_snapshot`` writes SIMULATED equity samples to the
  ``backtest_equity_snapshots`` table. Called by ``BacktestEngine._simulate``
  once per walk-forward window. Captures a point on a simulated equity curve.

DO NOT merge these tables for any reason. They describe different concepts
and any shared persistence is a category error (see ADR 0053 motivation).
"""
```

- [ ] **Step 3: Write the failing test in the backtest engine suite**

In `tests/milodex/backtesting/test_engine.py`:

```python
def test_simulate_writes_only_to_backtest_equity_snapshots(tmp_path):
    """ADR 0053: BacktestEngine._simulate writes to backtest_equity_snapshots,
    NOT portfolio_snapshots."""
    # Set up a minimal fixture: EventStore + strategy config + 1-period series.
    # Run engine._simulate(...).
    # Assert: portfolio_snapshots count delta = 0; backtest_equity_snapshots count delta > 0.
    ...
```

- [ ] **Step 4: Run test, verify FAIL**

`python -m pytest tests/milodex/backtesting/test_engine.py::test_simulate_writes_only_to_backtest_equity_snapshots -v`

Expected: FAIL — engine still calls `record_daily_snapshot`.

- [ ] **Step 5: Swap the helper call in `_simulate`**

In `src/milodex/backtesting/engine.py` around line 872 (verify via grep), change:

```python
record_daily_snapshot(event_store=es, ...)
```
To:
```python
record_backtest_equity_snapshot(event_store=es, backtest_run_id=<resolved from engine context>, ...)
```

The `backtest_run_id` should come from the engine's existing context (it has a `run_id` from `backtest_runs`). If the engine doesn't currently track this, add it as a constructor parameter or resolve from the active run's session_id.

- [ ] **Step 6: Run test, verify PASS**

`python -m pytest tests/milodex/backtesting/test_engine.py::test_simulate_writes_only_to_backtest_equity_snapshots -v`

- [ ] **Step 7: Run full backtesting + analytics tests**

```bash
python -m pytest tests/milodex/backtesting/ tests/milodex/analytics/ -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/analytics/snapshots.py src/milodex/backtesting/engine.py tests/milodex/analytics/test_snapshots.py tests/milodex/backtesting/test_engine.py
git -C C:/Users/zdm80/Milodex commit -m "feat(backtest): redirect _simulate writer to backtest_equity_snapshots (ADR 0053)

Engine now calls record_backtest_equity_snapshot at the analytics/snapshots.py
helper level. portfolio_snapshots is no longer polluted by backtest runs.
Docstring updated to make the broker-only / backtest split explicit."
```

---

### Task 17: Redirect `analytics/reports.py:build_trust_report()` read path

**Files:**
- Modify: `src/milodex/analytics/reports.py` (line ~103)
- Test: `tests/milodex/analytics/test_reports.py`

- [ ] **Step 1: Locate the read site**

```bash
python -c "
with open('src/milodex/analytics/reports.py') as f:
    for i, line in enumerate(f, 1):
        if 'list_portfolio_snapshots' in line or 'snapshot_count' in line or 'build_trust_report' in line:
            print(f'{i}: {line.rstrip()}')
"
```

- [ ] **Step 2: Write the failing test**

In `tests/milodex/analytics/test_reports.py`:

```python
def test_build_trust_report_reads_backtest_equity_for_backtest_strategy(tmp_path):
    """ADR 0053: trust reports for backtest strategies must read from
    backtest_equity_snapshots, not portfolio_snapshots (which is now broker-only)."""
    es = EventStore(tmp_path / "milodex.db")
    # Seed a backtest run + several backtest_equity_snapshots rows for a strategy.
    # Seed ZERO portfolio_snapshots rows for that strategy.
    # Call build_trust_report(strategy_id).
    # Assert: snapshot_count > 0 (reads from the new table).
    ...
```

- [ ] **Step 3: Run, verify FAIL**

`python -m pytest tests/milodex/analytics/test_reports.py::test_build_trust_report_reads_backtest_equity_for_backtest_strategy -v`

- [ ] **Step 4: Swap the read call**

In `src/milodex/analytics/reports.py` around line 103, change:
```python
snapshots = event_store.list_portfolio_snapshots_for_strategy(metrics.strategy_id)
```
To:
```python
snapshots = event_store.list_backtest_equity_snapshots_for_strategy(metrics.strategy_id)
```

(Assumes `build_trust_report` is always called for backtest strategies. If it serves both backtest and live trust reports, branch by context — see surrounding code.)

- [ ] **Step 5: Run, verify PASS**

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/analytics/reports.py tests/milodex/analytics/test_reports.py
git -C C:/Users/zdm80/Milodex commit -m "feat(analytics): redirect trust report read to backtest_equity_snapshots (ADR 0053)

build_trust_report now reads from the new table. Closes the silent-break the
spec flagged as a risk callout."
```

---

### Task 18: Update PerformanceState docstring (no SQL change required)

**Files:**
- Modify: `src/milodex/gui/performance_state.py` (docstring only)

The quarantine migration cleans portfolio_snapshots by construction, so `_SQL_ALL_PAPER` doesn't need a filter clause.

- [ ] **Step 1: Add a docstring sentence**

In `performance_state.py` near `_SQL_ALL_PAPER` or in the module-level docstring, add:

```python
# ADR 0053: portfolio_snapshots is broker-only post-migration 010. Backtest
# equity lives in backtest_equity_snapshots and is read separately by trust
# reports. The :w-suffix filter that used to be required here is unnecessary
# because migration 010 quarantined or moved all non-broker rows.
```

- [ ] **Step 2: Run the performance suite**

```bash
python -m pytest tests/milodex/gui/test_performance_state.py -v
```

Expected: tests still PASS (no behavioral change).

- [ ] **Step 3: Manually verify on dev DB**

After running the migration on the real `data/milodex.db` (which happens on next EventStore construction in the running app), launch the GUI:

```bash
python -m milodex gui
```

Section II ALL-PAPER should show a small positive percentage (~+0.83% at spec time) and a small drawdown — NOT +9865% / -98.99%.

Document the observed values in PR description.

- [ ] **Step 4: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/performance_state.py
git -C C:/Users/zdm80/Milodex commit -m "docs(perf): document ADR 0053 invariant in PerformanceState (no SQL change)

portfolio_snapshots is broker-only post-010; ALL-PAPER query needs no filter."
```

---

### Task 19: Cross-cutting tests — All-Paper invariants

**Files:**
- Modify: `tests/milodex/gui/test_performance_state.py` (extend)

- [ ] **Step 1: Add invariant tests**

```python
def test_all_paper_return_is_realistic_post_migration(tmp_path):
    """Post-migration All-Paper return must be a small honest percentage,
    not the +9865% leak from mixed-source dedup."""
    # Build a fixture DB with: 5 :w backtest rows (1015 → 102000 equity range),
    # 3 broker rows (100318, 100500, 101148), 1 stray (149315 / known session_id).
    # Run migrations through 010.
    # Build PerformanceState; read by_slice["All-Paper"]["return"].
    # Assert: 0.005 < return < 0.02  (small positive, not 100x).


def test_all_paper_drawdown_is_realistic_post_migration(tmp_path):
    # Same fixture; assert -0.05 < drawdown <= 0 (not -0.98).
```

- [ ] **Step 2: Run, verify PASS**

`python -m pytest tests/milodex/gui/test_performance_state.py -v`

- [ ] **Step 3: Commit**

```bash
git -C C:/Users/zdm80/Milodex add tests/milodex/gui/test_performance_state.py
git -C C:/Users/zdm80/Milodex commit -m "test(perf): All-Paper return/drawdown invariants post-ADR-0053 (issue 06)

Regression protection: any future re-merge of backtest+broker series produces
the +9865% / -98.99% pathology these tests reject."
```

---

### Task 20: Promotion-gate audit + final PR-5 verification

**Files (read-only inspection):**
- `src/milodex/promotion/` (entire directory)

**Section 9 step 5:** confirm promotion logic doesn't read `portfolio_snapshots` directly.

- [ ] **Step 1: Grep promotion for any reads**

```bash
python -m grep -r "portfolio_snapshots" src/milodex/promotion/
```

Expected: no results (or only references to `backtest_runs.metadata_json` for computed metrics). If matches surface, audit the reads — they may need redirection to `backtest_equity_snapshots`.

- [ ] **Step 2: Full-codebase audit**

```bash
python -m grep -r "portfolio_snapshots" src/milodex/ --include="*.py"
```

Expected callers:
- `analytics/snapshots.py` — the writer (legitimate)
- `analytics/reports.py` — already redirected in Task 17
- `gui/performance_state.py` — broker-only reads (legitimate post-migration)
- `gui/read_models.py` — display-layer reads (legitimate)
- `core/event_store.py` — table CRUD (legitimate)
- `core/migrations/005_*.sql` and `010_*.sql` (legitimate)

Any other caller is a potential leak. Investigate and redirect if needed.

- [ ] **Step 3: Run the full suite + lint**

```bash
python -m pytest -q
python -m ruff check src/ tests/
```

Expected: full PASS.

### PR-5 boundary

- [ ] **PR-5 ready for review.** Stop and request review before continuing.

---

## PR-6: Ledger Taxonomy + Section VII Expansion (issue 09)

**Goal:** Ledger captures all 6 lifecycle event types. Section VII Activity Feed adds backtest results.

**Scope update (post PR-0):** The `stage_return → RETURNED` ledger plumbing is **already merged in** (see PR-0 above; the idle-stage commit modified `_ledger_entries` to emit `outcomeKind: "returned"` with the "RETURNED" label, and `_latest_promotions` now excludes `stage_return` alongside `demotion`). Task 21 should treat the existing returned/demoted/promoted branches as a fixed baseline and add the four remaining sources (`backtest_complete`, `kill_switch_events`, `session_start`, `session_stop`, `new_strategy`) on top.

---

### Task 21: Extend `_ledger_entries` with 4 new event sources

**Files:**
- Modify: `src/milodex/gui/read_models.py` (around `_ledger_entries`, line 1028)
- Test: `tests/milodex/gui/test_read_models.py`

- [ ] **Step 1: Refactor existing logic into helpers**

In `read_models.py`, refactor `_ledger_entries` to delegate to per-source helpers:

```python
def _ledger_entries(db_path: Path, configs_dir: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        entries = []
        entries += _promotion_entries(conn)
        entries += _kill_switch_entries(conn)
        entries += _session_start_entries(conn)
        entries += _session_stop_entries(conn)
        entries += _backtest_complete_entries(conn)
        entries += _new_strategy_entries(conn, configs_dir)
    finally:
        conn.close()
    return sorted(entries, key=lambda e: str(e.get("timestamp") or ""), reverse=True)
```

Move existing promotion/kill_switch logic into `_promotion_entries(conn)` and `_kill_switch_entries(conn)`.

- [ ] **Step 2: Write `_session_start_entries`**

```python
def _session_start_entries(conn) -> list[dict[str, Any]]:
    entries = []
    for row in conn.execute("""
        SELECT strategy_id, started_at, session_id
        FROM strategy_runs
        WHERE started_at IS NOT NULL
        ORDER BY started_at DESC LIMIT 200
    """):
        entries.append({
            "timestamp": row["started_at"],
            "displayTimestamp": _compact_timestamp(str(row["started_at"])),
            "strategyId": row["strategy_id"],
            "subject": _short_strategy_name(row["strategy_id"]),
            "stage": "lifecycle",
            "transition": "session",
            "outcome": "STARTED",
            "outcomeKind": "started",
            "reason": "trading session began",
            "recent": True,
        })
    return entries
```

- [ ] **Step 3: Write `_session_stop_entries` with the kill-switch + orphan-recovered dedup**

```python
def _session_stop_entries(conn) -> list[dict[str, Any]]:
    """Sessions ended for operator-initiated reasons. EXCLUDES kill-switch
    and orphan-recovered closures (those emit their own ledger rows from
    kill_switch_events or are synthetic reconciliation rows)."""
    entries = []
    for row in conn.execute("""
        SELECT strategy_id, ended_at, exit_reason, session_id
        FROM strategy_runs
        WHERE ended_at IS NOT NULL
          AND exit_reason NOT IN ('kill_switch', 'orphan_recovered')
        ORDER BY ended_at DESC LIMIT 200
    """):
        entries.append({
            "timestamp": row["ended_at"],
            "displayTimestamp": _compact_timestamp(str(row["ended_at"])),
            "strategyId": row["strategy_id"],
            "subject": _short_strategy_name(row["strategy_id"]),
            "stage": "lifecycle",
            "transition": "session",
            "outcome": "STOPPED",
            "outcomeKind": "stopped",
            "reason": row["exit_reason"] or "stopped",
            "recent": True,
        })
    return entries
```

- [ ] **Step 4: Write `_backtest_complete_entries` with correct schema**

```python
def _backtest_complete_entries(conn) -> list[dict[str, Any]]:
    """Per ADR 0054 (Section C review): actual columns are `ended_at`, `status`,
    `metadata_json` — NOT `completed_at` / `metrics_json`. Sharpe lives at
    metadata_json.oos_aggregate.sharpe."""
    from milodex.promotion.policy import ACTIVE_PROMOTION_POLICY
    paper_gate = ACTIVE_PROMOTION_POLICY.paper_gate.min_sharpe  # 0.0
    capital_gate = ACTIVE_PROMOTION_POLICY.capital_gate.min_sharpe  # 0.5

    entries = []
    for row in conn.execute("""
        SELECT id, strategy_id, ended_at, status,
               json_extract(metadata_json, '$.oos_aggregate.sharpe') AS sharpe,
               json_extract(metadata_json, '$.oos_aggregate.max_drawdown_pct') AS max_dd,
               json_extract(metadata_json, '$.oos_aggregate.trade_count') AS n
        FROM backtest_runs
        WHERE status = 'completed' AND ended_at IS NOT NULL
        ORDER BY ended_at DESC LIMIT 200
    """):
        sharpe = row["sharpe"]
        # Three-tone color binding to policy thresholds:
        if sharpe is None:
            kind = "backtested"  # neutral
        elif sharpe >= capital_gate:
            kind = "backtested_strong"
        elif sharpe >= paper_gate:
            kind = "backtested_paper"
        else:
            kind = "backtested_weak"

        reason_parts = []
        if sharpe is not None:
            reason_parts.append(f"Sharpe {sharpe:.2f}")
        if row["max_dd"] is not None:
            reason_parts.append(f"max-dd {abs(row['max_dd'])*100:.1f}%")
        if row["n"] is not None:
            reason_parts.append(f"n={row['n']}")
        reason = " · ".join(reason_parts) or "completed"

        entries.append({
            "timestamp": row["ended_at"],
            "displayTimestamp": _compact_timestamp(str(row["ended_at"])),
            "strategyId": row["strategy_id"],
            "subject": _short_strategy_name(row["strategy_id"]),
            "stage": "backtest",
            "transition": "backtest",
            "outcome": "COMPLETED",
            "outcomeKind": kind,
            "reason": reason,
            "recent": True,
        })
    return entries
```

- [ ] **Step 5: Write `_new_strategy_entries` with MIN-recorded_at-across-event-tables**

```python
def _new_strategy_entries(conn, configs_dir: Path) -> list[dict[str, Any]]:
    """First appearance per strategy_id across event tables. YAML mtime
    fallback only for strategies with no event-store history."""
    rows = conn.execute("""
        WITH first_seen AS (
            SELECT strategy_id, recorded_at AS first_at FROM promotions
            UNION ALL
            SELECT strategy_id, started_at FROM strategy_runs WHERE started_at IS NOT NULL
            UNION ALL
            SELECT strategy_id, started_at FROM backtest_runs WHERE started_at IS NOT NULL
        )
        SELECT strategy_id, MIN(first_at) AS first_at FROM first_seen GROUP BY strategy_id
    """).fetchall()
    seen_with_history = {row["strategy_id"]: row["first_at"] for row in rows}

    entries = []
    for sid, first_at in seen_with_history.items():
        entries.append({
            "timestamp": first_at,
            "displayTimestamp": _compact_timestamp(str(first_at)),
            "strategyId": sid,
            "subject": _short_strategy_name(sid),
            "stage": "system",
            "transition": "registration",
            "outcome": "ADDED",
            "outcomeKind": "added",
            "reason": "strategy first appeared in event store",
            "recent": True,
        })

    # Fallback for strategies present in configs/ but with no event-store ancestry.
    for yaml_path in Path(configs_dir).glob("*.yaml"):
        # Parse the YAML's strategy_id (project convention; check existing loader).
        sid = _strategy_id_from_yaml(yaml_path)
        if sid and sid not in seen_with_history:
            mtime_iso = datetime.fromtimestamp(yaml_path.stat().st_mtime, tz=timezone.utc).isoformat()
            entries.append({
                "timestamp": mtime_iso,
                "displayTimestamp": _compact_timestamp(mtime_iso),
                "strategyId": sid,
                "subject": _short_strategy_name(sid),
                "stage": "system",
                "transition": "registration",
                "outcome": "ADDED",
                "outcomeKind": "added",
                "reason": "config file mtime (no event-store history)",
                "recent": True,
            })
    return entries
```

- [ ] **Step 6: Write the unified test**

In `tests/milodex/gui/test_read_models.py`:

```python
def test_ledger_entries_includes_all_six_event_types(tmp_path):
    """Ledger sources: promotions, kill_switch, session start, session stop,
    backtest completion, new strategy."""
    # Seed fixture DB with one of each event type.
    # Call _ledger_entries(db_path, configs_dir).
    # Assert: 6 entries, one per outcomeKind in {'promoted', 'fired',
    # 'started', 'stopped', 'backtested_*', 'added'}.
    # Assert sort: DESC by timestamp.


def test_kill_switch_does_not_emit_stop_row(tmp_path):
    """A session ended via kill_switch must NOT emit a 'STOPPED' row;
    the kill_switch_events row stands alone."""
    # Seed: strategy_runs row with ended_at + exit_reason='kill_switch',
    # plus kill_switch_events row at same timestamp.
    # Assert: only 1 entry for this event (the 'fired' kind, not 'stopped').
```

- [ ] **Step 7: Run, verify FAIL (helpers don't exist yet)**

`python -m pytest tests/milodex/gui/test_read_models.py::test_ledger_entries_includes_all_six_event_types -v`

- [ ] **Step 8: Run, verify PASS after implementation lands**

- [ ] **Step 9: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/read_models.py tests/milodex/gui/test_read_models.py
git -C C:/Users/zdm80/Milodex commit -m "feat(ledger): expand event taxonomy to 6 sources (issue 09)

Adds session start, session stop (kill-switch-filtered), backtest completed
(with metric tagging via policy.py thresholds), and new strategy added
(MIN-recorded-at-across-event-tables with YAML mtime fallback). Existing
promotion + kill_switch logic refactored into helpers."
```

---

### Task 22: Update LedgerSurface filter UI and outcomeKind palette

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/surfaces/LedgerSurface.qml` (filter chips around lines 144-195; outcomeKind coloring elsewhere)

- [ ] **Step 1: Inspect current filter structure**

Open LedgerSurface.qml, find the existing chip Repeater (currently flat 5 chips per Section C review).

- [ ] **Step 2: Reorganize into two-row grouped layout**

Replace the single chip row with two rows:
- Row 1 — outcome groups: `Promotion`, `Lifecycle`, `Backtest`, `System`
- Row 2 — existing time-range/stage filters

Group → kinds mapping (filter logic in QML or via `LedgerState.setLedgerFilter`):
- Promotion: outcomeKind IN ('promoted', 'demoted', 'returned')
- Lifecycle: outcomeKind IN ('started', 'stopped')
- Backtest: outcomeKind IN ('backtested', 'backtested_strong', 'backtested_paper', 'backtested_weak')
- System: outcomeKind IN ('fired', 'info', 'added')

When a group is selected, filter includes all kinds in that group. (Per spec: no sub-row expansion.)

- [ ] **Step 3: Extend outcomeKind → color mapping**

Find the existing color resolution (likely a JS-like function or switch in QML). Add:
- `started` → `Theme.color.status.info` (or similar bright variant)
- `stopped` → `Theme.color.text.secondary` (muted)
- `backtested_strong` → `Theme.color.status.positive` (sage)
- `backtested_paper` → `Theme.color.status.neutral` (or text.primary)
- `backtested_weak` → `Theme.color.status.negative` (rust)
- `backtested` (fallback for null sharpe) → `Theme.color.text.muted`
- `added` → `Theme.color.text.muted`

- [ ] **Step 4: Smoke verify QML loads**

`python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

- [ ] **Step 5: Visual verification**

Launch GUI; navigate to Ledger. Verify two filter rows; all chips render. Click each group filter and observe entries shrink to that group's kinds.

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/surfaces/LedgerSurface.qml
git -C C:/Users/zdm80/Milodex commit -m "feat(ledger): two-row grouped filter + new outcomeKind palette (issue 09)

Four groups (Promotion, Lifecycle, Backtest, System) replace the flat 5-chip
row that would have overflowed at 9+ chips. Color mapping extended for
started/stopped/backtested_*/added. Sharpe threshold coloring binds to
policy.py constants (paper_gate=0.0, capital_gate=0.5)."
```

---

### Task 23: Expand Section VII (ActivityFeedState) with backtest results

**Files:**
- Modify: `src/milodex/gui/activity_feed_state.py` (add backtest_runs as third source)
- Modify: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` Section VII (add BACKTESTS filter chip)
- Test: `tests/milodex/gui/test_activity_feed_state.py`

- [ ] **Step 1: Inspect current activity-feed sources**

```bash
python -c "
with open('src/milodex/gui/activity_feed_state.py') as f:
    print(f.read())
" | head -100
```

Identify the existing query/build pattern (explanations + paper trades).

- [ ] **Step 2: Write the failing test**

```python
def test_activity_feed_includes_backtest_results(tmp_path):
    """Section VII shows backtest results alongside orders/signals."""
    # Seed: one completed backtest_runs row + one explanations + one trade.
    # Build ActivityFeedState; read entries.
    # Assert: at least one entry with kind='backtest'.
```

- [ ] **Step 3: Run, verify FAIL**

- [ ] **Step 4: Add backtest_runs as a source in ActivityFeedState**

Add a SELECT against `backtest_runs WHERE status = 'completed'`, emit per-row entries shaped like other feed entries with `kind='backtest'`, `outcome='COMPLETED'`, metric summary in the reason field.

- [ ] **Step 5: Add BACKTESTS filter chip to Section VII**

In DeskSurface.qml Section VII (around lines 772-852), find the existing kind-filter toggle (All/Orders/Rejections/Signals/Fills). Add a "BACKTESTS" toggle.

- [ ] **Step 6: Run, verify PASS**

- [ ] **Step 7: Smoke + visual verify**

- [ ] **Step 8: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/activity_feed_state.py src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml tests/milodex/gui/test_activity_feed_state.py
git -C C:/Users/zdm80/Milodex commit -m "feat(desk): Section VII shows backtest results (issue 09 expansion)

Per operator clarification: 'every backtest result' belongs in Section VII
alongside orders/signals/fills/rejections. New BACKTESTS kind-filter chip."
```

---

### Task 24: Cross-source timestamp ordering verification (Section 9 step 7)

**Files (test only):**
- `tests/milodex/gui/test_read_models.py`

- [ ] **Step 1: Write the cross-source ordering test**

```python
def test_kill_switch_orders_above_session_stop_when_simultaneous(tmp_path):
    """When kill-switch fires and strategy_runs.ended_at write happen within ms
    of each other, the kill_switch_events entry must sort above the (excluded)
    stop entry — but the stop entry doesn't appear (excluded by filter), so the
    assertion here is that the kill_switch row appears and no STOPPED row
    appears for the same session."""
    # Seed: simulated kill-switch fire at recorded_at T;
    # strategy_runs.ended_at = T + 5ms with exit_reason='kill_switch'.
    # Build ledger; assert only one entry for this session ('fired').
```

- [ ] **Step 2: Run, verify PASS** (relies on Task 21's exit_reason filter)

- [ ] **Step 3: Document timestamp precision in PR description**

Note in PR description: "Timestamp precision across sources verified consistent (ISO 8601 UTC throughout). If a future writer emits a different precision, the sort may need re-verification."

- [ ] **Step 4: Commit**

```bash
git -C C:/Users/zdm80/Milodex add tests/milodex/gui/test_read_models.py
git -C C:/Users/zdm80/Milodex commit -m "test(ledger): kill-switch dedup integration test (Section 9 step 7)

Asserts kill-switch fire doesn't produce both a 'fired' and 'stopped' ledger
row when the session_runs row gets ended_at simultaneously."
```

---

### PR-6 boundary

- [ ] **Run full suite + lint, then stop for review.**

---

## PR-7a: Risk-Profile Model + ADR 0054 (issue 10, foundation)

**Goal:** Land the doctrine. Risk profiles exist as configs + loader + code-level ceilings. `execution/service.py` consumes the active profile. ADR 0054 documents the system. NO GUI work yet.

**Hard order:** ADR first (Task 25), then ceilings + profiles (Task 26), then loader (Task 27), then integration (Task 28), then verification (Task 29).

---

### Task 25: Write ADR 0054

**Files:**
- Create: `docs/adr/0054-risk-profiles-bounded-operator-preferences.md`

- [ ] **Step 1: Inspect existing ADR format** (same as Task 13 Step 1).

- [ ] **Step 2: Write ADR 0054**

Use the spec's Section 6 §ADR 0054 sketch as the authoritative source. Expand each numbered decision into a paragraph. Cite:
- `docs/FOUNDER_INTENT.md:131` (safe-by-default rule)
- `docs/FOUNDER_INTENT.md:137` ("the operator cannot disable the floor")
- CLAUDE.md "Risk layer is sacred" and "Operator owns preferences, risk layer owns enforcement"
- ADR 0011 (event store)

The 10 numbered decisions from the spec MUST appear verbatim (or paraphrased with equivalent semantics). Especially decision §3 (backtest exemption), §5 (refuse mid-flight), §6 (refuse during triggered kill switch), §8 ("visibly active" with persistent banner for Aggressive), §10 (no strategy/ML/agent may switch profiles).

- [ ] **Step 3: Commit**

```bash
git -C C:/Users/zdm80/Milodex add docs/adr/0054-risk-profiles-bounded-operator-preferences.md
git -C C:/Users/zdm80/Milodex commit -m "docs(adr): ADR 0054 — risk profiles are bounded operator preferences

First risk-policy mutation surface in Milodex. Codifies safe-default rule,
code-level absolute ceilings (not editable YAML), refuse-mid-flight policy,
refuse-during-triggered-kill-switch policy, persistent-banner visibility
for elevated postures, and the explicit denial that any strategy/ML/agent
may select or switch profiles."
```

---

### Task 26: Add `_ABSOLUTE_CEILINGS` code constants + three profile YAMLs

**Files:**
- Modify: `src/milodex/risk/config.py` (add constants block)
- Create: `configs/risk_profiles/conservative.yaml`
- Create: `configs/risk_profiles/standard.yaml`
- Create: `configs/risk_profiles/aggressive.yaml`
- Test: `tests/milodex/risk/test_config.py`

- [ ] **Step 1: Write the failing ceiling test**

In `tests/milodex/risk/test_config.py`:

```python
def test_absolute_ceilings_defined():
    from milodex.risk.config import _ABSOLUTE_CEILINGS
    assert "kill_switch.max_drawdown_pct" in _ABSOLUTE_CEILINGS
    assert "portfolio.max_total_exposure_pct" in _ABSOLUTE_CEILINGS
    assert "daily_limits.max_daily_loss_pct" in _ABSOLUTE_CEILINGS
    # Verify against documented justification:
    assert _ABSOLUTE_CEILINGS["kill_switch.max_drawdown_pct"] == 0.20
    assert _ABSOLUTE_CEILINGS["portfolio.max_total_exposure_pct"] == 0.85
    assert _ABSOLUTE_CEILINGS["daily_limits.max_daily_loss_pct"] == 0.08


def test_all_three_shipped_profiles_pass_ceiling_validation(tmp_path):
    """Aggressive.yaml MUST be within ceilings; this test guards against
    accidentally raising aggressive past the documented absolute maxima."""
    from milodex.risk.config import _load_overlay, _validate_against_ceilings, _merge
    base_path = Path("configs/risk_defaults.yaml")
    for profile_name in ["conservative", "standard", "aggressive"]:
        overlay = _load_overlay(profile_name)
        with open(base_path) as f:
            base = yaml.safe_load(f)
        merged = _merge(base, overlay)
        _validate_against_ceilings(merged)  # raises on violation
```

- [ ] **Step 2: Run, verify FAIL**

`python -m pytest tests/milodex/risk/test_config.py -v`

Expected: FAIL (constants and functions don't exist).

- [ ] **Step 3: Add the constants block**

In `src/milodex/risk/config.py`, near the top:

```python
# Account-level absolute ceilings. NOT EDITABLE. Per ADR 0054 and
# FOUNDER_INTENT.md "the operator cannot disable the floor."
#
# Each value is the maximum permitted across ANY risk profile.
#
# Justification (revisit only via ADR amendment):
# - kill_switch.max_drawdown_pct = 0.20: above Aggressive's 0.15 by a safety
#   margin, well below the 25% institutional pension-fund tolerance band.
#   Sub-$1k Phase-1 capital — a 20% drawdown is $200, recoverable.
# - portfolio.max_total_exposure_pct = 0.85: above Aggressive's 0.75; keeps
#   minimum 15% cash buffer regardless of profile.
# - daily_limits.max_daily_loss_pct = 0.08: above Aggressive's 0.05; single-
#   session loss never exceeds 8% even under elevated posture.
_ABSOLUTE_CEILINGS: dict[str, float] = {
    "kill_switch.max_drawdown_pct": 0.20,
    "portfolio.max_total_exposure_pct": 0.85,
    "daily_limits.max_daily_loss_pct": 0.08,
}


class CeilingViolation(RuntimeError):
    """Raised when a risk profile's resolved values exceed an account-level ceiling."""
```

- [ ] **Step 4: Create the three overlay YAMLs**

`configs/risk_profiles/conservative.yaml`:
```yaml
# Conservative risk profile — overlay on configs/risk_defaults.yaml.
# Default profile per ADR 0054 §2 (safe-by-default).
kill_switch:
  max_drawdown_pct: 0.05
portfolio:
  max_total_exposure_pct: 0.30
  max_concurrent_positions: 5
daily_limits:
  max_daily_loss_pct: 0.02
```

`configs/risk_profiles/standard.yaml`:
```yaml
# Standard risk profile — identity overlay (mirrors configs/risk_defaults.yaml).
# Empty by design.
{}
```

`configs/risk_profiles/aggressive.yaml`:
```yaml
# Aggressive risk profile — overlay on configs/risk_defaults.yaml.
# Bounded by code-level _ABSOLUTE_CEILINGS in src/milodex/risk/config.py.
kill_switch:
  max_drawdown_pct: 0.15
portfolio:
  max_total_exposure_pct: 0.75
  max_concurrent_positions: 15
daily_limits:
  max_daily_loss_pct: 0.05
```

- [ ] **Step 5: Run tests, verify PASS**

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add configs/risk_profiles/ src/milodex/risk/config.py tests/milodex/risk/test_config.py
git -C C:/Users/zdm80/Milodex commit -m "feat(risk): three risk-profile overlays + code-level absolute ceilings (ADR 0054)

Conservative/Standard/Aggressive YAML overlays + _ABSOLUTE_CEILINGS code
constants (not editable YAML) per founder intent 'operator cannot disable
the floor'. Conservative is the safe default. Ceiling values justified
in comment block; revisit only via ADR amendment."
```

---

### Task 27: Add loader functions

**Files:**
- Modify: `src/milodex/risk/config.py` (add functions)
- Test: `tests/milodex/risk/test_config.py`

- [ ] **Step 1: Write failing tests for all three fallback cases**

```python
def test_default_to_conservative_when_file_absent(tmp_path, monkeypatch):
    """ADR 0054: missing data/risk_profile.txt → silently default to conservative."""
    monkeypatch.chdir(tmp_path)  # ensures data/ is empty
    from milodex.risk.config import get_active_profile_name
    assert get_active_profile_name() == "conservative"


def test_malformed_profile_falls_back_to_conservative_with_warning(tmp_path, monkeypatch, caplog):
    """Unknown profile name → fallback + loud warning + (audit row written separately)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "risk_profile.txt").write_text("nonexistent_profile\n")
    from milodex.risk.config import load_active_risk_profile
    profile = load_active_risk_profile()
    assert "max_drawdown_pct" in profile["kill_switch"]
    # Loaded conservative
    assert profile["kill_switch"]["max_drawdown_pct"] == 0.05
    # Warning emitted
    assert any("malformed" in rec.message.lower() or "unknown" in rec.message.lower()
               for rec in caplog.records)


def test_ceiling_violation_refuses_startup(tmp_path, monkeypatch):
    """Profile that exceeds a ceiling → refuse to load."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs" / "risk_profiles").mkdir(parents=True)
    (tmp_path / "configs" / "risk_profiles" / "badprofile.yaml").write_text(
        "kill_switch:\n  max_drawdown_pct: 0.99\n"  # exceeds ceiling 0.20
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "risk_profile.txt").write_text("badprofile\n")
    # also need a configs/risk_defaults.yaml fixture
    from milodex.risk.config import load_active_risk_profile, CeilingViolation
    with pytest.raises(CeilingViolation):
        load_active_risk_profile()
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement the loader**

```python
import yaml
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def get_active_profile_name() -> str:
    """Read data/risk_profile.txt; fallback to 'conservative' if absent/unreadable."""
    profile_file = Path("data/risk_profile.txt")
    if not profile_file.exists():
        return "conservative"
    try:
        return profile_file.read_text(encoding="utf-8").strip().lower() or "conservative"
    except (OSError, UnicodeDecodeError):
        return "conservative"


def _load_overlay(profile_name: str) -> dict:
    """Read configs/risk_profiles/{name}.yaml; return parsed dict or {}."""
    path = Path("configs/risk_profiles") / f"{profile_name}.yaml"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge; overlay wins."""
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _get_by_path(d: dict, path: str):
    """Walk a dotted path through nested dicts. Returns None if any segment missing."""
    parts = path.split(".")
    cur = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _validate_against_ceilings(profile: dict) -> None:
    """Raise CeilingViolation if any path in _ABSOLUTE_CEILINGS is exceeded."""
    for path, ceiling in _ABSOLUTE_CEILINGS.items():
        value = _get_by_path(profile, path)
        if value is not None and value > ceiling:
            raise CeilingViolation(
                f"Profile value {path}={value} exceeds account-level ceiling {ceiling}. "
                f"Edit the active overlay in configs/risk_profiles/ to comply."
            )


def load_active_risk_profile() -> dict:
    """Load risk_defaults.yaml + active overlay, validate, return merged dict.

    Three-case fallback behavior per ADR 0054:
    - missing data/risk_profile.txt → silently default to conservative.
    - unknown profile name / malformed YAML → fallback to conservative + WARNING.
    - resolved values exceed ceilings → raise CeilingViolation (refuse startup).
    """
    base_path = Path("configs/risk_defaults.yaml")
    with open(base_path) as f:
        base = yaml.safe_load(f)

    active = get_active_profile_name()
    available = {"conservative", "standard", "aggressive"}

    if active not in available:
        logger.warning(
            "Risk profile %r unknown; falling back to conservative. "
            "Edit data/risk_profile.txt to one of: %s",
            active, ", ".join(sorted(available)),
        )
        active = "conservative"

    overlay = _load_overlay(active)
    merged = _merge(base, overlay)
    _validate_against_ceilings(merged)
    return merged
```

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/risk/config.py tests/milodex/risk/test_config.py
git -C C:/Users/zdm80/Milodex commit -m "feat(risk): load_active_risk_profile with three-case fallback (ADR 0054)

- Missing data/risk_profile.txt → silent default to conservative
- Unknown profile name / malformed YAML → fallback to conservative + warning
- Ceiling violation → raise CeilingViolation (refuse startup)

Asymmetric severity matches the asymmetric meaning of each failure mode."
```

---

### Task 28: Migrate execution/service.py to consume the active profile

**Files:**
- Modify: `src/milodex/execution/service.py` (line 376)
- Test: `tests/milodex/risk/test_runtime_consumer_routes_through_active_profile.py`

- [ ] **Step 1: Write the critical integration test FIRST**

```python
# tests/milodex/risk/test_runtime_consumer_routes_through_active_profile.py

def test_execution_service_uses_active_profile(tmp_path, monkeypatch):
    """ADR 0054 + spec §6 PR-7a runtime consumer integration: the active risk
    profile MUST flow through execution/service.py's evaluation. Without this
    test passing, the profile system is non-functional even though config
    loads correctly."""
    monkeypatch.chdir(tmp_path)
    # Set up project layout under tmp_path: configs/risk_defaults.yaml,
    # configs/risk_profiles/{conservative,standard,aggressive}.yaml,
    # data/risk_profile.txt='conservative'.

    # Construct an ExecutionService with realistic deps.
    # Invoke whatever code path runs the risk evaluator (likely a single
    # method call evaluating a fake intent).

    # Inspect the resolved limits used by the evaluator: assert
    # max_drawdown_pct == 0.05 (conservative), not 0.10 (base default).

    # Switch active profile to 'aggressive' (rewrite risk_profile.txt).
    # Re-evaluate. Assert max_drawdown_pct == 0.15.
```

The exact mechanism for inspecting "resolved limits used by evaluator" depends on `execution/service.py` internals; may need a spy or fixture pattern.

- [ ] **Step 2: Run, verify FAIL**

`python -m pytest tests/milodex/risk/test_runtime_consumer_routes_through_active_profile.py -v`

Expected: FAIL (service still calls `load_risk_defaults`).

- [ ] **Step 3: Migrate the call**

In `src/milodex/execution/service.py:376`, change:
```python
risk_defaults=load_risk_defaults(self._risk_defaults_path),
```
To:
```python
risk_defaults=load_active_risk_profile(),
```

Add the import: `from milodex.risk.config import load_active_risk_profile`.

The function signature it feeds into (RiskDefaults dataclass or dict-shaped) may need to handle a dict (since `load_active_risk_profile()` returns a dict, while `load_risk_defaults()` returned a `RiskDefaults` instance). If so, either:
- (a) Add `RiskDefaults.from_dict(d)` and call `RiskDefaults.from_dict(load_active_risk_profile())`
- (b) Refactor `load_active_risk_profile()` to return `RiskDefaults` (preferred — same shape as the legacy loader)

Use (b) — refactor `load_active_risk_profile()` to return a `RiskDefaults` instance so its return type matches the legacy `load_risk_defaults()` signature. This avoids touching every downstream consumer.

- [ ] **Step 4: Run test, verify PASS**

- [ ] **Step 5: Audit other risk_defaults consumers**

`python -m grep "load_risk_defaults\|risk_defaults_path" src/milodex/`

For each result, classify:
- `execution/service.py` — DONE (migrated above)
- `backtesting/engine.py:175-182` — INTENTIONAL base read per ADR 0054 §3. Leave alone.
- `cli/config_validation.py` — config validation only. Leave alone.
- `gui/read_models.py` — display layer. Consider migrating to read the active profile so the UI shows what's actually enforced (defer to PR-7c if not trivial here).
- `cli/commands/research.py` — audit during this task. If it runs trades, must migrate; if analysis-only, leave alone.
- `risk/__init__.py`, `risk/config.py` — definitions; not consumers.
- `risk/evaluator.py` — downstream of execution/service; receives config via parameter, doesn't read directly. Leave alone.
- `execution/config.py` — audit; likely just the path constant.

Document the audit results in PR description.

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/execution/service.py tests/milodex/risk/test_runtime_consumer_routes_through_active_profile.py
git -C C:/Users/zdm80/Milodex commit -m "feat(execution): consume active risk profile, not base defaults (ADR 0054)

execution/service.py:376 was calling load_risk_defaults(path) directly,
bypassing the new profile system. Switched to load_active_risk_profile().
This is the critical runtime integration — without it, PR-7a's profile
system would not actually affect enforcement.

Backtest engine intentionally retained on the base (ADR 0054 §3:
backtests evaluate strategy potential, not current operator posture)."
```

---

### Task 29: Final PR-7a verification + lint

- [ ] **Step 1: Run full suite**

```bash
python -m pytest -q
```

- [ ] **Step 2: Run lint**

```bash
python -m ruff check src/ tests/
```

- [ ] **Step 3: Manual smoke**

Launch GUI; nothing visible should change (PR-7a has no GUI work). But internally, the active profile is now Conservative on fresh install. Verify by inspecting `data/risk_profile.txt` (should not exist on a clean clone, but if it does, contents matter).

### PR-7a boundary

- [ ] **PR-7a ready for review.** Stop and request review.

---

## PR-7b: Risk-Profile Audit Table + Bridge

**Goal:** Migration 011 creates `risk_profile_changes`. New `risk_profile_bridge.py` enforces switch rules. No GUI work yet.

---

### Task 30: Write migration 011

**Files:**
- Create: `src/milodex/core/migrations/011_risk_profile_changes.sql`
- Test: `tests/milodex/core/test_migrations.py`

- [ ] **Step 1: Verify migration number is still 011**

```bash
ls C:/Users/zdm80/Milodex/src/milodex/core/migrations/
```

PR-5 should have landed 010 by now. The next number is 011. If something else landed in between, bump accordingly.

- [ ] **Step 2: Write the SQL**

`src/milodex/core/migrations/011_risk_profile_changes.sql`:

```sql
-- 011_risk_profile_changes.sql
--
-- ADR 0054: audit table for risk-profile switches and startup defaults.
-- Every change (successful, refused, or implicit-startup) writes one row.

CREATE TABLE IF NOT EXISTS risk_profile_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    from_profile TEXT NOT NULL,
    to_profile TEXT NOT NULL,
    actor TEXT NOT NULL,                   -- 'gui' | 'cli' | 'startup'
    confirmation_method TEXT NOT NULL,     -- 'typed' | 'single_click' | 'none'
    context_mode TEXT NOT NULL,            -- 'paper' | 'micro_live' | 'live'
    runners_active_count INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL,              -- 1 = applied, 0 = refused/failed
    failure_reason TEXT                    -- nullable; populated when success=0
);

CREATE INDEX IF NOT EXISTS idx_risk_profile_changes_time
    ON risk_profile_changes (recorded_at);
```

- [ ] **Step 3: Write the migration test**

In `tests/milodex/core/test_migrations.py`:

```python
def test_011_creates_risk_profile_changes_table(tmp_path):
    es = EventStore(tmp_path / "milodex.db")
    conn = sqlite3.connect(str(tmp_path / "milodex.db"))
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='risk_profile_changes'")
    assert cur.fetchone() is not None
    cur = conn.execute("PRAGMA table_info(risk_profile_changes)")
    cols = {row[1] for row in cur.fetchall()}
    assert {"from_profile", "to_profile", "actor", "confirmation_method",
            "context_mode", "runners_active_count", "success", "failure_reason"} <= cols
    es.close()
```

- [ ] **Step 4: Run, verify PASS** (after placing migration file)

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/core/migrations/011_risk_profile_changes.sql tests/milodex/core/test_migrations.py
git -C C:/Users/zdm80/Milodex commit -m "feat(core): migration 011 — risk_profile_changes audit table (ADR 0054)

Schema includes runners_active_count for invariant verification and a
failure_reason column for forensic record of refused switches."
```

---

### Task 31: Implement `risk_profile_bridge.py`

**Files:**
- Create: `src/milodex/gui/risk_profile_bridge.py`
- Test: `tests/milodex/gui/test_risk_profile_bridge.py`

- [ ] **Step 1: Write failing tests for all refusal cases**

```python
def test_refuse_when_runners_active(tmp_path, monkeypatch):
    """Mid-flight switch is refused per ADR 0054 §5."""
    # Set up DB with one open strategy_runs row (ended_at IS NULL).
    # Construct bridge; attempt switch.
    # Assert: returns False; switchRefused signal emitted with reason='active_runners';
    # audit row written with success=0, failure_reason='active_runners',
    # runners_active_count=1.


def test_refuse_when_kill_switch_triggered_unresolved(tmp_path):
    """Switch refused while a triggered kill switch is unreset per ADR 0054 §6."""


def test_refuse_typed_confirmation_mismatch_case_insensitive(tmp_path):
    """Typed confirmation must match profile name case-insensitively, trimmed.
    'AGGRESSIVE  ' should match 'aggressive'."""


def test_refuse_when_target_profile_unknown(tmp_path):
    """Switching to a non-shipped profile name is refused."""


def test_successful_switch_writes_atomic_file_and_audit(tmp_path):
    """Successful switch: data/risk_profile.txt rewritten atomically; audit row
    has success=1."""


def test_startup_implicit_default_writes_audit_row(tmp_path):
    """When the app starts without data/risk_profile.txt, an audit row with
    actor='startup', confirmation_method='none' is written."""
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement the bridge**

```python
# src/milodex/gui/risk_profile_bridge.py

from __future__ import annotations
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class RiskProfileBridge(QObject):
    """GUI-facing bridge for risk-profile inspection and switching (ADR 0054)."""

    profileChanged = Signal()
    switchRefused = Signal(str, str)  # (reason_code, human_message)
    switchApplied = Signal(str)  # new profile name

    def __init__(self, db_path: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path

    @Slot(result=str)
    def activeProfileName(self) -> str:
        from milodex.risk.config import get_active_profile_name
        return get_active_profile_name()

    @Slot(str, str, result=bool)
    def attemptSwitch(self, target_profile: str, confirmation_token: str) -> bool:
        """Try to switch profile. Returns True if applied, False if refused.

        For elevation (toward higher risk): confirmation_token must equal the
        target profile name (case-insensitive, trimmed).
        For reduction (toward safer): confirmation_token must equal
        'CONFIRM_REDUCTION'.
        """
        current = self.activeProfileName()
        target = target_profile.strip().lower()
        token = confirmation_token.strip().lower()

        known_profiles = ["conservative", "standard", "aggressive"]
        if target not in known_profiles:
            self._refuse("unknown_profile", f"Profile {target!r} is not shipped.",
                         current, target_profile)
            return False

        # ADR 0054 §5: refuse if any runner active.
        active_count = self._active_runners_count()
        if active_count > 0:
            self._refuse("active_runners",
                         f"Cannot switch while {active_count} runner(s) active. "
                         f"Stop all runners first.",
                         current, target_profile, runners_active_count=active_count)
            return False

        # ADR 0054 §6: refuse if a triggered kill switch is unresolved.
        if self._kill_switch_triggered_unresolved():
            self._refuse("kill_switch_open",
                         "Cannot switch while a triggered kill switch is unresolved. "
                         "Manually reset the kill switch first.",
                         current, target_profile)
            return False

        # Risk-direction check
        risk_order = {"conservative": 0, "standard": 1, "aggressive": 2}
        is_elevation = risk_order[target] > risk_order[current]

        if is_elevation:
            if token != target:
                self._refuse("typed_confirmation_mismatch",
                             f"Typed confirmation must equal {target!r}.",
                             current, target_profile)
                return False
            method = "typed"
        else:
            if token != "confirm_reduction":
                self._refuse("reduction_confirmation_missing",
                             "Reduction confirmation token missing.",
                             current, target_profile)
                return False
            method = "single_click"

        # Apply atomically.
        self._write_profile_file(target)
        self._audit(from_profile=current, to_profile=target, actor="gui",
                    confirmation_method=method, success=True, failure_reason=None,
                    runners_active_count=0)
        self.switchApplied.emit(target)
        self.profileChanged.emit()
        return True

    def _active_runners_count(self) -> int:
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM strategy_runs WHERE ended_at IS NULL"
            )
            return cur.fetchone()[0]
        finally:
            conn.close()

    def _kill_switch_triggered_unresolved(self) -> bool:
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM kill_switch_events
                    WHERE event_type = 'triggered'
                      AND id > COALESCE(
                          (SELECT MAX(id) FROM kill_switch_events WHERE event_type = 'reset'),
                          0
                      )
                )
            """)
            return bool(cur.fetchone()[0])
        finally:
            conn.close()

    def _write_profile_file(self, target: str) -> None:
        path = Path("data/risk_profile.txt")
        tmp = Path("data/risk_profile.txt.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(target + "\n", encoding="utf-8")
        tmp.replace(path)

    def _refuse(self, reason_code: str, human_message: str,
                current: str, target: str, runners_active_count: int = 0) -> None:
        logger.warning("Risk profile switch refused: %s — %s", reason_code, human_message)
        self._audit(from_profile=current, to_profile=target, actor="gui",
                    confirmation_method="none", success=False,
                    failure_reason=reason_code,
                    runners_active_count=runners_active_count)
        self.switchRefused.emit(reason_code, human_message)

    def _audit(self, *, from_profile: str, to_profile: str, actor: str,
               confirmation_method: str, success: bool,
               failure_reason: str | None, runners_active_count: int) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """INSERT INTO risk_profile_changes
                   (recorded_at, from_profile, to_profile, actor,
                    confirmation_method, context_mode, runners_active_count,
                    success, failure_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    from_profile, to_profile, actor,
                    confirmation_method,
                    "paper",  # phase-1
                    runners_active_count,
                    1 if success else 0,
                    failure_reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def record_startup_default(db_path: Path) -> None:
    """Write one audit row at app startup if data/risk_profile.txt is absent.

    Called from app.py during GUI bootstrap (PR-7c). Idempotent within an
    app session: only writes a row if no 'startup' actor row exists with
    recorded_at within the last 60 seconds (covers concurrent boot races).

    Acceptance:
    - Fresh install with no data/risk_profile.txt → one row written:
      from_profile='conservative', to_profile='conservative', actor='startup',
      confirmation_method='none', success=1.
    - Subsequent app starts within the same process lifetime → no duplicate row.
    - Tested in tests/milodex/gui/test_risk_profile_bridge.py.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        cur = conn.execute(
            "SELECT COUNT(*) FROM risk_profile_changes "
            "WHERE actor = 'startup' AND recorded_at >= ?",
            (cutoff,),
        )
        if cur.fetchone()[0] > 0:
            return  # already recorded within race window
        conn.execute(
            """INSERT INTO risk_profile_changes
               (recorded_at, from_profile, to_profile, actor,
                confirmation_method, context_mode, runners_active_count,
                success, failure_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                "conservative", "conservative", "startup",
                "none", "paper", 0, 1, None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/risk_profile_bridge.py tests/milodex/gui/test_risk_profile_bridge.py
git -C C:/Users/zdm80/Milodex commit -m "feat(risk): RiskProfileBridge with refusal rules + audit (ADR 0054)

Slot/Signal bridge for QML. Refuses switches when:
- any runner active (ADR 0054 §5)
- triggered kill switch unresolved (ADR 0054 §6)
- typed confirmation mismatch (elevation) / missing (reduction)
- target profile not in shipped set

All attempts (successful, refused, startup) write to risk_profile_changes
audit table."
```

---

### PR-7b boundary

- [ ] **Run full suite + lint, then stop for review.**

---

## PR-7c: Risk Office Drawer + Time Format + Quit (GUI surface)

**Goal:** The visible work — side drawer, persistent banner for Aggressive, time format toggle wired through QML, clean quit handler.

---

### Task 32: Enumerate time-format touch points (Section 9 step 10)

**Files (read-only inspection):**
- `src/milodex/gui/read_models.py`
- All `surfaces/*.qml` and `components/*.qml`

- [ ] **Step 1: Grep for current Python-side formatting**

```bash
python -m grep "_compact_timestamp" src/milodex/
```

Document each call site. If significantly more than the spec's enumerated set (DeskSurface, LedgerSurface, BenchSurface, FrontSurface, SectionHeader, BenchConfirmationModal) surfaces, escalate PR-7c size estimate from "small-to-decent" to "decent" — this affects task budget.

- [ ] **Step 2: Grep QML for time-formatting**

```bash
python -m grep -r "toFixed\|toLocale\|HH:MM" src/milodex/gui/qml/
```

Note any sites that pre-format times in QML (these likely need to route through the helper too).

- [ ] **Step 3: Record findings in PR description**

Total touch points: <N>. If N > 10, the change is larger than originally scoped — note and proceed.

---

### Task 33: Move time formatting to QML helper, bind to sessionBag.timeFormat

**Files:**
- Modify: `src/milodex/gui/read_models.py` (`_compact_timestamp` callsites return raw ISO)
- Modify: `src/milodex/gui/qml/Milodex/Main.qml` (sessionBag adds `timeFormat: "24h"`)
- Modify: each enumerated QML surface/component (use new helper)
- Create: `src/milodex/gui/qml/Milodex/components/TimeFormatHelper.qml` (or include inline in Main)

- [ ] **Step 1: Update sessionBag to include timeFormat**

In `Main.qml::sessionBag` (added in PR-3):
```qml
QtObject {
    id: sessionBag
    objectName: "sessionBag"
    property string perfSlice: "Week"
    property string throughputSlice: "Month"
    property string timeFormat: "24h"  // "24h" | "12h"
}
```

- [ ] **Step 2: Create the QML helper**

Either inline as a JavaScript helper or as a singleton:

```qml
// components/TimeFormatHelper.qml (or as a JS function in Main.qml)
function formatTimestamp(isoString, format) {
    if (!isoString) return "";
    var d = new Date(isoString);
    if (isNaN(d)) return isoString;  // unparseable; return raw
    var hh = d.getHours();
    var mm = d.getMinutes();
    if (format === "12h") {
        var ampm = hh >= 12 ? "PM" : "AM";
        var h12 = hh % 12; if (h12 === 0) h12 = 12;
        return h12 + ":" + (mm < 10 ? "0" + mm : mm) + " " + ampm;
    }
    // default 24h
    return (hh < 10 ? "0" + hh : hh) + ":" + (mm < 10 ? "0" + mm : mm);
}
```

- [ ] **Step 3: Update each touch point**

For each enumerated site (DeskSurface, LedgerSurface, BenchSurface, FrontSurface, SectionHeader, BenchConfirmationModal), replace bindings that read a pre-formatted Python-side string with a binding that reads raw ISO + calls `formatTimestamp(iso, sessionBag.timeFormat)`.

For example, in `LedgerSurface.qml`, change:
```qml
text: entry.displayTimestamp
```
To:
```qml
text: formatTimestamp(entry.timestamp, sessionBag.timeFormat)
```

This requires `entry.timestamp` to carry the raw ISO (already the case per `_ledger_entries` shape). Remove `displayTimestamp` from the entry dict in Python OR leave it for backward compat — but the QML now reads `timestamp`.

- [ ] **Step 4: Remove the Python-side formatting**

Task 32 enumerated all `_compact_timestamp` callers and confirmed they are display-only sites consumed by QML. **Delete the Python-side formatting** (make `_compact_timestamp` return its input unchanged, or remove it entirely and update each caller to return raw ISO). Do NOT leave a conditional "if only called for display" — Task 32 already proved that. Removing it now prevents future drift back to dual formatting.

- [ ] **Step 5: Write tests**

```python
def test_time_format_helper_24h_and_12h():
    """Reaches into QML via a minimal harness; verifies both formats."""
    # Render Main.qml; access sessionBag; call formatTimestamp with sample ISO.
    # Assert 24h: "15:42". Assert 12h: "3:42 PM".
```

- [ ] **Step 6: Smoke + manual verification**

`python -m milodex gui` → Risk Office (will be visible after Task 34) → toggle 24h/12h → verify timestamps update.

- [ ] **Step 7: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/read_models.py src/milodex/gui/qml/Milodex/
git -C C:/Users/zdm80/Milodex commit -m "feat(gui): QML-side time formatting bound to sessionBag.timeFormat

All enumerated touch points (DeskSurface, LedgerSurface, BenchSurface, FrontSurface,
SectionHeader, BenchConfirmationModal) now consume raw ISO and format in QML."
```

---

### Task 34: RiskOfficeDrawer.qml — the drawer itself

**Files:**
- Create: `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml`
- Modify: `src/milodex/gui/qml/Milodex/Main.qml` (host drawer + outside-click)
- Modify: `src/milodex/gui/qml/Milodex/components/RiskStrip.qml` (make Risk Office badge clickable)

- [ ] **Step 1: Create RiskOfficeDrawer.qml**

Skeleton:
```qml
import QtQuick
import QtQuick.Layouts
import Milodex 1.0

Item {
    id: root
    property bool open: false
    property string activeProfile: "conservative"  // bound from sessionBag/bridge

    width: 320
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    anchors.right: parent.right
    anchors.rightMargin: open ? 0 : -width  // slide animation
    Behavior on anchors.rightMargin { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

    Rectangle {
        anchors.fill: parent
        color: Theme.color.surface.canvas
        border.color: Theme.color.border.regular
        border.width: 1
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.space[4]
        spacing: Theme.space[4]

        // RISK PROFILE section
        // TIME FORMAT section
        // SYSTEM section
    }

    Keys.onEscapePressed: function(event) { root.open = false; event.accepted = true; }

    signal switchRequested(string targetProfile, string confirmationToken)
    signal quitRequested()
}
```

Implement each of the three sections inside the ColumnLayout. The RISK PROFILE section iterates over `["conservative","standard","aggressive"]`, shows each with its ceiling dials, active-state highlight, and on-click opens an inline confirmation panel below.

- [ ] **Step 2: Wire to RiskProfileBridge in Main.qml**

Register `RiskProfileBridge` in `app.py` as a context property. In Main.qml:
```qml
Connections {
    target: riskProfileBridge
    function onSwitchApplied(name) { drawerInstance.activeProfile = name; }
    function onSwitchRefused(code, msg) { drawerInstance.showError(code, msg); }
}
```

When drawer emits `switchRequested(target, token)`, call `riskProfileBridge.attemptSwitch(target, token)`.

- [ ] **Step 3: Make Risk Office badge clickable**

Find the RiskStrip.qml badge rendering. Wrap in a MouseArea; on click, toggle `drawerInstance.open`.

Update badge text to include active profile: `"RISK OFFICE · " + activeProfile.toUpperCase()`.

- [ ] **Step 4: Smoke verify**

`python -m pytest tests/milodex/gui/test_qml_load_smoke.py -v`

- [ ] **Step 5: Manual verify (no profile switching yet — that requires Task 35)**

Click Risk Office badge → drawer slides in. Click outside or press ESC → closes.

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/
git -C C:/Users/zdm80/Milodex commit -m "feat(gui): RiskOfficeDrawer side drawer + clickable Risk Office badge

Slide-in drawer (option C per brainstorming). Esc/outside-click closes.
Badge now shows active profile and opens the drawer."
```

---

### Task 35: Wire profile-switching with typed/single-click confirmation

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml`
- Test: integration via `tests/milodex/gui/test_risk_office_drawer.py`

- [ ] **Step 1: Add the inline confirmation panel**

In the RISK PROFILE section, when a non-active profile is clicked:
- For elevation (current → riskier): show text input + Confirm button. Placeholder: "Type 'aggressive' to confirm."
- For reduction (current → safer): show single Confirm button.

On Confirm: emit `switchRequested(targetProfile, tokenString)`.

- [ ] **Step 2: Show refusal feedback**

When `switchRefused` signal arrives from the bridge, render an inline error: red text below the action area: "<human_message>". Auto-dismiss after 5 seconds or on next click.

- [ ] **Step 3: Test**

```python
def test_drawer_elevation_requires_typed_confirmation(...):
    """Click Aggressive while Conservative is active; typed input appears;
    correct typed confirmation → switch lands."""


def test_drawer_reduction_requires_single_click(...):
    """Click Conservative while Aggressive is active; single Confirm button."""
```

- [ ] **Step 4: Manual verify**

End-to-end: click badge → drawer opens → click Aggressive → typed prompt → type "aggressive" → switch lands → banner appears (Task 36) → audit row written.

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml tests/milodex/gui/test_risk_office_drawer.py
git -C C:/Users/zdm80/Milodex commit -m "feat(gui): RiskOfficeDrawer profile-switch flow with confirmation gates

Elevation requires typed confirmation; reduction requires single-click.
Refusal messages render inline below the action."
```

---

### Task 36: ElevatedPostureBanner persistent across surfaces

**Files:**
- Create: `src/milodex/gui/qml/Milodex/components/ElevatedPostureBanner.qml`
- Modify: `src/milodex/gui/qml/Milodex/Main.qml` (host banner)

- [ ] **Step 1: Create the banner**

```qml
import QtQuick
import Milodex 1.0

Rectangle {
    id: root
    property string activeProfile: ""
    visible: activeProfile === "aggressive"
    height: visible ? Theme.space[4] : 0
    color: Theme.color.brand.accent  // oxblood
    Behavior on height { NumberAnimation { duration: 150 } }

    Text {
        anchors.centerIn: parent
        color: Theme.color.text.onAccent
        font.family: Theme.typography.editorial.italic.family
        font.italic: true
        font.pixelSize: Theme.typography.label.sm.pixelSize
        text: "ELEVATED POSTURE · AGGRESSIVE PROFILE ACTIVE"
    }
}
```

- [ ] **Step 2: Host in Main.qml**

Just below the top chrome strip, above any surface Loader:
```qml
ElevatedPostureBanner {
    id: elevatedBanner
    width: parent.width
    activeProfile: sessionBag.activeProfile  // bind to active profile somehow — through bridge.profileChanged
}
```

- [ ] **Step 3: Smoke + manual verify**

Switch to Aggressive (via Task 35 flow) → banner appears across all surfaces. Switch back to Conservative or Standard → banner disappears.

- [ ] **Step 4: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/
git -C C:/Users/zdm80/Milodex commit -m "feat(gui): persistent ElevatedPostureBanner for Aggressive profile (ADR 0054 §8)

Oxblood band across all surfaces when active profile is Aggressive.
Implements 'visibly active' doctrine — elevated posture must be unmistakable."
```

---

### Task 37: Time format toggle UI + Quit handler in drawer

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml`
- Modify: `src/milodex/gui/app.py` (clean-quit handler)

- [ ] **Step 1: Implement TIME FORMAT section**

Two radio-like buttons: 24-HOUR / 12-HOUR. Click toggles `sessionBag.timeFormat`. The QML helper from Task 33 picks it up immediately via binding.

- [ ] **Step 2: Implement SYSTEM section**

A single button: "QUIT MILODEX" (oxblood styling). On click: emit `quitRequested` signal up to Main.qml, which calls into a Python slot that orchestrates clean shutdown.

- [ ] **Step 3: Implement clean-quit in app.py**

```python
# in src/milodex/gui/app.py

@Slot()
def quitRequested(self) -> None:
    """Clean shutdown: stop all polling read models, drain QThreadPool, then quit."""
    # Stop each polling read model held by the app:
    for rm in [self._performance_state, self._active_ops_state, self._market_tape_state,
               self._ledger_state, self._activity_feed_state, ...]:
        if rm is not None:
            rm.stop()
    QThreadPool.globalInstance().waitForDone(3000)
    QGuiApplication.quit()
```

Register the slot on the bridge or a top-level controller exposed to QML.

- [ ] **Step 4: Test the clean-quit slot**

```python
def test_quit_handler_stops_all_polling_read_models(...):
    # Construct app with mock read models; invoke quit handler;
    # assert each read model's stop() was called.
```

- [ ] **Step 5: Manual verify**

Click Quit Milodex in drawer → app closes cleanly (check for "QThreadPool: Destroyed while threads are still running" warnings; should be none).

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/zdm80/Milodex add src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml src/milodex/gui/app.py tests/milodex/gui/
git -C C:/Users/zdm80/Milodex commit -m "feat(gui): time format toggle + clean quit handler in Risk Office drawer

Quit drains polling read models and QThreadPool before QGuiApplication.quit()
to prevent 'destroyed while threads still running' warnings.
Time format toggle wires directly to sessionBag.timeFormat (immediate
binding propagation to all enumerated touch points)."
```

---

### PR-7c boundary

- [ ] **Run full suite + lint, then stop for final review.**

```bash
python -m pytest -q
python -m ruff check src/ tests/
python -m ruff format --check src/ tests/
```

- [ ] **All 9 PRs complete.**

---

## Cross-cutting acceptance verification

After all PRs land, run the spec's manual smoke checkpoints:

**After PR-1 + PR-2:**
- No terminal popups on runner start
- Bench shows "Stop Trading" for a running paper-stage strategy
- Desk runner dropdown: readable, outside-click + ESC dismiss
- perfSlice toggle: zero layout shift
- Section header right-notes vertically centered

**After PR-5:**
- Section II ALL-PAPER shows small honest percentage
- New backtest writes to backtest_equity_snapshots only
- Trust report produces non-zero snapshot_count for backtest strategies

**After PR-7c:**
- Click Risk Office badge → drawer opens
- Switch to Aggressive (runners stopped, no triggered kill switch) → typed confirmation → switch lands → banner appears app-wide → audit row written
- Attempt switch with runner active → refusal → audit row with success=0
- Toggle time format → timestamps update across all pages
- Click Quit → app closes cleanly

---

## Out-of-scope reminders (DO NOT EXPAND)

Per spec §8:
- Eliminating redundant per-strategy writes to portfolio_snapshots
- CLI risk-profile override
- Durable persistence of UI session state
- Risk-profile-aware promotion gates
- Live-capital "human-approved" confirmation

These are deliberately deferred. If implementation surfaces a question that touches them, defer and note in PR description rather than expanding scope.

---

**End of plan.**

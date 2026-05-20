# UI Readiness Batch — Design Spec

**Date:** 2026-05-19
**Branch base:** `codex/ui-wiring-stabilization`
**Status:** Brainstormed and reviewed (4 parallel Opus critiques applied); pending spec-document-review.
**Companion plan:** to be written at `docs/superpowers/plans/2026-05-19-ui-readiness-batch.md` after spec approval.
**New ADRs introduced:** 0053 (backtest equity snapshots distinct from broker portfolio snapshots), 0054 (risk profiles are bounded operator preferences).

---

## 0. Why this exists

The operator used the app for a full day and produced a list of 12 concrete issues blocking comfortable use of the morning session. The fixes range from one-line subprocess flag changes to the first risk-policy mutation surface in the system. This spec captures: the bucketing of issues, the corrected design decisions (post-review), the per-PR scope, and the cross-cutting concerns (ADRs, testing, sequencing).

Two design dimensions distinguish this work from a routine bug batch:
- **Issue 06 is architectural.** `portfolio_snapshots` conflates backtest equity curves with live broker snapshots; the All-Paper return reads as `+9865.19%` purely because dedup-by-recorded_at picks an arbitrary mixed row per timestamp. Fix requires a schema split.
- **Issue 10 touches the risk layer.** The "Risk Office" badge becomes the system's first interactive risk-policy surface. Per CLAUDE.md doctrine ("Risk layer is sacred" / "Operator owns preferences, risk layer owns enforcement") and `docs/FOUNDER_INTENT.md` (safe-by-default, deliberately opted-in for higher risk, bounded by non-negotiable account-level guardrails), this is doctrine-bearing work requiring its own ADR and a 3-PR decomposition.

---

## 1. Scope: the 12 issues and bucketing

| # | Issue (operator's words, summarised) | Bucket |
|---|---|---|
| 01 | Paper trading no longer shows Stop Trading when runner starts | A · Runner hygiene |
| 02 | Empty terminal windows pop up when a runner starts | A · Runner hygiene |
| 03 | Runner dropdown overlaps Section III content unreadably | C · Layout discipline |
| 04 | Time-period change in Section II shifts the UI | C · Layout discipline |
| 05 | Runner dropdown doesn't close on outside click | C · Layout discipline |
| 06 | Section II ALL-PAPER shows +9865.19% P/L, -98.99% drawdown | B · Data correctness |
| 07 | Section VI Market Tape shows nothing for VIX | B · Data correctness |
| 08 | Section title right-notes (e.g. "200 events") are bottom-clipping | C · Layout discipline |
| 09 | Ledger semantic split: high-level milestones vs Section VII per-decision activity | E · New surfaces |
| 10 | Risk Office badge becomes interactive: profile switcher, time format, quit | E · New surfaces |
| 11 | Bench stage labels read as floating above (should be below-center) | C · Layout discipline |
| 12 | Section II/IV slice selections forgotten on page navigation | D · Session persistence |

Buckets map directly to PRs (Approach 1 in brainstorming):

```
PR-1   Bucket A           Runner hygiene                             tiny
PR-2   Bucket C           Layout discipline                          small
PR-3   Bucket D           Session persistence                        tiny
PR-4   Bucket B1          VIX investigation + fix                    small
PR-5   Bucket B2          Schema split + ADR 0053                    decent
PR-6   Bucket E1          Ledger taxonomy + Section VII expansion    small-to-decent
PR-7a  Bucket E2 / risk   Risk-profile model + ADR 0054              decent
PR-7b  Bucket E2 / audit  Risk-profile audit + bridge                small
PR-7c  Bucket E2 / GUI    Risk Office drawer + time format + quit    small-to-decent
```

Total: 9 PRs + 2 ADRs.

---

## 2. Decisions consolidated

These were settled during clarification and confirmed (or corrected) during review.

| Issue | Decision | Source |
|---|---|---|
| 01 | Regression in `is_session_running` calc — debug+fix during implementation. **Reproduction-first** required before changing logic (review finding). | clarification + Section A review |
| 02 | Replace `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` with `CREATE_NO_WINDOW \| CREATE_NEW_PROCESS_GROUP`. The two suppression flags are mutually exclusive per MSDN. | clarification + Section A review |
| 03 | Solid `surface.canvas` backdrop + `border.regular` on `RunnerSelect.qml` dropdown. **The operator's "unreadable" feedback is the §3-III amendment** to the prior 2026-05-18 operator-accepted decision; that file's comment block updates accordingly. | clarification + Section A review |
| 04 | Reserve vertical space in TODAY mode via `Item { height: <expanded_height> }` wrapper, children visibility-toggled internally. `Layout.preferredHeight` does not work — `perfCol` is a plain `Column`, not `ColumnLayout`. | clarification + Section A review |
| 05 | Outside-click + ESC close on the `RunnerSelect` dropdown. Overlay lives in `Main.qml` as a top-level z-layer (not as a DeskSurface sibling). | clarification + Section A review |
| 06 | Full schema split: new `backtest_equity_snapshots` table, in-framework SQL migration (modeled after `008_explanations_backtest_run_id.sql`), helper-level writer redirect, parallel read-path methods, retain forensic stray row via read-time filter (not deletion). ADR 0053 documents the separation. | clarification + Section B review |
| 07 | Investigation targets cache contents + data-ingest writer; likely root cause is Alpaca free tier not shipping VIX. Path forward: ensure VIX is fetched (Yahoo Finance fallback) or render "n/a" gracefully. | clarification + Section A review |
| 08 | `SectionHeader.qml` right-slot anchor changes from `anchors.baseline` to `anchors.verticalCenter` of the title. | clarification |
| 09 | Hybrid grain: trading sessions emit two immutable rows (Started/Stopped), backtests emit one outcome row, promotions/kill-switch/new-strategy each emit one row. **Kill-switch fires emit ONE ledger row** (from `kill_switch_events`), not three — `_session_stop_entries` filters out `exit_reason IN ('kill_switch','orphan_recovered')`. **Section VII expansion also added** (operator's intent on re-clarification). | clarification + Section C review |
| 10 | Side drawer (option C). **Real risk-profile system with three named profiles.** Default-to-Conservative on fresh install (per founder intent). All three profiles ship in Phase 1 (operator decision). **Switch refused while any runner is active** (operator decision). **Switch refused while triggered kill switch is unresolved** (designer judgement; aligns with manual-reset doctrine). Split into 3 PRs (7a/7b/7c) per review. | clarification + Section D review |
| 11 | Bench stage labels bottom-bias via `anchors.bottom: parent.bottom; anchors.bottomMargin: parent.height * 0.35` for ~65% from top. Before/after screenshots required as PR acceptance evidence. | clarification + Section A review |
| 12 | Lift `perfSlice` / `throughputSlice` to a `sessionBag` `QtObject` at `Main.qml` root. Session-only (not durable across app restarts). No `selectedRunnerId` reserved field — wire it now or omit (omit; outside this batch). | clarification + Section A review |

---

## 3. Section A — PR-1 through PR-4 (small fixes)

### PR-1 · Runner-launch hygiene (issues 01, 02)

**Tier 1 — repro confirmation (before code change)**

Run against current `data/milodex.db`:
```sql
SELECT id, recorded_at, strategy_id, exit_reason, ended_at, metadata_json
FROM strategy_runs
WHERE exit_reason = 'orphaned_no_live_runner'
ORDER BY id DESC LIMIT 20;
```
If recent rows correspond to runners the operator believes were live at that timestamp, the regression hypothesis is confirmed and the fix targets `_has_live_runner` or the bootstrap timing in [`orphan_reconciliation.py`](src/milodex/strategies/orphan_reconciliation.py:93-122). If rows correspond to genuinely-dead PIDs, the hypothesis is wrong and the regression must be re-diagnosed.

**Tier 2 — `is_session_running` correctness**

Read [`_latest_session_states` in read_models.py:1085](src/milodex/gui/read_models.py:1085). If repro shows the bootstrap reconciliation is closing live rows, the fix is in `_has_live_runner` (the PID-exists + start-time guard) — not in `_latest_session_states`. If repro is clean, do not touch this code path.

**Tier 3 — subprocess flag correction**

File: [`src/milodex/strategies/paper_runner_control.py:229-235`](src/milodex/strategies/paper_runner_control.py:229).

Change:
```python
# BEFORE
creationflags = 0
if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
if hasattr(subprocess, "DETACHED_PROCESS"):
    creationflags |= subprocess.DETACHED_PROCESS

# AFTER
creationflags = 0
if hasattr(subprocess, "CREATE_NO_WINDOW"):
    creationflags |= subprocess.CREATE_NO_WINDOW        # 0x08000000 — actual console suppression
if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
# DETACHED_PROCESS REMOVED: paradoxically creates a console for a console-subsystem child;
# mutually exclusive with CREATE_NO_WINDOW per MSDN (ERROR_INVALID_PARAMETER on combine).
```

**Acceptance**
- Starting a paper runner: no console window appears (no blink, no flash).
- For a strategy at PAPER stage with a live runner, Bench action menu shows "Stop Trading."
- After Stop Trading lands, the verb flips to "Start Trading."

**Tests**
- `tests/milodex/strategies/test_paper_runner_control.py` — assert `CREATE_NO_WINDOW` is in `creationflags` on Windows; assert `DETACHED_PROCESS` is NOT in `creationflags` (mutex).
- `tests/milodex/gui/test_read_models.py` — extend with fixture rows. Cases: live runner (`ended_at IS NULL`) → `is_session_running=True`; cleanly-stopped runner → False; reconciled-to-closed runner → False (the read model has no lock-state lookup; scenario "reconciled-but-pid-alive → True" intentionally dropped from this PR).

---

### PR-2 · Layout discipline (issues 03, 04, 05, 08, 11)

Five focused QML diffs in one PR. No new components.

**Files & changes**

1. **Issue 03** — [`components/RunnerSelect.qml`](src/milodex/gui/qml/Milodex/components/RunnerSelect.qml:96-156). Add a child `Rectangle` as the dropdown's first painted element:
   ```qml
   Rectangle {
       anchors.fill: parent
       color: Theme.color.surface.canvas
       border.color: Theme.color.border.regular
       border.width: 1
   }
   ```
   **Comment block update** ([`RunnerSelect.qml:6-11`](src/milodex/gui/qml/Milodex/components/RunnerSelect.qml:6)): replace the existing "intentionally borderless" comment with an amendment notice citing today's date and operator feedback ("unreadable in practice"). Do not silently override the prior comment.

2. **Issue 04** — [`surfaces/DeskSurface.qml:447-462`](src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml:447). Wrap the DRAWDOWN/SPY/EXCESS SubGrid:
   ```qml
   Item {
       height: <constant from expanded SubGrid height, e.g. Theme.space[8]>
       width: parent.width
       Loader {
           anchors.fill: parent
           active: !perfCol.isToday
           sourceComponent: drawdownSpyExcessGrid
       }
   }
   Component { id: drawdownSpyExcessGrid /* existing SubGrid contents */ }
   ```
   Layout height never varies with `perfSlice`.

3. **Issue 05** — outside-click + ESC dismissal. Implementation lives at [`Main.qml`](src/milodex/gui/qml/Milodex/Main.qml) root level, NOT as a sibling of `RunnerSelect`. Pattern:
   ```qml
   // Main.qml
   property bool _dropdownOpen: false
   MouseArea {
       anchors.fill: parent
       visible: _dropdownOpen
       z: <above page content, below dropdown bounds>
       onClicked: dropdownDismissed()
   }
   ```
   `RunnerSelect` emits `opened` / `dismissed` signals; Main listens to set `_dropdownOpen`. `Keys.onEscapePressed` also dismisses. The existing CLOSE pill affordance is preserved.

4. **Issue 08** — [`components/SectionHeader.qml:69`](src/milodex/gui/qml/Milodex/components/SectionHeader.qml:69). Change right-slot anchor:
   ```qml
   // BEFORE
   anchors.baseline: titleText.baseline
   // AFTER
   anchors.verticalCenter: titleText.verticalCenter
   ```

5. **Issue 11** — [`surfaces/BenchSurface.qml:250-310`](src/milodex/gui/qml/Milodex/surfaces/BenchSurface.qml:250). Replace baseline-top anchoring with bottom-bias:
   ```qml
   // Roman + name labels
   anchors.bottom: stageHeaderRow.bottom
   anchors.bottomMargin: stageHeaderRow.height * 0.35
   ```
   Acceptance evidence: before/after screenshots attached to the PR description.

**Acceptance**
- Click RUNNER dropdown → readable list with solid backing.
- Click anywhere outside dropdown bounds → list closes. ESC → list closes. CLOSE pill still works.
- Switching `perfSlice` between any pair of TODAY/WEEK/MONTH/YTD/ALL-PAPER → zero vertical movement.
- Section header right-notes ("200 events", "as of 15:41", "6 runners") vertically centered against title.
- Bench stage labels visually belong to the column below them.

**Tests**
- Extend [`tests/milodex/gui/test_qml_load_smoke.py`](tests/milodex/gui/test_qml_load_smoke.py) for the new layout invariants.
- New test: switch `perfSlice` Today→Week→Today programmatically and assert Section II bounding-box height delta is zero.
- New test: open RunnerSelect, simulate click outside dropdown bounds, assert `dismissed` signal emitted.

---

### PR-3 · Session persistence (issue 12)

**Files:** [`Main.qml`](src/milodex/gui/qml/Milodex/Main.qml) (new `sessionBag` QtObject), [`DeskSurface.qml`](src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml:43-44) (bind).

**Changes**

`Main.qml` root level:
```qml
QtObject {
    id: sessionBag
    property string perfSlice: "Week"
    property string throughputSlice: "Month"
}
```

`DeskSurface.qml` lines 43-44: replace the local `perfSlice` / `throughputSlice` properties with bindings to `Main.sessionBag.perfSlice` / `.throughputSlice`. The time-period toggle writes through these bindings.

No `selectedRunnerId` reserved slot. (Cross-page runner-selection persistence is a separate concern not in any of the 12 issues.)

**Acceptance**
- Set `perfSlice=TODAY` → navigate to Ledger → return to Desk → still TODAY.
- Set `throughputSlice=MONTH` → navigate pages → still MONTH.
- Quit app → reopen → both reset to defaults (Week, Month). Session-only.

**Tests**
- Unit test: `DeskSurface` reads/writes `sessionBag.perfSlice`, not a local property.

---

### PR-4 · VIX investigation + fix (issue 07)

**Tier 1 — targeted investigation**

```
# Inspect cache directory contents for VIX
ls -la data/cache/*/VIX* 2>&1

# Find the data-ingest writer (grep)
rg "fetch.*VIX|ingest.*VIX|SYMBOLS\s*=" src/milodex/data/

# Inspect MarketTapeState symbol list (already in code at market_tape_state.py:49)
rg "SYMBOLS\s*=" src/milodex/gui/market_tape_state.py
```

Expected: VIX already in `SYMBOLS` (per review confirmation). Cache likely missing because Alpaca free tier does not ship `^VIX`.

**Tier 2 — fix path** (chosen after investigation)

- If ingest job lacks VIX: add VIX to ingest universe with a Yahoo Finance fallback fetch (the existing data layer per `docs/VISION.md` lists Yahoo as a permitted free source).
- If ingest fetches VIX but parquet write fails silently: add error logging at the writer, fix the persistence bug.
- If neither source supports VIX with acceptable freshness: render the row as `VIX · n/a` with a tooltip explaining the limitation. **Verify the existing QML row template renders `pctChange === null` as "n/a"** before declaring graceful degradation complete; add the rendering if missing.

**Acceptance**
- VIX row appears in Section VI Market Tape with current close and % change, OR
- VIX row appears with clear "n/a" indicator (not silent omission).

**Tests**
- Unit test against `MarketTapeState` asserting the expected symbol set includes VIX.
- Unit test for the null-value rendering path on a market-tape row.

---

## 4. Section B — PR-5: portfolio_snapshots schema split + ADR 0053

### Diagnosed problem (from investigation during clarification)

`portfolio_snapshots` is written by two distinct paths that share a table by accident, not design:
- **Backtest path:** [`BacktestEngine._simulate`](src/milodex/backtesting/engine.py:872) → [`record_daily_snapshot()` in analytics/snapshots.py:52](src/milodex/analytics/snapshots.py:52) → [`EventStore.append_portfolio_snapshot()` at event_store.py:1068](src/milodex/core/event_store.py:1068). 277 rows; session IDs carry a `:wN` walk-forward suffix; equity values are simulated.
- **Live path:** [`StrategyRunner.shutdown`](src/milodex/strategies/runner.py) writes one row per session end. 38 rows; plain UUID session IDs; equity values are real broker account snapshots.

`_SQL_ALL_PAPER` dedups by `recorded_at` keeping the highest-id row per timestamp. For the earliest timestamp (a 2022-05-03 backtest fixture), the surviving row carries simulated equity `$1015.02`. For the latest timestamp (today), the surviving row carries the broker's `$101,148.22`. The reported return is exactly `(101148.22 / 1015.02) - 1 = +9865.19%`, and peak-to-trough is exactly `-98.99%`. Both numbers match the screen.

There is one additional anomaly: id=259, recorded_at `2024-12-31`, equity `$149,315.29`, no `:w` suffix. Provenance unclear; likely a pre-suffix-convention backtest leak.

### ADR 0053 (new)

**Title:** "Backtest equity snapshots are a distinct table from broker portfolio snapshots."

**Status:** Proposed, lands as part of PR-5.

**Decision:** `portfolio_snapshots` is the broker-side account-state ledger ONLY. Backtest equity curves live in a new `backtest_equity_snapshots` table. Writers, readers, schemas, and ownership are separated. Future code MUST NOT merge them for convenience.

**Justification:** The mixed-writer table produced a 4-orders-of-magnitude misread on the operator's primary trust surface. The damage budget for repeating this class of error is zero: the operator's morning sanity-check depends on this number. The two writers describe different concepts (real account state vs simulated trial); shared persistence is a category error.

**Citations:** complements ADR 0011 (event store as source of truth), ADR 0032 (audit-trail backfill policy spirit).

### Schema

In-framework SQL migration `src/milodex/core/migrations/006_backtest_equity_snapshots.sql`, modeled after [`008_explanations_backtest_run_id.sql`](src/milodex/core/migrations/008_explanations_backtest_run_id.sql):

```sql
-- 006_backtest_equity_snapshots.sql
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
    daily_pnl REAL,                       -- nullable for backtests (sim broker doesn't track the same way)
    positions_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_equity_session
    ON backtest_equity_snapshots (session_id);
CREATE INDEX IF NOT EXISTS idx_backtest_equity_strategy_time
    ON backtest_equity_snapshots (strategy_id, recorded_at);

-- One-time data migration: move the 277 :w-suffixed rows over.
-- backtest_run_id is NULL for migrated rows (no reliable mapping from string suffix).
INSERT INTO backtest_equity_snapshots
    (recorded_at, session_id, strategy_id, backtest_run_id,
     equity, cash, portfolio_value, daily_pnl, positions_json)
SELECT recorded_at, session_id, strategy_id, NULL,
       equity, cash, portfolio_value, daily_pnl, positions_json
FROM portfolio_snapshots
WHERE session_id LIKE '%:w%';

DELETE FROM portfolio_snapshots WHERE session_id LIKE '%:w%';

-- Stray $149K row: NOT deleted. Preserved as forensic evidence per the
-- operator's `feedback_inspect_before_deciding` rule. Excluded from read
-- queries via a value-ceiling filter (see PerformanceState change below).
```

### Writer redirect (helper level)

New helper in [`analytics/snapshots.py`](src/milodex/analytics/snapshots.py):
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
    """Record a simulated portfolio snapshot from a backtest engine."""
    event_store.append_backtest_equity_snapshot(...)
```

New method in [`event_store.py`](src/milodex/core/event_store.py): `append_backtest_equity_snapshot(...)`, plus `list_backtest_equity_snapshots_for_strategy(strategy_id)`.

[`BacktestEngine._simulate`](src/milodex/backtesting/engine.py:872): swap which helper it calls.

[`analytics/snapshots.py` docstring](src/milodex/analytics/snapshots.py:1-19): rewrite to make the broker-only contract explicit; cite ADR 0053.

### Read-path completeness

[`analytics/reports.py:103`](src/milodex/analytics/reports.py:103) `build_trust_report()` currently calls `event_store.list_portfolio_snapshots_for_strategy(metrics.strategy_id)` to summarise backtest equity. Post-migration this returns zero rows for any backtest strategy. **Redirect this call** to `list_backtest_equity_snapshots_for_strategy()`.

[`performance_state.py`](src/milodex/gui/performance_state.py) `_SQL_ALL_PAPER`: no SQL change for the join-back logic itself. Add a value-ceiling filter to exclude the stray forensic row:
```sql
SELECT recorded_at, equity FROM (
    SELECT recorded_at, equity,
           ROW_NUMBER() OVER (PARTITION BY recorded_at ORDER BY id DESC) AS rn
    FROM portfolio_snapshots
    WHERE equity < 1000000  -- defensive ceiling; excludes the $149K stray and any future anomaly
) sub
WHERE rn = 1
ORDER BY recorded_at;
```
Document the ceiling as a value-defense, not a real account limit.

### Pre-implementation verification step

Before writing the migration, verify the dedup-by-recorded_at assumption against the actual data:
```sql
-- Are sub-second timestamps an issue for live rows?
SELECT recorded_at, COUNT(*) cnt, MIN(equity) min_eq, MAX(equity) max_eq
FROM portfolio_snapshots
WHERE session_id NOT LIKE '%:w%'
GROUP BY recorded_at
HAVING COUNT(*) > 1;
```
If multiple rows per identical timestamp carry different equity values (e.g., during a shutdown wave), document the behavior and decide whether to dedup more aggressively (e.g., GROUP BY date-truncated timestamp).

### Acceptance

- Migration idempotent (running twice is a no-op past the first apply).
- After migration: `portfolio_snapshots` has 37 rows (1 stray + 36 surviving non-:w; verify exact count post-run); `backtest_equity_snapshots` has 277 rows.
- `BacktestEngine._simulate` writes only to `backtest_equity_snapshots`.
- `analytics/reports.py:build_trust_report()` produces non-zero `snapshot_count` for backtest strategies post-migration.
- Performance State ALL-PAPER return reads as a small honest percentage (~+0.83% based on live-only inspection).
- Drawdown reads as a small negative number reflecting actual paper variance.

### Tests

- New: `tests/milodex/core/test_migrations.py::test_006_split_backtest_snapshots` — fixture DB with mixed :w / non-:w rows; run migration; assert clean split, exact row counts.
- New: `tests/milodex/backtesting/test_engine.py::test_simulate_writes_to_backtest_equity_snapshots` — assert a simulated backtest writes only to the new table.
- New: `tests/milodex/analytics/test_reports.py::test_trust_report_reads_from_backtest_table` — fixture with both tables populated; assert `snapshot_count > 0` for a backtest strategy.
- Extend `tests/milodex/gui/test_performance_state.py` — assert PerformanceState against the migrated DB returns realistic numbers.

### Risk callouts (audited before merge)

- `promotion/policy.py` — confirm it consumes `backtest_runs.metadata_json` (computed metrics), NOT `portfolio_snapshots` directly. If the latter, redirect to `backtest_equity_snapshots`.
- Any test fixtures depending on the polluted data — update or recreate.

---

## 5. Section C — PR-6: Ledger taxonomy + Section VII expansion (issue 09)

### Event taxonomy

| Source | Emits | Grain | outcomeKind |
|---|---|---|---|
| `promotions` table | promote / demote / stage_return | 1 row each | `promoted` / `demoted` / `returned` |
| `kill_switch_events` table | trigger / reset | 1 row each | `fired` / `info` |
| `strategy_runs` (`started_at`) | Started Trading | 1 immutable row | `started` |
| `strategy_runs` (`ended_at`, with filter) | Stopped Trading | 1 immutable row | `stopped` |
| `backtest_runs` (`status='completed'`) | Backtest completed | 1 row + metric summary | `backtested` |
| Derived (event tables + YAML mtime fallback) | New strategy added | 1 immutable row | `added` |

### Implementation in [`read_models.py:_ledger_entries`](src/milodex/gui/read_models.py:1028)

Refactor to merge across all sources:

```python
def _ledger_entries(db_path):
    entries = []
    entries += _promotion_entries(conn)              # existing logic, refactored out
    entries += _kill_switch_entries(conn)            # existing logic, refactored out
    entries += _session_start_entries(conn)          # NEW
    entries += _session_stop_entries(conn)           # NEW — with exit_reason filter
    entries += _backtest_complete_entries(conn)      # NEW
    entries += _new_strategy_entries(conn, configs_dir)  # NEW
    return sorted(entries, key=lambda e: e["timestamp"], reverse=True)
```

### Per-source SQL (corrected against actual schema)

**`_session_start_entries`**
```sql
SELECT strategy_id, started_at, session_id
FROM strategy_runs
WHERE started_at IS NOT NULL
ORDER BY started_at DESC LIMIT 200;
```

**`_session_stop_entries`** — **dedup against kill-switch and reconciliation**:
```sql
SELECT strategy_id, ended_at, exit_reason, session_id
FROM strategy_runs
WHERE ended_at IS NOT NULL
  AND exit_reason NOT IN ('kill_switch', 'orphan_recovered')
ORDER BY ended_at DESC LIMIT 200;
```
Rationale: a kill-switch fire already emits its own ledger row from `kill_switch_events`. Including a Stopped Trading row would triple-count the event. Similarly, an orphan-reconciled close is synthetic — not a real operator-initiated stop.

**`_backtest_complete_entries`** — uses actual columns:
```sql
SELECT id, strategy_id, ended_at, status, metadata_json
FROM backtest_runs
WHERE status = 'completed' AND ended_at IS NOT NULL
ORDER BY ended_at DESC LIMIT 200;
```
Sharpe/n/max-dd extracted from `json_extract(metadata_json, '$.oos_aggregate.sharpe')` etc., per [`walk_forward_runner.py:341-372`](src/milodex/backtesting/walk_forward_runner.py:341).

**`_new_strategy_entries`** — `MIN(recorded_at)` across event tables:
```sql
WITH first_seen AS (
    SELECT strategy_id, MIN(recorded_at) AS first_at
    FROM (
        SELECT strategy_id, recorded_at FROM promotions
        UNION ALL
        SELECT strategy_id, started_at FROM strategy_runs WHERE started_at IS NOT NULL
        UNION ALL
        SELECT strategy_id, started_at FROM backtest_runs
    )
    GROUP BY strategy_id
)
SELECT strategy_id, first_at FROM first_seen ORDER BY first_at DESC;
```
For strategies appearing in `configs/` but never in any event table, fall back to YAML file mtime as the "added" timestamp. Document this fallback as a known degradation (mtime is unreliable under `git checkout`).

### Sharpe coloring threshold

Bind to constants in [`promotion/policy.py:140-141`](src/milodex/promotion/policy.py:140):
```python
PAPER_GATE_SHARPE = ACTIVE_PROMOTION_POLICY.paper_gate.min_sharpe  # 0.0
CAPITAL_GATE_SHARPE = ACTIVE_PROMOTION_POLICY.capital_gate.min_sharpe  # 0.5
```
Three-tone scale:
- Sharpe ≥ CAPITAL_GATE → `status.positive`
- Sharpe ≥ PAPER_GATE and < CAPITAL_GATE → `status.neutral` (paper-viable but not capital-ready)
- Sharpe < PAPER_GATE → `status.negative`

### QML — LedgerSurface filter UI

Current [`LedgerSurface.qml:178-209`](src/milodex/gui/qml/Milodex/surfaces/LedgerSurface.qml:178) renders a flat 5-chip Repeater. Adding 4 new outcomeKinds would push to 9 chips and break visual rhythm.

**Two-row grouped filter:**
- Row 1: outcome kind groups — `Promotion` (promoted/demoted/returned) · `Lifecycle` (started/stopped) · `Backtest` (backtested) · `System` (fired/info/added)
- Row 2: existing time-range / stage filters

Click on a group expands an inline sub-row of specific kinds within that group, OR selecting a group filters to all kinds within it (decide during implementation; prefer the simpler "select group filters all kinds within").

### Section VII expansion (operator's clarification)

[`activity_feed_state.py`](src/milodex/gui/activity_feed_state.py) currently reads `explanations` + paper `trades`. The operator stated Section VII should show "every backtest result." Expand:

- Add `backtest_runs` (completed) as a third source.
- Each completed backtest emits one Activity Feed entry with `kind='backtest'`, `subject=strategy_name`, `outcome=COMPLETED`, `metric_summary=Sharpe X / n=Y / max-dd Z%`.
- Filter chip in Section VII gets a new "BACKTESTS" toggle (extending the existing All/Orders/Rejections/Signals/Fills set).

### Acceptance

- Ledger shows: every promotion + every kill-switch + every Started/Stopped trading event (excluding kill-switch-initiated stops) + every backtest completion + every "new strategy added."
- Filter rows organize cleanly without chip-overflow.
- Section VII Order/Signal Tape now includes backtest results alongside orders/signals/rejections.

### Tests

- Extend `tests/milodex/gui/test_read_models.py::test_ledger_entries` with fixture DB containing all 6 event types; assert all surface; assert sort order is correct across cross-source timestamps; assert kill-switch fire emits exactly ONE ledger row (not three).
- New: `tests/milodex/gui/test_activity_feed_state.py::test_backtest_results_in_feed` — fixture with completed backtest runs; assert they surface in Section VII.

---

## 6. Section D — PRs 7a / 7b / 7c: Risk-profile system

The first risk-policy mutation surface in Milodex. Doctrine-bearing. Split into three PRs per the review finding that the original "one decent PR" framing conflated three independent concerns.

### ADR 0054 (new, lands with PR-7a)

**Title:** "Risk profiles are bounded operator preferences; the risk layer enforces them."

**Status:** Proposed, lands as part of PR-7a.

**Decision:**
1. Three named profiles: Conservative, Standard, Aggressive.
2. **Default-to-Conservative on fresh install.** Per `docs/FOUNDER_INTENT.md:131` — a fresh installation runs at conservative posture.
3. Profiles are **overlays** on top of the canonical `configs/risk_defaults.yaml` base. Conservative tightens, Aggressive loosens, Standard is the identity overlay.
4. **Account-level absolute ceilings are CODE CONSTANTS**, not editable YAML. Per founder intent: "the operator cannot disable the floor." Located in `src/milodex/risk/config.py` with documented justification per ceiling.
5. **Profile switches are refused while any runner is active.** Operator must stop all runners first. (No mid-flight risk drift possible; honors the doctrine.)
6. **Profile switches are refused while a triggered (unresolved) kill switch exists.** Once manually reset (per CLAUDE.md "Kill switch requires manual reset"), switches are allowed.
7. **Risk-elevation switches require typed confirmation** (case-insensitive, trimmed). **Risk-reduction switches require single-click confirmation** (still requires affirmative action; differs only in mechanism).
8. **Visibly active.** Active profile name appears in the persistent top-right Risk Office badge. **Aggressive profile additionally displays a persistent oxblood banner across all surfaces** while active. Conservative and Standard display no banner.
9. Every profile change writes a row to a new `risk_profile_changes` audit table, including: `from_profile`, `to_profile`, `actor`, `confirmation_method`, `context_mode`, `runners_active_count`. Failed-confirmation attempts ALSO emit an audit row with a `failure_reason` column for forensic completeness.
10. **No strategy, ML model, frontier agent, or feature may select or switch profiles.** Risk-profile mutation is restricted to operator-initiated UI/CLI paths only.

**Citation:** complements ADR 0011 (event store as source of truth); reaffirms CLAUDE.md "Risk layer is sacred" and `docs/FOUNDER_INTENT.md` "The Risk Layer — Operator Preferences, System Enforcement."

---

### PR-7a — Risk-profile model + loader + ADR 0054 (no GUI)

**New ADR file:** `docs/adr/0054-risk-profiles-bounded-operator-preferences.md`.

**New directory & YAML files:**
```
configs/risk_profiles/
  conservative.yaml    # overlay: tightens
  standard.yaml        # overlay: identity (mirrors risk_defaults.yaml)
  aggressive.yaml      # overlay: loosens (bounded by code-level ceilings)
```

Each overlay is a sparse YAML that gets merged onto `configs/risk_defaults.yaml` at load time. Keys present in the overlay override the base; absent keys inherit. This sidesteps the 39-callsite blast radius of renaming `risk_defaults.yaml`.

**Conservative overlay** (tightens):
```yaml
kill_switch:
  max_drawdown_pct: 0.05      # base is 0.10
portfolio:
  max_total_exposure_pct: 0.30 # base is 0.50
  max_concurrent_positions: 5  # base is 10
daily_limits:
  max_daily_loss_pct: 0.02     # base is 0.03
```

**Standard overlay** (identity — empty file or `{}`).

**Aggressive overlay** (loosens, but bounded):
```yaml
kill_switch:
  max_drawdown_pct: 0.15
portfolio:
  max_total_exposure_pct: 0.75
  max_concurrent_positions: 15
daily_limits:
  max_daily_loss_pct: 0.05
```

**Account-level ceilings as code constants** in `src/milodex/risk/config.py`:
```python
# Account-level absolute ceilings. NOT EDITABLE. Per ADR 0054 and
# FOUNDER_INTENT.md "the operator cannot disable the floor."
#
# Each value is the maximum permitted across ANY risk profile. The
# active profile's setting is rejected at load if it exceeds these.
#
# Justification (revisit only via ADR amendment):
# - MAX_DRAWDOWN_PCT_CEILING = 0.20: above Aggressive's 0.15 by a safety
#   margin, well below the 25% institutional pension-fund tolerance band.
#   Sub-$1k Phase-1 capital — a 20% drawdown is $200, recoverable.
# - MAX_TOTAL_EXPOSURE_PCT_CEILING = 0.85: above Aggressive's 0.75; keeps
#   at minimum 15% cash buffer regardless of profile.
# - MAX_DAILY_LOSS_PCT_CEILING = 0.08: above Aggressive's 0.05; daily
#   single-session loss never exceeds 8% even under elevated posture.
_ABSOLUTE_CEILINGS = {
    "kill_switch.max_drawdown_pct": 0.20,
    "portfolio.max_total_exposure_pct": 0.85,
    "daily_limits.max_daily_loss_pct": 0.08,
}
```

**Loader API** in `src/milodex/risk/config.py`:
```python
def get_active_profile_name() -> str:
    """Read data/risk_profile.txt; fallback to 'conservative' if absent."""

def _load_overlay(profile_name: str) -> dict:
    """Read configs/risk_profiles/{name}.yaml; return parsed dict or {} for identity."""

def _merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge; overlay wins."""

def _validate_against_ceilings(profile: dict) -> None:
    """Raise RuntimeError if any path in _ABSOLUTE_CEILINGS is exceeded."""

def load_active_risk_profile() -> dict:
    """Load risk_defaults.yaml + active overlay, validate, return merged dict.
    On invalid profile, refuse to start and fall back to Conservative."""
```

**Tests**
- `tests/milodex/risk/test_config.py::test_default_to_conservative_when_file_absent`
- `tests/milodex/risk/test_config.py::test_overlay_merge_correctness`
- `tests/milodex/risk/test_config.py::test_validate_refuses_ceiling_violation` — write a malicious overlay that exceeds a ceiling; assert refusal.
- `tests/milodex/risk/test_config.py::test_fallback_to_conservative_on_malformed_profile`
- `tests/milodex/risk/test_config.py::test_all_three_shipped_profiles_pass_validation` — guards against accidentally shipping an Aggressive overlay that violates ceilings.

---

### PR-7b — Audit table + bridge (no GUI)

**Migration:** `src/milodex/core/migrations/009_risk_profile_changes.sql`:

```sql
CREATE TABLE IF NOT EXISTS risk_profile_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    from_profile TEXT NOT NULL,
    to_profile TEXT NOT NULL,
    actor TEXT NOT NULL,                       -- 'gui' | 'cli' | 'startup'
    confirmation_method TEXT NOT NULL,         -- 'typed' | 'single_click' | 'none'
    context_mode TEXT NOT NULL,                -- 'paper' | 'micro_live' | 'live'
    runners_active_count INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL,                  -- 1 = applied, 0 = refused/failed
    failure_reason TEXT                        -- nullable; populated when success=0
);

CREATE INDEX IF NOT EXISTS idx_risk_profile_changes_time
    ON risk_profile_changes (recorded_at);
```

**New file:** `src/milodex/gui/risk_profile_bridge.py` — Slot/Signal bridge analog of `bench_command_bridge.py`. Public surface:

```python
class RiskProfileBridge(QObject):
    """GUI-facing bridge for risk-profile inspection and switching."""

    profileChanged = Signal()  # active profile name changed
    switchRefused = Signal(str, str)  # (reason_code, human_message)
    switchApplied = Signal(str)  # new profile name

    @Slot(result=str)
    def activeProfileName(self) -> str: ...

    @Slot(str, str, result=bool)
    def attemptSwitch(self, target_profile: str, confirmation_token: str) -> bool:
        """
        Returns True if applied, False if refused.
        confirmation_token: for elevation, the typed profile name (case-insensitive,
        trimmed); for reduction, the literal string 'CONFIRM_REDUCTION'.

        Refusal cases:
          - Active runner count > 0  → reason='active_runners'
          - Triggered kill switch unresolved  → reason='kill_switch_open'
          - Elevation with bad token  → reason='typed_confirmation_mismatch'
          - Reduction with missing token  → reason='reduction_confirmation_missing'
          - Profile name not in shipped set  → reason='unknown_profile'
        """
```

**Behavior**

- Active runners check: `len([row for row in strategy_runs if row.ended_at is None])` via existing read model.
- Triggered kill switch check: `EXISTS (SELECT 1 FROM kill_switch_events WHERE event_type = 'triggered' AND id > COALESCE((SELECT MAX(id) FROM kill_switch_events WHERE event_type = 'reset'), 0))`.
- Confirmation token comparison: case-insensitive, trimmed, ASCII-only. Compare `confirmation_token.strip().lower() == target_profile.lower()`.
- Refusal cases write an audit row with `success=0` and `failure_reason=<reason_code>`.
- Successful switch atomically rewrites `data/risk_profile.txt` (write to `data/risk_profile.txt.tmp`, then rename).
- Startup defaulting (file absent / unreadable / unknown profile name) writes one audit row with `actor='startup'` and `confirmation_method='none'`.

**Tests**
- `tests/milodex/gui/test_risk_profile_bridge.py::test_refuse_when_runners_active`
- `tests/milodex/gui/test_risk_profile_bridge.py::test_refuse_when_kill_switch_triggered`
- `tests/milodex/gui/test_risk_profile_bridge.py::test_refuse_typed_confirmation_mismatch`
- `tests/milodex/gui/test_risk_profile_bridge.py::test_audit_row_written_on_refusal`
- `tests/milodex/gui/test_risk_profile_bridge.py::test_atomic_file_update_on_success`
- `tests/milodex/gui/test_risk_profile_bridge.py::test_startup_default_writes_audit_row`

---

### PR-7c — Risk Office drawer + time format + quit (GUI only)

**New QML component:** `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml`.

Side drawer per chosen mockup (option C). Anchored to right edge, slides in. Width 320px, full content-area height.

Three sections separated by hairline dividers:
1. **RISK PROFILE** — three entries (Conservative / Standard / Aggressive). Each shows: profile name (uppercase eyebrow), current ceiling dials (max-drawdown, max-exposure, max-concurrent), active badge. Currently-active highlighted with `brand.primary` fill.
   - Clicking a non-active profile opens an inline confirmation panel below (replaces section content while open):
     - Elevation: "Switch to AGGRESSIVE? This raises max drawdown to 15%, max exposure to 75%, max positions to 15. Type 'aggressive' to confirm." + text input + Confirm button. Cancel returns to list.
     - Reduction: "Switch to CONSERVATIVE? This tightens max drawdown to 5%, max exposure to 30%, max positions to 5. Confirm?" + Confirm button.
   - On refusal (from bridge), inline error message shown ("Cannot switch while runners are active. Stop all runners first.").
2. **TIME FORMAT** — radio toggle, 24-HOUR / 12-HOUR. Default 24-HOUR (current behavior).
3. **SYSTEM** — "QUIT MILODEX" button (oxblood styling).

**RiskStrip badge** ([`Main.qml:128-138`](src/milodex/gui/qml/Milodex/Main.qml:128)):
- Append active profile to badge text: `RISK OFFICE · CONSERVATIVE` etc.
- Click opens the drawer.

**Persistent banner** (NEW component, e.g., `ElevatedPostureBanner.qml`):
- Visible when `activeProfile === "aggressive"`.
- Sits across all surfaces, just below the top chrome strip.
- Oxblood background, italic serif text: "ELEVATED POSTURE · AGGRESSIVE PROFILE ACTIVE".
- Implements ADR 0054 §8 ("visibly active").
- Conservative and Standard: no banner.

**Time format plumbing:**
- Add `timeFormat: "24h"` to `Main.qml::sessionBag` (the QtObject introduced in PR-3).
- All `_compact_timestamp()` callsites in `read_models.py` return raw ISO; remove the Python-side format.
- Add a QML helper component / inline function: `formatTimestamp(isoString, sessionBag.timeFormat)`.
- All time displays in DeskSurface, LedgerSurface, BenchSurface, FrontSurface re-route through the helper. **This is a wider plumbing change than the original draft acknowledged** — every timestamp display in QML must be touched.

**Quit handler:**
- Wire to a Python-side slot rather than direct `Qt.quit()`.
- On invocation: call `stop()` on all polling read models (`PerformanceState`, `ActiveOpsState`, `MarketTapeState`, `LedgerState`, `ActivityFeedState`, etc.), then drain the global `QThreadPool` via `waitForDone(3000)`, then `QGuiApplication.quit()`.

**Acceptance**
- Risk Office badge in top-right is clickable; shows active profile name.
- Click opens right-anchored drawer.
- Profile switch with typed confirmation works for elevations; single-click for reductions; refused appropriately when runners active or kill-switch open.
- Aggressive profile active → oxblood banner visible on all pages.
- Time format toggle updates timestamps app-wide immediately.
- Quit invokes clean shutdown (no orphaned QThreadPool warnings on exit).

**Tests**
- New: `tests/milodex/gui/test_risk_office_drawer.py` — QML smoke; drawer opens/closes; profile-click flow; confirmation modes.
- New: `tests/milodex/gui/test_elevated_posture_banner.py` — banner visible iff active profile is Aggressive.
- New: `tests/milodex/gui/test_time_format_toggle.py` — toggling format updates a sample timestamp display.
- New: `tests/milodex/gui/test_quit_shutdown.py` — invoking the quit slot calls `stop()` on each polling read model.

---

## 7. Cross-cutting concerns

### Per-PR CI

Each PR runs:
- `python -m pytest` (full test suite must pass)
- `python -m ruff check src/ tests/`
- `python -m ruff format --check src/ tests/`

Each PR adds the specific tests listed in its Tests section.

### Manual smoke checkpoints

After PR-1 + PR-2 land:
- Launch GUI; no terminal windows pop up on runner start.
- Bench shows "Stop Trading" for a running paper-stage strategy.
- Desk runner dropdown: readable, click-outside dismisses, ESC dismisses.
- perfSlice toggle: zero layout shift across TODAY/WEEK/MONTH/YTD/ALL-PAPER.
- Section header right-notes vertically centered.

After PR-5 lands:
- Section II ALL-PAPER shows a small positive number (not +9865%).
- CLI backtest run: new rows in `backtest_equity_snapshots`, none in `portfolio_snapshots`.
- Trust report (`milodex trust <strategy_id>`) produces non-zero snapshot_count for backtest strategies.

After PR-7c lands:
- Click Risk Office badge → drawer opens.
- Switch to Aggressive (with runners stopped, no triggered kill switch) → typed confirmation → switch applies → banner appears app-wide → audit row written.
- Attempt switch with runner active → refusal message → audit row with `success=0` written.
- Toggle time format → timestamps update across all pages.
- Click Quit → app closes cleanly.

### Sequencing for "tomorrow morning" target

Independence order, smallest-first:
```
PR-1   Runner hygiene              (must ship — most painful regression)
PR-2   Layout discipline           (small, high visibility, ship together if possible)
PR-3   Session persistence         (small)
PR-4   VIX                         (investigate first; small once root cause found)
PR-5   Schema split + ADR 0053     (decent; can ship same evening if smooth)
PR-6   Ledger + Section VII        (decent; can ship same evening)
PR-7a  Risk profile model          (decent — foundational, must precede 7b/7c)
PR-7b  Audit + bridge              (small once 7a lands)
PR-7c  Drawer + time format + quit (small-to-decent; widest QML changes)
```

If PRs 7a-c don't all land overnight, the operator opens an app in the morning that is already substantially better: no terminal popups, Stop Trading restored, layout stable, persistence working, VIX visible, honest All-Paper number, expanded Ledger. The Risk Office work continues afterward.

### ADRs

| ADR | Title | Lands with |
|---|---|---|
| 0053 | Backtest equity snapshots distinct from broker portfolio snapshots | PR-5 |
| 0054 | Risk profiles are bounded operator preferences | PR-7a |

Both follow the existing ADR directory pattern (`docs/adr/NNNN-slug.md`); both cite CLAUDE.md doctrine and FOUNDER_INTENT.md where relevant.

---

## 8. Out-of-scope (deferred, NOT in this batch)

- **Eliminating redundant per-strategy writes to `portfolio_snapshots`** — every live runner currently writes its own copy of the broker snapshot. Post-PR-5 this is harmless (all values agree) but conceptually redundant. Separate follow-up.
- **CLI risk-profile override** (`milodex --risk-profile aggressive`) — the audit table has the `actor='cli'` column reserved, but the CLI wiring itself is not in this batch.
- **Durable persistence of UI session state across app restarts** — `sessionBag` is session-only by deliberate choice.
- **Per-strategy activity-filter persistence in Section VII** — not in any of the 12 issues.
- **Risk-profile-aware promotion gates** — promotion thresholds remain bound to `policy.py` constants; whether they should vary with active profile is a future ADR question.
- **Live-capital "human-approved for live-capital effect"** confirmation — doctrine requires this but Phase 1 is paper-only; the seam is left in `risk_profile_bridge.py::attemptSwitch` via the `context_mode` field but enforcement is deferred until live capital is introduced.

---

## 9. Open verification steps (must complete during implementation)

These are explicit steps that must run during implementation, not assumptions:

1. **PR-1**: run the repro SQL query before changing any logic. Outcome determines whether `_has_live_runner` is touched or left alone.
2. **PR-4**: run `ls data/cache/*/VIX*` and grep the data-ingest writer before designing the fix. Outcome determines whether the fix is ingest-side or rendering-side.
3. **PR-5**: run the sub-second-timestamp verification SQL before writing the migration. Outcome determines whether dedup logic needs tightening beyond the current PARTITION BY recorded_at.
4. **PR-5 (audit before merge)**: `rg "portfolio_snapshots" src/milodex/promotion/` — confirm promotion logic doesn't read the table directly.
5. **PR-7a**: justify each `_ABSOLUTE_CEILINGS` value in the ADR; do not ship with placeholder numbers.

---

**End of spec.**

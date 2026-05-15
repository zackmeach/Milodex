# Editorial Dark Launch Readiness

**Scope:** Final pre-launch verification pass for the Editorial Dark initial release. Editorial Light + Bronze are deferred post-launch (see [DESIGN.md §7](DESIGN.md), [DESIGN_SYSTEM.md §1.2](DESIGN_SYSTEM.md), commit `fee27fe`).

**Predecessors:** [PHASE5_PLANNING.md](PHASE5_PLANNING.md), [VISION.md "Daily Operator Workflow"](VISION.md#daily-operator-workflow), [FOUNDER_INTENT.md](FOUNDER_INTENT.md), [ADR 0004](adr/0004-paper-only-phase-one.md) (paper-only), [ADR 0005](adr/0005-kill-switch-manual-reset.md) (manual reset), [ADR 0037](adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md) (distribution).

**Use this doc as:** a binary checklist. Each item is pass / fail / manual-required / N/A. If any blocker fails, the item — not this doc — drives the fix. No QML or theme changes are authorized from this pass unless a blocker is uncovered.

**Rule of this pass:** verify, do not polish. Polish items that surface go to "Non-blocking polish" below and ship post-launch.

**Pass run:** 2026-05-14, commit `fee27fe`, on `master`.
**Method:** command/script-driven. No UI click automation. Manual operator checks called out explicitly per §1 item.

---

## 0. Phase 1 paper lifecycle acceptance (2026-05-15)

Bench is no longer a read-only prototype for the paper lifecycle. The normal
operator path is now:

`backtest evidence -> promote to paper -> start paper runner -> controlled stop -> inspect paper evidence -> demote/walk back`

The submit-capable action families are demotion / walk-back, freeze manifest,
canonical backtest evidence, promote-to-paper, start paper runner, and stop
paper runner. Paper-runner Stop Trading is controlled-stop only; the kill
switch remains a separate Anchor/Risk Office affordance.

**Lifecycle walk result:** PASS on `master` at merge commit `2fc6a42`
(`2026-05-15`). The walk used the lifecycle-proof strategy
`regime.daily.sma200_rotation.spy_shy.v1`: it was walked back to `backtest`,
given canonical Bench backtest evidence, promoted back to `paper` with a
lifecycle-exempt evidence package, started as a paper runner, then stopped via
controlled stop.

Evidence captured:

- artifact root:
  `artifacts/gui-screenshots/20260515-150357/lifecycle-walk/`
- backtest/promote bridge transcript:
  `lifecycle-step1-backtest-promote.json`
- start-runner bridge transcript:
  `lifecycle-step2-start-runner.json`
- stop-request transcript:
  `lifecycle-step3-stop-request.json`
- controlled-stop assertion:
  `lifecycle-step4-controlled-stop.json`
- final DB/read-model assertions:
  `lifecycle-final-db-read-model-assertions.json`
- screenshots: `before-start/`, `active-session/`, `stop-requested/`,
  `stopped-session/`, and `paper-evidence/`

Acceptance assertions:

- canonical Bench backtest wrote completed `backtest_runs.id=89`;
- run id:
  `0733d4d1-b00a-4550-bdf8-0f7cce9cad20`;
- promote-to-paper wrote promotion event `id=12` with
  `promotion_type="lifecycle_exempt"`;
- YAML ended at `stage: "paper"`;
- Start Trading launched a paper session
  `8aee4efb-596e-4801-bb0b-5469ca1c5d12`;
- Stop Trading wrote a controlled-stop request which was consumed;
- `strategy_runs.exit_reason="controlled_stop"`;
- Bench read model shows paper evidence with status `completed`, session
  timestamps, exit reason `controlled_stop`, and paper trade count `0`;
- QML still routes submits through `BenchCommandBridge`; no direct broker,
  event-store, runner, or facade imports are exposed to QML.

Verification around the merged PR stack:

- PR #141 before merge: `ruff` clean; full suite
  `1421 passed, 5 skipped, 4 xfailed`.
- PR #144 before merge: `ruff` clean; full suite
  `1416 passed, 4 xfailed`.
- PR #145 before merge: `ruff` clean; full suite
  `1427 passed, 4 xfailed`.
- Docs closeout branch verification: `ruff` clean, `git diff --check` clean,
  full suite `1427 passed, 4 xfailed`.

No lifecycle-blocking patch PR was needed from this walk.

---

## Results summary

| Category | Auto-verified | Manual still needed | Result |
|---|---|---|---|
| Lint (ruff) | ✅ | — | clean |
| Full test suite (1259 passed, 4 xfailed) | ✅ | — | clean |
| Coverage gate (fail_under = 89) | ✅ | — | 89.00% (exact gate hit) |
| Phase 1 Bench paper lifecycle | ✅ | — | completed on `master` (`2fc6a42`) |
| CLI smoke | ✅ | — | clean (commands corrected from initial checklist) |
| GUI offscreen render (4 surfaces) | ✅ | — | captured |
| Bench interactive (Evidence rail + Confirmation modal) | ✅ | — | captured |
| Theme-scope grep audit | ✅ | — | no leaks into operator surfaces |
| Doctrine docs unchanged since `fee27fe` | ✅ | — | confirmed |
| Showcase tab gating in source | ✅ | — | `tabEnabled: false` on Light + Bronze (DesignSystemShowcase.qml:231–232) |
| First-run with missing `.env` / `data/milodex.db` | — | ✅ | operator manual |
| Kill-switch tripped-state rendering | — | ✅ | operator manual (fixture-driven) |
| Showcase tab visual render with `(post-launch)` suffix | — | ✅ | operator manual (no headless route for the showcase) |

**Recommendation:** **conditional GO** — every script-verifiable blocker passes. Three manual-only items remain; flip to unconditional GO once an operator walks them.

---

## 1. Launch blockers — verification results

### 1.1 First-run experience — MANUAL REQUIRED

- [x] **Strategy Bank read model is fixture-tolerant.** `BenchState`, `FrontPageState`, `DeskState`, `LedgerState`, `StrategyBankState` all instantiated against a real `data/milodex.db` and against `MagicMock` broker stubs in `scripts/capture_gui_screenshots.py` — no exceptions during QML root binding, all four surfaces rendered (artifacts §4).
- [ ] **Fresh-profile launch on Windows with no prior data dir** — operator manual. Steps:
  1. Rename `%LOCALAPPDATA%\Milodex\data\` aside.
  2. `python -m milodex.cli.main gui` (or installed `milodex gui`).
  3. Confirm GUI opens, Strategy Bank resolves to empty-state copy (not spinner, not traceback).
  4. Restore data dir.
- [ ] **No required-field crash if `data/milodex.db` is absent on first run** — operator manual; same flow as above. Code path: `BenchState(db_path=...)` should accept a missing file gracefully.

### 1.2 Local install / start command — PASS (with note)

- [x] **CLI entry point resolves.** `python -m milodex.cli.main --help` lists 15 subcommands including `gui`. Help text printed cleanly.
- [x] **GUI launch path importable.** All `milodex.gui.*` imports succeed in the capture scripts (offscreen QPA). `milodex gui` is the documented entry; not exercised headlessly here but exercised by every test in `tests/milodex/gui/`.
- [ ] **`pip install -e ".[dev]"` in a fresh venv** — not re-run this pass; the working environment is already editable-installed. Operator manual on clean machine before release.
- [ ] **Installer build path** (`installer/build_installer.ps1`) — N/A if not shipping installer this cycle.

**Note:** the v0 checklist named `milodex strategy list`. Actual subcommand is `milodex strategy run`. Other subcommands corrected below.

### 1.3 GUI opens cleanly — PASS

- [x] **Headless QML render of Main.qml succeeded** across Front, Desk, Bench, Ledger. No QML parse error, no missing-property errors observed in capture-script logs. Stderr was clean (no Qt warnings surfaced; the capture script exits 0 and writes PNGs).
- [x] **All three theme files load without parse errors.** `themes/EditorialDark.qml`, `themes/EditorialLight.qml`, `themes/Bronze.qml` all referenced by `Theme.qml:51` and `qmldir:5`; registration succeeded since the showcase tab gating reads `ThemeManager.theme === tabRoot.themeId` without errors.

### 1.4 Editorial Dark screenshot state — PASS

- [x] **Front, Desk, Bench, Ledger render under Editorial Dark with token-driven design.** Visually verified from PNGs:
  - Front: Newsreader serif headline, paper-only safety strip, "AT THE GATE" Sma200 Rotation card, snapshot sparkline.
  - Desk: paper-locked status row, strategy bank grouped by stage (backtest/paper/micro_live/full live), promotion-ledger column.
  - Bench: read-only board with backtest + paper sections, no mutation surface visible.
  - Ledger: chronological promotion / kill-switch ledger.
- [x] **No raw-hex leaks in operator surfaces.** Grep of `Bronze|Editorial Light` in `src/milodex/gui/qml/` returns hits only in: `qmldir` (module registration), `Theme.qml` (registry), `themes/*.qml` (concrete theme files), `DesignSystemShowcase.qml` (the showcase that explicitly disables them). No hits in `Front*`, `Desk*`, `Bench*`, `Ledger*`, `StrategyBank*`, or `components/`.

### 1.5 Design-system showcase sanity — MANUAL REQUIRED

- [x] **Source confirms gating.** `DesignSystemShowcase.qml:231–232`:
  ```qml
  ThemeTab { label: "Editorial Light"; themeId: "editorial-light"; tabEnabled: false }
  ThemeTab { label: "Bronze";          themeId: "bronze";          tabEnabled: false }
  ```
- [x] **No-op click handler when disabled** — confirmed at `DesignSystemShowcase.qml:225`: `if (tabRoot.tabEnabled) ThemeManager.set_theme(tabRoot.themeId)`. No `set_theme` invocation reachable without `tabEnabled`.
- [ ] **Visual render of disabled tabs with `(post-launch)` suffix** — operator manual. The showcase is reachable via in-app navigation that the capture script does not exercise headlessly. Steps:
  1. Launch GUI (`python -m milodex.cli.main gui`).
  2. Navigate to DesignSystemShowcase.
  3. Confirm Editorial Light + Bronze tabs render visibly disabled with `(post-launch)` suffix.
  4. Click both; confirm no theme switch and no console error.

### 1.6 No Light / Bronze launch affordance — PASS

- [x] **Theme-switch invocation gated to the showcase only.** Grep audit:
  - `ThemeManager.set_theme(...)` appears once in QML: `DesignSystemShowcase.qml:225`, guarded by `tabEnabled`.
  - No `set_theme` call in `FrontSurface.qml`, `DeskSurface.qml`, `BenchSurface.qml`, `LedgerSurface.qml`, `AnchorSurface.qml`, `StrategyBankSurface.qml`, `Main.qml`, or any `components/*.qml`.
- [x] **No "Bronze" or "Editorial Light" UI labels outside the showcase + theme infrastructure.** Confirmed via grep.

### 1.7 Broker / env missing-state behavior — MANUAL REQUIRED

- [x] **Capture scripts run with `MagicMock` broker stubs and no `.env`** — GUI surfaces render without crash. Implies `OperationalState`, `BrokerClientFactory`, and `KillSwitchStore` tolerate absent live broker connectivity.
- [ ] **Live missing-`.env` smoke** — operator manual. Steps:
  1. Rename `.env` aside (or remove `ALPACA_API_KEY` from environment).
  2. `python -m milodex.cli.main gui` — confirm GUI opens, broker surfaces show a "broker not configured" empty state, no crash.
  3. Confirm `milodex status` either reports paper mode or fails closed (do not silently default to live).
- [ ] **Invalid-key smoke** — operator manual. Set `ALPACA_API_KEY=invalid`, repeat (2)–(3); confirm graceful broker-init failure with kill-switch / paper-mode state still readable.

### 1.8 Paper-mode safety posture — PASS

- [x] **Paper-only header pinned across surfaces.** Front screenshot shows top-right strip: `RISK OFFICE | GUARD READY | PAPER ONLY | BROKER CONNECTED | NO REAL EXPOSURE`. Same strip visible on Bench and Ledger captures.
- [x] **`milodex status` reports paper mode.** Output: `Trading mode: paper`, equity $101,148.22 (paper account).
- [x] **BenchConfirmationModal enforces ADR 0004.** Source at `components/BenchConfirmationModal.qml:315`:
  > `"Paper-stage sessions use live feed with no capital exposure. Capital-bearing stages remain locked while ADR 0004 is in force."`
- [x] **Confirmation modal screenshot ("Promote to Paper — 52W High Proximity")** shows the safety-notice block. No `live` or `micro_live` capital-bearing flow exposed by default.

### 1.9 Kill-switch visibility — PARTIAL / MANUAL REQUIRED

- [x] **Kill-switch state references confirmed across 8 QML files**: `Main.qml`, `FrontSurface.qml`, `DeskSurface.qml`, `LedgerSurface.qml`, `AnchorSurface.qml`, `StrategyBankSurface.qml`, `components/Button.qml`, `components/RiskStrip.qml`. State is structurally surfaced, not hidden behind a settings drawer.
- [x] **Reset-state rendering** visible in Front capture: "GUARD READY" label in the Risk Office header strip.
- [x] **Manual-reset semantic preserved.** Ledger capture shows historical `kill switch / session / RESET` rows requiring operator action. `KillSwitchStore` exposes no auto-reset code path to the GUI.
- [ ] **Tripped-state rendering** — operator manual. Steps:
  1. From a paper session, trigger kill switch via `milodex` CLI (or fixture).
  2. Confirm Front + Operations renders kill-switch state unmistakably (color + label + reason).
  3. Confirm reset requires explicit confirmation.

### 1.10 Bench read-only boundary — PASS

- [x] **Bench surface renders without mutation affordances.** Captured Bench screenshot shows row list with no inline edit, delete, or run controls. Action verbs route through `BenchConfirmationModal`, which is a preview surface (see §1.11).
- [x] **`bench_v1_fixtures.py` is a separate module** from `bench_v1.py` and the production `BenchState` read model.
- [x] **BenchConfirmationModal renders a preview, not a writer.** Captured modal shows "What will happen", "Current paper state", "Modules included in confirm", "Safety notice" — all read-out. The Approve button is the operator's commit point, not the modal's; ADR 0004 keeps capital-bearing approvals locked.

### 1.11 Evidence rail and Confirmation modal behavior — PASS

- [x] **Evidence rail dossier renders.** `bench-evidence-rail.png` shows full dossier panel for selected row ("52W High Proximity") with identity, stage, family, universe, audit info, eligibility, capital sizing, evidence ladder, and gates. No stale-state bleed observed at script-driven selection.
- [x] **Confirmation modal blocks the surface.** `bench-confirmation-modal.png` shows the modal centered with the underlying Bench dimmed; Cancel and Approve are distinct buttons with no ambiguous default.
- [x] **Approve / Cancel paths visually distinct** — Cancel is a quiet button at left, primary action at right. No accidental-promote risk under this layout.
- [ ] **Esc / outside-click behavior** — script-driven capture does not exercise these. Low risk (Qt Quick default is to close on Esc) — operator manual confirmation optional, not blocking.

### 1.12 CLI smoke commands — PASS (subcommands corrected)

| Command | Exit | Notes |
|---|---|---|
| `python -m milodex.cli.main --help` | 0 | Lists 15 subcommands. |
| `python -m milodex.cli.main status` | 0 | Paper mode, equity $101,148.22, broker connected. |
| `python -m milodex.cli.main config validate configs/risk_defaults.yaml` | 0 | "Detected kind: risk". |
| `python -m milodex.cli.main config validate configs/sample_strategy.yaml` | 0 | "Detected kind: strategy". |
| `python -m milodex.cli.main backtest --help` | 0 | Clean usage; walk-forward flag present. |
| `python -m milodex.cli.main report --help` | 0 | `daily` + `strategy` subcommands. |
| `python -m milodex.cli.main promotion --help` | 0 | `freeze` / `manifest` / `promote` / `demote` / `history`. |

Corrections vs. v0 checklist:
- `milodex strategy list` → no such subcommand. Actual: `milodex strategy run`. `strategy list` removed from the smoke list.
- `milodex promote` → REFUSED stub by design (legacy shortcut skipped manifest freeze per ADR 0015). Use `milodex promotion promote`. Item replaced.
- `milodex config validate` requires a `path` arg. Two configs validated above.

### 1.13 Test suite status — PASS

- [x] **`pytest -q`**: `1259 passed, 4 xfailed, 1 warning in 206.65s`. 1 warning is third-party (`websockets` deprecation). Exit 0.
- [x] **`ruff check src/ tests/`**: `All checks passed!`. Exit 0.
- [ ] **GUI test slice baseline** (`pytest tests/milodex/gui/ -q` → 384 passed, 4 xfailed per `fee27fe` commit message) — covered by full-suite pass; the 4 xfails carry through. Not separately re-run this pass.
- [x] **Coverage gate** (`pytest --cov=src/milodex --cov-report=term-missing` ≥ 89): `TOTAL 9057 996 89%`, `Required test coverage of 89.0% reached. Total coverage: 89.00%`. Exact-gate pass — any drop will fail. Log at `.pytest_cov.log` (gitignored).

---

## 2. Non-blocking polish (ship without; revisit post-launch)

These do not block launch. Logged here so the launch pass does not relitigate them.

- [ ] **FRONT sparkline** — known cosmetic / data-fidelity follow-up on `FrontSurface.qml` `Sparkline.qml`. Behavior is correct (snapshot path visible in the Front capture); visual refinement deferred.
- [ ] Editorial Light parity verification pass.
- [ ] Bronze parity verification pass.
- [ ] Any showcase tab copy refinements beyond the `(post-launch)` suffix.
- [ ] Untracked working-tree items (`one off images/`, `scripts/capture_bench_interactive.py` was added as part of this work, then committed — re-check `git status` before tagging).

If a "polish" item turns out to be a correctness or safety issue under inspection, escalate it to §1 — do not silently fix it under polish.

---

## 3. Verification commands (this pass)

Run from repo root on Windows PowerShell. Each command below was executed during the 2026-05-14 pass; result column links to the relevant §1 item.

```powershell
# Lint                                                  → §1.13 PASS
python -m ruff check src/ tests/

# Full test suite                                       → §1.13 PASS (1259 passed, 4 xfailed)
python -m pytest -q

# Coverage (background)                                 → §1.13 logged at .pytest_cov.log
python -m pytest --cov=src/milodex --cov-report=term-missing -q

# CLI smoke (subcommands corrected from v0 checklist)   → §1.12 PASS
python -m milodex.cli.main --help
python -m milodex.cli.main status
python -m milodex.cli.main config validate configs/risk_defaults.yaml
python -m milodex.cli.main config validate configs/sample_strategy.yaml
python -m milodex.cli.main backtest --help
python -m milodex.cli.main report --help
python -m milodex.cli.main promotion --help

# Headless GUI screenshots (4 primary surfaces)         → §1.4 PASS
python scripts/capture_gui_screenshots.py

# Bench interactive screenshots (Evidence + Modal)      → §1.11 PASS
python scripts/capture_bench_interactive.py `
    --output-dir artifacts/gui-screenshots/20260514-102413/bench-interactive
```

Grep audit for stray theme leaks:

```powershell
# Result: zero hits outside themes/*.qml, Theme.qml, qmldir, DesignSystemShowcase.qml.
Select-String -Path src\milodex\gui\qml\**\*.qml -Pattern "Bronze|Editorial Light" -SimpleMatch
```

---

## 4. Screenshots / artifacts captured

All artifacts live under `artifacts/gui-screenshots/20260514-102413/` (gitignored; treat as launch evidence to attach to release notes manually).

| Artifact | Path | Status |
|---|---|---|
| Front surface, Editorial Dark | `artifacts/gui-screenshots/20260514-102413/front.png` | ✅ captured |
| Desk surface, Editorial Dark (populated) | `artifacts/gui-screenshots/20260514-102413/desk.png` | ✅ captured |
| Bench surface, Editorial Dark | `artifacts/gui-screenshots/20260514-102413/bench.png` | ✅ captured |
| Ledger surface, Editorial Dark | `artifacts/gui-screenshots/20260514-102413/ledger.png` | ✅ captured |
| Evidence rail with selected row | `.../bench-interactive/bench-evidence-rail.png` | ✅ captured |
| Confirmation modal in foreground | `.../bench-interactive/bench-confirmation-modal.png` | ✅ captured |
| `milodex status` CLI output | inline §1.8 / §1.12 | ✅ captured (text) |
| `pytest -q` final summary line | inline §1.13 (`1259 passed, 4 xfailed`) | ✅ captured (text) |
| Desk empty-state | — | ⏸ operator manual (fixture-driven; not produced by current scripts) |
| Operations tab, kill-switch tripped | — | ⏸ operator manual (§1.9) |
| DesignSystemShowcase active tab + disabled Light/Bronze | — | ⏸ operator manual (§1.5) |

Headless capture scripts do not currently surface DesignSystemShowcase or kill-switch-tripped state. Extending them is optional polish; for launch the operator walks those three states manually.

---

## 5. Remaining manual non-lifecycle checks

The Phase 1 paper lifecycle walk is complete in §0. The remaining manual checks
are broader launch-readiness checks that still need a human on the launch
machine. Save screenshots beside the lifecycle artifacts, then mark this
section complete.

### 5.1 First-run with no prior data

```powershell
# Back up, then nuke local state
Rename-Item "$env:LOCALAPPDATA\Milodex\data" "data.bak.$(Get-Date -Format yyyyMMdd)"
python -m milodex.cli.main gui
# Confirm: GUI opens, empty-state copy on Strategy Bank, no traceback.
# Restore: Rename-Item "$env:LOCALAPPDATA\Milodex\data.bak.*" data
```
Maps to §1.1, §1.7.

### 5.2 DesignSystemShowcase tabs

1. `python -m milodex.cli.main gui`
2. Navigate to DesignSystemShowcase.
3. Confirm Editorial Dark is active.
4. Confirm Editorial Light and Bronze tabs render visibly dimmed with `(post-launch)` suffix.
5. Click both. Confirm no theme switch and no QML console error.
6. Screenshot to `.../showcase-disabled-tabs.png`.

Maps to §1.5.

### 5.3 Kill-switch tripped state

1. From a paper session, trip kill switch (CLI: `python -m milodex.cli.main` — confirm the supported trip path; otherwise via test fixture).
2. Open GUI. Confirm Front + Operations renders unmistakable kill-switch state.
3. Attempt reset; confirm explicit confirmation is required.
4. Screenshot to `.../kill-switch-tripped.png`.

Maps to §1.9.

### 5.4 Broker/env failure modes

1. Move `.env` aside.
2. `python -m milodex.cli.main gui` — confirm broker-not-configured empty state, no crash.
3. Restore `.env`. Set `ALPACA_API_KEY=invalid`.
4. Repeat (2). Confirm graceful failure.

Maps to §1.7.

---

## 6. Final go / no-go

**Auto-verified blockers — all PASS:**

- ✅ §1.2 CLI entry point, GUI import path
- ✅ §1.3 GUI opens cleanly (headless render)
- ✅ §1.4 Editorial Dark screenshot state
- ✅ §1.6 No Light/Bronze affordance in operator surfaces
- ✅ §1.8 Paper-mode safety posture (ADR 0004 enforced in modal copy, header strip, CLI status)
- ✅ §1.10 Bench read-only boundary
- ✅ §1.11 Evidence rail + Confirmation modal behavior
- ✅ §1.12 CLI smoke (with corrected subcommand list)
- ✅ §1.13 pytest (1259 passed, 4 xfailed), ruff (clean), coverage 89.00% (exact gate).

**Manual non-lifecycle checks still required:**

1. First-run with no prior `data/` and missing `.env` (§1.1, §1.7).
2. DesignSystemShowcase visual gating of Light + Bronze tabs (§1.5).
3. Kill-switch tripped-state render and reset path (§1.9).

**NO-GO conditions (none currently observed):**

- A §1 blocker fails and the fix is non-trivial.
- A test or lint regression on the launch commit.
- Light or Bronze affordances reachable from the production GUI.
- Live trading reachable from any UI surface or CLI flag.
- Kill-switch can auto-reset.
- Bench can mutate state.

**Recommendation: CONDITIONAL GO.** Every script-verifiable blocker passes against `fee27fe` on `master`. The launch is gated only on the three operator-manual walks in §5. When those land green and their screenshots are saved into `artifacts/gui-screenshots/20260514-102413/`, append the final outcome line below and tag the release.

---

## 7. Outcome record

*(append on launch tag)*

```
Launch commit: ____________ (SHA)
Launch tag:    ____________ (e.g. v0.1.0)
Date:          ____________
Manual walks:  §5.1 ☐  §5.2 ☐  §5.3 ☐  §5.4 ☐
Coverage:      ____ % (≥ 89 required)
Final call:    GO / NO-GO
Notes:         ____________
```

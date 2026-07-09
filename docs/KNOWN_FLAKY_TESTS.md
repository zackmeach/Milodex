# Known Flaky Tests

## RESOLVED 2026-07-09: CI-slow async-wait flakes in bench-bridge and operational-state tests

### Affected (no longer flaky)

```
tests/milodex/gui/test_bench_command_bridge.py::test_submit_paper_runner_async_methods_return_queued_and_emit_final_results
tests/milodex/gui/test_operational_state.py::test_broker_failure_sets_error_status
```

Both flaked on GitHub Actions on 2026-07-09 (runs 29046179518 and 29048702843) on
docs-only/test-only PRs, passed locally and on CI rerun.

### Root causes (two distinct defects in the same family)

- **Bench bridge:** `_process_qt_until` was condition-based but carried a **2s
  default ceiling** — too tight for an xdist-loaded CI runner to schedule the
  async bridge worker. Fixed by raising the default ceiling to 10s (free on the
  happy path; deliberate negative-waits pass their own short `timeout_ms`).
- **Operational state:** `_wait_for_pool` waited on `dataStatus != "loading"`,
  which is only sound for a **first** poll. `PollingReadModel._kick_refresh`
  never sets `dataStatus` back to "loading", so after a first poll lands the
  status is already terminal and the wait returns before a **second** kick's
  worker runs — the two multi-kick tests (`test_broker_failure_sets_error_status`,
  `test_broker_recovery_clears_error`) raced it. Fixed by waiting on
  `_refresh_in_flight` going False (set True synchronously by the kick, cleared
  only in the completion/failure slot — sound for every poll). The other six
  `_wait_for_pool` copies in the gui tests are single-poll-only, so their
  status-based condition remains sound and was left unchanged.

---

## RESOLVED 2026-06-17: in-process Qt application-singleton pollution

### Affected (no longer flaky)

```
tests/milodex/gui/test_fonts.py::test_load_fonts_returns_nonzero_count
tests/milodex/gui/test_fonts.py::test_load_fonts_finds_all_three_families
tests/milodex/gui/test_qml_components.py::test_button_primary_instantiates_with_correct_variant
```
…and the intermittent **Win32 access violation in a Qt worker** during `pytest tests/milodex/gui`.

### Root cause (distinct from the subprocess quarantine below)

Qt permits exactly one `QCoreApplication`-family object per process and does not tolerate mixing
kinds. Several gui test modules create a **bare `QCoreApplication`** (e.g.
`test_bench_command_bridge._process_qt_until`), while the font/QML tests need a **GUI-capable**
application with a font subsystem. Each module used `<AppClass>.instance() or <AppClass>(...)`, so
*whichever* test ran first in a process won the singleton. Serial (`-n0`) always passed because an
early gui test established a `QGuiApplication` everyone reused — verified: `pytest tests/milodex/gui
-n0` = 846 passed. Under **pytest-xdist** (`-n auto`, the default) the tests scatter across worker
processes, so a worker could run the bare-`QCoreApplication` test (or no gui-app test at all) before
a font/QML test → `QFontDatabase.addApplicationFont` returns `-1` (`loaded_count == 0`), or a native
access-violation crash when `test_fonts` then constructs a `QApplication` over the bare core app.

### Fix

`tests/milodex/gui/conftest.py` constructs the most-derived `QApplication` once, at conftest import
(which precedes every test module in the directory during collection), held in a module global. It
becomes THE singleton that every `*.instance() or *Application(...)` call in the package reuses —
GUI-capable for the font/QML tests, and reused (never a new bare core app) by the `QCoreApplication`
call sites. No coverage lost; no test skipped; no change to the default xdist dist mode.

Verified: `pytest tests/milodex/gui` (xdist) green across 4 consecutive runs; full suite green across
3 consecutive runs (was intermittently red before).

### Not addressed by this fix

The subprocess quarantine below is a **different** mechanism (process-global QML *type-cache*
pollution affecting a freshly-spawned subprocess, not the in-process application singleton). The
conftest application singleton does not propagate into subprocesses, so that test stays quarantined.

---

## RESOLVED 2026-06-21: showcase subprocess test un-quarantined

`tests/milodex/gui/test_app.py::test_design_system_showcase_loads_without_errors_via_subprocess`
is **no longer skipped.** Its `@pytest.mark.skip` + `@pytest.mark.flaky_qt_pollution` markers were
removed, and the now-unused `flaky_qt_pollution` marker was dropped from `pyproject.toml`.

**Why it's safe now:** the showcase flake shared its root cause with its sibling
`test_main_qml_loads_without_errors_via_subprocess` — the `QFontDatabase: Cannot find font directory`
symptom, fixed once fonts were bundled under `src/milodex/gui/assets/fonts/` (loaded by
`gui/fonts.py:load_fonts`). The sibling was un-quarantined earlier for that reason; the showcase
lagged only because nobody re-verified it.

**Evidence (2026-06-21):** un-skipped and run **10 times** in full-suite context
(`pytest tests/milodex/gui` under the default xdist `-n auto`, 9 runs + 1 `--runxfail` pass) — green
every run (`847 passed`). If it ever shows a lone failure in a full-suite run, re-run it by node ID in
isolation to confirm before calling it a regression.

> **The inline-instantiation xfails are a DIFFERENT, still-open residual — do NOT remove them.** Four
> tests in `test_qml_components.py` (`test_button_danger_instantiates_with_correct_variant`,
> `test_status_pill_paper_instantiates`, `test_status_pill_killed_instantiates`,
> `test_strategy_row_instantiates_and_exposes_properties`) stay `@pytest.mark.xfail(strict=False)`.
> Verified 2026-06-21 under `--runxfail`: they still genuinely FAIL in full-suite xdist
> (`QQmlComponent.Error` — the process-global QML *type-cache* compiles the Milodex module before
> QtQuick.Layouts resolves). They pass in isolation (the occasional xpass). That type-cache root cause
> is NOT fixed, so these correctly remain xfail.

---

## Historical: Qt/QML subprocess pollution quarantine (2026-05-17 → resolved 2026-06-21 above)

### Affected test IDs (now un-quarantined — see Resolved section above)

```
tests/milodex/gui/test_app.py::test_design_system_showcase_loads_without_errors_via_subprocess
```

(Originally two tests were quarantined here. `test_anchor_surface_loads_without_errors_via_subprocess`
was removed when HR-4 (#220) deleted `AnchorSurface.qml`, so only the showcase load-smoke test remained
quarantined — and it is now resolved.)

### Same class, deliberately NOT quarantined

```
tests/milodex/gui/test_app.py::test_main_qml_loads_without_errors_via_subprocess
```

This subprocess-QML-load test belongs to the same pollution class but is **kept active (not skipped)**
because it is the primary app-QML smoke test — losing that coverage to silence a flake is the wrong
trade. It now passes reliably: the earlier `QFontDatabase: Cannot find font directory` symptom is gone
because fonts are bundled under `src/milodex/gui/assets/fonts/` and loaded by `gui/fonts.py:load_fonts`.
If it ever shows a lone failure in a full-suite run, treat it as the pollution class below — **not** a
regression — and confirm by re-running it in isolation by node ID (it passes). Root-cause remediation
is the same deferred task.

### Symptom

The quarantined test **passes reliably when run in isolation.** In a full `pytest tests/milodex/gui`
run it can fail nondeterministically, and the suite occasionally also produces a Win32 access violation
in a Qt thread-pool worker.

### Investigation conclusion

- **Pre-existing:** reproduces at commit `d762ecd`, before any Trading Desk feature work.
- **Environmental / timing:** failure pattern is nondeterministic; no code-logic defect found.
- **Root cause:** process-global Qt/QML type-cache state accumulated by earlier tests in the
  gui suite contaminates the subprocess-launch environment.
- **Not caused by the Trading Desk feature.**

### Quarantine rationale

Skipped (not xfail) to make full-suite CI deterministic.  `xfail` was rejected because the
test often passes in isolation; an `xfail` without `strict=True` would generate xpass noise,
and `strict=True` would flip the failure direction.  Skip is the unambiguous deterministic
choice.

The quarantined test carries `@pytest.mark.flaky_qt_pollution` so it remains greppable and can be
run on-demand in isolation (remove the `@pytest.mark.skip` locally, or run by node ID in a fresh
process).

### Real fix

Root-cause remediation — eliminating the process-global Qt/QML type-cache pollution across
the gui test suite — is deferred to a separate tracked task.  When fixed, remove the
`@pytest.mark.skip` decorator and re-verify full-suite determinism.

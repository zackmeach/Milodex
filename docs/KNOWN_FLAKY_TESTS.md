# Known Flaky Tests

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

## Quarantined: Qt/QML subprocess pollution (2026-05-17)

### Affected test IDs

```
tests/milodex/gui/test_app.py::test_design_system_showcase_loads_without_errors_via_subprocess
```

(Originally two tests were quarantined here. `test_anchor_surface_loads_without_errors_via_subprocess`
was removed when HR-4 (#220) deleted `AnchorSurface.qml`, so only the showcase load-smoke test remains
quarantined.)

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

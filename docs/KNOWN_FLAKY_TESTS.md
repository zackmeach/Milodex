# Known Flaky Tests

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

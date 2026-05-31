# Known Flaky Tests

## Quarantined: Qt/QML subprocess pollution (2026-05-17)

### Affected test IDs

```
tests/milodex/gui/test_app.py::test_design_system_showcase_loads_without_errors_via_subprocess
tests/milodex/gui/test_app.py::test_anchor_surface_loads_without_errors_via_subprocess
```

### Same class, deliberately NOT quarantined

```
tests/milodex/gui/test_app.py::test_main_qml_loads_without_errors_via_subprocess
```

This third subprocess-QML-load test exhibits the **identical** pollution flake (passes
in isolation; an occasional lone failure in a full-suite run, emitting `QFontDatabase:
Cannot find font directory`). It is **kept active (not skipped)** because it is the
primary app-QML smoke test — losing that coverage to silence a flake is the wrong
trade. Treat a single failure of it in a full run as the known pollution class below,
**not** a regression; confirm by re-running it in isolation by node ID (it passes —
verified 3/3 on 2026-05-31). Root-cause remediation is the same deferred task.

### Symptom

Both tests **pass reliably when run in isolation**.  In a full `pytest tests/milodex/gui`
run they fail nondeterministically — sometimes both fail, sometimes only one, occasionally
the suite also produces a Win32 access violation in a Qt thread-pool worker.

### Investigation conclusion

- **Pre-existing:** reproduces at commit `d762ecd`, before any Trading Desk feature work.
- **Environmental / timing:** failure pattern is nondeterministic; no code-logic defect found.
- **Root cause:** process-global Qt/QML type-cache state accumulated by earlier tests in the
  gui suite contaminates the subprocess-launch environment for these two tests.
- **Not caused by the Trading Desk feature.**

### Quarantine rationale

Skipped (not xfail) to make full-suite CI deterministic.  `xfail` was rejected because the
tests often pass in isolation; an `xfail` without `strict=True` would generate xpass noise,
and `strict=True` would flip the failure direction.  Skip is the unambiguous deterministic
choice.

Both tests carry `@pytest.mark.flaky_qt_pollution` so they remain greppable and can be
run on-demand in isolation (remove the `@pytest.mark.skip` locally, or run by node ID
in a fresh process).

### Real fix

Root-cause remediation — eliminating the process-global Qt/QML type-cache pollution across
the gui test suite — is deferred to a separate tracked task.  When fixed, remove both
`@pytest.mark.skip` decorators and re-verify full-suite determinism.

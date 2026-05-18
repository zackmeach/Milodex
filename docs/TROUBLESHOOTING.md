# Troubleshooting

## GUI-launched runners die silently / "no explanations" / phantom runners

**Date first hit:** 2026-05-18 (first real GUI usage).

### Symptom

- You launch the GUI, click Start Trading, a runner appears to start, but it
  produces **no explanations** and no trades.
- The GUI shows runners as "running" that have no live process ("phantoms").
- Multiple `python.exe` processes appear, some under `.venv\Scripts\python.exe`
  and some under the base interpreter (e.g. `…\Python312\python.exe`), with
  identical command lines.

### Root cause

`.venv\Scripts\python.exe` was a **uv (`uv venv`) trampoline**, not a stdlib
`python -m venv` interpreter. A trampoline does not run code itself — it
`CreateProcess`-spawns the **base** interpreter with the identical argv, and
your code executes in that base child. If the base interpreter's
site-packages are broken (here: a corrupt `pandas` —
`ModuleNotFoundError: No module named 'pandas.core.groupby.generic'`), every
runner dies on `import milodex.cli.main`. Detached runners historically sent
stdout/stderr to `DEVNULL`, so the failure was invisible.

The redirector + base-child **PID pair per process is normal Windows venv
behaviour** (the stdlib venv `python.exe` is also a ~270 KB redirector). The
pair is not a bug and not "self-duplication" — do not chase it.

### 5-minute diagnostic

```powershell
# 1. Is the venv python a trampoline to a different base?
.venv\Scripts\python.exe -c "import sys,os; print(os.getpid())"   # note pid
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'os.getpid' } |
  Select ProcessId, ParentProcessId, ExecutablePath
# Two rows (venv path + base path), code prints the *child's* pid => trampoline.

# 2. Does the venv actually resolve good deps via the runner entrypoint?
.venv\Scripts\python.exe -c "import pandas, milodex.cli.main; print(pandas.__file__)"
# Must resolve pandas from .venv\Lib\site-packages and not raise.

# 3. Newest runner log (exists since PR #158) — read the real error:
Get-ChildItem logs\runner.*.log | Sort LastWriteTime | Select -Last 1 | Get-Content
```

### Fix

Rebuild the venv with the **stdlib** tool, reinstall deps:

```powershell
Remove-Item .venv -Recurse -Force
& "C:\Users\zdm80\AppData\Local\Programs\Python\Python312\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -c "import pandas, milodex.cli.main; print('ok', pandas.__version__)"
```

Do **not** recreate it with `uv venv` (reintroduces the trampoline). Separately,
the base Python312's broken `pandas` is a latent footgun for other tooling —
worth fixing in the base install, but it no longer affects Milodex once `.venv`
is a real stdlib venv.

### Related guardrails (already on `master`)

These make a future recurrence loud and self-healing rather than silent:

- **#158** — runner stdout/stderr go to `logs/runner.<sid>.<ts>.log` (no more `DEVNULL`).
- **#159** — before spawning a runner, probe `import milodex.cli.main` in the
  chosen interpreter; refuse loudly if it fails.
- **#160** — refuse a duplicate runner launch when a live advisory-lock holder exists.
- **#161** — at GUI bootstrap, liveness-gated reconciliation closes orphaned
  `strategy_runs` so phantom "running" rows self-heal.

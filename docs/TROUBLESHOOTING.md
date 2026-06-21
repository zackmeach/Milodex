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

---

## SQLite event store is corrupt or locked ("file is not a database" / "database is locked")

**Date documented:** 2026-06-21 (F3 failure/recovery drill).

### Symptom

- A read command (`milodex report`, `milodex analytics list`) exits 1 with:
  `Error: Unexpected error (DatabaseError): file is not a database. Full traceback written to the log directory.`
- Or a write command (`milodex maintenance compact --apply`, a runner) hangs ~30s then exits 1 with:
  `Error: Unexpected error (OperationalError): database is locked.`

### Root cause

- *Corrupt:* `data/milodex.db` no longer has a valid SQLite header (truncated write, disk error, an
  editor saving over it). `EventStore.__init__` runs `PRAGMA journal_mode=WAL` on construction, which
  fails immediately. The CLI wraps it in the generic `unexpected_error` net (`cli/main.py`), so the
  message is raw sqlite, not "corrupt — restore a backup."
- *Locked:* another process holds a write lock. WAL mode lets readers proceed, so **reads are
  unaffected** — only writers contend. The connection's `busy_timeout=30000` means a writer waits up
  to 30s before surfacing "database is locked."

### Diagnostic

```powershell
# Is it corrupt? A healthy DB starts with the SQLite magic header.
.venv\Scripts\python.exe -c "print(open(r'data\milodex.db','rb').read(16))"
# Healthy => b'SQLite format 3\x00'. Garbage/short => corrupt.

# Who holds the lock? Look for a live runner / GUI / second CLI.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId, CommandLine
# Stale WAL/SHM siblings can indicate an unclean shutdown:
Get-ChildItem data\milodex.db*
```

### Fix

- *Locked:* wait for / stop the other writer (a runner, the GUI, or another CLI). If the holder is
  dead (phantom), clear `data\locks\*.lock` and remove stale `data\milodex.db-wal` / `-shm`. Re-run.
- *Corrupt:* restore the most recent backup (compaction writes `data\milodex.db.pre-compact-*.bak`):
  ```powershell
  Get-ChildItem data\milodex.db*.bak* | Sort LastWriteTime   # pick newest
  Copy-Item data\milodex.db.pre-compact-<ts>.bak data\milodex.db -Force
  ```
  If no backup exists, delete `data\milodex.db` (and `-wal`/`-shm`) — the store recreates an empty
  schema on next run. **You lose all trade/explanation history**, so prefer a backup.

---

## CLI commands fail with "APIError: unauthorized" or "Broker: UNREACHABLE"

**Date documented:** 2026-06-21 (F3 failure/recovery drill; extends the F1 clean-room audit).

### Symptom

- `milodex status` / `positions` / `orders` exit 1 with
  `Error: Unexpected error (APIError): {"message": "unauthorized."}` (bad/expired keys) or a clear
  `Error: ALPACA_API_KEY is not set. Copy .env.example to .env...` (no keys).
- `milodex report` shows `Broker: UNREACHABLE (...)` — `(ALPACA_API_KEY is not set...)` for missing
  keys, or an `(<html>...` fragment for a bad key.

### Root cause

- Missing keys are detected pre-flight and reported cleanly (actionable).
- Bad/expired keys: Alpaca returns HTTP 401. The `APIError`→`BrokerAuthError` translation currently
  lives only in `alpaca_client.py:submit_order`; the read paths (`get_account`, `get_orders`,
  `get_positions`) pass the raw `APIError` through, so it lands in the generic `unexpected_error` net
  and reads as cryptic. **All broker commands fail closed (exit 1) — the system never proceeds as if
  live on a broker error.** (A follow-up to add the translation to the read paths is filed.)

### Diagnostic

```powershell
# Confirm the keys actually loaded and look like paper keys.
.venv\Scripts\python.exe -c "from milodex.config import get_alpaca_credentials,get_trading_mode; k,_=get_alpaca_credentials(); print(get_trading_mode(), (k[:4] if k else None))"
# Full traceback for the 401 (ends in alpaca .../get_account):
Get-ChildItem logs\milodex-*.log | Sort LastWriteTime | Select -Last 1 | Get-Content -Tail 30
```

### Fix

- "ALPACA_API_KEY is not set": copy `.env.example` to `.env`, fill `ALPACA_API_KEY` /
  `ALPACA_SECRET_KEY` / `TRADING_MODE`.
- "unauthorized" / 401: the keys are wrong, revoked, or paper/live-mismatched. Verify in the Alpaca
  dashboard, confirm `TRADING_MODE=paper` matches paper keys, and that no shell env var is shadowing
  `.env`.
- A non-auth connect/timeout error → Alpaca outage or network: retry; check Alpaca status.

---

## "Data: stale" in the trust report (and the silent 200-DMA cache gap)

**Date documented:** 2026-06-21 (F3 failure/recovery drill).

### Symptom

- `milodex report` shows `Data: stale (latest bar <old timestamp>)` and a `Market data is stale`
  warning. (Staleness alone still prints `Operator action: none required` — it is a warning, not an
  action trigger.)
- OR a daily runner is alive but produces 0 explanations after close, with **no** "stale" warning —
  the report shows the last good bar or "no bars recorded yet."

### Root cause

- The report's freshness check ages the latest **live explanation row's** `latest_bar_timestamp`
  against a 24h threshold (`operations/freshness.py`, `report.py:_data_freshness`). It reflects bars
  the fleet actually recorded — **not the parquet cache**.
- Therefore an *interior* cache gap (e.g. a missing 2025 between an old backtest warm and a later
  runner tail) is **invisible to the report**: it collapses the 200-DMA rolling mean to all-NaN, the
  runner idles without recording, and there is no fresh row to flag. Only a per-year bar count can
  see this class. (See the CLAUDE.md daily-cache gotcha.)

### Diagnostic

```powershell
# A healthy year ~= 250 daily bars. A near-zero interior year = the gap.
.venv\Scripts\python.exe -c "import pandas as pd; df=pd.read_parquet(r'market_cache\v3\1Day\SPY.parquet'); print(df.index.year.value_counts().sort_index())"
.venv\Scripts\python.exe -m milodex.cli.main report   # confirm the stale verdict
```

### Fix

Force a full-range refetch+merge (the `--force` flag, added 2026-06-15, bypasses the 60-day gap-scan
floor and is additive/idempotent):

```powershell
.venv\Scripts\python.exe -m milodex.cli.main data fetch-universe `
  --universe-ref <ref> --start <before-gap> --end <today> --force
```

For intraday symbols, check per-session completeness with
`milodex data readiness --universe-ref <ref> --start <d> --end <d>` and warm the cache before launch.

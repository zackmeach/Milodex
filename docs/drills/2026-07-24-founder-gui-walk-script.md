# Founder GUI Walk Script — 2026-07-24 (M4 gate walks; refreshed 2026-09-28)

Supersedes [`2026-07-11-founder-gui-walk-script.md`](2026-07-11-founder-gui-walk-script.md)
— refreshed for the post-cleanup GUI: DesignSystemShowcase and its themed tabs
were **deleted** (#370, audit wave 2), so the old §3 showcase walk is gone;
Bench rows are now **template-group rollups** (#377); FRONT liveness copy,
LEDGER dates, and the runner-panel default were honesty-fixed (#376); the M4
observables (#352/#353/#354) now render on DESK. The DESK fleet table is
**incoming and not scripted here** — walk it when it lands, not before.

**2026-09-28 correction:** the current paper account has held positions. A blank
scratch ledger fails runner-start reconciliation. Use a consistent SQLite backup
of the local ledger for §2–§3 and reconcile it against the paper broker before
starting. The empty-state walk in §1 now uses its own scratch directory.

**Expected duration:** 20–30 minutes.
**Covers:** first-run empty state (§1), negative broker credentials (§2),
kill-switch trip→reset render HR-4 + controlled-stop-while-active HR-5 (§3),
and an M4-observables spot-check against the live-fire week's real rows (§4).

**Results feed:** the outcome record at the bottom of this file, then the M4
gate in [`docs/CURRENT_ROADMAP.md`](../CURRENT_ROADMAP.md) per its gate-only
protocol and the sign-off block in
[`2026-07-24-m4-closure-retrospective.md`](../architecture/roadmaps/2026-07-24-m4-closure-retrospective.md).
(`docs/LAUNCH_READINESS.md` is a frozen 2026-05-14 snapshot — its §1.1/§1.7/
§1.9/§5.x items are the historical ancestors of these walks; cite it for
lineage, do not edit it.)

**Prerequisites:**

- Run these commands from the original source checkout, not a managed worktree:
  `Set-Location C:\Users\zdm80\Milodex`. This checkout has the local `.venv`,
  `.env`, and source `data/milodex.db`; sync it to the reviewed M4 code first.
- GUI closed; no runners live:
  `.venv\Scripts\python.exe -m milodex.cli.main strategy status` — every row
  `stopped`/`never_ran`/`phantom`, none `running`. (Wedged? See
  `docs/TROUBLESHOOTING.md` before starting.)
- Run §3 only while `milodex status` says **Trading mode: paper** and
  **Market open: yes**. Stop and verify the runner is gone before the close.
  Daily strategies can evaluate after the close; this walk is not the separate
  after-close F2 rehearsal.
- Real Alpaca paper credentials in `.env` (§2 shadows them per-terminal
  without touching the file).

---

## Safety model — read before starting

Local writes in §1–§3 use **scratch state**, selected by the four env vars
`src/milodex/config.py` honors: `MILODEX_DATA_DIR`, `MILODEX_LOG_DIR`,
`MILODEX_LOCKS_DIR`, `MILODEX_CACHE_DIR`. Strategy configs stay the real
read-only bundled YAMLs. §1 starts with an empty scratch store; §2–§3 use a
consistent backup of the real event store. **The paper broker is shared**:
scratch state does not isolate a runner's possible broker orders. §4 has no
operator-initiated mutation, but opening the real GUI can reconcile orphaned
run rows and record its startup risk-profile default in the real ledger.

Scratch setup (once, in the terminal used for §2–§3):

```powershell
$scratchRoot = "C:\Users\zdm80\milodex-gui-walk-scratch"
if (Test-Path -LiteralPath $scratchRoot) { throw "The fixed scratch path already exists; preserve or remove it before this walk." }
New-Item -ItemType Directory -Force -Path "$scratchRoot\data","$scratchRoot\logs","$scratchRoot\data\locks","$scratchRoot\cache" | Out-Null
$backupScript = @'
import sqlite3
from pathlib import Path
source_path = Path("data/milodex.db").resolve()
if not source_path.is_file():
    raise SystemExit(f"Missing source ledger: {source_path}")
source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
target = sqlite3.connect(r"C:\Users\zdm80\milodex-gui-walk-scratch\data\milodex.db")
source.backup(target)
queued = target.execute(
    "SELECT count(*) FROM queued_intents WHERE strategy_id = ? AND status = 'queued'",
    ("regime.daily.sma200_rotation.spy_shy.v1",),
).fetchone()[0]
target.close()
source.close()
if queued:
    raise SystemExit(f"SPY/SHY has {queued} queued intents; do not run this walk")
'@
$backupScript | .venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { throw "Scratch ledger backup failed." }
$env:TRADING_MODE = "paper"
$env:MILODEX_DATA_DIR  = "$scratchRoot\data"
$env:MILODEX_LOG_DIR   = "$scratchRoot\logs"
$env:MILODEX_LOCKS_DIR = "$scratchRoot\data\locks"
$env:MILODEX_CACHE_DIR = "$scratchRoot\cache"
.venv\Scripts\python.exe -m milodex.cli.main reconcile
.venv\Scripts\python.exe -m milodex.cli.main orders --status open
```

Require a `CLEAN` reconciliation and **No matching orders** before §3. A drift
verdict or any broker open order stops the walk;
do not place corrective broker orders to make it pass. These env vars persist
for the PowerShell session. **Close the GUI and clear them (or close the
terminal) before §4 / anything against real state.**

---

## §1 — First-run empty state (~5 min; empty scratch dir; F1 GUI half)

Fresh terminal, **no** scratch vars set. Precheck that nothing is running,
then launch with a new empty scratch store:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status
$firstRunRoot = "C:\Users\zdm80\milodex-gui-first-run-scratch"
if (Test-Path -LiteralPath $firstRunRoot) { throw "The fixed first-run scratch path already exists; preserve or remove it before this walk." }
New-Item -ItemType Directory -Force -Path "$firstRunRoot\data","$firstRunRoot\logs","$firstRunRoot\data\locks","$firstRunRoot\cache" | Out-Null
$env:TRADING_MODE = "paper"
$env:MILODEX_DATA_DIR = "$firstRunRoot\data"
$env:MILODEX_LOG_DIR = "$firstRunRoot\logs"
$env:MILODEX_LOCKS_DIR = "$firstRunRoot\data\locks"
$env:MILODEX_CACHE_DIR = "$firstRunRoot\cache"
.venv\Scripts\python.exe -m milodex.cli.main gui
```

- [ ] GUI opens, no traceback (`EventStore` auto-creates + migrates the scratch `data\`).
- [ ] Bench/Desk render empty-state copy — not a spinner, not an error.
- [ ] FRONT liveness line reads honestly against the empty store (#376 —
      prose "None of your … running right now.", not a phantom count).

Close the GUI and clear the scratch selection before the next section:

```powershell
Remove-Item Env:\MILODEX_DATA_DIR, Env:\MILODEX_LOG_DIR, Env:\MILODEX_LOCKS_DIR, Env:\MILODEX_CACHE_DIR, Env:\TRADING_MODE
.venv\Scripts\python.exe -m milodex.cli.main strategy status   # still matches pre-walk state
```

Lineage: LAUNCH_READINESS §1.1/§5.1; retrospective gate item **F1 (GUI half)**.

---

## §2 — Missing / invalid broker credentials (~5 min; scratch state)

New terminal → run the scratch block above → shadow credentials per-terminal
(`load_dotenv` never overrides already-set vars, so `.env` is untouched):

**Missing-key:**

```powershell
$env:ALPACA_API_KEY = ""; $env:ALPACA_SECRET_KEY = ""
.venv\Scripts\python.exe -m milodex.cli.main gui
```

- [ ] GUI opens; broker surfaces show a not-configured/empty state, no crash.
- [ ] `.venv\Scripts\python.exe -m milodex.cli.main status` fails closed with
      the `ALPACA_API_KEY is not set` / "Copy .env.example" message.

**Invalid-key** (close GUI first):

```powershell
$env:ALPACA_API_KEY = "invalid"; $env:ALPACA_SECRET_KEY = "invalid"
.venv\Scripts\python.exe -m milodex.cli.main gui
```

- [ ] GUI degrades gracefully — no traceback, no false "connected" state.
- [ ] `milodex status` fails closed with the `BrokerAuthError`-classified
      message naming `ALPACA_API_KEY`.

Close the GUI; restore real creds for §3:

```powershell
Remove-Item Env:\ALPACA_API_KEY, Env:\ALPACA_SECRET_KEY
```

Lineage: LAUNCH_READINESS §1.7/§5.4; drill-matrix `broker_outage`/`clean_room`
cells are the CLI-side proof — this walk adds the GUI render.

---

## §3 — Kill-switch trip → reset (HR-4) + stop-while-active (HR-5) (~10–12 min; scratch state)

Same terminal, scratch vars set, real credentials restored.
Do not begin if the paper broker has any open order, SPY/SHY has a queued
intent, or the market is closed. The scratch ledger's clean result does not
by itself prove there are no resting broker orders.

### §3a — Seed + launch the regime runner

```powershell
.venv\Scripts\python.exe -m milodex.cli.main status
.venv\Scripts\python.exe -m milodex.cli.main reconcile
.venv\Scripts\python.exe -m milodex.cli.main orders --status open
```

- [ ] Status says `Trading mode: paper` and `Market open: yes` immediately
      before launch. Reconciliation says `CLEAN`; orders says `No matching
      orders.` If any check fails, stop before freeze or GUI start. Start only
      with enough market-open time to finish the stop and verify it before close.

Only after those three checks pass:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main promotion freeze regime.daily.sma200_rotation.spy_shy.v1 --frozen-by "gui-walk-2026-09-28"
if ($LASTEXITCODE -ne 0) { throw "Scratch manifest freeze failed." }
.venv\Scripts\python.exe -m milodex.cli.main gui
```

- [ ] Freeze exits 0 (manifest lands in the **scratch** store only).

In Bench: rows are now **template groups** (#377). A single-variant group
renders under the strategy's own display name (SMA200 Rotation); click the
row to **expand** its instance roster — actions live on the roster instance
rows, not the group row.

In a **second terminal**, select and verify the same scratch paths before
running any command, especially `halt`. Do this before confirming **Start
Trading** in the GUI:

```powershell
Set-Location C:\Users\zdm80\Milodex
$scratchRoot = "C:\Users\zdm80\milodex-gui-walk-scratch"
$env:TRADING_MODE = "paper"
$env:MILODEX_DATA_DIR = "$scratchRoot\data"
$env:MILODEX_LOG_DIR = "$scratchRoot\logs"
$env:MILODEX_LOCKS_DIR = "$scratchRoot\data\locks"
$env:MILODEX_CACHE_DIR = "$scratchRoot\cache"
$routeCheck = @'
from pathlib import Path
from milodex.config import get_cache_dir, get_data_dir, get_locks_dir, get_trading_mode
root = Path(r"C:\Users\zdm80\milodex-gui-walk-scratch").resolve()
assert (root / "data" / "milodex.db").is_file()
assert get_data_dir().resolve() == root / "data"
assert get_locks_dir().resolve() == root / "data" / "locks"
assert get_cache_dir().resolve() == root / "cache"
assert get_trading_mode() == "paper"
'@
$routeCheck | .venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { throw "Scratch routing or paper mode differs; do not run halt." }
.venv\Scripts\python.exe -m milodex.cli.main status
.venv\Scripts\python.exe -m milodex.cli.main orders --status open
```

- [ ] The second terminal confirms scratch routing, `Trading mode: paper`,
      `Market open: yes`, and `No matching orders.` If the market is near its
      scheduled close, defer §3. Recheck market status immediately before the
      GUI confirmation; do not start after the close.
- [ ] Expand the regime group → instance row shows paper stage → **Start
      Trading**. Confirm only while the market remains open.

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status regime.daily.sma200_rotation.spy_shy.v1
```

- [ ] Status says `state: running` with a live `holder_pid` for the new
      session. Do not proceed if it names the wrong session.

### §3b — Trip (HR-4)

**Credential fence first** — `halt` runs a best-effort `cancel_all_orders`
with the `.env` credentials (NOT scoped by `MILODEX_*`). Shadow them with
bogus values in the second terminal so the cancel fails soft (drill-proven:
the trip still lands durably — see the `kill_switch_trip_reset` cell in
[`2026-07-11-m4-drill-matrix.md`](2026-07-11-m4-drill-matrix.md)):

```powershell
if ([string]::IsNullOrWhiteSpace($routeCheck)) { throw "Run the second-terminal scratch routing check first." }
$routeCheck | .venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { throw "Scratch routing or paper mode differs; do not run halt." }
$env:ALPACA_API_KEY = "PKWALKBOGUSKEY000000"
$env:ALPACA_SECRET_KEY = "walkBogusSecret00000000000000000000000AAA"
.venv\Scripts\python.exe -m milodex.cli.main halt --confirm --reason "HR-4/HR-5 founder GUI walk"
```

- [ ] Reports the cancel step FAILED (expected), **and** `Kill switch: active`,
      **and** a controlled-stop request issued to the regime runner.

### §3c — Tripped-state render (HR-4)

In the still-open GUI (OperationalState polls ~1s — no relaunch):

- [ ] Header strip flips from "GUARD READY" to an unmistakable tripped
      indicator (color + label + the walk reason).
- [ ] Reset flow reachable from **both** paths — RiskStrip badge, and Risk
      Office drawer → KILL SWITCH section — each opens `KillSwitchResetModal`.
- [ ] Modal demands an explicit confirm; no auto-reset affordance.
- [ ] Screenshot tripped state + open modal. **Do not confirm yet.**

### §3d — Controlled stop while active (HR-5)

The §3b halt already fanned a stop request out; the runner consumes it on its
next poll (~60s window). Re-check state:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status regime.daily.sma200_rotation.spy_shy.v1
```

- **Still `running`:** in Bench, click **Stop Trading** on the instance row
  while the tripped banner shows.
  - [ ] Not blocked/greyed by the active kill switch; readiness panel lists
        `kill_switch` as informational, not blocking (HR-5 semantics,
        `commands/bench.py` stop family).
  - [ ] Approve; stop request accepted (duplicate request is harmless).
- **Already stopped** (the fan-out won the race — valid): confirm
  structurally — any `running` strategy shows an enabled Stop Trading while
  tripped; if none is running, record N/A (code-grounded + drill-proven).

Poll `strategy status` until it shows the session `ended`, no live holder PID,
and either `state: stopped` with exit `controlled_stop` or `state: failed`
with exit `kill_switch` (the runner-status classification for a trip).
A stop request alone is insufficient. If the runner is still live, wedged,
or its session did not close, **leave the kill switch tripped and keep the
scratch state**; do not reset or clean up. Once stopped, restore the real
paper credentials in this second terminal and check the broker again:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status regime.daily.sma200_rotation.spy_shy.v1
Remove-Item Env:\ALPACA_API_KEY, Env:\ALPACA_SECRET_KEY
.venv\Scripts\python.exe -m milodex.cli.main orders --status open
.venv\Scripts\python.exe -m milodex.cli.main reconcile
```

- [ ] No broker open orders; reconciliation `CLEAN`. Otherwise do not reset.

### §3e — Reset (completes HR-4)

Confirm the reset **from the GUI modal**, then cross-check:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main trade kill-switch status   # Active: no
.venv\Scripts\python.exe -m milodex.cli.main strategy status regime.daily.sma200_rotation.spy_shy.v1
.venv\Scripts\python.exe -m milodex.cli.main orders --status open
```

- [ ] Banner reverts to "GUARD READY" without a relaunch; the runner remains
      stopped and there are no broker open orders. Keep scratch state and
      investigate if either post-reset check fails.

Close the GUI. Lineage: LAUNCH_READINESS §1.9/§5.3; closes the HR-4/HR-5
"supervised window" items from the 2026-06-10 hardening roadmap.

---

## §4 — M4 observables spot-check (~5 min; REAL state)

Fresh terminal, **no scratch vars, no bogus creds**. Launch the GUI against
real state and read — click nothing that mutates. GUI bootstrap may write
orphan-run recovery and startup-default records even without a click; capture
the pre-launch `strategy status` output so those effects are distinguishable.

- [ ] **DESK → STRATEGY ATTENTION**: the live-fire week's operator alerts
      render (#352/#353) — `stale_market_data_idle` warnings from the pre-open
      launches and the `exit_intent_dropped` rows from 07-14/07-16/07-20
      (`no_fresh_price` / `no_clean_handoff`), each with strategy + symbol.
- [ ] **FRONT liveness copy** (#376): with the fleet down it reads "None of
      your N strategies are running right now." — prose, no false count.
- [ ] **LEDGER dates** (#376): rows carry full dates ("2026-07-19 …"), not
      bare clock times.
- [ ] **Bench group rollup** (#377): sections show template-group rows;
      expanding a multi-variant group (e.g. a benchmark family) lists its
      variants; single-variant groups read as before. Stage placement =
      highest promoted instance.
- [ ] Anything that reads wrong → note it in the outcome record; do not fix
      live.

Close the GUI.

---

## Cleanup

Run this only after the GUI is closed, §3e confirms no live runner or open
broker order, and the walk results have been recorded. If stop or reconciliation
was unconfirmed, keep the scratch directories and the kill switch state for
investigation.

```powershell
Remove-Item Env:\MILODEX_DATA_DIR, Env:\MILODEX_LOG_DIR, Env:\MILODEX_LOCKS_DIR, Env:\MILODEX_CACHE_DIR, Env:\TRADING_MODE -ErrorAction SilentlyContinue
Remove-Item Env:\ALPACA_API_KEY, Env:\ALPACA_SECRET_KEY -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "C:\Users\zdm80\milodex-gui-first-run-scratch" -Recurse -Force
Remove-Item -LiteralPath "C:\Users\zdm80\milodex-gui-walk-scratch" -Recurse -Force
.venv\Scripts\python.exe -m milodex.cli.main strategy status   # matches pre-walk state
```

---

## Outcome record

```
Walked by:   ____________   Date: ____________   Commit: ____________
§1 first-run:        PASS / FAIL  — notes: ____________
§2 credentials:      PASS / FAIL  — notes: ____________
§3 HR-4 trip+reset:  PASS / FAIL  — notes: ____________
§3 HR-5 stop-while-active: PASS / FAIL / N/A(structural)  — notes: ____________
§4 observables:      PASS / FAIL  — notes: ____________
Screenshots saved to: ____________
```

Copy the verdicts into the retrospective sign-off block and update the M4
gate in `docs/CURRENT_ROADMAP.md` per its gate-only protocol.

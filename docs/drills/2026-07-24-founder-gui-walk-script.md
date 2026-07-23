# Founder GUI Walk Script — 2026-07-24 (M4 gate walks)

Supersedes [`2026-07-11-founder-gui-walk-script.md`](2026-07-11-founder-gui-walk-script.md)
— refreshed for the post-cleanup GUI: DesignSystemShowcase and its themed tabs
were **deleted** (#370, audit wave 2), so the old §3 showcase walk is gone;
Bench rows are now **template-group rollups** (#377); FRONT liveness copy,
LEDGER dates, and the runner-panel default were honesty-fixed (#376); the M4
observables (#352/#353/#354) now render on DESK. The DESK fleet table is
**incoming and not scripted here** — walk it when it lands, not before.

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

- GUI closed; no runners live:
  `.venv\Scripts\python.exe -m milodex.cli.main strategy status` — every row
  `stopped`/`never_ran`/`phantom`, none `running`. (Wedged? See
  `docs/TROUBLESHOOTING.md` before starting.)
- Market closed is fine — §3 deliberately uses the daily regime strategy,
  which idles safely off-hours.
- Real Alpaca paper credentials in `.env` (§2 shadows them per-terminal
  without touching the file).

---

## Safety model — read before starting

Every mutating step (§2, §3) runs against **scratch state**, selected by the
three env vars `src/milodex/config.py` honors: `MILODEX_DATA_DIR`,
`MILODEX_LOG_DIR`, `MILODEX_LOCKS_DIR`. Strategy configs stay the real
read-only bundled YAMLs; only runtime state (kill switch, manifests, runner
sessions) lands in the throwaway store. §1 touches the real `data\` dir with
its own backup/restore; §4 is **read-only** against real state.

Scratch setup (once, in the terminal used for §2–§3):

```powershell
$scratchRoot = "C:\Users\zdm80\milodex-gui-walk-scratch"
New-Item -ItemType Directory -Force -Path "$scratchRoot\data","$scratchRoot\logs","$scratchRoot\data\locks" | Out-Null
$env:MILODEX_DATA_DIR  = "$scratchRoot\data"
$env:MILODEX_LOG_DIR   = "$scratchRoot\logs"
$env:MILODEX_LOCKS_DIR = "$scratchRoot\data\locks"
```

These persist for the PowerShell session. **Close the GUI and clear them
(or close the terminal) before §4 / anything against real state.**

---

## §1 — First-run empty state (~5 min; real data dir; F1 GUI half)

Fresh terminal, **no** scratch vars set.

Precheck (nothing running), then back up and launch:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status
Rename-Item data "data.bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
.venv\Scripts\python.exe -m milodex.cli.main gui
```

- [ ] GUI opens, no traceback (`EventStore` auto-creates + migrates `data\`).
- [ ] Bench/Desk render empty-state copy — not a spinner, not an error.
- [ ] FRONT liveness line reads honestly against the empty store (#376 —
      prose "None of your … running right now.", not a phantom count).

Close the GUI. Restore:

```powershell
Remove-Item data -Recurse -Force
Rename-Item (Get-ChildItem -Directory -Filter 'data.bak.*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName data
.venv\Scripts\python.exe -m milodex.cli.main strategy status   # matches pre-walk state
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

### §3a — Seed + launch the regime runner

```powershell
.venv\Scripts\python.exe -m milodex.cli.main promotion freeze regime.daily.sma200_rotation.spy_shy.v1 --frozen-by "gui-walk-2026-07-24"
.venv\Scripts\python.exe -m milodex.cli.main gui
```

- [ ] Freeze exits 0 (manifest lands in the **scratch** store only).

In Bench: rows are now **template groups** (#377). A single-variant group
renders under the strategy's own display name (SMA200 Rotation); click the
row to **expand** its instance roster — actions live on the roster instance
rows, not the group row.

- [ ] Expand the regime group → instance row shows paper stage → **Start
      Trading**.
- [ ] Second terminal (scratch vars set there too):
      `... strategy status regime.daily.sma200_rotation.spy_shy.v1` →
      `state: running`, live `holder_pid`. (Off-hours idling is by design.)

### §3b — Trip (HR-4)

**Credential fence first** — `halt` runs a best-effort `cancel_all_orders`
with the `.env` credentials (NOT scoped by `MILODEX_*`). Shadow them with
bogus values in the second terminal so the cancel fails soft (drill-proven:
the trip still lands durably — see the `kill_switch_trip_reset` cell in
[`2026-07-11-m4-drill-matrix.md`](2026-07-11-m4-drill-matrix.md)):

```powershell
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

### §3e — Reset (completes HR-4)

Confirm the reset **from the GUI modal**, then cross-check:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main trade kill-switch status   # Active: no
```

- [ ] Banner reverts to "GUARD READY" without a relaunch.

Close the GUI. Lineage: LAUNCH_READINESS §1.9/§5.3; closes the HR-4/HR-5
"supervised window" items from the 2026-06-10 hardening roadmap.

---

## §4 — M4 observables spot-check (~5 min; REAL state, read-only)

Fresh terminal, **no scratch vars, no bogus creds**. Launch the GUI against
real state and read — click nothing that mutates.

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

```powershell
Remove-Item Env:\MILODEX_DATA_DIR, Env:\MILODEX_LOG_DIR, Env:\MILODEX_LOCKS_DIR -ErrorAction SilentlyContinue
Remove-Item Env:\ALPACA_API_KEY, Env:\ALPACA_SECRET_KEY -ErrorAction SilentlyContinue
Remove-Item "C:\Users\zdm80\milodex-gui-walk-scratch" -Recurse -Force -ErrorAction SilentlyContinue
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

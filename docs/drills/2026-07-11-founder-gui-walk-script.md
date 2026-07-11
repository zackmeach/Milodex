# Founder GUI Walk Script — 2026-07-11

A step-by-step operator walk for the founder's supervised GUI window, covering
the manual checks that automated screenshots/tests cannot exercise: first-run
empty state, the design-system showcase gating, missing/invalid broker
credentials, and the kill-switch tripped-state render + reset (HR-4) and
controlled-stop-while-kill-switch-active (HR-5) walks that
`docs/architecture/roadmaps/2026-06-10-hardening-roadmap.md` and
`docs/reviews/2026-07-09-D9-manual-halt-brief.md` left as "supervised window"
items.

**Expected duration:** ~45–60 minutes.

**Prerequisites:**
- Milodex GUI is closed.
- No strategy runners are live: `.venv\Scripts\python.exe -m milodex.cli.main strategy status` — confirm every row is `stopped`/`never_ran`/`phantom`, none `running`. If anything shows `running`, stop it first (see `docs/TROUBLESHOOTING.md` if it's wedged) before starting this walk.
- Market closed is fine — this walk does not depend on live evaluation; §3 uses the daily (`1D`) regime strategy specifically because it idles safely outside market hours.
- Real Alpaca paper credentials in `.env` (unchanged for §1–§3; §2 temporarily shadows them for the negative-credential smoke without touching the file).

**Results feed:** `docs/LAUNCH_READINESS.md` §7 "Outcome record" (tick §5.1/§5.3
plus the HR-4/HR-5 manual items noted there) and the M4 gate in
`docs/CURRENT_ROADMAP.md`. Record PASS/FAIL + notes per section below, then
copy the summary into the LAUNCH_READINESS §7 block.

---

## Safety model — read this before starting

Every step that trips a kill switch or mutates runtime state runs against
**scratch state**, not the real `data\milodex.db`. Scratch state is selected
by three environment variables Milodex already honors
(`src/milodex/config.py`): `MILODEX_DATA_DIR`, `MILODEX_LOG_DIR`,
`MILODEX_LOCKS_DIR`. Strategy **configs** (`configs/*.yaml`) are read-only
bundled resources and are NOT redirected by these overrides — the scratch
walk still reads the real `configs/spy_shy_200dma_v1.yaml`, it just records
promotion/runtime state (kill switch, manifests, runner sessions) into a
throwaway event store instead of the real one.

The **only** step in this script that touches the real `data\milodex.db` is
§1 (first-run empty state) — it has its own explicit backup/restore
commands and a precheck. Every other section sets the scratch env vars first.

Set up the scratch root once at the start of the session:

```powershell
$scratchRoot = "C:\Users\zdm80\milodex-gui-walk-scratch"
New-Item -ItemType Directory -Force -Path "$scratchRoot\data","$scratchRoot\logs","$scratchRoot\data\locks" | Out-Null
$env:MILODEX_DATA_DIR  = "$scratchRoot\data"
$env:MILODEX_LOG_DIR   = "$scratchRoot\logs"
$env:MILODEX_LOCKS_DIR = "$scratchRoot\data\locks"
```

These three `$env:` vars persist for the rest of the PowerShell session —
every `milodex` invocation and every `gui` launch in §2–§4 below inherits
them. **Close the GUI and clear these three variables (`Remove-Item Env:\MILODEX_DATA_DIR`, etc., or just close the terminal) before doing anything against real state again.**

---

## §1 — First-run experience (real data dir; LAUNCH_READINESS §1.1 / §5.1)

This is the one step that touches the real `data\` directory. Run it in a
**fresh terminal with none of the scratch `$env:MILODEX_*` vars set.**

**Precheck — confirm nothing is running against real state:**

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status
```

Confirm no row shows `running`. If one does, stop before proceeding.

**Backup:**

```powershell
Rename-Item data "data.bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
```

**Launch:**

```powershell
.venv\Scripts\python.exe -m milodex.cli.main gui
```

`EventStore.__init__` creates `data\` fresh and auto-migrates an empty schema
on first read/write, so the GUI should open cleanly.

- [ ] GUI opens, no traceback.
- [ ] Strategy Bank renders empty-state copy (not a spinner, not an error).
- [ ] Front/Desk/Ledger surfaces render without crash against the empty store.

Close the GUI.

**Restore:**

```powershell
Remove-Item data -Recurse -Force
Rename-Item (Get-ChildItem -Directory -Filter 'data.bak.*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName data
```

Confirm real state is back: `.venv\Scripts\python.exe -m milodex.cli.main strategy status` should show the same strategies/history as before this section.

Maps to LAUNCH_READINESS §1.1, §5.1.

---

## §2 — Missing / invalid broker credentials (scratch state; LAUNCH_READINESS §1.7)

Open a **new terminal**, re-run the scratch-root setup block above, then shadow
the credentials for this terminal session only — `python-dotenv`'s
`load_dotenv()` does not override variables already present in the process
environment, so setting `$env:ALPACA_API_KEY` here masks `.env`'s real value
**without editing the file**. This is a deliberate improvement over
LAUNCH_READINESS §5.4's "move `.env` aside" — same coverage, zero risk of
forgetting to restore a renamed `.env`.

**Missing-key smoke:**

```powershell
$env:ALPACA_API_KEY = ""
$env:ALPACA_SECRET_KEY = ""
.venv\Scripts\python.exe -m milodex.cli.main gui
```

- [ ] GUI opens, broker surfaces show a "broker not configured" / empty state, no crash.
- [ ] `.venv\Scripts\python.exe -m milodex.cli.main status` (same terminal) fails closed with the `ALPACA_API_KEY is not set` / "Copy .env.example" message — does not silently proceed as paper or live.

Close the GUI.

**Invalid-key smoke:**

```powershell
$env:ALPACA_API_KEY = "invalid"
$env:ALPACA_SECRET_KEY = "invalid"
.venv\Scripts\python.exe -m milodex.cli.main gui
```

- [ ] GUI opens without crash; broker-dependent surfaces degrade gracefully (no traceback, no stale-looking "connected" state).
- [ ] `.venv\Scripts\python.exe -m milodex.cli.main status` fails closed with a `BrokerAuthError`-classified message naming `ALPACA_API_KEY` (see `docs/TROUBLESHOOTING.md` "APIError: unauthorized" section — the read-path translation from PR #284 is what makes this message actionable instead of a raw `APIError`).

Close the GUI, then clear the shadowed credentials so the rest of the walk uses real ones:

```powershell
Remove-Item Env:\ALPACA_API_KEY
Remove-Item Env:\ALPACA_SECRET_KEY
```

Maps to LAUNCH_READINESS §1.7, §5.4.

---

## §3 — Design-system showcase disabled tabs (scratch state; LAUNCH_READINESS §1.5 / §5.2)

Same terminal, scratch env vars still set, real credentials restored.

```powershell
.venv\Scripts\python.exe -m milodex.cli.main gui
```

1. Navigate to DesignSystemShowcase.
2. Confirm Editorial Dark is the active tab.
3. Confirm Editorial Light and Bronze tabs render visibly disabled with a `(post-launch)` suffix.
4. Click both disabled tabs.
   - [ ] No theme switch occurs.
   - [ ] No QML console error.
5. Screenshot the disabled-tabs state for the launch-evidence folder.

Close the GUI.

Maps to LAUNCH_READINESS §1.5, §5.2.

---

## §4 — Kill-switch: trip → reset render (HR-4) + controlled-stop-while-active (HR-5)

Same terminal, scratch env vars still set, real credentials.

### §4a — Seed one paper-stage strategy into scratch state

The regime strategy (`regime.daily.sma200_rotation.spy_shy.v1`, config
`configs/spy_shy_200dma_v1.yaml`) already declares `stage: "paper"` in its
real, read-only config. Freezing it writes a manifest into the **scratch**
event store (not the real one, since `MILODEX_DATA_DIR` is redirected) so
Bench shows it as launchable at paper stage for this walk only:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main promotion freeze regime.daily.sma200_rotation.spy_shy.v1 --frozen-by "gui-walk-2026-07-11"
```

- [ ] Command exits 0, reports a frozen manifest at stage `paper`.

### §4b — Launch the runner, confirm live

```powershell
.venv\Scripts\python.exe -m milodex.cli.main gui
```

In Bench, find `regime.daily.sma200_rotation.spy_shy.v1` at paper stage and
click **Start Trading**. Confirm via a second terminal (scratch env vars set
there too):

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status regime.daily.sma200_rotation.spy_shy.v1
```

- [ ] `state: running`, a live `holder_pid`.

Market being closed is fine here — the daily runner idles by design outside
market hours (`_IDLE_BY_DESIGN_NOTE`); it only needs to hold its lock to be a
valid target for the rest of this section.

### §4c — Trip the kill switch (HR-4)

There is no bare "trip only" CLI path — the only manual-trip affordance is
`milodex halt`, which trips the kill switch AND issues a controlled-stop
request to every live runner in the same call (ADR 0005 Addendum / D-9,
option A2).

**Credential fence — do this first.** `halt` also runs a best-effort
`cancel_all_orders` against the broker, and broker credentials come from
`.env` — they are NOT scoped by the `MILODEX_*` overrides. With real
credentials this step would cancel any resting orders on the **real paper
account**. Shadow the credentials with bogus values in this terminal so the
cancel step fails soft (the M4 drill matrix proved `halt` still trips
durably and reports honestly when the cancel fails — `docs/drills/2026-07-11-m4-drill-matrix.md`,
`kill_switch_trip_reset` cell):

```powershell
$env:ALPACA_API_KEY = "PKWALKBOGUSKEY000000"
$env:ALPACA_SECRET_KEY = "walkBogusSecret00000000000000000000000AAA"
```

Then, in that same second terminal (scratch env vars + bogus creds set):

```powershell
.venv\Scripts\python.exe -m milodex.cli.main halt --confirm --reason "HR-4/HR-5 founder GUI walk"
```

- [ ] Command reports the cancel step FAILED (expected — bogus creds; the
      trip must land anyway).
- [ ] Command reports `Kill switch: active`.
- [ ] Command reports a controlled-stop request issued to the regime runner.

### §4d — Confirm the GUI renders the tripped state (HR-4)

Back in the GUI (still open from §4b — OperationalState polls the kill-switch
store on a ~1s cadence, so it should update live without a relaunch):

- [ ] Front / Risk Office header strip switches from "GUARD READY" to an
      unmistakable tripped indicator (color + label + reason "HR-4/HR-5
      founder GUI walk").
- [ ] Open the reset flow from **both** reachable paths and confirm each opens
      the modal:
      - RiskStrip badge (kill-switch indicator).
      - Risk Office drawer → KILL SWITCH section.
- [ ] `KillSwitchResetModal` requires an explicit confirm action — no
      accidental auto-reset affordance.
- [ ] Screenshot the tripped-state render and the open reset modal for the
      launch-evidence folder.

**Do not confirm the reset yet** — §4e needs the kill switch still active.

### §4e — Controlled stop while the kill switch is active (HR-5)

HR-5 (`commands/bench.py` — the stop-family readiness check treats
`kill_switch` as `inspected`, not `required`, so a controlled stop is never
blocked by an active kill switch: "a controlled stop submits no trades; the
risk layer independently blocks anything a still-running runner attempts").

The `halt` in §4c already fired a controlled-stop request at the regime
runner as part of the same call, but the runner only consumes it at its next
poll cycle — so there is a short window (up to the runner's poll interval,
~60s for a daily/regime strategy) where Bench still shows it as `running`
with a stop pending. Use that window, or re-check status first:

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status regime.daily.sma200_rotation.spy_shy.v1
```

**If it still shows `running`:** in Bench, click **Stop Trading** on that row
while the kill-switch-tripped banner is still visible.
- [ ] The action is NOT blocked/greyed by the kill-switch-active state — no
      "kill switch active, action refused" error.
- [ ] The confirmation modal's readiness panel lists `kill_switch` as
      informational, not a blocking reason.
- [ ] Approve. Confirm the stop request is accepted (a second stop request is
      harmless — same target).

**If it already stopped** (the §4c fan-out beat you to it — also a valid
outcome): confirm structurally instead — with the kill switch still active,
open Bench, and confirm any strategy shown as `running` has an enabled
(non-blocked) **Stop Trading** action. If no strategy is currently running,
this sub-check is N/A; note that in the record and rely on the code
grounding above (already exercised by `tests/milodex/commands` — see the
HR-5 execution-log entry in the hardening roadmap).

### §4f — Reset (completes the HR-4 walk)

Back in the still-open reset modal from §4d (or reopen it):

```powershell
# equivalent CLI path, for cross-check only — do the actual reset from the GUI modal
.venv\Scripts\python.exe -m milodex.cli.main trade kill-switch status
```

- [ ] Confirm the reset from the GUI modal. Confirm the tripped banner clears
      and reverts to "GUARD READY" without a GUI relaunch.
- [ ] Cross-check via CLI:
      ```powershell
      .venv\Scripts\python.exe -m milodex.cli.main trade kill-switch status
      ```
      reports `active: false`.

Close the GUI.

Maps to LAUNCH_READINESS §1.9, §5.3; closes the HR-4 and HR-5 "supervised
window" items from `docs/architecture/roadmaps/2026-06-10-hardening-roadmap.md`.

---

## Cleanup

```powershell
Remove-Item Env:\MILODEX_DATA_DIR, Env:\MILODEX_LOG_DIR, Env:\MILODEX_LOCKS_DIR -ErrorAction SilentlyContinue
Remove-Item "C:\Users\zdm80\milodex-gui-walk-scratch" -Recurse -Force -ErrorAction SilentlyContinue
```

Confirm real state is untouched (no scratch env vars set, fresh terminal):

```powershell
.venv\Scripts\python.exe -m milodex.cli.main strategy status
```

Should match the pre-walk state from the §1 precheck (modulo whatever real
runners were already running before this walk started).

---

## Recording the outcome

Copy a summary into `docs/LAUNCH_READINESS.md` §7 "Outcome record": tick
§5.1/§5.2/§5.3/§5.4, note the HR-4/HR-5 verdicts and any screenshots taken,
and reference this file. Then update the M4 gate status in
`docs/CURRENT_ROADMAP.md` per its gate-only update protocol.

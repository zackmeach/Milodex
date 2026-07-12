# M4 Fault-Injection Drill Matrix

Generated: 2026-07-11 04:41 UTC  
Harness: `scripts/drills/run_drills.py` (standalone, subprocess-driven).  
Result: **8/8 cells PASS**.

Each cell injects a real fault into a throwaway scratch environment (tempfile-backed data / logs / locks / cache; never the real state) and asserts against the real operator surface — actual `python -m milodex.cli.main` output and/or actual event-store rows. Unit-test coverage is deliberately not the pass criterion.

## Verdicts

| Cell | Verdict | Notes |
| --- | --- | --- |
| `stale_market_data` | **PASS** | — |
| `locked_db` | **PASS** | slow ~30s |
| `corrupt_db` | **PASS** | — |
| `broker_outage` | **PASS** | outbound bogus-cred call |
| `dead_runner` | **PASS** | — |
| `wedged_stop` | **PASS** | — |
| `kill_switch_trip_reset` | **PASS** | outbound bogus-cred call |
| `clean_room` | **PASS** | see addendum: original PASS was from a `.env`-less checkout |

### Addendum 2026-07-11 — `clean_room` hermeticity caveat and re-verification

The original `clean_room` PASS above was produced in a checkout **without** a repo
`.env`. On a machine where the repo root has a real `.env` (the founder's), the
cell as originally shipped FAILed: the harness scrubbed `ALPACA_*` from the
subprocess environment, but `milodex.config`'s import-time `load_dotenv()` walks
up from the src tree and refilled the scrubbed keys from the repo `.env` — the
`creds="none"` posture then reached the live paper account and `milodex status`
exited 0 instead of failing closed. So the original PASS evidenced only the
credential-less-machine posture, not the founder-machine posture.

Fixed by suppressing the dotenv load in drill subprocesses
(`MILODEX_SKIP_DOTENV=1`, set unconditionally by `_build_subprocess_env`;
honored in `src/milodex/config.py` before `load_dotenv()`). Re-run on the
founder machine **with the real `.env` present** (2026-07-11, post-fix):

```
[drill] clean_room: PASS
=== 1/1 cells PASS ===
```

`creds="bogus"` / `"blank"` cells were never affected (`load_dotenv` does not
override variables already set), but all scratch subprocesses now skip dotenv
for defense in depth.

## `stale_market_data` — PASS

**Fault injected:** in-process daily StrategyRunner polled with clock +2d past the latest bar (stub provider returns a prior-session daily bar) against a scratch event store

**Assertions:**

```
  [PASS] exactly one durable stale_market_data_idle alert row
  [PASS] durable alert severity is warning
  [PASS] logged warning names the fetch-universe heal
  [PASS] `strategy status` exits 0
  [PASS] `strategy status` renders the alert
  [PASS] `strategy status` marks it [warning]
```

**Operator-facing output (verbatim, trimmed):**

```
Operator Alerts
  2026-07-13T21:00:00+00:00 [warning] stale_market_data_idle: Daily strategy regime.daily.sma200_rotation.spy_shy.v1 idling on stale market data: latest bar session 2026-07-11 < current session 2026-07-13.
-- logged warning --
Daily strategy regime.daily.sma200_rotation.spy_shy.v1 idling on STALE market data: latest bar is for session 2026-07-11 but the current session is 2026-07-13. No evaluation until the cache is refreshed. Heal with `milodex data fetch-universe --universe-ref <ref> --start <before-gap> --end <today> --force` (see docs/TROUBLESHOOTING.md, Data: stale).
```

**Durable record queried:**

```
list_operator_alerts(alert_type='stale_market_data_idle') -> 1 row(s)
summary: Daily strategy regime.daily.sma200_rotation.spy_shy.v1 idling on stale market data: latest bar session 2026-07-11 < current session 2026-07-13.
```

## `locked_db` — PASS

**Fault injected:** a second connection holds BEGIN IMMEDIATE on the scratch DB; the CLI write blocks for the 30s busy_timeout then errors

**Assertions:**

```
  [PASS] CLI exits nonzero (fail-closed)
  [PASS] message reports the store is locked
  [PASS] message points at TROUBLESHOOTING
```

**Operator-facing output (verbatim, trimmed):**

```
Error: Event store at C:\Users\zdm80\AppData\Local\Temp\milodex-drill-locked-nig6skml\data\milodex.db is locked â€” another runner, GUI, or CLI is writing to it. Stop that process (or wait), then retry. See docs/TROUBLESHOOTING.md.
```

**Durable record queried:**

```
exit code 1
```

## `corrupt_db` — PASS

**Fault injected:** garbage bytes written over the scratch milodex.db (WAL side files removed)

**Assertions:**

```
  [PASS] CLI exits nonzero (fail-closed)
  [PASS] message reports unreadable/corrupt store
  [PASS] message points at the .pre-compact-*.bak restore
  [PASS] message points at TROUBLESHOOTING
```

**Operator-facing output (verbatim, trimmed):**

```
Error: Event store at C:\Users\zdm80\AppData\Local\Temp\milodex-drill-corrupt-1kyfwjd7\data\milodex.db is unreadable or corrupt (file is not a database). Restore the most recent backup (C:\Users\zdm80\AppData\Local\Temp\milodex-drill-corrupt-1kyfwjd7\data\milodex.db.pre-compact-*.bak) or delete the file to recreate an empty store (trade/explanation history is lost). See docs/TROUBLESHOOTING.md.
```

**Durable record queried:**

```
exit code 1
```

## `broker_outage` — PASS

**Fault injected:** bogus Alpaca credentials (key PKDRILLB…) on `milodex status`

**Assertions:**

```
  [PASS] CLI exits nonzero (fail-closed)
  [PASS] did NOT proceed as if connected (no account/equity line)
  [PASS] broker-classified actionable error (auth names ALPACA_API_KEY, or connection)
  note: variant (b) unreachable-endpoint requires a broker-URL seam that does not exist without a code change; the bogus-cred path exercises the same fail-closed classification (auth online, connection offline).
```

**Operator-facing output (verbatim, trimmed):**

```
Error: Broker authentication failed during get_account: check ALPACA_API_KEY / ALPACA_SECRET_KEY in .env (and that TRADING_MODE matches the key type).
```

**Durable record queried:**

```
exit code 1; classification: BrokerAuthError (endpoint reached, 401 on bogus keys)
```

## `dead_runner` — PASS

**Fault injected:** open strategy_runs row + lock file naming dead PID 52232

**Assertions:**

```
  [PASS] `strategy status` reports phantom
  [PASS] phantom note names reap-orphans
  [PASS] reap-orphans exits 0
  [PASS] reap-orphans reports a closure
  [PASS] durable strategy_runs row closed with exit_reason=orphaned_no_live_runner
```

**Operator-facing output (verbatim, trimmed):**

```
drill.dead.daily.rotation.v1  state: phantom
  note: open session row with no live process â€” close it with `milodex maintenance reap-orphans` (the GUI bootstrap reaper also closes it)
-- reap-orphans --
Reaped 1 orphan run(s): drill.dead.daily.rotation.v1.
```

**Durable record queried:**

```
strategy_runs[drill.dead.daily.rotation.v1]: ended_at=2026-07-11 04:40:49.573368+00:00, exit_reason=orphaned_no_live_runner
```

## `wedged_stop` — PASS

**Fault injected:** live process holds the runner lock + a controlled-stop request backdated 300s (past 3x the 60s cadence); moot variant re-checks with a dead holder

**Assertions:**

```
  [PASS] wedged: `strategy status` renders UNCONSUMED (runner wedged)
  [PASS] wedged: remediation names hard-kill
  [PASS] wedged: remediation points at TROUBLESHOOTING
  [PASS] moot variant: classified moot (runner not live)
```

**Operator-facing output (verbatim, trimmed):**

```
drill.wedged.daily.rotation.v1  state: running
  session drill-wedged-session started 2026-07-11T04:40:50.015712+00:00
  stop requested: YES â€” UNCONSUMED (runner wedged)
  stop request: controlled-stop request UNCONSUMED for 5m â€” the runner lock is live but the process is not draining the request (wedged; a healthy runner consumes it within one poll). Controlled stop will not complete: hard-kill the PID and clear data/locks/*.lock (see docs/TROUBLESHOOTING.md).
-- moot variant --
  stop requested: yes (moot â€” runner not live)
  stop request: controlled-stop request present but no live runner holds the lock â€” the process is already gone, so the stop request is moot; clear the leftover data/locks/*.lock file if it lingers.
```

**Durable record queried:**

```
(filesystem-only: lock + controlled_stop.json under scratch locks dir)
```

## `kill_switch_trip_reset` — PASS

**Fault injected:** `milodex halt --confirm` with bogus creds (cancel fails) then a reset cycle; scratch event store only

**Assertions:**

```
  [PASS] halt exits 0 (fail-soft trip)
  [PASS] halt reports the cancel step failed on bogus creds
  [PASS] halt reports kill switch active
  [PASS] durable kill_switch_events 'activated' row
  [PASS] `kill-switch status` shows Active: yes
  [PASS] reset WITHOUT --confirm is refused (nonzero)
  [PASS] refusal names the --confirm requirement
  [PASS] reset --confirm exits 0 and clears it
  [PASS] durable kill_switch_events 'reset' row
```

**Operator-facing output (verbatim, trimmed):**

```
Operator Manual Halt
Orders: cancel_all_orders FAILED (APIError: {"message": "unauthorized."}
Kill switch: active
-- kill-switch status --
Active: yes
Reason: operator manual trip
-- reset without --confirm --
Error: trade kill-switch reset requires --confirm. Investigate the cause of the original activation before re-enabling trading.
-- reset --confirm --
Now active: no
```

**Durable record queried:**

```
after halt: latest kill_switch_event=activated (reason=operator manual trip)
after reset: latest kill_switch_event=reset
```

## `clean_room` — PASS

**Fault injected:** fresh scratch dir, no .env / no creds (part a) + empty data dir (part b)

**Assertions:**

```
  [PASS] `milodex status` exits nonzero (fail-closed)
  [PASS] names the missing ALPACA_API_KEY
  [PASS] names the copy-.env.example fix
  [PASS] fresh DB auto-migrated (schema_version > 0)
  [PASS] fresh DB created its tables
```

**Operator-facing output (verbatim, trimmed):**

```
Error: ALPACA_API_KEY is not set. Copy .env.example to .env and fill in your Alpaca credentials.
```

**Durable record queried:**

```
fresh event store: schema_version=17, 20 tables
```


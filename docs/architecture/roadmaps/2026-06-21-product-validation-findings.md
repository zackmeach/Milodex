# Product-Validation Findings — Usage-Burn Backlog Tier F

**Date:** 2026-06-21
**Scope:** Tier F of the usage-burn backlog (`2026-06-21-usage-burn-backlog.md`) — product-facing
validation that proves Milodex is a real, trustworthy, shareable system, not a lab experiment.
Covers **F1** (clean-room install + first-launch audit) and **F2** (end-to-end paper lifecycle
rehearsal). **F3** (failure/recovery drills) landed as concrete operator entries in
`docs/TROUBLESHOOTING.md` — see that file.

> These drills were run in **isolated scratch environments** (temp dirs / a fresh local clone with a
> stdlib venv). The real `data/`, `.env`, and event store were never touched; no live runner was
> launched; nothing was promoted to a capital stage.

---

## F1 — Clean-room install + first-launch audit

**Verdict: PASS-WITH-FRICTION.** A brand-new user can `git clone` → `python -m venv` →
`pip install -e ".[dev]"` → run the CLI and get clean, **fail-closed** first-launch behavior with
zero code edits. Missing-state behavior is genuinely well-built. The one real defect was in the GUI
screenshot tooling (now fixed — see PR for the `register_qml_types` repair).

**Method:** `git clone --branch master` into a temp dir; fresh stdlib `python -m venv` (Python 3.12.1
base); documented install path verbatim; first-launch with no `.env` and no `data/`.

### What works (trust-positive)
- **Install:** `pip install -e ".[dev]"` exits 0 in ~100s, no build errors, no version conflicts.
- **No `.env`:** `status` fails **CLOSED** with an actionable message + exit 1; `--json` returns a
  structured error. No silent live-default, no traceback. This is the correct product-trust behavior.
- **No `data/milodex.db`:** read commands auto-create + migrate the DB on first touch, show clean
  empty-state (`(none — no strategies have run yet)`), and degrade gracefully when the broker is
  unreachable. Exit 0.
- **GUI import path:** `import milodex.gui` offscreen is clean (fonts bundled; no `QFontDatabase`
  error); the QML load-smoke suite passes offscreen.

### Findings filed
| # | Severity | Finding | Disposition |
|---|----------|---------|-------------|
| 1 | **blocker** (GUI tooling) | `scripts/capture_gui_screenshots.py` + `capture_bench_interactive.py` crash on a clean checkout: they pass a removed `strategy_bank_state=` kwarg to `register_qml_types()` (and construct an orphaned `StrategyBankState`). Confirmed on master. | **FIXED** this batch (capture-scripts PR) |
| 2 | friction | A broker `401 unauthorized` (typo'd/placeholder key — the most common new-user mistake) is classified `unexpected_error` ("Unexpected error (APIError)") instead of a dedicated broker-auth error. | filed as follow-up task |
| 3 | friction | `pyproject.toml` pins `pandas>=2.0` / `pyarrow>=15.0` with **no upper bound** (numpy transitively unbounded) → fresh installs resolve pandas 3.x + numpy 2.x, newer majors than the creator runs. Drift risk that only surfaces on fresh installs/CI. | filed as follow-up task |
| 4 | cosmetic | Bare `milodex report` renders the raw nginx `<html>401</html>` blob in the "Broker:" line; should collapse to "UNREACHABLE (401)". | noted (low priority) |
| 5 | cosmetic | `py -3.12` launcher returned "No suitable Python runtime found" on this box; the direct Python312 path worked. README Quick Start's bare `report` example is undocumented-but-works. | noted (low priority) |

### Maps to LAUNCH_READINESS (was MANUAL-REQUIRED, now has evidence)
- **§1.1 first-run** — PASS (CLI runs from clean checkout; empty-state messaging clean).
- **§1.2 install** — PASS (editable install exit 0; console entry point installed). Minor: finding #3.
- **§1.7 broker/env missing-state** — PASS on fail-closed; needs-work on auth-error labeling (#2).
- **§5.1 no prior data** — PASS (DB auto-created + migrated; empty-state clean; broker degrades).

---

## F2 — End-to-end paper lifecycle rehearsal

**Verdict: the `backtest → evidence → promotion-proposal` spine produces reviewable, legible
artifacts on current code — no broken handoff vs the 2026-05-15 walk (`2fc6a42`).** The data-fetch
and real-backtest legs structurally require live Alpaca creds + cache and cannot be auto-walked
creds-free (this has always been true; §0 used the GUI Bench bridge in a live environment).

**Method:** walked the chain on a scratch DB. The CLI `backtest` cannot run creds-free
(`get_backtest_engine` unconditionally builds `AlpacaDataProvider`; no `--simulated` flag), so the
evidence **shape** was verified by driving the real CLI through the `backtest_engine_factory`
injection seam with a `SimulatedDataProvider` (the fixture path bypasses the cache, per CLAUDE.md).

### Per-leg results
- **Command surface — LEGIBLE.** All lifecycle verbs present, help clean. `promotion {freeze,
  manifest, promote, demote, history}`, `analytics {metrics, trades, compare, export, list}`,
  `report {daily, strategy}`, `research {screen, evidence, fan-out}`, `experiment {create, list,
  update, show}`. Top-level `promote` is the refused legacy stub by design.
- **Backtest → evidence — LEGIBLE.** Real CLI → engine → event store → `CommandResult` produced a
  full artifact: identity/period/equity/return/trades/risk-policy/data-quality, the **honest
  confidence label** (`insufficient evidence (trade count 2 < 30 statistical minimum)`), a persisted
  `backtest_runs` row (`status='completed'`, `metadata_json.initial_equity` top-level), and a
  walk-forward `--json` package with `oos_aggregate`, per-window `stability.single_window_dependency`,
  a complete `run_manifest` (code commit, config hash, provider class), and `uncertainty_label`.
- **Evidence assembler — LEGIBLE (shape).** `research/evidence_assembler.py` (the merged Workstream-E
  framework, ADR 0017) is a reporter, never a gate (`durable=False`, `iex_exploratory=True`); it
  writes one append-only experiment-registry row. Its terminal artifact (the registry) is reachable
  creds-free and verified via `experiment create/list`.
- **Promotion proposal — LEGIBLE; gating intact.** `_require_evidence_inputs` refuses without
  `--recommendation` + `--risk` (R-PRM-008); `--to live` requires `--confirm` + irreversibility
  warning; gate failures surface as a structured `BLOCKED` result naming each failure;
  `--lifecycle-exempt` is the regime path. No capital-stage promotion reachable without explicit
  operator flags.
- **Paper runner + explanation — LEGIBLE (read-only).** Operator surface is the `explanations` table
  keyed by `session_id`; controlled stop writes `exit_reason='controlled_stop'`. Market-hours gate
  confirmed (daily runner no-ops while market open; declines stale prior-session bars).

### Follow-ups (all low/trivial — documented, not separately filed)
1. **`analytics list` shows `TRADES = ?` for walk-forward runs** — the list renderer reads a top-level
   `trade_count` but WF runs store it under `metadata_json.$.oos_aggregate.trade_count`. Cosmetic;
   `analytics metrics` + the run JSON resolve it. One-line fallback in the list view would fix it.
2. **No creds-free smoke seam for `milodex backtest`** — the lifecycle's most consequential leg can't
   be auto-rehearsed without creds; the `backtest_engine_factory` seam is test-only. A hidden
   `--fixture`/`--self-test` would make the §0 walk unattended-rehearsable. Process gap, not a bug.
3. **`experiment create/show` first-attempt flag errors are verbose** — legible once known. Trivial.

### Operator rehearsal checklist (replaces the stale §0 walk)
Run from the **real repo root** with `.env` (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`,
`TRADING_MODE=paper`). Capital-stage actions (micro_live/live promotion, capital allocation,
kill-switch reset, real detached runner) are operator-only (Appendix Z) — never automate them.

```powershell
# 0. Preconditions
python -m milodex.cli.main status        # Trading mode: paper, broker connected
python -m milodex.cli.main reconcile     # clean run whose recorded_at resolves to TODAY (America/New_York)

# 1. Data fetch (creds) — warm/heal the cache for the strategy's universe
python -m milodex.cli.main data fetch-universe --universe-ref universe.spy_only.v1 --start 2018-01-01 --end <today>
#    Verify ~250 bars/yr on market_cache/v3/1Day/<SYM>.parquet (no interior gap)

# 2. Canonical walk-forward backtest for the lifecycle-proof regime strategy
python -m milodex.cli.main backtest regime.daily.sma200_rotation.spy_shy.v1 --start 2018-01-01 --end <today> --walk-forward --json
#    Verify JSON: status=success, oos_aggregate present, run_manifest.code.commit set; capture run_id
python -m milodex.cli.main analytics list ; python -m milodex.cli.main analytics metrics <run_id>

# 3. Promotion proposal → paper (lifecycle-exempt for the regime strategy ONLY)
python -m milodex.cli.main promotion freeze regime.daily.sma200_rotation.spy_shy.v1
python -m milodex.cli.main promotion promote regime.daily.sma200_rotation.spy_shy.v1 --to paper --run-id <run_id> --recommendation "rehearsal" --risk "regime strategy, low trade count" --lifecycle-exempt
#    NEVER --to micro_live/live, NEVER --confirm. Verify type=lifecycle_exempt, YAML stage -> paper.
python -m milodex.cli.main promotion history regime.daily.sma200_rotation.spy_shy.v1

# 4. Paper runner + explanation (operator-only; places paper orders) — LAUNCH AFTER MARKET OPEN
#    (a daily runner is a NO-OP while market is open; it evaluates after close + lockin). Use fleet-ops.
#    Inspect: SELECT * FROM explanations WHERE session_id='<sid>';  (data/milodex.db, after close)

# 5. Controlled stop -> verify strategy_runs.exit_reason='controlled_stop'
# 6. Walk back:  promotion demote ... --to backtest --reason "rehearsal complete"
```

---

## F3 — Failure / recovery drills

Landed as operator-facing recovery entries in `docs/TROUBLESHOOTING.md` (locked/corrupt SQLite DB,
broker outage / API error, stale market data). The dead/wedged-runner mode was already documented.
See that file and the F3 PR.

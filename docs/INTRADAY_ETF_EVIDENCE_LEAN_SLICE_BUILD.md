# Intraday ETF Evidence — Lean Slice Build Summary

**Built:** 2026-06-19 (overnight, autonomous). **Branch:** `intraday-etf-evidence-lean-slice` (worktree under `.worktrees/`). **NOT merged** — for your fresh-session review.

Baseline at branch point (master `b9f39f5`): `2743 passed, 1 skipped`. At branch tip: **`2795 passed, 1 skipped, 4 xfailed`** (full `ruff check` + `ruff format` clean).

## Process followed
spec → 4-lens adversarial review of the spec (Workflow) → fold → implement each PR with TDD → `/ponytail-review` each PR → `risk-invariant-reviewer` on PR1. Plan + the review fold are in `docs/superpowers/plans/2026-06-19-intraday-etf-evidence-lean-slice.md` (read the "Review fold" section — it lists every finding and resolution).

## Commits (each its own PR, tests green per commit)
| commit | PR | what |
|---|---|---|
| `d3d2864` | PR1 | ADR 0016 enforcement made real (denylist) + `universe.liquid_etf_core.v1` (17 ETFs) |
| `9b43ca9` | PR2 | intraday-aware data-readiness scanner |
| `2f655fb` | PR3 | `data readiness` CLI command |
| `d9f8c8a` | PR4 | IEX price-fidelity gate (shape-normalized cross-check) |
| `26786ca` | PR5 | no-trade + time-of-day-null baselines |
| `99dbab4` | — | ruff format pass (hook runs `ruff check` only) |

## What each PR does
- **PR1 (risk-reviewed → APPROVED).** `strategies/instrument_eligibility.py` denylists known leveraged/inverse/vol ETPs; wired into `resolve_universe_ref` (manifest path) AND `_load_universe` (inline path) — closing the inline bypass. ADR 0016:42 claimed enforcement that never existed; now it does. Denylist verified to trip none of the 8 manifests / inline configs. New `universe.liquid_etf_core.v1`.
  - risk-invariant-reviewer ran 7 break attempts (casing evasion, near-collision false-positive, live-submit-path reach, runner-wedge, silent-skip-loosens-control, phantom run-row, framing inversion) — **all failed**. Two diagnosability nits (documented): a forbidden config reached via `resolve_config_path` surfaces as "not found" (still fail-closed); existing `data fetch-universe` keeps its generic error code (I did NOT touch it — surgical; the NEW readiness CLI uses the distinct `universe_contains_forbidden_instrument` code).
- **PR2.** `data/intraday_readiness.py` — per-session completeness vs an expected bar grid (390 full / 210 half-day ÷ timeframe). WARNING-level checks: no-bars, coverage-below-floor, missing session open/close bar, interior gap, zero-volume, stale tail. Row-order-invariant content hash + feed-quality label. Session helpers lazily imported (keeps the strategy fleet out of the data import graph).
- **PR3.** `data readiness --universe-ref ... --timeframe 5m [--cross-check-reference]`. Rejects daily timeframe; distinct eligibility error code.
- **PR4 (see caveat).** `data/consolidated_reference.py` — fetches free daily OHLC (Yahoo, `auto_adjust=True`) and cross-checks IEX session ranges vs the consolidated reference. Compares **shape** `(high-low)/close`, not absolute levels, so an adjustment offset can't false-positive. Advisory `iex_inward_price_bias` warning only.
- **PR5.** `bench_no_trade` + `bench_time_of_day_null` (config-driven, auto-registered, stage=backtest). Plus a unit test proving the existing unconditional-long trades any single symbol (XLB), not just SPY.

## Decisions you should weigh on review
1. **PR4 is the weakest PR — the iex-methodology lens recommended deferring it.** I built it (decision #4 = "build it") but corrected the review's blocker: the original plan compared Alpaca **adjusted** intraday vs Yahoo **raw** daily (~7% offset → would flag the whole universe). Rebuilt to compare normalized range shape (adjustment-invariant). Residual: the trigger threshold (range-ratio floor 0.80, >50% of ≥5 sessions) is a **heuristic, not calibrated against real IEX-vs-consolidated data** (couldn't validate autonomously). It's advisory-only, opt-in, self-contained — **the clean first cut if you want to trim** (delete one module + one CLI flag).
2. **PR1 guards BOTH the manifest and inline universe paths.** The handoff said "resolve_universe_ref only"; I added the inline path too because a half-enforcement (`universe: [TQQQ]` inline still passes) repeats exactly the gap ADR 0016 already had. Risk reviewer confirmed safe.
3. **"Across 17 ETFs" is proven at unit level, not wired operationally.** A benchmark pointed at the 17-ETF manifest trades `sorted(universe)[0]` (= DIA) only. Per decision #2 (17 single-symbol backtests, no multi-symbol strategy logic), the operational fan-out needs 17 single-symbol configs or a batch recipe — a documented follow-up, not delivered here.

## Deferred / follow-ups
- Operational 17-symbol backtest fan-out (per-symbol configs or batch recipe).
- PR4 trigger calibration against a real IEX-vs-consolidated sample.
- Lift `_session_intraday` pure-time helpers to a neutral module (kills the data→strategies import smell; currently mitigated by lazy import).
- 5Min cache warmup for the 16 non-SPY ETFs (operational; not in the diff — see plan "Cache warmup"). New ETF backtests report 0 trades until warmed.
- Everything explicitly OUT of the lean slice: E-PR2/3/4, Workstream F/G/H, any risk/execution/promotion/event-store change.

## How to review / re-run
```powershell
# tests (worktree uses a PYTHONPATH shadow — the .venv editable install points at master's src)
Set-Location 'C:\Users\zdm80\Milodex\.worktrees\intraday-etf-evidence-lean-slice'
$env:PYTHONPATH = 'C:\Users\zdm80\Milodex\.worktrees\intraday-etf-evidence-lean-slice\src'
& 'C:\Users\zdm80\Milodex\.venv\Scripts\python.exe' -m pytest -q   # expect 2795 passed, 1 skipped
& 'C:\Users\zdm80\Milodex\.venv\Scripts\python.exe' -m milodex.cli.main data readiness --help
```
**Do not merge** without your own review. When ready: `git worktree remove` cleans up.

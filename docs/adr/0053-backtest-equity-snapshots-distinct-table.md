# ADR 0053 — Backtest equity snapshots are a distinct table from broker portfolio snapshots

**Status:** Accepted (2026-05-19)

## Context

`portfolio_snapshots` was originally specified (ADR 0011, `analytics/snapshots.py:1-19`)
as "broker-side account state" — one row per broker snapshot. Over time, a second
writer attached: `BacktestEngine._simulate` started calling `record_daily_snapshot()`
to record simulated equity per walk-forward window, persisting to the same table.

The two writers describe different concepts:
- **Broker portfolio snapshot** — one observation of the real Alpaca paper account.
- **Backtest equity sample** — one point on a simulated equity curve, scoped to a
  walk-forward window of a backtest run.

`PerformanceState._SQL_ALL_PAPER` dedups by `recorded_at` keeping the highest-id row
per timestamp. With both writers present, the earliest dedup-survivor was a
backtest's starting equity (~$1015) and the latest was today's broker equity
(~$101,148). The reported ALL-PAPER return was exactly `(101148.22 / 1015.02) - 1
= +9865.19%`. Peak-to-trough drawdown was -98.99%. Both numbers appeared on the
operator's primary trust surface.

The damage budget for repeating this class of error is zero: the operator's daily
trust check depends on this number being correct.

## Decision

1. `portfolio_snapshots` is now the **broker-side account-state ledger only**.
   The contract is reinstated in `analytics/snapshots.py` docstring.

2. A new table `backtest_equity_snapshots` holds simulated equity points. Schema
   parallels `portfolio_snapshots` plus a first-class `backtest_run_id INTEGER
   REFERENCES backtest_runs(id)` column. New backtests populate this column;
   migrated legacy rows leave it NULL (no reliable mapping from string `:wN`
   suffix).

3. The migration is in-framework SQL (migration 010), atomic via the framework's
   `BEGIN EXCLUSIVE` (see `event_store.py:1254`).

4. Anomalous rows whose provenance can't be cleanly attributed (notably the
   2024-12-31 / $149,315 / no-`:w` row) are **quarantined** to a new
   `portfolio_snapshots_quarantine` table, not deleted. Forensic preservation
   per the operator's principle that historical anomalies are evidence of past
   design failures and must remain inspectable.

5. Future code MUST NOT merge these tables. New writers needing equity snapshots
   for any third concept (live capital? micro-live?) get their own table.

## Consequences

- `BacktestEngine._simulate` swaps its writer call from `record_daily_snapshot`
  to `record_backtest_equity_snapshot`. Existing callers of `record_daily_snapshot`
  (only `StrategyRunner.shutdown`) are unaffected.

- `analytics/reports.py:build_trust_report()` is updated to read backtest equity
  from `list_backtest_equity_snapshots_for_strategy()`. Without this, trust reports
  for backtest strategies would silently lose their snapshot history.

- `PerformanceState._SQL_ALL_PAPER` requires no SQL change post-migration; the
  underlying table is clean by construction (quarantine handles the stray row).

- The `daily_pnl` column is preserved (nullable in the new table) — backtests
  don't track it the same way, but dropping the column would break
  `record_daily_snapshot`'s shared signature.

## Citations

- Complements ADR 0011 (event store as source of truth)
- Reaffirms `analytics/snapshots.py:1-19` docstring contract
- Operator principle: `feedback_inspect_before_deciding` (memory note)

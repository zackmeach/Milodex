-- Split portfolio_snapshots into broker-only + backtest-only tables (ADR 0053).
--
-- Background
-- ----------
-- `portfolio_snapshots` was intended (ADR 0011, analytics/snapshots.py:1-19) to
-- hold broker-side account state only. Two writers ended up using it:
--
--   1. StrategyRunner.shutdown -- one broker snapshot per session end.
--   2. BacktestEngine._simulate -- one simulated-equity point per walk-forward
--      window (session_id = `{run_id}:w{index}`) AND one per whole-period run
--      (session_id = run_id, matching backtest_runs.run_id directly).
--
-- The backtest rows mixed with broker rows corrupted PerformanceState's
-- ALL-PAPER dedup: earliest row was a backtest starting equity (~$1015),
-- latest was today's broker equity (~$101,148), reporting +9865.19% return
-- and -98.99% max drawdown on the operator's primary trust surface.
--
-- Fix
-- ---
-- New table `backtest_equity_snapshots` holds all simulated equity points.
-- All existing `:w`-suffixed rows migrate there. All whole-period backtest rows
-- (session_id IN backtest_runs.run_id) also migrate there with backtest_run_id
-- populated via direct join.
--
-- A `portfolio_snapshots_quarantine` table is created for future forensic use.
-- No rows are quarantined in this migration (all anomalous rows were
-- attributable to backtest_runs and are correctly migrated, not quarantined).
--
-- Idempotency: this migration runs only once, gated by _schema_version = 10
-- (handled by EventStore._apply_migrations). Re-running is a no-op.

-- ── New table: backtest equity snapshots ─────────────────────────────────────

CREATE TABLE backtest_equity_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at      TEXT    NOT NULL,
    session_id       TEXT    NOT NULL,
    strategy_id      TEXT    NOT NULL,
    equity           REAL    NOT NULL,
    cash             REAL    NOT NULL,
    portfolio_value  REAL    NOT NULL,
    daily_pnl        REAL,                         -- nullable: backtests don't track this
    positions_json   TEXT    NOT NULL,
    backtest_run_id  INTEGER REFERENCES backtest_runs(id)
);

CREATE INDEX idx_backtest_equity_snapshots_session
    ON backtest_equity_snapshots(session_id);

CREATE INDEX idx_backtest_equity_snapshots_strategy
    ON backtest_equity_snapshots(strategy_id);

CREATE INDEX idx_backtest_equity_snapshots_backtest_run_id
    ON backtest_equity_snapshots(backtest_run_id);

-- ── Forensic quarantine table ─────────────────────────────────────────────────
-- Holds rows removed from portfolio_snapshots whose provenance cannot be
-- cleanly attributed. Created here for future use; no rows are quarantined
-- in this migration (all detected anomalies are attributable to backtest_runs).

CREATE TABLE portfolio_snapshots_quarantine (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id      INTEGER NOT NULL,   -- id from portfolio_snapshots at migration time
    recorded_at      TEXT    NOT NULL,
    session_id       TEXT    NOT NULL,
    strategy_id      TEXT    NOT NULL,
    equity           REAL    NOT NULL,
    cash             REAL    NOT NULL,
    portfolio_value  REAL    NOT NULL,
    daily_pnl        REAL    NOT NULL,
    positions_json   TEXT    NOT NULL,
    quarantine_reason TEXT   NOT NULL
);

-- ── Migrate walk-forward backtest rows (:w suffix) ───────────────────────────
-- Rows with session_id LIKE '%:w%' are walk-forward window snapshots.
-- We populate backtest_run_id by stripping the ':wN' suffix and joining to
-- backtest_runs on run_id (mirrors the explanation backfill in migration 008).

INSERT INTO backtest_equity_snapshots (
    recorded_at, session_id, strategy_id,
    equity, cash, portfolio_value, daily_pnl, positions_json,
    backtest_run_id
)
SELECT
    ps.recorded_at, ps.session_id, ps.strategy_id,
    ps.equity, ps.cash, ps.portfolio_value, ps.daily_pnl, ps.positions_json,
    br.id
FROM portfolio_snapshots ps
LEFT JOIN backtest_runs br
    ON br.run_id = substr(ps.session_id, 1, instr(ps.session_id, ':w') - 1)
WHERE ps.session_id LIKE '%:w%';

DELETE FROM portfolio_snapshots
WHERE session_id LIKE '%:w%';

-- ── Migrate whole-period backtest rows (session_id = backtest_runs.run_id) ────
-- Rows where the session_id matches a backtest_runs.run_id directly are
-- whole-period backtest end-snapshots. All 5 such rows found in the live DB
-- (recorded_at 2024-12-31, ids 255-259) belong to backtest_runs ids 79-83.

INSERT INTO backtest_equity_snapshots (
    recorded_at, session_id, strategy_id,
    equity, cash, portfolio_value, daily_pnl, positions_json,
    backtest_run_id
)
SELECT
    ps.recorded_at, ps.session_id, ps.strategy_id,
    ps.equity, ps.cash, ps.portfolio_value, ps.daily_pnl, ps.positions_json,
    br.id
FROM portfolio_snapshots ps
JOIN backtest_runs br ON br.run_id = ps.session_id
WHERE ps.session_id NOT LIKE '%:w%';

DELETE FROM portfolio_snapshots
WHERE session_id IN (SELECT run_id FROM backtest_runs)
  AND session_id NOT LIKE '%:w%';

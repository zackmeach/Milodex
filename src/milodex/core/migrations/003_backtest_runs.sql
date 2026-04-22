-- Backtest engine ledger tables.
--
-- Backtest and paper runs share the same `trades` table so analytics
-- never has to join disparate schemas. The `trades.source` column
-- (already present since migration 001) distinguishes rows: 'paper' for
-- live paper sessions, 'backtest' for backtest engine output. Backtest
-- trades are additionally linked to a `backtest_runs` row via the new
-- `trades.backtest_run_id` foreign key.
--
-- See docs/reviews/PROJECT_STATE_ASSESSMENT_2026-04-21.md Finding #8
-- and ADR 0018 for the rationale for shared-ledger storage.

CREATE TABLE backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    config_path TEXT,
    config_hash TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    slippage_pct REAL,
    commission_per_trade REAL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX idx_backtest_runs_strategy
    ON backtest_runs(strategy_id);

ALTER TABLE trades ADD COLUMN backtest_run_id INTEGER REFERENCES backtest_runs(id);

CREATE INDEX idx_trades_backtest_run_id
    ON trades(backtest_run_id);

CREATE INDEX idx_trades_source
    ON trades(source);

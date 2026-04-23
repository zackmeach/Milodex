-- Daily portfolio snapshots, written by analytics.snapshots.record_daily_snapshot.
--
-- One row per trading-day close per session. Read-path only: fuels trust
-- reports and analytics dashboards. Not part of the trade-event ledger;
-- separate table keeps the write surface of execution/backtest narrow.
--
-- `strategy_id` is denormalized (not joined through sessions) so per-strategy
-- snapshot queries are a single index lookup.

CREATE TABLE portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    session_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    portfolio_value REAL NOT NULL,
    daily_pnl REAL NOT NULL,
    positions_json TEXT NOT NULL
);

CREATE INDEX idx_portfolio_snapshots_session
    ON portfolio_snapshots(session_id);

CREATE INDEX idx_portfolio_snapshots_strategy
    ON portfolio_snapshots(strategy_id);

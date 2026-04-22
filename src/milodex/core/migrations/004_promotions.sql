-- Promotion pipeline ledger.
--
-- Each row records one stage advancement for a strategy, capturing the gate
-- metrics at the time of promotion and who approved it.  The table is
-- append-only — no rows are deleted or updated.
--
-- promotion_type: 'statistical'      — Sharpe/DD/trade-count thresholds applied
--                 'lifecycle_exempt' — regime / lifecycle-proof strategy, exempt
--                                      per SRS R-PRM-004

CREATE TABLE promotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    from_stage TEXT NOT NULL,
    to_stage TEXT NOT NULL,
    promotion_type TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    backtest_run_id TEXT,
    sharpe_ratio REAL,
    max_drawdown_pct REAL,
    trade_count INTEGER,
    notes TEXT
);

CREATE INDEX idx_promotions_strategy_id ON promotions(strategy_id);

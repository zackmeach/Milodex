CREATE TABLE explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    status TEXT NOT NULL,
    strategy_name TEXT,
    strategy_stage TEXT,
    strategy_config_path TEXT,
    config_hash TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    order_type TEXT NOT NULL,
    time_in_force TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    market_open INTEGER NOT NULL,
    latest_bar_timestamp TEXT,
    latest_bar_close REAL,
    account_equity REAL NOT NULL,
    account_cash REAL NOT NULL,
    account_portfolio_value REAL NOT NULL,
    account_daily_pnl REAL NOT NULL,
    risk_allowed INTEGER NOT NULL,
    risk_summary TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    risk_checks_json TEXT NOT NULL,
    context_json TEXT NOT NULL
);

CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    explanation_id INTEGER NOT NULL REFERENCES explanations(id) ON DELETE CASCADE,
    recorded_at TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    order_type TEXT NOT NULL,
    time_in_force TEXT NOT NULL,
    estimated_unit_price REAL NOT NULL,
    estimated_order_value REAL NOT NULL,
    strategy_name TEXT,
    strategy_stage TEXT,
    strategy_config_path TEXT,
    submitted_by TEXT NOT NULL,
    broker_order_id TEXT,
    broker_status TEXT,
    message TEXT
);

CREATE TABLE kill_switch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    reason TEXT
);

CREATE TABLE strategy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    exit_reason TEXT,
    metadata_json TEXT NOT NULL
);

CREATE INDEX idx_explanations_strategy_time
    ON explanations(strategy_name, recorded_at);

CREATE INDEX idx_trades_strategy_time
    ON trades(strategy_name, recorded_at);

CREATE INDEX idx_trades_symbol
    ON trades(symbol);

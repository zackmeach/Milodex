-- Durable reconciliation readiness for R-OPS-004.
--
-- Every reconcile invocation records a run, including broker-down/incomplete
-- runs. Position drift corrections are append-only compensating entries and
-- are folded after trades; trades are never rewritten.

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    local_trading_day TEXT NOT NULL,
    status TEXT NOT NULL,
    broker_connected INTEGER NOT NULL,
    market_open INTEGER,
    checked_dimensions_version TEXT NOT NULL,
    checked_dimensions_json TEXT NOT NULL,
    deferred_checks_json TEXT NOT NULL,
    incident_hash TEXT,
    incident_recorded INTEGER NOT NULL,
    incident_deduplicated INTEGER NOT NULL,
    reason_codes_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_runs_recorded_at
    ON reconciliation_runs(recorded_at);

CREATE INDEX IF NOT EXISTS idx_reconciliation_runs_trading_day
    ON reconciliation_runs(local_trading_day, id);

CREATE TABLE IF NOT EXISTS reconciliation_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adjustment_id TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    symbol TEXT NOT NULL,
    local_qty_before REAL NOT NULL,
    broker_qty REAL NOT NULL,
    delta_qty REAL NOT NULL,
    reason TEXT NOT NULL,
    source_incident_hash TEXT NOT NULL,
    context_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_adjustments_symbol
    ON reconciliation_adjustments(symbol, effective_at);

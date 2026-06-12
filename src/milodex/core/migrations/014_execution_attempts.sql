-- Durable pre-submit outbox for broker order attempts (P1-02).
--
-- Before this table, ExecutionService called broker.submit_order() with NO
-- durable local record of the attempt: a crash (or DB failure) after the
-- broker accepted the order but before the explanation/trade rows committed
-- left the order invisible to the duplicate-order veto — exactly when the
-- veto is needed most. The outbox protocol is:
--
--   1. risk evaluation passes (blocked intents never reach this table)
--   2. INSERT one row here with status='pending' and a pre-generated
--      client_order_id (also passed to the broker for exact reconciliation)
--   3. call broker.submit_order()
--   4. UPDATE the row: 'submitted' + broker_order_id on success,
--      'rejected'/'error' + failure_detail on broker rejection/exception
--
-- A row stuck at 'pending' means a crash between steps 2 and 4 — the order
-- may or may not exist at the broker. The duplicate-order check counts
-- 'pending' and 'submitted' attempts alongside trades (fail-safe: veto),
-- and reconciliation surfaces stale 'pending' rows as informational WARNs.

CREATE TABLE IF NOT EXISTS execution_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL UNIQUE,
    strategy_name TEXT,
    strategy_config_path TEXT,
    session_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    order_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    broker_order_id TEXT,
    finalized_at TEXT,
    failure_detail TEXT
);

-- Drives the duplicate-order backstop (symbol + status + window scan).
CREATE INDEX IF NOT EXISTS idx_execution_attempts_symbol_status_created
    ON execution_attempts(symbol, status, created_at);

-- Drives the stale-pending sweep (status + age scan, symbol-agnostic).
CREATE INDEX IF NOT EXISTS idx_execution_attempts_status_created
    ON execution_attempts(status, created_at);

-- The hardened duplicate-order count correlates attempts to their trade row
-- via broker_order_id (NOT EXISTS subquery). Without this index that
-- correlation full-scans trades per candidate attempt on the submit hot
-- path — same rationale as idx_trades_explanation_id (migration 013).
CREATE INDEX IF NOT EXISTS idx_trades_broker_order_id
    ON trades(broker_order_id);

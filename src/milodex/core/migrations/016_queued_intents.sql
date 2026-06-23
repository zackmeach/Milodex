-- Queued intents (queue-at-open). A daily runner that confirms a close-bar
-- lock-in while the next session's open is in the future persists the intended
-- order here instead of submitting immediately; at the next session open a
-- runner drains it through the full risk battery and submits.
--
-- Unlike experiment_registry / promotions this table is NOT append-only — it is
-- a lifecycle table. A row moves queued -> consumed | expired | obsolete via the
-- mark_* methods. The drain CAS (mark_queued_intent_consumed) is the single
-- statement that gates a broker submit: exactly one process can flip a 'queued'
-- row to 'consumed', so a duplicate drain is impossible by construction.
--
-- idempotency_key = "{strategy_id}|{trading_session}|{side}|{symbol}" (UNIQUE):
-- one queued intent per strategy per session per side per symbol. A re-persist
-- of the same logical intent collides on the UNIQUE constraint rather than
-- double-queuing.
--
-- Additive only: creates one new table no existing code reads, so the minimum
-- compatible schema version is unchanged (per migration 007's
-- append-never-rewrite principle).

CREATE TABLE queued_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    strategy_id TEXT,
    strategy_config_path TEXT,
    config_hash TEXT,
    session_id TEXT,
    trading_session TEXT,
    locked_in_bar_timestamp TEXT,
    symbol TEXT,
    side TEXT,
    intent_class TEXT,
    notional_pct REAL,
    expected_stage TEXT,
    expected_max_positions INTEGER,
    expected_max_position_pct REAL,
    expected_daily_loss_cap_pct REAL,
    intent_payload_json TEXT,
    reasoning_json TEXT,
    created_at TEXT,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    consumed_at TEXT,
    consumed_by TEXT
);

CREATE INDEX idx_queued_intents_strategy_status
    ON queued_intents(strategy_id, status);

CREATE INDEX idx_queued_intents_status_expires
    ON queued_intents(status, expires_at);

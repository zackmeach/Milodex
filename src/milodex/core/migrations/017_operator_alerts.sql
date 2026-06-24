-- Operator-alert ledger (queue-at-open, ADR 0057 / D-6 durable-state clause).
--
-- Append-only record of operator-visible anomalies emitted OUTSIDE the
-- explanation lane -- e.g. an EXIT intent dropped for clean-handoff ambiguity
-- (I-4 fence) or a not-tradable drop. Durable so a relaunch / audit can
-- reconstruct WHY an exit did not drain. Additive only: no existing code reads
-- this table, so the minimum compatible schema version is UNCHANGED (stays 12).

CREATE TABLE operator_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    strategy_id TEXT,
    session_id TEXT,
    symbol TEXT,
    side TEXT,
    context_json TEXT
);

CREATE INDEX idx_operator_alerts_alert_type ON operator_alerts(alert_type);

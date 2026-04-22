ALTER TABLE explanations ADD COLUMN session_id TEXT;

ALTER TABLE trades ADD COLUMN session_id TEXT;

CREATE INDEX idx_explanations_session_id
    ON explanations(session_id);

CREATE INDEX idx_trades_session_id
    ON trades(session_id);

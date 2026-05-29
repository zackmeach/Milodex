-- Index the ON DELETE CASCADE foreign key trades.explanation_id.
--
-- Without an index on the FK child column, deleting an explanation must
-- full-scan the entire trades table to enforce the cascade. That makes bulk
-- explanation deletes quadratic (rows_deleted x trades) and effectively
-- unbounded — `milodex maintenance compact` pruning ~1M backtest explanations
-- stalled indefinitely until this index was added. With it, the cascade check
-- (and the prune) is O(log n) per row: ~900k rows deleted in seconds.
-- See docs/incidents/2026-05-29-runner-fleet-oom-freeze.md follow-ups.

CREATE INDEX IF NOT EXISTS idx_trades_explanation_id ON trades(explanation_id);

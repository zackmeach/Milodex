-- Experiment registry (R-PRM-011, PROMOTION_GOVERNANCE.md "Experiment Registry").
--
-- A formal research-memory ledger covering **promoted, rejected, failed,
-- inconclusive, abandoned, and active** strategy ideas — not only the ones that
-- made it to paper. Rejected work still contributes to the system's discipline;
-- discarding it discards the lesson with it.
--
-- The table is append-only — like `promotions`, no row is ever updated or
-- deleted. A strategy instance is never removed from durable state (R-PRM-011);
-- instead a new row records its terminal status, and `update_experiment`
-- carries prior fields forward into a fresh row. The version history of one
-- `experiment_id` IS its row sequence (latest-by-id wins).
--
--   experiment_id   stable idea key (e.g. "intraday-etf-rsi2-2026-06")
--   strategy_id     frozen instance id, nullable until an instance is frozen
--   config_hash     frozen manifest hash, nullable until frozen
--   hypothesis      the idea under test
--   stage_reached   backtest|paper|micro_live|live
--   terminal_status promoted|rejected|failed|inconclusive|abandoned|active
--   rationale       why it reached this terminal status
--   evidence_json   run_ids + per-symbol candidate-vs-baseline deltas + readiness ref
--   lessons         lessons or cautions learned (nullable)
--   revisitable     1 if worth revisiting later, else 0 (permanently retired)
--
-- Additive only: creates one new table no existing code reads, so the minimum
-- compatible schema version is unchanged (per migration 007's
-- append-never-rewrite principle).

CREATE TABLE experiment_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    strategy_id TEXT,
    config_hash TEXT,
    hypothesis TEXT NOT NULL,
    stage_reached TEXT NOT NULL,
    terminal_status TEXT NOT NULL,
    rationale TEXT NOT NULL,
    evidence_json TEXT,
    lessons TEXT,
    revisitable INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_experiment_registry_experiment_id ON experiment_registry(experiment_id);
CREATE INDEX idx_experiment_registry_terminal_status ON experiment_registry(terminal_status);

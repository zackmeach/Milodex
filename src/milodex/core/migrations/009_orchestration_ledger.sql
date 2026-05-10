-- ADR 0040 durable orchestration ledger.
--
-- These tables record operator intent and queue/progress state for Kanban/CLI
-- bulk orchestration. They intentionally do not create or mutate execution
-- evidence rows such as backtest_runs, strategy_runs, promotions, trades, or
-- explanations. Workers may later link a job to an execution row through the
-- polymorphic execution_ref fields.

CREATE TABLE orchestration_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL UNIQUE,
    action_type TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE orchestration_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    batch_id TEXT NOT NULL REFERENCES orchestration_batches(batch_id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    requested_stage TEXT NOT NULL,
    status TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    cancel_requested_at TEXT,
    execution_ref_type TEXT,
    execution_ref TEXT,
    progress_current INTEGER,
    progress_total INTEGER,
    progress_label TEXT,
    error_code TEXT,
    error_message TEXT,
    metadata_json TEXT NOT NULL
);

CREATE INDEX idx_orchestration_batches_status_requested_at
    ON orchestration_batches(status, requested_at);

CREATE INDEX idx_orchestration_jobs_batch_id
    ON orchestration_jobs(batch_id);

CREATE INDEX idx_orchestration_jobs_status
    ON orchestration_jobs(status);

CREATE INDEX idx_orchestration_jobs_execution_ref
    ON orchestration_jobs(execution_ref_type, execution_ref);

CREATE INDEX idx_orchestration_jobs_strategy_status
    ON orchestration_jobs(strategy_id, status);

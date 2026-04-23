-- Frozen strategy-config snapshots per promoted stage (ADR 0015, Phase 1.4 slice 1).
--
-- Each row records one freeze event: the canonical hash of the strategy YAML,
-- the full canonicalized JSON (what was hashed — so slice 2's evidence package
-- can reproduce the exact config), and the stage the strategy was at when the
-- freeze happened. Rows are append-only; the "active" manifest at a given
-- (strategy_id, stage) pair is the most recent row (latest id wins).
--
-- Why a separate table from `promotions`: a manifest is a snapshot consumed by
-- a promotion event, not a promotion event itself. Slice 2 will link promotions
-- to manifests via manifest_id. See AD-1 in the slice-1 plan.

CREATE TABLE strategy_manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    config_path TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    frozen_by TEXT NOT NULL
);

CREATE INDEX idx_strategy_manifests_strategy_stage
    ON strategy_manifests(strategy_id, stage);

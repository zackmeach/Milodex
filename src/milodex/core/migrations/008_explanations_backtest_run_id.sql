-- Add explicit backtest_run_id ancestor to explanations (the dual-ancestor model).
--
-- Background
-- ----------
-- ADR 0011 makes the event store the source of truth for trade and decision
-- history. Every explanation row should be linkable back to the run that
-- produced it. The schema as of migration 002 only carried `session_id` on
-- `explanations`, intended to point at `strategy_runs.session_id`.
--
-- The backtest engine, however, also writes explanations during simulation —
-- it overloads `session_id` to carry either the bare backtest run UUID
-- (whole-period mode) or `<run_id>:w<N>` (walk-forward window). Neither
-- value is in `strategy_runs`, so any analytic that joins
-- `explanations` to `strategy_runs USING (session_id)` silently drops every
-- backtest-produced row. The 2026-05-07 EOD audit surfaced this as
-- "6,223 orphan evaluations"; the real count across the full event store
-- was ~74,800 backtest rows that were never orphans — they had a valid
-- ancestor in `backtest_runs`, just not via the column the audit was
-- joining on.
--
-- Fix shape: dual-ancestor model
-- ------------------------------
-- An explanation row now has TWO optional ancestor columns. Exactly one is
-- expected to be set per row going forward:
--
--   * `session_id` (TEXT)     — references `strategy_runs.session_id` for
--                               live paper-runner explanations.
--   * `backtest_run_id` (INT) — references `backtest_runs.id` for backtest
--                               engine explanations (whole-period and
--                               walk-forward windows alike).
--
-- This mirrors the long-standing pattern on `trades`, where
-- `trades.backtest_run_id` was added in migration 003 for exactly the same
-- reason. The two ancestor tables stay separate (parallel ID spaces) — see
-- ADR 0018 for the shared-ledger rationale that keeps both kinds of trade
-- in `trades` while their parent runs live in distinct tables.
--
-- Code-path enforcement (rather than CHECK constraint)
-- ----------------------------------------------------
-- We enforce "exactly one of session_id / backtest_run_id" in
-- `EventStore.append_explanation`, not as a SQLite CHECK constraint, for
-- two reasons:
--
--   1. The constraint we want is "session_id refers to a real strategy_runs
--      row OR backtest_run_id refers to a real backtest_runs row." SQLite
--      cannot express a CHECK that references a separate table; FK
--      constraints can't be conditional. The best CHECK we could write is
--      "at least one is non-null," which is weaker than the code-path check.
--   2. Adding a CHECK against existing data would require deleting or
--      relabeling 9,319 legacy rows with NULL session_id (operator-CLI
--      smoke tests, pre-session backtests). Those have forensic value and
--      must not be deleted to satisfy a constraint.
--
-- The `backtest_run_id` column does carry a true FK reference to
-- `backtest_runs(id)`. Combined with `PRAGMA foreign_keys=ON` in the
-- connection setup, this gives DB-level guarantees on the new column for
-- new inserts.
--
-- Backfill strategy
-- -----------------
-- Existing rows are updated in place using a three-tier priority:
--
--   Tier 1: An explanation that has a corresponding trade row carries the
--           parent backtest_run_id directly on `trades.backtest_run_id`
--           (added in migration 003). Use that when present.
--   Tier 2: Walk-forward rows have session_id of the form `<uuid>:w<N>`.
--           Strip the suffix and look up `backtest_runs.run_id`.
--   Tier 3: Whole-period backtest rows have session_id directly equal to
--           `backtest_runs.run_id`. Look it up directly.
--
-- The 9,319 NULL-session_id legacy rows (operator CLI, pre-session
-- backtests with bar-date timestamps from 2022) and the 417 paper-runner
-- crash orphans (sessions that died before writing their `strategy_runs`
-- row) remain unlinked. They are documented in the PR body and are NOT
-- deleted — their forensic content is still readable, they simply have no
-- ancestor. Going forward, the code-path check rejects new writes that
-- lack an ancestor.
--
-- This migration is idempotent: re-running it is a no-op because
-- `ALTER TABLE ... ADD COLUMN` is gated by the `_schema_version` table
-- (handled in `EventStore._apply_migrations`), and the UPDATEs only touch
-- rows where `backtest_run_id IS NULL`.

ALTER TABLE explanations ADD COLUMN backtest_run_id INTEGER REFERENCES backtest_runs(id);

CREATE INDEX idx_explanations_backtest_run_id
    ON explanations(backtest_run_id);

-- Tier 1 backfill: link via trades.backtest_run_id when a trade row exists.
-- For an explanation that produced a trade, the trade's backtest_run_id is
-- the canonical answer.
UPDATE explanations
SET backtest_run_id = (
    SELECT t.backtest_run_id
    FROM trades t
    WHERE t.explanation_id = explanations.id
      AND t.backtest_run_id IS NOT NULL
    LIMIT 1
)
WHERE backtest_run_id IS NULL
  AND id IN (
      SELECT t.explanation_id FROM trades t WHERE t.backtest_run_id IS NOT NULL
  );

-- Tier 2 backfill: walk-forward window rows. session_id matches `<uuid>:w<N>`;
-- strip the suffix and look up the parent backtest_runs.id by run_id.
-- instr() returns 0 when the substring is missing, so the WHERE filter
-- guarantees we only touch rows that have the suffix structure.
UPDATE explanations
SET backtest_run_id = (
    SELECT br.id
    FROM backtest_runs br
    WHERE br.run_id = substr(explanations.session_id, 1, instr(explanations.session_id, ':w') - 1)
)
WHERE backtest_run_id IS NULL
  AND session_id IS NOT NULL
  AND instr(session_id, ':w') > 0
  AND substr(session_id, 1, instr(session_id, ':w') - 1) IN (SELECT run_id FROM backtest_runs);

-- Tier 3 backfill: whole-period backtest rows. session_id IS the run_id.
UPDATE explanations
SET backtest_run_id = (
    SELECT br.id FROM backtest_runs br WHERE br.run_id = explanations.session_id
)
WHERE backtest_run_id IS NULL
  AND session_id IS NOT NULL
  AND session_id IN (SELECT run_id FROM backtest_runs);

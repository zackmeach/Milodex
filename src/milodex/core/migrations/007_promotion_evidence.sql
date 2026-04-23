-- Promotion evidence package + reversal chain (ADR 0015, Phase 1.4 slice 2).
--
-- Adds three nullable columns to `promotions`:
--   manifest_id        FK -> strategy_manifests(id). The manifest frozen as part
--                      of this promotion. Null for historical rows written
--                      before slice 2.
--   reverses_event_id  FK -> promotions(id). Populated only by `demote`. The
--                      reversal chain (R-PRM-010) is reconstructable by walking
--                      this column.
--   evidence_json      Structured evidence package as JSON per
--                      PROMOTION_GOVERNANCE.md "Evidence Package: backtest ->
--                      paper". Null for historical rows.
--
-- Forward-only. Old rows stay valid; new rows always populate manifest_id and
-- evidence_json. Per ADR 0011's append-columns-never-rewrite-rows principle.

ALTER TABLE promotions ADD COLUMN manifest_id INTEGER REFERENCES strategy_manifests(id);
ALTER TABLE promotions ADD COLUMN reverses_event_id INTEGER REFERENCES promotions(id);
ALTER TABLE promotions ADD COLUMN evidence_json TEXT;

CREATE INDEX idx_promotions_reverses_event_id ON promotions(reverses_event_id);

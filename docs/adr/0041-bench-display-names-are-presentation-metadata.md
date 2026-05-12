# ADR 0041 — Bench display names are presentation metadata

**Status:** Accepted · 2026-05-10
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-A (display-name provenance), [ADR 0015](0015-strategy-identifier-and-frozen-manifest.md) (stable strategy identity), [ADR 0039](0039-stage-session-and-bench-section-are-distinct.md) (Bench read model), [PHASE6_OPERATOR_KANBAN_PREP.md](../PHASE6_OPERATOR_KANBAN_PREP.md)

## Context

The Bench prototype uses human names such as "ATR Channel Breakout" above dotted strategy ids. Production strategy configs have a stable `strategy.id`, a descriptive sentence, and identity-bearing filename conventions, but no first-class display-name field. The current GUI read model derives `name` from a short `description` or from `strategy_id`; execution config also has a legacy optional `strategy.name` fallback that must not become identity.

Milodex already treats `strategy_id` as the durable unit for config loading, promotion, manifests, attribution, event-store rows, and trade linkage. A human-readable display name improves the operator surface, but it cannot replace or mutate that identity.

## Decision

1. **Display names are presentation metadata, not identity.** Durable rows, manifests, locks, promotions, attribution, trades, jobs, and CLI commands continue to key on `strategy_id`.

2. **The config schema gains an optional `strategy.display_name` field.** It is the canonical source for GUI display names when present. It is short, human-readable, and non-unique. It does not participate in config-hash identity, promotion authorization, or execution routing beyond normal config snapshot evidence.

3. **Fallback is deterministic derivation from `strategy_id`, not `description`.** If `display_name` is absent, read models derive title case from the semantic id segment, e.g. `breakout.daily.atr_channel.sector_etfs.v1` becomes "ATR Channel". Long descriptions remain explanatory copy, not row labels.

4. **Read models expose provenance.** Bench cards should carry both `display_name` and `display_name_source` (`config`, `derived`). The UI may show the dotted `strategy_id` beneath the name in all cases.

5. **Existing `strategy.name` is not promoted.** Any legacy execution-only use of `strategy.name` must not be used for Bench labels unless a future cleanup ADR consolidates it. New configs use `display_name` for presentation.

## Rationale

Putting the name in YAML keeps the operator-facing label beside the strategy it describes, without creating a second metadata database that can drift from configs. Making it optional avoids a migration cliff for existing configs and keeps the first Phase 6 PR small.

Keeping `strategy_id` visible and authoritative preserves the audit trail. A row can become friendlier without making durable records ambiguous. Using descriptions as labels was convenient for Phase 5, but descriptions are prose; they can be longer, change tone, or explain behavior in a way that makes them poor identifiers on a dense board.

## Consequences

- **ADR 0036 Q-A is answered.**
- The first implementation PR updates the loader/read model and `configs/sample_strategy.yaml` to document optional `strategy.display_name`.
- Current strategy configs may add display names opportunistically, but the Bench must render correctly before every config is annotated.
- `strategy_id` remains visible on cards and remains the only durable identity.
- No promotion, risk, manifest, attribution, or execution logic may key off `display_name`.

## Non-goals

- Does not rename existing strategy ids.
- Does not require backfilling display names into event-store history.
- Does not change trade attribution or risk identity.
- Does not implement the Bench surface.

# ADR 0015 — Strategy Identifier, Versioning, and Frozen Instance Manifest

**Status:** Implemented (runtime drift check, freeze CLI) — state machine pending slice 2
**Date:** 2026-04-21
**Implementation:** Phase 1.4 slice 1 landed 2026-04-23. See `docs/superpowers/plans/2026-04-23-phase-1-4-slice-1-frozen-manifest.md`. Slices 2 (promotion state machine + evidence package) and 3 (live-stage refusal hook) remain pending.

## Context

Phase 1 runs multiple strategy instances — the SPY/SHY lifecycle-proof strategy and the first mean-reversion research-target strategy — with more to come. Every instance is configured in YAML, backtested, paper-traded, and (eventually) promoted. Two problems will destroy Milodex's credibility if left unsolved:

1. **Config drift.** If the YAML file read during promotion review differs even slightly from the YAML file that was actually backtested or paper-traded, the evidence no longer supports the promotion decision. "I think I changed the RSI threshold after that run" is the exact kind of silent drift the research loop is built to prevent.
2. **Identity confusion.** When parameters change, is it still the same strategy, a tuned variant, or a new strategy? Without a rule, the promotion log becomes meaningless: "paper trading v1" and "paper trading v1 with entry = 8 instead of 10" are treated as the same evidence, and compounding errors follow.

The Q&A that motivated this decision is folded into the rules below; this ADR is the authoritative answer.

## Decision

Every promotable strategy instance carries a **structured identifier** and a **frozen, hashable manifest**.

### Structured identifier

Format: `family.template.variant.vN`

- **family** — the edge family or strategic archetype (`meanrev`, `momentum`, `breakout`, `regime`).
- **template** — the specific logic template within the family (`daily.pullback_rsi2`, `daily.sma200_rotation`).
- **variant** — the universe / parameter profile (`curated_largecap`, `spy_shy`).
- **vN** — monotonically increasing version integer, starting at `v1`.

Example: `meanrev.daily.pullback_rsi2.curated_largecap.v1`

The identifier lives in the YAML as `strategy.id`, and is the primary key for runs, promotion records, and audit logs. Human readability beats clever shorthand.

### Version vs. variant

A **new version** is minted when the change alters the strategy's logical identity, expected behavior, or evidence interpretation. Triggers include:

- changing the edge family (meanrev → breakout)
- switching long-only ↔ long/short
- changing execution timing semantics (next-open ↔ close ↔ intraday)
- changing stop semantics (close-based ↔ intraday)
- changing ranking from single-name evaluation to portfolio construction
- materially changing the universe-definition philosophy (curated list → rules-based screener)

A **variant** stays within the same version when only approved parameter values move inside the declared range. Examples: `rsi_entry_threshold` 8 vs. 10, `max_hold_days` 4 vs. 5, `stop_loss_pct` 4% vs. 5%, `ma_filter_length` 150 vs. 200.

Heuristic: if the change alters the strategy's **identity**, bump the version. If it only tunes the same idea, it is a variant.

### Frozen instance manifest

Every backtest run, paper run, and promotion decision references an **immutable manifest**, not the mutable live YAML on disk.

- At run start, the loaded strategy config is serialized in canonical form (sorted keys, stable scalar formatting) and hashed with SHA-256. The full manifest plus its hash are written to the SQLite event store alongside the run record.
- Runs, fills, promotion-log entries, and explanation records (R-XC-008) all carry the manifest hash as a foreign key. The hash is the fingerprint of the exact config that produced the evidence.
- Promotion is refused if the hash of the currently-on-disk YAML does not match the hash of the manifest under review. The operator must either re-run under the new config (producing new evidence), revert the YAML, or explicitly mint a new variant / version.
- Manifests are never overwritten. A new config state produces a new manifest row; the old manifest stays available for audit.

## Rationale

- **One promotion, one reviewed config.** The hash-match gate closes the gap between "what was backtested" and "what is being promoted." Config drift becomes detectable and blocking rather than silent.
- **Variant vs. version stays in human judgment, not code.** The rule is written in prose because the boundary is semantic (does this change the idea?), not syntactic. Trying to derive variant-vs-version from diff size would paper over real judgment calls.
- **Identifier structure matches how the research loop actually works.** Edge family, template logic, universe scope, and version are the axes operators actually reason on; a flat UUID would lose all of that.
- **SHA-256 on canonical YAML is trivial to implement and durable.** No external dependency, no schema-version coupling, no migration burden. The canonical-form step is the only subtlety; pin it with a test.

## Consequences

- SRS gains requirements R-STR-011 (frozen manifest), R-STR-012 (identifier scheme), R-STR-013 (version vs. variant), and R-STR-014 (disable-condition catalog per instance).
- The SQLite schema adds a `strategy_manifests` table keyed by hash, with FKs from `trades`, `promotion_log`, and (future) backtest-run tables. This is consistent with ADR 0011's event-shaped store.
- CLI gains a `milodex config fingerprint <path>` command (or equivalent) so operators can see the hash before running.
- Promotion commands verify hash-match before writing to `promotion_log`; a mismatch produces a structured error, not a stack trace.
- Editing a live strategy's YAML while a run is active does **not** affect that run — the in-memory manifest is the one that governs. The edit only takes effect on the next run, and will produce a different hash.

## Links

- Supersedes: none
- Depends on: [0010](0010-hybrid-source-of-truth.md), [0011](0011-sqlite-event-store.md)
- Related: [0009](0009-promotion-pipeline-stage-model.md)

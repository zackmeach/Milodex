# Architecture Decision Records

This directory holds the significant architectural decisions for Milodex. Each ADR records *one* decision in **Context → Decision → Rationale** form.

## Conventions

- One decision per file, numbered sequentially: `NNNN-short-title.md`.
- Status values: `Accepted`, `Superseded`, `Deprecated`. New decisions default to `Accepted`.
- ADRs are **forward-facing**. They do not list rejected alternatives; they explain the chosen path.
- If a decision is revisited, the new ADR supersedes the old one. Link both ways.

## Document Authority Order

When Milodex documents disagree, apply this order — top wins:

1. **ADRs / decision records** (this directory)
2. **Normative subsystem specs** — `docs/SRS.md` and any future `docs/*-spec.md`
3. **Implementation-constrained interface docs** — config schemas, YAML templates under `configs/`
4. **Descriptive docs / planning notes** — `docs/VISION.md` narrative sections, roadmap prose
5. **Brainstorm material or historical notes**

`CLAUDE.md` sits outside this hierarchy — it is a set of guardrails for how code gets written in this repo, not a spec or decision record. Where `CLAUDE.md` conflicts with an ADR or the SRS, fix `CLAUDE.md` to match.

In practice most conflicts are resolved by the normative subsystem spec (the SRS) until intentionally revised by a new ADR. A single Q&A, conversation, or brainstorm note is **not** authority to override a normative doc; it becomes authoritative only when folded into an ADR or the SRS.

## Index

| # | Title | Status |
|---|-------|--------|
| [0001](0001-alpaca-as-broker.md) | Alpaca as sole phase-one broker | Accepted |
| [0002](0002-parquet-as-cache.md) | Parquet as local historical cache | Accepted |
| [0003](0003-config-driven-strategies.md) | Strategy parameters live in YAML, not code | Accepted |
| [0004](0004-paper-only-phase-one.md) | Paper-only trading for all of phase one | Accepted |
| [0005](0005-kill-switch-manual-reset.md) | Kill switch requires manual reset | Accepted |
| [0006](0006-abc-pattern-for-external-integrations.md) | Abstract base classes for external integrations | Accepted |
| [0007](0007-argparse-for-cli.md) | `argparse` for the CLI | Accepted |
| [0008](0008-risk-layer-veto-architecture.md) | Risk layer has veto over execution | Accepted |
| [0009](0009-promotion-pipeline-stage-model.md) | Promotion pipeline as enforced stage model | Accepted |
| [0010](0010-hybrid-source-of-truth.md) | Hybrid source of truth (Alpaca for state, Milodex for decisions) | Accepted |
| [0011](0011-sqlite-event-store.md) | SQLite as the event-shaped store | Accepted |
| [0012](0012-runtime-and-dual-stop.md) | Runtime model and dual-stop shutdown semantics | Accepted |
| [0013](0013-market-orders-only-phase-one.md) | Market orders only for Phase 1 | Accepted |
| [0014](0014-cli-formatter-abstraction.md) | CLI formatter abstraction for dual human/JSON output | Accepted |
| [0015](0015-strategy-identifier-and-frozen-manifest.md) | Strategy identifier, versioning, and frozen instance manifest | Accepted |
| [0016](0016-phase1-instrument-whitelist.md) | Phase 1 instrument whitelist (long-only U.S. stocks + plain ETFs) | Accepted |
| [0017](0017-data-source-hierarchy.md) | Data source hierarchy, adjustment policy, and disagreement handling | Accepted |
| [0018](0018-durable-state-directory-consolidation.md) | Durable state lives under `data/`, not `state/` (supersedes SRS R-XC-006) | Accepted |
| [0019](0019-risk-types-belong-in-risk-module.md) | Risk types belong in `risk/`, not `execution/` | Accepted |
| [0020](0020-promotion-thresholds-are-code-invariants.md) | Promotion thresholds are code-level invariants, not YAML tuning | Accepted |

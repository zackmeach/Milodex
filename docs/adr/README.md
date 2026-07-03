# Architecture Decision Records

This directory holds the significant architectural decisions for Milodex. Each ADR records *one* decision in **Context → Decision → Rationale** form.

## Conventions

- One decision per file, numbered sequentially: `NNNN-short-title.md`.
- Status values: `Accepted`, `Superseded`, `Deprecated`. New decisions default to `Accepted`.
  ADRs may add a short qualifier after `Accepted` when the status itself is
  accepted but the scope is intentionally narrow.
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
| [0021](0021-walk-forward-metrics-are-oos-aggregate.md) | Walk-forward metrics are OOS-aggregate, not whole-period | Accepted |
| [0022](0022-strategy-rotation-scope-is-the-declared-universe.md) | Strategy rotation scope is the declared universe | Accepted |
| [0023](0023-phase-1-is-closed-and-phase-2-may-open.md) | Phase 1 is closed and Phase 2 may open | Accepted |
| [0024](0024-account-scoped-position-caps-are-authoritative.md) | Account-scoped position caps are authoritative; strategy `risk.max_positions` is informational | Accepted |
| [0025](0025-phase-2-is-closed-and-phase-3-may-open.md) | Phase 2 is closed and Phase 3 may open | Accepted |
| [0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) | Concurrent multi-strategy uses per-process supervisor | Accepted |
| [0027](0027-phase-3-is-closed-and-phase-4-may-open.md) | Phase 3 is closed and Phase 4 may open | Accepted |
| [0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md) | Phase 4 scope closes as cleanup and per-strategy attribution | Accepted |
| [0029](0029-per-strategy-position-attribution-at-risk-layer.md) | Per-strategy position attribution at the risk layer | Accepted |
| [0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) | Backtest is exploratory; manifest discipline binds at paper+ | Accepted |
| [0031](0031-phase-4-is-closed-and-phase-5-may-open.md) | Phase 4 is closed and Phase 5 may open | Accepted |
| [0032](0032-audit-trail-backfill-policy.md) | Audit-trail backfill policy | Accepted |
| [0033](0033-gui-runtime-is-pyside6-qt-quick.md) | GUI runtime is PySide6 + Qt Quick (full QML) | Accepted |
| [0034](0034-phase-5-scope-orders-observability-before-features.md) | Phase 5 scope orders observability before features | Accepted |
| [0035](0035-design-system-and-theme-architecture.md) | Design system and theme architecture | Accepted |
| [0036](0036-operator-kanban-surface-for-promotion-pipeline.md) | Operator Kanban surface for the promotion pipeline | Accepted - visual spec only |
| [0037](0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md) | Distribution model: PyInstaller --onedir + Inno Setup, unsigned with documented SmartScreen workaround | Accepted |
| [0038](0038-phase-5-is-closed-and-phase-6-may-open.md) | Phase 5 is closed and Phase 6 may open | Accepted |
| [0039](0039-stage-session-and-bench-section-are-distinct.md) | Stage, session, and Bench section are distinct | Accepted |
| [0040](0040-bench-bulk-orchestration-uses-a-durable-job-ledger.md) | Bench bulk orchestration uses a durable job ledger | Accepted |
| [0041](0041-bench-display-names-are-presentation-metadata.md) | Bench display names are presentation metadata | Accepted |
| [0042](0042-live-and-micro-live-eligibility-is-locked-and-evidence-based.md) | Live and micro-live eligibility is locked and evidence-based | Accepted |
| [0043](0043-bench-demotion-actions-open-a-governance-flow.md) | Bench demotion actions open a governance flow | Accepted |
| [0044](0044-kanban-uses-cached-hover-validation-and-authoritative-drop-validation.md) | Kanban uses cached hover validation and authoritative drop validation | Superseded |
| [0045](0045-kanban-responsive-layout-uses-horizontal-board-scroll.md) | Kanban responsive layout uses horizontal board scroll | Superseded |
| [0046](0046-bench-stage-hues-extend-production-tokens.md) | Bench stage hues extend production tokens | Accepted |
| [0047](0047-bench-action-availability-is-the-validation-surface.md) | Bench action availability is the validation surface | Accepted |
| [0048](0048-bench-uses-vertical-stage-sections-with-natural-scroll.md) | Bench uses vertical stage sections with natural scroll | Accepted |
| [0049](0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) | Phase 6 Bench v1 is a visual prototype with no backend mutation | Accepted (amended in part by [ADR 0051](0051-bench-command-infrastructure-v1.md)) |
| [0050](0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md) | Strategy evidence has a freshness axis distinct from promotion stage | Accepted |
| [0051](0051-bench-command-infrastructure-v1.md) | Bench Command Infrastructure v1 — propose / submit lifecycle for the launch paper-lifecycle | Accepted |
| [0052](0052-promotion-policy-is-a-typed-governance-source-of-truth.md) | Promotion policy is a typed governance source of truth | Accepted |
| [0053](0053-backtest-equity-snapshots-distinct-table.md) | Backtest equity snapshots are a distinct table from broker portfolio snapshots | Accepted |
| [0054](0054-risk-profiles-bounded-operator-preferences.md) | Risk profiles are bounded operator preferences | Accepted |
| [0055](0055-event-store-per-strategy-position-ledger.md) | Event-store per-strategy position ledger for concurrent runners | Accepted |
| [0056](0056-cross-process-submit-serialization-per-account-advisory-lock.md) | Cross-process submit serialization uses a per-account advisory lock | Accepted |
| [0057](0057-daily-execution-queue-at-open.md) | Daily execution resolves via queue-at-open (D-1) | Accepted |
| [0058](0058-lifecycle-exemption-is-scoped-and-operator-override-is-split.md) | Lifecycle exemption is scoped; operator override is split (D-4) | Accepted |

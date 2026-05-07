# ADR 0032 — Audit-Trail Backfill Policy

**Status:** Accepted  
**Date:** 2026-05-07  
**Supersedes:** —  
**Related:** ADR 0015 (frozen-manifest discipline), ADR 0029 (per-strategy position attribution)

---

## Context

A forensic audit of `data/milodex.db` on 2026-05-07 revealed a gap in the
promotion history for `meanrev.daily.pullback_rsi2.curated_largecap.v1`:

| recorded_at | from_stage | to_stage | approved_by |
|---|---|---|---|
| 2026-04-22T16:44:14 | backtest | paper | owner |
| 2026-04-22T16:45:39 | paper | micro_live | owner |
| 2026-05-07T13:34:42 | backtest | paper | Zack Meacham |

The 2026-04-22 promotions used backtest run `2ccea042` (Sharpe 1.022, 1057
trades) on the pre-Phase-4 universe (no survivorship-correction, no
dividend-adjustment). During Phase 4 close-out the corrected baseline
(survivorship-corrected `curated_largecap.v2` + dividend-adjusted bars)
showed the honest Sharpe to be 0.732 — a ~28% deflation.

At some point between the 4/22 micro_live promotion and the 5/7
backtest→paper re-promotion, the YAML's `stage:` line was direct-edited back
to `backtest` rather than using `milodex promotion demote`. No demotion event
was recorded in the `promotions` table, leaving a stage-divergence gap:
the YAML and the DB disagreed, and the promotion history had no record of the
reversal.

## Decision

When a **stage divergence** is discovered between a strategy's YAML `stage:`
field and the `promotions` table, the canonical resolution is:

1. Insert a **synthetic demotion event** via a one-off script in `scripts/`.
2. The event must carry `approved_by='audit_backfill'` — explicitly not a
   real operator name, making the synthetic nature machine-detectable.
3. The `recorded_at` timestamp is chosen as a plausible date within the gap
   window, not claimed to be the exact moment of operator decision.
4. The `notes` field must describe:
   - When the audit was performed.
   - What the original promotion evidence was.
   - Why the demotion was likely necessary.
   - That the YAML was direct-edited rather than demoted via CLI.
5. `reverses_event_id` must reference the id of the promotion event that the
   demotion logically reverses.
6. `backtest_run_id`, `sharpe_ratio`, `trade_count`, `max_drawdown_pct`,
   `evidence_json`, and `manifest_id` are all `NULL` — no evidence package
   is fabricated.
7. The script must be **idempotent**: running it twice must not insert two rows.
   Guard on `WHERE approved_by='audit_backfill' AND strategy_id=?`.
8. The script must ship with a test that:
   - Seeds the original promotion events.
   - Confirms exactly one row is inserted on the first call.
   - Confirms no row is added on the second call.

The `approved_by='audit_backfill'` sentinel is the load-bearing semantic
distinction. Any downstream query, report, or analytics code that reasons
about "real" promotions may filter this value out. Any audit query that
specifically looks for gaps should surface it.

## Rationale

Making the audit trail honest about its own gaps is preferable to leaving
the divergence silent. A synthetic event with an explicit `audit_backfill`
marker is unambiguous about provenance and does not claim to reconstruct
operator intent. Leaving no record would be worse: downstream tools would
continue to see a stage jump from `micro_live` to `backtest` (implicit, via
the 5/7 re-promotion) with no explanatory event.

This policy is consistent with:

- **ADR 0015** (frozen-manifest discipline): the manifest for the original
  micro_live promotion stands as-is; we do not fabricate a demotion manifest.
- **ADR 0029** (per-strategy position attribution): attribution records remain
  tied to real trades; backfill events carry no trade evidence.

## Consequences

- The `promotions` table may contain rows with `approved_by='audit_backfill'`.
  Analytics and reporting code should treat these as synthetic housekeeping
  events, not real operator decisions.
- The policy creates a documented, testable, and auditable pattern for future
  stage-divergence discoveries.
- Direct YAML edits that change `stage:` remain a prohibited shortcut; this
  ADR defines the remediation path, not an endorsement of the underlying error.

## Application

The specific backfill for `meanrev.daily.pullback_rsi2.curated_largecap.v1`
is implemented in `scripts/backfill_pullback_rsi2_audit_gap.py` and covered
by `tests/milodex/scripts/test_backfill_pullback_rsi2_audit_gap.py`.

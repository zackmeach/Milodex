# ADR 0017 — Data Source Hierarchy, Adjustment Policy, and Disagreement Handling

**Status:** Accepted (forward-looking — implementation deferred to Phase 1.2+ data work)
**Date:** 2026-04-21

## Context

Phase 1 needs three distinct data jobs done well and kept separate in the abstraction layer:

1. **Canonical research data** — historical OHLCV bars, reference metadata (active / delisted / exchange), split and dividend events, corporate-action-aware history. This is the data that earns promotion evidence. It must be deep, correctable, and provider-agnostic over time.
2. **Execution-adjacent market data** — the quotes and bars the broker itself sees, used for previews and live/paper runtime monitoring so that operator-facing prices match what the broker uses to evaluate orders.
3. **Broker state of record** — orders, fills, positions, order-state transitions. This is the ground truth for "did the trade happen."

Today the repo has only an Alpaca data provider (R-DAT-002) and Yahoo Finance mentioned informally. That is fine for Phase 1.1 but insufficient for Phase 1.2+ research. Alpaca's IEX feed covers ~2.5% of market volume and was never intended as a canonical research dataset; Alpaca's corporate-actions endpoint carries an explicit delay warning. Using either as the source of truth for research evidence would silently contaminate the promotion log.

There are also two adjacent questions that belong together with the data-source decision:

- **Adjustment policy.** Storing only split-adjusted bars loses raw history; storing only raw bars makes indicator math painful. The decision about which is canonical affects reproducibility.
- **Provider-vs-broker disagreement.** When the research layer and the broker layer disagree at trade time, one of them is wrong — or both are right about different things. A silent "pick one" would defeat the whole audit model.

This ADR resolves all three together because they are the same design problem seen from three angles.

## Decision

### Provider roles and priority

Milodex uses a role-based data stack. Roles are what the SRS talks about; specific providers are pinned here and here only.

| Role | Provider (Phase 1 selection) | What it authoritatively serves |
|---|---|---|
| **Canonical research data provider** | **Massive** | Historical OHLCV, universe reference metadata (active status, listing metadata, primary exchange), splits, dividends, ticker events, delistings. This is the data backtests, promotion reviews, and research analytics read from. |
| **Execution-adjacent market data feed** | **Alpaca SIP feed** | Previews, live/paper runtime monitoring, quotes and bars used for operator-facing pricing at submit time. Covers all U.S. exchanges (unlike IEX). |
| **Broker state of record** | **Alpaca Trading API / order streams** | Orders, fills, positions, order-state transitions. Ground truth for execution reality. |
| **Fallback market-data feed** | **Alpaca IEX feed** | Lower-fidelity local-development and degraded-operation fallback only. Explicitly **not** usable as the canonical research dataset. |

**Alpaca's corporate-actions endpoint** is supplementary, not authoritative, due to the documented processing delay. Canonical corporate-action state comes from the research provider.

### Adjustment policy

- **Raw unadjusted bars are the canonical preserved record.** Stored on disk, never rewritten by later adjustments. A bar written on day T will still exist byte-identically one year later.
- **Split-adjusted research views are computed from raw bars + split events.** Used for signal computation, chart continuity, and indicator math. Can be rebuilt at any time from the preserved raw record.
- **Dividends are cash events**, not silent price rewrites. They flow into P&L and total-return accounting as discrete events; they do not mutate the signal series into a total-return proxy.

This means every strategy that uses split-adjusted closes is operating on a derived view whose inputs (raw bars + split events) are themselves preserved and versioned.

### Broker-vs-research disagreement

A **split source-of-truth** model applies and extends ADR 0010:

- **Broker wins for execution reality** — order status, fills, open positions, submission-time tradability.
- **Canonical research provider wins for historical analytics** — backtests, research bars, reference metadata, corporate-action-aware history.
- **At trade time, disagreements do not silently resolve.** If the preview price, tradability, or session state from the research / market-data layer materially disagrees with the broker-adjacent view beyond a configurable tolerance, Milodex blocks submission and surfaces both values for operator review. "Block then ask" beats "pick silently" every time.

Concrete defaults for the tolerance (subject to tuning in `configs/risk_defaults.yaml`): price disagreement > 50 bps, tradability flag mismatch, market-session state mismatch.

## Rationale

- **Role-based stack keeps the SRS vendor-neutral.** Requirements talk about "the canonical research data provider" and "the broker state of record." Swapping Massive for a different provider in Phase 2 becomes an ADR-level decision, not a code-and-spec rewrite. The `DataProvider` ABC (ADR 0006) already provides the seam.
- **Massive fits the research role.** Its stock aggregates expose OHLCV + VWAP on custom intervals including daily; its reference endpoints expose active status, primary exchange, listing metadata, and delisted timestamps; its split, dividend, and ticker-event endpoints cover the corporate-action surface the research loop needs. This is the specific capability gap Alpaca's data feed does not fill.
- **Alpaca SIP for execution-adjacent.** When the broker evaluates an order, its view is what matters. SIP covers all exchanges; IEX alone is not representative. Keeping SIP in the "execution-adjacent" role (not the "research" role) preserves the honesty boundary.
- **Raw bars as canonical is the only reproducible choice.** Adjustments are views over events. If you store adjusted bars, every future split mutates your history retroactively and your year-old backtest is no longer reproducible byte-for-byte. Storing raw + computing adjusted lets the same manifest hash (ADR 0015) produce the same backtest a year later.
- **Disagreement-blocks-submission preserves the audit model.** The whole point of the explainability contract (R-XC-008) is that decisions are reconstructable. If the system silently picked a price when two sources disagreed, the reconstruction would be a lie.

## Consequences

- SRS Data domain gains requirements covering field completeness (R-DAT-007), adjustment policy (R-DAT-008), corporate-action handling (R-DAT-009), cache invalidation (R-DAT-010), backtest eligibility thresholds (R-DAT-011), automated data-quality checks (R-DAT-012), staleness policies per flow (R-DAT-013 / R-DAT-014 / R-DAT-015), and universe-manifest handling (R-DAT-016). None of these name Massive; all talk about roles.
- SRS Cross-Cutting gains R-XC-010 for provider-vs-broker disagreement.
- A `MassiveDataProvider` implementing the `DataProvider` ABC is added in Phase 1.2+. The existing `AlpacaDataProvider` is repurposed to fulfill the "execution-adjacent" and "fallback" roles; it is no longer the canonical research provider.
- The universe manifest (`configs/universe_phase1_v1.yaml`) sources its active-status and listing metadata from the canonical research provider, not from Alpaca. That shifts one concrete responsibility across providers in Phase 1.2 work.
- The Phase 1.2 roadmap entry in `docs/VISION.md` picks up one additional dependency: integrating the research-provider client before the first strategy produces promotion evidence.
- `.env.example` eventually grows a `MASSIVE_API_KEY` entry; secrets handling (R-XC-001) covers it without change.
- Credentials, rate limiting, and error handling for the research provider are implementation concerns handled at the provider-adapter level; no SRS change required.

## Links

- Supersedes: none (extends [0010](0010-hybrid-source-of-truth.md) for the disagreement semantics)
- Depends on: [0006](0006-abc-pattern-for-external-integrations.md), [0010](0010-hybrid-source-of-truth.md)
- Related: [0002](0002-parquet-as-cache.md), [0016](0016-phase1-instrument-whitelist.md)

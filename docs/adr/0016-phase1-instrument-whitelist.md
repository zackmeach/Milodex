# ADR 0016 — Phase 1 Instrument Whitelist

**Status:** Accepted
**Date:** 2026-04-21

## Context

Alpaca supports a broad set of asset classes (common stocks, ETFs, crypto, options). Massive exposes reference metadata across an even wider range. Phase 1's job is to prove the Milodex lifecycle on the simplest reliable instrument set — not to stress-test the platform against every available product. Without a hard whitelist, it is easy to quietly extend scope during research (someone backtests an option strategy "just to see") and discover later that corporate-action handling, assignment logic, settlement semantics, margin math, or data adjustments were never designed.

## Decision

Phase 1 trades **long-only U.S.-listed common stocks and plain-vanilla ETFs**. Everything else is out of scope for the entire phase.

**Allowed:**
- U.S.-listed common stocks (NYSE, NASDAQ, NYSE-American)
- Plain-vanilla ETFs tracking broad indices, sectors, or single commodities / bond maturities
- Long positions only

**Explicitly forbidden in Phase 1:**
- Options (single-leg and multi-leg)
- Futures
- Cryptocurrencies
- Forex
- OTC / pink-sheet securities
- Leveraged ETFs (2x, 3x, -1x, -3x, etc.)
- Inverse ETFs
- Volatility ETPs (VXX, UVXY, SVXY, etc.)
- Short selling
- Margin-dependent strategies (strategies whose sizing assumes margin beyond Reg T cash)
- Multi-leg products (spreads, paired trades as a single unit)

## Rationale

- **Clean data layer.** Common stocks and plain ETFs have the best-documented corporate-action behavior, the most reliable reference metadata, and the fewest silent vendor-specific quirks. Leveraged and inverse ETFs compound daily and require different return-math assumptions; volatility ETPs have decay behavior that breaks naive backtesting.
- **Clean risk layer.** Long-only, no margin, no shorting means the risk evaluator never has to reason about borrow availability, pattern-day-trader rules for shorts, margin calls, or assignment. Every check in R-EXE-004 is simpler when there is no short side.
- **Clean execution layer.** Market orders on liquid equities and ETFs settle T+1 (T+2 pre-2024) with no exercise, assignment, or rolling. Fill behavior is well-documented and comparable across venues.
- **Evidence portability.** The research loop produces evidence that is interpretable by anyone familiar with U.S. equity markets. Expanding into options or crypto requires a new body of domain knowledge for evidence review, and Phase 1 should not be paying that cost yet.
- **This is a ceiling, not a target.** The whitelist narrows Phase 1 below what brokers and data vendors support on purpose. Widening is a Phase 2+ decision with its own ADR.

## Consequences

- The config validator refuses any strategy instance whose declared universe contains an instrument outside the whitelist. Enforcement point is the universe manifest load, not the strategy runtime, so the error is caught before any data fetch.
- Short-side fields are rejected: `side: sell_short`, margin-enabled order types, and options instrument identifiers all fail config validation with structured errors.
- The `meanrev` family's `long_only: true` invariant (per `docs/strategy-families.md`) is consistent with this ADR but independent — lifting the whitelist would still leave the family invariant in place.
- The Phase 1 universe manifest (`configs/universe_phase1_v1.yaml`) is internally consistent with this whitelist. A future universe manifest that added, say, `UVXY` or `TQQQ` would fail validation and block the entire promotion pipeline until the ADR is superseded.
- Massive's and Alpaca's broader instrument coverage is not exercised in Phase 1. This is intentional; integration work for those surfaces is deferred to Phase 2+.

## Links

- Supersedes: none
- Related: [0001](0001-alpaca-as-broker.md), [0004](0004-paper-only-phase-one.md), [0008](0008-risk-layer-veto-architecture.md), [0017](0017-data-source-hierarchy.md)

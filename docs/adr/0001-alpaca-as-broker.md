# ADR 0001 — Alpaca as Sole Phase-One Broker

**Status:** Accepted
**Date:** 2026-04-16

## Context

Milodex needs a brokerage to execute trades. Phase one targets US equities/ETFs, daily swing tempo, and under $1,000 of operator capital. The operator is a solo developer working on a personal project; the broker will provide both market data and order execution during phase one.

## Decision

Alpaca is the sole broker for phase one, for both paper and (eventual) live trading. The broker integration is accessed exclusively through the `BrokerClient` abstract interface; no code outside `src/milodex/broker/` imports `alpaca-py`.

## Rationale

- **Commission-free trading** on US equities/ETFs aligns with sub-$1k capital where fees would otherwise dominate returns.
- **Paper trading is first-class**, not an afterthought — a separate base URL with the same API surface. This makes R-EXE-007 (paper-only enforcement) straightforward.
- **Official Python SDK (`alpaca-py`)** is well-maintained and covers orders, positions, account, market clock, and historical bars — the full phase-one surface in one dependency.
- **Same provider for data and execution** removes a category of reconciliation bugs where latest-bar timestamp and submission venue disagree.
- **Phase-one scope is narrow enough** that optionality across brokers has no immediate payoff. The ABC (see ADR 0006) preserves swap capability for later without the cost of implementing it now.

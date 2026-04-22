# ADR 0006 — Abstract Base Classes for External Integrations

**Status:** Accepted
**Date:** 2026-04-16

## Context

Milodex integrates with external systems — today, Alpaca for both market data and execution — and will plausibly integrate with others later (Yahoo Finance for data, alternative brokers, sentiment providers). The rest of the system should not care which vendor is behind the interface, and tests should be runnable without network access or vendor credentials.

## Decision

Each external integration point is defined by a Python `abc.ABC` subclass. Concrete vendor implementations live alongside the ABC but are instantiated through factories or DI at the edge of the system (typically in CLI command setup). Currently:

- `DataProvider` → `AlpacaDataProvider`
- `BrokerClient` → `AlpacaBrokerClient`

Return types are Milodex-defined domain models (`Bar`, `BarSet`, order/position types), never raw vendor SDK objects.

## Rationale

- **Testability without credentials.** A `FakeBrokerClient` that implements the ABC can drive end-to-end tests in CI or on a machine without a `.env` file, which unlocks honest coverage of risk and execution logic.
- **Vendor churn insulation.** When `alpaca-py` bumps major versions or its models change, the blast radius is one file — the Alpaca implementation — rather than every caller in the codebase.
- **Enforces "no leakage."** A returned `alpaca.orders.Order` creeping into strategy code would silently couple the project to a vendor SDK. Typed Milodex return types plus code-review convention make such leaks visible.
- **One responsibility per ABC.** `DataProvider` and `BrokerClient` are split (not one `BrokerAPI` god-object) so a future Yahoo Finance integration can satisfy `DataProvider` alone without having to stub order submission.
- **Aligns with R-BRK-003, R-BRK-006, R-DAT-001.** The ABCs are the contract those requirements cite.

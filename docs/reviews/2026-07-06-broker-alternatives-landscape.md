# Broker Alternatives — Landscape & Abstraction Assessment

**Date:** 2026-07-06
**Status:** Reference / decision doc (no action required; one optional pre-positioning PR proposed)
**Scope:** Should Milodex reduce its Alpaca dependence, and if so, toward whom and how?
**Method:** 17-agent Opus fan-out (8 web-sourced candidate profiles → 8 adversarial verification passes → 1 synthesis), findings re-grounded against the actual `broker/` and `data/` code. Provenance and confidence at the end.

---

## TL;DR

**Stay on Alpaca.** It is the *only* candidate in the cohort that satisfies the entire `BrokerClient` / `DataProvider` contract out of the box — `client_order_id` idempotency, `cancel_all_orders` for the kill switch, a real trading-calendar endpoint, **API fractional shares**, **real-data paper trading**, and both daily and 5Min history. Every alternative loses at least one of those, and most lose two.

The one genuine Alpaca weakness — the free feed is IEX-only, which is why research verdicts are treated as non-durable (ADR 0017) — **is a data-vendor tier, not a broker defect.** It is fixed by a `$99/mo` SIP toggle (Alpaca "Algo Trader Plus") that keeps the entire integration, the paper pipeline, and the crypto path intact. Switching brokers to fix data quality is a category error: you take on the hardest port in the list to solve something a config change already solves.

**The only worthwhile move now** is a small seam-hardening PR that makes any *future* adapter a drop-in. Detail in [§5](#5-the-abstraction-plan-the-only-actionable-part). Abstract further only when a concrete trigger fires — not preemptively.

> **The premise was half-right.** The instinct that the project is "quite Alpaca-dependent" is understandable but the coupling is already well-contained. The dependency is not the risk; the *hardcoded default* at three call sites is the only real leak, and it's cheap to close.

---

## 1. The actual coupling (grounded in code)

The `/caveman` premise was that Milodex is dangerously Alpaca-coupled. The code says otherwise — the abstraction is mostly already there:

- **Clean, broker-neutral ABCs exist.** `BrokerClient` ([broker/client.py:24](../../src/milodex/broker/client.py)) and `DataProvider` ([data/provider.py:16](../../src/milodex/data/provider.py)) are real abstract interfaces. `broker/models.py:5` explicitly enforces *"No Alpaca-specific types leak past this boundary"* and holds neutral `Order` / `Position` / `AccountInfo` dataclasses and `OrderType` / `TimeInForce` enums.
- **Alpaca is quarantined to ~1,200 lines across 3 files:** `broker/alpaca_client.py` (446), `data/alpaca_provider.py` (552), `core/_alpaca_retry.py` (136). Each is marked *"the ONLY file in the … layer that imports alpaca-py."* One dependency: `alpaca-py>=0.43.2,<1`.
- **A factory seam already partly exists.** `CommandContext.broker_factory` / `data_provider_factory` ([cli/_shared.py:66](../../src/milodex/cli/_shared.py)) are injectable `Callable`s, wired through `cli/main.py:132`, `commands/bench.py`, `gui/app.py`, and every `cli/commands/*.py`. The risk / execution / promotion stack above the ABC is broker-agnostic.

**Three real weaknesses remain** (these are the abstraction gaps, not the interfaces):

1. The factory is typed to the **concrete** `Callable[[], AlpacaBrokerClient]`, not the `BrokerClient` ABC — so a second concrete can't be injected without a type change.
2. Credentials are **un-injectable**: `AlpacaBrokerClient.__init__` takes zero args and reads the hardcoded `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` env names internally ([config.py:52](../../src/milodex/config.py)). A second broker can't reuse the constructor shape.
3. **One direct-instantiation leak** bypasses the factory entirely: `walk_forward_batch.py:370` calls `AlpacaDataProvider()` directly.

**Baked-in equity assumptions in the ABC:** `latest_completed_session` (a market-calendar primitive; safe-defaults to `None`), `cancel_all_orders` (kill switch needs it), `client_order_id` (crash-reconcile idempotency), `is_symbol_tradable`. Any candidate is scored against *these*, not a generic feature list.

---

## 2. Verdict & fit-ranking

Fit-scored **for Milodex specifically** (US equities/ETFs, <$1k fractional sizing, paper-first pipeline, cheap data preferred, Python/Windows, future crypto). Not a generic "best broker" ranking.

| # | Candidate | Fit | One-line |
|---|-----------|-----|----------|
| 1 | **Alpaca** (incumbent, stay) | **92** | Only candidate satisfying every ABC out of the box; sole gap is IEX free data, fixable with $99/mo SIP without switching. |
| 2 | **Alpaca crypto** (24/7 canary lane) | **88** | Near-zero incremental swap cost — same SDK/seams, free full-quality crypto data, native fractional; needs only a `/`-symbol cache key + crypto order-type mapping. |
| 3 | **Interactive Brokers** | **55** | Only alternative that fixes data quality (real SIP ~$14.50/mo) *and* keeps fractional + $0 min — but retail must babysit a stateful local gateway (no headless OAuth). Real tax on an unattended bot. |
| 4 | Coinbase Advanced Trade (crypto) | 48 | Strongest crypto-native standalone (official SDK, `client_order_id`, cancel-all, deep candles) but ~2.4–8× Alpaca's fees and zero seam reuse. |
| 5 | Binance.US (crypto) | 38 | Near-zero fees (0%/0.02%) but no first-party US Python SDK, KYC-gated keys, state-availability gaps. |
| 6 | Tastytrade | 30 | Free DXFeed data + mature SDK, but fractional unsupported for automation and sandbox wipes positions every 24h — breaks <$1k sizing *and* the multi-day paper stage. |
| 7 | Kraken (crypto) | 28 | Capable live API, but the 720-candle REST OHLC cap makes it unusable as a clean backtest `DataProvider`. |
| 8 | Charles Schwab | 18 | Great free real-time data, but **no API paper trading** and **no API fractional** — two independent deal-breakers. |
| 9 | Tradier | 18 | Clean REST + real sandbox, but whole-share-only API, ~18–40d intraday history cap, no crypto. |
| 10 | TradeStation | 15 | Competent v3 API but no fractional, spot crypto killed 2024, no `client_order_id`/cancel-all/calendar, $10/mo inactivity fee. |
| 11 | Frameworks (lumibot/LEAN/backtrader/PyBroker) | 8 | Inversion-of-control frameworks that own the strategy loop — adopting one **demotes the sacred risk layer** and single execution chokepoint. Architecturally incompatible. |
| 12 | Robinhood / Wealthsimple | 3 | No official equities API (Robinhood crypto-only; Wealthsimple ToS-prohibited, Canada-only). Not implementable. |

---

## 3. Capability matrix (equity candidates vs the ABC surface)

| Capability (ABC requirement) | Alpaca | IBKR | Schwab | Tradier | TradeStation | Tastytrade |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| **API paper trading** | ✅ real-time | ✅ (needs funded live first) | ❌ **live-only** | ✅ (15-min delayed) | ✅ SIM | ⚠️ 24h wipe, delayed |
| **Fractional via API** (<$1k sizing) | ✅ notional | ✅ best ($0.01) | ❌ **web/mobile only** | ❌ likely whole-share | ❌ | ❌ automation |
| Data vs Alpaca-IEX free | baseline; SIP $99/mo | ✅ SIP ~$14.50/mo | ✅ **free real-time** | ✅ if funded | ⚠️ paid | ✅ free DXFeed |
| `client_order_id` (idempotency) | ✅ | ✅ cOID | ❌ | ⚠️ unclear | ❌ | ❌ |
| `cancel_all_orders` (kill switch) | ✅ | ✅ reqGlobalCancel | ❌ by-id only | ✅ | ❌ | ⚠️ unclear |
| Market calendar (`latest_completed_session`) | ✅ | ✅ per-contract | ✅ | ✅ | ❌ | ⚠️ unclear |
| 5Min historical depth | ✅ deep | ✅ deep | ✅ ~9mo | ❌ **~18d** | ✅ | ⚠️ streaming-collect |
| Crypto (roadmap) | ✅ | ⚠️ separate path, 11 coins | ❌ | ❌ | ❌ (spot killed 2024) | ✅ |
| Unattended-friendly (headless) | ✅ key+REST | ❌ **stateful gateway** | ⚠️ 7-day re-auth | ✅ | ⚠️ | ✅ |
| **Swap cost for Milodex** | n/a (built) | **high** | high (wasted) | medium | medium | medium |

✅ satisfies · ⚠️ partial/uncertain · ❌ missing or disqualifying

---

## 4. Per-candidate notes

### Interactive Brokers (IBKR) — the only serious equity alternative
The deepest, cheapest-data, most-capable broker on the list: real SIP-quality consolidated US data for ~$14.50/mo streaming (vs Alpaca's IEX-only free tier), best-in-class fractional shares ($0.01 min, ~22,825 symbols, verified against IBKR's own page), $0 account minimum, $0-commission US stock/ETF on IBKR Lite, and native crypto. Every ABC method maps cleanly: `cOID` → `client_order_id`, `reqGlobalCancel` → `cancel_all_orders`, `/trsrv/secdef/schedule` → calendar, `reqHistoricalData` → daily+5Min, native stop orders.

**The load-bearing con survived adversarial verification against IBKR's own wording:** retail/individual clients are **not** approved for OAuth 2.0 headless auth. You must run a stateful Java TWS / IB Gateway with a periodic manual session login and a ~daily forced restart (IBC / docker watchdog required). For an explicitly *unattended* bot that is a real, ongoing operational tax Alpaca does not impose — plus async-socket paradigm, monotonic `orderId` lifecycle, and pacing-limit plumbing (50 msg/s, 50 concurrent historical requests, 60/10min). **Highest swap cost of any candidate.** Crypto is a separate legal entity / API path (11 US coins, not "~20" as first reported), so the canary would be a distinct integration, not a symbol swap.

*Do not switch for data quality alone — SIP-on-Alpaca is cheaper and keeps everything. IBKR is the destination only if you ever outgrow Alpaca on execution quality/breadth, or want fractional + real data and refuse to pay Alpaca SIP.* Fast-moving: IBKR has stated OAuth-2.0-for-individuals is "under consideration, no ETA" — that would remove the main blocker; re-check before committing.
Sources: [IBKR OAuth docs](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/), [market-data subscriptions](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/), [fractional](https://www.interactivebrokers.com/en/trading/fractional-trading.php), [ib_async](https://github.com/ib-api-reloaded/ib_async).

### Charles Schwab (post-TD Ameritrade API) — two independent deal-breakers
A real, free, retail-accessible API with genuinely better data than Alpaca's free tier (real-time SIP-grade quotes, ~15yr daily / ~9mo 5Min, all included with a funded account — its one clear win). But: **no API paper trading** (Schwab support confirmed live-only; the "sandbox" only validates OAuth/endpoint shapes, no fill simulation) and **no fractional shares via API** (Stock Slices are web/mobile only). Each independently disqualifies a <$1k paper-first pipeline. Also: no `client_order_id`, no cancel-all, and a hard **7-day OAuth refresh-token expiry** forcing a weekly interactive browser re-login. Verifier confirmed all deal-breakers against 2+ independent sources; **not worth swapping.**
Sources: [Schwab Trader API](https://developer.schwab.com/products/trader-api--individual), [OAuth token doc](https://developer.schwab.com/user-guides/apis-and-apps/oauth-restart-vs-refresh-token), [schwab-py](https://github.com/alexgolec/schwab-py).

### Tradier — fails sizing, backtest-data, and roadmap at once
Options-first, developer-friendly, clean REST + WebSocket, genuine free sandbox, $0 minimum, cheap flat pricing. But two hard blockers for this project: **intraday history is catastrophically shallow (5Min ≈ 18 days, 1Min ≈ 10 days)** — cannot feed a walk-forward backtester needing months of 5Min — and fractional via API is unconfirmed/most-likely-absent (whole-share sizing floors to 0 on high-priced names). No crypto. Not a clear upgrade over Alpaca.
Sources: [docs.tradier.com](https://docs.tradier.com), [pricing](https://tradier.com/individuals/pricing).

### TradeStation — competent peer, wrong fit
Mature v3 REST + streaming, true SIM/paper, $0 equity commissions. Killed by: **no fractional shares** (fatal <$1k), **spot crypto shut down Feb 2024** (kills the canary), plus paid real-time equities data, a **$10/mo inactivity fee** a small idle account trips, human-email API-key approval, and no official Python SDK, no `client_order_id`/cancel-all/calendar. Swap cost not justified.

### Tastytrade — closest "modern data" contender, still blocked
Official self-service Open API (OAuth personal grants, no gate), mature `tastyware/tastytrade` async SDK, one account across equities/options/futures/crypto, free real-time DXFeed data (a genuine data edge over Alpaca IEX). Blocked by three project-specific problems: **fractional not supported for automated trading**, **no `client_order_id`** (breaks the crash-reconcile contract), and a **poor sandbox** (15-min delayed, 24h position wipe — no multi-day strategy validation). Historical bars are adequate but arrive as a slow streaming-collect, not a clean REST batch.
Source: [tastytrade Open API](https://developer.tastytrade.com).

### Near-peer challengers (Public / Webull / Moomoo / Robinhood / Wealthsimple)
Only **Public.com** is a plausible developer-first drop-in, but it has **no documented paper/sandbox** and **no confirmed calendar endpoint** — two hard blockers for the pipeline and the calendar-gated daily runner. Robinhood (no official equities API — crypto-only), Webull (OpenAPI exists but heavier onboarding), Moomoo/Futu (always-running local OpenD gateway — unattended friction), Wealthsimple (no official API, ToS-prohibited, Canada-only) are all worse fits.

### Multi-broker abstraction libraries — keep the hand-written ABC
Three groups: (1) **CCXT** — de-facto crypto multi-exchange lib, genuinely adapter-shaped and maps cleanly onto the ABCs, but **crypto-only** (relevant only to the future canary, not Phase 1). (2) **Strategy frameworks** (lumibot, QuantConnect LEAN, backtrader, PyBroker) — **inversion-of-control**: they own the strategy loop, sizing, and risk. Adopting one means abandoning Milodex's custom risk/execution/promotion stack and **demoting the sacred risk layer and single execution chokepoint** — a hard architectural violation, not a slot-in. (3) **Unified equity adapters** — none exist; only commercial hosted aggregators (SnapTrade, Plaid), the wrong shape for a self-hosted execution chokepoint. **Verdict: write per-broker adapters behind the existing ABC (alpaca-py today, ccxt for the crypto lane later). Do not adopt a framework.**

---

## 5. The abstraction plan (the only actionable part)

**Recommendation: do-nothing-until-triggered on the broker swap, plus one cheap pre-positioning refactor.**

There is no live pain a broker switch fixes. The sole real problem (IEX data durability) is a `$99/mo` Alpaca SIP toggle, not a re-platform. But the seam has three closable gaps ([§1](#1-the-actual-coupling-grounded-in-code)), and closing them is cheap and makes any eventual adapter a drop-in.

**Seam-hardening PR (decent — or split into 2 small):**

1. Retype `broker_factory` / `data_provider_factory` in `CommandContext` from concrete `AlpacaBrokerClient`/`AlpacaDataProvider` to the `BrokerClient`/`DataProvider` **ABCs**.
2. Add a real `build_broker(name) -> BrokerClient` factory and route the hardcoded defaults through it — **including `walk_forward_batch.py:370`, which currently bypasses the factory** and instantiates `AlpacaDataProvider()` directly (the one real leak).
3. **Generalize credentials:** make `AlpacaBrokerClient.__init__` accept injected creds (a `BrokerCredentials` struct) instead of reading `ALPACA_*` env names internally; add per-broker env-name resolution so a second broker's keys don't collide.
4. **Abstract the calendar:** make `latest_completed_session` / `is_market_open` pull from a pluggable market-calendar source (`exchange_calendars` for brokers with no calendar endpoint; an always-open adapter for crypto) rather than assuming a broker calendar endpoint.
5. **Add capability flags to `BrokerClient`** (`supports_cancel_all`, `supports_client_order_id`, `supports_fractional`, `supported_order_types`/TIF) so the kill switch and sizing/risk layers degrade gracefully for brokers missing one — several candidates miss at least one.

**Effort:** the factory/credential/capability/calendar refactor is a **decent PR** (the plumbing half already exists via `broker_factory` — it's mostly retyping, one credential-shape change, and routing 3 defaults). A second *concrete* IBKR adapter is **large** — not the method mapping (mechanical) but the surrounding gateway/session/pacing ops. An **Alpaca-crypto** adapter is instead **small** (reuses SDK + seams).

**Do NOT** adopt a multi-broker framework — it inverts control and demotes the risk layer. Write adapters behind the existing ABC only when a trigger fires. *This PR is a roadmap §10-style deferred internal refactor: worth doing when convenient, not a gate.*

---

## 6. Crypto path (the 24/7 canary lane)

**Alpaca's own crypto is the best venue by a wide margin.** Lowest swap cost (reuses `alpaca-py` + the existing seams), **free full-quality data** (the IEX problem is equities-only — no consolidated crypto tape exists to degrade), native decimal/notional sizing, first-class paper on the already-provisioned account, low 0.15%/0.25% fees, ~5–6yr history. It needs only:

- a filesystem-safe cache key for `/`-symbols (`ParquetCache._path` nests `BTC/` and fails — already flagged in project memory; broker-independent prerequisite),
- `fractional_units_for_notional_pct` instead of `shares_for_notional_pct`,
- a crypto order-type/TIF mapping (stop-limit + gtc/ioc only — no stop-market/day),
- an always-open calendar adapter.

If a crypto-native venue is ever needed for deeper books, **Coinbase Advanced Trade** is the strongest standalone (official SDK, `client_order_id`, cancel-all, paginated candles) but ~0.6–1.2% entry fees are punishing on a thin edge, and it's a net-new integration. **Avoid Kraken as a backtest source** (720-candle REST OHLC cap defeats a clean `get_bars`). **Avoid Binance.US** for a solo US dev (no first-party US Python SDK, KYC/state gating) despite near-zero fees.

---

## 7. The honest stay-on-Alpaca case

Alpaca is the only broker in the cohort that satisfies **every** ABC method natively — `client_order_id` idempotency, `cancel_all_orders` for the kill switch, a real `/v2/calendar` + `/v2/clock` for the session gate, fractional/notional sizing for <$1k capital, free real-data paper trading, and both 1D and 5Min history. Every alternative loses at least one; most lose two.

The one true Alpaca weakness — IEX free-tier data making research verdicts non-durable — is **not a broker defect**; it's a data-vendor tier, fixed by a `$99/mo` SIP toggle that keeps the integration, the paper pipeline, and the crypto path intact (and lifts the rate limit 200→10,000/min). Switching brokers to fix data quality means taking on the hardest port in the list (IBKR's gateway ops, or a fractional/paper deal-breaker elsewhere) to solve something a config change already solves.

The codebase is already built against Alpaca at zero swap cost, and **the current bottleneck is execution + evidence, not broker capability** — a swap burns effort against a non-constraint.

---

## 8. Provenance & confidence

- **Produced by** a 17-agent Opus fan-out (2026-07-06): 8 web-sourced candidate profiles → 8 adversarial verification passes (each tasked to *refute* the profile) → 1 high-effort synthesis. ~1.49M tokens, 325 tool calls. Findings re-grounded against the live `broker/` and `data/` source; the `broker_factory` seam was verified directly in code.
- **High confidence:** the two structural non-negotiables that eliminate most candidates — *API fractional shares* and *real API paper trading* — and the IBKR "retail can't do headless OAuth / must babysit a gateway" blocker (confirmed against IBKR's own wording). Schwab's no-paper / no-fractional / 7-day-reauth trio confirmed by 2+ sources each.
- **Fast-moving — re-check before acting:** exact market-data dollar figures (IBKR ~$14.50/mo, Alpaca SIP $99/mo); IBKR OAuth-2.0-for-individuals ("no ETA" — would materially change its score); Schwab's stated intent to add fractional-API support; per-venue crypto fee tiers.
- **Not committed.** This is an untracked reference doc under `docs/reviews/`. (If committed, note the `scripts/audit_phase_state.py` gate classifies docs/ paths that mention phase strings.)

**Bottom line:** the dependency is contained, the seam is good, the swap is unmotivated. Buy SIP when a strategy nears capital-readiness; harden the seam when convenient; abstract to a second broker only when a concrete trigger fires.

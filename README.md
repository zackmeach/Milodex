# Milodex

**A personal, research-led autonomous trading system — designed around the hard problem, which is not placing trades but avoiding the trap of mistaking noise for signal.**

Milodex runs locally, connects to a brokerage via official APIs, and takes strategies from idea → backtest → paper → (eventually) live capital through a governed promotion pipeline. Every decision is logged with reasoning, every config that produced evidence is frozen and hashed, and every trade passes through a risk layer with veto power before it reaches the broker.

The name is a nod to Milo — a golden retriever — and the word "Index." Loyal, tireless, and always fetching returns.

> **Status:** Phase 1, paper-trading only. Live capital is gated behind a future ADR, not a feature flag. See [docs/VISION.md](docs/VISION.md) and [docs/ROADMAP_PHASE1.md](docs/ROADMAP_PHASE1.md).

---

## Why This Project Is Interesting

Most personal trading projects are a strategy script plus a broker SDK call. Milodex is built around the parts that are usually skipped:

- **Research integrity over cleverness.** The bar is not "can this system trade automatically?" — it's "can this system avoid convincing its operator that noise is skill?" That constraint shapes every design decision.
- **A risk layer with veto power.** Strategies propose, risk disposes. No code path reaches the broker without passing through a single execution chokepoint that invokes risk checks, records an explanation record, and stamps the decision into an append-only event store. ([ADR 0008](docs/adr/0008-risk-layer-veto-architecture.md))
- **Frozen, hashable config manifests.** Every backtest, paper run, and promotion decision references an immutable SHA-256-hashed manifest of the exact config that produced the evidence — not the mutable YAML on disk. Promotion is refused when the live YAML's hash doesn't match the reviewed manifest's hash. Config drift becomes detectable and blocking instead of silent. ([ADR 0015](docs/adr/0015-strategy-identifier-and-frozen-manifest.md))
- **A governed promotion pipeline.** `backtest → paper → micro_live → live`. No skipping stages. Thresholds (Sharpe, drawdown, minimum trade count) are enforced in code, not by operator memory. Live is hard-blocked in Phase 1 and requires a future ADR to unlock. ([ADR 0009](docs/adr/0009-promotion-pipeline-stage-model.md), [ADR 0020](docs/adr/0020-promotion-thresholds-are-code-invariants.md))
- **Kill switch with manual reset.** When tripped, trading halts. There is no auto-resume path. ([ADR 0005](docs/adr/0005-kill-switch-manual-reset.md))
- **An event-sourced SQLite store as the source of truth** for trades, explanations, promotion-log entries, strategy runs, and kill-switch events. Append-only, content-hash-keyed where idempotency matters. ([ADR 0011](docs/adr/0011-sqlite-event-store.md))
- **ADR-driven design.** 20 numbered Architecture Decision Records capture the "why" behind every consequential choice, from broker selection to durable state layout to why risk types live in the risk module. See the [ADR index](docs/adr/).

---

## Architecture at a Glance

```
                        ┌─────────────────────┐
                        │     CLI / Runner    │
                        └──────────┬──────────┘
                                   │ intent
                                   ▼
   ┌───────────────┐      ┌─────────────────┐      ┌──────────────┐
   │  strategies/  │─────▶│   execution/    │─────▶│   broker/    │
   │ (config-driven)│     │  (single choke- │      │   (Alpaca)   │
   └───────┬───────┘      │   point; risk,  │      └──────────────┘
           │              │   explanations, │
           │              │   manifest hash)│
           │              └────┬────────┬───┘
           ▼                   ▼        ▼
   ┌───────────────┐   ┌──────────────┐  ┌─────────────────────┐
   │ backtesting/  │   │    risk/     │  │      core/          │
   │ (walk-forward,│   │  (veto power,│  │ SQLite event store, │
   │  min 30 trades│   │   kill switch│  │ advisory locks,     │
   │  to conclude) │   │   daily caps)│  │ schema migrations   │
   └───────────────┘   └──────────────┘  └─────────────────────┘
```

Nine modules in `src/milodex/`: `broker`, `strategies`, `risk`, `execution`, `backtesting`, `data`, `analytics`, `core`, `cli`, plus `promotion/`. Separation of concerns is enforced by the rule that strategies never call the broker directly and the risk layer is never bypassed for convenience.

---

## By the Numbers

- **435 tests** covering strategies, risk, execution, promotion, event store, CLI, and end-to-end flows
- **20 ADRs** documenting the reasoning behind consequential architectural decisions
- **~60 source modules / ~60 test modules** in a `src/`-layout Python 3.11 package
- Ruff-linted, typed, CI-friendly

---

## Documentation

The `docs/` tree is deliberate, not decorative — the documentation *is* part of the design:

| Doc | What it's for |
|---|---|
| [VISION.md](docs/VISION.md) | Project vision, principles, Phase 1 scope, autonomy boundary |
| [FOUNDER_INTENT.md](docs/FOUNDER_INTENT.md) | Why the project exists and the spirit it's built in |
| [SRS.md](docs/SRS.md) | Software Requirements Spec: every R-*-### requirement the system must meet |
| [ROADMAP_PHASE1.md](docs/ROADMAP_PHASE1.md) | Phase 1 delivery plan and slice tracking |
| [RISK_POLICY.md](docs/RISK_POLICY.md) | Risk limits, kill-switch policy, loss caps |
| [PROMOTION_GOVERNANCE.md](docs/PROMOTION_GOVERNANCE.md) | What it takes to move a strategy from one stage to the next |
| [OPERATIONS.md](docs/OPERATIONS.md) | Daily schedule, startup/shutdown, degraded modes, concurrency model |
| [ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md) | Code style, testing, review standards |
| [CLI_UX.md](docs/CLI_UX.md) | CLI design principles and command surface |
| [adr/](docs/adr/) | 20 Architecture Decision Records |

---

## Quick Start

```powershell
git clone <repo-url>
cd Milodex
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

Copy-Item .env.example .env   # then fill in ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_MODE=paper
```

### Try it

```powershell
milodex status                                    # trading mode, market hours, account summary
milodex positions --sort market-value --limit 10
milodex orders --status open --verbose
milodex data bars SPY --timeframe 1d --start 2025-01-01 --end 2025-01-31
milodex config validate configs\sample_strategy.yaml
milodex config fingerprint configs\spy_shy_200dma_v1.yaml   # SHA-256 of canonical YAML

# Paper trading — every submit passes through the risk layer
milodex trade preview SPY --side buy --quantity 1 --order-type market
milodex trade submit  SPY --side buy --quantity 1 --order-type market --paper
milodex trade kill-switch status

# Strategies
milodex strategy list
milodex strategy run regime.daily.sma200_rotation.spy_shy.v1

# Promotion governance
milodex promotion history regime.daily.sma200_rotation.spy_shy.v1
milodex promote <strategy-id> --to paper --lifecycle-exempt --approved-by <you>
```

### Run the test suite

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

---

## Project Structure

```
src/milodex/
    broker/        Brokerage API integration (Alpaca) — every broker call goes through here
    strategies/    Config-driven strategy definitions + foreground runner
    risk/          Risk layer with veto power over all trades (never bypassed)
    execution/     Single chokepoint: intent → risk → explanation → broker
    backtesting/   Walk-forward engine; min 30 trades before statistical conclusions
    data/          Market data acquisition (Alpaca, Yahoo)
    analytics/     Performance metrics, reporting, benchmark (vs SPY)
    promotion/     Stage transitions, gates, drift detection, evidence packages
    core/          SQLite event store, advisory locks, schema migrations
    cli/           Command-line interface (Phase 1 primary surface)
configs/           Strategy + risk YAML (parameters live here, not in code)
docs/              Vision, SRS, ADRs, operations, governance
tests/             Mirrors src/ structure — 435 tests
```

---

## Tech Stack

Python 3.11 · SQLite (event store) · Alpaca (broker + market data) · Yahoo Finance (secondary data) · pandas / numpy · argparse · pytest · ruff. No runtime dependencies added without justification — the dependency list is itself a design decision.

---

## Phase 1 Constraints (by design)

- **Market:** US equities / ETFs only
- **Tempo:** Daily swing trades (1–5 day holds)
- **Broker:** Alpaca (paper only)
- **Capital:** Under $1,000 when live eventually unlocks
- **Edge families:** Momentum, mean reversion, breakout, regime
- **Two Phase 1 strategies, two purposes:** a lifecycle-proof regime strategy (SPY/SHY 200-DMA) to validate the platform end-to-end, and a mean-reversion research-target strategy as the first real edge hunt. They are promoted under separate rules — see [docs/SRS.md](docs/SRS.md).

---

## Author's Note

Milodex is a personal project, built evenings and weekends, used partly as a proving ground for disciplined AI-assisted software engineering in an unfamiliar domain. The research-system discipline, the ADR trail, and the risk-layer-as-sacred-architecture are deliberate — the interesting problem in autonomous trading is not execution, it's honesty with yourself about what you actually know.

# Milodex

**A personal, research-led autonomous trading system — designed around the hard problem, which is not placing trades but avoiding the trap of mistaking noise for signal.**

Milodex runs locally, connects to a brokerage via official APIs, and takes strategies from idea → backtest → paper → (eventually) live capital through a governed promotion pipeline. Every decision is logged with reasoning, every config that produced evidence is frozen and hashed, and every trade passes through a risk layer with veto power before it reaches the broker.

The name is a nod to Milo — a golden retriever — and the word "Index." Loyal, tireless, and always fetching returns.

> **Status:** Phases 1–5 closed; Phase 6 (operator surfaces / the Bench) in progress. Phase 1 closed 2026-05-04 (ADR 0023); Phases 2–5 closed via ADR 0025 / 0027 / 0031 / 0038. Live capital remains gated behind an ADR, not a feature flag. **For current state and the full doc map, start at [docs/README.md](docs/README.md);** active planning lives in [docs/PHASE6_BENCH_PREP.md](docs/PHASE6_BENCH_PREP.md), and the phase model is in [docs/VISION.md](docs/VISION.md).

---

## Why This Project Is Interesting

Most personal trading projects are a strategy script plus a broker SDK call. Milodex is built around the parts that are usually skipped:

- **Research integrity over cleverness.** The bar is not "can this system trade automatically?" — it's "can this system avoid convincing its operator that noise is skill?" That constraint shapes every design decision, including the trust report that *flags* a strategy as fragile when its OOS aggregate depends on a single lucky window.
- **A risk layer with veto power.** Strategies propose, risk disposes. No code path reaches the broker without passing through a single execution chokepoint that invokes risk checks, records an explanation record, and stamps the decision into an append-only event store. ([ADR 0008](docs/adr/0008-risk-layer-veto-architecture.md))
- **Frozen, hashable config manifests.** Every backtest, paper run, and promotion decision references an immutable SHA-256-hashed manifest of the exact config that produced the evidence — not the mutable YAML on disk. The runtime drift check refuses execution when the live YAML's hash doesn't match the frozen manifest's hash. Config drift becomes detectable and blocking instead of silent. ([ADR 0015](docs/adr/0015-strategy-identifier-and-frozen-manifest.md))
- **A governed promotion pipeline.** `backtest → paper → micro_live → live`. No skipping stages. Thresholds (Sharpe, drawdown, minimum trade count) are enforced in code, not by operator memory. Live is hard-blocked and requires a future ADR to unlock. ([ADR 0009](docs/adr/0009-promotion-pipeline-stage-model.md), [ADR 0020](docs/adr/0020-promotion-thresholds-are-code-invariants.md))
- **Walk-forward backtesting that is honest about fragility.** Walk-forward runs report OOS-aggregate metrics labeled as such, surface single-window dependency, and refuse to silently pass the promotion gate when the aggregate signal leans on one good year. ([ADR 0021](docs/adr/0021-walk-forward-metrics-are-oos-aggregate.md))
- **Kill switch with manual reset.** When tripped, trading halts. There is no auto-resume path. ([ADR 0005](docs/adr/0005-kill-switch-manual-reset.md))
- **An event-sourced SQLite store as the source of truth** for trades, explanations, promotion-log entries, strategy runs, kill-switch events, and frozen manifests. Append-only, content-hash-keyed where idempotency matters. ([ADR 0011](docs/adr/0011-sqlite-event-store.md))
- **A rich-terminal CLI that's the daily operator surface.** Color-coded by exposure (live red, paper yellow, backtest cyan), threshold-coded by promotion gate (Sharpe ≥ 0.5 green / 0–0.5 yellow / negative red, drawdown thresholded at 7.5% and 15%), with kill-switch banners that override everything else on screen. The `--json` machine contract sits next to it untouched, so the same command output drives operators or scripts. The CLI is what the GUI gate ([ENGINEERING_STANDARDS.md §"GUI Readiness Gate"](docs/ENGINEERING_STANDARDS.md)) requires before any GUI work begins.
- **ADR-driven design.** 54 numbered Architecture Decision Records capture the "why" behind every consequential choice, from broker selection to durable state layout to why risk types live in the risk module. See the [ADR index](docs/adr/).

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
   │  OOS-aggregate│   │   kill switch│  │ advisory locks,     │
   │  metrics)     │   │   daily caps)│  │ schema migrations   │
   └───────────────┘   └──────────────┘  └─────────────────────┘
```

Ten modules in `src/milodex/`: `broker`, `strategies`, `risk`, `execution`, `backtesting`, `data`, `analytics`, `promotion`, `core`, `cli`. Separation of concerns is enforced by the rule that strategies never call the broker directly and the risk layer is never bypassed for convenience.

---

## By the Numbers

- **54 ADRs** documenting the reasoning behind consequential architectural decisions (0001–0054)
- A **`src/`-layout Python 3.11 package** with a test suite mirroring the `src/` tree
- **90%+ test coverage** with a one-way ratchet (CI fails on regression below the floor)
- Ruff-linted, formatted, and clean

---

## Documentation

The `docs/` tree is deliberate, not decorative — the documentation *is* part of the design:

| Doc | What it's for |
|---|---|
| [docs/README.md](docs/README.md) | **The documentation map — start here.** Classifies every doc as living / frozen / closed-history and routes to the current set. |
| [VISION.md](docs/VISION.md) | Project vision, principles, phase model, autonomy boundary |
| [FOUNDER_INTENT.md](docs/FOUNDER_INTENT.md) | Why the project exists and the spirit it's built in |
| [SRS.md](docs/SRS.md) | Software Requirements Spec: every R-*-### requirement the system must meet |
| [ROADMAP_PHASE1.md](docs/ROADMAP_PHASE1.md) | Phase 1 delivery plan + success-criteria evidence — **closed/historical** (Phase 1 closed 2026-05-04, ADR 0023) |
| [RISK_POLICY.md](docs/RISK_POLICY.md) | Risk limits, kill-switch policy, loss caps |
| [PROMOTION_GOVERNANCE.md](docs/PROMOTION_GOVERNANCE.md) | What it takes to move a strategy from one stage to the next |
| [OPERATIONS.md](docs/OPERATIONS.md) | Daily schedule, startup/shutdown, degraded modes, concurrency model |
| [ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md) | Code style, testing, scaffolded-vs-implemented discipline, coverage ratchet |
| [CLI_UX.md](docs/CLI_UX.md) | CLI design principles and command surface |
| [REPORTING.md](docs/REPORTING.md) | Trust report contract, confidence labels, uncertainty surfacing |
| [adr/](docs/adr/) | Architecture Decision Records — the decision backbone and binding Document Authority Order (indexed in [adr/README.md](docs/adr/README.md)) |

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

### Daily operator surface (rich-rendered in a TTY)

```powershell
milodex status                                              # trading mode, market hours, account snapshot
milodex report                                              # trust dashboard — kill-switch + strategies + freshness
milodex report strategy regime.daily.sma200_rotation.spy_shy.v1   # per-strategy: stage, perf, confidence
milodex positions --sort market-value --limit 10
milodex orders --status open --verbose
milodex reconcile                                           # broker vs event store drift check
```

### Paper trading — every submit passes through the risk layer

```powershell
milodex trade preview SPY --side buy --quantity 1 --order-type market
milodex trade submit  SPY --side buy --quantity 1 --order-type market --paper
milodex trade kill-switch status
milodex trade kill-switch reset --confirm                   # required after a trip
```

### Strategies and backtesting

```powershell
milodex strategy run regime.daily.sma200_rotation.spy_shy.v1
milodex backtest meanrev.daily.pullback_rsi2.curated_largecap.v1 --start 2015-01-01 --end 2024-12-31 --walk-forward
milodex analytics metrics <run-id> --compare-spy            # threshold-colored vs SPY benchmark
milodex analytics list                                      # recent backtest runs
```

### Promotion governance

```powershell
milodex promotion freeze    regime.daily.sma200_rotation.spy_shy.v1
milodex promotion manifest  regime.daily.sma200_rotation.spy_shy.v1
milodex promotion history   meanrev.daily.pullback_rsi2.curated_largecap.v1
milodex promotion promote   regime.daily.sma200_rotation.spy_shy.v1 --to paper \
                            --recommendation "..." --risk "..." --lifecycle-exempt
milodex promotion demote    meanrev.daily.pullback_rsi2.curated_largecap.v1 --to backtest --reason "..."
```

Every command supports `--json` for machine-readable output. The `--json` contract is stable and documented in [docs/CLI_UX.md](docs/CLI_UX.md).

### Run the test suite

```powershell
.\.venv\Scripts\python.exe -m pytest                                 # full test suite
.\.venv\Scripts\python.exe -m pytest --cov=src/milodex --cov-report=term-missing
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
```

---

## Project Structure

```
src/milodex/
    broker/        Brokerage API integration (Alpaca) — every broker call goes through here
    strategies/    Config-driven strategy definitions + foreground runner
    risk/          Risk layer with veto power over all trades (never bypassed)
    execution/     Single chokepoint: intent → risk → explanation → broker
    backtesting/   Walk-forward engine; OOS-aggregate metrics; per-window stability diagnostics
    data/          Market data acquisition (Alpaca, Yahoo)
    analytics/     Performance metrics, reporting, benchmark (vs SPY), portfolio snapshots
    promotion/     Stage transitions, gates, drift detection, evidence packages, frozen manifests
    core/          SQLite event store, advisory locks, schema migrations
    cli/           Command-line interface (primary operator surface) + rich-terminal views
configs/           Strategy + risk YAML (parameters live here, not in code)
docs/              Vision, SRS, ADRs, operations, governance, reporting contract
tests/             Mirrors src/ structure
```

---

## Tech Stack

Python 3.11 · SQLite (event store) · Alpaca (broker + market data) · Yahoo Finance (secondary data) · pandas · pyarrow · rich (terminal UI) · pytest · pytest-cov · ruff. No runtime dependencies added without justification — the dependency list is itself a design decision.

---

## Phase 1 Constraints (by design)

- **Market:** US equities / ETFs only
- **Tempo:** Daily swing trades (1–5 day holds)
- **Broker:** Alpaca (paper only)
- **Capital:** Under $1,000 when live eventually unlocks
- **Edge families:** Momentum, mean reversion, breakout, regime
- **Two Phase 1 strategies, two purposes:** a *lifecycle-proof* regime strategy (SPY/SHY 200-DMA) to validate the platform end-to-end, and a *research-target* mean-reversion strategy as the first real edge hunt. They are promoted under separate rules — regime is exempt from the Sharpe / 30-trade gates per `R-PRM-004` because rotation can't produce them; meanrev is not. See [docs/SRS.md](docs/SRS.md).

---

## What's Been Validated

All six Phase 1 success criteria are closed with inline forensic evidence in [ROADMAP_PHASE1.md §2](docs/ROADMAP_PHASE1.md). Phase 1 was formally closed 2026-05-04 via ADR 0023.

- **SC-1 closed** 2026-04-26. Both strategies defined entirely in YAML with frozen manifests at `paper` stage. Runtime drift checks refuse execution on hash mismatch.
- **SC-2 closed** 2026-04-26. Multi-year walk-forward backtests run from the CLI for both strategies. Meanrev OOS-aggregate Sharpe 0.33 — honest refusal, not a setback.
- **SC-3 closed** 2026-04-28. Regime fired BUY SPY ×12 on 2026-04-23 (filled $710.21). Meanrev fired simultaneous BUY GLD ×23 + BUY SLV ×152 on 2026-04-28 (both filled).
- **SC-4 closed** 2026-04-23. `RiskEvaluator` rejected a $100k notional order — four simultaneous gate violations, non-synthetic evidence the layer works.
- **SC-5 closed** 2026-04-23/2026-05-04. Kill switch exercised against both strategies — halted trading, refused submission with `kill_switch_active`, required manual reset.
- **SC-6 closed** 2026-04-26. Operator can answer "is this strategy making/losing money, and how does it compare to SPY?" from the CLI alone.

---

## Author's Note

Milodex is a personal project, built evenings and weekends, used partly as a proving ground for disciplined AI-assisted software engineering in an unfamiliar domain. The research-system discipline, the ADR trail, and the risk-layer-as-sacred-architecture are deliberate — the interesting problem in autonomous trading is not execution, it's honesty with yourself about what you actually know.

# Milodex

**A personal, research-led trading system that discovers, validates, deploys, and monitors strategies without fooling its operator.**

---

## What Is Milodex?

Milodex is a fully autonomous trading system that analyzes market data, identifies opportunities, and executes trades without human intervention. It runs locally on your machine, connects to a brokerage via official APIs, and makes buy/sell decisions based on configurable, testable strategies.

But more importantly, Milodex is built to solve the *hard* problem: not placing trades — any script can do that — but avoiding the trap of mistaking noise for signal. The system is designed around truthful discovery, rigorous validation, and strict risk controls so that every strategy earns its way from research to live capital.

The name is a nod to Milo — a golden retriever — and the word "Index." Loyal, tireless, and always fetching returns.

---

## Why Build It?

- **Profit.** The primary goal is to generate real returns. Not guaranteed, not easy — but that's the target.
- **Exploration.** The right strategy isn't known yet — and that's by design. Milodex is built to experiment across timeframes, asset classes, and analysis methods. Testing determines direction.
- **Ownership.** This isn't a SaaS product or a managed fund. It's a personal tool that runs on your hardware, trades your money, and answers to you.
- **Shareability.** If it works, friends should be able to download an installer and run it themselves. Not a product for sale — a tool worth sharing.
- **Resume signal.** A working autonomous trading system that touches APIs, data pipelines, strategy engines, risk management, backtesting, analytics, and a polished GUI is a strong portfolio piece.

---

## Who Is It For?

Milodex is built by and for its creator — a software engineer with a CS degree, strong Python skills, and a willingness to dedicate evenings and weekends to the project. It may eventually be shared with friends via GitHub and a distributable installer, but it is not intended as a commercial product.

---

## The Mission

> Build a personal, research-led trading operating system that can reliably discover and validate a small number of repeatable strategies, then deploy them with strict automated risk controls.

The bar is not "can this system trade automatically?" — it's "can this system avoid convincing me that noise is skill?"

---

## Core Principles

### Research First, Automation Second
The system's primary job is fast, honest experimentation. A strategy earns its way through a rigorous promotion pipeline — backtest, walk-forward validation, paper trading, micro-capital live — before it ever touches real money. The research engine is the product; autonomous execution is a reward for strategies that survive.

### Let Testing Lead
No strategy, asset class, or timeframe is locked in permanently. The system is designed to backtest, paper trade, and evaluate — then the data decides what goes live. Even the universe of watched assets should be discoverable rather than hand-picked.

### Moderate Risk, Configurable Controls
The goal is decent returns with controlled downside — not moonshots. A configurable kill switch enforces hard loss thresholds. Position sizing, daily loss caps, and risk parameters are all tunable per strategy without touching code through versioned config files.

### Full Transparency
Milodex logs every decision with reasoning. Full analytics — charts, performance metrics, exportable reports — are available on demand. No black box. You check in when you want, and everything is there waiting.

### Start Free, Scale If Justified
Market data starts with free sources (Alpaca built-in, Yahoo Finance). Premium data or alternative sources (news, sentiment, social) are only added if testing shows they provide an edge that outpaces their cost.

### Earned Autonomy
Milodex operates autonomously within its configured boundaries. But certain actions always require human review: re-enabling after a kill switch event, deploying a new strategy to live capital, and increasing position size limits. Full autonomy is a privilege strategies earn through evidence, not a default.

---

## Phase One Scope

The vision is broad, but phase one is deliberately narrow. One market, one tempo, one broker — chosen to maximize learning speed and minimize the ways things can go wrong early.

### First Market: US Equities / ETFs
More stable, better studied, and well-supported by Alpaca. Crypto and other asset classes are future expansions once the infrastructure is proven.

### First Tempo: Daily Swing (1–5 Day Holds)
Compatible with under-$1k capital (avoids the $25k pattern day trader rule), a local machine running on evenings/weekends, and a system that doesn't need to fight latency. Slower tempos like weekly rotation may be explored in parallel since the infrastructure is similar.

### First Edge Families: Momentum, Mean Reversion, Breakout
Two to three edge families will be tested in phase one. The goal is not to commit to one — it's to build the research harness that can evaluate them honestly and let the data pick winners.

### First Capital: Under $1,000
Small enough that mistakes are tuition, not catastrophe. Strategies start with a fraction of this and scale only with evidence.

---

## Validation & Promotion

Strategies do not graduate on gut feeling. There is a defined pipeline with explicit gates:

### Backtest → Walk-Forward Validation
Strategies are tested using rolling train/test windows with out-of-sample holdout data. This prevents the classic overfitting trap where a strategy "learns the answer key" from historical data. A minimum of 30 trades is required to draw any statistical conclusions.

### Paper Trading → Statistical Threshold
Paper trading duration is not time-based — it's evidence-based. A strategy must demonstrate a Sharpe ratio above 0.5, maximum drawdown under 15%, and at least 30 paper trades before it's eligible for live capital. These thresholds are starting points and may be adjusted as the system matures.

### Micro-Capital Live → Scaled Live
First live deployment uses a small fraction of total capital. Scaling up requires continued performance against the same statistical thresholds, measured in live conditions.

### Execution Realism
Backtests assume conservative slippage of 0.1–0.2% per trade to avoid overstating edge. These estimates are tightened over time as real fill data becomes available for comparison.

---

## Risk Architecture

Risk management is not a feature — it's a layer. It sits between every strategy decision and every trade execution, and it has veto power.

### Configurable Per Strategy
Position sizing limits, daily loss caps, and kill switch thresholds are all defined in versioned strategy configs. Different strategies can have different risk profiles.

### Kill Switch
When a configurable loss threshold is hit, trading halts immediately. All open orders are cancelled. The system waits for manual review and re-enablement — it does not auto-resume.

### Autonomy Boundary
The following actions always require manual human review:
- Re-enabling trading after a kill switch event
- Deploying a new strategy to live capital
- Increasing position size limits

Everything else — signal generation, order placement, position management, logging — runs autonomously.

### Unacceptable Failures
The system must be architected to prevent or detect:
- Oversized orders (fat-finger protection)
- Duplicate orders
- Trading on stale or disconnected market data
- Partial fills spiraling exposure
- Paper/live performance divergence going unnoticed
- Kill switch failing to fire

---

## Observability

After every trading day, the following must be available:

- **Trade log with reasoning:** What was traded, when, at what price, and why the strategy made that decision.
- **Daily portfolio snapshot:** Current positions, cash, total value, exposure breakdown.
- **Performance vs. benchmark:** Running comparison against S&P 500 (SPY). If Milodex isn't beating a simple index fund, it isn't earning its complexity.
- **Key metrics:** Sharpe ratio, Sortino ratio, max drawdown, win rate, average win/loss, total return.
- **Exportable reports:** Full analytics available as charts and exportable data for deeper analysis.

No notifications or alerts by default. The data is always there — you check in when you want.

---

## High-Level Shape

### Broker
Alpaca is the starting broker — official API, free commission-free trading, paper trading built in, supports stocks and crypto. A clean fit for phase one. The architecture should allow swapping brokers later if needed.

### Strategies
Modular and pluggable. Strategies are defined in versioned config files, not hardcoded. Start with simpler technical approaches, iterate toward ML and alternative data as the infrastructure matures and testing justifies complexity.

### Risk Management
A dedicated risk layer sits between strategy decisions and trade execution. It enforces configurable thresholds — max position size, daily loss limits, kill switch triggers — and can override any strategy call. The strategy proposes; risk management disposes.

### Analytics
Full-stack visibility: trade logs with reasoning, performance charts, key metrics (Sharpe ratio, drawdown, win rate, benchmark comparison vs. S&P 500), and exportable reports. This isn't optional — it's how you know if the thing is actually working.

### Interface
CLI first for speed of development. Eventually a clean, polished desktop GUI. The leading option is either PySide6 or a Tauri-based frontend (JS/TS) — to be decided based on what delivers the best look and feel with manageable complexity.

### Platform
Windows-first (primary dev machine). Designed to be distributable — friends can clone the repo or grab an installer and run it on their own machines.

### Data Sources
Start with free APIs. Infrastructure should be built to plug in premium or alternative data sources later without rearchitecting. The threshold for adding a paid source: it must demonstrably improve returns beyond its cost.

---

## What Milodex Is Not

- **Not a get-rich-quick scheme.** The market is brutally competitive. This project is built with eyes open about the difficulty.
- **Not a black box.** Every trade is logged and explainable.
- **Not a product for sale.** It's a personal tool, potentially shared with friends, and a portfolio piece.
- **Not locked into one strategy.** The whole point is to explore, test, and adapt.
- **Not a bet on prediction alone.** Average prediction with disciplined risk, strong portfolio construction, and clean execution may outperform brilliant prediction with sloppy everything else.

---

## Success Criteria

Milodex is succeeding if:

- Post-cost returns are positive and measurable against S&P 500.
- Max drawdown stays within configured ceilings.
- Sharpe/Sortino ratios meet or exceed promotion thresholds.
- Live performance does not materially degrade from paper trading results.
- The system runs autonomously without requiring daily attention.
- Strategy decisions are explainable from the logs alone.

Milodex is failing if:

- It cannot distinguish between strategies that work and strategies that got lucky.
- Risk controls are bypassed, misconfigured, or untested.
- The operator (you) cannot tell whether the system is making or losing money without digging.

---

*Next steps: architecture design, detailed roadmap, and technical decisions.*

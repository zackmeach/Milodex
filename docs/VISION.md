# Milodex

**A personal, research-led trading system that discovers, validates, deploys, and monitors strategies without fooling its operator.**

> **Read this first:** [`docs/FOUNDER_INTENT.md`](FOUNDER_INTENT.md) captures the founder's personal intent for Milodex — the deeper "why" behind the project. When VISION describes *what* to build and *in what order*, FOUNDER_INTENT describes the *spirit* it must be built in: trustworthy, accessible, polished, and credibly real. All documentation and product decisions should stay consistent with it.

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
- **Shareability (Phase 2+).** If Phase 1 produces a trustworthy single-operator system, Phase 2 can package it so friends can clone or install it. Friends are expected to trust the defaults initially, not heavily customize. "Shareable with friends" is a Phase 2 packaging goal, not a Phase 1 design constraint — the architecture should avoid painting itself into a corner, but must not sacrifice simplicity to prepare for friend-facing distribution early.
- **Resume signal.** A working autonomous trading system that touches APIs, data pipelines, strategy engines, risk management, backtesting, analytics, and a polished GUI is a strong portfolio piece.

### Priority Rank

When these goals conflict, the order is fixed:

1. **Research operating system** — disciplined strategy development and controlled execution.
2. **Personal trading tool** — eventual real capital deployment.
3. **Portfolio / showcase value** — resume signal and a tool worth sharing.

Research integrity and system trustworthiness beat personal utility; personal utility beats showcase polish. If a feature makes Milodex look cooler but weakens auditability, safety, or discipline, it loses. Prefer slower, clearer, and more defensible over more impressive or more "autonomous."

Equally fixed is the rank order of *what Milodex must be excellent at first*: **evaluating** strategies first, **monitoring** second, **executing** third, **discovering** last. The first proof of value is not finding exotic ideas — it is showing the system can rigorously test, explain, gate, and manage a strategy in a way that deserves trust.

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
Milodex operates autonomously within its configured boundaries. In Phase 1, "autonomous" means **the system can run the workflow without constant manual babysitting, but not without human authority over consequential transitions**. Milodex may automatically run scheduled research jobs, evaluate signals, enforce constraints, monitor bots, raise alerts, and execute paper-trading logic — but live capital deployment, major risk changes, and recovery from kill events remain human-gated. Phase 1 autonomy is operational independence within pre-approved boundaries, not independence from the operator. Full autonomy is a privilege strategies earn through evidence, not a default.

---

## Phase One Scope

The vision is broad, but phase one is deliberately narrow. One market, one tempo, one broker — chosen to maximize learning speed and minimize the ways things can go wrong early.

### Phase One Win Condition

Phase 1 succeeds if Milodex can take **one promotable strategy instance** through the full lifecycle: define it, backtest it honestly, produce a reviewable explanation of its behavior, promote it to paper through an explicit decision gate, run it in paper with risk controls and audit logs, and require human approval for any live-capital transition. The win is **not** that the strategy makes money. The win is that Milodex can evaluate, gate, monitor, and manage a strategy in a disciplined and trustworthy way end to end.

The smallest believable version of this — still something to be proud of — is that same loop for one simple strategy.

A **promotable strategy instance** is one fully defined version of a strategy carrying: strategy type / logic identifier, instrument universe, timeframe and data resolution, signal parameters, sizing rules, risk limits, execution constraints, and promotion stage (`backtest`, `paper`, `micro_live`, `live`). It is the unit of configuration, backtesting, promotion, and audit — every reference to "a strategy" below means an instance.

### First Market: US Equities / ETFs
More stable, better studied, and well-supported by Alpaca. Crypto and other asset classes are future expansions once the infrastructure is proven.

### First Tempo: Daily Swing (1–5 Day Holds)
Compatible with under-$1k capital (avoids the $25k pattern day trader rule), a local machine running on evenings/weekends, and a system that doesn't need to fight latency. Slower tempos like weekly rotation may be explored in parallel since the infrastructure is similar.

### First Strategy (Lifecycle Proof) — SPY/SHY 200-DMA Regime

Phase 1 runs two strategies at two distinct purposes, and the docs keep them separate on purpose.

The **lifecycle-proof strategy** is a long-only daily trend-following regime strategy on SPY with SHY as the defensive fallback. Checks once per trading day after the close: if SPY is above its 200-day moving average, hold SPY; otherwise, rotate to SHY. 100% allocated to one asset at a time. No leverage, no shorting, no intraday trading.

This strategy exists to prove the **platform** can safely carry a strategy through configuration, backtesting, promotion, paper execution, risk enforcement, logging, explainability, and operator approval. It is not expected to produce statistically novel returns. Its promotion evidence is operational — lifecycle milestones reached, controls verified, explanations reviewable — **not** Sharpe- or trade-count-based. Because a 200-DMA regime strategy generates only 1–3 signals per year, the standard 30-trade / Sharpe > 0.5 thresholds cannot be applied to it; its gates are defined separately in the promotion pipeline.

### First Edge Family (Research Target) — Mean Reversion

Once the platform lifecycle is proven end-to-end with SPY/SHY, **mean reversion** is the first edge family Milodex actually *researches* — the family used to exercise the full research loop on daily swing tempo (1–5 day holds) with signal generation that genuinely tries to find edge. Momentum and breakout follow as the harness matures. The goal is not to commit to any one family — it is to build a research harness that can evaluate them honestly and let the data pick winners.

### First Capital: Under $1,000
Small enough that mistakes are tuition, not catastrophe. Strategies start with a fraction of this and scale only with evidence.

---

## Research Loop

Every promotable strategy instance moves through the same nine-step loop:

1. **Define the hypothesis.** State what the strategy exploits, where it trades, when it acts, and what would count as success.
2. **Specify the instance.** Freeze the config: universe, timeframe, parameters, sizing, risk limits, execution assumptions, promotion stage.
3. **Backtest first.** Do not start in paper. Prove the idea survives historical testing under realistic slippage, fees, delays, and constraints.
4. **Analyze results.** Return, drawdown, win/loss structure, regime behavior, turnover, parameter sensitivity, whether behavior is actually explainable.
5. **First decision gate.** Promote to paper, revise and retest, or reject and archive.
6. **Paper trade.** Run under live market conditions without real capital — test signal generation, orchestration, approvals, broker integration, operational stability.
7. **Analyze paper results.** Compare expected vs. actual. Did it trade when it should? Did execution match assumptions? Did risk controls fire? Was operator review manageable?
8. **Second decision gate.** Promote to limited live, continue paper, revise and restart, or drop.
9. **Archive everything.** Even dropped strategies keep their config, results, and rejection reason for audit and future learning.

The loop operates on promotable strategy instances as defined under "Phase One Win Condition" above.

### Research Discipline

A research loop is only as honest as the rules around it. Four rules apply to every instance in Phase 1:

- **Curated universe first.** The initial universe for every research-target strategy is a small, intentionally chosen set of highly liquid U.S. equities and ETFs — clean data, stable liquidity, simple corporate actions. Automatic asset discovery is Phase 2+. Universe drift is a silent source of overfitting and is not worth inviting until the core loop is proven.
- **Idea versus tuning.** A **new idea** changes the hypothesis (the market behavior exploited, the entry/exit concept, the universe logic, the ranking or portfolio-construction approach, the risk model, the timing model). Everything else is **tuning**. RSI 8 vs. RSI 10 is tuning. Oversold pullback reversal vs. gap-fill reversal is an idea. Treat them separately — tuning noise masquerading as idea generation is the single fastest way to confuse research breadth with optimization search.
- **Optimization has a line.** Optimization is acceptable when it tests **robustness** across a small, pre-declared range of sensible values, and when every tuning round is followed by an out-of-sample check. It crosses into overfitting when ad-hoc tuning repeatedly searches for the best historical answer without a strong prior reason for the search space. Narrow "magic" parameter islands and large performance swings from small parameter changes are treated as fragility, not edge.
- **Cap the concurrent backlog.** Phase 1 supports at most **5** concurrent experiments and operationally targets no more than **2** at a time. The point is disciplined evaluation, not running a strategy zoo — cap enforced by SRS R-XC-009.

The normative meaning of each strategy family — its semantic invariants, parameter surface, entry/exit rules, and default disable conditions — lives in `docs/strategy-families.md`, never in YAML. YAML configs carry frozen values only.

---

## Validation & Promotion

Strategies do not graduate on gut feeling. There is a defined pipeline with explicit gates.

### What Counts as Evidence

"Evidence" for promotion is the documented body of results and operational proof tied to one frozen strategy instance, evaluated against explicit minimum gate criteria for the next stage. Promising behavior is not enough. A strategy must demonstrate credible performance, understandable behavior, verified controls, and sufficient operational reliability to justify greater trust. Promotion decisions are based on **categories of evidence plus concrete thresholds**, never on intuition or a single vanity metric. For the SPY/SHY lifecycle-proof strategy, evidence is operational (lifecycle milestones hit, risk checks exercised, explanations verified); for research-target strategies, evidence is statistical plus operational.

### What Makes a Backtest Honest Enough to Trust

A backtest is honest enough when it is simple, reproducible, conservative, and free from obvious self-deception. Concretely: data that would actually have been available at the time (no lookahead, no survivorship bias where relevant), realistic slippage / commission / execution-timing assumptions, documented parameters and constraints, and robust behavior across multiple market regimes without fragile tuning. "Trust" in Phase 1 does **not** mean "this will make money." It means "this result is credible enough to justify paper trading."

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
The following actions always require explicit human approval:

- Promoting any strategy from paper to live trading
- Allocating or increasing real capital to a live strategy
- Re-enabling any bot after a kill switch, circuit breaker, or major risk event
- Changing core risk limits for live deployment
- Granting a new broker connection permission to place live trades
- Overriding a blocked or rejected execution decision
- Retiring or replacing a live strategy with a materially different version

Everything else — signal generation, order placement, position management, logging — runs autonomously within the configured boundaries.

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

### Explainability Contract

For every meaningful decision — signal, block, trade, promotion, kill — Milodex must record **what it decided, why it decided it, what inputs were used, what constraints were checked, and what alternatives were rejected or not taken**. At minimum: the strategy instance involved, the triggering data or event, the relevant rule or threshold, the current risk state, the resulting action, and whether the action required or bypassed human approval. An operator should be able to reconstruct the reasoning behind any recommendation, block, trade, promotion, or kill without guessing.

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
The data layer is organized by **role**, not by vendor. The `DataProvider` abstraction (ADR 0006) exposes three roles, each with a currently-selected provider pinned in ADR 0017:

- **Canonical research data** — historical OHLCV, reference metadata, corporate actions. Drives every backtest and promotion decision. Phase 1 selection: Massive.
- **Execution-adjacent market data** — previews and runtime monitoring that must match what the broker sees. Phase 1 selection: Alpaca SIP feed.
- **Broker state of record** — orders, fills, positions. Phase 1 selection: Alpaca Trading API.
- **Fallback feed** — local-development and degraded-operation only; never canonical research. Phase 1 selection: Alpaca IEX feed.

Historical bars are stored **raw unadjusted as the canonical preserved record**. Split-adjusted research views are derived; dividends are recorded as discrete cash events. This preserves byte-level reproducibility across time — a manifest hash that produces one backtest today produces the same backtest a year later regardless of intervening corporate actions.

Phase 1 trades long-only U.S. common stocks and plain ETFs only (ADR 0016). The threshold for expanding either the data-source stack or the instrument whitelist in Phase 2+: it must demonstrably improve the research loop beyond its integration and evidence-review cost.

---

## What Milodex Is Not

- **Not a get-rich-quick scheme.** The market is brutally competitive. This project is built with eyes open about the difficulty.
- **Not a black box.** Every trade is logged and explainable.
- **Not a product for sale.** It's a personal tool, potentially shared with friends, and a portfolio piece.
- **Not locked into one strategy.** The whole point is to explore, test, and adapt.
- **Not a bet on prediction alone.** Average prediction with disciplined risk, strong portfolio construction, and clean execution may outperform brilliant prediction with sloppy everything else.
- **Not a hype-driven "AI trader."** Milodex does not optimize for excitement, opacity, or rapid automation over discipline.
- **Not a noisy dashboard of indicators** with no coherent operating model.
- **Not a bloated platform** trying to serve every asset class, user type, and trading style at once.
- **Not a gambling toy** or an overbuilt distributed system chasing complexity for its own sake.

### Out of Scope for Phase 1 — No Matter How Tempting

Phase 1 stays focused on proving the research → validation → promotion → controlled execution loop for a single operator. These are explicitly out of scope for Phase 1:

- High-frequency or low-latency trading
- Multi-user collaboration as a first-class system requirement
- Fully autonomous live trading without human gating
- Complex portfolio optimization across many strategies at once
- Options market making, advanced derivatives infrastructure, or prime-style execution logic
- AI-generated strategy invention without strict human review
- Cloud-native distributed architecture built for scale before the core workflow is proven
- Social, marketplace, or subscription-platform features

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

## Detailed Roadmap

Phase one is broken into sub-phases. Each sub-phase is a checkpoint where the system is coherent, testable, and usable end-to-end for what it claims to do. No sub-phase is time-boxed — they advance when the evidence says they should.

### Phase 1.0 — Foundation *(complete)*
Data and broker layers. `DataProvider` and `BrokerClient` abstract interfaces behind concrete Alpaca implementations. Parquet-backed historical cache. Standardized `Bar`/`BarSet` models. Config loader for `.env` and Alpaca credentials. This is the load-bearing foundation: nothing else gets to import `alpaca-py` directly.

### Phase 1.1 — Execution & CLI *(complete)*
`ExecutionService` that normalizes trade intents and pipes them through the `RiskEvaluator` before submission. Eleven enforced risk checks (kill switch, paper-mode enforcement, stage eligibility, market hours, data staleness, daily loss cap, fat-finger, single-position cap, total exposure cap, concurrent positions cap, duplicate-order detection). `KillSwitchStateStore` with manual-reset semantics. `argparse`-based CLI with real `status`, `positions`, `orders`, `data bars`, `config validate`, `trade preview`, `trade submit --paper`, `trade order-status`, `trade cancel`, and `trade kill-switch status` commands.

### Phase 1.2 — Strategy Engine
First real signal logic. A strategy runtime that reads a YAML config, subscribes to a `BarSet` stream, produces trade intents, and hands them to `ExecutionService`. Two strategy instances are delivered in this sub-phase at distinct purposes: the **SPY/SHY 200-DMA lifecycle-proof strategy** (exercises the full platform path end-to-end without claims about edge) and the first **mean-reversion** research-target strategy (the first real edge-hunt on daily swing tempo). The harness is structured so momentum or breakout research-target strategies can be added without refactor. A minimal backtest harness rides alongside so the same strategy code runs historical and live with no branches.

The strategy engine runs as a **manually-invoked, long-running foreground process** (`milodex strategy run <name>`). The operator starts it and leaves it running while markets are open. Shutdown is intentional and dialog-driven: the operator distinguishes between a **controlled stop** (graceful, finish the current evaluation cycle and exit without accepting new intents) and the **kill switch** (immediate abort, cancel all open orders, persist halt state). See ADR 0012 for the full runtime model.

### Phase 1.3 — Analytics & Reporting
Trade log with decision reasoning, daily portfolio snapshots, running SPY benchmark comparison, and the core metrics named in "Observability" (Sharpe, Sortino, max drawdown, win rate, avg win/loss, total return). Exportable reports. Nothing is complete until the operator can answer "is this strategy making money?" from the CLI without opening the code.

### Phase 1.4 — Promotion Pipeline
Formal state machine for `backtest → paper → micro_live → live`. Stage transitions require evidence (Sharpe > 0.5, max drawdown < 15%, min 30 trades) and — for `micro_live` and above — explicit operator approval. Stage is enforced at the risk layer: a strategy whose config declares `stage: paper` cannot submit live orders even if the operator edits code by accident.

### Phase 2+ *(appendix only — not in scope now)*
Crypto universe, ML-driven signals, sentiment / alternative data, additional brokers, desktop GUI (PySide6 or Tauri), installer distribution for friends. Each is deliberately parked until phase one has produced at least one validated strategy. See `docs/SRS.md` Phase 2+ appendix for details.

---

## Technical Decisions

The why-this-choice rationale for each significant architectural decision lives in Architecture Decision Records under `docs/adr/`. This section names the decisions so a reader knows where to look. None of these should be re-litigated without a specific reason.

- **Alpaca as sole phase-one broker** — `docs/adr/0001-alpaca-as-broker.md`
- **Parquet as local historical cache** — `docs/adr/0002-parquet-as-cache.md`
- **Strategy parameters live in YAML, not code** — `docs/adr/0003-config-driven-strategies.md`
- **Paper-only trading for all of phase one** — `docs/adr/0004-paper-only-phase-one.md`
- **Kill switch requires manual reset** — `docs/adr/0005-kill-switch-manual-reset.md`
- **Abstract base classes for external integrations** — `docs/adr/0006-abc-pattern-for-external-integrations.md`
- **`argparse` for the CLI** — `docs/adr/0007-argparse-for-cli.md`
- **Risk layer with veto over execution** — `docs/adr/0008-risk-layer-veto-architecture.md`
- **Promotion pipeline as enforced stage model** — `docs/adr/0009-promotion-pipeline-stage-model.md`
- **Hybrid source of truth (Alpaca authoritative for state, Milodex for decisions)** — `docs/adr/0010-hybrid-source-of-truth.md`
- **SQLite as the event-shaped store** — `docs/adr/0011-sqlite-event-store.md`
- **Runtime model & dual-stop shutdown semantics** — `docs/adr/0012-runtime-and-dual-stop.md`
- **Market orders only for Phase 1** — `docs/adr/0013-market-orders-only-phase-one.md`
- **CLI formatter abstraction for dual human/JSON output** — `docs/adr/0014-cli-formatter-abstraction.md`
- **Strategy identifier, versioning, and frozen instance manifest** — `docs/adr/0015-strategy-identifier-and-frozen-manifest.md`
- **Phase 1 instrument whitelist** — `docs/adr/0016-phase1-instrument-whitelist.md`
- **Data source hierarchy, adjustment policy, and disagreement handling** — `docs/adr/0017-data-source-hierarchy.md`

---

## Daily Operator Workflow

The operator is the developer. The CLI is tuned for a specific loop — not status-checking for curiosity, but actively managing strategy deployments from backtest through paper and (eventually) live.

A typical session looks like:

1. **Anchor.** `milodex status` — current trading mode, kill-switch state, market clock, account balance, open positions. Everything else flows from this.
2. **Inspect.** `milodex positions` and `milodex orders` to see what's running.
3. **Configure or tune.** Edit a `configs/*.yaml` strategy file — adjust universe, parameters, risk overrides, or promotion stage.
4. **Validate.** `milodex config validate <path>` catches schema errors before anything touches the market.
5. **Rehearse.** `milodex trade preview` runs a full dry evaluation through the risk layer — every check fires, no order is submitted. This is the intended rehearsal path before any real submission.
6. **Execute (paper).** `milodex trade submit --paper` submits through `ExecutionService`, which cannot be bypassed. Risk has veto; paper mode is enforced regardless of what the strategy config claims.
7. **Evaluate.** (Phase 1.3+) `milodex analytics` commands surface the metrics that decide whether a strategy earns promotion.
8. **Promote or kill.** (Phase 1.4+) If evidence crosses the thresholds, advance the stage in the strategy's YAML. If the kill switch fires, `milodex trade kill-switch status` shows why — reset is manual and deliberate.

The loop is designed so every step is inspectable from the CLI alone. If the operator has to open a Python shell or edit code to answer a question about a running strategy, the CLI has failed.

---

## Highest-Stakes Assumption

The risk layer's correctness is the single most load-bearing assumption in this entire system.

Every design principle above — research-first, earned autonomy, moderate risk, full transparency — implicitly assumes that when a strategy proposes a trade, the risk layer will reject it if it shouldn't happen. An undetected bug, a misconfigured threshold, or a code path that submits orders without passing through `ExecutionService.evaluate()` does not just degrade performance; it breaks the promise the system makes to its operator.

This risk is called out here deliberately. Specific mitigations:

- **One execution path.** All order submission flows through `ExecutionService`. The `BrokerClient` should never be called directly from strategy or CLI code for the purpose of placing orders.
- **No silent overrides.** Risk parameters can be tuned via config, but there is no `--skip-risk` flag and there is not going to be one. If a check is wrong, the answer is to fix the check, not bypass it.
- **Stage enforcement at the risk layer.** A strategy's stage is checked *before* the trade intent is honored. A `stage: paper` strategy physically cannot place live orders.
- **Duplicate-path detection.** Future analytics should reconcile trades submitted (per `ExecutionService` records) with trades filled (per Alpaca) to detect any order that reached the broker without a corresponding evaluation.
- **Tests are not optional.** The `RiskEvaluator` has the highest test-coverage priority of any module. A missing test here is a latent autonomy failure.

The rest of the system exists to serve the research loop. The risk layer exists to make sure that when the research loop is wrong — and it will sometimes be wrong — the damage stops at "tuition."

---

*Next steps: see `docs/SRS.md` for formal requirements and `docs/adr/` for decision records.*

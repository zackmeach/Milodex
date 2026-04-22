# Milodex — Software Requirements Specification

**Status:** Living document
**Scope:** Phase 1 (US equities/ETFs, daily swing, Alpaca, under $1k capital)
**Audience:** The operator (developer) and any future contributor

---

## How to Read This Document

This SRS is organized by **domain**, not by phase. Each domain section answers three questions:

1. **What the module must do** — user stories (operator-facing flows) and system-level specs (`the system shall…`).
2. **Inputs and outputs** — where data comes from and where it goes.
3. **Acceptance criteria** — verifiable pass/fail conditions that double as test specs.

Requirements are numbered per domain (e.g., `R-BRK-001`) so they can be cited in code comments, tests, and commits.

Phase 2+ scope is preserved in the appendix as declared future intent. It is not in scope for any phase-one work.

The implied Phase 1 MVP: **one mean-reversion strategy runs end-to-end from backtest through paper trading, producing real metrics against real historical and live-paper data.** Concurrent multi-strategy execution is out of scope for Phase 1 (see Phase 2+ appendix).

---

## Key Terms

- **Promotable strategy instance** — one fully defined version of a strategy carrying its own strategy type, universe, timeframe, signal parameters, sizing rules, risk limits, execution constraints, and promotion stage. It is the unit of configuration, backtesting, promotion, and audit. Every requirement below that references "a strategy" means an instance.
- **Lifecycle-proof strategy** — the SPY/SHY 200-DMA regime strategy used to validate the *platform* end-to-end. Its promotion gates are operational (lifecycle milestones reached, controls verified), not statistical. It is intentionally exempt from the 30-trade / Sharpe > 0.5 gates in R-PRM-004, because a regime strategy generates only 1–3 signals per year.
- **Research-target strategy (edge-family target)** — strategies intended to discover actual edge. Mean reversion is the first in Phase 1. Research-target strategies are subject to the full statistical thresholds in R-PRM-004.
- **Controlled stop vs. kill switch** — see R-EXE-011 and R-EXE-012. Controlled stop = graceful shutdown, state preserved, positions and orders untouched. Kill switch = halt, cancel all open orders, persist halt state, manual reset required.
- **Explanation record** — the audit payload required by R-XC-008 for every meaningful decision.

---

## Operator Profile

The operator is the developer. Assumptions:

- Comfortable editing YAML, running Python commands, reading logs.
- Familiar with Alpaca API concepts (paper/live modes, assets, orders, positions).
- Working on a Windows dev machine, evenings and weekends.
- Not a non-technical user — requirements do not dumb down for beginners.

The CLI is tuned for this user. Error messages assume familiarity with the system's domain language.

---

## Domain 1 — Broker Integration (`src/milodex/broker/`)

### Purpose
Single authoritative interface to the brokerage. Nothing outside this module imports `alpaca-py`.

### User Stories
- *As the operator,* I can submit a paper order and get back a canonical order object without touching Alpaca's SDK types.
- *As the operator,* I can list my open positions and orders in one call each, independent of which broker is behind the interface.
- *As the operator,* I can trigger `cancel_all_orders()` as part of a kill-switch event and have every open order cancelled at the broker.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-BRK-001 | The system **shall** expose an abstract `BrokerClient` base class defining the full broker surface (orders, positions, account, market clock). | Unit test: a subclass that omits any abstract method fails instantiation with `TypeError`. |
| R-BRK-002 | The system **shall** provide an `AlpacaBrokerClient` concrete implementation of `BrokerClient`. | Integration test against Alpaca paper API: each `BrokerClient` method returns a non-null result or well-typed error. |
| R-BRK-003 | `BrokerClient` methods **shall** return Milodex-defined domain types, never raw Alpaca SDK objects. | Type-check: no return value in the `broker/` public interface has a type from the `alpaca` package. |
| R-BRK-004 | The system **shall** provide `cancel_all_orders()` to support kill-switch enforcement. | After calling `cancel_all_orders()`, `get_orders(status="open")` returns an empty list within 5 seconds. |
| R-BRK-005 | The system **shall** expose a market-clock query so callers can determine whether markets are open. | Method returns `(is_open: bool, next_open: datetime, next_close: datetime)`. |
| R-BRK-006 | The broker module **shall not** contain any strategy, risk, or analytics logic. | Code review: no imports from `milodex.strategies`, `milodex.risk`, `milodex.execution`, or `milodex.analytics` inside `broker/`. |
| R-BRK-007 | Phase 1 order submission **shall** support **market orders only**. Limit, stop, stop-limit, and trailing-stop are Phase 2+. | Test: submitting any non-market order type raises a structured `UnsupportedOrderType` error before reaching the broker. See ADR 0013. |
| R-BRK-008 | Position sizing **shall** be expressed in **notional dollars** (fractional shares), not share count. | Test: a $50 notional intent on a $500 stock produces a 0.1-share order; share-only sizing is refused at the `ExecutionService` boundary. |
| R-BRK-009 | Every order submitted to Alpaca **shall** carry a deterministic client order ID of the form `{strategy_name}-{YYYYMMDD}-{uuid4[:8]}`. | Test: two submissions produce distinct IDs; crash-recovery reconciliation can match a prior submission to its broker-side state via the ID. |
| R-BRK-010 | Order time-in-force **shall** default to DAY; GTC and other TIFs are Phase 2+. | Schema test on the order submission path. |

---

## Domain 2 — Market Data (`src/milodex/data/`)

### Purpose
Acquire, cache, and serve standardized OHLCV bars to strategies and the backtester.

### User Stories
- *As the operator,* I can fetch daily bars for a list of symbols over a date range from the CLI and see them cached locally afterward.
- *As a strategy,* I can request a `BarSet` for any symbol + timeframe and receive a pandas-backed object with canonical column names, without knowing the upstream data source.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-DAT-001 | The system **shall** define a `DataProvider` ABC with `get_bars`, `get_latest_bar`, and `get_tradeable_assets`. | Abstract method coverage verified via `inspect.getmembers`. |
| R-DAT-002 | The system **shall** provide `AlpacaDataProvider` implementing `DataProvider`. | Integration test: `get_bars(["SPY"], DAY_1, ...)` returns a non-empty `BarSet`. |
| R-DAT-003 | Historical bars **shall** be cached locally as Parquet files keyed by `(symbol, timeframe, date-range)`. | After one fetch, a second identical fetch returns from cache without a network call (verifiable via mocked client or request count). |
| R-DAT-004 | A `BarSet` **shall** expose canonical bar columns `timestamp, open, high, low, close, volume, vwap` and **shall** be accompanied — via sidecar metadata on the containing request — by: symbol, session date, split-adjustment state (or split factor), dividend event data where applicable, active / delisted status, primary exchange / asset-type metadata, and market-session calendar alignment. For the Phase 1 research-target family (mean-reversion daily swing), daily `open` and `close` are mandatory fields because signals are computed on completed daily bars and executed at the next open. | Schema assertion test on `.to_dataframe()` output plus an accompanying metadata-presence test on the request's sidecar. |
| R-DAT-005 | The system **shall** support timeframes `MINUTE_1, MINUTE_5, MINUTE_15, HOUR_1, DAY_1`. | Enum round-trip test: each value maps to a valid Alpaca timeframe and back. |
| R-DAT-006 | Cache freshness **shall** be detectable so the execution layer can reject stale data. | `get_latest_bar()` return value includes a timestamp that the risk evaluator can compare against `now()`. |
| R-DAT-007 | The `DataProvider` ABC **shall** expose three named roles: **canonical research data provider** (historical OHLCV, reference metadata, corporate actions), **execution-adjacent market-data feed** (previews, runtime monitoring, broker-aligned quotes), and **fallback market-data feed** (degraded-mode local dev only). Role assignment is configured per environment; no strategy or risk code names a concrete provider. See ADR 0017. | Code review: `strategies/`, `execution/`, `backtesting/`, and `analytics/` reference only role interfaces, never a vendor-specific provider class. |
| R-DAT-008 | Historical OHLCV **shall** be stored on disk as **raw unadjusted bars** as the canonical preserved record. Split-adjusted research views **shall** be computed as a derived layer from the raw record plus split events, reproducibly rebuildable from preserved inputs. Dividends **shall** be recorded as discrete cash events and flow through P&L / total-return accounting; they **shall not** mutate signal bar series into a total-return proxy. | Test: a stored bar written at time T is byte-identical after later split events are ingested; the adjusted view is rebuilt from (raw bars, split events) deterministically; dividends appear in analytics as cash events, not as bar-price mutations. |
| R-DAT-009 | Corporate actions **shall** be handled with explicit rules: **splits** preserve raw history and invalidate affected cached indicators; **dividends** record as cash events for holdings and performance; **symbol changes** maintain an internal stable asset identity with a ticker-alias timeline — raw ticker text **shall not** be a permanent identity key; **delistings** preserve the historical asset, mark it inactive, prevent new entries, and remain visible in research and audit history. | Per-action unit tests; integration test covering one full split + one dividend + one ticker change on paper-traded symbols. |
| R-DAT-010 | Cache invalidation **shall** follow these rules: (a) finalized historical daily bars are immutable except when a corporate action or vendor correction touches them; (b) the current session's daily bar is provisional until the session is complete and the provider's finalization window passes; (c) corporate-action and ticker reference metadata refreshes daily before the trading session and invalidates immediately on detected relevant events; (d) universe-membership metadata refreshes daily and any change creates a new universe manifest version (never a silent in-place mutation); (e) live quote / last-trade preview caches are short-lived and disposable, never reused as durable truth; (f) any symbol touched by a split, dividend adjustment, ticker event, or delisting change invalidates cached indicators, eligibility flags, and historical projections for that symbol. | Per-rule unit test; end-to-end test: injecting a synthetic split event invalidates the affected symbol's cached indicators before the next evaluation cycle. |
| R-DAT-011 | A symbol **shall** be eligible for inclusion in a backtest run only if it has: (a) enough warm-up history for every declared indicator; (b) at least **252** prior trading sessions before its first eligible signal date; (c) at least **98%** expected daily-bar coverage over the tested interval; (d) no unexplained gap longer than **2 consecutive trading sessions**; (e) corporate-action coverage sufficient to explain major discontinuities. The backtester **shall never silently forward-fill missing bars** to satisfy these conditions. Excluded symbols **shall** be recorded explicitly in the run manifest. | Test: a symbol failing any condition is either excluded with a manifest entry or the entire run is refused; forward-fill is not performed under any flag. |
| R-DAT-012 | Before any backtest, preview, or submit flow, the system **shall** run an automated data-quality scan covering at minimum: missing bars, duplicate bars, impossible OHLC relationships (e.g., `low > high`), stale timestamps, volume anomalies, price jumps unexplained by recorded corporate actions, active/delisted contradictions, session / timezone misalignment, warm-up-history gaps, and provider-vs-broker discrepancies beyond tolerance. Severe findings **shall** block the run; non-fatal findings **shall** be surfaced in the evidence package and audit trail for the run. | Per-check unit test; integration test: a synthetic bad bar injected into the research cache is detected and blocks a backtest, while a benign warning is logged into the run's evidence package. |
| R-DAT-013 | **Research / backtest staleness policy.** Historical bars **shall** be as of the latest finalized daily snapshot from the canonical research data provider. The current-day daily bar **shall not** be treated as final until the session is complete and the provider's finalization window has passed. Corporate-action and reference snapshots used by a run **shall** be version-frozen with the run manifest (per R-STR-011). | Test: a run referencing a version-frozen snapshot produces identical output one month later regardless of intervening vendor-side corrections. |
| R-DAT-014 | **Preview staleness policy.** Market-data displayed in preview output **shall** be treated as stale if older than **60 seconds** during market hours. Preview output **shall** distinguish explicitly between "last completed session" data and any current-session live snapshot. | Test: preview output labels its data origin and refuses to proceed with market-data older than 60 s during market hours. |
| R-DAT-015 | **Submit staleness policy.** At submission time, broker-facing state **shall** be fresh-now with no cached order/position/tradability data older than **5 seconds**. Order state, positions, and tradability at submit **shall** be read from the broker state of record directly. If freshness cannot be verified, the submission **shall** block rather than guess. | Test: a mocked broker that stalls past 5 s triggers a structured staleness error before any order is sent. |
| R-DAT-016 | Trading universes **shall** be expressed as frozen manifests (e.g., `configs/universe_phase1_v1.yaml`) referenced by strategy configs via `universe_ref`. A strategy **shall not** inline a symbol list; any membership change **shall** create a new universe manifest version. Universe manifests **shall** be hashed and persisted with each run alongside the strategy manifest (per R-STR-011). Universes **shall** be validated against the Phase 1 instrument whitelist (ADR 0016) at load time. | Schema test: a strategy inlining a universe list fails `milodex config validate`; a universe manifest containing a forbidden instrument (option, leveraged ETF, short, etc.) fails load with a whitelist error. |

---

## Domain 3 — Execution & Risk (`src/milodex/execution/`)

### Purpose
The single gate every trade passes through. `ExecutionService` normalizes trade intents; `RiskEvaluator` applies every check; `KillSwitchStateStore` persists the halt/resume state.

> **See also:** [`docs/RISK_POLICY.md`](RISK_POLICY.md) specifies the Phase 1 numeric defaults (paper baseline, sizing, exposure caps, daily loss), the kill-switch trigger enumeration, the strategy-level vs account-level scope split, the post-trip incident requirements, the duplicate-order key set and block-on-uncertainty rule, and the hard-stop vs warning classification. Requirements R-EXE-014 through R-EXE-017 below refer to it. Numeric values live in `configs/risk_defaults.yaml`.

### User Stories
- *As the operator,* I can preview a trade intent and see which risk checks would pass or fail *before* submitting.
- *As the operator,* I can submit a trade and be confident that if any check fails, no order reaches the broker.
- *As the operator,* when the kill switch trips, I must reset it manually; the system will never auto-resume.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-EXE-001 | All order submissions **shall** flow through `ExecutionService.submit()`. Direct `BrokerClient.submit_order()` calls from strategy or CLI code are forbidden. | Lint/convention test: grep for `submit_order(` outside `execution/` returns no matches in strategy/CLI code. |
| R-EXE-002 | `ExecutionService` **shall** run the full `RiskEvaluator` check set before any order is submitted. | Unit test: if any check fails, `submit()` raises and `BrokerClient.submit_order` is never called. |
| R-EXE-003 | `ExecutionService` **shall** support a `preview=True` mode that runs evaluation without submission. | `preview()` returns a structured result showing each check's outcome; no broker call is made. |
| R-EXE-004 | The `RiskEvaluator` **shall** enforce these checks: kill switch active, paper-mode enforcement, strategy stage eligibility, market hours, data staleness, daily loss cap (kill-switch threshold and strategy-level), order-value fat-finger, single-position size, total portfolio exposure, **sector exposure cap, correlated-idea exposure cap,** concurrent positions cap, **config fingerprint match,** **broker-vs-local state reconciliation,** duplicate-order detection. See `docs/RISK_POLICY.md` for numeric thresholds and "Hard Stops vs Warnings" for the classification of each check. | Each check has a dedicated unit test covering pass and fail cases. |
| R-EXE-005 | A kill-switch event **shall** (a) halt new order submission, (b) call `BrokerClient.cancel_all_orders()`, (c) persist state to `KillSwitchStateStore`. | End-to-end test: triggering the kill switch leaves no open orders and a persisted state file indicating `active: true`. |
| R-EXE-006 | Kill-switch reset **shall** require an explicit operator action. The system **shall not** auto-resume under any condition. | No code path writes `active: false` to the state file without an explicit `reset()` call originating from a CLI command. |
| R-EXE-007 | During phase one, the `RiskEvaluator` **shall** reject any trade whose routing implies live (non-paper) submission, regardless of strategy config. | Test: a strategy config declaring `stage: live` still fails at evaluation time. |
| R-EXE-008 | All risk thresholds **shall** be sourced from `configs/risk_defaults.yaml` with optional per-strategy overrides. | Test: editing `risk_defaults.yaml` changes evaluator behavior without code changes. |
| R-EXE-009 | Duplicate-order detection **shall** consider, at minimum, `(strategy_instance, symbol, side, action_type, target_quantity_or_exposure, execution_window)` per `docs/RISK_POLICY.md` "Duplicate-Order Policy". If the system cannot confidently determine duplicate status, it **shall block** the order and require review rather than submit. | Tests: identical intent submitted twice within the window fails the second time; an intent whose duplicate status cannot be determined (e.g., unreconciled prior submission) is blocked with a clear error. |
| R-EXE-010 | The kill switch **shall** trip on **any** trigger enumerated in `docs/RISK_POLICY.md` "Kill-Switch Triggers", including but not limited to: daily loss cap breach, portfolio drawdown breach, repeated order submission failures, repeated broker-vs-local state mismatches, repeated data-quality failures, stale or unverifiable submit-time data, duplicate-order detection failure, execution divergence from allowed assumptions, account-level exposure breach, strategy config fingerprint mismatch at runtime, repeated runtime exceptions in critical trading paths, broker connectivity failure after retries, operator-triggered emergency stop. | Unit test per trigger class: synthetically inducing the condition persists `active: true` to `KillSwitchStateStore` at the correct scope (strategy vs account per R-EXE-014) and calls `cancel_all_orders()` for account-level trips. |
| R-EXE-011 | The system **shall** distinguish **controlled stop** from **kill switch**. A controlled stop: (a) stops accepting new trade intents, (b) allows the current evaluation cycle to complete, (c) persists state and exits cleanly, (d) does **not** cancel open orders or positions at the broker, (e) does **not** trip the persistent kill-switch state. | Integration test: triggering a controlled stop mid-run leaves open paper orders untouched at Alpaca and leaves kill-switch state untouched. |
| R-EXE-012 | The kill switch (whether triggered by a rule in R-EXE-010 or invoked intentionally by the operator via the close dialog) **shall** always result in the same state: `active: true` persisted, all open orders cancelled at the broker, and manual reset required on next startup. | Integration test: both trigger paths produce identical `KillSwitchStateStore` state and an empty open-orders list at Alpaca. |
| R-EXE-013 | The system **shall** enforce that at most **one strategy runs at a time** during Phase 1. Attempting to start a second concurrent strategy process **shall** be refused with a clear error identifying the running strategy. | Test: a file-lock or PID file under `state/` prevents concurrent `strategy run` invocations. Multi-strategy concurrency is Phase 2+. |
| R-EXE-014 | The kill switch **shall** support two scopes: **strategy-level** (halts one strategy instance) and **account-level** (halts all trading). Conditions bounded to one strategy (e.g., that strategy's config fingerprint mismatch, its repeated rejections) trip the strategy-level switch; conditions threatening system integrity (broker connectivity loss, broker-vs-local mismatch, account-exposure breach, operator emergency stop) trip the account-level switch. Scope assignment per trigger **shall** match `docs/RISK_POLICY.md` "Kill-Switch Scope". | Tests: a strategy-fingerprint mismatch halts only that instance and leaves others eligible; a broker-connectivity trip halts all and cancels all open orders. |
| R-EXE-015 | A kill-switch trip **shall** produce a reviewable incident record, not merely a halt flag. The record **shall**: (a) capture the exact triggering condition, (b) snapshot local state, broker state, and affected strategy state, (c) mark scope (strategy vs account) in durable state, (d) surface an operator-facing incident summary, (e) be linked by ID to any subsequent re-enable governance event (per `docs/PROMOTION_GOVERNANCE.md` reversal pattern), (f) permit read-only inspection and exposure-reducing actions during the halt. No auto-resume path **shall** exist. | Integration test: a synthetic trip writes a complete incident record with all listed fields; read-only CLI commands succeed during the halt; reset requires an explicit operator command that writes a linked governance event. |
| R-EXE-016 | The `RiskEvaluator` **shall** distinguish **exposure-increasing** orders from **exposure-reducing** orders and apply policy asymmetrically per `docs/RISK_POLICY.md` "Reducing vs Increasing Exposure". Exposure-increasing orders run the full check set. Exposure-reducing orders (Phase 1: sells that close or shrink existing long positions) run a more permissive set and **shall** remain permitted while an account-level kill switch is active, subject to the rule that no reducing order may itself create new net risk. | Tests: an ordinary closing sell succeeds while the kill switch is active; any sell whose net effect would increase risk is blocked; an increasing buy is blocked during kill-switch active state. |
| R-EXE-017 | Risk checks **shall** be classified into **hard stops** (block submission, no implicit override) and **warnings** (logged in the explanation record per R-XC-008, do not block) matching `docs/RISK_POLICY.md` "Hard Stops vs Warnings". Warnings that repeat beyond a configured threshold **shall** escalate to their corresponding hard stop (per R-EXE-010 trigger set). | Tests: each classified check exhibits the documented behavior; N consecutive data-quality warnings trip the data-quality hard stop; the CLI preview per R-CLI-007 displays hard-stop and warning outcomes distinctly. |
| R-EXE-018 | The daily loss computation **shall** equal `(current_realized_pnl + current_unrealized_pnl) - start_of_day_equity_snapshot`, compared against `daily_limits.max_daily_loss_pct` in `configs/risk_defaults.yaml`. A start-of-day equity snapshot **shall** be persisted once per trading day and **shall not** be recomputed intraday. | Tests: a synthetic day with fixed SOD equity and moving P&L produces the expected breach timing; intraday restart preserves the SOD snapshot. |

---

## Domain 4 — Strategy Engine (`src/milodex/strategies/`)

### Purpose
Transform market data into trade intents according to config-defined rules. Contains no risk or execution logic.

### User Stories
- *As the operator,* I can define a strategy entirely in a YAML file under `configs/` — universe, parameters, tempo, risk overrides, stage — and run it without editing Python code.
- *As the operator,* I can add a new edge family (e.g., a breakout strategy) by implementing one class, without touching existing strategies.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-STR-001 | The system **shall** define a `Strategy` base class with a method that accepts market data (`BarSet`) and current portfolio state and returns zero or more trade intents. | Unit test: base class is abstract; subclasses must implement the generate method. |
| R-STR-002 | Every strategy **shall** be driven by a YAML config validated against a known schema. | `milodex config validate` passes/fails correctly on valid/invalid configs. |
| R-STR-003 | Strategy configs **shall** include: name, version, enabled, universe, parameters, tempo (bar size, hold days), risk overrides, stage, backtest assumptions. | Schema test covers every required field. |
| R-STR-004 | Strategies **shall not** call `BrokerClient` directly. Their output is intents; submission is `ExecutionService`'s job. | Code review / lint: no `BrokerClient` import in `strategies/`. |
| R-STR-005 | The same strategy code **shall** run under backtesting and paper trading without modification. Timeframe and data source are injected; strategy code does not branch on mode. | Integration test: identical parameter set produces consistent intents on historical and paper-live data streams. |
| R-STR-006 | Phase 1.2 **shall** deliver two strategy instances: (a) the **SPY/SHY 200-DMA lifecycle-proof strategy** (long-only daily regime, defined under Key Terms), which exercises the full platform lifecycle without claims about edge; and (b) the first **mean-reversion** research-target strategy, which is the first edge family fully implemented. Momentum and breakout research-target strategies follow in later sub-phases without architectural change. | End-to-end test: both configured strategies consume bars, produce intents, pass risk, and reach paper submission. The lifecycle-proof strategy additionally exercises at least one stage transition through the promotion pipeline. |
| R-STR-007 | The strategy engine **shall** run as a **manually-invoked, long-running foreground process** started by `milodex strategy run <name>`. The process remains active across many evaluation cycles until the operator stops it. Daemons, external schedulers (Task Scheduler / cron), and process supervisors are Phase 2+. | Integration test: invoking the run command blocks and logs periodic heartbeat; exit requires either a controlled stop, a kill switch, or process termination. See ADR 0012. |
| R-STR-008 | On receiving SIGINT (Ctrl+C) during a running strategy, the process **shall** display an interactive three-option shutdown dialog: `[c]ontrolled stop` / `[k]ill switch` / `[n]evermind — keep running`. Selecting `controlled stop` sets a shutdown flag; selecting `kill switch` immediately invokes R-EXE-012 semantics; selecting `nevermind` resumes execution. | Integration test: each option produces the documented state and exit behavior. |
| R-STR-009 | A second SIGINT while the shutdown dialog is already open, or an OS-level forced-close event (CTRL_CLOSE_EVENT on Windows, SIGTERM on POSIX) **shall** default to kill-switch semantics (R-EXE-012) as a hard fallback. | Test: a double Ctrl+C triggers the kill switch and cancels open orders. |
| R-STR-010 | Strategy state (rolling features, last-trade timestamps, streak counters) **shall** persist to `state/strategies/<name>.json` on controlled stop and reload on next start. Kill switch does **not** guarantee state flush — state on disk may lag up to one evaluation cycle. | Test: controlled stop + restart resumes with identical state; kill-switch restart may reset to last-flushed state. |
| R-STR-011 | Every strategy run (backtest, paper, live) **shall** compute a canonical-form SHA-256 hash of its loaded config (the *frozen instance manifest*, per ADR 0015) and persist the manifest plus hash to the SQLite `strategy_manifests` table before any signal is generated or order evaluated. Runs, fills, promotion-log entries, and explanation records (R-XC-008) **shall** reference that manifest by hash. | Test: two identical YAMLs produce identical hashes; any byte-level change produces a different hash; a run cannot start if the manifest cannot be persisted. |
| R-STR-012 | Every strategy **shall** carry a structured identifier of the form `family.template.variant.vN` (per ADR 0015) declared as `strategy.id` in its YAML. The identifier **shall** be the primary key used across runs, promotion logs, explanation records, and CLI surfaces. | Schema test: configs without a well-formed `strategy.id` fail `milodex config validate`. |
| R-STR-013 | The version-vs-variant rule (ADR 0015) **shall** be enforced at promotion time: a change that alters a family's semantic invariants (as declared in `docs/strategy-families.md`) requires a new `vN` and a fresh evidence trail. Pure parameter tuning within the family's declared parameter surface **shall** reuse the current version and produce a new manifest hash. | Test: a config edit that toggles `long_only` false (not in the `meanrev` parameter surface) is refused at promotion with a structured "new version required" error; an edit to `rsi_entry_threshold` is accepted and produces a new manifest hash under the same `strategy.id`. |
| R-STR-014 | Each strategy instance **shall** carry a `disable_conditions` catalog that starts with the family's default disable conditions (per `docs/strategy-families.md`) and may be extended — **not reduced** — via `disable_conditions_additional` in YAML. The risk layer **shall** halt the strategy when any active condition in that catalog is true, independent of code-level correctness. | Test: removing a family-default condition in YAML is refused at config-validate; manually tripping a condition halts new intent generation and records an explanation record. |
| R-STR-015 | The normative meaning of a strategy family (market behavior exploited, semantic invariants, parameter surface, entry/exit/ranking rules, default disable conditions) **shall** live in `docs/strategy-families.md`, not in YAML configs. YAML configs carry frozen instance values only. | Code review: no YAML in `configs/` embeds prose definitions of entry/exit/stop rules; changes to rule meaning land in `docs/strategy-families.md` with a matching config update. |
| R-STR-016 | Phase 1 position sizing **shall** be **simple fixed-percent of current account equity**, defaulting to `sizing.per_position_target_pct` in `configs/risk_defaults.yaml` (0.10). Volatility-aware sizing is explicitly out of scope for Phase 1. A strategy may override the target via its per-strategy `risk_overrides`, but the resulting target size **shall not** exceed `portfolio.max_single_position_pct`. | Tests: a strategy with no sizing override produces intents sized at 10% of current equity; an override exceeding the single-position cap is refused at config-validate. |

---

## Domain 5 — Backtesting (`src/milodex/backtesting/`)

### Purpose
Evaluate strategies against historical data honestly, with walk-forward validation and realistic execution assumptions.

### User Stories
- *As the operator,* I can run `milodex backtest <strategy.yaml>` and get metrics plus a per-trade log without needing a notebook.
- *As the operator,* I can trust that backtest results are not overfitted because the engine enforces walk-forward train/test splits.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-BKT-001 | The backtester **shall** execute strategy logic against cached historical bars. | Test: running a backtest on a known strategy over a known date range produces deterministic output. |
| R-BKT-002 | The backtester **shall** apply configurable slippage (default 0.1–0.2%) and commission (default 0.0 for Alpaca) to simulated fills. | Test: toggling slippage changes equity curves predictably. |
| R-BKT-003 | The backtester **shall** support walk-forward validation with rolling train/test windows and out-of-sample holdout. | Test: walk-forward config produces N non-overlapping evaluation windows as specified. |
| R-BKT-004 | The backtester **shall** refuse to report "pass" status on any run with fewer than 30 trades. | Test: 29-trade backtest returns `statistically_insufficient: true`. |
| R-BKT-005 | Backtest output **shall** include equity curve, trade log, and the core metric set (Sharpe, Sortino, max drawdown, win rate, avg win/loss, total return). | Snapshot test of output schema. |
| R-BKT-006 | The backtester **shall** reuse the same `Strategy` interface as paper/live execution. No backtest-specific `Strategy` subclass. | Code review: one `Strategy` class type is consumed by both backtester and executor. |

---

## Domain 6 — Analytics & Reporting (`src/milodex/analytics/`)

### Purpose
Answer the question "is this working?" from the CLI alone, without opening the code or a notebook.

> **See also:** [`docs/REPORTING.md`](REPORTING.md) specifies the primary trust report the operator opens most often, the minimum analytics set that supports trust/distrust judgment, mandatory trade-level reasoning fields, daily and weekly summary contents, the rule for which paper/live divergences to surface, attribution slices, essential vs nice-to-have charts, and the vocabulary for presenting uncertainty. Requirements R-ANA-006 through R-ANA-010 below refer to it.

### User Stories
- *As the operator,* I can see my running Sharpe, Sortino, max drawdown, win rate, avg win/loss, and total return per strategy from a CLI command.
- *As the operator,* I can compare strategy performance against SPY for the same period.
- *As the operator,* I can export a performance report (CSV or JSON) for deeper off-line analysis.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-ANA-001 | Every submitted trade **shall** be persisted to the SQLite event store at `state/milodex.db` (table `trades`) with: client order ID, strategy name + version, stage, symbol, side, quantity (notional + shares), order type, submitted timestamp (UTC), Alpaca order ID, fill timestamp, fill price, filled quantity, status, reasoning blob (JSON text), and per-check risk verdicts (JSON text). See ADR 0011. | Schema test: `PRAGMA table_info(trades)` matches the documented schema; a synthetic submission produces a row that round-trips through `pd.read_sql`. |
| R-ANA-001a | Stage transitions (promotion / demotion via `enabled: false`) **shall** be persisted to the SQLite `promotion_log` table with: strategy name, from/to stage, timestamp, evidence snapshot (JSON text), optional operator note. | Schema test plus an end-to-end test covering a single transition. |
| R-ANA-001b | Positions, open orders, account balance, and buying power **shall not** be mirrored in SQLite. Those are queried live from Alpaca per R-ANA-001c (hybrid source of truth, ADR 0010). | Code review: no `positions` or `orders_current` table exists; `milodex status` re-queries Alpaca on every invocation. |
| R-ANA-001c | On startup, the system **shall** reconcile Alpaca's current positions and open orders against the SQLite trade log. Mismatches (a position with no corresponding trade record, or a trade record with no matching broker state) **shall** be logged at WARN level and surfaced in `milodex status`. The system **shall not** auto-correct either side. | Integration test: manually closing a paper position on Alpaca's web UI produces a WARN on next startup naming the reconciliation mismatch. |
| R-ANA-002 | The system **shall** compute Sharpe, Sortino, max drawdown, win rate, average win, average loss, and total return per strategy and aggregate. | Golden-file test against a known trade log. |
| R-ANA-003 | The system **shall** compute SPY benchmark comparison over the same period as any reported strategy. | Test: report includes strategy return and SPY return side-by-side. |
| R-ANA-004 | Reports **shall** be exportable to CSV and JSON via a CLI command. | Round-trip test: export + re-import yields identical data. |
| R-ANA-005 | The analytics layer **shall** surface the divergence between paper and live performance for any strategy that has trades in both modes. | (Deferred until any strategy reaches `micro_live`.) |
| R-ANA-006 | The analytics layer **shall** compute and expose the **minimum analytics set** enumerated in `docs/REPORTING.md` "Minimum Analytics Set for Trust / Distrust" for every strategy instance. Any CLI or report surface that presents strategy-level analytics **shall** present this set at parity — no cherry-picking. | Test: the per-strategy analytics command returns every field in the minimum set or an explicit "insufficient evidence" marker with a documented reason; a surface that omits a field fails a schema test. |
| R-ANA-007 | Every preview and every submit **shall** persist a **trade-level reasoning record** containing the fields enumerated in `docs/REPORTING.md` "Mandatory Trade-Level Reasoning Fields". The record **shall** be reconstructable later as both a machine event and a human-readable explanation, consistent with R-XC-008 and with the preview/submit audit fields in `docs/OPERATIONS.md`. | Schema test on the reasoning payload; end-to-end test reconstructs the human-readable explanation from the DB alone. |
| R-ANA-008 | The analytics layer **shall** produce a **daily summary** and a **weekly summary** with the contents enumerated in `docs/REPORTING.md` "Daily and Weekly Summaries". The weekly summary **shall not** be a simple seven-day rollup of the daily summary — it **shall** additionally include trend-in-trustworthiness and required-operator-actions sections. | Snapshot test against a fixture week's trade log; the weekly output contains fields absent from daily outputs. |
| R-ANA-009 | The analytics layer **shall** surface paper/live divergences that meet any trigger in `docs/REPORTING.md` "Paper / Live Divergence — What to Surface", including accumulating-small-mismatches. Divergences below the triggers **shall not** be surfaced individually but **shall** still be persisted for later aggregation. | Tests: synthetic divergence of each trigger class surfaces in the CLI; a single explained small slippage does not surface individually but contributes to the accumulation trigger. |
| R-ANA-010 | The analytics layer **shall** support attribution slices by **strategy, symbol, regime, and holding period** (per `docs/REPORTING.md` "Attribution Slices") via CLI commands that do not require writing SQL or opening a notebook. | Test: each slice is reachable from a documented CLI command and returns a structured result. |
| R-ANA-011 | Phase 1 **shall** produce every chart in `docs/REPORTING.md` "Essential charts". Nice-to-have charts are deferred and **shall not** block Phase 1 Success Criteria. | Test: the report-generation command writes one image (or structured equivalent) per essential chart. |

---

## Domain 7 — CLI (`src/milodex/cli/`)

### Purpose
The primary interaction surface. Every operator action supported in Phase 1 is reachable from the CLI.

> **See also:** [`docs/CLI_UX.md`](CLI_UX.md) specifies the five most-used commands that define Milodex's product identity, the ideal daily operator workflow, the output-priority order (readability → auditability → speed), the JSON contract a future GUI or script can rely on, which commands require preview-before-commit, which require confirmation prompts, how errors are phrased under stress, what a good `status` command contains, and the decision context that must be visible before any submit. Requirements R-CLI-015 through R-CLI-021 below refer to it.

### User Stories
- *As the operator,* I run `milodex status` as my anchor command — it shows trading mode, kill-switch state, market clock, account balance, and open positions in one view.
- *As the operator,* I can preview any trade before submitting it, and I get clear per-check output showing which risk evaluations would pass or fail.
- *As the operator,* when a command fails, the error message tells me *which check failed and why* — not a stack trace.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-CLI-001 | The CLI **shall** expose `milodex status` returning mode, kill-switch state, market clock, account summary, open positions. | Snapshot test against mocked broker. |
| R-CLI-002 | The CLI **shall** expose `milodex positions` and `milodex orders`. | Output schema test. |
| R-CLI-003 | The CLI **shall** expose `milodex data bars <symbols> <timeframe> <start> <end>` that fetches and caches bars. | Integration test: second invocation hits cache. |
| R-CLI-004 | The CLI **shall** expose `milodex config validate <path>` that validates a strategy YAML against the schema. | Test: valid config → exit 0, invalid → exit nonzero with structured error. |
| R-CLI-005 | The CLI **shall** expose `milodex trade preview` and `milodex trade submit --paper` with matching argument surfaces. | Test: `preview` output and `submit` dry-run output are structurally identical. |
| R-CLI-006 | The CLI **shall** expose `milodex trade order-status`, `milodex trade cancel`, and `milodex trade kill-switch status`. | Command coverage test. |
| R-CLI-007 | Risk-rejection errors **shall** print a per-check pass/fail table, not raw exceptions. | Test: injecting a kill-switch-active state prints the table with "kill switch: FAIL". |
| R-CLI-008 | The CLI **shall** refuse to run destructive commands (e.g., `trade submit`) if required `.env` values are missing. | Test: unset `ALPACA_API_KEY` causes early, clear failure. |
| R-CLI-009 | Every CLI command **shall** support a `--json` flag that emits structured, machine-readable output for programmatic consumers. Human and JSON output are produced by a shared formatter abstraction so that either backend can be removed without rewriting commands. See ADR 0014. | Test: `milodex status --json` output parses as valid JSON; removing the JSON formatter from the code base breaks only the JSON tests, not the command tests. |
| R-CLI-010 | Process exit codes **shall** follow this convention: `0` success · `1` generic error · `2` risk-rejected trade · `3` invalid configuration · `4` broker unavailable · `5` kill-switch active · `6` missing credentials. Each non-zero exit **shall** also print a single-line reason on stderr in both human and JSON modes. | Matrix test: each defined failure injected at the CLI layer returns the declared exit code. |
| R-CLI-011 | The CLI **shall** support global verbosity flags: `-v` (DEBUG), default (INFO), `--quiet` (WARN and above). Flags apply to every subcommand without per-command plumbing. | Test: `-v` emits DEBUG log lines that `--quiet` suppresses; both are compatible with `--json` (JSON output is unaffected; log stream changes). |
| R-CLI-012 | The CLI **shall** expose a **primary trust report** command (e.g., `milodex trust` or `milodex overview`) that answers, in one view, the questions enumerated in `docs/REPORTING.md` "The Primary Trust Report": which strategies are active/halted/paper/review, what each is doing or expects next, whether anything drifted, whether any warnings/incidents exist, and whether the operator should intervene. | Snapshot test: output contains a section for each of the five question areas; a missing section fails the test. |
| R-CLI-013 | The CLI **shall** show directly the items under `docs/REPORTING.md` "Show directly in the CLI" and **shall** defer to exports (per R-ANA-004) the items under "Use exports for". Full backtest reports, complete trade ledgers, and archival evidence packages **shall not** be primary CLI output. | Test: `milodex status` and `milodex trust` outputs stay within the documented direct-show set; full trade ledger is reachable only via the export command. |
| R-CLI-014 | Every CLI surface that presents strategy performance, promotion candidacy, or trade reasoning **shall** label uncertainty explicitly using the vocabulary in `docs/REPORTING.md` "How Milodex Presents Uncertainty" (e.g., "insufficient evidence", "low confidence", "review required", "behavior diverged from expectation", "paper evidence not yet strong enough for promotion"). Whenever a low-confidence label is shown, the reason **shall** also be shown (e.g., low trade count, stale data, parameter sensitivity). No CLI surface **shall** present a polished summary that omits known uncertainty. | Test: a strategy with < 30 trades renders "insufficient evidence: trade count 14 < 30" rather than a bare Sharpe value; a surface that hides the label when present fails the test. |
| R-CLI-015 | The CLI **shall** expose the five most-used commands as first-class, discoverable entry points per `docs/CLI_UX.md` "The Five Most-Used Commands": `status`, `preview`, `report`, `reconcile`, and a daily-workflow command. These commands **shall** appear at the top of `--help`, each with a one-line purpose line that matches the doc. | Test: `milodex --help` lists all five above any secondary commands; help strings match the doc's one-liners. |
| R-CLI-016 | Default human-readable CLI output **shall** optimize in the priority order **readability → auditability → speed**, per `docs/CLI_UX.md` "Output Priorities". Under stress conditions (kill-switch active, reconciliation failure, broker outage), output **shall** switch to direct, explicit layouts rather than stylized or condensed ones. | Test: a stress-mode status call (kill-switch active) renders every field in its explicit form (no truncation, no ANSI color collapse that hides warnings); a style test fails any surface that hides a warning under normal styling. |
| R-CLI-017 | Every `--json` payload **shall** include, at minimum, the fields enumerated in `docs/CLI_UX.md` "JSON Output Contract": command name, UTC ISO-8601 timestamp, success/failure status, machine error code (when applicable), strategy instance IDs and config fingerprints (when relevant), stage, data freshness state, broker connectivity state, structured warnings and blockers arrays, decision summaries, audit record references, and a human-readable summary field. Breaking changes to the JSON schema **shall** require an ADR. | Schema test against every `--json` command; a CI check fails a PR that changes the schema without a matching ADR. |
| R-CLI-018 | The CLI **shall** offer a `--preview` (or equivalent distinct subcommand) for every command that can change exposure, strategy state, or governance state — matching the list in `docs/CLI_UX.md` "Preview-Before-Commit". A commit path with no preview counterpart **shall** fail a CLI-structure test. Preview invocations **shall** write preview audit records per R-OPS-011 / R-ANA-007. | Tests: every listed command has a preview variant; a synthetic commit-only command added to the codebase is flagged by the structure test. |
| R-CLI-019 | Commands in `docs/CLI_UX.md` "Require a confirmation prompt" **shall** prompt the operator before proceeding and **shall** be bypassable only by an explicit `--yes` flag. Commands in "Stay non-interactive" **shall** never prompt. Use of `--yes` **shall** be recorded in the command's explanation record (R-XC-008) as an auditable fact. | Tests: each consequential command prompts without `--yes` and succeeds with it; `--yes` appears in the resulting audit record; scripted invocations of read-only commands never hang waiting for input. |
| R-CLI-020 | Every non-trivial error surface **shall** answer four questions per `docs/CLI_UX.md` "Error Phrasing Under Stress": **what failed, why, what Milodex did in response, what the operator should do next**. Generic error strings ("something went wrong", raw stack traces at the top level) **shall** be refused by a CI lint. The per-check pass/fail table (R-CLI-007) remains the concrete format for risk-rejected trades. | Tests: each defined failure class from R-CLI-010 renders output that contains all four answers (via a structural check on the error payload, not substring matching). |
| R-CLI-021 | Before any submit-capable command is allowed to proceed, the CLI **shall** display the full decision context enumerated in `docs/CLI_UX.md` "Decision Context Required Before Any Submit". If any field cannot be determined (e.g., reconciliation state unknown), the submit **shall** be refused — absence of context is itself a hard stop, consistent with `docs/RISK_POLICY.md` "block on uncertainty". | Test: a synthetic submit with unknown reconciliation state is refused with a structured error naming the missing field; a successful submit path renders every listed context field. |

---

## Domain 8 — Promotion Pipeline

### Purpose
Encode the progression `backtest → paper → micro_live → live` as enforced state, not convention. Skipping stages is not possible.

> **See also:** [`docs/PROMOTION_GOVERNANCE.md`](PROMOTION_GOVERNANCE.md) specifies the contents of each promotion evidence package, the fields required on every promotion review artifact, the events that force disablement or review, the rules for reversing a decision, and the experiment registry that preserves rejected and abandoned strategies. Requirements R-PRM-007 through R-PRM-011 below refer to it.

### User Stories
- *As the operator,* I can see a strategy's current stage and what evidence it needs to advance.
- *As the operator,* I can advance a strategy's stage only when thresholds are met; advancing to `micro_live` or `live` additionally requires explicit approval.
- *As the risk layer,* I can refuse a trade whose originating strategy's stage doesn't allow that trade's destination.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-PRM-001 | Each strategy config **shall** declare a `stage` in `{backtest, paper, micro_live, live}`. | Schema test. |
| R-PRM-002 | The risk layer **shall** reject any order whose strategy stage does not permit that order's destination (e.g., `stage: backtest` cannot submit paper orders; `stage: paper` cannot submit live orders). | Unit test per stage/destination matrix cell. |
| R-PRM-003 | Stage transitions **shall** be advisory-recorded in a promotion log with timestamp and evidence snapshot (metrics at time of transition). | Log schema test. |
| R-PRM-004 | Advancing a **research-target** strategy to `paper` **shall** require the backtest metrics to meet thresholds: Sharpe ≥ 0.5, max drawdown ≤ 15%, ≥ 30 trades. The **lifecycle-proof** strategy (per Key Terms) is exempt from these statistical thresholds because a regime strategy cannot produce 30 trades in a realistic backtest window; its `paper` gate requires instead (a) a successful deterministic backtest run, (b) explanation records (R-XC-008) generated for every simulated signal, and (c) the risk layer having rejected at least one synthetic fault-injection trade. Both gate types **shall** be recorded in the `promotion_log`. | Test: a research-target strategy with worse statistical metrics is refused at promotion; the lifecycle-proof strategy is allowed to advance on its operational gate without meeting the trade-count threshold. |
| R-PRM-005 | Advancing to `micro_live` or `live` **shall** require explicit operator confirmation via a distinct CLI command, not just a config edit. | Test: editing `stage: live` in the YAML alone does not enable live trading; the promotion command must also run. |
| R-PRM-006 | Phase 1 **shall not** enable `micro_live` or `live` stages end-to-end. Requirements R-PRM-002, R-PRM-004, R-PRM-005 apply, but actual live-capital submission is out of scope for phase one. | Code review + test: attempting `live` stage hits R-EXE-007 hard stop. |
| R-PRM-007 | Every stage transition (promotion, demotion, or reversal) **shall** attach a **promotion review artifact** whose contents satisfy the fields enumerated in `docs/PROMOTION_GOVERNANCE.md` ("Approval Authority and Governance Artifacts"). Artifacts **shall** be persisted to `promotion_log` (per R-ANA-001a) and **shall not** be editable once written — corrections take the form of a new event referencing the prior one. | Schema test on the artifact payload; integration test that attempts to mutate a prior row fail closed. |
| R-PRM-008 | Advancement to `paper` **shall** be refused unless a complete backtest→paper **evidence package** (per `docs/PROMOTION_GOVERNANCE.md`) is attached to the review artifact. Missing fields **shall** cause the CLI promotion command to fail with a per-field pass/fail list (consistent with R-CLI-007 style). | Test: a promotion attempt missing a required evidence field is refused and the error enumerates the missing field(s). |
| R-PRM-009 | A strategy **may** be automatically disabled (set `enabled: false`) or automatically marked for review when any event from "Demotion and Disablement" in `docs/PROMOTION_GOVERNANCE.md` fires, but the strategy's lifecycle **stage shall not** change without an explicit operator-confirmed demotion artifact. Auto-disable and stage demotion are distinct operations. | Test: a kill-switch trip or divergence-threshold breach disables the strategy and writes a review-required event, but the `stage` field in config and in `promotion_log` remains unchanged until an operator demotion command runs. |
| R-PRM-010 | Reversal of a prior promotion or demotion **shall** be recorded as a new `promotion_log` event referencing the original decision; prior rows **shall not** be altered. The reversal artifact **shall** contain the fields listed under "Reversibility of Promotion Decisions" in `docs/PROMOTION_GOVERNANCE.md`. | Integration test: reversing a promotion leaves the original row intact and writes a new row whose `reverses_event_id` column points at the original. |
| R-PRM-011 | Milodex **shall** maintain an experiment registry covering promoted, rejected, failed, inconclusive, and abandoned strategy instances, per "Experiment Registry" in `docs/PROMOTION_GOVERNANCE.md`. A strategy instance **shall not** be deleted from durable state; instead its registry entry records its terminal status. The CLI **shall** expose a command that lists registry entries filterable by terminal status. | Test: rejecting a strategy writes a registry entry with the required fields; the CLI listing returns it under the `rejected` filter; no delete path exists in the code. |

---

## Domain 9 — Runtime & Operations

### Purpose
Define Milodex's day-to-day operating model: a scheduled daily workflow with light continuous monitoring. Cover startup, shutdown, reconciliation, degraded-mode behavior, concurrency, command-safety, idempotency, and audit coverage.

> **See also:** [`docs/OPERATIONS.md`](OPERATIONS.md) specifies the daily-schedule windows, the mandatory startup/shutdown checklists, the reconciliation field set, the three degraded-mode policies (broker down; data available + broker down; broker up + data stale), the safe-anytime vs market-hours-required command classification, the idempotency list, and the preview/submit audit-record field sets. Requirements R-OPS-001 through R-OPS-010 below refer to it.

### User Stories
- *As the operator,* I can start Milodex once per day, have it reconcile cleanly, run the post-close workflow, and leave it safely until the next session.
- *As the operator,* if the broker is down or data is stale mid-workflow, I want Milodex to fail safe (block exposure-increasing actions, preserve state, log the incident) rather than guess.
- *As a future reviewer,* I can reconstruct what Milodex did and why from durable audit records alone — no transient in-memory context required.

### System Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-OPS-001 | Milodex **shall** operate as a scheduled daily workflow with four workflow windows (pre-market, open, post-close, end-of-day reporting) per `docs/OPERATIONS.md` "Daily Schedule". Window offsets **shall** be configurable; an always-on autonomous daemon is Phase 2+ (per the Phase 2 appendix). | Test: each window produces its documented durable artifact when invoked; no runtime component assumes an intraday decision cadence. |
| R-OPS-002 | Every startup **shall** execute the full startup checklist in `docs/OPERATIONS.md` "Startup" before enabling any submit-capable command. A startup event **shall** be persisted to the audit log. Failure of any required step **shall** leave the system in a startup-failed state that blocks sensitive commands. | Integration test: a synthetic broker outage at startup blocks `trade submit` with a structured error; the audit log contains a startup event with the failed step named. |
| R-OPS-003 | Every shutdown (controlled stop, kill switch, or clean exit) **shall** flush logs, persist workflow state, record unresolved orders and incidents, write a shutdown event, and close external connections. Kill-switch shutdown has weaker state-flush guarantees than controlled stop (per R-STR-010) but **shall not** skip the audit-event write. | Integration test: each shutdown path produces a shutdown event in the audit log; controlled stop additionally produces a flushed state snapshot; kill-switch produces at minimum the audit event and cancelled-orders record. |
| R-OPS-004 | Local-vs-broker state reconciliation **shall** cover the fields enumerated in `docs/OPERATIONS.md` "State Reconciliation". On any execution-critical mismatch, exposure-increasing actions **shall** be blocked until the mismatch is resolved, and the mismatch **shall** be logged as an incident. | Test: synthetic position disagreement between local state and the broker blocks the next exposure-increasing submit and writes a reconciliation-incident record; reducing orders remain permitted per R-EXE-016. |
| R-OPS-005 | On broker connectivity failure mid-workflow, Milodex **shall** stop new exposure-increasing actions, preserve workflow state, mark the workflow degraded, log an incident, permit read-only analysis, retry per a conservative policy, and require a successful reconciliation before re-enabling submits. No auto-resume path **shall** bypass reconciliation. | Integration test: injecting a broker outage mid-workflow produces the documented transition; first submit attempt after restored connectivity is refused until reconciliation has run. |
| R-OPS-006 | When data is available but the broker is down, Milodex **shall** permit research, reporting, and preview generation, **shall** block all submit-capable actions, and **shall** mark every output produced in this mode as non-executable in its audit record. | Test: preview invoked during broker-down mode returns a result flagged `executable: false`; submit is refused with a structured error referencing the broker-down mode. |
| R-OPS-007 | When the broker is available but required data is stale or unverifiable, Milodex **shall** block all exposure-increasing decisions that depend on that data, permit reconciliation and broker-safe inspection, and move into a blocked / review-required workflow state when freshness is a hard requirement. Stale-data blocks **shall** appear in the preview per-check table (R-CLI-007). | Test: a synthetic stale-data condition blocks an otherwise-valid submit and surfaces the block in the per-check output; reporting commands in the same session still succeed. |
| R-OPS-008 | Concurrency **shall** follow the Phase 1 model in `docs/OPERATIONS.md` "Concurrency Model": read-only tasks may run concurrently; state-changing operations (submit, reconcile, promote, demote, kill-switch handling, config changes) **shall** be serialized via advisory locks under `data/locks/` (per ADR 0018). Attempting a locked operation while another holds the lock **shall** fail with a clear error naming the holder. | Test: two concurrent `trade submit` invocations produce exactly one submission and one structured lock-conflict error; two concurrent `status` invocations both succeed. |
| R-OPS-009 | Commands **shall** be classified as "safe anytime" or "requires market-hours/workflow-readiness" per `docs/OPERATIONS.md` "Command Safety Classification". The CLI **shall** refuse second-class commands with a structured error when preconditions (reconciliation clean, data fresh, broker reachable, kill switch inactive at the relevant scope) are not met. | Test: `milodex status` succeeds in all states; `milodex trade submit --paper` is refused when reconciliation has not passed this startup, with the unmet precondition named in the error. |
| R-OPS-010 | The operations in `docs/OPERATIONS.md` "Idempotency Guarantees" **shall** be idempotent — repeated invocation **shall** either produce the same safe result or explicitly refuse the second attempt without state corruption. Idempotency **shall** be enforced by durable keys (content hashes, request IDs, per-window uniqueness constraints), not by in-memory deduplication. | Tests: running `milodex reconcile` twice produces identical durable state on the second run; re-invoking a promotion command for the same (strategy, from-stage, to-stage) produces either an exact-match refusal or a no-op with a linked reference to the prior event. |
| R-OPS-011 | Every preview **shall** persist an audit record containing the fields enumerated in `docs/OPERATIONS.md` "Preview audit record". Every submit **shall** persist an audit record that is a superset, adding the fields in "Submit audit record". Submit records **shall** link by ID to the preview they originated from when one exists, so the path considered → proposed → approved → submitted → filled is reconstructable from durable state alone. | Schema test on both record types; end-to-end test reconstructs a full preview-to-fill chain from the database without replaying any Python. |

---

## Cross-Cutting Requirements

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| R-XC-001 | Secrets (Alpaca keys) **shall** be loaded exclusively from `.env`. They **shall not** be printed to logs, error messages, or test output. | Log scrape test on all CLI commands. |
| R-XC-002 | All modules **shall** conform to line length 100, ruff rule sets `E, F, I, N, W, UP`. | `ruff check` exit 0. |
| R-XC-003 | The `tests/milodex/` directory structure **shall** mirror `src/milodex/`. | Directory diff test. |
| R-XC-004 | The `RiskEvaluator` **shall** have the highest unit-test coverage of any module, reflecting its role as the highest-stakes layer. | Coverage report: `execution/risk.py` coverage ≥ project average + 10 points. |
| R-XC-005 | The `logs/` directory **shall** be gitignored except for `.gitkeep`. | `git check-ignore logs/foo.log` returns true. |
| R-XC-006 | Durable Milodex-authoritative state **shall** live under `data/` (per ADR 0018, superseding the original `state/` layout): `data/milodex.db` (SQLite event store per ADR 0011 — trade log, promotion log, kill-switch events, strategy runs, backtest runs, explanation records), `data/locks/` (single-process advisory lock files per R-EXE-013), and `data/strategies/<strategy_id>.json` (per-strategy state flush per R-STR-010; reserved, not yet populated). Kill-switch state is held inside the event store, not in a top-level JSON file. The `data/` directory **shall** be gitignored except for `.gitkeep`; paths **shall** be resolved through `milodex.config.get_data_dir()` and `milodex.config.get_locks_dir()` (overridable via `MILODEX_DATA_DIR` and `MILODEX_LOCKS_DIR`). | `git check-ignore data/milodex.db` returns true; `get_data_dir()` and `get_locks_dir()` resolve to the documented paths on a fresh clone. |
| R-XC-007 | All internal timestamps **shall** be stored and manipulated in UTC (ISO-8601 with `+00:00` suffix). CLI display **shall** convert to America/New_York (market time zone). No module stores local-time timestamps. | Grep/lint: `datetime.now()` without `tz=timezone.utc` is flagged in code review; display formatters carry an explicit tz conversion. |
| R-XC-008 | Every meaningful decision — strategy signal, risk block or allow, order submission, stage promotion, kill-switch trip — **shall** persist an *explanation record* capturing: the strategy instance, the triggering data or event, the rule or threshold evaluated, the current risk state, the resulting action, any alternatives rejected or not taken, and whether human approval was required or bypassed. Explanation records **shall** be reconstructable from the SQLite event store alone, without re-running Python. | Schema test on the explanation-record payload stored in `trades.reasoning` (per R-ANA-001), `promotion_log`, and kill-switch event rows. An end-to-end test reconstructs the full reasoning chain for one submitted trade and one promotion from the DB alone. |
| R-XC-009 | Phase 1 **shall** support at most **5** concurrent experiments (a running or under-review strategy instance counts as one experiment) and **shall** operationally target no more than **2** at a time. The CLI **shall** refuse to start a sixth experiment with a clear error naming the active four or five. Concurrent execution remains governed by R-EXE-013 (single-strategy *runtime*); this cap bounds the research *backlog* across backtest, paper, and promotion review. | Test: a sixth experiment creation is refused; listing experiments surfaces the current count against the cap. |
| R-XC-010 | A **split source-of-truth** model applies: the broker state of record wins for execution reality (orders, fills, positions, submission-time tradability); the canonical research data provider wins for historical analytics (backtests, research bars, reference metadata, corporate-action-aware history). At trade time, if the market-data-feed view and the broker-adjacent view materially disagree beyond a configured tolerance (price > 50 bps, tradability flag mismatch, market-session state mismatch, or other rule in `configs/risk_defaults.yaml`), the system **shall** block submission, surface both values in the error, and require operator review. The system **shall not** silently choose one side. See ADR 0017. | Test: synthetic provider/broker disagreement exceeding the configured tolerance produces a structured block at submit time with both values logged; a disagreement within tolerance produces a logged warning in the explanation record but does not block. |
| R-XC-011 | Business logic **shall** live in authoritative domain modules (strategy, risk, execution eligibility, promotion policy, audit/governance); orchestration modules (CLI handlers, workflow runners, broker/data adapters, reporting wiring, startup/shutdown) **shall** coordinate only and **shall not** invent policy. See `docs/ENGINEERING_STANDARDS.md` "Authoritative vs Orchestration Modules". | Code review / lint: checks that CLI/adapter/orchestration modules do not implement threshold math, risk decisions, or promotion rules. A lint rule flags business-logic constructs in orchestration paths. |
| R-XC-012 | A dedicated **application / service layer** **shall** sit between the CLI (and any future GUI) and the domain modules for every meaningful workflow (preview, submit, reconcile, promote, demote, report generation, incident handling). The CLI **shall not** reach directly into raw business logic or infrastructure for these workflows. | Code review: CLI command handlers invoke service-layer entry points; no CLI module imports from a domain module's internal submodules directly. Integration test: the same service-layer call used by the CLI can be driven by a test without instantiating the CLI. |
| R-XC-013 | State placement **shall** follow `docs/ENGINEERING_STANDARDS.md` "State: SQLite vs Files vs Never-Persisted": durable operational state in SQLite, larger/portable artifacts in files under documented directories, and the never-persisted list **shall** never be written to logs, exports, or durable state. | Grep/lint: no secret-like patterns in logs or exports; a schema test verifies documented SQLite tables exist; a CI check verifies no undocumented table is created by a migration. |
| R-XC-014 | Configs, schemas, and state migrations **shall** be versioned explicitly and independently per `docs/ENGINEERING_STANDARDS.md` "Versioning". Every run and every promotion artifact **shall** record the config version, schema version, and migration state it depended on. Material format changes **shall** be migrated explicitly or rejected clearly — **no silent upgrades**. | Tests: a run row includes config/schema/migration fields; a migration test fails on any ordering, skipped, or implicit migration; loading an out-of-date config produces a structured "migration required" error rather than silently coercing. |
| R-XC-015 | Before a feature is marked done, the applicable test categories from `docs/ENGINEERING_STANDARDS.md` "Mandatory Tests Before 'Done'" **shall** exist: unit, integration, workflow, failure-path, idempotency, and regression. PR review **shall** refuse a feature lacking the applicable categories. | Review policy documented in `CLAUDE.md` / `AGENTS.md`; CI surface lists test categories per feature-scoped test directory for spot-check. |
| R-XC-016 | Any module, command, or doc representing a feature that is not yet fully implemented per `docs/ENGINEERING_STANDARDS.md` "Scaffolded vs Implemented" **shall** be labeled as scaffolded — in code comments, CLI help strings, and the relevant doc. A feature **shall not** be called implemented until every listed criterion is true. | Grep test: any scaffolded function is tagged with a structured marker (e.g., `# scaffolded`) that CI tallies; the Phase 1 Success Criteria test refuses "done" until no scaffolded markers remain in critical paths. |
| R-XC-017 | No surface (CLI output, docs, README, future GUI, error messages) **shall** present Milodex as financial advice, imply guaranteed returns or safety, hide uncertainty or weak evidence, enable risky modes by default, claim that the founder's preferences are universal best practices, or take live-capital actions without explicit user consent and setup. See `docs/DISTRIBUTION.md` "What Milodex Clearly Refuses". | Content lint + review: CI scans CLI help, README, and doc text for forbidden claim patterns (e.g., "guaranteed", "risk-free", "the right way", "automatic live trading"); a failing match blocks merge. A first-run test confirms defaults place the operator in paper mode with kill switches enabled and live trading disabled. |
| R-XC-018 | When Milodex is installed by someone other than the founder (Phase 2+ installer or manual clone), the first-run experience **shall** display the onboarding warnings enumerated in `docs/DISTRIBUTION.md` "Onboarding Warnings" before enabling any submit-capable command, and the shipping defaults **shall** match `docs/DISTRIBUTION.md` "Safe-Default Shipping Profile" (paper mode, live disabled, conservative sizing, exposure cap, kill switches enabled, preview-before-commit, strong auditability, clear status surfaces). Any change to the onboarding warning text **shall** require an ADR. | First-run test: a clean install blocks `trade submit` until the onboarding warning acknowledgment is recorded in durable state; the default config-state matches the safe-default profile; a PR that edits onboarding warning text without a matching ADR fails CI. |

---

## Phase 1 Success Criteria

Phase 1 is complete — and only complete — when all of the following are simultaneously true:

1. Both a **lifecycle-proof strategy** (SPY/SHY 200-DMA) and a **mean-reversion research-target strategy** are defined entirely in `configs/*.yaml` files.
2. Each strategy can be backtested from the CLI over a multi-year historical range. The mean-reversion strategy produces all core metrics; the lifecycle-proof strategy produces deterministic output and explanation records per R-XC-008.
3. Each strategy, unchanged, runs in paper mode against Alpaca and submits real paper orders when its rule fires.
4. The `RiskEvaluator` has rejected at least one real attempted trade during development (evidence that the layer works on something other than synthetic tests).
5. The kill switch has been manually triggered, verified to halt trading, and verified to require explicit reset.
6. The operator can answer "is this strategy making or losing money, and how does it compare to SPY?" from the CLI alone.

"Tests pass" is necessary but not sufficient. The goal is a living, paper-trading strategy whose performance the operator can evaluate honestly.

---

## Appendix — Phase 2+ Future Scope

Preserved here as declared future intent. **Not in scope for phase one.** Do not implement against these requirements yet; do not second-guess phase-one architecture on their behalf.

### Concurrent Multi-Strategy Execution
- Lift the Phase 1 single-strategy restriction (R-EXE-013). Multiple strategies run simultaneously against the same Alpaca account.
- Portfolio-level risk checks (exposure, concurrent positions) continue to apply globally; per-strategy risk stays scoped to the strategy.
- **Capital allocation model (proposed):** a single shared pool, with each strategy's allocation weighted by its validated reliability (Sharpe, drawdown stability, trade count). High-confidence strategies receive a larger slice; new strategies start small and earn share. This is directional intent for Phase 2+, not a committed design.
- Controlled-stop and kill-switch semantics extend: controlled stop can target a single strategy or all strategies; kill switch remains global.

### Daemon / Supervisor Runtime
- Lift the manual-foreground restriction (R-STR-007). The strategy runtime runs as a long-lived supervised process, auto-restarting on crash, wake-from-sleep aware, with a GUI or Web UI as the operator's primary surface.
- Requires rethinking the SIGINT-driven shutdown dialog (a daemon has no stdin).

### Crypto & Alternative Assets
- Extend `BrokerClient` with a crypto-capable implementation (likely still Alpaca given its crypto support, but the abstraction permits swap).
- Universe filtering must handle 24/7 markets; market-hours risk check becomes per-asset-class.

### ML-Driven Signals
- Signal generation via trained models (feature extraction, training pipeline, model registry, drift monitoring).
- Thresholds may tighten: ML strategies require more paper trades before promotion due to higher overfit risk.

### Alternative / Sentiment Data
- News, social, macro indicators. `DataProvider` abstraction extends to non-bar data types.
- Paid data sources require evidence of edge-beyond-cost before adoption.

### Desktop GUI
- PySide6 or Tauri (decision deferred). Primary use case: visual strategy performance monitoring and report browsing.
- CLI remains first-class; GUI is a view layer, not a replacement.
- **Readiness gate:** the GUI **shall not** be started until all conditions in `docs/ENGINEERING_STANDARDS.md` "GUI Readiness Gate" are true (stable CLI workflow; trustworthy preview/submit/reconcile/reporting; risk and kill-switch integration-tested; durable state and audit logging in place; frozen-enough config/promotion workflows; stable JSON / service-layer contracts; the founder can stand behind the system without the GUI). The GUI is not a tool to compensate for unclear core behavior.

### Alternative Brokers
- The `BrokerClient` ABC is designed for this. Candidates evaluated only when Alpaca proves insufficient (e.g., instrument coverage, execution quality).

### Distributable Installer
- Friends can `pip install` or run a Windows installer. Configs and secrets stay local per-user.
- Requires stability of the public CLI surface — not started until phase-one strategies are validated.
- **Shareability posture applies from Phase 1.** `docs/DISTRIBUTION.md` defines what Milodex takes responsibility for, what it clearly refuses, the onboarding warnings any installer or first-run flow must display, the safe-default shipping profile (paper by default, live disabled, conservative sizing, kill switches enabled), acceptable secrets-and-config flows, and the areas that remain openly opinionated rather than pretending to be universal truth. The Phase 1 requirements R-XC-017 and R-XC-018 already bind this posture before the packaged installer lands, so the project is intentionally shareable from day one rather than retrofitted later.
- **Install-ergonomics ambition:** near one-click / very low-friction install eventually; for Phase 1, developer-oriented but very well-documented setup is sufficient, provided it is clean, fast, and realistic for another person to follow without guesswork.

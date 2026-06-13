# Milodex — Domain Context

The shared vocabulary of Milodex, a personal autonomous trading system. This is the **domain map** an agent or new reader should read first: each term gives the one concept it names, the **module that owns it**, and the **ADR** that decided it. It deliberately omits general programming concepts — only terms specific to this system's domain belong here.

Architecture vocabulary (module / interface / depth / seam / adapter / leverage / locality) lives in the `improve-codebase-architecture` skill, not here. This file is the *domain* language; that skill is the *structure* language.

## Language

### Strategy

**Strategy (promotable strategy instance)**:
One fully-defined version of a trading strategy carrying its own type, universe, tempo, signal parameters, sizing, risk limits, and promotion stage. The unit of configuration, backtest, promotion, and audit.
Owner: `strategies/` · ADR-0003, ADR-0015 · _Avoid_: "the algo", "model" (a Strategy may contain a model, it is not one).

**Strategy id**:
The canonical identity string `{family}.{template}.{variant}.v{version}` (e.g. `meanrev.rsi2.pullback.v1`). Must equal that composed form; new family strings are accepted (no allowlist).
Owner: `strategies/loader.py` · ADR-0015 · _Avoid_: "strategy name" (that is presentation metadata, ADR-0041).

**Family / Template / Variant**:
The three taxonomy axes of a Strategy id — edge family (momentum / mean-reversion / breakout), the template within it, and the parameter variant.
Owner: `strategies/loader.py` · _Avoid_: "category", "type" used loosely.

**Lifecycle-proof (regime) strategy**:
The single SPY/SHY 200-DMA regime Strategy used to prove the *platform* end-to-end. Gated on operational milestones, not statistics — exempt from the trade-count / Sharpe thresholds because a regime strategy emits only 1–3 signals/year.
Owner: `strategies/` + `promotion/policy.py` · ADR-0015, ADR-0052 · _Avoid_: "the benchmark strategy".

**Research-target (edge-family) strategy**:
A Strategy intended to discover real edge; subject to the stage-aware statistical thresholds in the capital-readiness gate.
Owner: `promotion/policy.py` · ADR-0052 · _Avoid_: "edge strategy" used as if promoted.

**Strategy bank**:
The set of all Strategies and their canonical state (what's at paper, what's blocked at backtest, and why). Holds exactly one Lifecycle-proof strategy plus N Research-target strategies.
Owner: `docs/STRATEGY_BANK.md` (state) · _Avoid_: "portfolio" (that is held positions, not the catalog).

**Tempo / bar size**:
The cadence a Strategy trades at. `tempo.bar_size` (`1D`, `5Min`, …) drives backtest-engine dispatch.
Owner: `strategies/` + `backtesting/engine.py` · _Avoid_: "timeframe" except for the derived `Timeframe` enum.

**Universe**:
The declared instruments a Strategy may act on; also the rotation scope.
Owner: `strategies/` · ADR-0022 · _Avoid_: "watchlist", "symbols" (informal).

### Risk

**Risk layer (the veto)**:
The enforcement Module that evaluates every Intent and can veto it. Strategy proposes, risk disposes. Never bypassed or weakened.
Owner: `risk/evaluator.py` · ADR-0008, ADR-0019 · _Avoid_: "risk check", "validation" (singular — it is the authority, not a check).

**Risk profile**:
A bounded, opted-into, auditable *operator preference* over risk posture — selected within non-negotiable account-level guardrails. The operator owns preferences; the risk layer owns enforcement. Never "the user controls the risk layer".
Owner: `risk/` · ADR-0054 · _Avoid_: "risk settings", "user risk config".

**Kill switch**:
A halt: cancel all open orders, persist halt state, **manual reset required**. Account-scoped today.
Owner: `execution/state.py` (`KillSwitchStateStore`) · ADR-0005 · _Avoid_: conflating with Controlled stop.

**Controlled stop**:
A graceful runner shutdown — state preserved, positions and orders untouched. Needs a live, cooperative runner to consume the request.
Owner: `strategies/` runtime + `operations/` · ADR-0012 · _Avoid_: "kill", "halt".

**Account-scoped cap vs per-strategy cap**:
The account-level position/exposure limit is authoritative and attribution-free; per-strategy caps constrain only that Strategy's attributed positions. The account cap is the only check that can block an intent a sibling's positions already filled against.
Owner: `risk/evaluator.py` · ADR-0024, ADR-0029.

**Disable conditions**:
Per-Strategy auto-halt predicates (drawdown breach, data-quality) evaluated alongside the veto; a breach halts the Strategy (R-STR-014).
Owner: `risk/disable_conditions.py` · _Avoid_: "circuit breaker".

### Execution

**Execution service (the chokepoint)**:
The single path from Intent → trade: it invokes the Risk layer, records the Explanation, and submits to the Broker. No code path reaches the broker except through here.
Owner: `execution/service.py` · ADR-0008 · _Avoid_: "order manager", "trade service" (it is *the* chokepoint, singular).

**Intent**:
A proposed trade from a Strategy, before the Risk layer rules on it.
Owner: `execution/` · _Avoid_: "signal" (a signal produces an Intent; they are not the same), "order" (an order is post-submit).

**Explanation (explanation record)**:
The durable audit payload recorded for every meaningful decision, keyed by `session_id`. Required by R-XC-008.
Owner: `core/event_store.py` · ADR-0011 · _Avoid_: "log", "note".

### Promotion

**Promotion stage**:
A Strategy's lifecycle position: `backtest → paper → micro_live → live`. No skipping. Distinct from a Bench stage *section* (UI grouping).
Owner: `promotion/state_machine.py` · ADR-0009, ADR-0039 · _Avoid_: using "stage" for the UI section without qualifying.

**Promotion gate**:
The two-tier rule a Strategy must pass to advance: a permissive paper-readiness tier and a stricter capital-readiness tier (post-paper), plus the Lifecycle-proof exemption. Thresholds are code invariants.
Owner: `promotion/policy.py` · ADR-0020, ADR-0052.

**Manifest (frozen manifest)**:
The immutable snapshot of a Strategy's configuration that binds at paper+ stages. The Risk layer reads back the active manifest hash to detect drift.
Owner: `promotion/manifest.py` + `core/event_store.py` · ADR-0015, ADR-0030 · _Avoid_: "config snapshot" (informal).

**Config hash (canonical hash)**:
The SHA-256 over the canonical-JSON encoding of a Strategy config; the identity the manifest-drift veto compares.
Owner: `strategies/loader.py` + `promotion/` · ADR-0015.

**Evidence / evidence freshness**:
The recorded proof backing a promotion decision; freshness is a distinct axis from promotion stage (stale evidence ≠ wrong stage).
Owner: `promotion/run_evidence.py` · ADR-0042, ADR-0050.

### Backtesting & data

**Backtest (walk-forward)**:
Out-of-sample-validated simulation; minimum 30 trades before statistical conclusions. Exploratory — the Manifest does not bind at backtest, only at paper+.
Owner: `backtesting/` · ADR-0021, ADR-0030 · _Avoid_: "sim" (ambiguous with the kernel).

**OOS aggregate**:
The out-of-sample aggregate block in a backtest run's `metadata_json` where walk-forward metrics live (not columns).
Owner: `backtesting/` + `core/event_store.py` · ADR-0021.

**Simulation kernel**:
The shared tick/mark-to-market engine the backtest engine drives for both daily and intraday replay.
Owner: `backtesting/simulation_kernel.py` · _Avoid_: "the engine" (the engine dispatches *to* the kernel).

**Market data provider / source hierarchy**:
The interface over market-data sources, tried free-first (Alpaca, Yahoo) before premium.
Owner: `data/` · ADR-0017, ADR-0001 · _Avoid_: "feed".

**Cache (parquet cache)**:
The on-disk parquet store of fetched bars. Cannot key a `/`-symbol as-is.
Owner: `data/cache.py` · ADR-0002.

### Runtime & operations

**Runner (paper runner)**:
The process that drives one Strategy live against paper: fetch → evaluate → (post-close, for daily) lock in → submit via the Execution service.
Owner: `strategies/runner.py` · ADR-0026.

**Session / session_id**:
One runner execution; the key that ties Explanations to the run that produced them.
Owner: `core/event_store.py` · _Avoid_: "run" used bare (see Strategy run).

**Strategy run**:
The event-store record of a runner execution. `ended_at IS NULL` means open/"running" (no liveness check — a dead one shows as phantom until bootstrap reconcile).
Owner: `core/event_store.py` (`strategy_runs`) · ADR-0011.

**Event store**:
The SQLite source of truth for trades, Explanations, kill-switch, Strategy runs, backtest runs, and the per-strategy position ledger. Durable state under `data/`.
Owner: `core/event_store.py` · ADR-0011, ADR-0010, ADR-0018 · _Avoid_: "database", "DB" when the *concept* (source of truth) is meant.

**Advisory lock**:
The file-based lock guarding single-runner-per-strategy and other exclusivity invariants.
Owner: `core/advisory_lock.py` · _Avoid_: "mutex", "file lock" (informal).

**Reconciliation**:
The out-of-trade-path routine comparing the per-strategy ledger against broker net and surfacing divergence as informational WARN; also closes phantom runs at bootstrap.
Owner: `operations/reconciliation.py` · ADR-0055, ADR-0032.

**Per-strategy position ledger / attribution**:
The strategy-scoped view of positions and open lots derived from the event store — *not* Alpaca net — used to give each runner its own position view when strategies share one account.
Owner: `risk/attribution.py` + `core/event_store.py` · ADR-0055, ADR-0029.

### Operator surface

**Operator**:
The developer who runs the system. Owns risk *preferences* (never enforcement). The autonomy boundary lists the actions that always require explicit human approval (promote to live, allocate real capital, re-enable after a kill switch, …).
Owner: `docs/VISION.md` "Autonomy Boundary" · _Avoid_: "user" when the privileged human is meant.

**Bench**:
The GUI promotion-pipeline surface (the Kanban of Strategies across stages). A Bench *stage section* is a UI grouping, distinct from a promotion stage.
Owner: `gui/` + `commands/bench.py` · ADR-0036, ADR-0039, ADR-0049 · _Avoid_: "board" without context.

**Read model**:
A GUI-side projection that reads the event store (mode=ro) to render a surface; carries no business rules.
Owner: `gui/` · ADR-0051 · _Avoid_: "view model" (Qt term, different layer).

## Relationships

- A **Strategy** advances through **Promotion stages** under the **Promotion gate**; at paper+ its config is frozen into a **Manifest**.
- A **Strategy** emits **Intents**; the **Execution service** routes each through the **Risk layer** (veto), records an **Explanation**, and submits an order.
- The **Risk layer** enforces; the **Operator** sets **Risk profile** preferences within guardrails — the relationship is never inverted.
- A **Runner** executes a **Strategy** as a **Strategy run**; **Reconciliation** compares its **per-strategy ledger** against broker net.
- A **Backtest** produces evidence (an **OOS aggregate**); the **Promotion gate** reads it; the **Manifest** binds only at paper+.
- The **Event store** is the source of truth under all of the above (trades, Explanations, runs, ledger, kill-switch).

## Example dialogue

> **Dev:** "When a **Strategy** at paper fires, does the **Runner** submit straight to Alpaca?"
> **Operator:** "No — the Runner produces an **Intent**, and only the **Execution service** reaches the broker. It calls the **Risk layer** first; if the veto passes it records an **Explanation** and submits."
> **Dev:** "And if the Strategy's config drifted from what we promoted?"
> **Operator:** "The veto reads back the active **Manifest** hash and blocks on a mismatch. The Strategy can't out-vote the Risk layer — it never can."

## Flagged ambiguities

- **"position"** meant both the broker's net position and a Strategy's attributed lot. Resolved: **broker net** (account-level, authoritative for account caps) vs **per-strategy ledger position** (attribution-only, ADR-0055). They diverge by design during same-symbol co-runs.
- **"stop"** meant both graceful shutdown and emergency halt. Resolved: **Controlled stop** (graceful, positions untouched) vs **Kill switch** (cancel orders, manual reset). Never interchangeable.
- **"stage"** meant both lifecycle position and a UI grouping. Resolved: **Promotion stage** (backtest/paper/micro_live/live) vs **Bench stage section** (presentation only, ADR-0039).
- **"strategy"** meant both the abstract idea and a configured version. Resolved: a **Strategy** in this document is always an *instance* (one config, one id, one stage).
- **"user" vs "operator"** — the privileged human is the **Operator**; "user" drifts toward implying control over enforcement, which is wrong (ADR-0054).
- **"name" vs "id"** — **Strategy id** is the canonical identity; a display name is presentation metadata (ADR-0041) and is never load-bearing.

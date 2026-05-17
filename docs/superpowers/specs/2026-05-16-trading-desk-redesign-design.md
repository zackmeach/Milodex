# The Trading Desk — Redesign & Live-Data Wiring

**Status:** Design approved (brainstorming), pending spec review + implementation plan
**Date:** 2026-05-16
**Scope:** Rewrite the DESK surface to a new 7-section information architecture and wire every section to live Milodex data. Phase 5, in the lineage of PR E ("paint a real surface with live data from `data/milodex.db`").

---

## 1. Problem & Goal

The current `DeskSurface.qml` (~1300 lines, 8 lettered sections) runs on inline mock data. It was an exploratory layout built before the operator knew how the system would actually be used. The operator has produced a refined visual + IA reference in Claude Design (a React/HTML mockup) — "The Trading Desk": 7 numbered sections covering performance, live operations, risk throughput, and market context on one working spread.

**Goal:** replace the temporary mock DESK surface with a redesigned QML surface that matches the new IA and is wired to real data, such that it both *looks right* and *functions properly* — every section reflects live Milodex state from `data/milodex.db` and existing services, with no invented numbers.

**Non-goal:** the React/HTML mockup is a visual + IA reference only. Production GUI is locked to PySide6 + Qt Quick (QML) per ADR 0033 (local-FastAPI+browser explicitly rejected). This work does not reopen that decision.

## 2. Locked Decisions (from brainstorming)

1. **Deliverable:** QML rewrite of `DeskSurface.qml`, wired to live data. React mockup = reference only.
2. **Scope boundary:** DESK surface only. `Main.qml` chrome (top-nav, Risk Office strip, kill banner) and the `FRONT`/`BENCH`/`LEDGER` surfaces are untouched. (Fallback: chrome changes permitted only if the vision genuinely requires them — to be raised explicitly if hit.)
3. **Today P/L semantics:** "Today" binds the existing `OperationalState` account `daily_pnl` (live-ish, ~15s broker poll). Week / Month / YTD / All-Paper are derived from `portfolio_snapshots`.
4. **Freshness posture:** timestamp ("as of") on the two EOD/daily-grained sections. A **stale flag** is applied to **Performance & Trust only** (it is operator-trust-bearing and names "Trust"); if its underlying snapshot is older than the staleness threshold the hero degrades to a muted "stale as of \<date\>" treatment instead of presenting an old number as current. Market Tape is timestamp-only (decorative; decisions are strategy/risk-gated, so stale market context cannot drive a bad trade).
5. **Soft-field definitions** (see §5 for full detail): cadence + heartbeat replace last/next-cycle; "needs review" = a tripped gate awaiting a human decision (three concrete cases); "underperforming" = below the prior-stage evidence that earned the current stage, gated by a minimum-evidence floor; needs-review(c) is the unacknowledged subset of underperforming (relationship stated explicitly so the two counts read coherently).
6. **Funnel top stage:** "Evaluations" (a hard count of paper-scoped `explanations` rows in the slice), not an inferred "Cycles run". Every funnel stage is then a predicate over one population.
7. **Palette:** the production result is intentionally **not** pixel-identical to the mockup. Status colors use the editorial theme-tinted set (sage / mustard / rust / ink) per DESIGN_SYSTEM.md + ADR 0035, role-stable across themes. Layout and information design match the mockup; palette is editorial. Operator accepts this and prefers the QML feel/structure over the web mockup's aesthetic.
8. **Slice toggles:** read-models precompute **all** slices per refresh and expose a `bySlice` payload. Slice toggling is a pure client-side index — instant, zero extra DB hit.
9. **Sequencing:** Approach B — all read-models built and tested headless first; then one atomic `DeskSurface.qml` rewrite consuming proven contracts.
10. **Trust guarantee:** all new dashboard read-models are strictly read-only; never write; never touch the risk/execution path. Asserted by test.

## 3. Architecture

Every section is fed by a Python `QObject` read-model following the existing `_PollingReadModel` contract (the pattern shared by `OperationalState` and `StrategyBankState`):

- per-instance `QThreadPool(maxThreadCount=1)`;
- `start()` performs one immediate refresh and arms a timer;
- `stop()` drains the pool (bounded wait) and disconnects signals — load-bearing for the known Windows shutdown crash vector;
- DB/broker failure preserves last-known values and sets `dataStatus` / `dataError`;
- QML binds `Q_PROPERTY`s only and **never queries**; worker→main updates via `Qt.QueuedConnection`.

The six new models are siblings of the existing ones, not a new architecture. The QML surface is rewritten; the chrome and other surfaces are reused unchanged.

### IA → read-model map

| # | Section | Read-model | Primary source |
|---|---------|-----------|----------------|
| I | Risk & Mode | `OperationalState` *(reuse, no new code)* | kill switch, broker, market, mode + DB-present check |
| II | Performance & Trust | `PerformanceState` *(new)* | `portfolio_snapshots` (Week+); `OperationalState` `daily_pnl` (Today); SPY from data cache; stale flag |
| III | Active Operations | `ActiveOpsState` *(new)* | `strategy_runs`, tempo config (cadence), `advisory_lock`, stop-request sentinel, `explanations` (last-eval) |
| IV | Risk Layer Throughput | `RiskThroughputState` *(new)* | `explanations` + `trades`, paper-scoped, by slice |
| V | Strategy Attention | `AttentionState` *(new; extends `StrategyBankState`)* | `promotions`, `strategy_manifests`, `portfolio_snapshots`, gate machinery |
| VI | Market Tape | `MarketTapeState` *(new)* | data cache / provider (SPY/QQQ/IWM/TLT/VIX), timestamp-only |
| VII | Order / Signal Tape | `ActivityFeedState` *(new)* | `explanations` ⨝ `trades`, desc by `recorded_at`, filterable |

Each read-model owns exactly one section's data contract, is testable headless against a fixture DB, and exposes a stable `Q_PROPERTY` interface. Internals can change without touching the surface.

## 4. Section data contracts

Cadences below are spec-tunable defaults.

### `PerformanceState` — Section II (refresh ~30s)
- Slices: Today / Week / Month / YTD / All-Paper, exposed as a `bySlice` map.
- **Today** P/L is *not* owned here — QML binds `OperationalState` account `daily_pnl` (no second broker poll). This model owns Week+ derived from `portfolio_snapshots`: period return, peak-to-trough drawdown over the slice's equity series, equity sparkline series.
- Benchmark: SPY period return over the same window from the data cache. **Excess = strategy return − SPY return**.
- **Stale flag (this section only):** newest `portfolio_snapshots` row older than threshold (default: > 2 trading days) → `isStale=true`, `staleAsOf` set; QML degrades the hero to a muted "stale as of \<date\>" state.
- `Q_PROPERTY`s: `bySlice`, `sparkline`, `benchmarkBySlice`, `isStale`, `staleAsOf`, `lastRefreshedAt`, `dataStatus`, `dataError`.

### `RiskThroughputState` — Section IV (refresh ~30s)
Paper-scoped: excludes `decision_type='backtest_fill'` and any non-live-stage rows; only `strategy_stage ∈ {paper, micro_live, live}`. Same 5 slices, `bySlice` map. Stage definitions (all hard predicates over one population):
- **Evaluations** = count of paper-scoped `explanations` rows in the slice (funnel top; replaces "Cycles run").
- **Signals** = `explanations` rows where `status != 'no_signal'`.
- **Orders proposed** = `decision_type ∈ {submit, preview}`.
- **Risk-approved** = proposed ∧ `risk_allowed = 1`.
- **Rejected** = `risk_allowed = 0` (`status = 'blocked'`).
- **Submitted** = `trades.status = 'submitted'`.
- **Filled** = `trades.broker_status = 'filled'`.

### `ActiveOpsState` — Section III (refresh ~10s, liveness-sensitive)
Runner = most-recent `strategy_runs` per `strategy_id`. Per runner:
- **Session state:** `running` if `ended_at` is null; else `stopped` + `exit_reason` (`controlled_stop` / `kill_switch` / `orphan_recovered`).
- **Cadence:** strategy tempo from the frozen manifest config (`bar_size` / `poll_interval`). *Verification item (planning):* confirm whether config expresses "open/close-only" as a distinct cadence. If not, cadence degrades gracefully to `bar_size`+interval and the limitation is documented — not a blocker.
- **Heartbeat** (replaces "next cycle"): derived verdict from `now − max(explanations.recorded_at for the session)` vs cadence — `on schedule` or `overdue by Xm`. Last-evaluation timestamp is retained as the input and shown.
- **Runner lock:** `advisory_lock` held / released.
- **Stop requested:** stop-request sentinel file present / absent (`request_controlled_stop` → `stop_request_path`).
- **Session age:** `now − started_at`.

### `AttentionState` — Section V (refresh ~60s, slow-moving)
Reuses `StrategyBankState` for paper/backtest classification; adds:
- **Rollups:** running-now (`strategy_runs` with null `ended_at`), paper-testing, backtest-only, **needs-review**, **underperforming**.
- **Needs-review** = count of strategies where a system gate has tripped and a human decision is pending:
  - (a) backtest-eligible (latest backtest meets promotion thresholds) and not yet promoted to paper;
  - (b) paper has sufficient data for micro_live promotion and not yet promoted;
  - (c) a live-stage underperformer with **no operator action** (a `promotions` row with `promotion_type='demotion'`, a frozen manifest, or a stop-request) recorded *after* the underperformance breach. Confirmed: no automatic demotion exists — every demote/freeze path is operator-initiated propose→submit (`approved_by` always human), so (c) is real and necessary.
- **Underperforming** = count of strategies at any live-ish stage (paper / micro_live / live) performing below the prior-stage evidence that earned the current stage (paper < its backtest promotion metrics, micro_live < its paper metrics, …), the baseline being the `promotions` row's stored `sharpe_ratio` / `max_drawdown_pct` / `trade_count`. **Gated by a minimum-evidence floor:** no flag until the sample is statistically meaningful (threshold defined in the implementation plan). Today this is effectively paper-vs-backtest only (one micro_live promotion, zero live; live boundary locked).
- **Relationship (explicit):** Underperforming is the measurement; needs-review(c) is the subset of underperformers that are also unacknowledged. The dashboard must present these so "Underperforming N / Needs Review M" reads coherently (M includes the unacknowledged underperformers, not an unrelated number).
- **Drift list:** top-N items with notes (e.g. "Pullback RSI2 — below paper expectation").

### `MarketTapeState` — Section VI (refresh ~60s)
SPY / QQQ / IWM / TLT / VIX: latest cached daily close, % change vs prior close, `asOf` (bar date). Timestamp-only, no stale flag (locked).

### `ActivityFeedState` — Section VII (refresh ~10s)
`explanations` ∪ `trades` normalized to `{time, strategy, kind, detail, symbol, tone}`, descending by `recorded_at`, capped (default last 200), paper-scoped. Client-side filter: All / Orders / Rejections / Signals / Fills.

## 5. QML rewrite (`DeskSurface.qml`)

**Layout** (mockup IA, chrome reused): chrome → header band (kicker / title / standfirst) → Row 1 `I · II · III` → hairline → Row 2 `IV · V · VI` → hairline → `VII` full-width.

**Palette:** status tones re-expressed in the editorial token vocabulary — `positive→sage`, `negative→rust`, `warning→mustard`, `muted→ink-muted`, `data→text.primary` (JetBrains Mono). No literals; a hardcoded hex fails the token-binding contract structurally at theme-swap.

**Components:** existing design-system components (`Button`, `StatusPill`, `StrategyRow`, `Surface`) reused. New shared components, composed against `Theme` tokens, each independently testable:
- `SectionHeader` (Roman numeral in Newsreader `display.sm` + name in `label.xs` uppercase + optional right slot)
- `SegmentedToggle` (slice pill, shared by II and IV)
- `Sparkline` (Qt `Shape`/`Canvas`, instant redraw)
- `FunnelRow` (label · proportional bar · mono value)
- `RollupCell`, `TapeRow`, `RunnerSelect` (native-style combo), `ActivityTable` (filterable)

**Binding model:** QML binds `Q_PROPERTY`s only. Slice toggles index the precomputed `bySlice` payload client-side (instant).

**Section I distinction:** the top Risk Office strip is chrome (reused). Section I "Risk & Mode" is a separate body module binding the same `OperationalState` plus a DB-present check — shared source, distinct widget.

**Animation discipline (locked design-system rules):** state changes instant; P&L figures never crossfade values; kill banner never pulses; slice toggle instant; no idle animation.

## 6. Testing surface

Each read-model tested headless against a seeded fixture DB (temp sqlite, known rows — assert exact aggregates):
- Lifecycle: `start()` → exactly one immediate refresh + timer armed; `stop()` → pool drained + signals disconnected.
- Failure path: DB/broker error → last-known preserved, `dataStatus='error'`, `dataError` set.
- Payload shape.

Model-specific:
- `PerformanceState`: slice math (return/drawdown/excess vs a known series); Today reads `OperationalState`, not a second poll; stale-flag boundary (just-fresh vs just-stale).
- `RiskThroughputState`: paper-scoping excludes backtest rows; each stage predicate count exact; slice windowing.
- `ActiveOpsState`: running vs stopped from `ended_at`; heartbeat on-schedule/overdue at the threshold boundary; lock held/released; stop-sentinel present/absent.
- `AttentionState`: needs-review (a)/(b)/(c) each independently; underperforming evidence-floor boundary (below floor → not flagged even when underperforming); (c) ⊂ underperforming holds.
- `MarketTapeState`: % vs prior close; `asOf` from bar date.
- `ActivityFeedState`: union ordering desc, cap, filter predicates, paper-scoped.

Component tests (existing pattern): `SegmentedToggle` index, `Sparkline` path, `ActivityTable` filter, structural token-binding check.

**Read-only trust guarantee (asserted by test):** every dashboard read-model is strictly read-only — no INSERT/UPDATE/DELETE, never touches the risk/execution path (e.g., read-only DB connection in fixture, or statement audit). Preserves Phase 5 exit criterion C-3 and FOUNDER_INTENT #1.

## 7. PR decomposition (Approach B)

| PR | Scope | Size |
|----|-------|------|
| 1 | `PerformanceState` + tests | small |
| 2 | `RiskThroughputState` + tests | small |
| 3 | `ActiveOpsState` + tests — scheduled early to surface the cadence-config verification item before PR 8 depends on it | small–decent |
| 4 | `AttentionState` + tests (extends `StrategyBankState`) | decent |
| 5 | `MarketTapeState` + tests | tiny |
| 6 | `ActivityFeedState` + tests | small |
| 7 | Shared QML components + component tests (no surface wiring) | decent |
| 8 | Atomic `DeskSurface.qml` rewrite consuming all read-models + components; old mock surface removed; bridges registered in `gui/app.py`; integration test | decent–large |

All eight kept separate (separation is the point of Approach B). Within this authorized sequence, PR-and-continue autonomously per standing cadence; deliberate checkpoints: **after PR 3** (cadence-risk finding) and **before PR 8** (last look before the atomic rewrite).

## 8. Risks & verification items

- **Cadence-config expressibility (PR 3):** whether the frozen manifest config distinguishes "open/close-only" strategies from a generic `bar_size`+interval cadence. Graceful degradation defined; not a blocker.
- **Snapshot granularity:** `portfolio_snapshots` is ~daily/EOD-grained (291 rows over ~4 years). Week+ slices are honest EOD aggregates; the stale flag protects the hero. "Today" sidesteps this via the live broker poll.
- **Minimum-evidence floor (PR 4):** the exact statistical floor for "underperforming" is set in the implementation plan; without it the counter misleads in the current early-paper regime (promotions are days old).
- **`AttentionState` ↔ `StrategyBankState` coupling:** extends rather than duplicates; the implementation plan must define the seam so `StrategyBankState` internals stay encapsulated.

### Plan-level clarifications (from spec review — pin these before/at PR start)

- **Canonical paper-scope predicate:** `RiskThroughputState` and `AttentionState` must scope identically. The implementation plan pins one canonical predicate (name + exact definition of "paper-scoped" / "live-ish stage" = `strategy_stage ∈ {paper, micro_live, live}` and `decision_type != 'backtest_fill'`) reused by both models so the two cannot drift.
- **`ActivityFeedState` cardinality:** §3 IA map says ⨝ (join); §4 describes normalize-to-common-shape (union). The plan must state explicitly that PR 6 builds a **union** of `explanations`-derived rows and `trades`-derived rows normalized to one shape (not a row-multiplying join), so feed cardinality is correct.
- **Funnel trade-stage coherence:** "Submitted" / "Filled" derive from `trades` while Evaluations…Rejected derive from `explanations`. The plan must specify how trade-derived stages join back to the explanation population (via `trades.explanation_id`) so the funnel proportions remain coherent and a fill is never counted without its originating evaluation.

## 9. Out of scope

- Any change to `Main.qml` chrome or the `FRONT`/`BENCH`/`LEDGER` surfaces (unless the fallback in §2.2 is explicitly triggered).
- Reopening ADR 0033 (no web/FastAPI surface).
- Live intraday tick data; new market-data ingestion. Market context is cached-daily, as the system already operates (daily-swing).
- Auto-demotion / any automation of lifecycle decisions (constrained by VISION:134, ADR 0021).
- Editorial Light / Bronze theme polish for the new components beyond token-correctness (they must theme-swap correctly; visual tuning of non-default themes is later).

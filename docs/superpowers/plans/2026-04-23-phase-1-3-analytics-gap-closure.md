# Phase 1.3 §5.1.2 + §5.1.3 Analytics & Reporting — Gap-Closure Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Phase 1.3 §5.1.2 (Analytics & Metrics) and §5.1.3 (CLI — Reporting Surface) on master, bringing `milodex analytics` and its backing modules up to the checklist in `docs/ROADMAP_PHASE1.md` lines 142–161.

**Architecture:** Additive, not structural. `§5.1.1` established the execution-path contract; §5.1.2 is pure data-over-event-store work. Four axes:
1. Round out `metrics.py` with the missing PnL-aggregate and drawdown-duration metrics.
2. Add `snapshots.py` (daily portfolio state → event store) and `reports.py` (trust-report assembly).
3. Extend CLI with a `--strategy` convenience resolver and multi-format export.
4. Backfill test coverage where it's thin (benchmark mocks, CLI integration).

**Tech Stack:** Python 3.11+, pytest, existing `milodex.analytics.*`, `milodex.cli.commands.analytics`, SQLite event store.

---

## 1. Context

### What landed in §5.1.1
- Backtest engine rides `ExecutionService.submit_backtest()` via `SimulatedBroker` + `NullRiskEvaluator` ([commits 01935e1..0a6e8f5](../../../) on master).
- CLI uncertainty labels (R-CLI-014, R-PRM-004) on `milodex backtest`.
- Engine-side R-XC-008 enrichment (rule, config_hash, bar_timestamp).

### What's already on master for §5.1.2/§5.1.3 (from the scaffolding commit `8a62528`)
- `src/milodex/analytics/metrics.py` — total return, CAGR, max DD magnitude, Sharpe, Sortino, win rate, avg hold, confidence label. Golden tests in [test_metrics.py](../../../tests/milodex/analytics/test_metrics.py).
- `src/milodex/analytics/benchmark.py` — SPY buy-and-hold comparison. **No tests.**
- `src/milodex/cli/commands/analytics.py` — subcommands `metrics`, `trades`, `compare`, `export`, `list`. Keyed on `run_id`. CSV-only export. **No CLI tests.**
- `src/milodex/cli/commands/reconcile.py` — complete.
- `--json` formatter abstraction (ADR 0014) applied across read commands.

### What's outstanding (audit 2026-04-23)
| # | Item | Status |
|---|------|--------|
| 1 | `metrics.py` profit factor, avg win $, avg loss $ | MISSING |
| 2 | `metrics.py` max drawdown *duration* | MISSING |
| 3 | `analytics/snapshots.py` | MISSING |
| 4 | `analytics/reports.py` (trust report) | MISSING |
| 5 | CLI `--strategy` shortcut (latest-run resolver) | MISSING |
| 6 | Export `--format {csv,json,md}` (R-ANA-006) | CSV-only |
| 7 | Benchmark unit tests (SPY mocked) | MISSING |
| 8 | Analytics CLI integration tests | MISSING |

### Explicitly deferred (not in this plan)
- **R-XC-008 strategy-interface extension.** Requires `Strategy.evaluate()` to return reasoning alongside `TradeIntent` — a cross-cutting interface change to a sacred surface (CLAUDE.md). Separate sub-plan after §5.1.2/§5.1.3 are stable.

---

## 2. Architectural Decisions

### AD-1 — Extend `PerformanceMetrics` in place; keep it a flat dataclass
The four missing metrics (`profit_factor`, `avg_win_usd`, `avg_loss_usd`, `max_drawdown_duration_days`) live on the existing `PerformanceMetrics` dataclass as new optional fields. No subclassing, no separate "trade PnL stats" object.

**Why.** Callers already destructure the flat shape (`performance_metrics_to_dict`, JSON schema consumers). Splitting into nested objects breaks the JSON contract and adds no analytical value. Per-metric confidence labels remain off-field — `confidence_label` stays a single value derived from `trade_count`.

### AD-2 — FIFO PnL pairing already exists; extend `_trade_stats` to return amounts
`_trade_stats` in [metrics.py:196](../../../src/milodex/analytics/metrics.py) already walks FIFO BUY/SELL pairs and computes per-pair PnL. It currently returns `(win_rate, avg_hold, winning_count, losing_count)` and discards the dollar amounts. Extend it to return the aggregates too.

**Why.** Single pass, single source of truth. Avoid duplicating the pairing loop in a second helper.

### AD-3 — Snapshots live behind a dedicated recorder, not inside `StrategyRunner` or `ExecutionService`
New `analytics/snapshots.py` exposes a `record_daily_snapshot(event_store, broker, session_id)` function. The paper runner and (optionally) the backtest engine call it once per trading day's close. Snapshot rows go to a new `portfolio_snapshots` table.

**Why.** Snapshot concerns (positions, cash, equity) are a read of broker state, not a write of trade state — a separate module keeps the event-store writer surface narrow and doesn't bloat `ExecutionService`. Goes through the event store for durability per ADR 0011.

### AD-4 — Trust report is pure composition, not a new compute path
`analytics/reports.py` exposes `assemble_trust_report(run_id, event_store, data_provider) -> TrustReport`. It calls `compute_metrics` + `compute_benchmark` + reads snapshot history + computes the benchmark delta and uncertainty label. Zero new math.

**Why.** The roadmap wording ("assembles a trust report") is composition language. A trust report is a view, not a new measurement. Keeps the report regenerable from event-store state at any time.

### AD-5 — CLI keying: keep `run_id` required; add `--strategy <id>` as a convenience that resolves to the latest run
Preserves the explicit `run_id` contract for `metrics`, `trades`, `compare`, `export`. Adds a `--strategy` flag that, when present, resolves to the latest `backtest_runs` row for that strategy_id and uses its `run_id`. `--strategy` and positional `run_id` are mutually exclusive.

**Why.** Operators pasting UUIDs from `analytics list` is the honest path; the shortcut is ergonomic sugar. Does not break any existing JSON contract or script. ROADMAP wording (`<strategy_id>`) is satisfied by the flag's presence.

### AD-6 — Export formats via a format registry in the CLI command, not a new module
The `export` subcommand accepts `--format {csv,json,md}` (default `csv`). Internally dispatches to small inline builders: CSV (existing), JSON (wraps the same rows as `performance_metrics_to_dict`), Markdown (thin table formatter). No new `milodex.analytics.exporters` package — three format functions inline in [commands/analytics.py](../../../src/milodex/cli/commands/analytics.py) are enough.

**Why.** YAGNI. Three formats, each ~20 lines. A registry module would be premature abstraction.

### AD-7 — New SQL table for snapshots via a migration, not JSON-blob shoehorning
Add `004_portfolio_snapshots.sql` migration with columns: `id`, `recorded_at`, `session_id`, `equity`, `cash`, `portfolio_value`, `daily_pnl`, `positions_json` (opaque JSON for position list). Follows existing migration naming (`003_backtest_runs.sql` precedent).

**Why.** First-class columns for the values metrics care about, opaque JSON for the position-list detail. Matches the shape established by `backtest_runs.metadata_json`.

---

## 3. File Inventory

Grouped by commit (§4). Paths relative to `C:\Users\zdm80\Milodex`.

### Phase A — Metric completeness (AD-1, AD-2)

**Modify:**
- `src/milodex/analytics/metrics.py` — extend `PerformanceMetrics` dataclass with `profit_factor: float | None`, `avg_win_usd: float | None`, `avg_loss_usd: float | None`, `max_drawdown_duration_days: int`. Extend `_trade_stats()` to return `(win_rate, avg_hold, winning, losing, avg_win_usd, avg_loss_usd, profit_factor)`. Extend `_max_drawdown()` into `_max_drawdown_stats()` returning `(magnitude, duration_days)`.
- `src/milodex/cli/_shared.py` — extend `performance_metrics_to_dict()` to emit the new fields.
- `tests/milodex/analytics/test_metrics.py` — new golden cases: known-PnL trade sequence verifies `profit_factor`, `avg_win_usd`, `avg_loss_usd`; hand-crafted equity curve verifies `max_drawdown_duration_days`.

### Phase B — Snapshots module + table (AD-3, AD-7)

**Create:**
- `src/milodex/core/migrations/004_portfolio_snapshots.sql` — `portfolio_snapshots` table.
- `src/milodex/analytics/snapshots.py` — `PortfolioSnapshot` dataclass + `record_daily_snapshot(event_store, broker, *, session_id)` function.
- `tests/milodex/analytics/test_snapshots.py` — recorder writes row; row shape matches; multiple calls append distinct rows.

**Modify:**
- `src/milodex/core/event_store.py` — `append_portfolio_snapshot()` + `list_portfolio_snapshots_for_session()` + `list_portfolio_snapshots_for_strategy()` (joined via session_id → strategy_runs, or via a strategy_id denorm column if cleaner; decide during implementation).

### Phase C — Trust report (AD-4)

**Create:**
- `src/milodex/analytics/reports.py` — `TrustReport` dataclass (metrics bundle, benchmark delta, uncertainty label, snapshot summary, open questions). `assemble_trust_report(run_id, event_store, data_provider)` factory.
- `tests/milodex/analytics/test_reports.py` — builds a report for a seeded run + seeded snapshots; asserts delta computation, uncertainty label propagation, open-question surfacing.

### Phase D — CLI shortcuts + export formats (AD-5, AD-6)

**Modify:**
- `src/milodex/cli/commands/analytics.py` — add `--strategy` flag to `metrics`, `trades`, `compare` (via new `--strategy-a` / `--strategy-b`), `export`. Add `--format {csv,json,md}` to `export`. Add inline `_export_trades_json()`, `_export_trades_markdown()`, `_export_equity_curve_json()`, `_export_equity_curve_markdown()` helpers. Add `_resolve_run_id(event_store, strategy_id)` helper that returns the most-recent `run_id` for a strategy.
- `tests/milodex/cli/test_analytics_command.py` — NEW. Cover: `--strategy` resolves to latest run, `--format json` / `--format md` each produce expected structure, `metrics` / `trades` / `compare` / `export` happy paths via `cli_entrypoint`.

### Phase E — Benchmark test coverage

**Create:**
- `tests/milodex/analytics/test_benchmark.py` — mocks a `DataProvider` with a canned SPY BarSet; asserts `compute_benchmark` returns a `PerformanceMetrics` with expected total return + drawdown; empty / missing-bar cases fail gracefully.

### Phase F — Roadmap hygiene

**Modify:**
- `docs/ROADMAP_PHASE1.md` lines 142–161 — flip checkboxes, note any residual deferrals (expected: none), link back to the commits that closed each bullet.

---

## 4. Commit Sequence

Small commits, each self-contained and test-green. TDD pattern per the §5.1.1 plan.

### Phase A — Metric completeness (1 commit)

1. **`feat(analytics): add profit factor, avg win/loss $, and max drawdown duration`**
   - Extends `PerformanceMetrics`, `_trade_stats`, and `_max_drawdown` (→ `_max_drawdown_stats`).
   - Extends `performance_metrics_to_dict`.
   - Adds golden tests.
   - Green: `pytest tests/milodex/analytics/test_metrics.py`.

### Phase B — Snapshots (1 commit)

2. **`feat(analytics): add portfolio_snapshots table and daily recorder`**
   - Creates migration 004, `snapshots.py`, event-store helpers.
   - Adds `test_snapshots.py`.
   - No callers yet — wiring into `StrategyRunner` / engine is Phase D if scope-creep-safe, otherwise its own follow-up.
   - Green: `pytest tests/milodex/analytics/test_snapshots.py`, full suite.

### Phase C — Trust report (1 commit)

3. **`feat(analytics): assemble trust report composing metrics + benchmark + snapshots`**
   - Creates `reports.py`, `test_reports.py`.
   - Green: `pytest tests/milodex/analytics/test_reports.py`.

### Phase D — CLI shortcuts + export formats (2 commits)

4. **`feat(cli): add --strategy shortcut resolving to latest backtest run`**
   - Adds `--strategy` flag + `_resolve_run_id` to `analytics metrics`, `trades`, `compare`, `export`.
   - Mutual-exclusivity tests with positional `run_id`.
   - Green: `pytest tests/milodex/cli/test_analytics_command.py`.

5. **`feat(cli): add JSON and Markdown export formats to analytics export`**
   - Adds `--format` flag + three inline format builders.
   - Tests shape of each format output.
   - Green: `pytest tests/milodex/cli/test_analytics_command.py`.

### Phase E — Test backfill (1 commit)

6. **`test(analytics): add SPY benchmark unit tests with mocked provider`**
   - Covers the last untested module in the analytics package.
   - Green: `pytest tests/milodex/analytics/`.

### Phase F — Docs (1 commit)

7. **`docs(roadmap): mark §5.1.2 and §5.1.3 closed`**
   - Flips checkboxes 142–161. Notes R-XC-008 as the one surviving deferral (now scoped for §5.1.4 or its own sub-plan).

**Total: 7 commits.** Phase A, B, C are independent and can land in any order. Phase D depends on none of them (uses existing metrics). Phase E has no dependencies. Phase F last.

---

## 5. Test Plan

### Unit

| Test file | Coverage |
|-----------|----------|
| `tests/milodex/analytics/test_metrics.py` (extended) | profit_factor / avg_win_usd / avg_loss_usd / max_drawdown_duration_days golden cases |
| `tests/milodex/analytics/test_snapshots.py` (new) | recorder writes, row shape, append semantics, session→strategy lookup |
| `tests/milodex/analytics/test_reports.py` (new) | trust report assembly, delta math, uncertainty propagation, open-question surfacing |
| `tests/milodex/analytics/test_benchmark.py` (new) | mocked SPY provider: return%, DD match; empty bars → graceful failure |
| `tests/milodex/cli/test_analytics_command.py` (new) | `--strategy` resolves latest, `--format {csv,json,md}` each produce expected shape, subcommand happy paths via `cli_entrypoint` |

### Integration (manual, user-run, isolated data dir)

```bash
# After Phase C — reproduce the baseline regime run and pull a trust report
TMPDIR=$(mktemp -d)
MILODEX_DATA_DIR="$TMPDIR/data" MILODEX_LOG_DIR="$TMPDIR/logs" MILODEX_LOCKS_DIR="$TMPDIR/data/locks" \
  ./.venv/Scripts/python.exe -m milodex.cli.main backtest \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --start 2024-01-02 --end 2024-06-28
# Expected: baseline — Trading days 124, Final equity $101,348.57, +1.35%, 1 BUY

# --strategy shortcut
MILODEX_DATA_DIR="$TMPDIR/data" MILODEX_LOG_DIR="$TMPDIR/logs" MILODEX_LOCKS_DIR="$TMPDIR/data/locks" \
  ./.venv/Scripts/python.exe -m milodex.cli.main analytics metrics \
  --strategy regime.daily.sma200_rotation.spy_shy.v1 --compare-spy
# Expected: resolves to the backtest run just created; SPY delta included

# Multi-format export
MILODEX_DATA_DIR="$TMPDIR/data" MILODEX_LOG_DIR="$TMPDIR/logs" MILODEX_LOCKS_DIR="$TMPDIR/data/locks" \
  ./.venv/Scripts/python.exe -m milodex.cli.main analytics export \
  --strategy regime.daily.sma200_rotation.spy_shy.v1 \
  --format json --output "$TMPDIR/export"
# Expected: JSON files land in $TMPDIR/export
```

### Lint + format

```bash
ruff check src/milodex/{analytics,cli} tests/milodex/{analytics,cli}
ruff format --check src/milodex/ tests/milodex/
```

### Production-DB guard

`_guard_real_event_store_untouched` in [tests/conftest.py:56](../../../tests/conftest.py) will fail loudly if any refactor accidentally writes to `data/milodex.db`.

---

## 6. Open Questions

1. **Snapshot wiring.** Plan scopes the `snapshots.py` module but does NOT wire `StrategyRunner` / backtest engine to call it. Do you want the wiring in this plan (adds ~30 lines to two callers + two small tests, extends Phase B to two commits), or deferred so Phase 1.3 closure doesn't touch the runner?

2. **`--strategy` resolution ambiguity.** When a strategy has zero backtest runs, should `--strategy` error out or return a recognizable empty-state `CommandResult`? I lean error (loud failure beats silent nothing) — confirm.

3. **Markdown export scope.** Roadmap says `{csv,json,md}` per R-ANA-006 but doesn't specify Markdown layout. I'll produce a simple table-per-section (equity curve, trade ledger, metrics). Acceptable, or do you want a specific layout the Markdown must match?

4. **Snapshot table column for strategy lookup.** `portfolio_snapshots` needs to be queryable by strategy for the trust report. Cleanest: add a `strategy_id TEXT` column denormed on insert. Alternative: join via `session_id` → `strategy_runs.strategy_id`. First is simpler and cheaper to query; second is a purer schema. Preference?

---

## 7. Non-Goals

- No R-XC-008 strategy-interface work (separate plan).
- No changes to `RiskEvaluator`, `ExecutionService`, or `BacktestEngine` (§5.1.1 sealed those).
- No new CLI commands. Only extensions to existing `analytics` and (if scope allows) wiring snapshots from `StrategyRunner`.
- No Phase 1.4 promotion-pipeline work.
- No Grafana / dashboard surface. CLI + JSON remain the trust surface.

---

## 8. Verification Before Completion

Task is done when:
- `pytest tests/milodex/` green, full suite (currently 338; this plan adds ~25–35 new tests, no regressions expected on existing tests).
- `ruff check` + `ruff format --check` clean.
- Manual §5 integration block reproduces baseline regime numbers post-Phase-A and Phase-C outputs include benchmark delta.
- `git log --oneline -7` shows the 7-commit sequence from §4.
- Production `data/milodex.db` mtime unchanged across development.
- `docs/ROADMAP_PHASE1.md` §5.1.2 + §5.1.3 checkboxes flipped, R-XC-008 flagged as the single surviving deferral.

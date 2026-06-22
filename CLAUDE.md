# Milodex

Personal autonomous trading system. See `docs/VISION.md` for the full project vision, and **`docs/FOUNDER_INTENT.md` for the founder's personal intent** — the deeper "why" that should guide product, UX, and documentation decisions. When in doubt about tone, scope, or tradeoffs, defer to FOUNDER_INTENT. The live path to trust closure is tracked in **`docs/CURRENT_ROADMAP.md`** — the canonical control-tower roadmap (gate-only updates; one active milestone).

## Commands

```bash
pip install -e ".[dev]"      # Install in editable mode with dev deps
python -m pytest             # Run tests (bare `pytest`/`ruff` may not be on PATH; use `python -m`)
                             # Runs in parallel by default (pytest-xdist, capped at 8 workers); full suite ~1m50s. Add `-n0` to disable for tight single-test loops.
python -m ruff check src/ tests/    # Lint
python -m ruff format src/ tests/   # Format
python -m milodex.cli.main <subcommand>   # CLI entry — `python -m milodex` does NOT work (no __main__.py at package root); always include `.cli.main`
python -m milodex.cli.main gui            # Launch the GUI (Phase 6 surface); short form: `milodex gui`. `python -m milodex.gui` does NOT work (no __main__.py in gui/)
```

## Architecture

src-layout Python package (`src/milodex/`). Fourteen modules:

- **broker/** — Brokerage API integration (Alpaca). All broker access goes through this interface.
- **strategies/** — Config-driven strategy definitions. No hardcoded strategy logic — parameters live in `configs/*.yaml`.
- **risk/** — Risk management layer. Sits between strategies and execution with **veto power** over all trades. Never bypass.
- **execution/** — Trade orchestration service. Single chokepoint from intent → trade: invokes the risk layer, records explanations, submits to broker. No code path reaches the broker without passing through here.
- **promotion/** — Promotion lifecycle surface (ADR 0015): frozen strategy manifests, evidence, and stage-transition governance. The risk layer reads back the active manifest hash from here.
- **backtesting/** — Backtest engine with walk-forward validation. Minimum 30 trades before statistical conclusions. Intentionally below the risk layer — risk is enforced at promotion, not simulation.
- **research/** — Research-evidence lane: candidate fan-out (`fanout.py`), candidate-rate screens (`candidate_rates.py`), and evidence assembly (`evidence_assembler.py`) that joins a walk-forward candidate against null baselines and writes append-only experiment-registry rows. IEX-fed verdicts are exploratory / non-durable (ADR 0017).
- **data/** — Market data acquisition. Start with free sources (Alpaca, Yahoo Finance). Premium only if testing justifies cost.
- **analytics/** — Performance metrics, trade logging, benchmark comparison (vs SPY).
- **core/** — Shared infrastructure: SQLite event store (ADR 0011), advisory locks, schema migrations. Source of truth for trade, explanation, kill-switch, strategy-run, and backtest-run history. Durable state lives under `data/` per ADR 0018.
- **operations/** — Operational maintenance routines run outside the trade path: data-freshness checks, broker reconciliation, and periodic orphan-runner maintenance.
- **cli/** — Command-line interface. Primary interaction surface.
- **commands/** — Backend command facades the GUI (and future tooling) reaches. Thin orchestrators over existing CLI/governance/runtime callees — no business rules of their own (ADR 0051).
- **gui/** — GUI subsystem (PySide6 + Qt Quick), per ADR 0033/0035. Bundled fonts, QML theme infrastructure, read models.

## Key Design Rules

- **Risk layer is sacred.** Every trade passes through `risk/` before execution. Strategy proposes, risk disposes. Never bypass or weaken for convenience.
- **Operator owns risk preferences; risk layer owns enforcement.** The operator may eventually choose a risk posture from inside explicit, bounded, auditable policy — safe by default, deliberately opted into for higher risk, human-approved for live-capital effect, logged, visibly active, and bounded by non-negotiable account-level guardrails. No strategy, ML model, frontier agent, or feature may modify, weaken, or bypass the risk policy that evaluates it. Do not write code or docs framing this as *"the user controls the risk layer"* or *"strategies configure their own risk"* — both invert the relationship. Full thesis: `docs/FOUNDER_INTENT.md` "The Risk Layer — Operator Preferences, System Enforcement."
- **Strategies are config-driven.** Strategy parameters live in `configs/*.yaml`, not in code. The code defines behavior; config defines tuning.
- **Promotion pipeline is mandatory.** Stages: backtest → paper → micro_live → live. No skipping stages. The gate is two-tier: a permissive paper-readiness tier and a stricter capital-readiness tier (post-paper), plus a lifecycle-proof exemption for the regime strategy. Authoritative threshold definitions live in `src/milodex/promotion/policy.py` (ADR 0052) — do not restate numeric thresholds here.
- **Kill switch requires manual reset.** When triggered, trading halts. Auto-resume is never acceptable.
- **Actions that always require explicit human approval:** promoting any strategy to live, allocating or increasing real capital to a live strategy, re-enabling after any kill switch or major risk event, changing core risk limits for live, granting a new broker live-trade permission, overriding a blocked or rejected execution decision, retiring or replacing a live strategy with a materially different version. See `docs/VISION.md` "Autonomy Boundary" for the authoritative list.
- **Strategy bank, two roles.** The bank holds (1) a single **lifecycle-proof regime strategy** (SPY/SHY 200-DMA) exempt from the 30-trade / Sharpe thresholds because a regime strategy can't produce them, and (2) **statistically-promoted edge strategies** that must pass the capital-readiness gate (thresholds defined in `src/milodex/promotion/policy.py` / ADR 0052). Each strategy is configured and promoted independently. The canonical bank state — what's at paper, what's blocked at backtest, and why — lives in `docs/STRATEGY_BANK.md`. See `docs/SRS.md` Key Terms.

## Config Schema

Non-daily strategies must declare `strategy.tempo.position_lifecycle`: `same_session`
uses US-equity RTH-only replay and close flattening; `multi_session` preserves positions
across outer days and is required for the 24/7 crypto canaries.

- `configs/sample_strategy.yaml` — Per-strategy config template (parameters, risk limits, promotion stage)
- `configs/risk_defaults.yaml` — Global risk guardrails (kill switch, fat-finger protection, daily loss caps). Applies above all strategy configs.

## Environment

Requires `.env` (see `.env.example`): `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `TRADING_MODE` (paper/live).

## Phase One Constraints

- **Market:** US equities/ETFs only
- **Tempo:** Daily swing trades (1–5 day holds)
- **Broker:** Alpaca
- **Capital:** Under $1,000
- **Edge families:** Momentum, mean reversion, breakout

## Code Style

- Python 3.11+
- Line length: 100
- Linting: ruff (rules: E, F, I, N, W, UP)
- Tests: pytest, mirror src structure in `tests/milodex/`
- No runtime dependencies added without justification — add as each component is built

## Gotchas

- `logs/` is gitignored except `.gitkeep` — don't commit log files
- `.env` is gitignored — never commit API keys. Use `.env.example` as template.
- Backtest slippage is universe-aware: ~3 bps (liquid-ETF universes) / 5 bps (mixed or large-cap), fallback 5 bps (`slippage_pct_default: 0.0005` in `configs/risk_defaults.yaml`) — don't assume zero slippage
- Pattern day trader rule: **decommissioned by FINRA Regulatory Notice 26-10 effective 2026-06-04** ($25k minimum and day-trade-count thresholds eliminated; Alpaca implements on the effective date, not the 18-month phase-in). Pre-2026-06-04 the old rule (under $25k = no same-day round trips) still binds **margin accounts only**; cash accounts were never PDT-bound. The paper account is at $101k margin (multiplier=2), so PDT never bound in any case.
- **Backtest engine dispatches on `tempo.bar_size`.** `BacktestEngine._simulate` at `src/milodex/backtesting/engine.py:938` branches on the `Timeframe` enum derived from the strategy's bar_size: `_simulate_daily` (`engine.py:994`) for `1D`, `_simulate_intraday` (`engine.py:1153`) for everything else. Intraday helpers (event timeline, cursors, mark-to-market) live in `src/milodex/backtesting/intraday_simulation.py`. Design spec: `docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md`. The intraday path implements advance→evaluate→drain ordering; T+1 open fills are guaranteed by construction. If an intraday backtest reports 0 trades, the engine is NOT the suspect — check sizing math first (`shares_for_notional_pct` floors to 0 when equity × notional_pct < unit_price).
- **`.venv` must be a stdlib `python -m venv`, not a `uv venv` trampoline.** A uv/trampoline `.venv\Scripts\python.exe` re-execs the *base* interpreter, so the code runs under the base Python's site-packages — if the base has a broken/missing dep (e.g. corrupt pandas), runners die on import. Symptom: GUI-launched runners produce no explanations / "phantom" runners; fix + 5-min diagnostic in `docs/TROUBLESHOOTING.md`. The redirector+base-child PID *pair* per process is normal Windows venv behaviour — not a bug.
- **Daily (`1D`) strategies are a no-op while the market is OPEN** (`runner.py` market-hours gate returns `[]` before any fetch). "Runner running but 0 explanations" during market hours is BY DESIGN, not a stall — it evaluates after close + the lockin window. Don't debug a non-bug.
- **Daily runners decline stale (prior-session) bars.** A daily runner only locks in a close when the latest fetched bar is for the current session (`_is_current_session_bar`: `latest_bar.timestamp.date() >= _now().date()`, both UTC). This defends against pre-open/weekend launch, where the latest available bar is a *prior* session's close — locking that in once silently poisoned the watermark and suppressed the day's real post-close evaluation (observed 2026-05-28). Launching after the open or post-close both work correctly. The guard also means a daily runner will not evaluate on stale market data (it idles until the data is current) — so "daily runner alive but 0 explanations after close" can also mean the bar cache is stale, not just the market-hours gate.
- **Diagnostic surface:** event store `data/milodex.db` (SQLite) — `strategy_runs.ended_at IS NULL` = open/"running" (no liveness check; a dead one shows as phantom until #161 bootstrap reconcile); `explanations` = per-decision audit keyed by `session_id`; advisory locks in `data/locks/*.lock`; per-runner logs `logs/runner.<sid>.<ts>.log`. Inspect columns via `pragma table_info`.
- **Controlled-stop ("Stop Trading") needs a live, cooperative runner** to consume the request file. It hangs/no-ops on a wedged or already-dead runner — for those, hard-kill the PID and clear `data/locks/*.lock` instead.
- **Promotion CLI: `milodex promotion promote ...`**, not `milodex promote` (refused legacy shortcut). `--lifecycle-exempt` bypasses the statistical gate for ANY promotion — operator-override mechanism, not just for lifecycle-proof regime strategies. The bypass short-circuit is `promotion/state_machine.py:check_gate` (`if lifecycle_exempt:`); `policy.py`'s `LifecycleGateDefinition` is define-only (`enforced=False`), so the R-PRM-004 operational gate is NOT actually enforced in code.
- **`backtest_runs` metrics live in `metadata_json`**, not columns. Walk-forward: `json_extract(metadata_json, '$.oos_aggregate.trade_count')`. Initial equity is top-level `'$.initial_equity'`, NOT under `oos_aggregate`.
- **5Min cache (`market_cache/v3/5Min/`) holds 17 ETF symbols** — SPY, QQQ, IWM, DIA, GLD, TLT + the 11 XL* SPDR sector ETFs. A new intraday strategy on a symbol *outside* this set needs cache warmup first. Legacy daily cache at `market_cache/1Day/` (unversioned) coexists with the v3 layout.
- **The daily cache can develop a silent interior gap that starves the daily fleet.** `AlpacaDataProvider.get_bars` only gap-scans the recent window (`recent_gap_start = max(start, cache_start, today − 60d)`) and assumes older history is complete. When a daily runner appends a **non-contiguous tail** after a long idle (e.g. a backtest warm ending 2024-12-31 + a live tail starting 2026-03-09, with **all of 2025 missing**), `get_bars` never refetches the interior hole. Every daily "deserving" strategy filters on a 200-DMA (atr_channel: 100) over a `max(365, param*3)`-day window (`runner.py:_history_window_days`), so a hole inside that window collapses the rolling mean to **all-NaN** → the strategy idles or trades a NaN signal **with no error** (observed + fixed 2026-06-15; was the real Monday-launch risk). Heal: `milodex data fetch-universe --universe-ref <ref> --start <before-gap> --end <today> --force` — the `--force` flag (added 2026-06-15, `AlpacaDataProvider.backfill_range`) force-refetches the full range, bypassing the 60-day gap-scan floor; merge is additive/idempotent. Diagnose with a per-year bar-count on `market_cache/v3/1Day/<SYM>.parquet` (a healthy year ≈ 250 bars).
- **`tests/milodex/gui/test_qml_load_smoke.py` substring-asserts against QML source.** It pins literals in `gui/qml/Milodex/components/BenchConfirmationModal.qml` — both *required* tokens (e.g. `"Run backtest"`, the `BenchCommandBridge.*Async(` slot names) and *forbidden* ones (`"initial_equity"`, `_canonicalBacktestParams`, `2020-01-01` must stay OUT: canonical backtest params are Python-owned, P2-12). Renaming a bridge call, reintroducing a param literal, or editing pinned copy silently fails the smoke test until the assertion is updated to match.
- **`shares_for_notional_pct` (`execution/sizing.py`) floors to whole shares** → returns 0 for high-unit-price assets (e.g. BTC at ~$50k). Use `fractional_units_for_notional_pct` for crypto/fractional sizing; the simulation kernel and event store are already float-native.
- **The intraday backtest engine is calendar/asset-class agnostic** — its event timeline is built from raw bar timestamps (`backtesting/intraday_simulation.py`), so 24/7 crypto bars replay correctly. US-equity session logic (9:30/16:00 ET, half-days, opening range, time-stop) lives ONLY in `strategies/_session_intraday.py`: a 24/7 strategy must NOT import it — compute continuous (non-session-reset) indicators instead.
- **`held_days` is day-granular by design** — `simulation_kernel.tick_held_days` increments once per *outer trading day*, not per bar. A sub-day bar-count max-hold can't be expressed without threading an entry timestamp through the shared kernel; express hold limits as `max_hold_days`.
- **`ParquetCache` cannot key a `/`-symbol** — `_path` is `{dir}/{SYMBOL}.parquet` (`data/cache.py:92`), so `BTC/USD` nests a `BTC/` dir and the write fails. Crypto/`/`-symbols need a filesystem-safe cache key before any cache-backed (real-data) path works; the fixture/backtest path (`SimulatedDataProvider`) bypasses the cache entirely.
- **GUI subprocess QML tests: a lone full-suite non-pass is a SKIP, not a failure.** Fonts are now bundled (`src/milodex/gui/assets/fonts/`, loaded by `gui/fonts.py:load_fonts`), so `test_app.py::test_main_qml_loads_without_errors_via_subprocess` PASSES (the old "missing PySide6 fonts" / `QFontDatabase: Cannot find font directory` failure is gone). The one non-pass in a clean full run is `test_design_system_showcase_loads_without_errors_via_subprocess`, `@pytest.mark.skip`-quarantined for pre-existing Qt/QML process-global pollution (`docs/KNOWN_FLAKY_TESTS.md`) — so a green suite reads `… 1 skipped …`, NOT `1 failed`. Treat an actual `1 failed` as a real regression to investigate, not this.
- **Strategy config validation is `strategies/loader.py:load_strategy_config`, NOT `cli/config_validation.py`** (a thin wrapper that delegates strategy validation to the loader). Required `strategy.` keys: id, family, template, variant, version, description, enabled, parameters, tempo, risk, stage, backtest, disable_conditions_additional. `id` must equal `{family}.{template}.{variant}.v{version}`; **new family strings are accepted (no allowlist)** — a new strategy = `loader.py build_default_registry()` + one `configs/*.yaml` + the `strategies/*.py` class.
- **Adding a field to `DecisionReasoning` (`strategies/base.py`)**: declare it `field(default=..., metadata={"omit_if_default": True})` so `asdict()` self-omits it when unset. Otherwise the golden test `tests/milodex/strategies/test_base_reasoning.py` (pins the legacy 7-key blob) and every rule strategy's serialized `explanations.context_json` change shape. Legacy fields always serialize; only metadata-flagged fields omit-when-default; `asdict()` carries no type/`kind` dispatch.
- **Per-strategy DB trade rows ≠ broker net when strategies share one account (ADR 0055).** Alpaca nets per account+symbol; a sibling's sell can flatten broker net while your strategy's submitted `trades` still show an open lot (2026-06-03: rsi2 +13, vwap_trend −13 → account flat). The runner now derives `context.positions` and `entry_state` from the **strategy-scoped** event-store ledger (`strategy_positions` / `strategy_open_lots` in `risk/attribution.py`) — so runner positions are **not** Alpaca net by design during concurrent same-symbol operation. Account-level risk caps still use live broker positions. Reconciliation surfaces per-strategy vs broker divergence as informational WARN only (not R-OPS-004 incidents). Same-symbol co-run is now **allowed** (the launch-time eval-symbol guard was removed 2026-06-15, ADR 0026 addendum superseded): the three invariants it proxied are closed in the risk/execution layers — PR1 paper submit serialization (ADR 0056 amended), PR2 opposite-side resting-order veto (`_check_opposite_side_order`), PR3 per-strategy cap reads the strategy ledger (ADR 0055 amended). Cross-process submit serialization is now in place for all non-backtest submits — `paper`/`micro_live`/`live` (per-account advisory lock, ADR 0056); per-symbol advisory locks remain deferred. One fail-safe same-symbol residual remains (not unsafe): partial-fill ledger reconciliation (`RISK_POLICY.md` #5) — fails closed for the cap, nil in the paper regime. The duplicate-order veto is now **per-strategy** (PR5): `_check_duplicate_order` scopes `count_recent_submitted_orders` to the proposing strategy via the durable event-store path (the account-wide broker `recent_orders` path was dropped — it carried no strategy tag and only added a cross-strategy false-veto). See ADR 0026 addendum 2026-06-15.

# Milodex

Personal autonomous trading system. See `docs/VISION.md` for the full project vision, and **`docs/FOUNDER_INTENT.md` for the founder's personal intent** — the deeper "why" that should guide product, UX, and documentation decisions. When in doubt about tone, scope, or tradeoffs, defer to FOUNDER_INTENT.

## Commands

```bash
pip install -e ".[dev]"      # Install in editable mode with dev deps
python -m pytest             # Run tests (bare `pytest`/`ruff` may not be on PATH; use `python -m`)
python -m ruff check src/ tests/    # Lint
python -m ruff format src/ tests/   # Format
```

## Architecture

src-layout Python package (`src/milodex/`). Twelve modules:

- **broker/** — Brokerage API integration (Alpaca). All broker access goes through this interface.
- **strategies/** — Config-driven strategy definitions. No hardcoded strategy logic — parameters live in `configs/*.yaml`.
- **risk/** — Risk management layer. Sits between strategies and execution with **veto power** over all trades. Never bypass.
- **execution/** — Trade orchestration service. Single chokepoint from intent → trade: invokes the risk layer, records explanations, submits to broker. No code path reaches the broker without passing through here.
- **promotion/** — Promotion lifecycle surface (ADR 0015): frozen strategy manifests, evidence, and stage-transition governance. The risk layer reads back the active manifest hash from here.
- **backtesting/** — Backtest engine with walk-forward validation. Minimum 30 trades before statistical conclusions. Intentionally below the risk layer — risk is enforced at promotion, not simulation.
- **data/** — Market data acquisition. Start with free sources (Alpaca, Yahoo Finance). Premium only if testing justifies cost.
- **analytics/** — Performance metrics, trade logging, benchmark comparison (vs SPY).
- **core/** — Shared infrastructure: SQLite event store (ADR 0011), advisory locks, schema migrations. Source of truth for trade, explanation, kill-switch, strategy-run, and backtest-run history. Durable state lives under `data/` per ADR 0018.
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
- Backtest slippage defaults to 0.1–0.2% — don't assume zero slippage
- Pattern day trader rule: under $25k capital means no same-day round trips (daily swing avoids this)
- **`.venv` must be a stdlib `python -m venv`, not a `uv venv` trampoline.** A uv/trampoline `.venv\Scripts\python.exe` re-execs the *base* interpreter, so the code runs under the base Python's site-packages — if the base has a broken/missing dep (e.g. corrupt pandas), runners die on import. Symptom: GUI-launched runners produce no explanations / "phantom" runners; fix + 5-min diagnostic in `docs/TROUBLESHOOTING.md`. The redirector+base-child PID *pair* per process is normal Windows venv behaviour — not a bug.
- **Daily (`1D`) strategies are a no-op while the market is OPEN** (`runner.py` market-hours gate returns `[]` before any fetch). "Runner running but 0 explanations" during market hours is BY DESIGN, not a stall — it evaluates after close + the lockin window. Don't debug a non-bug.
- **Diagnostic surface:** event store `data/milodex.db` (SQLite) — `strategy_runs.ended_at IS NULL` = open/"running" (no liveness check; a dead one shows as phantom until #161 bootstrap reconcile); `explanations` = per-decision audit keyed by `session_id`; advisory locks in `data/locks/*.lock`; per-runner logs `logs/runner.<sid>.<ts>.log`. Inspect columns via `pragma table_info`.
- **Controlled-stop ("Stop Trading") needs a live, cooperative runner** to consume the request file. It hangs/no-ops on a wedged or already-dead runner — for those, hard-kill the PID and clear `data/locks/*.lock` instead.

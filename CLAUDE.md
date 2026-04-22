# Milodex

Personal autonomous trading system. See `docs/VISION.md` for the full project vision, and **`docs/FOUNDER_INTENT.md` for the founder's personal intent** — the deeper "why" that should guide product, UX, and documentation decisions. When in doubt about tone, scope, or tradeoffs, defer to FOUNDER_INTENT.

## Commands

```bash
pip install -e ".[dev]"      # Install in editable mode with dev deps
pytest                        # Run tests
ruff check src/ tests/        # Lint
ruff format src/ tests/       # Format
```

## Architecture

src-layout Python package (`src/milodex/`). Nine modules:

- **broker/** — Brokerage API integration (Alpaca). All broker access goes through this interface.
- **strategies/** — Config-driven strategy definitions. No hardcoded strategy logic — parameters live in `configs/*.yaml`.
- **risk/** — Risk management layer. Sits between strategies and execution with **veto power** over all trades. Never bypass.
- **execution/** — Trade orchestration service. Single chokepoint from intent → trade: invokes the risk layer, records explanations, submits to broker. No code path reaches the broker without passing through here.
- **backtesting/** — Backtest engine with walk-forward validation. Minimum 30 trades before statistical conclusions. Intentionally below the risk layer — risk is enforced at promotion, not simulation.
- **data/** — Market data acquisition. Start with free sources (Alpaca, Yahoo Finance). Premium only if testing justifies cost.
- **analytics/** — Performance metrics, trade logging, benchmark comparison (vs SPY).
- **core/** — Shared infrastructure: SQLite event store (ADR 0011), advisory locks, schema migrations. Source of truth for trade, explanation, kill-switch, strategy-run, and backtest-run history. Durable state lives under `data/` per ADR 0018.
- **cli/** — Command-line interface. Primary interaction surface.

## Key Design Rules

- **Risk layer is sacred.** Every trade passes through `risk/` before execution. Strategy proposes, risk disposes. Never bypass or weaken for convenience.
- **Strategies are config-driven.** Strategy parameters live in `configs/*.yaml`, not in code. The code defines behavior; config defines tuning.
- **Promotion pipeline is mandatory.** Stages: backtest → paper → micro_live → live. No skipping stages. Thresholds: Sharpe > 0.5, max drawdown < 15%, minimum 30 trades.
- **Kill switch requires manual reset.** When triggered, trading halts. Auto-resume is never acceptable.
- **Actions that always require explicit human approval:** promoting any strategy to live, allocating or increasing real capital to a live strategy, re-enabling after any kill switch or major risk event, changing core risk limits for live, granting a new broker live-trade permission, overriding a blocked or rejected execution decision, retiring or replacing a live strategy with a materially different version. See `docs/VISION.md` "Autonomy Boundary" for the authoritative list.
- **Two strategies, two purposes.** Phase 1 runs a **lifecycle-proof strategy** (SPY/SHY 200-DMA regime) to validate the platform end-to-end, and a **mean-reversion research-target strategy** as the first real edge hunt. The two are configured and promoted separately — the lifecycle-proof strategy is exempt from the 30-trade / Sharpe thresholds because a regime strategy can't produce them. See `docs/SRS.md` Key Terms.

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

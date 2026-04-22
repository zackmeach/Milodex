# Milodex

Personal autonomous trading system. See `docs/VISION.md` for full project vision.

## Commands

```bash
pip install -e ".[dev]"      # Install in editable mode with dev deps
pytest                        # Run tests
ruff check src/ tests/        # Lint
ruff format src/ tests/       # Format
```

## Architecture

src-layout Python package (`src/milodex/`). Seven modules:

- **broker/** — Brokerage API integration (Alpaca). All broker access goes through this interface.
- **strategies/** — Config-driven strategy definitions. No hardcoded strategy logic — parameters live in `configs/*.yaml`.
- **risk/** — Risk management layer. Sits between strategies and execution with **veto power** over all trades. Never bypass.
- **backtesting/** — Backtest engine with walk-forward validation. Minimum 30 trades before statistical conclusions.
- **data/** — Market data acquisition. Start with free sources (Alpaca, Yahoo Finance). Premium only if testing justifies cost.
- **analytics/** — Performance metrics, trade logging, benchmark comparison (vs SPY).
- **cli/** — Command-line interface. Primary interaction surface.

## Key Design Rules

- **Risk layer is sacred.** Every trade passes through `risk/` before execution. Strategy proposes, risk disposes. Never bypass or weaken for convenience.
- **Strategies are config-driven.** Strategy parameters live in `configs/*.yaml`, not in code. The code defines behavior; config defines tuning.
- **Promotion pipeline is mandatory.** Stages: backtest → paper → micro_live → live. No skipping stages. Thresholds: Sharpe > 0.5, max drawdown < 15%, minimum 30 trades.
- **Kill switch requires manual reset.** When triggered, trading halts. Auto-resume is never acceptable.
- **Three actions always require human review:** re-enabling after kill switch, deploying to live capital, increasing position size limits.

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

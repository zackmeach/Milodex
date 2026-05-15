# ADR 0020 — Promotion Thresholds Are Code-Level Invariants

**Status:** Accepted
**Date:** 2026-04-22
**Relates to:** ADR 0009 (promotion pipeline stage model), ADR 0003 (config-driven strategies)

**Implementation update (2026-05):** Later promotion work split the gate by target stage and made the trade-count floor cadence-aware. `MIN_SHARPE`, `MAX_DRAWDOWN_PCT`, and `MIN_TRADES` remain code-level defaults/invariants for the strict capital-readiness gate; `PAPER_MIN_SHARPE` and `PAPER_MAX_DRAWDOWN_PCT` define the paper-readiness gate; and `check_gate(min_trade_count=...)` can receive a strategy's `backtest.min_trades_required` value instead of always using `MIN_TRADES`.

## Context

Three numeric thresholds gate statistical promotion from `paper` to `micro_live`:

- Sharpe ratio > 0.5 (SRS R-PRM-001)
- Maximum drawdown < 15% (SRS R-PRM-002)
- Trade count at or above the applicable floor, defaulting to 30 (SRS R-PRM-004, R-BKT-004)

Today these live as Python constants in [src/milodex/strategies/promotion.py](../../src/milodex/strategies/promotion.py):

```python
MIN_SHARPE: float = 0.5
MAX_DRAWDOWN_PCT: float = 15.0
MIN_TRADES: int = 30  # default floor; strategies may supply min_trade_count
```

`CLAUDE.md` states *"Strategies are config-driven. Strategy parameters live in `configs/*.yaml`, not in code."* Read literally, that rule says these thresholds belong in YAML — most naturally in `configs/risk_defaults.yaml` alongside the other global guardrails.

The 2026-04-22 architecture health review flagged the inconsistency and asked for an explicit decision: **are promotion thresholds tuning (config) or invariants (code)?**

## Decision

Promotion thresholds remain Python constants in `strategies/promotion.py`. They are **invariants**, not tuning parameters. The "config-driven" rule applies to **strategy parameters** (RSI period, SMA length, universe selection, per-strategy risk caps) — not to **governance thresholds** that decide when any strategy is allowed to risk real money.

Specifically:

1. `MIN_SHARPE`, `MAX_DRAWDOWN_PCT`, and the default `MIN_TRADES` floor stay as module-level constants in `promotion.py`.
2. These global defaults are **not** added to `configs/risk_defaults.yaml`; strategy YAML may declare `backtest.min_trades_required` as the per-strategy evidence floor.
3. Changing them requires editing Python code, passing code review (for a future multi-contributor state), and a git commit — i.e. the same friction as changing any other invariant in the codebase.
4. The lifecycle-exempt bypass (ADR 0009, per `promotion_type='lifecycle_exempt'`) stays as the only legitimate way to promote a strategy without meeting these thresholds. Lifecycle-exempt status is granted per-strategy, not per-run, and is itself a code-reviewed decision.

## Rationale

- **Governance vs tuning is a meaningful distinction.** Strategy parameters tune *what a strategy does*. Promotion thresholds decide *which strategies are allowed to exist in production*. The first is a research question; the second is a policy question. Putting them in the same file invites conflating them.
- **Friction is the feature.** The whole point of a promotion gate is that it should be annoying to loosen. YAML files are optimized for fast iteration — exactly the wrong signal for a guardrail. A Python constant that requires a commit to change is harder to "temporarily relax for one strategy" than a YAML key.
- **The SRS is the normative source.** R-PRM-004 names the strict capital-stage numbers (0.5, 15%) and the default trade-count floor (30). That makes them normative defaults/invariants by the ADR-README authority order (SRS outranks config schemas), while strategy config may raise or lower the evidence floor for cadence via `backtest.min_trades_required`.
- **Auditability is already solved.** Git history is the audit log for any code change. Moving thresholds to YAML does not add auditability — every YAML change would be committed anyway — it only shifts the file being audited.
- **Config fingerprinting (ADR 0015) doesn't need these values.** The config hash captures *per-strategy* reproducibility. Promotion thresholds are global and apply uniformly; they are not part of a strategy's config and don't belong in its fingerprint.
- **The "config-driven" rule is narrower than it reads.** ADR 0003 scopes it to strategy *parameters and universe selection*. Governance thresholds were never in scope — this ADR makes that explicit so a future reader doesn't re-litigate the question.

## Consequences

- `promotion.py` constants remain the single source of truth for global gate thresholds and the default trade-count floor. If the SRS numbers change, the constants are updated in the same commit as the SRS edit.
- `configs/risk_defaults.yaml` continues to hold only per-run, per-account operational guardrails (position caps, daily loss, kill-switch thresholds). It stays the right place for values that an operator might reasonably tune between runs.
- Adding a new promotion threshold (e.g. a minimum backtest-window duration) is a code change plus an SRS requirement addition plus this ADR getting referenced. That is the intended level of friction.
- Strategies that cannot meet the statistical thresholds (e.g. regime strategies that trade too infrequently) continue to use the `lifecycle_exempt` escape hatch. That escape hatch is not expanded.

## Non-goals

- This ADR does **not** change the strict capital-stage threshold values. 0.5 / 15% / default 30 remain as specified by SRS R-PRM-004.
- This ADR does **not** argue the values are *correct* — only that their storage location is code, not YAML. The values themselves are revisited in the SRS if experience shows they are wrong.
- This ADR does **not** apply to per-strategy risk caps (`max_position_pct`, `max_positions`, `daily_loss_cap_pct` in a strategy YAML). Those remain config-driven — they are tuning, not governance.
- This ADR does **not** restrict `configs/risk_defaults.yaml` from gaining new keys. It only asserts that promotion thresholds are not among them.

## Update to `CLAUDE.md`

The "Strategies are config-driven" rule in `CLAUDE.md` is refined (not revised) to clarify that it applies to strategy-level tuning. Governance thresholds (promotion gates) are invariants in code. The existing "Promotion pipeline is mandatory" rule in `CLAUDE.md` already lists the three numeric thresholds inline — that inline listing is now the authoritative copy alongside the SRS, and the code constants must match it.

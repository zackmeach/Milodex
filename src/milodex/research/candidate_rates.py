"""Measure a candidate's per-symbol round-trip rate for the matched baseline.

The random matched-exposure baseline (``bench_random_matched_exposure_long``)
matches the candidate's trade COUNT in expectation via a per-symbol
``session_entry_rate``. This module measures that rate from the candidate's own
per-symbol walk-forward results:

    rate[symbol] = oos_round_trip_count / oos_trading_days

Both fields are read from the SAME :class:`WalkForwardResult`. The numerator is
``oos_round_trip_count`` (round-trips ≈ entries), NOT ``oos_trade_count`` (which
counts buy+sell FILLS ≈ 2× round-trips and would set the rate 2× too high). The
result is clamped to ``[0, 1]``.

Note on the denominator: ``oos_trading_days`` includes half-days, which the
baseline skips — a known, small under-count (~1%/yr) that biases the matched
rate slightly high. Acceptable for a null baseline.

Precondition: the candidate's per-symbol configs must exist in the configs
directory and their 5Min caches must be warmed (Tier-0 fan-out + warmup). When
``run_one`` is left to its default, an unwarmed cache surfaces as a per-symbol
backtest error from the engine.

A measured rate of exactly ``0.0`` means an always-flat baseline (degenerate
null) — this emits a WARNING rather than silently accepting it.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from milodex.backtesting.walk_forward_runner import WalkForwardResult
    from milodex.cli._shared import CommandContext

logger = logging.getLogger(__name__)


class _RateResult(Protocol):
    """The two fields ``measure_candidate_rates`` reads off a walk-forward run."""

    oos_round_trip_count: int
    oos_trading_days: int


class _RunOne(Protocol):
    def __call__(self, strategy_id: str, *, start: date, end: date) -> tuple[str, _RateResult]: ...


def measure_candidate_rates(
    *,
    candidate_strategy_ids: Sequence[str],
    start: date,
    end: date,
    ctx: CommandContext | None = None,
    run_one: _RunOne | None = None,
) -> dict[str, float]:
    """Return ``{symbol: session_entry_rate}`` for each candidate config.

    Runs each per-symbol candidate walk-forward over ``[start, end]`` and
    computes ``oos_round_trip_count / oos_trading_days``, clamped to ``[0, 1]``.

    ``run_one`` is the per-strategy runner: ``(strategy_id) -> (symbol, result)``.
    It defaults to a production runner built from ``ctx`` (which must then be
    provided). Injecting ``run_one`` keeps the rate arithmetic unit-testable
    without spinning up the backtest engine.
    """
    if run_one is None:
        if ctx is None:
            msg = "measure_candidate_rates requires either ctx or run_one"
            raise ValueError(msg)
        run_one = _make_default_run_one(ctx)

    rates: dict[str, float] = {}
    for strategy_id in candidate_strategy_ids:
        symbol, result = run_one(strategy_id, start=start, end=end)
        rate = _rate_from_result(result)
        if rate == 0.0:
            logger.warning(
                "Candidate %s (%s) measured 0 round-trips over %d OOS trading days — "
                "the matched baseline would be degenerate (always flat). Rate set to 0.0.",
                strategy_id,
                symbol,
                getattr(result, "oos_trading_days", 0),
            )
        rates[symbol] = rate
    return rates


def _rate_from_result(result: _RateResult) -> float:
    trading_days = result.oos_trading_days
    if trading_days <= 0:
        return 0.0
    rate = result.oos_round_trip_count / trading_days
    return max(0.0, min(1.0, rate))


def _make_default_run_one(ctx: CommandContext) -> _RunOne:
    """Build the production per-strategy runner: backtest engine + walk-forward."""

    def run_one(strategy_id: str, *, start: date, end: date) -> tuple[str, WalkForwardResult]:
        from milodex.backtesting.walk_forward_runner import (
            derive_walk_forward_spans,
            run_walk_forward,
        )

        engine = ctx.get_backtest_engine(strategy_id)
        symbol = engine.universe[0]
        all_bars, train_days, test_days, step_days = derive_walk_forward_spans(engine, start, end)
        result = run_walk_forward(
            engine,
            start_date=start,
            end_date=end,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
            all_bars=all_bars,
        )
        return symbol, result

    return run_one


__all__ = ["measure_candidate_rates"]

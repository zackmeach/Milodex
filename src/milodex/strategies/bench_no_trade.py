"""No-trade baseline. The absolute floor: never trades, P&L == 0.

Answers "is the candidate strategy better than doing nothing?" — the trivial
null every candidate must clear before any other comparison matters. A research
instrument, never promoted past backtest.

See ``docs/STRATEGY_BANK.md`` and the intraday ETF evidence lane for how the
baseline suite (no-trade, time-of-day null, unconditional intraday long) frames
candidate evaluation.
"""

from __future__ import annotations

from milodex.data.models import BarSet
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
)


class BenchNoTradeStrategy(Strategy):
    """Never emits an intent. Works for any universe; SPY config provided."""

    family = "benchmark"
    template = "no_trade"
    parameter_specs = ()

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = (bars, context)
        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(
                rule="no_signal", narrative="no-trade baseline: never trades"
            ),
        )

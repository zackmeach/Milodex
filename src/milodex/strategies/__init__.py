"""Strategy definitions and execution.

Each strategy is a modular, configurable unit that consumes market data
and produces trading signals. Strategies are defined in versioned config
files and must earn promotion through the validation pipeline before
touching live capital.
"""

from milodex.strategies.base import Strategy, StrategyContext, StrategyParameterSpec
from milodex.strategies.loader import (
    LoadedStrategy,
    StrategyConfig,
    StrategyLoader,
    StrategyRegistry,
    build_default_registry,
    compute_config_hash,
    load_strategy_config,
    resolve_universe_ref,
    validate_strategy_parameters,
)
from milodex.strategies.breakout_donchian import BreakoutDonchianStrategy
from milodex.strategies.meanrev_ibs_lowclose import MeanrevIbsLowcloseStrategy
from milodex.strategies.meanrev_rsi2_pullback import MeanrevRsi2PullbackStrategy
from milodex.strategies.momentum_daily_tsmom import MomentumDailyTsmomStrategy
from milodex.strategies.momentum_dual_absolute_gem import MomentumDualAbsoluteGemStrategy
from milodex.strategies.momentum_xsec_rotation import MomentumXsecRotationStrategy
from milodex.strategies.regime_spy_shy_200dma import RegimeSpyShy200DmaStrategy

__all__ = [
    "BreakoutDonchianStrategy",
    "LoadedStrategy",
    "MeanrevIbsLowcloseStrategy",
    "MeanrevRsi2PullbackStrategy",
    "MomentumDailyTsmomStrategy",
    "MomentumDualAbsoluteGemStrategy",
    "MomentumXsecRotationStrategy",
    "RegimeSpyShy200DmaStrategy",
    "Strategy",
    "StrategyConfig",
    "StrategyContext",
    "StrategyLoader",
    "StrategyParameterSpec",
    "StrategyRegistry",
    "build_default_registry",
    "compute_config_hash",
    "load_strategy_config",
    "resolve_universe_ref",
    "validate_strategy_parameters",
]

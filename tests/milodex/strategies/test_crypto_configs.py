"""Crypto canary configs load through the real loader + registry.

Proves the archetype is *representable*: the YAML validates (including the new
30Min bar size), the strict strategy-id convention is satisfied, the registry
resolves the family/template to the concrete class, and parameters validate
against each class's declared spec.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from milodex.strategies.loader import StrategyLoader, build_default_registry
from milodex.strategies.meanrev_crypto_rsi2 import MeanrevCryptoRsi2Strategy
from milodex.strategies.momentum_crypto_ema_cross import MomentumCryptoEmaCrossStrategy

REPO = Path(__file__).resolve().parents[3]
CONFIGS = REPO / "configs"

MOMENTUM_CONFIG = CONFIGS / "momentum_crypto_ema_cross_btc_usd_1h_v1.yaml"
MEANREV_CONFIG = CONFIGS / "meanrev_crypto_rsi2_btc_usd_30m_v1.yaml"


def test_registry_resolves_both_crypto_templates() -> None:
    registry = build_default_registry()
    assert registry.resolve("momentum", "crypto.ema_cross") is MomentumCryptoEmaCrossStrategy
    assert registry.resolve("meanrev", "crypto.rsi2") is MeanrevCryptoRsi2Strategy


def test_momentum_config_loads_and_validates() -> None:
    loaded = StrategyLoader().load(MOMENTUM_CONFIG)
    assert loaded.config.strategy_id == "momentum.crypto.ema_cross.btc_usd_1h.v1"
    assert loaded.config.stage == "backtest"
    assert loaded.config.tempo["bar_size"] == "1H"
    assert loaded.config.universe == ("BTC/USD",)
    assert isinstance(loaded.strategy, MomentumCryptoEmaCrossStrategy)


def test_meanrev_config_loads_and_validates() -> None:
    loaded = StrategyLoader().load(MEANREV_CONFIG)
    assert loaded.config.strategy_id == "meanrev.crypto.rsi2.btc_usd_30m.v1"
    assert loaded.config.stage == "backtest"
    assert loaded.config.tempo["bar_size"] == "30Min"
    assert loaded.config.universe == ("BTC/USD",)
    assert isinstance(loaded.strategy, MeanrevCryptoRsi2Strategy)


@pytest.mark.parametrize("config_path", [MOMENTUM_CONFIG, MEANREV_CONFIG])
def test_config_is_backtest_only_and_disabled_for_runtime(config_path: Path) -> None:
    """Backtest-only canaries: stage=backtest (inert) and not paper-promoted."""
    loaded = StrategyLoader().load(config_path)
    assert loaded.config.stage == "backtest"

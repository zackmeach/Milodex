"""Verification #5 — the risk layer governs a non-rule decider's intent.

The backtest path injects ``NullRiskEvaluator`` (BYPASS) by design, so it
*cannot* demonstrate a veto. This standalone unit test constructs the **real**
``RiskEvaluator`` and feeds it a ``TradeIntent`` actually emitted by each
non-rule decider, priced so the order breaches the order-value cap. The
evaluator must refuse it — proving the harness governs a non-rule technique's
intent identically to a rule's (the same evaluator, the same caps, the same
veto), with the technique holding no special status.

This is the only verification that demonstrates risk pass-through; the
backtest does not (and the brief forbids claiming it does).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pandas as pd

from milodex.broker.models import AccountInfo, OrderSide, OrderType, TimeInForce
from milodex.data.models import Bar, BarSet
from milodex.execution.models import ExecutionRequest, TradeIntent
from milodex.execution.state import KillSwitchState
from milodex.risk import (
    EvaluationContext,
    ReconciliationReadiness,
    RiskDefaults,
    RiskEvaluator,
)
from milodex.strategies.base import StrategyContext
from milodex.strategies.scored_linear_features import ScoredLinearFeaturesStrategy
from milodex.strategies.tree_bucketed_lookup import TreeBucketedLookupStrategy

_DEFAULTS = RiskDefaults(
    kill_switch_enabled=True,
    kill_switch_max_drawdown_pct=0.10,
    require_manual_reset=True,
    max_single_position_pct=0.20,
    max_concurrent_positions=3,
    max_total_exposure_pct=0.80,
    max_daily_loss_pct=0.03,
    max_trades_per_day=20,
    max_order_value_pct=0.15,
    duplicate_order_window_seconds=60,
    max_data_staleness_seconds=300,
)

_SCORED_PARAMS: dict[str, object] = {
    "momentum_lookback": 63,
    "rsi_lookback": 14,
    "ma_length": 50,
    "vol_lookback": 20,
    "feature_weights": {"momentum": 1.0, "ma_distance": 0.5, "rsi": -0.2, "realized_vol": -0.5},
    "target_positions": 2,
    "exit_rank_buffer": 1,
    "max_concurrent_positions": 3,
    "per_position_notional_pct": 0.45,
    "stop_loss_pct": 0.10,
    "max_hold_days": 10,
}

_TREE_PARAMS: dict[str, object] = {
    "momentum_lookback": 63,
    "rsi_lookback": 14,
    "momentum_split": 0.0,
    "rsi_split_strong": 70.0,
    "rsi_split_dip": 30.0,
    "leaf_actions": {
        "strong_buy": {"action": "enter", "priority": 3},
        "trend_follow": {"action": "enter", "priority": 2},
        "dip_buy": {"action": "enter", "priority": 1},
        "neutral_skip": {"action": "skip", "priority": 0},
    },
    "target_positions": 2,
    "max_concurrent_positions": 3,
    "per_position_notional_pct": 0.45,
    "stop_loss_pct": 0.10,
    "max_hold_days": 10,
}


def test_real_risk_layer_vetoes_scored_decider_oversized_intent() -> None:
    intent = _first_buy_intent(
        ScoredLinearFeaturesStrategy(),
        params=_SCORED_PARAMS,
        strategy_id="scored.daily.linear_features.sector_etfs.v1",
        family="scored",
        template="daily.linear_features",
    )
    _assert_cap_governs(intent)


def test_real_risk_layer_vetoes_tree_decider_oversized_intent() -> None:
    intent = _first_buy_intent(
        TreeBucketedLookupStrategy(),
        params=_TREE_PARAMS,
        strategy_id="tree.daily.bucketed_lookup.sector_etfs.v1",
        family="tree",
        template="daily.bucketed_lookup",
    )
    _assert_cap_governs(intent)


def _assert_cap_governs(intent: TradeIntent) -> None:
    """The same decider intent is vetoed above the order-value cap and allowed
    below it — a real veto, not a constant deny."""
    portfolio = 1_000.0
    cap = portfolio * _DEFAULTS.max_order_value_pct  # 150.0

    blocked = RiskEvaluator().evaluate(
        _veto_context(intent, estimated_order_value=cap + 1.0, portfolio=portfolio)
    )
    assert blocked.allowed is False
    order_value_check = next(c for c in blocked.checks if c.name == "order_value")
    assert order_value_check.passed is False
    assert order_value_check.reason_code == "max_order_value_exceeded"

    allowed = RiskEvaluator().evaluate(
        _veto_context(intent, estimated_order_value=cap - 1.0, portfolio=portfolio)
    )
    assert allowed.allowed is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_buy_intent(
    strategy,
    *,
    params: dict[str, object],
    strategy_id: str,
    family: str,
    template: str,
) -> TradeIntent:
    bars = {f"S{k}": _make_bars(_series(k)) for k in range(8)}
    context = StrategyContext(
        strategy_id=strategy_id,
        family=family,
        template=template,
        variant="sector_etfs",
        version=1,
        config_hash="hash",
        parameters=params,
        universe=tuple(bars),
        universe_ref="universe.sector_etfs_spdr.v1",
        disable_conditions=(),
        config_path="configs/test.yaml",
        manifest={},
        positions={},
        equity=100_000.0,
        bars_by_symbol=bars,
        entry_state={},
    )
    decision = strategy.evaluate(next(iter(bars.values())), context)
    buys = [intent for intent in decision.intents if intent.side is OrderSide.BUY]
    assert buys, "the decider must emit a BUY intent to feed the risk evaluator"
    return buys[0]


def _veto_context(
    intent: TradeIntent, *, estimated_order_value: float, portfolio: float
) -> EvaluationContext:
    """Build an EvaluationContext where every rule passes except the
    order-value cap, which is exercised by ``estimated_order_value``.

    In the real pipeline ``estimated_order_value`` is ``quantity * price``;
    setting it directly is exactly how the order-value cap is exercised
    (mirrors ``tests/milodex/risk/test_risk_rules.py``).
    """
    request = ExecutionRequest(
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.quantity,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        estimated_unit_price=estimated_order_value / intent.quantity,
        estimated_order_value=estimated_order_value,
    )
    account = AccountInfo(
        equity=portfolio,
        cash=portfolio,
        buying_power=portfolio,
        portfolio_value=portfolio,
        daily_pnl=0.0,
    )
    return EvaluationContext(
        intent=intent,
        request=request,
        account=account,
        positions=[],
        recent_orders=[],
        reconciliation_readiness=ReconciliationReadiness(
            ready=True,
            reason_code=None,
            message="test clean reconciliation",
            local_trading_day="2026-05-25",
            status="clean",
            broker_connected=True,
        ),
        latest_bar=_fresh_bar(),
        market_open=True,
        trading_mode="paper",
        preview_only=False,
        kill_switch_state=KillSwitchState(active=False),
        risk_defaults=_DEFAULTS,
    )


def _fresh_bar() -> Bar:
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=10),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )


def _series(k: int, n: int = 90) -> list[float]:
    drift = (k - 4) * 0.003
    amp = 0.02 + 0.01 * (k % 4)
    phase = 0.6 * k
    return [100.0 * (1.0 + drift) ** i * (1.0 + amp * math.sin(0.25 * i + phase)) for i in range(n)]


def _make_bars(closes: list[float]) -> BarSet:
    n = len(closes)
    timestamps = pd.date_range(pd.Timestamp("2023-01-02", tz=UTC), periods=n, freq="D")
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
            "vwap": closes,
        }
    )
    return BarSet(frame)

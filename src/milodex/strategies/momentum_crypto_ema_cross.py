"""EMA-cross momentum crypto canary on BTC/USD (1-hour bars, long-only).

Implements the ``momentum`` family's ``crypto.ema_cross`` template — a
backtest-only canary proving the crypto-spot archetype through the existing
research/backtest path:

- Single-name, long-only, one position max, no pyramiding, no leverage, no
  shorting. Spot crypto, fractional sizing.
- 24/7: indicators are computed over the **continuous** close history (every
  completed bar), with NO session reset and NO market-hours gating. The
  strategy deliberately does NOT import ``_session_intraday`` (which encodes
  US-equity cash-session concepts that do not apply to a 24/7 market).
- Entry (flat): fast EMA above slow EMA after warmup → BUY a fractional
  position sized to ``per_position_notional_pct`` of equity.
- Exits (priority): (1) ``stop_loss_pct`` below the recorded entry price,
  (2) fast EMA falls below slow EMA (trend invalidation).
- Fill executes at the *next* bar's open (engine T+1 semantics — no
  lookahead). No intrabar decisions; the engine only ever passes completed
  bars.

STAGE = backtest. NOT a paper/live candidate. This is a harness proof, not an
alpha claim — defaults are boring, not tuned. An honest comparator would be an
unconditional buy-and-hold of BTC/USD on identical friction; building that
benchmark strategy is deferred with the crypto data-ingestion task.

Rules in code below. Config: configs/momentum_crypto_ema_cross_btc_usd_1h_v1.yaml.
Shared indicators: src/milodex/strategies/_indicators.py.
"""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import fractional_units_for_notional_pct
from milodex.strategies._indicators import ema_series
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
)


class MomentumCryptoEmaCrossStrategy(Strategy):
    """EMA-cross momentum on single-name BTC/USD (long-only, 24/7)."""

    family = "momentum"
    template = "crypto.ema_cross"
    parameter_specs = (
        StrategyParameterSpec("fast_ema_period", expected_types=(int,)),
        StrategyParameterSpec("slow_ema_period", expected_types=(int,)),
        StrategyParameterSpec("stop_loss_pct", expected_types=(int, float)),
        StrategyParameterSpec("per_position_notional_pct", expected_types=(int, float)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars  # primary barset is read via context.bars_by_symbol below

        params = _validated_parameters(context)

        universe_symbols = sorted({symbol.upper() for symbol in context.universe})
        if not universe_symbols:
            return _no_signal("empty universe")
        primary_symbol = universe_symbols[0]

        barset = context.bars_by_symbol.get(primary_symbol)
        if barset is None or len(barset) == 0:
            return _no_signal(f"no bar data for {primary_symbol}")

        df = barset.to_dataframe()
        closes = df["close"].astype(float)
        latest_close = float(closes.iloc[-1])

        slow_period = params["slow_ema_period"]
        if len(closes) <= slow_period:
            return _no_signal(
                f"insufficient bars ({len(closes)}) for slow EMA warmup ({slow_period})"
            )

        fast = float(ema_series(closes, params["fast_ema_period"]).iloc[-1])
        slow = float(ema_series(closes, slow_period).iloc[-1])
        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # --- Position open: exits (stop_loss > trend invalidation). ---
        if open_qty > 0:
            entry_price = _entry_price(context, primary_symbol)
            stop_loss_pct = params["stop_loss_pct"]
            if entry_price is not None and latest_close <= entry_price * (1 - stop_loss_pct):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="momentum.crypto.ema_cross.stop_loss",
                    narrative=(
                        f"latest close {latest_close:.2f} breached stop "
                        f"{stop_loss_pct:.2%} below entry {entry_price:.2f} → exit"
                    ),
                    triggering_values={"latest_close": latest_close, "entry_price": entry_price},
                    threshold={"stop_loss_pct": stop_loss_pct},
                )
            if fast < slow:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="momentum.crypto.ema_cross.cross_down",
                    narrative=(
                        f"fast EMA {fast:.2f} fell below slow EMA {slow:.2f} → "
                        f"trend invalidated, exit {primary_symbol}"
                    ),
                    triggering_values={"fast_ema": fast, "slow_ema": slow},
                    threshold={"slow_ema": slow},
                )
            return _no_signal(
                f"holding {primary_symbol}: fast EMA {fast:.2f} >= slow EMA {slow:.2f}, "
                f"trend intact, not stopped"
            )

        # --- Flat: enter on fast-above-slow. ---
        if fast <= slow:
            return _no_signal(
                f"fast EMA {fast:.2f} not above slow EMA {slow:.2f} — no uptrend, stay flat"
            )

        units = fractional_units_for_notional_pct(
            equity=context.equity,
            notional_pct=params["per_position_notional_pct"],
            unit_price=latest_close,
        )
        if units <= 0:
            return _no_signal(
                f"insufficient equity {context.equity:.2f} for a position at {latest_close:.2f}"
            )
        intent = TradeIntent(
            symbol=primary_symbol,
            side=OrderSide.BUY,
            quantity=float(units),
            order_type=OrderType.MARKET,
        )
        return StrategyDecision(
            intents=[intent],
            reasoning=DecisionReasoning(
                rule="momentum.crypto.ema_cross.entry",
                narrative=(
                    f"fast EMA {fast:.2f} above slow EMA {slow:.2f} — buy {units} "
                    f"{primary_symbol} for trend continuation"
                ),
                triggering_values={
                    "fast_ema": fast,
                    "slow_ema": slow,
                    "latest_close": latest_close,
                },
                threshold={"slow_ema": slow},
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    fast_ema_period = int(required("fast_ema_period"))
    slow_ema_period = int(required("slow_ema_period"))
    if fast_ema_period < 1:
        msg = f"fast_ema_period must be >= 1, got {fast_ema_period}"
        raise ValueError(msg)
    if slow_ema_period <= fast_ema_period:
        msg = (
            "require slow_ema_period > fast_ema_period, got "
            f"fast={fast_ema_period}, slow={slow_ema_period}"
        )
        raise ValueError(msg)

    stop_loss_pct = float(required("stop_loss_pct"))
    if not 0 < stop_loss_pct <= 0.5:
        msg = f"stop_loss_pct must be in (0, 0.5], got {stop_loss_pct!r}"
        raise ValueError(msg)

    per_position_notional_pct = float(required("per_position_notional_pct"))
    if not 0 < per_position_notional_pct <= 1:
        msg = f"per_position_notional_pct must be in (0, 1], got {per_position_notional_pct!r}"
        raise ValueError(msg)

    return {
        "fast_ema_period": fast_ema_period,
        "slow_ema_period": slow_ema_period,
        "stop_loss_pct": stop_loss_pct,
        "per_position_notional_pct": per_position_notional_pct,
    }


def _entry_price(context: StrategyContext, symbol: str) -> float | None:
    state = context.entry_state.get(symbol) if context.entry_state else None
    if not state:
        return None
    entry_price = state.get("entry_price")
    if (
        isinstance(entry_price, int | float)
        and not isinstance(entry_price, bool)
        and entry_price > 0
    ):
        return float(entry_price)
    return None


def _no_signal(narrative: str) -> StrategyDecision:
    return StrategyDecision(
        intents=[],
        reasoning=DecisionReasoning(rule="no_signal", narrative=narrative),
    )


def _exit_decision(
    symbol: str,
    quantity: float,
    *,
    rule: str,
    narrative: str,
    triggering_values: dict[str, Any],
    threshold: dict[str, Any],
) -> StrategyDecision:
    intent = TradeIntent(
        symbol=symbol,
        side=OrderSide.SELL,
        quantity=float(quantity),
        order_type=OrderType.MARKET,
    )
    return StrategyDecision(
        intents=[intent],
        reasoning=DecisionReasoning(
            rule=rule,
            narrative=narrative,
            triggering_values=triggering_values,
            threshold=threshold,
        ),
    )

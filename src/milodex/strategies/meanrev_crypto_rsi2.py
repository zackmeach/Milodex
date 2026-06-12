"""RSI(2) mean-reversion crypto canary on BTC/USD (30-minute bars, long-only).

Implements the ``meanrev`` family's ``crypto.rsi2`` template — the more active
of the two backtest-only crypto canaries, exercising a faster cadence and
fee/slippage sensitivity:

- Single-name, long-only, one position max, no pyramiding, no leverage, no
  shorting. Spot crypto, fractional sizing.
- 24/7: a short-period Wilder RSI is computed over the **continuous** close
  history (every completed bar), with NO session reset and NO market-hours
  gating. Deliberately does NOT import ``_session_intraday``.
- Entry (flat): RSI at/below ``rsi_entry_threshold`` (deeply oversold) → BUY a
  fractional position sized to ``per_position_notional_pct`` of equity.
- Exits (priority): (1) ``stop_loss_pct`` below the recorded entry price,
  (2) RSI reverts to/above ``rsi_exit_threshold`` (mean reversion captured),
  (3) ``max_hold_days`` reached so a position cannot linger forever.
- Fill executes at the *next* bar's open (engine T+1 semantics — no lookahead).

**Max-hold is day-granular.** The backtest engine's held-days accounting ticks
once per outer trading day by design (simulation_kernel.py "tick_held_days"),
so ``max_hold`` is expressed in days, not in 30-minute bars. A true bar-count
max-hold for a sub-day 24/7 strategy would require threading an entry timestamp
through the shared simulation kernel — out of scope for this proof slice. In
practice the RSI-reversion exit dominates; ``max_hold`` is a rare backstop.
One-position-max is itself the anti-churn rule (no re-entry until the prior
position is closed).

STAGE = backtest. NOT a paper/live candidate; a harness proof, not an alpha
claim — defaults are boring, not tuned. Fee drag at 30-minute cadence is more
material than at 1-hour cadence (roughly twice the round-trips for the same
per-trade slippage); see the proof-slice report. Honest comparator = an
unconditional buy-and-hold of BTC/USD on identical friction (deferred with
crypto data ingestion).

Config: configs/meanrev_crypto_rsi2_btc_usd_30m_v1.yaml.
Shared indicators: src/milodex/strategies/_indicators.py.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import fractional_units_for_notional_pct
from milodex.strategies._indicators import wilder_rsi_series
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
    relation_less_than,
)


class MeanrevCryptoRsi2Strategy(Strategy):
    """RSI(2) mean-reversion on single-name BTC/USD (long-only, 24/7)."""

    family = "meanrev"
    template = "crypto.rsi2"
    parameter_specs = (
        StrategyParameterSpec("rsi_lookback", expected_types=(int,), minimum=2, maximum=50),
        StrategyParameterSpec(
            "rsi_entry_threshold", expected_types=(int, float), exclusive_minimum=0
        ),
        StrategyParameterSpec("rsi_exit_threshold", expected_types=(int, float), maximum=100),
        StrategyParameterSpec(
            "stop_loss_pct", expected_types=(int, float), exclusive_minimum=0, maximum=0.5
        ),
        StrategyParameterSpec("max_hold_days", expected_types=(int,), minimum=1),
        StrategyParameterSpec(
            "per_position_notional_pct",
            expected_types=(int, float),
            exclusive_minimum=0,
            maximum=1,
        ),
    )
    parameter_relations = (relation_less_than("rsi_entry_threshold", "rsi_exit_threshold"),)

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

        lookback = params["rsi_lookback"]
        rsi_series = wilder_rsi_series(closes, lookback)
        current_rsi = (
            None if rsi_series.empty or pd.isna(rsi_series.iloc[-1]) else float(rsi_series.iloc[-1])
        )
        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # --- Position open: exits (stop_loss > rsi_exit > max_hold). ---
        if open_qty > 0:
            entry_price = _entry_price(context, primary_symbol)
            stop_loss_pct = params["stop_loss_pct"]
            if entry_price is not None and latest_close <= entry_price * (1 - stop_loss_pct):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.crypto.rsi2.stop_loss",
                    narrative=(
                        f"latest close {latest_close:.2f} breached stop "
                        f"{stop_loss_pct:.2%} below entry {entry_price:.2f} → exit"
                    ),
                    triggering_values={"latest_close": latest_close, "entry_price": entry_price},
                    threshold={"stop_loss_pct": stop_loss_pct},
                )
            if current_rsi is not None and current_rsi >= params["rsi_exit_threshold"]:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.crypto.rsi2.rsi_exit",
                    narrative=(
                        f"RSI {current_rsi:.2f} reverted to/above exit threshold "
                        f"{params['rsi_exit_threshold']} → take profit"
                    ),
                    triggering_values={"rsi": current_rsi},
                    threshold={"rsi_exit_threshold": params["rsi_exit_threshold"]},
                )
            held_days = _held_days(context, primary_symbol)
            if held_days >= params["max_hold_days"]:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.crypto.rsi2.max_hold",
                    narrative=(
                        f"held {held_days} day(s) >= max_hold_days "
                        f"{params['max_hold_days']} → time-stop exit {primary_symbol}"
                    ),
                    triggering_values={"held_days": held_days},
                    threshold={"max_hold_days": params["max_hold_days"]},
                )
            rsi_display = f"{current_rsi:.2f}" if current_rsi is not None else "n/a"
            return _no_signal(
                f"holding {primary_symbol}: RSI {rsi_display}, not stopped/reverted/timed out"
            )

        # --- Flat: enter when oversold. ---
        if current_rsi is None:
            return _no_signal(f"RSI undefined (fewer than {lookback + 1} closes)")
        if current_rsi > params["rsi_entry_threshold"]:
            return _no_signal(
                f"RSI {current_rsi:.2f} not at/below entry threshold "
                f"{params['rsi_entry_threshold']} — not oversold"
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
                rule="meanrev.crypto.rsi2.entry",
                narrative=(
                    f"RSI {current_rsi:.2f} at/below entry threshold "
                    f"{params['rsi_entry_threshold']} — buy {units} {primary_symbol} "
                    f"for mean reversion"
                ),
                triggering_values={"rsi": current_rsi, "latest_close": latest_close},
                threshold={"rsi_entry_threshold": params["rsi_entry_threshold"]},
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    rsi_lookback = int(required("rsi_lookback"))
    if rsi_lookback < 2 or rsi_lookback > 50:
        msg = f"rsi_lookback must be in [2, 50], got {rsi_lookback}"
        raise ValueError(msg)

    rsi_entry_threshold = float(required("rsi_entry_threshold"))
    rsi_exit_threshold = float(required("rsi_exit_threshold"))
    if not 0 < rsi_entry_threshold < rsi_exit_threshold <= 100:
        msg = (
            "require 0 < rsi_entry_threshold < rsi_exit_threshold <= 100, got "
            f"entry={rsi_entry_threshold!r}, exit={rsi_exit_threshold!r}"
        )
        raise ValueError(msg)

    stop_loss_pct = float(required("stop_loss_pct"))
    if not 0 < stop_loss_pct <= 0.5:
        msg = f"stop_loss_pct must be in (0, 0.5], got {stop_loss_pct!r}"
        raise ValueError(msg)

    max_hold_days = int(required("max_hold_days"))
    if max_hold_days < 1:
        msg = f"max_hold_days must be >= 1, got {max_hold_days}"
        raise ValueError(msg)

    per_position_notional_pct = float(required("per_position_notional_pct"))
    if not 0 < per_position_notional_pct <= 1:
        msg = f"per_position_notional_pct must be in (0, 1], got {per_position_notional_pct!r}"
        raise ValueError(msg)

    return {
        "rsi_lookback": rsi_lookback,
        "rsi_entry_threshold": rsi_entry_threshold,
        "rsi_exit_threshold": rsi_exit_threshold,
        "stop_loss_pct": stop_loss_pct,
        "max_hold_days": max_hold_days,
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


def _held_days(context: StrategyContext, symbol: str) -> int:
    state = context.entry_state.get(symbol) if context.entry_state else None
    if not state:
        return 0
    held = state.get("held_days", 0)
    if isinstance(held, int | float) and not isinstance(held, bool) and math.isfinite(held):
        return int(held)
    return 0


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

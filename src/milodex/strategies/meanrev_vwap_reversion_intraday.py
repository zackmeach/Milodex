"""VWAP mean-reversion intraday strategy on SPY.

Implements the ``meanrev`` family's ``vwap_reversion.intraday`` template:

- Single-name, long-only, intraday round-trip on 5min bars (SPY).
- Session VWAP is the cumulative volume-weighted average price from the 9:30
  ET open through the latest completed bar (``session_vwap`` helper).
- Entry: any bar in the entry window ``[opening_range_minutes,
  opening_range_minutes + entry_window_minutes)`` ET where price has stretched
  at least ``entry_deviation_pct`` *below* session VWAP → BUY (betting on
  reversion back toward VWAP).
- Fill executes at the *next* bar's open (engine T+1 fill semantics — no
  lookahead).
- Exits (checked in priority order): (1) ``stop_loss_pct`` from the entry
  price (cut the loser), (2) price reverts to/above session VWAP (target hit),
  (3) time-stop ``exit_minutes_before_close`` before the close (forced flat).
- One entry per session: once a prior in-window bar already met the entry
  deviation, the strategy refuses re-entry — at most one round trip per day
  (mirrors the ORB ``one entry per session`` rule).
- Half-day sessions (early 13:00 ET close): skipped entirely.

Honest framing — VWAP mean-reversion on a liquid index ETF is a well-known
pattern and any edge after realistic slippage is the open question. This
candidate must be measured against ``benchmark.unconditional_intraday_long.spy.v1``
on identical universe/friction; see ``docs/STRATEGY_BANK.md`` for the rubric.
This strategy is long-only because the engine's intraday path models long
round-trips (BUY-to-open / SELL-to-close); it cannot fade *above*-VWAP
stretches, only *below*-VWAP ones.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies._session_intraday import (
    ET_TZ,
    MARKET_OPEN_ET,
    in_entry_window,
    is_half_day,
    is_time_stop_bar,
    session_date_et,
    session_vwap,
    session_vwap_series,
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
    single_symbol,
)


class MeanrevVwapReversionIntradayStrategy(Strategy):
    """VWAP mean-reversion intraday strategy on single-name SPY (long-only)."""

    family = "meanrev"
    template = "vwap_reversion.intraday"
    parameter_specs = (
        StrategyParameterSpec(
            "opening_range_minutes", expected_types=(int,), minimum=5, maximum=120
        ),
        StrategyParameterSpec(
            "entry_window_minutes", expected_types=(int,), minimum=5, maximum=360
        ),
        StrategyParameterSpec(
            "entry_deviation_pct", expected_types=(int, float), exclusive_minimum=0, maximum=0.2
        ),
        StrategyParameterSpec(
            "stop_loss_pct", expected_types=(int, float), exclusive_minimum=0, maximum=0.5
        ),
        StrategyParameterSpec(
            "exit_minutes_before_close", expected_types=(int,), minimum=0, maximum=60
        ),
        StrategyParameterSpec(
            "per_position_notional_pct",
            expected_types=(int, float),
            exclusive_minimum=0,
            maximum=1,
        ),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars  # We read from context.bars_by_symbol for symmetry with other strategies.

        parameters = _validated_parameters(context)

        primary_symbol = single_symbol(context.universe)
        if primary_symbol is None:
            return _no_signal("empty universe")

        barset = context.bars_by_symbol.get(primary_symbol)
        if barset is None or len(barset) == 0:
            return _no_signal(f"no bar data for {primary_symbol}")

        df = barset.to_dataframe()
        latest_ts = df["timestamp"].iloc[-1]
        latest_close = float(df["close"].iloc[-1])
        session_date = session_date_et(latest_ts)

        # Half-day sessions: skip entirely (defensively close any open position).
        if is_half_day(session_date):
            open_qty = float(context.positions.get(primary_symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.vwap.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} open — "
                        f"closing defensively"
                    ),
                    triggering_values={"session_date": session_date.isoformat()},
                    threshold={"reason": "half_day_skip"},
                )
            return _no_signal(f"half-day session {session_date}; VWAP-revert skips half-days")

        vwap = session_vwap(df, session_date)
        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # --- Position open: evaluate exits (stop_loss > target > time-stop). ---
        if open_qty > 0:
            entry_price = _entry_price(context, primary_symbol)
            stop_loss_pct = parameters["stop_loss_pct"]
            if entry_price is not None and latest_close <= entry_price * (1 - stop_loss_pct):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.vwap.stop_loss",
                    narrative=(
                        f"latest close {latest_close:.4f} breached stop "
                        f"{stop_loss_pct:.2%} below entry {entry_price:.4f} → exit"
                    ),
                    triggering_values={"latest_close": latest_close, "entry_price": entry_price},
                    threshold={"stop_loss_pct": stop_loss_pct},
                )
            if vwap is not None and latest_close >= vwap:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.vwap.target",
                    narrative=(
                        f"latest close {latest_close:.4f} reverted to/above session VWAP "
                        f"{vwap:.4f} → take profit"
                    ),
                    triggering_values={"latest_close": latest_close, "session_vwap": vwap},
                    threshold={"session_vwap": vwap},
                )
            if is_time_stop_bar(latest_ts, parameters["exit_minutes_before_close"]):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.vwap.time_stop",
                    narrative=(
                        f"time-stop bar reached ({parameters['exit_minutes_before_close']}min "
                        f"before close) → exit {primary_symbol}"
                    ),
                    triggering_values={"latest_ts": str(latest_ts)},
                    threshold={
                        "exit_minutes_before_close": parameters["exit_minutes_before_close"]
                    },
                )
            vwap_display = f"{vwap:.2f}" if vwap is not None else "n/a"
            return _no_signal(
                f"holding {primary_symbol}: close {latest_close:.2f} vs VWAP "
                f"{vwap_display}, not stopped/timed out"
            )

        # --- Flat: evaluate entry. ---
        if vwap is None:
            return _no_signal("session VWAP undefined (no volume yet)")

        opening_range_minutes = parameters["opening_range_minutes"]
        entry_window_minutes = parameters["entry_window_minutes"]
        if not in_entry_window(latest_ts, opening_range_minutes, entry_window_minutes):
            return _no_signal(
                f"outside entry window [{opening_range_minutes}, "
                f"{opening_range_minutes + entry_window_minutes}) min after open"
            )

        entry_deviation_pct = parameters["entry_deviation_pct"]
        if _already_entered_this_session(
            df,
            session_date,
            opening_range_minutes,
            entry_window_minutes,
            entry_deviation_pct,
            latest_ts,
        ):
            return _no_signal(
                "already had a below-VWAP entry signal earlier this session — "
                "one entry per session rule"
            )

        deviation = (vwap - latest_close) / vwap
        if deviation < entry_deviation_pct:
            return _no_signal(
                f"deviation below VWAP {deviation:.4%} < entry threshold "
                f"{entry_deviation_pct:.4%} — not stretched enough"
            )

        shares = shares_for_notional_pct(
            equity=context.equity,
            notional_pct=parameters["per_position_notional_pct"],
            unit_price=latest_close,
        )
        if shares <= 0:
            return _no_signal(
                f"insufficient equity {context.equity:.2f} for one share at {latest_close:.2f}"
            )
        intent = TradeIntent(
            symbol=primary_symbol,
            side=OrderSide.BUY,
            quantity=float(shares),
            order_type=OrderType.MARKET,
        )
        return StrategyDecision(
            intents=[intent],
            reasoning=DecisionReasoning(
                rule="meanrev.vwap.entry",
                narrative=(
                    f"close {latest_close:.4f} stretched {deviation:.4%} below session VWAP "
                    f"{vwap:.4f} (>= {entry_deviation_pct:.4%}) — buy {primary_symbol} for "
                    f"reversion"
                ),
                triggering_values={
                    "latest_close": latest_close,
                    "session_vwap": vwap,
                    "deviation_below_vwap": deviation,
                },
                threshold={"entry_deviation_pct": entry_deviation_pct},
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    opening_range_minutes = int(required("opening_range_minutes"))
    if opening_range_minutes < 5 or opening_range_minutes > 120:
        msg = f"opening_range_minutes must be in [5, 120], got {opening_range_minutes}"
        raise ValueError(msg)

    entry_window_minutes = int(required("entry_window_minutes"))
    if entry_window_minutes < 5 or entry_window_minutes > 360:
        msg = f"entry_window_minutes must be in [5, 360], got {entry_window_minutes}"
        raise ValueError(msg)

    entry_deviation_pct = float(required("entry_deviation_pct"))
    if not 0 < entry_deviation_pct <= 0.2:
        msg = f"entry_deviation_pct must be in (0, 0.2], got {entry_deviation_pct!r}"
        raise ValueError(msg)

    stop_loss_pct = float(required("stop_loss_pct"))
    if not 0 < stop_loss_pct <= 0.5:
        msg = f"stop_loss_pct must be in (0, 0.5], got {stop_loss_pct!r}"
        raise ValueError(msg)

    exit_minutes_before_close = int(required("exit_minutes_before_close"))
    if exit_minutes_before_close < 0 or exit_minutes_before_close > 60:
        msg = f"exit_minutes_before_close must be in [0, 60], got {exit_minutes_before_close}"
        raise ValueError(msg)

    per_position_notional_pct = float(required("per_position_notional_pct"))
    if not 0 < per_position_notional_pct <= 1:
        msg = f"per_position_notional_pct must be in (0, 1], got {per_position_notional_pct!r}"
        raise ValueError(msg)

    return {
        "opening_range_minutes": opening_range_minutes,
        "entry_window_minutes": entry_window_minutes,
        "entry_deviation_pct": entry_deviation_pct,
        "stop_loss_pct": stop_loss_pct,
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
    }


def _entry_price(context: StrategyContext, symbol: str) -> float | None:
    """Return the recorded entry price for ``symbol`` from entry_state, or None.

    The backtest kernel populates ``entry_state[symbol] = {"entry_price":
    fill_price, "held_days": ...}`` on fill and pops it on exit. None-safe so
    unit tests that set ``positions`` without ``entry_state`` still exercise
    the target / time-stop exits.
    """
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


def _already_entered_this_session(
    df: pd.DataFrame,
    session_date: Any,
    opening_range_minutes: int,
    entry_window_minutes: int,
    entry_deviation_pct: float,
    latest_ts: Any,
) -> bool:
    """Return True if any PRIOR in-window bar already stretched >= entry_deviation_pct
    below its own cumulative session VWAP.

    "Prior" excludes the latest bar (the current evaluation point). Enforces
    at most one round trip per session — once price has been this far below
    VWAP, the entry opportunity is treated as used (mirrors the ORB one-entry
    rule).
    """
    series = session_vwap_series(df, session_date)
    if series.empty:
        return False
    et = pd.DatetimeIndex(series["timestamp"]).tz_convert(ET_TZ)
    offsets = [(t.hour - MARKET_OPEN_ET.hour) * 60 + (t.minute - MARKET_OPEN_ET.minute) for t in et]
    upper = opening_range_minutes + entry_window_minutes
    in_window = pd.Series(
        [opening_range_minutes <= off < upper for off in offsets], index=series.index
    )
    prior = series.loc[in_window & (series["timestamp"] < latest_ts)]
    if prior.empty:
        return False
    deviation = (prior["vwap_cum"] - prior["close"].astype(float)) / prior["vwap_cum"]
    return bool((deviation >= entry_deviation_pct).any())


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

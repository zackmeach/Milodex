"""Opening Range Breakout (ORB) intraday strategy on SPY.

Implements the ``breakout`` family's ``orb.intraday`` template:

- Single-name, long-only, intraday round-trip on 5min bars
- Opening range = first ``opening_range_minutes`` after 9:30 ET
- Entry: any bar in ``[opening_range_minutes, opening_range_minutes +
  entry_window_minutes)`` ET where ``close > range_high`` → BUY
- Fill executes at the *next* bar's open (engine T+1 fill semantics — no
  lookahead)
- Stops: ``low <= range_low`` (range re-entry) OR time-stop at session
  close minus ``exit_minutes_before_close``
- One entry per session: once a prior bar in the entry window has already
  broken above ``range_high``, the strategy refuses re-entry (the strategy
  treats the first breakout as the only entry opportunity, regardless of
  whether the engine actually filled it)
- Half-day sessions (early close at 13:00 ET): skipped entirely. No
  entries, no positions. <6 sessions/year affected.

Honest framing — expect a null result. ORB on SPY is one of the most
heavily competed-away intraday patterns in equities, and post-2022 the
0DTE options boom has materially changed SPY intraday microstructure.
This template exists to establish the validation infrastructure for
intraday signals (paired with the unconditional-intraday-long benchmark),
not because we expect ORB to find alpha.
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
    entry_window_bars,
    in_entry_window,
    is_half_day,
    is_time_stop_bar,
    opening_range_bars,
    session_date_et,
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
)


class BreakoutOrbIntradayStrategy(Strategy):
    """Opening Range Breakout intraday strategy on single-name SPY."""

    family = "breakout"
    template = "orb.intraday"
    parameter_specs = (
        StrategyParameterSpec("opening_range_minutes", expected_types=(int,)),
        StrategyParameterSpec("entry_window_minutes", expected_types=(int,)),
        StrategyParameterSpec("exit_minutes_before_close", expected_types=(int,)),
        StrategyParameterSpec("per_position_notional_pct", expected_types=(int, float)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars  # We read from context.bars_by_symbol for symmetry with other strategies.

        parameters = _validated_parameters(context)

        universe_symbols = sorted({symbol.upper() for symbol in context.universe})
        if not universe_symbols:
            return _no_signal("empty universe")
        primary_symbol = universe_symbols[0]

        barset = context.bars_by_symbol.get(primary_symbol)
        if barset is None or len(barset) == 0:
            return _no_signal(f"no bar data for {primary_symbol}")

        df = barset.to_dataframe()
        latest_ts = df["timestamp"].iloc[-1]
        latest = df.iloc[-1]
        latest_close = float(latest["close"])
        latest_low = float(latest["low"])
        session_date = session_date_et(latest_ts)

        # Half-day sessions: skip entirely. Force-close any unexpected position
        # (defensive — the strategy should not have opened one on a half-day,
        # but if it did via a prior crash/restart, exit immediately).
        if is_half_day(session_date):
            open_qty = float(context.positions.get(primary_symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="breakout.orb.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} position "
                        f"open — closing defensively"
                    ),
                    triggering_values={"session_date": session_date.isoformat()},
                    threshold={"reason": "half_day_skip"},
                )
            return _no_signal(f"half-day session {session_date}; ORB skips half-days")

        # Latest bar's offset from market open determines whether we're past the
        # opening range, in the entry window, etc.
        latest_offset = _offset_minutes_from_open(latest_ts)
        opening_range_minutes = parameters["opening_range_minutes"]

        # Still inside the opening range — no decision possible yet.
        if latest_offset < opening_range_minutes:
            return _no_signal(
                f"still in opening range (offset {latest_offset} < {opening_range_minutes})"
            )

        # Opening range complete. Compute the range from the first
        # opening_range_minutes worth of bars.
        range_bars = opening_range_bars(df, session_date, opening_range_minutes)
        if range_bars.empty:
            return _no_signal("no bars present in opening range window (data gap?)")
        range_high = float(range_bars["high"].max())
        range_low = float(range_bars["low"].min())

        open_qty = float(context.positions.get(primary_symbol, 0.0))
        if open_qty > 0:
            # Hold or exit.
            if latest_low <= range_low:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="breakout.orb.stop_loss",
                    narrative=(
                        f"latest low {latest_low:.4f} re-entered range "
                        f"(<= range_low {range_low:.4f}) → stop"
                    ),
                    triggering_values={"latest_low": latest_low, "range_low": range_low},
                    threshold={"range_low": range_low},
                )
            if is_time_stop_bar(latest_ts, parameters["exit_minutes_before_close"]):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="breakout.orb.time_stop",
                    narrative=(
                        f"time-stop bar reached ({parameters['exit_minutes_before_close']}min "
                        f"before close) → exit {primary_symbol}"
                    ),
                    triggering_values={"latest_ts": str(latest_ts)},
                    threshold={
                        "exit_minutes_before_close": parameters["exit_minutes_before_close"]
                    },
                )
            return _no_signal(
                f"holding {primary_symbol}: low {latest_low:.2f} > range_low {range_low:.2f}, "
                f"not yet at time-stop"
            )

        # No open position — check entry conditions.
        if not in_entry_window(
            latest_ts,
            opening_range_minutes,
            parameters["entry_window_minutes"],
        ):
            return _no_signal(
                f"outside entry window (offset {latest_offset}, window "
                f"[{opening_range_minutes}, "
                f"{opening_range_minutes + parameters['entry_window_minutes']}))"
            )

        # One entry per session: if a prior bar in the entry window already broke
        # above range_high, refuse re-entry even if the position is now closed.
        if _already_breakout_this_session(
            df,
            session_date,
            range_high,
            opening_range_minutes,
            parameters["entry_window_minutes"],
            latest_ts,
        ):
            return _no_signal(
                "already had a breakout earlier this session — one entry per session rule"
            )

        if latest_close <= range_high:
            return _no_signal(f"close {latest_close:.4f} did not break range_high {range_high:.4f}")

        # Breakout confirmed — emit BUY intent.
        shares = shares_for_notional_pct(
            equity=context.equity,
            notional_pct=parameters["per_position_notional_pct"],
            unit_price=latest_close,
        )
        if shares <= 0:
            return _no_signal(
                f"insufficient equity {context.equity:.2f} for one share at "
                f"{latest_close:.2f} (per_position_notional_pct="
                f"{parameters['per_position_notional_pct']:.2%})"
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
                rule="breakout.orb.entry",
                narrative=(
                    f"close {latest_close:.4f} broke above range_high "
                    f"{range_high:.4f} within entry window — buy {primary_symbol}"
                ),
                triggering_values={
                    "latest_close": latest_close,
                    "range_high": range_high,
                    "range_low": range_low,
                },
                threshold={"range_high": range_high},
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
    if entry_window_minutes < 5 or entry_window_minutes > 240:
        msg = f"entry_window_minutes must be in [5, 240], got {entry_window_minutes}"
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
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
    }


def _already_breakout_this_session(
    df: pd.DataFrame,
    session_date: Any,
    range_high: float,
    opening_range_minutes: int,
    entry_window_minutes: int,
    latest_ts: Any,
) -> bool:
    """Return True if any PRIOR bar in today's entry window already had close > range_high.

    "Prior" excludes the latest bar (which is the current evaluation point).
    The rule treats the first breakout as the only entry opportunity per
    session, regardless of whether the engine actually filled it — once
    the price has been above the range, the easy money is gone.
    """
    window = entry_window_bars(df, session_date, opening_range_minutes, entry_window_minutes)
    if window.empty:
        return False
    prior = window[window["timestamp"] < latest_ts]
    if prior.empty:
        return False
    return bool((prior["close"] > range_high).any())


def _offset_minutes_from_open(ts: Any) -> int:
    """Return minutes elapsed from 9:30 ET to ``ts``. Negative if pre-open."""
    pts = pd.Timestamp(ts)
    if pts.tz is None:
        pts = pts.tz_localize("UTC")
    et = pts.tz_convert(ET_TZ)
    t = et.time()
    return (t.hour - MARKET_OPEN_ET.hour) * 60 + (t.minute - MARKET_OPEN_ET.minute)


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

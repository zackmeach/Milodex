"""Late-session momentum intraday strategy on SPY.

Implements the ``momentum`` family's ``late_session.intraday`` template:

- Single-name, long-only, intraday round-trip on 5min bars.
- Entry window is the LATE session: [opening_range_minutes, opening_range_minutes
  + entry_window_minutes) — with defaults (300, 60) this is [14:30, 15:30) ET.
  The strategy deliberately waits for the final hour where end-of-day momentum
  tends to persist.
- Entry: flat, in entry window, latest_close > close[-(momentum_lookback_bars+1)]
  over the session's regular bars → BUY. Ride to time-stop.
- No VWAP gate, no volume gate — pure price momentum (ponytail: VWAP/volume
  are kept out deliberately; they are separate signal axes and adding them here
  would duplicate the vwap_trend candidate's logic).
- Exits (priority): stop_loss_pct from entry price → is_time_stop_bar.
- One entry per session: once a prior in-window bar had positive lookback
  momentum, the session's entry shot is consumed.
- Half-day sessions: skipped entirely.
"""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies._session_intraday import (
    entry_window_bars,
    in_entry_window,
    is_half_day,
    is_time_stop_bar,
    regular_session_bars,
    session_date_et,
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
    single_symbol,
)


class MomentumLateSessionIntradayStrategy(Strategy):
    """Late-session momentum intraday strategy on single-name SPY (long-only)."""

    family = "momentum"
    template = "late_session.intraday"
    parameter_specs = (
        StrategyParameterSpec(
            "opening_range_minutes", expected_types=(int,), minimum=5, maximum=360
        ),
        StrategyParameterSpec(
            "entry_window_minutes", expected_types=(int,), minimum=5, maximum=240
        ),
        StrategyParameterSpec(
            "momentum_lookback_bars", expected_types=(int,), minimum=1, maximum=78
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

    def max_lookback_periods(self) -> int:
        # ponytail: session-reset indicators only; one RTH session = 390min/5min = 78
        return 78

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars

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

        # Half-day sessions: skip entirely.
        if is_half_day(session_date):
            open_qty = float(context.positions.get(primary_symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="momentum.late_session.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} open "
                        f"— closing defensively"
                    ),
                    triggering_values={"session_date": session_date.isoformat()},
                    threshold={"reason": "half_day_skip"},
                )
            return _no_signal(
                f"half-day session {session_date}; late-session momentum skips half-days"
            )

        session = regular_session_bars(df, session_date)
        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # Position open: exits (stop_loss > time-stop). No signal invalidation exit.
        if open_qty > 0:
            entry_price = _entry_price(context, primary_symbol)
            stop_loss_pct = parameters["stop_loss_pct"]
            if entry_price is not None and latest_close <= entry_price * (1 - stop_loss_pct):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="momentum.late_session.stop_loss",
                    narrative=(
                        f"latest close {latest_close:.4f} breached stop "
                        f"{stop_loss_pct:.2%} below entry {entry_price:.4f} → exit"
                    ),
                    triggering_values={
                        "latest_close": latest_close,
                        "entry_price": entry_price,
                    },
                    threshold={"stop_loss_pct": stop_loss_pct},
                )
            if is_time_stop_bar(latest_ts, parameters["exit_minutes_before_close"]):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="momentum.late_session.time_stop",
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
                f"holding {primary_symbol}: close {latest_close:.2f}, not at stop or time-stop"
            )

        # Flat: evaluate entry.
        opening_range_minutes = parameters["opening_range_minutes"]
        entry_window_minutes = parameters["entry_window_minutes"]
        if not in_entry_window(latest_ts, opening_range_minutes, entry_window_minutes):
            return _no_signal(
                f"outside entry window [{opening_range_minutes}, "
                f"{opening_range_minutes + entry_window_minutes}) min after open"
            )

        lookback = parameters["momentum_lookback_bars"]
        if len(session) <= lookback:
            return _no_signal(
                f"insufficient session bars ({len(session)}) for momentum lookback {lookback}"
            )

        # One-entry re-scan: prior in-window bar with positive momentum → done.
        if _already_entered_this_session(
            df,
            session_date,
            opening_range_minutes,
            entry_window_minutes,
            lookback,
            latest_ts,
        ):
            return _no_signal(
                "already had a late-session momentum signal earlier this session "
                "— one entry per session rule"
            )

        # Momentum check: latest_close > close lookback bars ago (within session).
        prior_close = float(session["close"].astype(float).iloc[-(lookback + 1)])
        if latest_close <= prior_close:
            return _no_signal(
                f"no positive momentum: close {latest_close:.4f} <= close "
                f"{lookback} bars ago {prior_close:.4f}"
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
                rule="momentum.late_session.entry",
                narrative=(
                    f"close {latest_close:.4f} > close {lookback} bars ago "
                    f"{prior_close:.4f} — late-session positive momentum, "
                    f"buy {primary_symbol} for time-stop ride"
                ),
                triggering_values={
                    "latest_close": latest_close,
                    "prior_close": prior_close,
                    "momentum_lookback_bars": lookback,
                },
                threshold={"momentum_lookback_bars": lookback},
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    opening_range_minutes = int(required("opening_range_minutes"))
    if opening_range_minutes < 5 or opening_range_minutes > 360:
        msg = f"opening_range_minutes must be in [5, 360], got {opening_range_minutes}"
        raise ValueError(msg)

    entry_window_minutes = int(required("entry_window_minutes"))
    if entry_window_minutes < 5 or entry_window_minutes > 240:
        msg = f"entry_window_minutes must be in [5, 240], got {entry_window_minutes}"
        raise ValueError(msg)

    momentum_lookback_bars = int(required("momentum_lookback_bars"))
    if momentum_lookback_bars < 1 or momentum_lookback_bars > 78:
        msg = f"momentum_lookback_bars must be in [1, 78], got {momentum_lookback_bars}"
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
        "momentum_lookback_bars": momentum_lookback_bars,
        "stop_loss_pct": stop_loss_pct,
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
    }


def _already_entered_this_session(
    df: Any,
    session_date: Any,
    opening_range_minutes: int,
    entry_window_minutes: int,
    lookback: int,
    latest_ts: Any,
) -> bool:
    """Return True if any PRIOR in-window bar this session had positive lookback momentum.

    The persistent primary trigger is ``close > close[-(lookback+1)]`` over the
    session bars visible at that bar. We reconstruct this by scanning prior
    in-window bars and checking whether each had close > the close ``lookback``
    bars earlier in the cumulative session bars up to that point.
    """
    window = entry_window_bars(df, session_date, opening_range_minutes, entry_window_minutes)
    if window.empty:
        return False
    prior_window = window[window["timestamp"] < latest_ts]
    if prior_window.empty:
        return False

    # For each prior in-window bar, we need the full session up to that bar
    # to compute close[-(lookback+1)].
    from milodex.strategies._session_intraday import regular_session_bars as _rsb

    session = _rsb(df, session_date)
    if session.empty or len(session) <= lookback:
        return False

    for _, row in prior_window.iterrows():
        ts = row["timestamp"]
        visible = session[session["timestamp"] <= ts]
        if len(visible) <= lookback:
            continue
        c_latest = float(visible["close"].astype(float).iloc[-1])
        c_prior = float(visible["close"].astype(float).iloc[-(lookback + 1)])
        if c_latest > c_prior:
            return True
    return False


def _entry_price(context: StrategyContext, symbol: str) -> float | None:
    """Return the recorded entry price for ``symbol`` from entry_state, or None."""
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

"""Gap-continuation intraday strategy on SPY.

Implements the ``gap`` family's ``gap_continuation.intraday`` template:

- Single-name, long-only, intraday round-trip on 5min bars.
- Identifies an overnight gap-up (today's open vs. prior session's close),
  then enters only when the gap is *holding/extending* (close > today_open).
- Entry: flat, in entry window, gap_pct >= min_gap_pct AND latest_close >
  today_open → BUY. One entry per session.
- Exits (priority): stop_loss_pct from entry price → close < today_open
  (gap-fill invalidation) → time-stop exit_minutes_before_close before close.
- Half-day sessions: skipped entirely.

Cross-session reads: this strategy computes prior_close from the most recent
prior-session's last regular bar. The warmup window must therefore cover at
least two full sessions → max_lookback_periods() returns 156 (2 × 78).

Honest framing: gap-continuation on large-cap ETFs like SPY is heavily
competed intraday. This template exists as evidence infrastructure — the gap
signal is real but likely thin after 5bp slippage.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

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


class GapContinuationIntradayStrategy(Strategy):
    """Gap-continuation intraday strategy on single-name SPY (long-only)."""

    family = "gap"
    template = "gap_continuation.intraday"
    parameter_specs = (
        StrategyParameterSpec(
            "opening_range_minutes", expected_types=(int,), minimum=5, maximum=120
        ),
        StrategyParameterSpec(
            "entry_window_minutes", expected_types=(int,), minimum=5, maximum=240
        ),
        StrategyParameterSpec(
            "min_gap_pct", expected_types=(int, float), exclusive_minimum=0, maximum=0.2
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
        # ponytail: gap reads the prior session's close; 78 (one RTH session)
        # would only warm the current session → prior_close always None → never
        # enters. Two full sessions = 156.
        return 156

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
        latest = df.iloc[-1]
        latest_close = float(latest["close"])
        session_date = session_date_et(latest_ts)

        # Half-day sessions: skip entirely.
        if is_half_day(session_date):
            open_qty = float(context.positions.get(primary_symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="gap.gap_continuation.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} open "
                        f"— closing defensively"
                    ),
                    triggering_values={"session_date": session_date.isoformat()},
                    threshold={"reason": "half_day_skip"},
                )
            return _no_signal(f"half-day session {session_date}; gap-continuation skips half-days")

        # today_open = first bar of today's regular session.
        today_bars = regular_session_bars(df, session_date)
        if today_bars.empty:
            return _no_signal("no regular-session bars for today")
        today_open = float(today_bars["open"].iloc[0])

        # prior_close = last regular-session close on the most recent prior session date.
        prior_close = _prior_session_close(df, session_date)
        if prior_close is None:
            return _no_signal("no prior session data to compute gap")

        gap_pct = (today_open - prior_close) / prior_close

        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # Position open: exits (stop_loss > gap-fill invalidation > time-stop).
        if open_qty > 0:
            entry_price = _entry_price(context, primary_symbol)
            stop_loss_pct = parameters["stop_loss_pct"]
            if entry_price is not None and latest_close <= entry_price * (1 - stop_loss_pct):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="gap.gap_continuation.stop_loss",
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
            if latest_close < today_open:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="gap.gap_continuation.invalidation",
                    narrative=(
                        f"latest close {latest_close:.4f} fell below today_open "
                        f"{today_open:.4f} — gap filled, thesis invalidated, exit"
                    ),
                    triggering_values={
                        "latest_close": latest_close,
                        "today_open": today_open,
                    },
                    threshold={"today_open": today_open},
                )
            if is_time_stop_bar(latest_ts, parameters["exit_minutes_before_close"]):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="gap.gap_continuation.time_stop",
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
                f"holding {primary_symbol}: close {latest_close:.2f} > today_open "
                f"{today_open:.2f}, gap intact, not timed out"
            )

        # Flat: evaluate entry.
        opening_range_minutes = parameters["opening_range_minutes"]
        entry_window_minutes = parameters["entry_window_minutes"]
        if not in_entry_window(latest_ts, opening_range_minutes, entry_window_minutes):
            return _no_signal(
                f"outside entry window [{opening_range_minutes}, "
                f"{opening_range_minutes + entry_window_minutes}) min after open"
            )

        if gap_pct < parameters["min_gap_pct"]:
            return _no_signal(
                f"gap_pct {gap_pct:.4%} below min_gap_pct "
                f"{parameters['min_gap_pct']:.4%} — gap too small"
            )

        # One-entry re-scan: if a prior in-window bar already had close > today_open
        # (gap was qualifying), refuse re-entry.
        if _already_entered_this_session(
            df,
            session_date,
            today_open,
            opening_range_minutes,
            entry_window_minutes,
            latest_ts,
        ):
            return _no_signal(
                "already had a gap-continuation signal earlier this session "
                "— one entry per session rule"
            )

        if latest_close <= today_open:
            return _no_signal(
                f"close {latest_close:.4f} <= today_open {today_open:.4f} "
                f"— gap not holding/extending"
            )

        # Gap confirmed and holding — emit BUY.
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
                rule="gap.gap_continuation.entry",
                narrative=(
                    f"gap_pct {gap_pct:.4%} >= min {parameters['min_gap_pct']:.4%} "
                    f"and close {latest_close:.4f} > today_open {today_open:.4f} "
                    f"— gap holding, buy {primary_symbol}"
                ),
                triggering_values={
                    "gap_pct": gap_pct,
                    "today_open": today_open,
                    "prior_close": prior_close,
                    "latest_close": latest_close,
                },
                threshold={
                    "min_gap_pct": parameters["min_gap_pct"],
                    "today_open": today_open,
                },
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

    min_gap_pct = float(required("min_gap_pct"))
    if not 0 < min_gap_pct <= 0.2:
        msg = f"min_gap_pct must be in (0, 0.2], got {min_gap_pct!r}"
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
        "min_gap_pct": min_gap_pct,
        "stop_loss_pct": stop_loss_pct,
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
    }


def _prior_session_close(df: pd.DataFrame, session_date: date) -> float | None:
    """Return the last regular-session close on the most recent session before ``session_date``.

    Scans ``df`` for all distinct ET dates that precede ``session_date``, picks
    the maximum (most recent prior date), and returns its last regular-session
    bar close. Returns None when no prior session data is present.
    """
    if df.empty:
        return None
    # Collect all distinct session dates in df that predate today.
    from milodex.strategies._session_intraday import session_date_et as _sde

    dates_seen: set[date] = set()
    for ts in df["timestamp"]:
        d = _sde(ts)
        if d < session_date:
            dates_seen.add(d)
    if not dates_seen:
        return None
    prior_date = max(dates_seen)
    prior_bars = regular_session_bars(df, prior_date)
    if prior_bars.empty:
        return None
    return float(prior_bars["close"].iloc[-1])


def _already_entered_this_session(
    df: pd.DataFrame,
    session_date: date,
    today_open: float,
    opening_range_minutes: int,
    entry_window_minutes: int,
    latest_ts: Any,
) -> bool:
    """Return True if any PRIOR in-window bar this session had close > today_open.

    The persistent primary trigger for the one-entry guard is ``close > today_open``
    (gap holding) — this is the condition that qualifies a bar as an entry candidate.
    Once any prior bar qualified, the session's entry shot is used.
    """
    window = entry_window_bars(df, session_date, opening_range_minutes, entry_window_minutes)
    if window.empty:
        return False
    prior = window[window["timestamp"] < latest_ts]
    if prior.empty:
        return False
    return bool((prior["close"].astype(float) > today_open).any())


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

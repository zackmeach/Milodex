"""Opening-range retest intraday strategy on SPY.

Implements the ``breakout`` family's ``opening_range_retest.intraday`` template:

- Single-name, long-only, intraday round-trip on 5min bars.
- Distinct from ORB (first cross above range_high): this strategy waits for the
  *retest* — the 3-phase sequence:
    (a) a prior in-window bar breaks out above range_high (close > range_high),
    (b) a subsequent bar dips back to or below range_high (retest),
    (c) the latest bar reclaims: close > range_high AND
        low >= range_high * (1 - retest_band_pct) (didn't collapse through).
- Entry: all three phases confirmed on the latest bar → BUY.
- Exits (priority): latest_low <= range_low (structural stop — range fully
  re-entered) → stop_loss_pct from entry price → time-stop
  exit_minutes_before_close before close.
- One entry per session: once a prior in-window bar completed the full
  breakout-then-reclaim sequence, the session's entry shot is consumed.
- Half-day sessions: skipped entirely.

Honest framing: a retest entry is slower than a first-cross ORB entry, which
means catching the second leg of a move. The signal is real in theory but
thin after slippage on a large liquid ETF like SPY.
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
    opening_range_bars,
    session_date_et,
    to_eastern,
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
    single_symbol,
)


class BreakoutOpeningRangeRetestIntradayStrategy(Strategy):
    """Opening-range retest intraday strategy on single-name SPY (long-only)."""

    family = "breakout"
    template = "opening_range_retest.intraday"
    parameter_specs = (
        StrategyParameterSpec(
            "opening_range_minutes", expected_types=(int,), minimum=5, maximum=120
        ),
        StrategyParameterSpec(
            "entry_window_minutes", expected_types=(int,), minimum=5, maximum=240
        ),
        StrategyParameterSpec(
            "retest_band_pct", expected_types=(int, float), exclusive_minimum=0, maximum=0.05
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
        latest = df.iloc[-1]
        latest_close = float(latest["close"])
        latest_low = float(latest["low"])
        session_date = session_date_et(latest_ts)

        # Half-day sessions: skip entirely.
        if is_half_day(session_date):
            open_qty = float(context.positions.get(primary_symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="breakout.orb_retest.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} open "
                        f"— closing defensively"
                    ),
                    triggering_values={"session_date": session_date.isoformat()},
                    threshold={"reason": "half_day_skip"},
                )
            return _no_signal(f"half-day session {session_date}; orb-retest skips half-days")

        opening_range_minutes = parameters["opening_range_minutes"]
        range_bars = opening_range_bars(df, session_date, opening_range_minutes)
        # Require ALL expected on-grid bars; a partial range understates range_high
        # and manufactures false breakouts on data gaps.
        # ponytail: bar size is 5Min for all intraday ETF strategies in this family.
        bar_minutes = 5
        expected_offsets = set(range(0, opening_range_minutes, bar_minutes))
        actual_offsets = {
            (eastern.hour * 60 + eastern.minute) - (9 * 60 + 30)
            for eastern in (to_eastern(ts) for ts in range_bars["timestamp"])
        }
        if actual_offsets != expected_offsets:
            missing_offsets = sorted(expected_offsets - actual_offsets)
            unexpected_offsets = sorted(actual_offsets - expected_offsets)
            return _no_signal(
                "incomplete opening range: exact 5-minute grid mismatch "
                f"(missing offsets={missing_offsets}, unexpected offsets={unexpected_offsets})"
            )
        range_high = float(range_bars["high"].max())
        range_low = float(range_bars["low"].min())

        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # Position open: exits (structural stop > stop_loss > time-stop).
        if open_qty > 0:
            if latest_low <= range_low:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="breakout.orb_retest.structural_stop",
                    narrative=(
                        f"latest low {latest_low:.4f} re-entered full range "
                        f"(<= range_low {range_low:.4f}) → structural stop"
                    ),
                    triggering_values={"latest_low": latest_low, "range_low": range_low},
                    threshold={"range_low": range_low},
                )
            entry_price = _entry_price(context, primary_symbol)
            stop_loss_pct = parameters["stop_loss_pct"]
            if entry_price is not None and latest_close <= entry_price * (1 - stop_loss_pct):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="breakout.orb_retest.stop_loss",
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
                    rule="breakout.orb_retest.time_stop",
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
                f"not at stop or time-stop"
            )

        # Flat: evaluate entry.
        entry_window_minutes = parameters["entry_window_minutes"]
        if not in_entry_window(latest_ts, opening_range_minutes, entry_window_minutes):
            return _no_signal(
                f"outside entry window [{opening_range_minutes}, "
                f"{opening_range_minutes + entry_window_minutes}) min after open"
            )

        # One-entry re-scan: if a prior in-window bar already completed the full
        # breakout-then-reclaim sequence, the session's entry shot is consumed.
        if _already_entered_this_session(
            df,
            session_date,
            range_high,
            opening_range_minutes,
            entry_window_minutes,
            parameters["retest_band_pct"],
            latest_ts,
        ):
            return _no_signal(
                "already had a retest-reclaim signal earlier this session "
                "— one entry per session rule"
            )

        # Phase (a): a prior in-window bar broke above range_high.
        prior_window = _prior_window_bars(
            df, session_date, opening_range_minutes, entry_window_minutes, latest_ts
        )
        breakout_bars = prior_window[prior_window["close"].astype(float) > range_high]
        if breakout_bars.empty:
            return _no_signal(
                f"no prior breakout above range_high {range_high:.4f} in entry window"
            )

        # Phase (b): after the first breakout bar, a bar dipped to <= range_high.
        first_breakout_ts = breakout_bars["timestamp"].iloc[0]
        after_breakout = prior_window[prior_window["timestamp"] > first_breakout_ts]
        retest_bars = after_breakout[after_breakout["close"].astype(float) <= range_high]
        if retest_bars.empty:
            return _no_signal(
                f"breakout happened but no retest (<= range_high {range_high:.4f}) "
                f"detected in prior window"
            )

        # Phase (c): latest bar reclaims — close > range_high and didn't collapse.
        retest_band_pct = parameters["retest_band_pct"]
        retest_floor = range_high * (1 - retest_band_pct)
        if latest_close <= range_high:
            return _no_signal(
                f"retest phase detected but latest close {latest_close:.4f} has not "
                f"reclaimed range_high {range_high:.4f}"
            )
        if latest_low < retest_floor:
            return _no_signal(
                f"latest low {latest_low:.4f} collapsed below retest floor "
                f"{retest_floor:.4f} ({retest_band_pct:.3%} below range_high) "
                f"— retest too deep"
            )

        # All three phases confirmed — emit BUY.
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
                rule="breakout.orb_retest.entry",
                narrative=(
                    f"ORB retest confirmed: broke {range_high:.4f}, retested, reclaimed "
                    f"(close {latest_close:.4f} > range_high, low {latest_low:.4f} "
                    f">= floor {retest_floor:.4f}) — buy {primary_symbol}"
                ),
                triggering_values={
                    "latest_close": latest_close,
                    "latest_low": latest_low,
                    "range_high": range_high,
                    "range_low": range_low,
                    "retest_floor": retest_floor,
                },
                threshold={
                    "range_high": range_high,
                    "retest_band_pct": retest_band_pct,
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

    retest_band_pct = float(required("retest_band_pct"))
    if not 0 < retest_band_pct <= 0.05:
        msg = f"retest_band_pct must be in (0, 0.05], got {retest_band_pct!r}"
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
        "retest_band_pct": retest_band_pct,
        "stop_loss_pct": stop_loss_pct,
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
    }


def _prior_window_bars(
    df: pd.DataFrame,
    session_date: date,
    opening_range_minutes: int,
    entry_window_minutes: int,
    latest_ts: Any,
) -> pd.DataFrame:
    """Return in-window bars strictly before latest_ts."""
    window = entry_window_bars(df, session_date, opening_range_minutes, entry_window_minutes)
    if window.empty:
        return window
    return window[window["timestamp"] < latest_ts].reset_index(drop=True)


def _already_entered_this_session(
    df: pd.DataFrame,
    session_date: date,
    range_high: float,
    opening_range_minutes: int,
    entry_window_minutes: int,
    retest_band_pct: float,
    latest_ts: Any,
) -> bool:
    """Return True if any PRIOR in-window bar already completed the 3-phase sequence.

    The persistent primary trigger (one-entry guard key) is: a bar where all of
    (a) a preceding bar broke above range_high, (b) a subsequent bar retested
    (<= range_high), and (c) that bar itself reclaimed (close > range_high and
    low >= range_high*(1-retest_band_pct)).  Once any such bar exists as a prior,
    the session's entry shot is used.
    """
    prior = _prior_window_bars(
        df, session_date, opening_range_minutes, entry_window_minutes, latest_ts
    )
    if prior.empty:
        return False

    # Walk through prior bars in order; once we see a bar complete all 3 phases → True.
    retest_floor = range_high * (1 - retest_band_pct)
    breakout_seen = False
    retest_seen = False

    for _, row in prior.iterrows():
        c = float(row["close"])
        low = float(row["low"])
        if not breakout_seen:
            if c > range_high:
                breakout_seen = True
        elif not retest_seen:
            if c <= range_high:
                retest_seen = True
            # if still > range_high keep waiting for retest
        else:
            # breakout and retest both seen — check reclaim
            if c > range_high and low >= retest_floor:
                return True
            # if this bar doesn't reclaim, keep scanning (could reclaim on a later prior bar)

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

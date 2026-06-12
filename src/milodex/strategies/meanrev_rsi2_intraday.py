"""RSI(2) intraday mean-reversion strategy on SPY.

Implements the ``meanrev`` family's ``rsi2.intraday`` template — the intraday
sibling of the daily ``meanrev.daily.pullback_rsi2`` strategy, adapted to 5min
SPY bars with a forced same-day exit:

- Single-name, long-only, intraday round-trip on 5min bars (SPY).
- A short-period Wilder RSI is computed over the *current session's* regular
  cash-session closes (it resets every session — this is an intraday oscillator,
  not a multi-day one).
- Entry: any bar in the entry window where session RSI <= ``rsi_entry_threshold``
  (oversold) → BUY, betting on a snap-back.
- Fill executes at the *next* bar's open (engine T+1 fill semantics — no
  lookahead).
- Exits (priority order): (1) ``stop_loss_pct`` from the entry price,
  (2) session RSI >= ``rsi_exit_threshold`` (reverted), (3) time-stop
  ``exit_minutes_before_close`` before the close.
- One entry per session: once a prior in-window bar already printed an oversold
  RSI, the strategy refuses re-entry — at most one round trip per day.
- Half-day sessions (early 13:00 ET close): skipped entirely.

Deliberately has NO multi-day trend filter (the daily sibling uses a 200-SMA
filter; here the oscillator is intraday and self-contained). Downside is capped
by ``stop_loss_pct`` and the forced time-stop. Long-only because the engine's
intraday path models long round-trips. Honest benchmark:
``benchmark.unconditional_intraday_long.spy.v1``.
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
    regular_session_bars,
    session_date_et,
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
    relation_less_than,
)


class MeanrevRsi2IntradayStrategy(Strategy):
    """RSI(2) intraday mean-reversion strategy on single-name SPY (long-only)."""

    family = "meanrev"
    template = "rsi2.intraday"
    parameter_specs = (
        StrategyParameterSpec(
            "opening_range_minutes", expected_types=(int,), minimum=5, maximum=120
        ),
        StrategyParameterSpec(
            "entry_window_minutes", expected_types=(int,), minimum=5, maximum=360
        ),
        StrategyParameterSpec("rsi_lookback", expected_types=(int,), minimum=2, maximum=50),
        StrategyParameterSpec(
            "rsi_entry_threshold", expected_types=(int, float), exclusive_minimum=0
        ),
        StrategyParameterSpec("rsi_exit_threshold", expected_types=(int, float), maximum=100),
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
    parameter_relations = (relation_less_than("rsi_entry_threshold", "rsi_exit_threshold"),)

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars

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
        latest_close = float(df["close"].iloc[-1])
        session_date = session_date_et(latest_ts)

        if is_half_day(session_date):
            open_qty = float(context.positions.get(primary_symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.rsi2.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} open — "
                        f"closing defensively"
                    ),
                    triggering_values={"session_date": session_date.isoformat()},
                    threshold={"reason": "half_day_skip"},
                )
            return _no_signal(f"half-day session {session_date}; RSI2-intraday skips half-days")

        session = regular_session_bars(df, session_date)
        lookback = parameters["rsi_lookback"]
        rsi_series = _wilder_rsi_series(session["close"].astype(float), lookback)
        current_rsi = (
            None if rsi_series.empty or pd.isna(rsi_series.iloc[-1]) else float(rsi_series.iloc[-1])
        )
        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # --- Position open: exits (stop_loss > rsi_exit > time-stop). ---
        if open_qty > 0:
            entry_price = _entry_price(context, primary_symbol)
            stop_loss_pct = parameters["stop_loss_pct"]
            if entry_price is not None and latest_close <= entry_price * (1 - stop_loss_pct):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.rsi2.stop_loss",
                    narrative=(
                        f"latest close {latest_close:.4f} breached stop "
                        f"{stop_loss_pct:.2%} below entry {entry_price:.4f} → exit"
                    ),
                    triggering_values={"latest_close": latest_close, "entry_price": entry_price},
                    threshold={"stop_loss_pct": stop_loss_pct},
                )
            if current_rsi is not None and current_rsi >= parameters["rsi_exit_threshold"]:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.rsi2.rsi_exit",
                    narrative=(
                        f"session RSI {current_rsi:.2f} reverted above exit threshold "
                        f"{parameters['rsi_exit_threshold']} → take profit"
                    ),
                    triggering_values={"session_rsi": current_rsi},
                    threshold={"rsi_exit_threshold": parameters["rsi_exit_threshold"]},
                )
            if is_time_stop_bar(latest_ts, parameters["exit_minutes_before_close"]):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="meanrev.rsi2.time_stop",
                    narrative=(
                        f"time-stop bar reached ({parameters['exit_minutes_before_close']}min "
                        f"before close) → exit {primary_symbol}"
                    ),
                    triggering_values={"latest_ts": str(latest_ts)},
                    threshold={
                        "exit_minutes_before_close": parameters["exit_minutes_before_close"]
                    },
                )
            rsi_display = f"{current_rsi:.2f}" if current_rsi is not None else "n/a"
            return _no_signal(
                f"holding {primary_symbol}: RSI {rsi_display}, not stopped/reverted/timed out"
            )

        # --- Flat: evaluate entry. ---
        opening_range_minutes = parameters["opening_range_minutes"]
        entry_window_minutes = parameters["entry_window_minutes"]
        if not in_entry_window(latest_ts, opening_range_minutes, entry_window_minutes):
            return _no_signal(
                f"outside entry window [{opening_range_minutes}, "
                f"{opening_range_minutes + entry_window_minutes}) min after open"
            )

        if current_rsi is None:
            return _no_signal(f"session RSI undefined (fewer than {lookback + 1} session closes)")

        rsi_entry_threshold = parameters["rsi_entry_threshold"]
        if _already_entered_this_session(
            session,
            rsi_series,
            opening_range_minutes,
            entry_window_minutes,
            rsi_entry_threshold,
            latest_ts,
        ):
            return _no_signal(
                "already had an oversold RSI entry signal earlier this session — "
                "one entry per session rule"
            )

        if current_rsi > rsi_entry_threshold:
            return _no_signal(
                f"session RSI {current_rsi:.2f} not at/below entry threshold "
                f"{rsi_entry_threshold} — not oversold"
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
                rule="meanrev.rsi2.entry",
                narrative=(
                    f"session RSI {current_rsi:.2f} at/below entry threshold "
                    f"{rsi_entry_threshold} — buy {primary_symbol} for mean reversion"
                ),
                triggering_values={"session_rsi": current_rsi, "latest_close": latest_close},
                threshold={"rsi_entry_threshold": rsi_entry_threshold},
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
        "rsi_lookback": rsi_lookback,
        "rsi_entry_threshold": rsi_entry_threshold,
        "rsi_exit_threshold": rsi_exit_threshold,
        "stop_loss_pct": stop_loss_pct,
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
    }


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


def _wilder_rsi_series(closes: pd.Series, lookback: int) -> pd.Series:
    """Return a Wilder-smoothed RSI series aligned to ``closes``.

    Identical recursive smoothing to ``meanrev_rsi2_pullback._wilder_rsi``
    (seed = simple mean of the first ``lookback`` gains/losses, then Wilder
    recursion), vectorised into a single O(n) pass so the per-bar guard does
    not re-run an O(n) computation for every prior bar. Entries before enough
    history exist are ``NaN``.
    """
    n = len(closes)
    result = pd.Series([float("nan")] * n, index=closes.index, dtype=float)
    if n <= lookback:
        return result

    deltas = closes.diff()
    gains = deltas.clip(lower=0.0).to_numpy()
    losses = (-deltas).clip(lower=0.0).to_numpy()

    # Seed with the simple average of the first ``lookback`` deltas (indices
    # 1..lookback; index 0's delta is NaN and excluded).
    avg_gain = float(gains[1 : lookback + 1].mean())
    avg_loss = float(losses[1 : lookback + 1].mean())
    result.iloc[lookback] = _rsi_from(avg_gain, avg_loss)
    for i in range(lookback + 1, n):
        avg_gain = (avg_gain * (lookback - 1) + float(gains[i])) / lookback
        avg_loss = (avg_loss * (lookback - 1) + float(losses[i])) / lookback
        result.iloc[i] = _rsi_from(avg_gain, avg_loss)
    return result


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 50.0 if avg_gain == 0.0 else 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _already_entered_this_session(
    session: pd.DataFrame,
    rsi_series: pd.Series,
    opening_range_minutes: int,
    entry_window_minutes: int,
    rsi_entry_threshold: float,
    latest_ts: Any,
) -> bool:
    """Return True if any PRIOR in-window bar already printed RSI <= entry threshold."""
    if session.empty:
        return False
    et = pd.DatetimeIndex(session["timestamp"]).tz_convert(ET_TZ)
    offsets = [(t.hour - MARKET_OPEN_ET.hour) * 60 + (t.minute - MARKET_OPEN_ET.minute) for t in et]
    upper = opening_range_minutes + entry_window_minutes
    in_window = pd.Series(
        [opening_range_minutes <= off < upper for off in offsets], index=session.index
    )
    prior_mask = in_window & (session["timestamp"] < latest_ts)
    prior_rsi = rsi_series[prior_mask.to_numpy()]
    return bool((prior_rsi <= rsi_entry_threshold).any())


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

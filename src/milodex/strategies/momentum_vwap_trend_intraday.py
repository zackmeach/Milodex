"""VWAP trend-continuation intraday strategy on SPY.

Implements the ``momentum`` family's ``vwap_trend.intraday`` template:

- Single-name, long-only, intraday round-trip on 5min bars (SPY).
- Session VWAP is the cumulative volume-weighted average price from the 9:30
  ET open through the latest completed bar (``session_vwap`` helper).
- Entry: any bar in the entry window where ALL of the following hold → BUY:
  (1) price is at least ``min_above_vwap_pct`` *above* session VWAP (uptrend),
  (2) momentum is positive — the close is above the close ``momentum_lookback_bars``
  bars ago, and (3) volume confirmation — the latest bar's volume exceeds
  ``volume_factor`` × the mean volume of the prior ``momentum_lookback_bars``.
- Fill executes at the *next* bar's open (engine T+1 fill semantics — no
  lookahead).
- Exits (priority order): (1) ``stop_loss_pct`` from the entry price,
  (2) trend invalidation — the close falls back below session VWAP,
  (3) time-stop ``exit_minutes_before_close`` before the close.
- One entry per session: once a prior in-window bar already traded above VWAP
  by the threshold, the strategy refuses re-entry — at most one round trip
  per day.
- Half-day sessions (early 13:00 ET close): skipped entirely.

This is the long-only mirror of the VWAP mean-reversion candidate: it tries to
*ride* above-VWAP strength rather than fade below-VWAP weakness. The honest
benchmark is ``benchmark.unconditional_intraday_long.spy.v1`` — a long-only
trend filter only adds value if it beats unconditional intraday long after
slippage. Long-only because the engine's intraday path models long
round-trips; it cannot trade downtrends short.
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
    session_vwap_series,
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
)


class MomentumVwapTrendIntradayStrategy(Strategy):
    """VWAP trend-continuation intraday strategy on single-name SPY (long-only)."""

    family = "momentum"
    template = "vwap_trend.intraday"
    parameter_specs = (
        StrategyParameterSpec(
            "opening_range_minutes", expected_types=(int,), minimum=5, maximum=120
        ),
        StrategyParameterSpec(
            "entry_window_minutes", expected_types=(int,), minimum=5, maximum=360
        ),
        StrategyParameterSpec(
            "min_above_vwap_pct", expected_types=(int, float), exclusive_minimum=0, maximum=0.2
        ),
        StrategyParameterSpec(
            "momentum_lookback_bars", expected_types=(int,), minimum=1, maximum=78
        ),
        StrategyParameterSpec("volume_factor", expected_types=(int, float), exclusive_minimum=0),
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
                    rule="momentum.vwap_trend.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} open — "
                        f"closing defensively"
                    ),
                    triggering_values={"session_date": session_date.isoformat()},
                    threshold={"reason": "half_day_skip"},
                )
            return _no_signal(f"half-day session {session_date}; VWAP-trend skips half-days")

        series = session_vwap_series(df, session_date)
        vwap = (
            None
            if series.empty or pd.isna(series["vwap_cum"].iloc[-1])
            else float(series["vwap_cum"].iloc[-1])
        )
        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # --- Position open: exits (stop_loss > invalidation > time-stop). ---
        if open_qty > 0:
            entry_price = _entry_price(context, primary_symbol)
            stop_loss_pct = parameters["stop_loss_pct"]
            if entry_price is not None and latest_close <= entry_price * (1 - stop_loss_pct):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="momentum.vwap_trend.stop_loss",
                    narrative=(
                        f"latest close {latest_close:.4f} breached stop "
                        f"{stop_loss_pct:.2%} below entry {entry_price:.4f} → exit"
                    ),
                    triggering_values={"latest_close": latest_close, "entry_price": entry_price},
                    threshold={"stop_loss_pct": stop_loss_pct},
                )
            if vwap is not None and latest_close < vwap:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="momentum.vwap_trend.invalidation",
                    narrative=(
                        f"latest close {latest_close:.4f} fell back below session VWAP "
                        f"{vwap:.4f} → trend invalidated, exit"
                    ),
                    triggering_values={"latest_close": latest_close, "session_vwap": vwap},
                    threshold={"session_vwap": vwap},
                )
            if is_time_stop_bar(latest_ts, parameters["exit_minutes_before_close"]):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="momentum.vwap_trend.time_stop",
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
                f"{vwap_display}, trend intact, not timed out"
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

        min_above_vwap_pct = parameters["min_above_vwap_pct"]
        if _already_entered_this_session(
            series,
            opening_range_minutes,
            entry_window_minutes,
            min_above_vwap_pct,
            latest_ts,
        ):
            return _no_signal(
                "already had an above-VWAP entry signal earlier this session — "
                "one entry per session rule"
            )

        lookback = parameters["momentum_lookback_bars"]
        if len(series) <= lookback:
            return _no_signal(
                f"insufficient session bars ({len(series)}) for momentum lookback {lookback}"
            )

        above_pct = (latest_close - vwap) / vwap
        if above_pct < min_above_vwap_pct:
            return _no_signal(
                f"close {above_pct:.4%} above VWAP < min {min_above_vwap_pct:.4%} — not extended"
            )

        prior_close = float(series["close"].astype(float).iloc[-(lookback + 1)])
        if latest_close <= prior_close:
            return _no_signal(
                f"no positive momentum: close {latest_close:.4f} <= close {lookback} bars ago "
                f"{prior_close:.4f}"
            )

        volumes = series["volume"].astype(float)
        latest_volume = float(volumes.iloc[-1])
        prior_volume_mean = float(volumes.iloc[-(lookback + 1) : -1].mean())
        volume_factor = parameters["volume_factor"]
        if prior_volume_mean <= 0 or latest_volume <= volume_factor * prior_volume_mean:
            return _no_signal(
                f"no volume confirmation: latest vol {latest_volume:.0f} <= "
                f"{volume_factor}x prior mean {prior_volume_mean:.0f}"
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
        volume_ratio = latest_volume / prior_volume_mean
        return StrategyDecision(
            intents=[intent],
            reasoning=DecisionReasoning(
                rule="momentum.vwap_trend.entry",
                narrative=(
                    f"close {latest_close:.4f} is {above_pct:.4%} above session VWAP {vwap:.4f} "
                    f"with positive {lookback}-bar momentum and {volume_ratio:.2f}x volume — "
                    f"buy {primary_symbol} for continuation"
                ),
                triggering_values={
                    "latest_close": latest_close,
                    "session_vwap": vwap,
                    "above_vwap_pct": above_pct,
                    "prior_close": prior_close,
                    "latest_volume": latest_volume,
                    "prior_volume_mean": prior_volume_mean,
                },
                threshold={
                    "min_above_vwap_pct": min_above_vwap_pct,
                    "momentum_lookback_bars": lookback,
                    "volume_factor": volume_factor,
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
    if entry_window_minutes < 5 or entry_window_minutes > 360:
        msg = f"entry_window_minutes must be in [5, 360], got {entry_window_minutes}"
        raise ValueError(msg)

    min_above_vwap_pct = float(required("min_above_vwap_pct"))
    if not 0 < min_above_vwap_pct <= 0.2:
        msg = f"min_above_vwap_pct must be in (0, 0.2], got {min_above_vwap_pct!r}"
        raise ValueError(msg)

    momentum_lookback_bars = int(required("momentum_lookback_bars"))
    if momentum_lookback_bars < 1 or momentum_lookback_bars > 78:
        msg = f"momentum_lookback_bars must be in [1, 78], got {momentum_lookback_bars}"
        raise ValueError(msg)

    volume_factor = float(required("volume_factor"))
    if volume_factor <= 0:
        msg = f"volume_factor must be > 0, got {volume_factor!r}"
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
        "min_above_vwap_pct": min_above_vwap_pct,
        "momentum_lookback_bars": momentum_lookback_bars,
        "volume_factor": volume_factor,
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


def _already_entered_this_session(
    series: pd.DataFrame,
    opening_range_minutes: int,
    entry_window_minutes: int,
    min_above_vwap_pct: float,
    latest_ts: Any,
) -> bool:
    """Return True if any PRIOR in-window bar already traded >= min_above_vwap_pct
    above its own cumulative session VWAP.

    Uses only the above-VWAP condition (the persistent primary trigger) for the
    one-round-trip-per-session guard — the momentum / volume confirmations are
    transient and intentionally not part of the "already used my shot" test.
    """
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
    above = (prior["close"].astype(float) - prior["vwap_cum"]) / prior["vwap_cum"]
    return bool((above >= min_above_vwap_pct).any())


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

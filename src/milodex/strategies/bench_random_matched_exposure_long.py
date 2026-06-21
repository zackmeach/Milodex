"""Random matched-exposure intraday long baseline (E-PR2).

Implements the ``benchmark`` family's ``random_matched_exposure.intraday``
template — the *null* against which the RSI(2) intraday candidate is judged.
It replaces *signal* with *chance*, holding to the session close:

- Same entry window and ``per_position_notional_pct`` as the candidate; skips
  half-days. **No stop_loss. No signal exit.** Exit is the session time-stop
  only ("random entry, held to close").
- Per session, a deterministic RNG seeded from ``(symbol, session_date, seed)``
  decides:
  1. ``enter_this_session = rng.random() < session_entry_rate`` — matches the
     candidate's round-trip COUNT in expectation (the per-symbol rate is
     measured and injected into config; see ``research/candidate_rates.py``).
  2. if entering, ``target_offset_min`` = a uniformly random integer minute in
     ``[opening_range_minutes, opening_range_minutes + entry_window_minutes)``.
- **Streaming entry:** emit BUY at the *first PRESENT in-window bar* whose
  offset-from-open is ``>= target_offset_min``, while flat AND not
  already-entered-this-session. The engine's visible barset is cursor-truncated
  (only bars up to "now"), so an exact ``==`` match would silently never fire on
  a missing bar — under-trading thin symbols. First-present-bar-``>=``-target is
  robust to gaps; the residual edge (target past the last present in-window bar
  → no fire) is a minor, coverage-correlated under-fire.
- **One entry per session:** a ``positions`` flat-check alone is insufficient —
  the T+1 fill lands one bar later, so ``positions`` is empty across bars and a
  positions-only check would re-emit. The prior-bar re-scan
  (:func:`_already_entered_this_session`) refuses a new BUY if any prior visible
  in-window bar this session already reached offset ``>= target``.

Determinism is per-``(symbol, session_date, seed)`` — the RNG is derived inside
:meth:`evaluate` (never a process-global ``np.random.seed``), and ``enter`` is
drawn before ``target`` in fixed order every call so the choice is stable across
the session's growing barsets.

Long-only. The matched-exposure framing matches on trade-COUNT and the
entry-WINDOW, deliberately NOT on hold-duration (the candidate exits on
stop/RSI-revert/time-stop; this baseline always holds to the time-stop).
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import numpy as np
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
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
    single_symbol,
)

# ponytail: all intraday ETF strategies are 5Min; promote to a config param if a
# non-5min variant appears.
_BAR_MINUTES = 5


class BenchRandomMatchedExposureLongStrategy(Strategy):
    """Random-entry, held-to-close intraday long baseline (single-name, long-only)."""

    family = "benchmark"
    template = "random_matched_exposure.intraday"
    parameter_specs = (
        StrategyParameterSpec(
            "opening_range_minutes", expected_types=(int,), minimum=5, maximum=120
        ),
        StrategyParameterSpec(
            "entry_window_minutes", expected_types=(int,), minimum=5, maximum=360
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
        StrategyParameterSpec(
            "session_entry_rate", expected_types=(int, float), minimum=0, maximum=1
        ),
        StrategyParameterSpec("seed", expected_types=(int,)),
    )

    def max_lookback_periods(self) -> int:
        # The null is session-reset: _already_entered_this_session scans only the
        # current session, so cross-session warmup is unnecessary. Declaring the
        # one-session bar bound (390min RTH / 5min = 78) keeps the warmup heuristic
        # from treating the large `seed` parameter as a lookback period.
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

        opening_range_minutes = parameters["opening_range_minutes"]
        entry_window_minutes = parameters["entry_window_minutes"]
        exit_minutes_before_close = parameters["exit_minutes_before_close"]

        if is_half_day(session_date):
            # Defensive: close any unexpected position on a half-day (matches siblings).
            open_qty = float(context.positions.get(primary_symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="benchmark.random_matched.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} open — "
                        f"closing defensively"
                    ),
                )
            return _no_signal(
                f"half-day session {session_date}; random-matched baseline skips half-days"
            )

        open_qty = float(context.positions.get(primary_symbol, 0.0))

        # --- Position open: time-stop is the ONLY exit. No stop, no signal exit. ---
        if open_qty > 0:
            if is_time_stop_bar(latest_ts, exit_minutes_before_close):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="benchmark.random_matched.exit",
                    narrative=(
                        f"time-stop bar reached ({exit_minutes_before_close}min before "
                        f"close) → close {primary_symbol}"
                    ),
                )
            return _no_signal(f"holding {primary_symbol} until time-stop")

        # --- Flat: per-session random entry. ---
        seed = parameters["seed"]
        seed_basis = _seed_basis(primary_symbol, session_date, seed)
        rng = _session_rng(seed_basis)
        session_entry_rate = parameters["session_entry_rate"]
        # Draw order is FIXED — ``enter`` then ``target`` — so the choice is stable
        # across the session's growing barsets.
        entered_session = bool(rng.random() < session_entry_rate)
        target_offset_min = int(
            rng.integers(
                opening_range_minutes,
                opening_range_minutes + entry_window_minutes - _BAR_MINUTES + 1,
            )
        )

        def reasoning(entry_offset_min: int | None) -> dict[str, Any]:
            return {
                "seed_basis": seed_basis,
                "session_entry_rate": session_entry_rate,
                "entered_session": entered_session,
                "target_offset_min": target_offset_min,
                "entry_offset_min": entry_offset_min,
            }

        if not entered_session:
            return _no_signal(
                f"random draw: no entry this session (rate {session_entry_rate})",
                extras=reasoning(None),
            )

        if not in_entry_window(latest_ts, opening_range_minutes, entry_window_minutes):
            return _no_signal(
                f"outside entry window [{opening_range_minutes}, "
                f"{opening_range_minutes + entry_window_minutes}) min after open",
                extras=reasoning(None),
            )

        latest_offset = _et_offset_minutes(latest_ts)
        if latest_offset < target_offset_min:
            return _no_signal(
                f"in window but offset {latest_offset} < target {target_offset_min} — waiting",
                extras=reasoning(None),
            )

        # First-present-bar-at-or-after-target: refuse if a PRIOR visible in-window
        # bar this session already reached offset >= target (the T+1 fill gap leaves
        # ``positions`` flat, so this re-scan — not a positions check — is what
        # enforces one entry per session).
        if _already_entered_this_session(
            df,
            session_date,
            opening_range_minutes,
            entry_window_minutes,
            target_offset_min,
            latest_ts,
        ):
            return _no_signal(
                "a prior in-window bar this session already reached the target offset — "
                "one entry per session rule",
                extras=reasoning(None),
            )

        shares = shares_for_notional_pct(
            equity=context.equity,
            notional_pct=parameters["per_position_notional_pct"],
            unit_price=latest_close,
        )
        if shares <= 0:
            return _no_signal(
                f"insufficient equity {context.equity:.2f} for one share at {latest_close:.2f}",
                extras=reasoning(None),
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
                rule="benchmark.random_matched.entry",
                narrative=(
                    f"random matched-exposure entry: buy {primary_symbol} at first in-window "
                    f"bar (offset {latest_offset}) >= target {target_offset_min}"
                ),
                triggering_values={"latest_close": latest_close, "latest_offset": latest_offset},
                threshold={"target_offset_min": target_offset_min},
                extras=reasoning(latest_offset),
            ),
        )


def _seed_basis(symbol: str, session_date: date, seed: int) -> str:
    return f"{symbol}:{session_date.isoformat()}:{seed}"


def _session_rng(seed_basis: str) -> np.random.Generator:
    """Deterministic per-(symbol, session_date, seed) RNG.

    Derived inside ``evaluate`` from a stable string basis — never a
    process-global ``np.random.seed`` (windows run sequentially with no reseed,
    so a global seed would correlate every window).
    """
    digest = hashlib.sha256(seed_basis.encode()).digest()[:8]
    return np.random.default_rng(int.from_bytes(digest, "big"))


def _et_offset_minutes(ts: Any) -> int:
    """Minutes from 9:30 ET to ``ts`` (negative if pre-open)."""
    et = pd.Timestamp(ts)
    if et.tz is None:
        et = et.tz_localize("UTC")
    et = et.tz_convert(ET_TZ)
    return (et.hour - MARKET_OPEN_ET.hour) * 60 + (et.minute - MARKET_OPEN_ET.minute)


def _already_entered_this_session(
    df: pd.DataFrame,
    session_date: date,
    opening_range_minutes: int,
    entry_window_minutes: int,
    target_offset_min: int,
    latest_ts: Any,
) -> bool:
    """Return True if any PRIOR in-window bar already reached offset >= target.

    Mirrors RSI2's ``_already_entered_this_session`` but keyed on the random
    target offset rather than an RSI threshold. Restricts to the current session
    and to bars strictly before ``latest_ts``.
    """
    if df.empty:
        return False
    et = pd.DatetimeIndex(df["timestamp"]).tz_convert(ET_TZ)
    same_session = pd.Series(et.date == session_date, index=df.index)
    offsets = [_et_offset_minutes(t) for t in et]
    upper = opening_range_minutes + entry_window_minutes
    in_window = pd.Series([opening_range_minutes <= off < upper for off in offsets], index=df.index)
    at_or_after_target = pd.Series([off >= target_offset_min for off in offsets], index=df.index)
    prior = pd.Series(df["timestamp"].to_numpy() < latest_ts, index=df.index)
    mask = same_session & in_window & at_or_after_target & prior
    return bool(mask.any())


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

    exit_minutes_before_close = int(required("exit_minutes_before_close"))
    if exit_minutes_before_close < 0 or exit_minutes_before_close > 60:
        msg = f"exit_minutes_before_close must be in [0, 60], got {exit_minutes_before_close}"
        raise ValueError(msg)

    per_position_notional_pct = float(required("per_position_notional_pct"))
    if not 0 < per_position_notional_pct <= 1:
        msg = f"per_position_notional_pct must be in (0, 1], got {per_position_notional_pct!r}"
        raise ValueError(msg)

    session_entry_rate = float(required("session_entry_rate"))
    if not 0 <= session_entry_rate <= 1:
        msg = f"session_entry_rate must be in [0, 1], got {session_entry_rate!r}"
        raise ValueError(msg)

    seed = int(required("seed"))

    return {
        "opening_range_minutes": opening_range_minutes,
        "entry_window_minutes": entry_window_minutes,
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
        "session_entry_rate": session_entry_rate,
        "seed": seed,
    }


def _no_signal(narrative: str, *, extras: dict[str, Any] | None = None) -> StrategyDecision:
    return StrategyDecision(
        intents=[],
        reasoning=DecisionReasoning(
            rule="no_signal",
            narrative=narrative,
            extras=extras or {},
        ),
    )


def _exit_decision(
    symbol: str,
    quantity: float,
    *,
    rule: str,
    narrative: str,
) -> StrategyDecision:
    intent = TradeIntent(
        symbol=symbol,
        side=OrderSide.SELL,
        quantity=float(quantity),
        order_type=OrderType.MARKET,
    )
    return StrategyDecision(
        intents=[intent],
        reasoning=DecisionReasoning(rule=rule, narrative=narrative),
    )

"""Single source of truth for the data-staleness verdict (D-1 queue-at-open).

Two risk-layer gates must agree on whether the latest bar is fresh:

* ``RiskEvaluator._check_data_staleness`` (the ``data_staleness`` veto), and
* ``risk.disable_conditions._evaluate_data_quality`` (the
  ``data_quality_issue`` disable condition, owned by ``ALL_FAMILIES`` and
  therefore active for every strategy).

Historically both inlined the same global ``max_data_staleness_seconds``
budget; their docstrings promised the two verdicts "can never diverge" but
nothing structurally enforced it. Widening one gate without the other would
silently move the veto from ``data_staleness`` to ``disable_condition_active``.
This module makes the non-divergence a construction guarantee: both gates call
:func:`staleness_verdict`, so they cannot disagree on policy.

Policy (founder, D-1) — session identity is authoritative; the wall clock is
only a defense-in-depth ceiling:

* For ``1D`` (daily) strategies a runner locks in on the prior session's close
  and submits at the next open, so the freshest bar is legitimately ~a session
  old. The global intraday budget would false-veto it. Instead:

  - **Fail closed** (stale) when the exchange calendar could not resolve the
    latest completed session (``context.latest_completed_session is None``) —
    an unavailable or ambiguous calendar must never license a trade.
  - Otherwise FRESH iff the bar's session date equals the latest completed
    session **and** the bar is within a generous seven-calendar-day ceiling.
    The ceiling is pure defense in depth against a dead feed whose session
    date happens to match (it cannot under a correct calendar, but the gate
    does not assume the calendar is correct).

* For every other path — non-``1D`` strategies and operator-manual / legacy
  callers with ``strategy_config is None`` — the existing 300-second
  wall-clock budget is unchanged.

The verdict carries the computed age (when a bar exists) so each caller can
build its own audit message without re-deriving it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from milodex.risk.evaluator import EvaluationContext

#: Generous wall-clock ceiling for the 1D session-identity path. Defense in
#: depth only: a correct exchange calendar already makes a dead feed's session
#: date mismatch, so this never fires in normal operation. It bounds the blast
#: radius if the calendar is wrong AND the feed is stale at a matching date.
DAILY_STALENESS_CEILING = timedelta(days=7)

#: Tempo string identifying a daily strategy (``StrategyExecutionConfig.bar_size``).
_DAILY_BAR_SIZE = "1D"


@dataclass(frozen=True)
class StalenessVerdict:
    """Outcome of the shared staleness policy.

    ``is_stale`` is the single bit both gates branch on. ``detail`` is a
    human-readable reason; ``age_seconds`` is the bar age in seconds (or
    ``None`` when no bar was available).
    """

    is_stale: bool
    detail: str
    age_seconds: int | None


def _is_daily(context: EvaluationContext) -> bool:
    # ``bar_size`` is None-safe via getattr: strategy_config is None for
    # operator-manual / legacy callers, and the field defaults to "" (never
    # "1D") for configs that omit tempo, so both correctly take the 300s path.
    return getattr(context.strategy_config, "bar_size", None) == _DAILY_BAR_SIZE


def staleness_verdict(context: EvaluationContext, now: datetime) -> StalenessVerdict:
    """Return the shared stale/fresh verdict for the context's latest bar.

    ``now`` is supplied by the caller (each gate reads ``datetime.now`` from
    its own module) so the verdict stays a pure function and both gates remain
    independently clock-injectable in tests.
    """
    bar = context.latest_bar
    if bar is None:
        return StalenessVerdict(
            is_stale=True,
            detail="no latest bar available",
            age_seconds=None,
        )

    # Normalize to UTC-aware before subtracting: a naive bar timestamp would
    # raise TypeError against an aware ``now`` (offset-naive vs offset-aware).
    # A naive timestamp is assumed UTC — the system stores and compares all
    # market data in UTC.
    bar_ts = bar.timestamp
    if bar_ts.tzinfo is None:
        bar_ts = bar_ts.replace(tzinfo=UTC)
    age = now - bar_ts
    age_seconds = int(age.total_seconds())

    if _is_daily(context):
        latest_session = context.latest_completed_session
        if latest_session is None:
            # Fail closed: an unavailable or ambiguous exchange calendar must
            # never license a 1D submit.
            return StalenessVerdict(
                is_stale=True,
                detail="exchange calendar unavailable; cannot confirm latest session (fail-closed)",
                age_seconds=age_seconds,
            )
        # PRECONDITION: ``bar_ts.date()`` must be the bar's SESSION date. This
        # holds for a daily session-stamped bar (Alpaca daily bars are stamped
        # 00:00 ET == 04:00/05:00 UTC of the session date, so ``.date()`` is the
        # session date). It does NOT hold for an intraday latest-trade bar
        # (``AlpacaDataProvider.get_latest_bar`` -> ``get_stock_latest_bar``),
        # which is stamped at the trade minute: during RTH its ``.date()`` is
        # *today* while ``latest_completed_session`` is the prior session, so the
        # comparison fails CLOSED (blocks). That is safe (over-blocks, never
        # under-blocks) but means the queue-at-open DRAIN (Task C) must evaluate
        # the daily submit against the locked-in daily session bar, not the live
        # latest-trade bar, or legitimate daily submits will be vetoed at the
        # open. The post-close daily submit path is unaffected: post-close the
        # latest-trade bar's date and the latest completed session are both
        # today.
        if bar_ts.date() != latest_session:
            return StalenessVerdict(
                is_stale=True,
                detail=(
                    f"latest bar session {bar_ts.date().isoformat()} is not the latest "
                    f"completed session {latest_session.isoformat()}"
                ),
                age_seconds=age_seconds,
            )
        if age > DAILY_STALENESS_CEILING:
            return StalenessVerdict(
                is_stale=True,
                detail=(
                    f"latest bar is stale by {age_seconds} seconds "
                    f"(beyond {DAILY_STALENESS_CEILING.days}-day ceiling)"
                ),
                age_seconds=age_seconds,
            )
        return StalenessVerdict(
            is_stale=False,
            detail="latest bar matches the latest completed session",
            age_seconds=age_seconds,
        )

    # Non-1D / operator-manual path: the global wall-clock budget, unchanged.
    max_age = timedelta(seconds=context.risk_defaults.max_data_staleness_seconds)
    if age > max_age:
        return StalenessVerdict(
            is_stale=True,
            detail=f"latest bar is stale by {age_seconds} seconds",
            age_seconds=age_seconds,
        )
    return StalenessVerdict(
        is_stale=False,
        detail="latest bar is within staleness limits",
        age_seconds=age_seconds,
    )

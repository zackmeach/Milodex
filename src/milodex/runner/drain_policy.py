"""Drain-time gate helpers for the runner's queued-intent drain.

These decide whether a persisted queued intent may proceed to the submit
path. They are deliberately CONSERVATIVE and FAIL-CLOSED: any condition
that is not an affirmative go produces a DROP, and a broker read that
RAISES is caught here so the exception never reaches the runner loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from milodex.broker.client import BrokerClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TradableDecision:
    # machine tag values: not_tradable | tradability_unknown | tradability_read_error
    drop: bool
    reason: str | None = None  # machine tag (see above), or None on PROCEED
    detail: str | None = None  # human/exception text for logging/explanation


def tradable_drop_decision(broker: BrokerClient, symbol: str) -> TradableDecision:
    """DROP a queued intent unless the broker AFFIRMATIVELY reports tradable.

    PROCEED (drop=False) iff ``broker.is_symbol_tradable(symbol) is True``.
    DROP when the read returns False (halted/inactive), None (unknown), OR
    raises. The raise is caught and converted to a DROP here so a flaky
    broker read can never propagate into the drain loop and crash the runner.
    """
    try:
        tradable = broker.is_symbol_tradable(symbol)
    except Exception as exc:  # noqa: BLE001 - fail-closed: any read error -> DROP
        logger.warning("tradability read failed for %s; dropping intent: %s", symbol, exc)
        return TradableDecision(drop=True, reason="tradability_read_error", detail=str(exc))
    if tradable is True:
        return TradableDecision(drop=False)
    if tradable is False:
        return TradableDecision(drop=True, reason="not_tradable", detail=f"{symbol} not tradable")
    return TradableDecision(
        drop=True, reason="tradability_unknown", detail=f"{symbol} tradability unknown"
    )

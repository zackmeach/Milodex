"""Exposure-direction helpers for risk enforcement."""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, Position


def _held_long_qty(symbol: str, broker_positions: list[Position]) -> float:
    """Held LONG quantity for ``symbol`` (0.0 if absent or net short).

    Shared by :func:`is_exposure_increasing` and
    :func:`exposure_increasing_notional` so the boolean classification and the
    notional projection cannot disagree about what counts as "covered".
    """
    for position in broker_positions:
        if position.symbol.upper() == symbol:
            return max(0.0, float(position.quantity))
    return 0.0


def is_exposure_increasing(intent: Any, broker_positions: list[Position]) -> bool:
    """Return True when an intent increases or opens broker-side exposure.

    R-OPS-004 reconciliation readiness is asymmetric: exposure-increasing
    paper actions fail closed on drift, while broker-grounded reducing sells
    remain available so the operator can flatten risk.
    """
    if intent.side == OrderSide.BUY:
        return True
    held = _held_long_qty(intent.normalized_symbol(), broker_positions)
    return float(intent.quantity) > held


def exposure_increasing_notional(
    intent: Any, request: Any, broker_positions: list[Position]
) -> float:
    """Notional of an intent that INCREASES exposure (0.0 if purely reducing).

    BUY: the full order notional. SELL: only the portion beyond the held
    same-symbol long — a naked short or a sell-beyond-held; a fully-covered exit
    returns 0.0. Shares :func:`_held_long_qty` with :func:`is_exposure_increasing`
    so the cap projection and the increasing/reducing classification cannot drift.

    Assumes ``estimated_order_value == estimated_unit_price * quantity`` (how
    ExecutionRequest is constructed), so a cap's reducing remainder
    ``order_value - increasing`` is always >= 0.
    """
    if intent.side == OrderSide.BUY:
        return float(request.estimated_order_value)
    held = _held_long_qty(intent.normalized_symbol(), broker_positions)
    excess_qty = max(0.0, float(intent.quantity) - held)
    return excess_qty * float(request.estimated_unit_price)

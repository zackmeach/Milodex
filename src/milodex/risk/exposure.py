"""Exposure-direction helpers for risk enforcement."""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, Position


def is_exposure_increasing(intent: Any, broker_positions: list[Position]) -> bool:
    """Return True when an intent increases or opens broker-side exposure.

    R-OPS-004 reconciliation readiness is asymmetric: exposure-increasing
    paper actions fail closed on drift, while broker-grounded reducing sells
    remain available so the operator can flatten risk.
    """
    if intent.side == OrderSide.BUY:
        return True

    symbol = intent.normalized_symbol()
    held_qty = 0.0
    for position in broker_positions:
        if position.symbol.upper() == symbol:
            held_qty = float(position.quantity)
            break
    if held_qty <= 0:
        return True
    return float(intent.quantity) > held_qty

"""Shared position-sizing utilities.

Strategies propose a *target* symbol; the number of shares to buy is a
separate concern that depends on account equity, the configured notional
percentage, and the current unit price. Sizing lives here (in
``execution``) rather than inside each strategy so both the ``regime``
and ``meanrev`` families share one definition — and so the golden-output
tests of each strategy can reason about sizing without re-implementing
the arithmetic.
"""

from __future__ import annotations

import math


def shares_for_notional_pct(
    *,
    equity: float,
    notional_pct: float,
    unit_price: float,
) -> int:
    """Return whole shares sized to ``notional_pct`` of ``equity``.

    Rounds down. Returns ``0`` when the account is too small to afford a
    single share at the requested allocation — callers decide whether to
    skip the trade or raise.

    Raises ``ValueError`` for clearly invalid inputs so misconfiguration
    surfaces loudly instead of silently producing zero-share orders.
    """
    if equity <= 0:
        msg = f"equity must be > 0, got {equity!r}"
        raise ValueError(msg)
    if notional_pct <= 0:
        msg = f"notional_pct must be > 0, got {notional_pct!r}"
        raise ValueError(msg)
    if notional_pct > 1:
        msg = f"notional_pct must be <= 1, got {notional_pct!r}"
        raise ValueError(msg)
    if unit_price <= 0:
        msg = f"unit_price must be > 0, got {unit_price!r}"
        raise ValueError(msg)

    target_notional = equity * notional_pct
    raw_shares = target_notional / unit_price
    return max(0, math.floor(raw_shares))

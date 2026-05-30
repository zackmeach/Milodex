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


#: Decimal places fractional unit sizes are rounded to. Eight places is finer
#: than any spot-crypto exchange lot size (BTC trades to 1e-8, a satoshi), so
#: the rounding never changes a fillable size — it only strips binary-float
#: noise so audit/equity values stay clean.
_FRACTIONAL_UNIT_PRECISION = 8


def fractional_units_for_notional_pct(
    *,
    equity: float,
    notional_pct: float,
    unit_price: float,
) -> float:
    """Return *fractional* units sized to ``notional_pct`` of ``equity``.

    The crypto-archetype companion to :func:`shares_for_notional_pct`. Spot
    crypto is fractional/notional-friendly: a single unit (e.g. one BTC at
    ~$50k) costs far more than a small account's per-position allocation, so
    flooring to whole units — as the equities helper deliberately does — would
    silently zero out every order. This helper does **not** floor; it returns
    the exact notional/price ratio rounded to :data:`_FRACTIONAL_UNIT_PRECISION`
    decimals.

    Input validation mirrors :func:`shares_for_notional_pct` so
    misconfiguration surfaces loudly rather than producing nonsense sizes.
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
    return round(target_notional / unit_price, _FRACTIONAL_UNIT_PRECISION)

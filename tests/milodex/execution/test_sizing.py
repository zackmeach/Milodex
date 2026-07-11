"""Tests for position-sizing helpers.

``shares_for_notional_pct`` (whole-share, equities) is exercised indirectly by
strategy golden-output tests; this module pins the crypto-archetype companion
``fractional_units_for_notional_pct``, which must NOT floor to whole units.
"""

from __future__ import annotations

import pytest

from milodex.execution.sizing import (
    fractional_units_for_notional_pct,
    shares_for_notional_pct,
)


def test_returns_fractional_units_below_one() -> None:
    """A $50k unit at 10% of $100k equity → 0.2 units, NOT floored to 0."""
    units = fractional_units_for_notional_pct(
        equity=100_000.0, notional_pct=0.10, unit_price=50_000.0
    )
    assert units == pytest.approx(0.2)


def test_scales_with_price() -> None:
    units = fractional_units_for_notional_pct(
        equity=100_000.0, notional_pct=0.10, unit_price=25_000.0
    )
    assert units == pytest.approx(0.4)


def test_rounds_to_eight_decimals() -> None:
    """Repeating fractions are rounded to 8 dp so audit/equity values stay clean."""
    units = fractional_units_for_notional_pct(
        equity=100_000.0, notional_pct=0.10, unit_price=30_000.0
    )
    assert units == 0.33333333


def test_raises_on_non_positive_equity() -> None:
    with pytest.raises(ValueError, match="equity"):
        fractional_units_for_notional_pct(equity=0.0, notional_pct=0.10, unit_price=100.0)


def test_raises_on_non_positive_notional() -> None:
    with pytest.raises(ValueError, match="notional_pct"):
        fractional_units_for_notional_pct(equity=100_000.0, notional_pct=0.0, unit_price=100.0)


def test_raises_when_notional_exceeds_one() -> None:
    with pytest.raises(ValueError, match="notional_pct"):
        fractional_units_for_notional_pct(equity=100_000.0, notional_pct=1.5, unit_price=100.0)


def test_raises_on_non_positive_price() -> None:
    with pytest.raises(ValueError, match="unit_price"):
        fractional_units_for_notional_pct(equity=100_000.0, notional_pct=0.10, unit_price=0.0)


def test_fifty_dollar_notional_on_five_hundred_dollar_stock() -> None:
    """R-BRK-008: A $50 notional intent on a $500 stock produces a 0.1-share order.

    Sizing is expressed in notional dollars (fractional shares), not share count.
    The fractional helper must return 0.1; the whole-share helper floors it to 0,
    proving fractional sizing is required to honor notional sizing.
    """
    units = fractional_units_for_notional_pct(equity=500.0, notional_pct=0.10, unit_price=500.0)
    assert units == pytest.approx(0.1)

    whole_shares = shares_for_notional_pct(equity=500.0, notional_pct=0.10, unit_price=500.0)
    assert whole_shares == 0

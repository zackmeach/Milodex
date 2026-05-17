"""Golden test: slippage produces the correct filled_avg_price delta.

The existing test_engine_slippage.py asserts ``engine._slippage_pct`` (the
resolved attribute) — it does not verify that slippage is actually applied when
the broker fills an order.  A regression that stored the slippage correctly
but forgot to pass it to SimulatedBroker, or applied it to the wrong side,
would pass all existing tests.

This test drives SimulatedBroker directly with a known close price and known
slippage, then asserts the exact filled_avg_price for both BUY and SELL.

Fill price model (from simulated.py):
  BUY  fill = close * (1 + slippage_pct)
  SELL fill = close * (1 - slippage_pct)

Meaningfulness check (mutate-then-revert):
    In simulated.py change fill_price_for to return ``close`` unconditionally
    (remove the slippage multiplication).  Both golden assertions fail because
    the returned fill price equals 100.0, not 100.10 / 99.90.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from milodex.broker.models import OrderSide, OrderStatus, OrderType, TimeInForce
from milodex.broker.simulated import SimulatedBroker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLOSE = 100.0
_SLIPPAGE = 0.001  # 10 bps
_QTY = 5.0
_COMMISSION = 0.0  # isolated so commission doesn't obscure slippage signal


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def sim_broker() -> SimulatedBroker:
    broker = SimulatedBroker(slippage_pct=_SLIPPAGE, commission_per_trade=_COMMISSION)
    broker.set_simulation_day(
        day=datetime.now(tz=UTC),
        closes={"SPY": _CLOSE},
    )
    return broker


# ---------------------------------------------------------------------------
# Golden tests
# ---------------------------------------------------------------------------


def test_slippage_buy_fill_price_golden(sim_broker):
    """BUY fill = close * (1 + slippage_pct): correct magnitude and sign.

    Expected: 100.0 * 1.001 = 100.10
    Sign check: buy fill > close (slippage is a cost for the buyer).
    """
    order = sim_broker.submit_order(
        symbol="SPY",
        side=OrderSide.BUY,
        quantity=_QTY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    expected = _CLOSE * (1.0 + _SLIPPAGE)

    assert order.status == OrderStatus.FILLED
    assert order.filled_avg_price == pytest.approx(expected, rel=1e-9), (
        f"BUY fill price {order.filled_avg_price} != expected {expected}. "
        "Slippage is not being applied (or applied with wrong sign) for buys."
    )
    # Sign sanity: buyer pays more than the mid.
    assert order.filled_avg_price > _CLOSE


def test_slippage_sell_fill_price_golden(sim_broker):
    """SELL fill = close * (1 - slippage_pct): correct magnitude and sign.

    Expected: 100.0 * 0.999 = 99.90
    Sign check: sell fill < close (slippage is a cost for the seller).
    """
    order = sim_broker.submit_order(
        symbol="SPY",
        side=OrderSide.SELL,
        quantity=_QTY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    expected = _CLOSE * (1.0 - _SLIPPAGE)

    assert order.status == OrderStatus.FILLED
    assert order.filled_avg_price == pytest.approx(expected, rel=1e-9), (
        f"SELL fill price {order.filled_avg_price} != expected {expected}. "
        "Slippage is not being applied (or applied with wrong sign) for sells."
    )
    # Sign sanity: seller receives less than the mid.
    assert order.filled_avg_price < _CLOSE


def test_slippage_buy_and_sell_deltas_are_symmetric(sim_broker):
    """BUY and SELL deltas from close are equal in magnitude (symmetric model).

    delta_buy  = fill_buy  - close =  close * slippage_pct
    delta_sell = close - fill_sell =  close * slippage_pct

    A regression that used different slippage fractions for each side would
    break this assertion.
    """
    buy_order = sim_broker.submit_order(
        symbol="SPY",
        side=OrderSide.BUY,
        quantity=_QTY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    sell_order = sim_broker.submit_order(
        symbol="SPY",
        side=OrderSide.SELL,
        quantity=_QTY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    delta_buy = buy_order.filled_avg_price - _CLOSE
    delta_sell = _CLOSE - sell_order.filled_avg_price

    assert delta_buy == pytest.approx(delta_sell, rel=1e-9), (
        f"Buy delta {delta_buy} != sell delta {delta_sell}. "
        "Slippage is asymmetric between buy and sell sides."
    )
    assert delta_buy == pytest.approx(_CLOSE * _SLIPPAGE, rel=1e-9)

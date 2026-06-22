"""Tests for the BrokerClient abstract base class.

R-BRK-001: BrokerClient is an ABC defining the full broker surface (orders,
positions, account, market clock). A subclass that omits any abstract method
must fail instantiation with TypeError.
"""

from __future__ import annotations

import pytest

from milodex.broker.client import BrokerClient
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderType,
    Position,
    TimeInForce,
)

_ABSTRACT_METHODS = (
    "submit_order",
    "get_order",
    "cancel_order",
    "cancel_all_orders",
    "get_orders",
    "get_positions",
    "get_position",
    "get_account",
    "is_market_open",
)


class _FullBroker(BrokerClient):
    """Concrete subclass implementing every abstract method — instantiates cleanly."""

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
        client_order_id: str | None = None,
    ) -> Order:
        raise NotImplementedError  # pragma: no cover

    def get_order(self, order_id: str) -> Order:
        raise NotImplementedError  # pragma: no cover

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError  # pragma: no cover

    def cancel_all_orders(self) -> list[Order]:
        raise NotImplementedError  # pragma: no cover

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        raise NotImplementedError  # pragma: no cover

    def get_positions(self) -> list[Position]:
        raise NotImplementedError  # pragma: no cover

    def get_position(self, symbol: str) -> Position | None:
        raise NotImplementedError  # pragma: no cover

    def get_account(self) -> AccountInfo:
        raise NotImplementedError  # pragma: no cover

    def is_market_open(self) -> bool:
        raise NotImplementedError  # pragma: no cover


def test_full_concrete_subclass_instantiates() -> None:
    """R-BRK-001 (positive): a subclass implementing every abstract method instantiates."""
    broker = _FullBroker()
    assert isinstance(broker, BrokerClient)


@pytest.mark.parametrize("missing_method", _ABSTRACT_METHODS)
def test_incomplete_subclass_raises_type_error(missing_method: str) -> None:
    """R-BRK-001 (negative): omitting any single abstract method fails instantiation.

    Each abstract method is load-bearing for the ABC contract: dropping any one
    leaves the subclass abstract, so instantiation must raise TypeError.
    """
    methods = {
        name: getattr(_FullBroker, name) for name in _ABSTRACT_METHODS if name != missing_method
    }
    incomplete_cls = type(f"_Missing_{missing_method}", (BrokerClient,), methods)

    with pytest.raises(TypeError):
        incomplete_cls()  # type: ignore[abstract]

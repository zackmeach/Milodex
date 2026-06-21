# tests/milodex/broker/test_alpaca_client.py
"""Tests for AlpacaBrokerClient.

All tests mock the Alpaca SDK -- no real API calls.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests
from alpaca.common.exceptions import APIError

from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)


def _make_429_api_error() -> APIError:
    """Construct an APIError that reports status_code == 429."""
    http_error = MagicMock(spec=requests.exceptions.HTTPError)
    http_error.response = MagicMock()
    http_error.response.status_code = 429
    return APIError('{"code": 429, "message": "too many requests"}', http_error)


@pytest.fixture()
def client():
    """Create an AlpacaBrokerClient with mocked credentials."""
    with patch("milodex.broker.alpaca_client.get_alpaca_credentials") as mock_creds:
        mock_creds.return_value = ("test-key", "test-secret")
        with patch("milodex.broker.alpaca_client.get_trading_mode") as mock_mode:
            mock_mode.return_value = "paper"
            with patch("milodex.broker.alpaca_client.TradingClient") as mock_cls:
                instance = AlpacaBrokerClient()
                instance._client = mock_cls.return_value
                yield instance


def _mock_alpaca_order(**overrides):
    """Create a mock Alpaca order object."""
    order = MagicMock()
    order.id = overrides.get("id", "order-abc-123")
    order.symbol = overrides.get("symbol", "AAPL")
    order.side = overrides.get("side", "buy")
    order.type = overrides.get("type", "market")
    order.qty = overrides.get("qty", "10")
    order.time_in_force = overrides.get("time_in_force", "day")
    order.status = overrides.get("status", "new")
    order.submitted_at = overrides.get("submitted_at", datetime(2025, 1, 15, 14, 30, tzinfo=UTC))
    order.limit_price = overrides.get("limit_price", None)
    order.stop_price = overrides.get("stop_price", None)
    order.filled_qty = overrides.get("filled_qty", None)
    order.filled_avg_price = overrides.get("filled_avg_price", None)
    order.filled_at = overrides.get("filled_at", None)
    return order


class TestSubmitOrder:
    def test_submit_market_order(self, client):
        """R-BRK-002: AlpacaBrokerClient.submit_order returns a well-typed Order."""
        client._client.submit_order.return_value = _mock_alpaca_order()
        result = client.submit_order("AAPL", OrderSide.BUY, 10.0)
        assert isinstance(result, Order)
        assert result.symbol == "AAPL"
        assert result.side == OrderSide.BUY
        assert result.status == OrderStatus.PENDING

    def test_submit_limit_order(self, client):
        client._client.submit_order.return_value = _mock_alpaca_order(
            type="limit", limit_price="150.50"
        )
        result = client.submit_order(
            "AAPL",
            OrderSide.BUY,
            10.0,
            order_type=OrderType.LIMIT,
            limit_price=150.50,
        )
        assert result.order_type == OrderType.LIMIT


class TestGetOrder:
    def test_get_order_by_id(self, client):
        client._client.get_order_by_id.return_value = _mock_alpaca_order(
            status="filled", filled_qty="10", filled_avg_price="151.25"
        )
        result = client.get_order("order-abc-123")
        assert isinstance(result, Order)
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == 10.0


class TestCancelOrder:
    def test_cancel_returns_true(self, client):
        client._client.cancel_order_by_id.return_value = None
        assert client.cancel_order("order-abc-123") is True


class TestCancelAllOrders:
    def test_cancel_all_returns_list(self, client):
        """R-BRK-004: cancel_all_orders() returns cancelled orders for kill-switch use."""
        client._client.cancel_orders.return_value = [
            _mock_alpaca_order(id="o1", status="pending_cancel"),
            _mock_alpaca_order(id="o2", status="pending_cancel"),
        ]
        result = client.cancel_all_orders()
        assert len(result) == 2


class TestGetOrders:
    def test_get_all_orders(self, client):
        """R-BRK-003: get_orders returns Milodex Order domain types, not raw Alpaca objects."""
        client._client.get_orders.return_value = [
            _mock_alpaca_order(id="o1"),
            _mock_alpaca_order(id="o2"),
        ]
        result = client.get_orders()
        assert len(result) == 2
        assert all(isinstance(o, Order) for o in result)


class TestGetPositions:
    def test_get_positions(self, client):
        pos = MagicMock()
        pos.symbol = "AAPL"
        pos.qty = "10"
        pos.avg_entry_price = "150.0"
        pos.current_price = "155.0"
        pos.market_value = "1550.0"
        pos.unrealized_pl = "50.0"
        pos.unrealized_plpc = "0.0333"

        client._client.get_all_positions.return_value = [pos]
        result = client.get_positions()
        assert len(result) == 1
        assert isinstance(result[0], Position)
        assert result[0].symbol == "AAPL"

    def test_get_position_found(self, client):
        pos = MagicMock()
        pos.symbol = "AAPL"
        pos.qty = "10"
        pos.avg_entry_price = "150.0"
        pos.current_price = "155.0"
        pos.market_value = "1550.0"
        pos.unrealized_pl = "50.0"
        pos.unrealized_plpc = "0.0333"

        client._client.get_open_position.return_value = pos
        result = client.get_position("AAPL")
        assert isinstance(result, Position)

    def test_get_position_not_found(self, client):
        # Simulate Alpaca raising when position not found.
        # We use a generic Exception subclass here to avoid importing alpaca in test code.
        client._client.get_open_position.side_effect = Exception("position does not exist")
        result = client.get_position("ZZZZZ")
        assert result is None


class TestGetAccount:
    def test_get_account(self, client):
        acct = MagicMock()
        acct.equity = "10000.0"
        acct.cash = "5000.0"
        acct.buying_power = "5000.0"
        acct.portfolio_value = "10000.0"
        acct.equity_previous_close = "9850.0"

        client._client.get_account.return_value = acct
        result = client.get_account()
        assert isinstance(result, AccountInfo)
        assert result.equity == 10000.0
        assert result.daily_pnl == 150.0  # 10000 - 9850

    def test_get_account_retries_on_transient_read_timeout(self, client):
        """A read-only call survives a transient Alpaca ReadTimeout (the
        2026-06-17 co-run-soak crash). The 429-only helper would propagate it
        and kill the runner; the transient helper retries the idempotent read."""
        acct = MagicMock()
        acct.equity = "10000.0"
        acct.cash = "5000.0"
        acct.buying_power = "5000.0"
        acct.portfolio_value = "10000.0"
        acct.equity_previous_close = "9850.0"

        client._client.get_account.side_effect = [
            requests.exceptions.ReadTimeout("read timed out"),
            acct,
        ]
        with patch("time.sleep"):
            result = client.get_account()

        assert isinstance(result, AccountInfo)
        assert client._client.get_account.call_count == 2

    def test_get_account_falls_back_to_last_equity(self, client):
        acct = MagicMock()
        acct.equity = "10000.0"
        acct.cash = "5000.0"
        acct.buying_power = "5000.0"
        acct.portfolio_value = "10000.0"
        del acct.equity_previous_close
        acct.last_equity = "9900.0"

        client._client.get_account.return_value = acct
        result = client.get_account()

        assert isinstance(result, AccountInfo)
        assert result.daily_pnl == 100.0


class TestIsMarketOpen:
    def test_market_open(self, client):
        """R-BRK-005: AlpacaBrokerClient exposes a market-clock query (is_market_open)."""
        clock = MagicMock()
        clock.is_open = True
        client._client.get_clock.return_value = clock
        assert client.is_market_open() is True

    def test_market_closed(self, client):
        clock = MagicMock()
        clock.is_open = False
        client._client.get_clock.return_value = clock
        assert client.is_market_open() is False


class TestRetryOn429:
    """Verify that broker calls retry on Alpaca 429 rate-limit responses."""

    def test_get_account_retries_on_429_then_succeeds(self, client):
        """get_account retries when Alpaca returns 429, then returns on success."""
        err = _make_429_api_error()
        acct = MagicMock()
        acct.equity = "10000.0"
        acct.cash = "5000.0"
        acct.buying_power = "5000.0"
        acct.portfolio_value = "10000.0"
        acct.equity_previous_close = "9900.0"

        client._client.get_account.side_effect = [err, err, acct]

        with patch("time.sleep"):
            result = client.get_account()

        assert isinstance(result, AccountInfo)
        assert client._client.get_account.call_count == 3

    def test_get_account_exhausts_retries_and_reraises(self, client):
        """get_account re-raises 429 after max_attempts (default 5)."""
        err = _make_429_api_error()
        client._client.get_account.side_effect = err

        with patch("time.sleep"):
            with pytest.raises(APIError) as exc_info:
                client.get_account()

        assert exc_info.value is err
        assert client._client.get_account.call_count == 5

    def test_submit_order_retries_on_429_then_succeeds(self, client):
        """submit_order retries on 429.

        Alpaca returns 429 before any state change, so retry is safe and
        cannot produce duplicate orders.
        """
        err = _make_429_api_error()
        client._client.submit_order.side_effect = [err, _mock_alpaca_order()]

        with patch("time.sleep"):
            result = client.submit_order("AAPL", OrderSide.BUY, 10.0)

        assert isinstance(result, Order)
        assert client._client.submit_order.call_count == 2

    def test_get_orders_retries_on_429_then_succeeds(self, client):
        """get_orders retries on 429 then returns the order list."""
        err = _make_429_api_error()
        client._client.get_orders.side_effect = [err, [_mock_alpaca_order()]]

        with patch("time.sleep"):
            result = client.get_orders()

        assert len(result) == 1
        assert client._client.get_orders.call_count == 2

    def test_get_positions_retries_on_429_then_succeeds(self, client):
        """get_positions retries on 429 then returns the position list."""
        err = _make_429_api_error()
        pos = MagicMock()
        pos.symbol = "AAPL"
        pos.qty = "5"
        pos.avg_entry_price = "150.0"
        pos.current_price = "155.0"
        pos.market_value = "775.0"
        pos.unrealized_pl = "25.0"
        pos.unrealized_plpc = "0.033"
        client._client.get_all_positions.side_effect = [err, [pos]]

        with patch("time.sleep"):
            result = client.get_positions()

        assert len(result) == 1
        assert client._client.get_all_positions.call_count == 2

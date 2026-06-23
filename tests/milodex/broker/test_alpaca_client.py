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
        """cancel_all_orders() returns the cancelled orders (kill-switch enforcement helper)."""
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
        """AlpacaBrokerClient exposes a boolean market-clock query (is_market_open)."""
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


def _cal(session_date, close):
    """A minimal stand-in for alpaca-py's Calendar (date + tz-naive ET close)."""
    row = MagicMock()
    row.date = session_date
    row.close = close
    return row


class TestLatestCompletedSession:
    """latest_completed_session maps the Alpaca trading calendar to the date of
    the most recent session that has already closed; fails closed (None) on any
    error or ambiguity. Alpaca's Calendar.close is tz-naive US/Eastern wall time.
    """

    def test_picks_latest_session_already_closed(self, client):
        from datetime import date

        # now = Mon 2026-05-11 14:00 UTC == 10:00 ET. Fri 05-08 closed at 16:00
        # ET (20:00 UTC); Mon 05-11 closes at 16:00 ET (still in the future).
        now = datetime(2026, 5, 11, 14, 0, tzinfo=UTC)
        client._client.get_calendar.return_value = [
            _cal(date(2026, 5, 7), datetime(2026, 5, 7, 16, 0)),
            _cal(date(2026, 5, 8), datetime(2026, 5, 8, 16, 0)),
            _cal(date(2026, 5, 11), datetime(2026, 5, 11, 16, 0)),  # not yet closed
        ]
        assert client.latest_completed_session(now) == date(2026, 5, 8)

    def test_session_closing_exactly_at_now_counts_as_completed(self, client):
        from datetime import date

        # now == Fri 16:00 ET (20:00 UTC). The Friday close (<= now) counts.
        now = datetime(2026, 5, 8, 20, 0, tzinfo=UTC)
        client._client.get_calendar.return_value = [
            _cal(date(2026, 5, 7), datetime(2026, 5, 7, 16, 0)),
            _cal(date(2026, 5, 8), datetime(2026, 5, 8, 16, 0)),
        ]
        assert client.latest_completed_session(now) == date(2026, 5, 8)

    def test_returns_none_when_no_session_has_closed(self, client):
        from datetime import date

        # now is before the only session's close.
        now = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)  # 08:00 ET, pre-close
        client._client.get_calendar.return_value = [
            _cal(date(2026, 5, 8), datetime(2026, 5, 8, 16, 0)),
        ]
        assert client.latest_completed_session(now) is None

    def test_returns_none_on_empty_calendar(self, client):
        now = datetime(2026, 5, 11, 14, 0, tzinfo=UTC)
        client._client.get_calendar.return_value = []
        assert client.latest_completed_session(now) is None

    def test_returns_none_on_sdk_exception_fail_closed(self, client):
        now = datetime(2026, 5, 11, 14, 0, tzinfo=UTC)
        client._client.get_calendar.side_effect = RuntimeError("boom")
        assert client.latest_completed_session(now) is None

    def test_any_ambiguous_row_fails_closed_to_none(self, client):
        """Any malformed row in the window => None (untrustworthy calendar).

        The docstring promises "None on any failure or ambiguity". A
        skip-and-fall-back returns an OLDER valid session, which then blesses a
        bar dated to that older session as fresh — a fail-OPEN. Any ambiguous
        row poisons the whole latest-session determination, so fail closed.
        """
        from datetime import date

        now = datetime(2026, 5, 11, 14, 0, tzinfo=UTC)
        client._client.get_calendar.return_value = [
            _cal(date(2026, 5, 8), datetime(2026, 5, 8, 16, 0)),
            _cal(None, datetime(2026, 5, 9, 16, 0)),  # ambiguous
            _cal(date(2026, 5, 10), None),  # ambiguous
        ]
        assert client.latest_completed_session(now) is None

    def test_malformed_latest_row_does_not_fall_back_to_prior_session(self, client):
        """The dangerous case: the genuinely-latest session's row is malformed.

        Monday after close, but the Monday row is ambiguous. Skip-and-fall-back
        would return the prior Friday — and a Friday-dated bar would then pass
        staleness as "fresh" on a Monday. Fail closed (None) instead so the risk
        layer blocks the 1D submit.
        """
        from datetime import date

        # now = Mon 2026-05-11 21:00 UTC == 17:00 ET, after the 16:00 ET close.
        now = datetime(2026, 5, 11, 21, 0, tzinfo=UTC)
        client._client.get_calendar.return_value = [
            _cal(date(2026, 5, 7), datetime(2026, 5, 7, 16, 0)),
            _cal(date(2026, 5, 8), datetime(2026, 5, 8, 16, 0)),
            _cal(None, datetime(2026, 5, 11, 16, 0)),  # latest session, malformed
        ]
        assert client.latest_completed_session(now) is None

    def test_retries_on_429_then_succeeds(self, client):
        from datetime import date

        now = datetime(2026, 5, 11, 14, 0, tzinfo=UTC)
        err = _make_429_api_error()
        client._client.get_calendar.side_effect = [
            err,
            [_cal(date(2026, 5, 8), datetime(2026, 5, 8, 16, 0))],
        ]
        with patch("time.sleep"):
            assert client.latest_completed_session(now) == date(2026, 5, 8)
        assert client._client.get_calendar.call_count == 2

    def test_real_calendar_model_et_close_is_dst_correct(self, client):
        """Pin the SDK mapping against REAL alpaca-py Calendar objects (not
        MagicMocks): Calendar.close is tz-naive ET wall time, so 16:00 EDT ==
        20:00 UTC. At 19:59 UTC on Friday the Friday session is NOT yet
        completed; the mapping must localize to America/New_York to get this
        right (a naive-UTC comparison would wrongly count it)."""
        from datetime import date

        from alpaca.trading.models import Calendar

        client._client.get_calendar.return_value = [
            Calendar(date="2026-05-07", open="09:30", close="16:00"),
            Calendar(date="2026-05-08", open="09:30", close="16:00"),
        ]
        # 19:59 UTC Fri == 15:59 EDT, one minute before the 16:00 ET close.
        pre_close = datetime(2026, 5, 8, 19, 59, tzinfo=UTC)
        assert client.latest_completed_session(pre_close) == date(2026, 5, 7)
        # One minute later (20:01 UTC == 16:01 EDT) the Friday session counts.
        post_close = datetime(2026, 5, 8, 20, 1, tzinfo=UTC)
        assert client.latest_completed_session(post_close) == date(2026, 5, 8)

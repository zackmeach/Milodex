"""Order-status / order-type mapping and read-path swallow-site observability.

PR-5b (follow-up to #284): the read-path auth/connect translation and the
submit-classifier are already covered by ``test_alpaca_client_auth_errors.py``
and ``test_alpaca_client_submit_errors.py``. This file covers the remaining
scope:

- ``_translate_order`` must classify EVERY Alpaca terminal status correctly
  (never silently fall through to PENDING, which would inject phantom open
  exposure into the risk layer's caps) and must WARN-log any genuinely
  unrecognized status while still classifying it conservatively (PENDING is
  the conservative choice for an unknown status: better to over-count open
  exposure than under-count it).
- an unrecognized ``order_type`` must WARN-log instead of silently coercing
  to MARKET.
- ``get_position`` / ``cancel_order`` swallow exceptions by contract (return
  None / False); they must now log a WARNING when they do, without changing
  that return contract.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.models import OrderStatus, OrderType


@pytest.fixture()
def client():
    with patch("milodex.broker.alpaca_client.get_alpaca_credentials") as creds:
        creds.return_value = ("test-key", "test-secret")
        with patch("milodex.broker.alpaca_client.get_trading_mode") as mode:
            mode.return_value = "paper"
            with patch("milodex.broker.alpaca_client.TradingClient") as cls:
                instance = AlpacaBrokerClient()
                instance._client = cls.return_value
                yield instance


def _alpaca_order(status: str, order_type: str = "market"):
    order = MagicMock()
    order.id = "order-1"
    order.symbol = "SPY"
    order.status = status
    order.type = order_type
    order.side = "buy"
    order.time_in_force = "day"
    order.qty = "1"
    order.submitted_at = datetime(2026, 6, 30, tzinfo=UTC)
    order.limit_price = None
    order.stop_price = None
    order.filled_qty = None
    order.filled_avg_price = None
    order.filled_at = None
    return order


# Statuses that must map to a terminal (non-PENDING) status. (alpaca_status, expected)
TERMINAL_STATUSES = [
    ("filled", OrderStatus.FILLED),
    ("canceled", OrderStatus.CANCELLED),
    ("expired", OrderStatus.CANCELLED),
    ("rejected", OrderStatus.REJECTED),
    ("done_for_day", OrderStatus.CANCELLED),
    ("replaced", OrderStatus.CANCELLED),
]


@pytest.mark.parametrize(("alpaca_status", "expected"), TERMINAL_STATUSES)
def test_terminal_status_never_reported_as_pending(client, alpaca_status, expected):
    order = client._translate_order(_alpaca_order(alpaca_status))
    assert order.status == expected
    assert order.status != OrderStatus.PENDING


# Statuses that are NOT terminal — Alpaca documents these as still-live /
# in-flight, so they must classify as open (PENDING) and count toward
# in-flight exposure in evaluator.py. "stopped" = a fill is guaranteed but
# not yet posted; "suspended" = paused, may resume; "pending_review" is
# currently absent from _STATUS_MAP entirely (must not fall through to the
# unknown-status WARN path, since the map claims to be exhaustive).
OPEN_STATUSES = [
    "stopped",
    "suspended",
    "pending_review",
]


@pytest.mark.parametrize("alpaca_status", OPEN_STATUSES)
def test_inflight_status_classifies_as_open_not_terminal(client, alpaca_status, caplog):
    with caplog.at_level(logging.WARNING, logger="milodex.broker.alpaca_client"):
        order = client._translate_order(_alpaca_order(alpaca_status))

    assert order.status == OrderStatus.PENDING
    assert order.is_open
    # These are known/mapped statuses, not unknown ones — no WARN.
    assert not any(record.levelno == logging.WARNING for record in caplog.records)


def test_unmapped_status_warns_and_classifies_conservatively(client, caplog):
    with caplog.at_level(logging.WARNING, logger="milodex.broker.alpaca_client"):
        order = client._translate_order(_alpaca_order("some_future_alpaca_status"))

    # Conservative: an unknown status is treated as still-open (PENDING), never
    # silently dropped from risk-layer open exposure.
    assert order.status == OrderStatus.PENDING
    assert any(
        "some_future_alpaca_status" in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_known_status_does_not_warn(client, caplog):
    with caplog.at_level(logging.WARNING, logger="milodex.broker.alpaca_client"):
        client._translate_order(_alpaca_order("filled"))

    assert not any(record.levelno == logging.WARNING for record in caplog.records)


def test_unrecognized_order_type_warns_instead_of_silent_market_coercion(client, caplog):
    with caplog.at_level(logging.WARNING, logger="milodex.broker.alpaca_client"):
        order = client._translate_order(_alpaca_order("filled", order_type="trailing_stop"))

    assert order.order_type == OrderType.MARKET
    assert any(
        "trailing_stop" in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_known_order_type_does_not_warn(client, caplog):
    with caplog.at_level(logging.WARNING, logger="milodex.broker.alpaca_client"):
        client._translate_order(_alpaca_order("filled", order_type="limit"))

    assert not any(record.levelno == logging.WARNING for record in caplog.records)


def test_cancel_order_swallow_logs_warning_but_still_returns_false(client, caplog):
    client._client.cancel_order_by_id.side_effect = APIError('{"message": "order not found"}')

    with caplog.at_level(logging.WARNING, logger="milodex.broker.alpaca_client"):
        result = client.cancel_order("order-1")

    assert result is False
    assert any(record.levelno == logging.WARNING for record in caplog.records)

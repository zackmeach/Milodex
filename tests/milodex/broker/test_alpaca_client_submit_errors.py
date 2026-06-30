"""submit_order's APIError -> typed-exception classifier (the execution chokepoint).

``execution/service.py`` catches exactly ``(OrderRejectedError, InsufficientFundsError)``
around the submit; a misclassification in this four-branch classifier would silently
break the chokepoint's error handling. The classifier was previously untested.

Kept in a dedicated file (mirrors ``test_alpaca_client_auth_errors.py``) so the new
coverage stays isolated.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from alpaca.common.exceptions import APIError

from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.exceptions import (
    BrokerAuthError,
    BrokerConnectionError,
    InsufficientFundsError,
    OrderRejectedError,
)
from milodex.broker.models import OrderSide


def _api_error(message: str, status: int = 400) -> APIError:
    """APIError whose status_code is a non-429 (so the 429-retry helper re-raises
    it immediately) and whose ``str()`` carries ``message``."""
    http_error = MagicMock(spec=requests.exceptions.HTTPError)
    http_error.response = MagicMock()
    http_error.response.status_code = status
    return APIError(message, http_error)


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


def _submit(client: AlpacaBrokerClient):
    return client.submit_order(symbol="SPY", side=OrderSide.BUY, quantity=1)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # forbidden / auth -> BrokerAuthError (checked first)
        ('{"message": "forbidden."}', BrokerAuthError),
        ('{"message": "request is not authorized."}', BrokerAuthError),  # "auth" in "authorized"
        # insufficient / buying power -> InsufficientFundsError
        ('{"message": "insufficient buying power"}', InsufficientFundsError),
        ('{"message": "insufficient day trading buying power"}', InsufficientFundsError),
        # everything else APIError -> OrderRejectedError (the catch-all)
        ('{"message": "order rejected: market is closed"}', OrderRejectedError),
        ('{"message": "potential wash trade detected"}', OrderRejectedError),
    ],
)
def test_submit_order_apierror_is_classified(client, message, expected):
    client._client.submit_order.side_effect = _api_error(message)

    with pytest.raises(expected) as ei:
        _submit(client)

    # The original APIError is preserved as the cause for logs / traceback.
    assert isinstance(ei.value.__cause__, APIError)


@pytest.mark.parametrize("message", ["connection reset by peer", "read timeout"])
def test_submit_order_connect_timeout_becomes_broker_connection_error(client, message):
    # A non-APIError whose message reads as a network failure -> BrokerConnectionError.
    client._client.submit_order.side_effect = RuntimeError(message)

    with pytest.raises(BrokerConnectionError):
        _submit(client)


def test_submit_order_unrelated_exception_is_reraised_unchanged(client):
    # A non-network, non-API exception is a genuine bug — it must propagate as-is,
    # never silently reclassified into a broker exception.
    err = ValueError("genuine programming bug")
    client._client.submit_order.side_effect = err

    with pytest.raises(ValueError) as ei:
        _submit(client)
    assert ei.value is err

"""Broker-boundary exception translation on the READ paths.

A 401 (bad / expired / placeholder Alpaca key) on a read used to escape as a raw
``APIError`` and the CLI rendered it as ``Unexpected error (APIError): ...`` for
``status`` / ``positions`` / ``orders``. ``AlpacaBrokerClient._read_call`` now
translates read-path vendor exceptions into the broker-agnostic hierarchy so
callers (the CLI ``BrokerError`` handler, the ``report`` broker probe, risk-layer
reads) get an actionable ``BrokerAuthError`` instead.

Kept in a dedicated file (not appended to ``test_alpaca_client.py``) to keep the
new coverage isolated.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from alpaca.common.exceptions import APIError

from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.exceptions import BrokerAuthError, BrokerConnectionError, BrokerError


def _api_error(message: str, status: int = 401) -> APIError:
    """Construct an APIError whose ``status_code`` is a non-429 (so the retry
    helper re-raises it immediately) and whose ``str()`` carries ``message``."""
    http_error = MagicMock(spec=requests.exceptions.HTTPError)
    http_error.response = MagicMock()
    http_error.response.status_code = status
    return APIError(message, http_error)


@pytest.fixture()
def client():
    """AlpacaBrokerClient with a fully mocked underlying alpaca-py client."""
    with patch("milodex.broker.alpaca_client.get_alpaca_credentials") as creds:
        creds.return_value = ("test-key", "test-secret")
        with patch("milodex.broker.alpaca_client.get_trading_mode") as mode:
            mode.return_value = "paper"
            with patch("milodex.broker.alpaca_client.TradingClient") as cls:
                instance = AlpacaBrokerClient()
                instance._client = cls.return_value
                yield instance


# (underlying alpaca-py attribute made to fail, public read method to invoke)
READ_PATHS = [
    ("get_account", "get_account"),
    ("get_orders", "get_orders"),
    ("get_all_positions", "get_positions"),
    ("get_clock", "is_market_open"),
]


def _fail(client: AlpacaBrokerClient, alpaca_attr: str, exc: Exception) -> None:
    getattr(client._client, alpaca_attr).side_effect = exc


@pytest.mark.parametrize(("alpaca_attr", "method"), READ_PATHS)
def test_read_path_401_raises_actionable_broker_auth_error(client, alpaca_attr, method):
    _fail(client, alpaca_attr, _api_error('{"message": "unauthorized."}'))

    with pytest.raises(BrokerAuthError) as ei:
        getattr(client, method)()

    msg = str(ei.value)
    assert "authentication failed" in msg.lower()
    assert "ALPACA_API_KEY" in msg
    # The raw vendor payload (and any HTML 401 body) must NOT leak into the
    # rendered message — that was the report.py "UNREACHABLE (<html>...)" bug.
    assert "unauthorized" not in msg.lower()
    # The original APIError is preserved as the cause for the logs / traceback.
    assert isinstance(ei.value.__cause__, APIError)


@pytest.mark.parametrize(("alpaca_attr", "method"), READ_PATHS)
def test_read_path_non_auth_apierror_is_reraised_unchanged(client, alpaca_attr, method):
    # Translation scope is deliberately narrow: a NON-auth APIError (e.g. a 500,
    # or a 429 that exhausted retries) is re-raised UNCHANGED — preserving the
    # existing read-path contract (see test_get_account_exhausts_retries_and_reraises).
    err = _api_error('{"message": "internal server error"}', status=500)
    _fail(client, alpaca_attr, err)

    with pytest.raises(APIError) as ei:
        getattr(client, method)()

    assert ei.value is err
    assert not isinstance(ei.value, BrokerError)


@pytest.mark.parametrize(("alpaca_attr", "method"), READ_PATHS)
def test_read_path_connect_timeout_becomes_broker_connection_error(client, alpaca_attr, method):
    # A non-transient exception whose message reads as a network failure. (A real
    # ConnectTimeout would be retried 5x by call_with_retry_on_transient with
    # sleeps; this exercises _read_call's translation branch directly.)
    _fail(client, alpaca_attr, RuntimeError("connection timeout talking to the broker"))

    with pytest.raises(BrokerConnectionError):
        getattr(client, method)()


@pytest.mark.parametrize(("alpaca_attr", "method"), READ_PATHS)
def test_read_path_unrelated_exception_is_reraised_unchanged(client, alpaca_attr, method):
    # _read_call must NOT swallow a genuine bug: a non-network, non-API exception
    # propagates as-is (not wrapped in BrokerError) so real defects stay visible.
    _fail(client, alpaca_attr, ValueError("genuine programming bug"))

    with pytest.raises(ValueError):
        getattr(client, method)()

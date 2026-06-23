"""Tests for the broker tradable/asset-status read (D-1 queue-at-open, Phase 4).

`is_symbol_tradable` is the drain-time halt/tradable seam: ``True`` only when the
broker affirmatively reports the asset tradable AND active; ``False`` when it
exists but is halted/inactive/not-tradable; the read MUST NOT swallow exceptions
(the drain-time helper owns the catch). Covers the Alpaca real impl (SDK mocked)
and the SimulatedBroker test-controllable fake.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.simulated import SimulatedBroker


@pytest.fixture()
def client():
    with patch("milodex.broker.alpaca_client.get_alpaca_credentials") as creds:
        creds.return_value = ("k", "s")
        with patch("milodex.broker.alpaca_client.get_trading_mode") as mode:
            mode.return_value = "paper"
            with patch("milodex.broker.alpaca_client.TradingClient") as cls:
                inst = AlpacaBrokerClient()
                inst._client = cls.return_value
                yield inst


def _asset(tradable=True, status="active"):
    a = MagicMock()
    a.tradable = tradable
    a.status = MagicMock()
    a.status.value = status
    return a


class TestAlpacaIsSymbolTradable:
    def test_tradable_active_returns_true(self, client):
        client._client.get_asset.return_value = _asset(tradable=True, status="active")
        assert client.is_symbol_tradable("AAPL") is True

    def test_not_tradable_returns_false(self, client):
        client._client.get_asset.return_value = _asset(tradable=False, status="active")
        assert client.is_symbol_tradable("AAPL") is False

    def test_inactive_status_returns_false(self, client):
        client._client.get_asset.return_value = _asset(tradable=True, status="inactive")
        assert client.is_symbol_tradable("AAPL") is False

    def test_status_plain_string_active(self, client):
        a = _asset(tradable=True)
        a.status = "active"  # no .value attribute
        client._client.get_asset.return_value = a
        assert client.is_symbol_tradable("AAPL") is True

    def test_api_error_propagates(self, client):
        client._client.get_asset.side_effect = APIError("not found")
        with pytest.raises(Exception):  # broker does NOT swallow; drain helper does
            client.is_symbol_tradable("ZZZZ")


def _sim():
    return SimulatedBroker(slippage_pct=0.0, commission_per_trade=0.0)


class TestSimulatedIsSymbolTradable:
    def test_symbol_with_close_is_tradable(self):
        b = _sim()
        b.set_simulation_day(datetime(2025, 1, 2, tzinfo=UTC), {"AAPL": 190.0})
        assert b.is_symbol_tradable("AAPL") is True
        assert b.is_symbol_tradable("aapl") is True  # case-insensitive

    def test_symbol_without_close_is_unknown(self):
        b = _sim()
        b.set_simulation_day(datetime(2025, 1, 2, tzinfo=UTC), {"AAPL": 190.0})
        assert b.is_symbol_tradable("MSFT") is None

    def test_forced_override_wins(self):
        b = _sim()
        b.set_simulation_day(datetime(2025, 1, 2, tzinfo=UTC), {"AAPL": 190.0})
        b.set_tradable_override("AAPL", False)
        assert b.is_symbol_tradable("AAPL") is False
        b.set_tradable_override("AAPL", RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            b.is_symbol_tradable("AAPL")

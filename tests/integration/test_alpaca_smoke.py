# tests/integration/test_alpaca_smoke.py
"""Integration smoke tests against Alpaca paper trading.

These tests hit the real Alpaca API using credentials from .env.
They are skipped in CI and run manually:

    pytest tests/integration/ -v -m integration

Requires valid ALPACA_API_KEY and ALPACA_SECRET_KEY in .env.
"""

from datetime import date, timedelta

import pytest

from milodex.config import get_alpaca_credentials

# Skip all tests in this module if credentials aren't configured
try:
    get_alpaca_credentials()
    HAS_CREDENTIALS = True
except ValueError:
    HAS_CREDENTIALS = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not HAS_CREDENTIALS, reason="No Alpaca credentials in .env"),
]


class TestAlpacaDataSmoke:
    def test_fetch_spy_daily_bars(self):
        from milodex.data.alpaca_provider import AlpacaDataProvider
        from milodex.data.models import Timeframe

        provider = AlpacaDataProvider()
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=5)
        result = provider.get_bars(["SPY"], Timeframe.DAY_1, start, end)

        assert "SPY" in result
        assert len(result["SPY"]) > 0

    def test_get_latest_bar(self):
        from milodex.data.alpaca_provider import AlpacaDataProvider
        from milodex.data.models import Bar

        provider = AlpacaDataProvider()
        bar = provider.get_latest_bar("SPY")
        assert isinstance(bar, Bar)
        assert bar.close > 0


class TestAlpacaBrokerSmoke:
    def test_get_account(self):
        from milodex.broker.alpaca_client import AlpacaBrokerClient
        from milodex.broker.models import AccountInfo

        client = AlpacaBrokerClient()
        acct = client.get_account()
        assert isinstance(acct, AccountInfo)
        assert acct.equity > 0

    def test_is_market_open_returns_bool(self):
        from milodex.broker.alpaca_client import AlpacaBrokerClient

        client = AlpacaBrokerClient()
        result = client.is_market_open()
        assert isinstance(result, bool)

# tests/milodex/test_config.py
"""Tests for shared configuration."""

from pathlib import Path
from unittest.mock import patch

import pytest

from milodex.config import get_alpaca_credentials, get_cache_dir, get_trading_mode


class TestGetAlpacaCredentials:
    def test_returns_key_and_secret_from_env(self):
        with patch.dict(
            "os.environ",
            {
                "ALPACA_API_KEY": "test-key",
                "ALPACA_SECRET_KEY": "test-secret",
            },
        ):
            key, secret = get_alpaca_credentials()
            assert key == "test-key"
            assert secret == "test-secret"

    def test_raises_when_api_key_missing(self):
        with patch.dict("os.environ", {"ALPACA_SECRET_KEY": "secret"}, clear=True):
            with pytest.raises(ValueError, match="ALPACA_API_KEY"):
                get_alpaca_credentials()

    def test_raises_when_secret_key_missing(self):
        with patch.dict("os.environ", {"ALPACA_API_KEY": "key"}, clear=True):
            with pytest.raises(ValueError, match="ALPACA_SECRET_KEY"):
                get_alpaca_credentials()


class TestGetTradingMode:
    def test_returns_paper_mode(self):
        with patch.dict("os.environ", {"TRADING_MODE": "paper"}):
            assert get_trading_mode() == "paper"

    def test_returns_live_mode(self):
        with patch.dict("os.environ", {"TRADING_MODE": "live"}):
            assert get_trading_mode() == "live"

    def test_defaults_to_paper_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            assert get_trading_mode() == "paper"

    def test_raises_on_invalid_mode(self):
        with patch.dict("os.environ", {"TRADING_MODE": "yolo"}):
            with pytest.raises(ValueError, match="TRADING_MODE"):
                get_trading_mode()


class TestGetCacheDir:
    def test_returns_path_object(self):
        result = get_cache_dir()
        assert isinstance(result, Path)

    def test_default_is_market_cache(self):
        result = get_cache_dir()
        assert result.name == "market_cache"

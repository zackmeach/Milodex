# tests/milodex/test_config.py
"""Tests for shared configuration."""

from pathlib import Path
from unittest.mock import patch

import pytest

from milodex.config import (
    get_alpaca_credentials,
    get_cache_dir,
    get_data_dir,
    get_logs_dir,
    get_trading_mode,
)


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


class TestGetLogsDir:
    def test_returns_path_object(self):
        result = get_logs_dir()
        assert isinstance(result, Path)

    def test_default_is_logs(self):
        result = get_logs_dir()
        assert result.name == "logs"


class TestGetDataDir:
    def test_returns_path_object(self):
        result = get_data_dir()
        assert isinstance(result, Path)

    def test_default_is_data(self):
        result = get_data_dir()
        assert result.name == "data"


class TestSkipDotenvGuard:
    """The import-time ``load_dotenv()`` bootstrap honors MILODEX_SKIP_DOTENV.

    Drives ``milodex.config`` in a subprocess with a stub ``dotenv`` module
    earlier on PYTHONPATH that records the call in an env canary — so the
    guard is pinned in CI (no repo ``.env`` required) and a revert of the
    guard in config.py fails here, not only on machines that have a real
    ``.env`` (where the clean_room drill cell would catch the credential
    leak end-to-end).
    """

    @staticmethod
    def _bootstrap_calls_dotenv(tmp_path: Path, *, skip_flag: str | None) -> bool:
        import os
        import subprocess
        import sys

        import milodex

        (tmp_path / "dotenv.py").write_text(
            "import os\n"
            "def load_dotenv(*args, **kwargs):\n"
            "    os.environ['DOTENV_LOADED_CANARY'] = '1'\n",
            encoding="utf-8",
        )
        src_dir = Path(milodex.__file__).resolve().parents[1]
        env = dict(os.environ)
        env.pop("DOTENV_LOADED_CANARY", None)
        env.pop("MILODEX_SKIP_DOTENV", None)
        if skip_flag is not None:
            env["MILODEX_SKIP_DOTENV"] = skip_flag
        env["PYTHONPATH"] = f"{tmp_path}{os.pathsep}{src_dir}"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os, milodex.config; "
                "print(os.environ.get('DOTENV_LOADED_CANARY', '0'), end='')",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout == "1"

    def test_bootstrap_loads_dotenv_by_default(self, tmp_path: Path):
        assert self._bootstrap_calls_dotenv(tmp_path, skip_flag=None) is True

    def test_bootstrap_skips_dotenv_when_flag_set(self, tmp_path: Path):
        assert self._bootstrap_calls_dotenv(tmp_path, skip_flag="1") is False

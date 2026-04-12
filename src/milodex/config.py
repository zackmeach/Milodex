"""Shared configuration for Milodex.

Loads environment variables from .env and provides typed accessors
for credentials, trading mode, and file paths. Single source of truth —
no other module reads .env or os.environ for these values.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (if it exists)
load_dotenv()


def get_alpaca_credentials() -> tuple[str, str]:
    """Load ALPACA_API_KEY and ALPACA_SECRET_KEY from environment.

    Returns:
        Tuple of (api_key, secret_key).

    Raises:
        ValueError: If either key is missing or empty.
    """
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()

    if not api_key:
        raise ValueError(
            "ALPACA_API_KEY is not set. "
            "Copy .env.example to .env and fill in your Alpaca credentials."
        )
    if not secret_key:
        raise ValueError(
            "ALPACA_SECRET_KEY is not set. "
            "Copy .env.example to .env and fill in your Alpaca credentials."
        )
    return api_key, secret_key


def get_trading_mode() -> str:
    """Return 'paper' or 'live' from TRADING_MODE env var.

    Defaults to 'paper' if unset. Raises on invalid values.
    """
    mode = os.environ.get("TRADING_MODE", "paper").strip().lower()
    if mode not in ("paper", "live"):
        raise ValueError(
            f"TRADING_MODE must be 'paper' or 'live', got '{mode}'. "
            "Check your .env file."
        )
    return mode


def get_cache_dir() -> Path:
    """Return path for local market data cache.

    Default: {project_root}/market_cache/
    Override with MILODEX_CACHE_DIR env var.
    """
    override = os.environ.get("MILODEX_CACHE_DIR", "").strip()
    if override:
        return Path(override)

    # Walk up from this file to find project root (where pyproject.toml lives)
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current / "market_cache"
        current = current.parent

    # Fallback: relative to cwd
    return Path.cwd() / "market_cache"

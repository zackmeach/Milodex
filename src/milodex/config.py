"""Shared configuration for Milodex.

Loads environment variables from .env and provides typed accessors
for credentials, trading mode, and file paths. Single source of truth —
no other module reads .env or os.environ for these values.

Path resolution strategy
------------------------
There are three contexts in which these helpers run:

1. **Editable install** (``pip install -e .``): walks up from ``__file__`` to
   find ``pyproject.toml`` and returns repo-relative paths.  Used for all
   developer and CI workflows.

2. **PyInstaller frozen bundle** (``sys.frozen = True``): pyproject.toml is
   absent from the bundle.  Writable user data routes to
   ``%LOCALAPPDATA%\\Milodex\\`` (Windows); read-only bundled resources
   (QML, configs, fonts) are under ``sys._MEIPASS``.

3. **Env-var override**: explicit ``MILODEX_*`` environment variables win over
   both of the above.  Power users can redirect any path without rebuilding.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (if it exists)
load_dotenv()


def _localappdata_root() -> Path:
    """Return the Windows %LOCALAPPDATA% path, with a safe fallback.

    Uses the env var when available (the normal case on all supported
    Windows installations).  Falls back to the conventional path relative
    to home so the function never raises even on non-Windows platforms —
    frozen bundles are Windows-only in Phase 5, but keeping the code
    portable costs nothing.
    """
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        return Path(localappdata)
    # Fallback for environments where LOCALAPPDATA is unset (uncommon on
    # Windows; may happen in container / CI contexts).
    return Path.home() / "AppData" / "Local"


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
            f"TRADING_MODE must be 'paper' or 'live', got '{mode}'. Check your .env file."
        )
    return mode


def get_bundled_resource_dir() -> Path:
    """Return the root directory for read-only bundled resources.

    Bundled resources include ``configs/``, ``assets/fonts/``, and QML trees.
    The path differs by execution context:

    - **Frozen (PyInstaller --onedir):** ``sys._MEIPASS`` — the directory where
      PyInstaller extracts the bundle at runtime.  Data files declared in the
      spec's ``datas`` list are relative to this root.
    - **Editable install / source:** the repository root (found via the
      ``pyproject.toml`` walk), so ``get_bundled_resource_dir() / "configs"``
      resolves to the same ``configs/`` directory that the editable install
      uses.

    This function is intentionally for *read-only* resources.  Writable
    runtime state (event store, logs, locks) routes through :func:`get_data_dir`,
    :func:`get_logs_dir`, and :func:`get_locks_dir` instead.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys._MEIPASS to the extraction root; this is where
        # all data files listed in the spec's `datas` tuples land.
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]

    # Editable install: walk up to the repo root.
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent

    # Fallback (shouldn't be reached in normal usage).
    return Path.cwd()


def get_cache_dir() -> Path:
    """Return path for local market data cache.

    Default: ``{project_root}/market_cache/`` (editable) or
    ``%LOCALAPPDATA%\\Milodex\\market_cache\\`` (frozen bundle).
    Override with ``MILODEX_CACHE_DIR`` env var.
    """
    override = os.environ.get("MILODEX_CACHE_DIR", "").strip()
    if override:
        return Path(override)

    if getattr(sys, "frozen", False):
        # Frozen bundle: writable user data lives under %LOCALAPPDATA%\Milodex\.
        path = _localappdata_root() / "Milodex" / "market_cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # Walk up from this file to find project root (where pyproject.toml lives)
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current / "market_cache"
        current = current.parent

    # Fallback: relative to cwd
    return Path.cwd() / "market_cache"


def get_data_dir() -> Path:
    """Return path for local stateful app data.

    Default: ``{project_root}/data/`` (editable) or
    ``%LOCALAPPDATA%\\Milodex\\data\\`` (frozen bundle).
    Override with ``MILODEX_DATA_DIR`` env var.
    """
    override = os.environ.get("MILODEX_DATA_DIR", "").strip()
    if override:
        return Path(override)

    if getattr(sys, "frozen", False):
        path = _localappdata_root() / "Milodex" / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current / "data"
        current = current.parent

    return Path.cwd() / "data"


def get_logs_dir() -> Path:
    """Return path for local runtime logs and state files.

    Default: ``{project_root}/logs/`` (editable) or
    ``%LOCALAPPDATA%\\Milodex\\logs\\`` (frozen bundle).
    Override with ``MILODEX_LOG_DIR`` env var.
    """
    override = os.environ.get("MILODEX_LOG_DIR", "").strip()
    if override:
        return Path(override)

    if getattr(sys, "frozen", False):
        path = _localappdata_root() / "Milodex" / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current / "logs"
        current = current.parent

    return Path.cwd() / "logs"


def get_locks_dir() -> Path:
    """Return path for advisory lock files used for single-process serialization.

    Defaults to ``<data_dir>/locks`` so all durable Milodex-authoritative
    state stays under one root. Override with ``MILODEX_LOCKS_DIR``.
    """
    override = os.environ.get("MILODEX_LOCKS_DIR", "").strip()
    if override:
        return Path(override)
    return get_data_dir() / "locks"

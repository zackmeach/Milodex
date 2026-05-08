"""Tests for frozen-bundle path resolution in milodex.config.

Covers the PyInstaller sys.frozen=True branch for get_data_dir,
get_cache_dir, get_logs_dir, and get_bundled_resource_dir, as well as
the env-var override precedence rules.

Tests deliberately avoid any filesystem side-effects: monkeypatching
LOCALAPPDATA keeps paths pointing at fake directories, and the mkdir
calls in the frozen branches are exercised against the fake path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch  # noqa: F401 (used in patch.object calls throughout)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_config(monkeypatch):
    """Return a fresh import of milodex.config with the current monkeypatched
    sys / environment state applied.

    We reload rather than call the cached module so that the ``sys.frozen``
    checks evaluate against the monkeypatched state, not the import-time state.
    """
    import importlib

    import milodex.config as cfg_module

    importlib.reload(cfg_module)
    return cfg_module


# ---------------------------------------------------------------------------
# Frozen-mode: get_data_dir
# ---------------------------------------------------------------------------


def test_get_data_dir_frozen_uses_localappdata(monkeypatch, tmp_path):
    """Frozen bundle routes data dir to %LOCALAPPDATA%\\Milodex\\data\\."""
    fake_localappdata = str(tmp_path / "AppData" / "Local")
    monkeypatch.setenv("LOCALAPPDATA", fake_localappdata)
    # Clear any data-dir override so the frozen branch is exercised.
    monkeypatch.delenv("MILODEX_DATA_DIR", raising=False)

    with patch.object(sys, "frozen", True, create=True):
        with patch.object(sys, "_MEIPASS", str(tmp_path / "meipass"), create=True):
            import importlib

            import milodex.config as cfg

            importlib.reload(cfg)
            result = cfg.get_data_dir()

    assert result == Path(fake_localappdata) / "Milodex" / "data"
    assert result.exists()  # frozen branch calls mkdir


# ---------------------------------------------------------------------------
# Frozen-mode: get_cache_dir
# ---------------------------------------------------------------------------


def test_get_cache_dir_frozen_uses_localappdata(monkeypatch, tmp_path):
    fake_localappdata = str(tmp_path / "AppData" / "Local")
    monkeypatch.setenv("LOCALAPPDATA", fake_localappdata)
    monkeypatch.delenv("MILODEX_CACHE_DIR", raising=False)

    with patch.object(sys, "frozen", True, create=True):
        with patch.object(sys, "_MEIPASS", str(tmp_path / "meipass"), create=True):
            import importlib

            import milodex.config as cfg

            importlib.reload(cfg)
            result = cfg.get_cache_dir()

    assert result == Path(fake_localappdata) / "Milodex" / "market_cache"
    assert result.exists()


# ---------------------------------------------------------------------------
# Frozen-mode: get_logs_dir
# ---------------------------------------------------------------------------


def test_get_logs_dir_frozen_uses_localappdata(monkeypatch, tmp_path):
    fake_localappdata = str(tmp_path / "AppData" / "Local")
    monkeypatch.setenv("LOCALAPPDATA", fake_localappdata)
    monkeypatch.delenv("MILODEX_LOG_DIR", raising=False)

    with patch.object(sys, "frozen", True, create=True):
        with patch.object(sys, "_MEIPASS", str(tmp_path / "meipass"), create=True):
            import importlib

            import milodex.config as cfg

            importlib.reload(cfg)
            result = cfg.get_logs_dir()

    assert result == Path(fake_localappdata) / "Milodex" / "logs"
    assert result.exists()


# ---------------------------------------------------------------------------
# Frozen-mode: get_bundled_resource_dir
# ---------------------------------------------------------------------------


def test_get_bundled_resource_dir_frozen_returns_meipass(monkeypatch, tmp_path):
    fake_meipass = str(tmp_path / "meipass")

    with patch.object(sys, "frozen", True, create=True):
        with patch.object(sys, "_MEIPASS", fake_meipass, create=True):
            import importlib

            import milodex.config as cfg

            importlib.reload(cfg)
            result = cfg.get_bundled_resource_dir()

    assert result == Path(fake_meipass)


# ---------------------------------------------------------------------------
# Non-frozen mode: get_bundled_resource_dir returns repo root
# ---------------------------------------------------------------------------


def test_get_bundled_resource_dir_non_frozen_returns_repo_root(monkeypatch):
    # Ensure sys.frozen is absent (the normal editable-install state).
    # patch.object with create=True sets the attribute; after the with-block
    # the attribute is removed if it didn't exist before.
    if hasattr(sys, "frozen"):
        monkeypatch.delattr(sys, "frozen")

    import importlib

    import milodex.config as cfg

    importlib.reload(cfg)
    result = cfg.get_bundled_resource_dir()

    # In an editable install the function walks up to the directory that
    # contains pyproject.toml — the repo root.
    assert (result / "pyproject.toml").exists(), (
        f"get_bundled_resource_dir() returned {result!r} which does not "
        "contain pyproject.toml — expected the repo root."
    )


# ---------------------------------------------------------------------------
# Env-var override wins over frozen mode
# ---------------------------------------------------------------------------


def test_get_data_dir_env_var_wins_over_frozen(monkeypatch, tmp_path):
    """MILODEX_DATA_DIR takes precedence even when sys.frozen is True."""
    custom = str(tmp_path / "custom_data")
    monkeypatch.setenv("MILODEX_DATA_DIR", custom)

    with patch.object(sys, "frozen", True, create=True):
        with patch.object(sys, "_MEIPASS", str(tmp_path / "meipass"), create=True):
            import importlib

            import milodex.config as cfg

            importlib.reload(cfg)
            result = cfg.get_data_dir()

    assert result == Path(custom)


def test_get_cache_dir_env_var_wins_over_frozen(monkeypatch, tmp_path):
    custom = str(tmp_path / "custom_cache")
    monkeypatch.setenv("MILODEX_CACHE_DIR", custom)

    with patch.object(sys, "frozen", True, create=True):
        with patch.object(sys, "_MEIPASS", str(tmp_path / "meipass"), create=True):
            import importlib

            import milodex.config as cfg

            importlib.reload(cfg)
            result = cfg.get_cache_dir()

    assert result == Path(custom)


def test_get_logs_dir_env_var_wins_over_frozen(monkeypatch, tmp_path):
    custom = str(tmp_path / "custom_logs")
    monkeypatch.setenv("MILODEX_LOG_DIR", custom)

    with patch.object(sys, "frozen", True, create=True):
        with patch.object(sys, "_MEIPASS", str(tmp_path / "meipass"), create=True):
            import importlib

            import milodex.config as cfg

            importlib.reload(cfg)
            result = cfg.get_logs_dir()

    assert result == Path(custom)


# ---------------------------------------------------------------------------
# get_locks_dir derives from get_data_dir (frozen → correct subtree)
# ---------------------------------------------------------------------------


def test_get_locks_dir_frozen_derives_from_data_dir(monkeypatch, tmp_path):
    fake_localappdata = str(tmp_path / "AppData" / "Local")
    monkeypatch.setenv("LOCALAPPDATA", fake_localappdata)
    monkeypatch.delenv("MILODEX_DATA_DIR", raising=False)
    monkeypatch.delenv("MILODEX_LOCKS_DIR", raising=False)

    with patch.object(sys, "frozen", True, create=True):
        with patch.object(sys, "_MEIPASS", str(tmp_path / "meipass"), create=True):
            import importlib

            import milodex.config as cfg

            importlib.reload(cfg)
            result = cfg.get_locks_dir()

    expected = Path(fake_localappdata) / "Milodex" / "data" / "locks"
    assert result == expected

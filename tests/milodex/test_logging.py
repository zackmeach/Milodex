# tests/milodex/test_logging.py
"""Tests for the logging bootstrap module."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from milodex._logging import install_file_handler


class TestInstallFileHandler:
    def test_creates_log_dir_if_absent(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "nested" / "logs"
        assert not log_dir.exists()
        handler = install_file_handler(log_dir)
        try:
            assert log_dir.is_dir()
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_log_records_land_in_file(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        handler = install_file_handler(log_dir)
        try:
            logger = logging.getLogger("milodex.test_logging_records")
            logger.setLevel(logging.DEBUG)
            logger.debug("sentinel-message-xyz")
            handler.flush()
            log_text = (log_dir / "milodex.log").read_text(encoding="utf-8")
            assert "sentinel-message-xyz" in log_text
        finally:
            # Clean up: remove this handler so it doesn't bleed into other tests.
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_log_format_includes_timestamp_level_name(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        handler = install_file_handler(log_dir)
        try:
            logger = logging.getLogger("milodex.test_logging_format")
            logger.setLevel(logging.WARNING)
            logger.warning("format-check-message")
            handler.flush()
            log_text = (log_dir / "milodex.log").read_text(encoding="utf-8")
            # ISO8601-ish timestamp present (e.g. 2026-05-07T...)
            assert "T" in log_text  # date/time separator
            assert "WARNING" in log_text
            assert "milodex.test_logging_format" in log_text
            assert "format-check-message" in log_text
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_idempotent_no_duplicate_handlers(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        root = logging.getLogger()
        before = len(root.handlers)
        h1 = install_file_handler(log_dir)
        h2 = install_file_handler(log_dir)
        try:
            assert h1 is h2, "Second call should return the same handler, not create a new one"
            assert len(root.handlers) == before + 1
        finally:
            root.removeHandler(h1)
            h1.close()

    def test_handler_is_rotating(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        handler = install_file_handler(log_dir)
        try:
            assert isinstance(handler, logging.handlers.RotatingFileHandler)
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()


class TestNoAutoInstallOnImport:
    def test_importing_logging_module_does_not_add_handler(self) -> None:
        """Importing milodex._logging must not install a handler as a side effect.

        install_file_handler() is the only public API and must be called
        explicitly.  Module-level code in _logging.py must not call it.
        """
        import importlib

        root = logging.getLogger()
        before = list(root.handlers)

        # Force a fresh evaluation of the module's top-level code.
        import milodex._logging as mod

        importlib.reload(mod)

        after = list(root.handlers)
        # No new handlers should appear from the reload.
        new_handlers = [h for h in after if h not in before]
        assert new_handlers == [], (
            f"Reloading milodex._logging added unexpected handlers: {new_handlers}. "
            "Module-level code must not call install_file_handler()."
        )

    def test_real_logs_dir_unmodified_by_test_run(self) -> None:
        """The project logs/ directory must not receive milodex.log during tests.

        The conftest autouse fixture redirects MILODEX_LOG_DIR to tmp_path
        for every test, so even when main() is invoked in CLI tests the
        handler writes to a temp directory, not the real logs/.
        """
        from milodex.config import get_logs_dir

        # MILODEX_LOG_DIR is set to tmp_path by the conftest autouse fixture.
        # get_logs_dir() should therefore return a tmp path, not the real logs/.
        logs_dir = get_logs_dir()
        real_logs = Path(__file__).resolve().parent.parent.parent / "logs"
        assert logs_dir != real_logs, (
            "MILODEX_LOG_DIR was not redirected by conftest — "
            "tests may write to the real logs/ directory."
        )
        # Additionally confirm milodex.log does not exist in the real logs dir.
        real_log_file = real_logs / "milodex.log"
        assert not real_log_file.exists(), (
            f"Real log file {real_log_file} exists — a test leaked outside tmp_path."
        )

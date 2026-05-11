"""Logging bootstrap for the Milodex CLI.

Call ``install_file_handler()`` once at CLI startup to route all milodex
loggers to a process-scoped rotating file under ``logs/``.  Tests and library
imports never call this, so no FileHandler is installed outside the CLI.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 5


def _resolve_level() -> int:
    """Return the effective log level, respecting MILODEX_LOG_LEVEL if set.

    Set ``MILODEX_LOG_LEVEL=DEBUG`` in the environment to capture all
    third-party library debug output (urllib3, alpaca-py, etc.).
    Defaults to INFO to avoid gigabyte log files from library noise.
    """
    raw = os.environ.get("MILODEX_LOG_LEVEL", "").strip().upper()
    if raw:
        numeric = getattr(logging, raw, None)
        if isinstance(numeric, int):
            return numeric
    return logging.INFO


def install_file_handler(log_dir: Path, *, level: int | None = None) -> logging.Handler:
    """Install a process-scoped RotatingFileHandler on the root logger.

    Idempotent: if a FileHandler pointing at the same path is already attached
    to the root logger, it is returned as-is without adding a duplicate.

    Args:
        log_dir: Directory that will contain ``milodex-<pid>.log``.  Created if absent.
        level: Minimum level forwarded to the file.  Defaults to INFO (or the
               value of the ``MILODEX_LOG_LEVEL`` env var if set).  Pass
               ``logging.DEBUG`` explicitly to capture all library internals.

    Returns:
        The installed (or pre-existing) handler instance.
    """
    if level is None:
        level = _resolve_level()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"milodex-{os.getpid()}.log"

    root = logging.getLogger()

    # Idempotency guard: don'''t stack duplicate handlers across hot-reloads.
    for existing in root.handlers:
        if isinstance(existing, logging.FileHandler) and Path(existing.baseFilename) == log_path:
            return existing

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    handler.setFormatter(formatter)

    # Lower the root logger'''s effective level if needed so records at `level` flow.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    root.addHandler(handler)
    return handler

"""Logging bootstrap for the Milodex CLI.

Call ``install_file_handler()`` once at CLI startup to route all milodex
loggers to a rotating file under ``logs/milodex.log``.  Tests and library
imports never call this, so no FileHandler is installed outside the CLI.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 5


def install_file_handler(log_dir: Path, *, level: int = logging.DEBUG) -> logging.Handler:
    """Install a RotatingFileHandler on the root logger targeting *log_dir*/milodex.log.

    Idempotent: if a FileHandler pointing at the same path is already attached
    to the root logger, it is returned as-is without adding a duplicate.

    Args:
        log_dir: Directory that will contain ``milodex.log``.  Created if absent.
        level: Minimum level forwarded to the file.  Defaults to DEBUG so that
               all operational noise (429 retries, cache hits, etc.) is captured.

    Returns:
        The installed (or pre-existing) handler instance.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "milodex.log"

    root = logging.getLogger()

    # Idempotency guard: don't stack duplicate handlers across hot-reloads.
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

    # Lower the root logger's effective level if needed so DEBUG records flow.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    root.addHandler(handler)
    return handler

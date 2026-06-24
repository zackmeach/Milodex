"""Tests for :mod:`milodex.gui._db_logging` (backlog D1).

Covers:
1. ``log_db_read_error`` rate-limiting:
   - two calls for the SAME site in quick succession → exactly ONE warning record
   - two calls for DIFFERENT sites → two warning records
2. Empty-return contract is unchanged: a real read helper still returns its
   empty container when its query raises ``sqlite3.Error`` (missing table), and
   it emits exactly one warning while doing so.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from milodex.gui import _db_logging
from milodex.gui._db_logging import log_db_read_error


def _reset_rate_limit() -> None:
    _db_logging._last_warn.clear()


def test_same_site_twice_warns_once(caplog) -> None:
    _reset_rate_limit()
    err = sqlite3.Error("boom")
    with caplog.at_level(logging.WARNING, logger=_db_logging.logger.name):
        log_db_read_error("k", err)
        log_db_read_error("k", err)
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    assert "k" in records[0].getMessage()


def test_different_sites_warn_separately(caplog) -> None:
    _reset_rate_limit()
    err = sqlite3.Error("boom")
    with caplog.at_level(logging.WARNING, logger=_db_logging.logger.name):
        log_db_read_error("site_a", err)
        log_db_read_error("site_b", err)
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 2


def test_real_helper_returns_empty_and_warns_on_sqlite_error(
    tmp_path: Path, caplog
) -> None:
    """A real read helper still returns its empty container on sqlite3.Error
    (missing table) and emits exactly one rate-limited warning."""
    _reset_rate_limit()
    from milodex.gui.query_helpers import _latest_promotions

    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE other(a INTEGER)")  # no `promotions` table
    conn.commit()
    conn.row_factory = sqlite3.Row

    with caplog.at_level(logging.WARNING, logger=_db_logging.logger.name):
        try:
            result = _latest_promotions(conn)
        finally:
            conn.close()

    assert result == {}
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    assert "query_helpers._latest_promotions" in records[0].getMessage()

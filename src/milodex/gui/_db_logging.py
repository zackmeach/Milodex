"""Rate-limited logging for swallowed SQLite read errors (backlog D1).

GUI read helpers swallow ``sqlite3.Error`` and return an empty container so a
locked/corrupt/schema-drifted DB renders as "no data" rather than crashing the
polling read models. That silence is the problem: a real DB fault is
indistinguishable from a genuinely empty store. These helpers are called from
polling read models on a ~30s tick across several models, so an unconditional
warning would produce a per-tick log storm.

``log_db_read_error`` makes a real fault visible without the storm: at most one
WARNING per swallow-site per minute.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Per-site monotonic timestamp of the last emitted warning.
_last_warn: dict[str, float] = {}
_WARN_INTERVAL_S = 60.0


def log_db_read_error(site: str, exc: Exception) -> None:
    """Warn about a swallowed sqlite read error at most once per ``site`` per
    minute. Read helpers swallow sqlite errors and return empty; this makes a
    real DB fault visible in logs without a per-poll-tick storm.
    # ponytail: rate-limit; upgrade to fault/recovery transition tracking if
    # diagnostics ever need explicit recovery lines.
    """
    now = time.monotonic()
    last = _last_warn.get(site)
    if last is not None and (now - last) < _WARN_INTERVAL_S:
        return
    _last_warn[site] = now
    logger.warning("Swallowed sqlite read error at %s: %s", site, exc)

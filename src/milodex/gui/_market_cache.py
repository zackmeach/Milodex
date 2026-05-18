"""Shared helpers for reading the Parquet market cache.

Extracted here so both :mod:`milodex.gui.performance_state` and
:mod:`milodex.gui.market_tape_state` can import without circular
dependencies.
"""

from __future__ import annotations

import re
from pathlib import Path


def _latest_cache_version(cache_dir: Path) -> str | None:
    """Return the highest ``vN`` directory name inside ``cache_dir``.

    Ignores directories that do not match the ``vN`` pattern (e.g. ``1Day``).
    Returns ``None`` if no ``vN`` directory exists.
    """
    pattern = re.compile(r"^v(\d+)$")
    best_n: int | None = None
    best_name: str | None = None
    try:
        for entry in cache_dir.iterdir():
            if entry.is_dir():
                m = pattern.match(entry.name)
                if m:
                    n = int(m.group(1))
                    if best_n is None or n > best_n:
                        best_n = n
                        best_name = entry.name
    except OSError:
        return None
    return best_name

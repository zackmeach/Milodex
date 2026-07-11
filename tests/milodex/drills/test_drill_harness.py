"""CI guard for the M4 fault-injection drill harness (``scripts/drills/``).

The harness is standalone-runnable; this wrapper just smoke-runs the fast,
offline cells inside pytest so a refactor that silently breaks a drill (a
renamed operator message, a changed exit code, a dropped durable field) is
caught by the suite rather than only by a manual harness run.

Excluded here (still exercised by the standalone harness):

* ``locked_db`` — holds the 30s SQLite ``busy_timeout`` (too slow for CI).
* ``broker_outage`` / ``kill_switch_trip_reset`` — make one outbound,
  unauthenticated, bogus-credential Alpaca request; network-dependent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The drill package lives under ``scripts/`` (not the ``src`` layout pytest puts
# on the path), so make the repo root importable.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.drills.cells import (  # noqa: E402
    CELL_REGISTRY,
    NETWORK_CELLS,
    SLOW_CELLS,
)

_FAST_OFFLINE_CELLS = sorted(set(CELL_REGISTRY) - SLOW_CELLS - NETWORK_CELLS)


def test_fast_offline_cells_cover_the_expected_set() -> None:
    """Guard the registry: the CI set is exactly the fast, offline cells."""
    assert _FAST_OFFLINE_CELLS == [
        "clean_room",
        "corrupt_db",
        "dead_runner",
        "stale_market_data",
        "wedged_stop",
    ]


@pytest.mark.parametrize("cell_name", _FAST_OFFLINE_CELLS)
def test_drill_cell_passes(cell_name: str) -> None:
    """Each fast offline drill cell injects its fault and asserts green."""
    result = CELL_REGISTRY[cell_name]()  # type: ignore[operator]
    assert result.status == "PASS", (
        f"drill cell {cell_name!r} did not pass ({result.status}):\n{result.detail}\n"
        f"--- operator output ---\n{result.operator_output}"
    )

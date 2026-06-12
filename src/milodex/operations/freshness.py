"""Shared data-freshness threshold for staleness checks."""

from __future__ import annotations

#: Bars older than this many hours are considered stale. Consumed by the CLI
#: trust report (``cli/commands/report.py:_data_freshness``) and the Bench
#: workflow-readiness check (``commands/bench.py:_data_freshness_issue``).
DATA_FRESHNESS_STALE_HOURS: float = 24.0

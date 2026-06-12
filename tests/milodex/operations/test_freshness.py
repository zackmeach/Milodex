"""Contract test: both freshness consumers share the one threshold (P3-02)."""

from __future__ import annotations

from milodex.cli.commands import report as report_module
from milodex.commands import bench as bench_module
from milodex.operations.freshness import DATA_FRESHNESS_STALE_HOURS


def test_bench_and_report_share_the_freshness_threshold():
    assert bench_module._DATA_FRESHNESS_STALE_HOURS is DATA_FRESHNESS_STALE_HOURS
    assert report_module.DATA_FRESHNESS_STALE_HOURS is DATA_FRESHNESS_STALE_HOURS
    assert DATA_FRESHNESS_STALE_HOURS == 24.0

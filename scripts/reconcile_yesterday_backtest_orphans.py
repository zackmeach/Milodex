"""One-off cleanup of three orphaned ``backtest_runs`` rows from 2026-05-06.

These three walk-forward backtest runs started on 2026-05-06 and never wrote
their close-out — their ``status`` is still ``'running'`` and ``ended_at`` is
NULL more than 24 hours later:

    446aff19-ba40-4207-b502-ff286a67f0e9
        momentum.daily.tsmom.curated_largecap.v1, 16:50:23
        (superseded by a completed 17:05 run)
    52833d8d-5caf-4905-bdc1-ceeb0bffae80
        momentum.daily.dual_absolute.gem_weekly.v1, 17:01:15
    771e08cd-7d2c-4a38-aac9-47415327abda
        breakout.daily.donchian_20_10.sector_etfs.v1, 17:02:24

Forensic signature: ``metadata_json`` for all three contains only
``{"walk_forward": true, "windows_planned": 4}`` (sparse planning state),
while completed walk-forward runs have rich result keys
(``oos_aggregate``, ``windows``, ``stability``, ``step_days``, etc.). They
died before fold-1 ever produced output — almost certainly victims of the
parquet 0-byte cache bug fixed in PR #44 (now also defended by atomic
``ParquetCache.write()``).

The accompanying code change (this PR) adds startup reconciliation to
``BacktestEngine.run`` and ``run_walk_forward``, so any future engine that
dies mid-run gets cleaned up the next time the same strategy backtests.
But these three rows pre-date the fix and need a one-shot sweep.

Synthetic ``ended_at = started_at + 1s`` matches PR #44's convention for
yesterday's regime-runner orphan: we don't know when the process actually
died, and using "now" would mislead reports into showing a 24+ hour run
duration.

Usage:
    python scripts/reconcile_yesterday_backtest_orphans.py
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from milodex.core.event_store import EventStore

DB_PATH = Path("data/milodex.db")

ORPHAN_RUN_IDS = (
    "446aff19-ba40-4207-b502-ff286a67f0e9",
    "52833d8d-5caf-4905-bdc1-ceeb0bffae80",
    "771e08cd-7d2c-4a38-aac9-47415327abda",
)


def main() -> int:
    store = EventStore(DB_PATH)

    targets = []
    for run_id in ORPHAN_RUN_IDS:
        run = store.get_backtest_run(run_id)
        if run is None:
            print(f"SKIP {run_id}: no such row")
            continue
        if run.status != "running" or run.ended_at is not None:
            print(
                f"SKIP {run_id}: already terminal (status={run.status!r}, ended_at={run.ended_at})"
            )
            continue
        targets.append(run)

    if not targets:
        print("Nothing to reconcile.")
        return 0

    total_reconciled = 0
    for run in targets:
        synthetic_ended_at = run.started_at + timedelta(seconds=1)
        # Per-strategy_id scope, matching the new method's contract. Since
        # each of these three is the ONLY orphan for its strategy_id (we
        # verified this in the PR forensic write-up), each call closes
        # exactly the row we want.
        reconciled = store.reconcile_orphan_backtest_runs(
            strategy_id=run.strategy_id,
            ended_at=synthetic_ended_at,
        )
        total_reconciled += reconciled
        print(
            f"OK   {run.run_id} ({run.strategy_id}): "
            f"started_at={run.started_at.isoformat()} -> "
            f"ended_at={synthetic_ended_at.isoformat()} "
            f"(rows reconciled in this call: {reconciled})"
        )

    print(f"\nTotal rows reconciled: {total_reconciled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

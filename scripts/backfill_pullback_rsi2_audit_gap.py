"""Audit-trail backfill: synthetic demotion event for pullback_rsi2.

Closes the audit gap discovered on 2026-05-07: the strategy
``meanrev.daily.pullback_rsi2.curated_largecap.v1`` was demoted from
``micro_live`` back to ``backtest`` by a direct YAML edit rather than via
``milodex promotion demote``, leaving no event in the ``promotions`` table.

This script inserts a single synthetic demotion row (``approved_by='audit_backfill'``)
with a plausible timestamp and a notes field describing the discovery context.
It is safe to run multiple times — it is idempotent.

Usage::

    python scripts/backfill_pullback_rsi2_audit_gap.py [--db PATH]
    python scripts/backfill_pullback_rsi2_audit_gap.py --verify-only
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STRATEGY_ID = "meanrev.daily.pullback_rsi2.curated_largecap.v1"

# Synthetic timestamp: during Phase 4 close-out, chosen as the most plausible
# date for the operator stage-correction.  Must fall strictly between
# 2026-04-22T16:45:40+00:00 (the micro_live promotion) and
# 2026-05-07T13:34:41+00:00 (the backtest→paper re-promotion).
SYNTHETIC_RECORDED_AT = datetime(2026, 5, 6, 20, 0, 0, tzinfo=UTC)

NOTES = (
    "Audit-trail backfill recorded 2026-05-07. "
    "Original promotion (2026-04-22) to micro_live was made on backtest run "
    "2ccea042-d869-43ef-aa13-ae49a9483ec4 (Sharpe 1.022 / 1057 trades) using "
    "the pre-Phase-4 universe (pre-survivorship-correction, pre-dividend-adjustment). "
    "The corrected baseline shows the honest Sharpe is 0.732 (~28% deflation, "
    "run 5210be26-5d60-4ad5-8834-7efc162cb391). "
    "Between 2026-04-22 and 2026-05-07 the YAML's `stage:` line was direct-edited "
    "back to `backtest` without invocation of `milodex promotion demote`, leaving "
    "no event in this table. "
    "This synthetic event closes the audit hole forensically without claiming to "
    "know the exact moment of operator decision. "
    "The 2026-05-07 backtest→paper re-promotion supersedes this entry on the "
    "corrected universe."
)


def run_backfill(db_path: Path) -> dict:
    """Insert the synthetic demotion event and return a result dict.

    Returns a dict with keys:
      - ``inserted``: bool — True if a new row was inserted, False if already present.
      - ``backfill_row``: the promotion row after the operation.
      - ``all_promotions``: list of all promotions for STRATEGY_ID in chronological order.
    """
    # Import here so the script can be imported into tests without requiring
    # the full package to be installed (the path injection in test_* handles that).
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from milodex.core.event_store import EventStore, PromotionEvent

    store = EventStore(db_path)

    # ── Idempotency check ────────────────────────────────────────────────────
    existing = [
        p
        for p in store.list_promotions_for_strategy(STRATEGY_ID)
        if p.approved_by == "audit_backfill"
    ]
    if existing:
        backfill_row = existing[0]
        all_promotions = list(
            reversed(store.list_promotions_for_strategy(STRATEGY_ID))
        )
        return {"inserted": False, "backfill_row": backfill_row, "all_promotions": all_promotions}

    # ── Resolve reverses_event_id ────────────────────────────────────────────
    # The row we're logically reversing is the 2026-04-22 paper→micro_live promotion.
    micro_live_event = next(
        (
            p
            for p in store.list_promotions_for_strategy(STRATEGY_ID)
            if p.from_stage == "paper" and p.to_stage == "micro_live"
        ),
        None,
    )
    reverses_event_id = micro_live_event.id if micro_live_event is not None else None

    # ── Insert ───────────────────────────────────────────────────────────────
    event = PromotionEvent(
        strategy_id=STRATEGY_ID,
        from_stage="micro_live",
        to_stage="backtest",
        promotion_type="demotion",
        approved_by="audit_backfill",
        recorded_at=SYNTHETIC_RECORDED_AT,
        backtest_run_id=None,
        sharpe_ratio=None,
        max_drawdown_pct=None,
        trade_count=None,
        notes=NOTES,
        manifest_id=None,
        reverses_event_id=reverses_event_id,
        evidence_json=None,
    )
    new_id = store.append_promotion(event)

    backfill_row = store.get_promotion(new_id)
    all_promotions = list(reversed(store.list_promotions_for_strategy(STRATEGY_ID)))

    return {"inserted": True, "backfill_row": backfill_row, "all_promotions": all_promotions}


def _print_result(result: dict) -> None:
    if result["inserted"]:
        print("Inserted synthetic demotion event.")
    else:
        print("Row already present — no action taken (idempotent run).")

    row = result["backfill_row"]
    print(f"\nBackfill row  id={row.id}  recorded_at={row.recorded_at}")
    print(f"  strategy_id       : {row.strategy_id}")
    print(f"  from_stage        : {row.from_stage}")
    print(f"  to_stage          : {row.to_stage}")
    print(f"  promotion_type    : {row.promotion_type}")
    print(f"  approved_by       : {row.approved_by}")
    print(f"  reverses_event_id : {row.reverses_event_id}")
    print(f"  notes             : {row.notes[:80]}...")

    print(f"\nAll promotions for {STRATEGY_ID} (chronological):")
    for p in result["all_promotions"]:
        rev = f"  reverses={p.reverses_event_id}" if p.reverses_event_id else ""
        print(
            f"  id={p.id:>3}  {p.recorded_at}  "
            f"{p.from_stage} -> {p.to_stage}  by={p.approved_by}{rev}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "data" / "milodex.db",
        help="Path to milodex.db (default: data/milodex.db relative to repo root)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Print current promotions without inserting anything",
    )
    args = parser.parse_args()

    if args.verify_only:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from milodex.core.event_store import EventStore

        store = EventStore(args.db)
        promotions = list(reversed(store.list_promotions_for_strategy(STRATEGY_ID)))
        print(f"Promotions for {STRATEGY_ID} (chronological):")
        for p in promotions:
            rev = f"  reverses={p.reverses_event_id}" if p.reverses_event_id else ""
            print(
                f"  id={p.id:>3}  {p.recorded_at}  "
                f"{p.from_stage} -> {p.to_stage}  by={p.approved_by}{rev}"
            )
        return

    result = run_backfill(args.db)
    _print_result(result)


if __name__ == "__main__":
    main()

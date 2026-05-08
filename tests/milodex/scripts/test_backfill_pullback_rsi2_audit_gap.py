"""Tests for scripts/backfill_pullback_rsi2_audit_gap.py.

Covers:
- A fresh store pre-seeded with the original 4/22 promotion events receives
  exactly one new demotion row on the first call to run_backfill().
- The inserted row carries the correct field values (from_stage, to_stage,
  promotion_type, approved_by, reverses_event_id).
- Calling run_backfill() a second time does not insert a duplicate (idempotency).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.core.event_store import EventStore, PromotionEvent

# Make the scripts/ directory importable regardless of CWD.
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from backfill_pullback_rsi2_audit_gap import STRATEGY_ID, run_backfill  # noqa: E402

# ── Fixtures ─────────────────────────────────────────────────────────────────

_ORIGINAL_BACKTEST_TO_PAPER = PromotionEvent(
    strategy_id=STRATEGY_ID,
    from_stage="backtest",
    to_stage="paper",
    promotion_type="statistical",
    approved_by="owner",
    recorded_at=datetime(2026, 4, 22, 16, 44, 14, tzinfo=UTC),
    sharpe_ratio=1.0222127027450525,
    trade_count=1057,
    backtest_run_id="2ccea042-d869-43ef-aa13-ae49a9483ec4",
)

_ORIGINAL_PAPER_TO_MICRO_LIVE = PromotionEvent(
    strategy_id=STRATEGY_ID,
    from_stage="paper",
    to_stage="micro_live",
    promotion_type="statistical",
    approved_by="owner",
    recorded_at=datetime(2026, 4, 22, 16, 45, 39, tzinfo=UTC),
    sharpe_ratio=1.0222127027450525,
    trade_count=1057,
    backtest_run_id="2ccea042-d869-43ef-aa13-ae49a9483ec4",
)


@pytest.fixture()
def seeded_store(tmp_path: Path) -> tuple[EventStore, int]:
    """Return an EventStore pre-seeded with the two original 4/22 promotions.

    The second return value is the id of the paper→micro_live event, which
    the backfill script should reference as ``reverses_event_id``.
    """
    store = EventStore(tmp_path / "milodex.db")
    store.append_promotion(_ORIGINAL_BACKTEST_TO_PAPER)
    micro_live_id = store.append_promotion(_ORIGINAL_PAPER_TO_MICRO_LIVE)
    return store, micro_live_id


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestBackfillInsert:
    def test_inserts_exactly_one_row(self, seeded_store: tuple[EventStore, int]) -> None:
        store, _ = seeded_store
        db_path = store._path

        before_count = len(store.list_promotions_for_strategy(STRATEGY_ID))
        run_backfill(db_path)
        after_count = len(store.list_promotions_for_strategy(STRATEGY_ID))

        assert after_count == before_count + 1

    def test_inserted_row_fields(self, seeded_store: tuple[EventStore, int]) -> None:
        store, micro_live_id = seeded_store
        db_path = store._path

        result = run_backfill(db_path)

        assert result["inserted"] is True
        row = result["backfill_row"]

        assert row.strategy_id == STRATEGY_ID
        assert row.from_stage == "micro_live"
        assert row.to_stage == "backtest"
        assert row.promotion_type == "demotion"
        assert row.approved_by == "audit_backfill"
        assert row.reverses_event_id == micro_live_id

        # NULL fields as specified.
        assert row.backtest_run_id is None
        assert row.sharpe_ratio is None
        assert row.trade_count is None
        assert row.max_drawdown_pct is None
        assert row.evidence_json is None
        assert row.manifest_id is None

        # Notes must mention the key context phrases.
        assert row.notes is not None
        assert "audit_backfill" in row.notes or "Audit-trail backfill" in row.notes
        assert "2ccea042" in row.notes

    def test_chronological_order_has_four_entries(
        self, seeded_store: tuple[EventStore, int]
    ) -> None:
        store, _ = seeded_store
        db_path = store._path

        run_backfill(db_path)
        run_backfill(db_path)  # second call — idempotency check
        # list_promotions_for_strategy returns newest-first; reverse for chronological.
        promos = list(reversed(store.list_promotions_for_strategy(STRATEGY_ID)))

        # Pre-seed has 2 rows; backfill adds 1; second call adds 0.
        assert len(promos) == 3

        stages = [(p.from_stage, p.to_stage) for p in promos]
        assert stages == [
            ("backtest", "paper"),
            ("paper", "micro_live"),
            ("micro_live", "backtest"),
        ]


class TestIdempotency:
    def test_second_run_inserts_nothing(self, seeded_store: tuple[EventStore, int]) -> None:
        store, _ = seeded_store
        db_path = store._path

        first = run_backfill(db_path)
        second = run_backfill(db_path)

        assert first["inserted"] is True
        assert second["inserted"] is False

    def test_row_count_unchanged_on_second_run(self, seeded_store: tuple[EventStore, int]) -> None:
        store, _ = seeded_store
        db_path = store._path

        run_backfill(db_path)
        count_after_first = len(store.list_promotions_for_strategy(STRATEGY_ID))

        run_backfill(db_path)
        count_after_second = len(store.list_promotions_for_strategy(STRATEGY_ID))

        assert count_after_first == count_after_second

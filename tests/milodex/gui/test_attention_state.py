"""Tests for :class:`milodex.gui.attention_state.AttentionState`.

TDD structure per spec §4/§5:
1. Fixture builders + seeders.
2. Pure-logic tests: _compute_underperforming, needsReview classifiers a/b/c.
3. _query_attention tests (no Qt).
4. Relationship test: (c) ⊆ underperforming.
5. Standard scaffold tests: initial-loading, refresh, missing DB, error preserves
   last-known, concurrent-kick drops, stop-drains (corrected delayed-timer form),
   read-only connection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# PySide6 availability guard
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QCoreApplication, QThreadPool  # noqa: F401
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping Qt-aware AttentionState tests",
)

# ---------------------------------------------------------------------------
# Fixture DB builder
# ---------------------------------------------------------------------------


def _create_fixture_db(path: Path) -> None:
    """Create a minimal SQLite DB with all tables required by AttentionState."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT,
            strategy_id TEXT,
            from_stage TEXT,
            to_stage TEXT,
            promotion_type TEXT,
            approved_by TEXT,
            backtest_run_id TEXT,
            sharpe_ratio REAL,
            max_drawdown_pct REAL,
            trade_count INTEGER,
            notes TEXT,
            manifest_id TEXT,
            reverses_event_id TEXT,
            evidence_json TEXT
        );

        CREATE TABLE backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            strategy_id TEXT,
            config_path TEXT,
            config_hash TEXT,
            start_date TEXT,
            end_date TEXT,
            started_at TEXT,
            ended_at TEXT,
            status TEXT,
            slippage_pct REAL,
            commission_per_trade REAL,
            metadata_json TEXT
        );

        CREATE TABLE strategy_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            strategy_id TEXT,
            started_at TEXT,
            ended_at TEXT,
            exit_reason TEXT,
            metadata_json TEXT
        );

        CREATE TABLE strategy_manifests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT,
            stage TEXT,
            config_hash TEXT,
            config_json TEXT,
            config_path TEXT,
            frozen_at TEXT,
            frozen_by TEXT
        );

        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            explanation_id TEXT,
            recorded_at TEXT,
            status TEXT,
            source TEXT,
            symbol TEXT,
            side TEXT,
            quantity REAL,
            strategy_name TEXT,
            strategy_stage TEXT,
            broker_order_id TEXT,
            broker_status TEXT,
            estimated_order_value REAL,
            session_id TEXT,
            backtest_run_id TEXT
        );

        CREATE TABLE explanations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT,
            decision_type TEXT,
            status TEXT,
            strategy_name TEXT,
            strategy_stage TEXT,
            symbol TEXT,
            session_id TEXT
        );
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Seeders
# ---------------------------------------------------------------------------


def _seed_promotion(
    db: Path,
    *,
    strategy_id: str,
    to_stage: str,
    promotion_type: str = "statistical",
    sharpe_ratio: float | None = None,
    max_drawdown_pct: float | None = None,
    trade_count: int | None = None,
    backtest_run_id: str | None = None,
    recorded_at: str | None = None,
) -> None:
    if recorded_at is None:
        recorded_at = datetime.now(tz=UTC).isoformat()
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO promotions
            (recorded_at, strategy_id, from_stage, to_stage, promotion_type,
             backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recorded_at,
            strategy_id,
            "backtest",
            to_stage,
            promotion_type,
            backtest_run_id,
            sharpe_ratio,
            max_drawdown_pct,
            trade_count,
        ),
    )
    conn.commit()
    conn.close()


def _seed_backtest_run(
    db: Path,
    *,
    strategy_id: str,
    sharpe: float | None,
    max_dd: float | None,
    trades: int | None,
    status: str = "completed",
    run_id: str = "run-001",
) -> None:
    meta = {
        "oos_aggregate": {
            "sharpe": sharpe,
            "max_drawdown_pct": max_dd,
            "trade_count": trades,
        }
    }
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, strategy_id, status, started_at, ended_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            strategy_id,
            status,
            datetime.now(tz=UTC).isoformat(),
            datetime.now(tz=UTC).isoformat(),
            json.dumps(meta),
        ),
    )
    conn.commit()
    conn.close()


def _seed_strategy_run(
    db: Path,
    *,
    strategy_id: str,
    ended_at: str | None = None,
    sharpe: float | None = None,
    session_id: str = "sess-001",
) -> None:
    meta = {"sharpe": sharpe} if sharpe is not None else {}
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO strategy_runs
            (session_id, strategy_id, started_at, ended_at, metadata_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session_id,
            strategy_id,
            datetime.now(tz=UTC).isoformat(),
            ended_at,
            json.dumps(meta),
        ),
    )
    conn.commit()
    conn.close()


def _seed_trade(
    db: Path,
    *,
    strategy_name: str,
    strategy_stage: str = "paper",
    status: str = "filled",
    backtest_run_id: str | None = None,
    recorded_at: str | None = None,
) -> None:
    if recorded_at is None:
        recorded_at = datetime.now(tz=UTC).isoformat()
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO trades
            (recorded_at, status, strategy_name, strategy_stage, backtest_run_id,
             symbol, side, quantity)
        VALUES (?, ?, ?, ?, ?, 'SPY', 'BUY', 1.0)
        """,
        (recorded_at, status, strategy_name, strategy_stage, backtest_run_id),
    )
    conn.commit()
    conn.close()


def _seed_frozen_manifest(db: Path, *, strategy_id: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO strategy_manifests
            (strategy_id, stage, frozen_at)
        VALUES (?, 'paper', ?)
        """,
        (strategy_id, datetime.now(tz=UTC).isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Pure-logic: _compute_underperforming
# ---------------------------------------------------------------------------


def test_underperforming_evidence_floor_below() -> None:
    """evidence_n < MIN_TRADES → False even with bad paper Sharpe."""
    from milodex.gui.attention_state import _compute_underperforming
    from milodex.promotion.state_machine import MIN_TRADES

    assert _compute_underperforming(0.0, 1.5, MIN_TRADES - 1) is False


def test_underperforming_evidence_floor_at_boundary_flagged() -> None:
    """evidence_n == MIN_TRADES with paper < baseline → True."""
    from milodex.gui.attention_state import _compute_underperforming
    from milodex.promotion.state_machine import MIN_TRADES

    assert _compute_underperforming(0.3, 1.5, MIN_TRADES) is True


def test_underperforming_evidence_floor_at_boundary_good_performance() -> None:
    """evidence_n == MIN_TRADES but paper >= baseline → False."""
    from milodex.gui.attention_state import _compute_underperforming
    from milodex.promotion.state_machine import MIN_TRADES

    assert _compute_underperforming(1.6, 1.5, MIN_TRADES) is False


def test_underperforming_none_paper_sharpe() -> None:
    """None paper_sharpe → False (regardless of evidence count)."""
    from milodex.gui.attention_state import _compute_underperforming
    from milodex.promotion.state_machine import MIN_TRADES

    assert _compute_underperforming(None, 1.5, MIN_TRADES) is False


def test_underperforming_none_baseline_sharpe() -> None:
    """None baseline_sharpe → False."""
    from milodex.gui.attention_state import _compute_underperforming
    from milodex.promotion.state_machine import MIN_TRADES

    assert _compute_underperforming(0.3, None, MIN_TRADES) is False


def test_underperforming_both_none() -> None:
    """Both None → False."""
    from milodex.gui.attention_state import _compute_underperforming
    from milodex.promotion.state_machine import MIN_TRADES

    assert _compute_underperforming(None, None, MIN_TRADES) is False


def test_underperforming_equal_sharpes() -> None:
    """paper_sharpe == baseline_sharpe → False (not strictly less)."""
    from milodex.gui.attention_state import _compute_underperforming
    from milodex.promotion.state_machine import MIN_TRADES

    assert _compute_underperforming(1.0, 1.0, MIN_TRADES) is False


def test_underperforming_custom_min_evidence() -> None:
    """Custom min_evidence_n is respected."""
    from milodex.gui.attention_state import _compute_underperforming

    # floor = 5, evidence = 4 → False
    assert _compute_underperforming(0.1, 1.0, 4, min_evidence_n=5) is False
    # floor = 5, evidence = 5 → True (paper < baseline)
    assert _compute_underperforming(0.1, 1.0, 5, min_evidence_n=5) is True


# ---------------------------------------------------------------------------
# needsReview classifier (a): gate-pass + no paper promotion
# ---------------------------------------------------------------------------


def test_needs_review_case_a_gate_pass_not_promoted(tmp_path) -> None:
    """Strategy that clears all gates and has no paper promotion → in needsReview."""
    from milodex.gui.attention_state import _query_attention

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    _seed_backtest_run(db, strategy_id="strat.a", sharpe=1.0, max_dd=10.0, trades=35)

    result = _query_attention(db)
    assert result["needs_review"] >= 1


def test_needs_review_case_a_already_promoted_excluded(tmp_path) -> None:
    """Strategy that passes gates but IS promoted to paper → not in case (a)."""
    from milodex.gui.attention_state import _query_attention

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    _seed_backtest_run(db, strategy_id="strat.a", sharpe=1.0, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="strat.a", to_stage="paper", sharpe_ratio=1.0)

    result = _query_attention(db)
    # case (a) should not count strat.a; total may still be 0
    # strat.a is paper now, no micro_live, evidence=0 → case(b) doesn't fire (0 < MIN_TRADES)
    assert result["needs_review"] == 0


def test_needs_review_case_a_failing_gate_excluded(tmp_path) -> None:
    """Strategy that fails a gate (low Sharpe) → not in case (a)."""
    from milodex.gui.attention_state import _query_attention

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    _seed_backtest_run(db, strategy_id="strat.fail", sharpe=0.1, max_dd=10.0, trades=35)

    result = _query_attention(db)
    assert result["needs_review"] == 0


# ---------------------------------------------------------------------------
# needsReview classifier (b): paper with ≥ MIN_TRADES evidence, no micro_live
# ---------------------------------------------------------------------------


def test_needs_review_case_b_enough_evidence_no_micro(tmp_path) -> None:
    """Paper strategy with ≥ MIN_TRADES fills and no micro_live promotion → in needsReview."""
    from milodex.gui.attention_state import _query_attention
    from milodex.promotion.state_machine import MIN_TRADES

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    _seed_backtest_run(db, strategy_id="strat.b", sharpe=1.0, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="strat.b", to_stage="paper", sharpe_ratio=1.0)

    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="strat.b", strategy_stage="paper")

    result = _query_attention(db)
    assert result["needs_review"] >= 1


def test_needs_review_case_b_below_evidence_floor(tmp_path) -> None:
    """Paper strategy with < MIN_TRADES fills → not in case (b)."""
    from milodex.gui.attention_state import _query_attention
    from milodex.promotion.state_machine import MIN_TRADES

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    _seed_backtest_run(db, strategy_id="strat.b", sharpe=1.0, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="strat.b", to_stage="paper", sharpe_ratio=1.0)

    for _ in range(MIN_TRADES - 1):
        _seed_trade(db, strategy_name="strat.b", strategy_stage="paper")

    result = _query_attention(db)
    assert result["needs_review"] == 0


def test_needs_review_case_b_already_micro_live(tmp_path) -> None:
    """Paper strategy with ≥ MIN_TRADES fills already promoted to micro_live → not in case (b)."""
    from milodex.gui.attention_state import _query_attention
    from milodex.promotion.state_machine import MIN_TRADES

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    _seed_backtest_run(db, strategy_id="strat.b", sharpe=1.0, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="strat.b", to_stage="paper", sharpe_ratio=1.0)
    _seed_promotion(db, strategy_id="strat.b", to_stage="micro_live")

    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="strat.b", strategy_stage="paper")

    result = _query_attention(db)
    assert result["needs_review"] == 0


# ---------------------------------------------------------------------------
# needsReview classifier (c): underperforming + no operator acknowledgement
# ---------------------------------------------------------------------------


def test_needs_review_case_c_underperformer_no_action(tmp_path) -> None:
    """Underperforming strategy with no demotion or freeze → in needsReview(c).

    Adds micro_live promotion to suppress case (b), so the test specifically
    verifies case (c) alone triggers needs_review.
    """
    from milodex.gui.attention_state import _query_attention
    from milodex.promotion.state_machine import MIN_TRADES

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    # Promote to paper with baseline_sharpe=1.5
    _seed_backtest_run(db, strategy_id="strat.c", sharpe=1.5, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="strat.c", to_stage="paper", sharpe_ratio=1.5)
    # suppress case (b): micro_live promotion
    _seed_promotion(db, strategy_id="strat.c", to_stage="micro_live")

    # Add MIN_TRADES fills so evidence floor is met
    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="strat.c", strategy_stage="paper")

    # Add a completed strategy_run with bad live Sharpe
    _seed_strategy_run(
        db, strategy_id="strat.c", ended_at=datetime.now(tz=UTC).isoformat(), sharpe=0.2
    )

    result = _query_attention(db)
    assert result["underperforming"] >= 1
    assert result["needs_review"] >= 1


def test_needs_review_case_c_underperformer_with_demotion(tmp_path) -> None:
    """Underperforming strategy with a demotion row → NOT in needsReview(c).

    Also adds micro_live promotion to suppress case (b) interference (since
    strategy has ≥ MIN_TRADES paper fills, it would otherwise trigger case (b)
    as well).
    """
    from milodex.gui.attention_state import _query_attention
    from milodex.promotion.state_machine import MIN_TRADES

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    _seed_backtest_run(db, strategy_id="strat.c2", sharpe=1.5, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="strat.c2", to_stage="paper", sharpe_ratio=1.5)
    # suppress case (b): add micro_live promotion
    _seed_promotion(db, strategy_id="strat.c2", to_stage="micro_live")

    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="strat.c2", strategy_stage="paper")

    _seed_strategy_run(
        db, strategy_id="strat.c2", ended_at=datetime.now(tz=UTC).isoformat(), sharpe=0.2
    )

    # Add demotion row — this is the operator acknowledgement for (c)
    _seed_promotion(
        db,
        strategy_id="strat.c2",
        to_stage="backtest",
        promotion_type="demotion",
    )

    result = _query_attention(db)
    # underperforming should include strat.c2, but needs_review case(c) should NOT
    assert result["underperforming"] >= 1
    assert result["needs_review"] == 0


def test_needs_review_case_c_underperformer_with_frozen_manifest(tmp_path) -> None:
    """Underperforming strategy with a frozen manifest → NOT in needsReview(c).

    Also adds micro_live promotion to suppress case (b) interference.
    """
    from milodex.gui.attention_state import _query_attention
    from milodex.promotion.state_machine import MIN_TRADES

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    _seed_backtest_run(db, strategy_id="strat.c3", sharpe=1.5, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="strat.c3", to_stage="paper", sharpe_ratio=1.5)
    # suppress case (b): add micro_live promotion
    _seed_promotion(db, strategy_id="strat.c3", to_stage="micro_live")

    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="strat.c3", strategy_stage="paper")

    _seed_strategy_run(
        db, strategy_id="strat.c3", ended_at=datetime.now(tz=UTC).isoformat(), sharpe=0.2
    )
    _seed_frozen_manifest(db, strategy_id="strat.c3")

    result = _query_attention(db)
    assert result["underperforming"] >= 1
    assert result["needs_review"] == 0


# ---------------------------------------------------------------------------
# Relationship test: (c) ⊆ underperforming  — MUST hold
# ---------------------------------------------------------------------------


def test_relationship_c_subset_of_underperforming(tmp_path) -> None:
    """LOCKED INVARIANT: every strategy in needsReview(c) is also underperforming.

    Seeds:
    - strat.under  — underperformer with no operator action → in (c) AND underperforming
    - strat.demoted — underperformer with demotion → in underperforming but NOT (c)

    Asserts:
    1. strat.under is counted in underperforming.
    2. The (c) subset is non-empty (strat.under is in it).
    3. strat.demoted is counted in underperforming.
    4. The (c) count does not include strat.demoted (needs_review decreases by 1
       relative to a run without the demotion, verified structurally).
    5. General: needs_review(c) ≤ underperforming always.
    """
    from milodex.gui.attention_state import _compute_underperforming, _query_attention
    from milodex.promotion.state_machine import MIN_TRADES

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    # strat.under — underperformer, no action
    # micro_live promotion added to suppress case (b) so only case (c) fires for strat.under
    _seed_backtest_run(db, strategy_id="strat.under", sharpe=1.5, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="strat.under", to_stage="paper", sharpe_ratio=1.5)
    _seed_promotion(db, strategy_id="strat.under", to_stage="micro_live")
    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="strat.under", strategy_stage="paper")
    _seed_strategy_run(
        db, strategy_id="strat.under", ended_at=datetime.now(tz=UTC).isoformat(), sharpe=0.2
    )

    # strat.demoted — underperformer, operator acknowledged via demotion
    _seed_backtest_run(
        db,
        strategy_id="strat.demoted",
        sharpe=1.5,
        max_dd=10.0,
        trades=35,
        run_id="run-002",
    )
    _seed_promotion(db, strategy_id="strat.demoted", to_stage="paper", sharpe_ratio=1.5)
    # suppress case (b) for strat.demoted: add micro_live promotion so only (c) logic applies
    _seed_promotion(db, strategy_id="strat.demoted", to_stage="micro_live")
    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="strat.demoted", strategy_stage="paper")
    _seed_strategy_run(
        db,
        strategy_id="strat.demoted",
        ended_at=datetime.now(tz=UTC).isoformat(),
        sharpe=0.3,
        session_id="sess-002",
    )
    _seed_promotion(
        db,
        strategy_id="strat.demoted",
        to_stage="backtest",
        promotion_type="demotion",
    )

    result = _query_attention(db)

    underperforming_count = result["underperforming"]
    needs_review_count = result["needs_review"]

    # Both strategies should be underperforming (evidence ≥ floor, paper < baseline)
    assert underperforming_count >= 2, (
        f"Expected both strat.under and strat.demoted in underperforming, "
        f"got underperforming={underperforming_count}"
    )

    # Only strat.under should be in needsReview(c); strat.demoted is acknowledged
    # needsReview should include strat.under but NOT strat.demoted
    assert needs_review_count >= 1, "strat.under should be in needs_review"
    # needs_review cannot exceed underperforming (since case(c) ⊆ underperforming,
    # and cases a/b add non-underperforming strategies which we haven't seeded)
    # In this fixture only (c) fires: no gate-pass-not-promoted, no sufficient evidence w/o micro
    assert needs_review_count <= underperforming_count, (
        f"(c)⊆underperforming violated: needs_review={needs_review_count} > "
        f"underperforming={underperforming_count}"
    )

    # Confirm _compute_underperforming logic matches for both
    assert _compute_underperforming(0.2, 1.5, MIN_TRADES) is True, "strat.under should be flagged"
    assert _compute_underperforming(0.3, 1.5, MIN_TRADES) is True, "strat.demoted should be flagged"

    # strat.under → in (c); strat.demoted → not in (c) due to demotion
    # Since only (c) fires here, needs_review == count(strat.under only) == 1
    assert needs_review_count == 1, (
        f"Only strat.under should be in needs_review(c); got {needs_review_count}"
    )


# ---------------------------------------------------------------------------
# _query_attention: rollup correctness
# ---------------------------------------------------------------------------


def test_query_attention_running_now(tmp_path) -> None:
    """running_now counts distinct strategies with ended_at IS NULL."""
    from milodex.gui.attention_state import _query_attention

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    # Two running (ended_at=None), one completed
    _seed_strategy_run(db, strategy_id="s1")
    _seed_strategy_run(db, strategy_id="s2")
    _seed_strategy_run(db, strategy_id="s3", ended_at=datetime.now(tz=UTC).isoformat())

    result = _query_attention(db)
    assert result["running_now"] == 2


def test_query_attention_paper_testing_and_backtest_only(tmp_path) -> None:
    """paperTesting and backtestOnly match _query_bank output."""
    from milodex.gui.attention_state import _query_attention

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    # One paper strategy
    _seed_backtest_run(db, strategy_id="paper.one", sharpe=1.0, max_dd=10.0, trades=35)
    _seed_promotion(db, strategy_id="paper.one", to_stage="paper", sharpe_ratio=1.0)

    # One blocked-at-backtest strategy (fails gate)
    _seed_backtest_run(
        db, strategy_id="blocked.one", sharpe=0.1, max_dd=10.0, trades=35, run_id="run-002"
    )

    result = _query_attention(db)
    assert len(result["paper_list"]) == 1
    assert len(result["blocked_list"]) == 1


def test_query_attention_empty_db(tmp_path) -> None:
    """All rollups are zero on an empty DB."""
    from milodex.gui.attention_state import _query_attention

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    result = _query_attention(db)
    assert result["running_now"] == 0
    assert result["needs_review"] == 0
    assert result["underperforming"] == 0
    assert result["drift_list"] == []


def test_query_attention_missing_db_raises(tmp_path) -> None:
    """_query_attention raises when the DB path does not exist."""
    from milodex.gui.attention_state import _query_attention

    with pytest.raises(Exception):  # noqa: B017
        _query_attention(tmp_path / "nonexistent.db")


# ---------------------------------------------------------------------------
# Multi-strategy scenario: exercises every rollup + driftList
# ---------------------------------------------------------------------------


def test_query_attention_multi_strategy_scenario(tmp_path) -> None:
    """Multi-strategy fixture exercises all rollup buckets and driftList entries."""
    from milodex.gui.attention_state import _query_attention
    from milodex.promotion.state_machine import MIN_TRADES

    db = tmp_path / "att.db"
    _create_fixture_db(db)

    # --- runningNow: 2 active sessions ---
    _seed_strategy_run(db, strategy_id="live.s1")  # no ended_at
    _seed_strategy_run(db, strategy_id="live.s2")  # no ended_at

    # --- paperTesting: gate-pass promoted to paper ---
    _seed_backtest_run(db, strategy_id="paper.good", sharpe=1.2, max_dd=8.0, trades=40)
    _seed_promotion(db, strategy_id="paper.good", to_stage="paper", sharpe_ratio=1.2)

    # --- backtestOnly: fails Sharpe gate ---
    _seed_backtest_run(
        db, strategy_id="bt.fail", sharpe=0.2, max_dd=8.0, trades=40, run_id="run-bt"
    )

    # --- needsReview(a): gate-pass, not promoted ---
    _seed_backtest_run(
        db,
        strategy_id="nr.a",
        sharpe=1.0,
        max_dd=5.0,
        trades=35,
        run_id="run-nra",
    )

    # --- needsReview(b): paper + enough evidence, no micro_live ---
    _seed_backtest_run(
        db, strategy_id="nr.b", sharpe=1.0, max_dd=5.0, trades=35, run_id="run-nrb"
    )
    _seed_promotion(db, strategy_id="nr.b", to_stage="paper", sharpe_ratio=1.0)
    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="nr.b", strategy_stage="paper")

    # --- needsReview(c) + underperforming: paper, bad live sharpe, no action ---
    _seed_backtest_run(
        db, strategy_id="nr.c", sharpe=1.8, max_dd=5.0, trades=35, run_id="run-nrc"
    )
    _seed_promotion(db, strategy_id="nr.c", to_stage="paper", sharpe_ratio=1.8)
    for _ in range(MIN_TRADES):
        _seed_trade(db, strategy_name="nr.c", strategy_stage="paper")
    _seed_strategy_run(
        db,
        strategy_id="nr.c",
        ended_at=datetime.now(tz=UTC).isoformat(),
        sharpe=0.1,
        session_id="sess-nrc",
    )

    # --- driftList recency: paper.good has no recent fills ---
    # (no trades for paper.good → will appear as "no fills in N days")

    result = _query_attention(db)

    assert result["running_now"] == 2
    assert len(result["paper_list"]) >= 3  # paper.good, nr.b, nr.c
    assert len(result["blocked_list"]) >= 1  # bt.fail

    # needsReview: nr.a (case a), nr.b (case b), nr.c (case c)
    assert result["needs_review"] >= 3
    # underperforming: nr.c
    assert result["underperforming"] >= 1

    # underperforming >= case(c) count = 1
    assert result["underperforming"] >= 1

    # driftList: at least nr.c (underperformer) + paper.good (no fills)
    drift_names = [d["name"] for d in result["drift_list"]]
    assert "nr.c" in drift_names
    assert "paper.good" in drift_names


# ---------------------------------------------------------------------------
# Read-only connection test — no Qt required
# ---------------------------------------------------------------------------


def test_read_only_connection_blocks_writes(tmp_path) -> None:
    """Connecting with file:...?mode=ro raises OperationalError on write attempt."""
    db = tmp_path / "readonly_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.commit()
    conn.close()

    ro_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_conn.execute("CREATE TABLE x(a)")
    ro_conn.close()


# ---------------------------------------------------------------------------
# Qt-aware fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication for Qt-aware tests."""
    if not _PYSIDE6_AVAILABLE:
        return None

    import os
    import sys

    from PySide6.QtGui import QGuiApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


def _make_state(db_path: Path, refresh_interval_ms: int = 99_999_999):
    from milodex.gui.attention_state import AttentionState

    return AttentionState(db_path=db_path, refresh_interval_ms=refresh_interval_ms)


def _wait_for_pool(state) -> None:
    state._thread_pool.waitForDone(2000)  # noqa: SLF001
    QCoreApplication.processEvents()
    QCoreApplication.processEvents()


# ---------------------------------------------------------------------------
# Qt lifecycle tests
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_initial_state_is_loading(qapp, tmp_path) -> None:
    """Before any refresh, dataStatus is 'loading' and rollups is empty."""
    _ = qapp
    db = tmp_path / "att.db"
    _create_fixture_db(db)
    state = _make_state(db)

    assert state.dataStatus == "loading"
    assert state.rollups == {}
    assert state.driftList == []
    assert state.lastRefreshedAt == ""
    assert state.dataErrorMessage == ""


@_skip_no_qt
def test_refresh_populates_rollups(qapp, tmp_path) -> None:
    """After a successful refresh, rollups contains all expected keys."""
    _ = qapp
    db = tmp_path / "att.db"
    _create_fixture_db(db)

    # Minimal data: one running strategy
    _seed_strategy_run(db, strategy_id="s1")

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state)

    assert state.dataStatus == "ready"
    rollups = state.rollups
    assert "runningNow" in rollups
    assert "paperTesting" in rollups
    assert "backtestOnly" in rollups
    assert "needsReview" in rollups
    assert "underperforming" in rollups
    assert rollups["runningNow"] == 1

    state.stop()


# Lifecycle scaffold tests (missing-DB error, error-after-success preservation,
# in-flight drop, stop-drains-worker) were removed in PR C of RM-007 — those
# contracts are now covered ONCE in tests/milodex/gui/test_polling_lifecycle.py.

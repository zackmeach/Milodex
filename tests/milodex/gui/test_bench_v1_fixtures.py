"""Tests for the v1 Bench fixture row set.

Covers:

- Per-fixture invariants (valid construction, ID uniqueness, dotted ID
  convention).
- The empty-menu floor invariant (ADR 0047 Decision 5) verified per
  ADR 0049 Decision 5: ``Open Evidence`` is the last item on every
  fixture row.
- Menu-state-space coverage: across all fixtures, every menu rule
  branch is exercised (per ADR 0049 Decision 5 — "at least one row
  exercising every menu rule").
- Anchor scenarios — specific fixtures must produce the menus
  documented in bench-brief §7.3.
"""

from __future__ import annotations

import pytest

from milodex.gui.bench_v1 import (
    LABEL_DEMOTE_TO_BACKTEST,
    LABEL_INITIATE_BACKTEST,
    LABEL_OPEN_EVIDENCE,
    LABEL_REFRESH_BACKTEST,
    LABEL_RETURN_TO_IDLE,
    LABEL_START_TRADING,
    LABEL_STOP_TRADING,
    Stage,
    compute_menu_items,
)
from milodex.gui.bench_v1_fixtures import BenchFixtureRow, bench_v1_demo_rows

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _labels_for(row: BenchFixtureRow) -> list[str]:
    return [item.label for item in compute_menu_items(row.state)]


def _row_by_id(strategy_id: str) -> BenchFixtureRow:
    rows = bench_v1_demo_rows()
    matches = [r for r in rows if r.strategy_id == strategy_id]
    assert len(matches) == 1, f"expected exactly one fixture for {strategy_id!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# Per-fixture invariants
# ---------------------------------------------------------------------------


class TestFixtureRowInvariants:
    def test_demo_rows_returns_a_non_empty_list(self) -> None:
        rows = bench_v1_demo_rows()
        assert isinstance(rows, list)
        assert len(rows) > 0

    def test_demo_rows_returns_a_fresh_list_each_call(self) -> None:
        # Callers may sort/filter without contaminating the canonical set.
        a = bench_v1_demo_rows()
        b = bench_v1_demo_rows()
        assert a is not b
        assert a == b

    def test_strategy_ids_are_unique(self) -> None:
        ids = [row.strategy_id for row in bench_v1_demo_rows()]
        assert len(ids) == len(set(ids)), "duplicate strategy_id in fixture set"

    def test_every_row_has_dotted_strategy_id(self) -> None:
        # ADR 0015 strategy-id convention is dotted form like
        # `family.tempo.template.universe.vN`. The fixture set should
        # follow it (loose check: at least three dots).
        for row in bench_v1_demo_rows():
            assert row.strategy_id.count(".") >= 3, row.strategy_id

    def test_every_row_has_display_name_and_description(self) -> None:
        for row in bench_v1_demo_rows():
            assert row.display_name.strip(), row.strategy_id
            assert row.description.strip(), row.strategy_id

    def test_every_row_has_family_and_template(self) -> None:
        for row in bench_v1_demo_rows():
            assert row.family.strip(), row.strategy_id
            assert row.template.strip(), row.strategy_id


# ---------------------------------------------------------------------------
# Empty-menu floor on every fixture (ADR 0047 Decision 5; ADR 0049 Decision 5)
# ---------------------------------------------------------------------------


class TestOpenEvidenceFloorOnFixtures:
    @pytest.mark.parametrize(
        "row", bench_v1_demo_rows(), ids=lambda r: r.strategy_id
    )
    def test_open_evidence_is_last_item_on_every_fixture(
        self, row: BenchFixtureRow
    ) -> None:
        labels = _labels_for(row)
        assert labels[-1] == LABEL_OPEN_EVIDENCE, row.strategy_id

    @pytest.mark.parametrize(
        "row", bench_v1_demo_rows(), ids=lambda r: r.strategy_id
    )
    def test_compute_menu_items_returns_at_least_one_item(
        self, row: BenchFixtureRow
    ) -> None:
        items = compute_menu_items(row.state)
        assert len(items) >= 1, row.strategy_id


# ---------------------------------------------------------------------------
# Menu state-space coverage (ADR 0049 Decision 5)
# ---------------------------------------------------------------------------


class TestMenuStateSpaceCoverage:
    """Across all fixtures, every menu rule branch must produce some
    output. If a future fixture rewrite drops a branch, this test
    fails — the user-visible menu has gaps in v1 demo coverage."""

    def test_every_visible_promotion_verb_is_exercised(self) -> None:
        # In v1 the only visible Promote verb is `Promote to Paper`
        # (Promote to Micro Live and Promote to Live are policy-hidden
        # by ADR 0004). Verify Promote to Paper appears at least once.
        all_labels = self._all_labels()
        assert "Promote to Paper" in all_labels

    @pytest.mark.parametrize("verb", ["Return to Paper", "Return to Micro Live", "Return to Live"])
    def test_every_return_to_active_verb_is_exercised(self, verb: str) -> None:
        assert verb in self._all_labels(), verb

    def test_return_to_idle_is_exercised(self) -> None:
        assert LABEL_RETURN_TO_IDLE in self._all_labels()

    def test_demote_to_backtest_is_exercised(self) -> None:
        # Demote is visible at PAPER (the only stage where it is not
        # locked by ADR 0043 Decision 3 in v1).
        assert LABEL_DEMOTE_TO_BACKTEST in self._all_labels()

    def test_initiate_backtest_is_exercised(self) -> None:
        assert LABEL_INITIATE_BACKTEST in self._all_labels()

    def test_refresh_backtest_is_exercised(self) -> None:
        assert LABEL_REFRESH_BACKTEST in self._all_labels()

    def test_start_trading_is_exercised(self) -> None:
        assert LABEL_START_TRADING in self._all_labels()

    def test_stop_trading_is_exercised(self) -> None:
        assert LABEL_STOP_TRADING in self._all_labels()

    def test_open_evidence_is_exercised(self) -> None:
        # Trivially true if the floor invariant holds on any fixture,
        # but worth asserting explicitly so the coverage table is
        # complete in one place.
        assert LABEL_OPEN_EVIDENCE in self._all_labels()

    def test_every_promotion_stage_has_at_least_one_fixture(self) -> None:
        stages = {row.state.current_stage for row in bench_v1_demo_rows()}
        assert stages == set(Stage), (
            f"missing fixtures for stages: {set(Stage) - stages}"
        )

    def test_in_flight_run_state_is_exercised(self) -> None:
        # At least one fixture sets runs_in_flight[BACKTEST] = True so
        # the in-flight suppression branch of re_run_verb is covered.
        rows = bench_v1_demo_rows()
        assert any(
            row.state.runs_in_flight.get(Stage.BACKTEST, False) for row in rows
        )

    def test_session_running_state_is_exercised(self) -> None:
        rows = bench_v1_demo_rows()
        assert any(row.state.is_session_running for row in rows)

    @staticmethod
    def _all_labels() -> set[str]:
        rows = bench_v1_demo_rows()
        labels: set[str] = set()
        for row in rows:
            labels.update(_labels_for(row))
        return labels


# ---------------------------------------------------------------------------
# Capital-stage policy filters honored by fixtures
# ---------------------------------------------------------------------------


class TestCapitalStagePolicyAcrossFixtures:
    """The ADR 0004 / ADR 0043 capital-stage filters must produce the
    expected hide-behavior across the fixture set, not just in unit
    tests."""

    def test_no_fixture_emits_promote_to_micro_live(self) -> None:
        for row in bench_v1_demo_rows():
            assert "Promote to Micro Live" not in _labels_for(row), row.strategy_id

    def test_no_fixture_emits_promote_to_live(self) -> None:
        for row in bench_v1_demo_rows():
            assert "Promote to Live" not in _labels_for(row), row.strategy_id

    def test_no_micro_live_or_live_fixture_emits_demote_to_backtest(self) -> None:
        # ADR 0043 Decision 3 + ADR 0004: capital-affecting demotions
        # remain locked while ADR 0004 is in force.
        for row in bench_v1_demo_rows():
            if row.state.current_stage in {Stage.MICRO_LIVE, Stage.LIVE}:
                assert LABEL_DEMOTE_TO_BACKTEST not in _labels_for(row), row.strategy_id


# ---------------------------------------------------------------------------
# Anchor scenarios — specific fixtures produce specific bench-brief §7.3 menus
# ---------------------------------------------------------------------------


class TestAnchorScenarios:
    """Each test pins a specific fixture's expected menu, matching the
    canonical bench-brief §7.3 row examples. If the fixture data
    changes in a way that drifts the menu, these tests fail with a
    precise diff."""

    def test_idle_no_history(self) -> None:
        row = _row_by_id("breakout.daily.atr_channel.sector_etfs.demo.v1")
        assert _labels_for(row) == [LABEL_INITIATE_BACKTEST, LABEL_OPEN_EVIDENCE]

    def test_idle_no_history_backtest_in_flight(self) -> None:
        row = _row_by_id("meanrev.daily.bollinger_squeeze.qqq_holdings.v1")
        assert _labels_for(row) == [LABEL_OPEN_EVIDENCE]

    def test_idle_with_prior_paper_fresh_pass(self) -> None:
        row = _row_by_id("momentum.daily.cross_sectional_rsi.spy_holdings.v1")
        assert _labels_for(row) == [
            LABEL_INITIATE_BACKTEST,
            "Return to Paper",
            LABEL_OPEN_EVIDENCE,
        ]

    def test_idle_with_prior_micro_live_fresh_pass(self) -> None:
        row = _row_by_id("regime.daily.sma200_rotation.spy_shy.v1")
        assert _labels_for(row) == [
            LABEL_INITIATE_BACKTEST,
            "Return to Paper",
            "Return to Micro Live",
            LABEL_OPEN_EVIDENCE,
        ]

    def test_idle_with_prior_live_fresh_not_applicable(self) -> None:
        row = _row_by_id("breakout.daily.donchian_20_10.sector_etfs.demo.v1")
        assert _labels_for(row) == [
            LABEL_INITIATE_BACKTEST,
            "Return to Paper",
            "Return to Micro Live",
            "Return to Live",
            LABEL_OPEN_EVIDENCE,
        ]

    def test_idle_with_micro_live_stale_pass(self) -> None:
        row = _row_by_id("meanrev.weekly.rsi_2_oversold.semi_etfs.v1")
        assert _labels_for(row) == [LABEL_REFRESH_BACKTEST, LABEL_OPEN_EVIDENCE]

    def test_idle_with_micro_live_stale_fail(self) -> None:
        row = _row_by_id("meanrev.daily.bollinger_squeeze.tech_etfs.v1")
        assert _labels_for(row) == [LABEL_INITIATE_BACKTEST, LABEL_OPEN_EVIDENCE]

    def test_idle_with_invalidated_backtest(self) -> None:
        row = _row_by_id("momentum.daily.relative_strength_macd.bond_etfs.v1")
        assert _labels_for(row) == [LABEL_INITIATE_BACKTEST, LABEL_OPEN_EVIDENCE]

    def test_backtest_fresh_pass(self) -> None:
        row = _row_by_id("breakout.daily.range_expansion_atr.energy_etfs.v1")
        assert _labels_for(row) == [
            "Promote to Paper",
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_backtest_fresh_fail(self) -> None:
        row = _row_by_id("meanrev.intraday.opening_range_fade.qqq.v1")
        assert _labels_for(row) == [LABEL_RETURN_TO_IDLE, LABEL_OPEN_EVIDENCE]

    def test_backtest_aging_pass(self) -> None:
        row = _row_by_id("regime.weekly.dual_momentum.global_etf_set.v1")
        assert _labels_for(row) == [
            "Promote to Paper",
            LABEL_REFRESH_BACKTEST,
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_backtest_run_in_flight(self) -> None:
        row = _row_by_id("breakout.daily.bollinger_breakout.gold_etfs.v1")
        assert _labels_for(row) == [LABEL_RETURN_TO_IDLE, LABEL_OPEN_EVIDENCE]

    def test_paper_fresh_pass_session_idle(self) -> None:
        row = _row_by_id("momentum.daily.cross_sectional_rsi.bonds.v1")
        assert _labels_for(row) == [
            LABEL_START_TRADING,
            LABEL_DEMOTE_TO_BACKTEST,
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_paper_session_running(self) -> None:
        row = _row_by_id("breakout.daily.atr_channel.sector_etfs.paper_runner.v1")
        assert _labels_for(row) == [
            LABEL_STOP_TRADING,
            LABEL_DEMOTE_TO_BACKTEST,
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_paper_aging_pass_with_backtest_aging_pass(self) -> None:
        row = _row_by_id("meanrev.daily.zscore_reversion.bonds.v1")
        assert _labels_for(row) == [
            LABEL_START_TRADING,
            LABEL_REFRESH_BACKTEST,
            LABEL_DEMOTE_TO_BACKTEST,
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_micro_live_session_idle(self) -> None:
        row = _row_by_id("regime.daily.adaptive_volatility.spy_iwm.v1")
        assert _labels_for(row) == [
            LABEL_START_TRADING,
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_live_session_running(self) -> None:
        row = _row_by_id("regime.daily.sma200_rotation.spy_shy.live.v1")
        assert _labels_for(row) == [
            LABEL_STOP_TRADING,
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]


# ---------------------------------------------------------------------------
# Display metadata vs. evidence consistency
# ---------------------------------------------------------------------------


class TestDisplayMetadataConsistency:
    def test_metric_snapshots_only_present_when_evidence_supports_them(self) -> None:
        # Forward direction: if a row carries metric snapshots, there must
        # be at least one stage with completed evidence (anything other than
        # Missing+Pending).
        from milodex.gui.bench_v1 import Freshness

        for row in bench_v1_demo_rows():
            has_completed_evidence = any(
                ev.freshness != Freshness.MISSING
                for ev in row.state.evidence_by_stage.values()
            )
            if row.sharpe is not None:
                assert has_completed_evidence, (
                    f"{row.strategy_id} has metrics but no completed evidence"
                )

    def test_metric_snapshots_absent_when_no_usable_evidence_or_invalidated(self) -> None:
        # Reverse direction: rows where every evidence record is either
        # Missing (no completed evidence) or Invalidated (prior result
        # voided, no longer trusted) should leave metrics None.
        # Aging+Pass / Stale+Pass / Fresh+Fail etc. count as completed
        # evidence and may carry metrics.
        from milodex.gui.bench_v1 import Freshness

        for row in bench_v1_demo_rows():
            evidence_records = list(row.state.evidence_by_stage.values())
            if not evidence_records:
                # No evidence records at all — metrics must be None.
                assert row.sharpe is None, (
                    f"{row.strategy_id} carries metrics with no evidence records"
                )
                continue
            all_unusable_or_invalidated = all(
                ev.freshness in {Freshness.MISSING, Freshness.INVALIDATED}
                for ev in evidence_records
            )
            if all_unusable_or_invalidated:
                assert row.sharpe is None, (
                    f"{row.strategy_id} carries metrics but all evidence is "
                    f"Missing or Invalidated (display would mislead the operator)"
                )

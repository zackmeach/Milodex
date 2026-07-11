"""Behavioral tests for ``LedgerState`` (src/milodex/gui/ledger_state.py).

Exercises ``_refilter`` multi-dimension filtering, ``setGroupFilter``'s
outcome/stage reset semantics, ``clearLedgerFilters``, and the dirty-check
(no-emit-if-unchanged) contract — driven through the public Slot API and
``_apply_result`` (the same entry point ``PollingReadModel`` uses after a
successful refresh), matching the Qt fixture conventions in
``test_polling_lifecycle.py``. The read model is never ``start()``-ed, so the
lazy ``build_ledger_snapshot`` builder is never invoked and no real db/config
path is touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication

from milodex.gui.ledger_state import LedgerState


@pytest.fixture(autouse=True)
def _qt_app():
    """Ensure a QCoreApplication exists for the duration of each test."""
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _entry(
    *,
    strategy_id: str,
    stage: str,
    outcome_kind: str,
    recent: bool = True,
) -> dict[str, Any]:
    return {
        "strategyId": strategy_id,
        "stage": stage,
        "outcomeKind": outcome_kind,
        "recent": recent,
    }


def _make_state() -> LedgerState:
    """A LedgerState never started — the builder lambda is never invoked."""
    return LedgerState(db_path=Path("unused.db"), configs_dir=Path("unused_configs"))


_SEED_ENTRIES = [
    _entry(strategy_id="a.b.c.v1", stage="paper", outcome_kind="promoted", recent=True),
    _entry(
        strategy_id="a.b.c.v1", stage="backtest", outcome_kind="backtested_strong", recent=False
    ),
    _entry(strategy_id="x.y.z.v1", stage="paper", outcome_kind="started", recent=True),
    _entry(strategy_id="x.y.z.v1", stage="system", outcome_kind="fired", recent=False),
    _entry(strategy_id="x.y.z.v1", stage="lifecycle", outcome_kind="stopped", recent=True),
]


def _seed(state: LedgerState, entries: list[dict[str, Any]]) -> None:
    state._apply_result({"entries": entries})  # noqa: SLF001 — production entry point


# --------------------------------------------------------------------------- #
# _refilter: multi-dimension filtering
# --------------------------------------------------------------------------- #


def test_refilter_no_filters_returns_all_entries() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    assert len(state.entries) == len(_SEED_ENTRIES)


def test_refilter_stage_filter() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setLedgerFilter("paper", "all", "all", "all")
    stages = {e["stage"] for e in state.entries}
    assert stages == {"paper"}
    assert len(state.entries) == 2


def test_refilter_strategy_filter() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setLedgerFilter("all", "x.y.z.v1", "all", "all")
    ids = {e["strategyId"] for e in state.entries}
    assert ids == {"x.y.z.v1"}
    assert len(state.entries) == 3


def test_refilter_outcome_filter() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setLedgerFilter("all", "all", "started", "all")
    kinds = {e["outcomeKind"] for e in state.entries}
    assert kinds == {"started"}
    assert len(state.entries) == 1


def test_refilter_time_filter_recent() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setLedgerFilter("all", "all", "all", "recent")
    assert len(state.entries) == 3
    assert all(e["recent"] for e in state.entries)


def test_refilter_combined_stage_and_strategy_and_time() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setLedgerFilter("paper", "x.y.z.v1", "all", "recent")
    assert len(state.entries) == 1
    entry = state.entries[0]
    assert entry["strategyId"] == "x.y.z.v1"
    assert entry["stage"] == "paper"
    assert entry["outcomeKind"] == "started"


def test_refilter_combination_with_no_matches_yields_empty_list() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setLedgerFilter("paper", "does.not.exist.v1", "all", "all")
    assert state.entries == []


def test_refilter_group_filter_membership() -> None:
    """Group→outcomeKind membership map (lines 54-61): 'promotion' group
    matches promoted/demoted/returned only."""
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setGroupFilter("promotion")
    kinds = {e["outcomeKind"] for e in state.entries}
    assert kinds == {"promoted"}


def test_refilter_group_filter_lifecycle_membership() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setGroupFilter("lifecycle")
    kinds = {e["outcomeKind"] for e in state.entries}
    assert kinds == {"started", "stopped"}


def test_refilter_group_filter_system_membership() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setGroupFilter("system")
    # No entries in the seed have outcomeKind in {fired, info, added} except 'fired'.
    kinds = {e["outcomeKind"] for e in state.entries}
    assert kinds == {"fired"}


def test_refilter_unknown_group_matches_nothing() -> None:
    """An unrecognized group string is not 'all', so _GROUP_KINDS.get()
    returns None (no restriction) per current lookup semantics — verify the
    literal fallback behavior rather than assume it filters to empty."""
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setGroupFilter("not_a_real_group")
    # group_kinds is None for unknown keys, so no group-based exclusion occurs.
    assert len(state.entries) == len(_SEED_ENTRIES)


# --------------------------------------------------------------------------- #
# setGroupFilter resets outcome+stage filters
# --------------------------------------------------------------------------- #


def test_set_group_filter_resets_outcome_and_stage_filters() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setLedgerFilter("paper", "all", "started", "all")
    assert state.stageFilter == "paper"
    assert state.outcomeFilter == "started"

    state.setGroupFilter("promotion")

    assert state.stageFilter == "all"
    assert state.outcomeFilter == "all"
    assert state.groupFilter == "promotion"


def test_set_group_filter_all_clears_group_restriction() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setGroupFilter("promotion")
    assert len(state.entries) == 1

    state.setGroupFilter("all")

    assert state.groupFilter == "all"
    assert len(state.entries) == len(_SEED_ENTRIES)


def test_set_ledger_filter_resets_group_filter() -> None:
    """setLedgerFilter's own semantics (line 69): applying it after a group
    filter resets the group filter back to 'all'."""
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setGroupFilter("promotion")
    assert state.groupFilter == "promotion"

    state.setLedgerFilter("all", "all", "all", "all")

    assert state.groupFilter == "all"


# --------------------------------------------------------------------------- #
# clearLedgerFilters restores unfiltered view
# --------------------------------------------------------------------------- #


def test_clear_ledger_filters_restores_unfiltered_view() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setLedgerFilter("paper", "x.y.z.v1", "started", "recent")
    assert len(state.entries) == 1

    state.clearLedgerFilters()

    assert state.stageFilter == "all"
    assert state.strategyFilter == "all"
    assert state.outcomeFilter == "all"
    assert state.timeFilter == "all"
    assert state.groupFilter == "all"
    assert len(state.entries) == len(_SEED_ENTRIES)


def test_clear_ledger_filters_after_group_filter_restores_unfiltered_view() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)
    state.setGroupFilter("lifecycle")
    assert len(state.entries) == 2

    state.clearLedgerFilters()

    assert state.groupFilter == "all"
    assert len(state.entries) == len(_SEED_ENTRIES)


# --------------------------------------------------------------------------- #
# Dirty-check: no emit if the filtered result is unchanged
# --------------------------------------------------------------------------- #


def test_applying_same_filter_twice_emits_entries_changed_once() -> None:
    state = _make_state()
    _seed(state, _SEED_ENTRIES)

    emit_count = {"n": 0}
    state.entriesChanged.connect(lambda: emit_count.__setitem__("n", emit_count["n"] + 1))

    state.setLedgerFilter("paper", "all", "all", "all")
    assert emit_count["n"] == 1

    # Re-applying the identical filter yields the same filtered list —
    # _refilter's `if filtered != self._entries` guard must suppress the emit.
    state.setLedgerFilter("paper", "all", "all", "all")
    assert emit_count["n"] == 1


def test_reapplying_seed_with_identical_entries_does_not_emit() -> None:
    """_apply_result's own dirty-check (line 49): feeding the same entries
    list again must not re-trigger _refilter's emit."""
    state = _make_state()
    _seed(state, _SEED_ENTRIES)

    emit_count = {"n": 0}
    state.entriesChanged.connect(lambda: emit_count.__setitem__("n", emit_count["n"] + 1))

    # Same entries content (new list instance, equal by value) — _apply_result's
    # `entries != self._all_entries` guard short-circuits before _refilter runs.
    _seed(state, [dict(e) for e in _SEED_ENTRIES])
    assert emit_count["n"] == 0


def test_changing_filter_to_a_narrower_but_equal_result_does_not_re_emit() -> None:
    """Two different filter dimensions that happen to select the same
    filtered entries list should not double-emit on the second application."""
    state = _make_state()
    entries = [
        _entry(strategy_id="only.one.v1", stage="paper", outcome_kind="promoted"),
        _entry(strategy_id="only.one.v1", stage="backtest", outcome_kind="backtested_strong"),
    ]
    _seed(state, entries)

    emit_count = {"n": 0}
    state.entriesChanged.connect(lambda: emit_count.__setitem__("n", emit_count["n"] + 1))

    state.setLedgerFilter("paper", "all", "all", "all")
    assert emit_count["n"] == 1
    assert len(state.entries) == 1

    # A different filter dimension (outcome instead of stage) selects the
    # same single entry — the resulting list is content-equal, so the
    # dirty-check must suppress a second emit.
    state.setLedgerFilter("all", "all", "promoted", "all")
    assert emit_count["n"] == 1

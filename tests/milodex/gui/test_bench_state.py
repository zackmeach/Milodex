"""Behavioral tests for ``BenchState.selectStrategy`` (src/milodex/gui/bench_state.py:52-56).

Follows the same Qt fixture conventions as ``test_polling_lifecycle.py`` /
``test_ledger_state.py``: the read model is never ``start()``-ed, so the lazy
``build_bench_snapshot`` builder is never invoked and no real db/config/locks
path is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from milodex.gui.bench_state import BenchState


@pytest.fixture(autouse=True)
def _qt_app():
    """Ensure a QCoreApplication exists for the duration of each test."""
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _make_state() -> BenchState:
    return BenchState(
        db_path=Path("unused.db"),
        configs_dir=Path("unused_configs"),
        locks_dir=Path("unused_locks"),
    )


def test_select_strategy_updates_property_and_emits_signal() -> None:
    state = _make_state()
    assert state.selectedStrategyId == ""

    emit_count = {"n": 0}
    state.selectedStrategyChanged.connect(lambda: emit_count.__setitem__("n", emit_count["n"] + 1))

    state.selectStrategy("meanrev.daily.rsi2pullback.v1")

    assert state.selectedStrategyId == "meanrev.daily.rsi2pullback.v1"
    assert emit_count["n"] == 1


def test_select_strategy_same_value_is_a_no_op() -> None:
    """Per the implemented semantic (`if strategy_id != self._selected_strategy_id`),
    re-selecting the currently-selected id does not re-emit."""
    state = _make_state()
    state.selectStrategy("meanrev.daily.rsi2pullback.v1")

    emit_count = {"n": 0}
    state.selectedStrategyChanged.connect(lambda: emit_count.__setitem__("n", emit_count["n"] + 1))

    state.selectStrategy("meanrev.daily.rsi2pullback.v1")

    assert state.selectedStrategyId == "meanrev.daily.rsi2pullback.v1"
    assert emit_count["n"] == 0

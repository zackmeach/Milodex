"""Lock the ``read_models`` re-export shim surface (PR12 decompose).

``read_models.py`` was decomposed into focused modules and turned into a thin
re-export shim. These tests assert that every symbol the codebase imports from
``milodex.gui.read_models`` is (a) still importable from the shim and (b) the
*same object* as in its new home module. Identity (``is``) matters: app.py and
qml_setup.py both import the four State classes from the shim, and
``test_qml_registry`` requires those to be the same class objects.

If a future refactor drops a re-export, these tests fail loudly instead of the
breakage surfacing as a confusing ImportError in an unrelated GUI test.
"""

from __future__ import annotations

import milodex.gui.bench_actions as bench_actions
import milodex.gui.bench_state as bench_state
import milodex.gui.front_page_state as front_page_state
import milodex.gui.kanban_state as kanban_state
import milodex.gui.ledger_state as ledger_state
import milodex.gui.query_helpers as query_helpers
import milodex.gui.read_models as read_models
import milodex.gui.snapshot_builders as snapshot_builders
import milodex.gui.strategy_row as strategy_row

# (shim attribute name, new-home module, new-home attribute name)
_REEXPORTS = [
    ("FrontPageState", front_page_state, "FrontPageState"),
    ("BenchState", bench_state, "BenchState"),
    ("KanbanState", kanban_state, "KanbanState"),
    ("LedgerState", ledger_state, "LedgerState"),
    ("build_front_page_snapshot", snapshot_builders, "build_front_page_snapshot"),
    ("build_bench_snapshot", snapshot_builders, "build_bench_snapshot"),
    ("build_kanban_snapshot", snapshot_builders, "build_kanban_snapshot"),
    ("build_ledger_snapshot", snapshot_builders, "build_ledger_snapshot"),
    ("_StrategyRow", strategy_row, "_StrategyRow"),
    ("_compute_bench_action_menu", bench_actions, "_compute_bench_action_menu"),
    ("_action_intent_preview", bench_actions, "_action_intent_preview"),
    ("_open_ro_conn", query_helpers, "_open_ro_conn"),
]


def test_shim_reexports_every_contract_symbol() -> None:
    """All 12 contract symbols are importable from the shim AND are the
    identical object exported by their new home module."""
    for shim_name, home_module, home_name in _REEXPORTS:
        assert hasattr(read_models, shim_name), (
            f"read_models shim no longer re-exports {shim_name!r}"
        )
        shim_obj = getattr(read_models, shim_name)
        home_obj = getattr(home_module, home_name)
        assert shim_obj is home_obj, (
            f"read_models.{shim_name} is not the same object as "
            f"{home_module.__name__}.{home_name}"
        )


def test_all_lists_the_contract_symbols() -> None:
    """``__all__`` must enumerate exactly the contract surface (no drift)."""
    expected = {name for name, _, _ in _REEXPORTS}
    assert set(read_models.__all__) == expected
    # No duplicates in __all__.
    assert len(read_models.__all__) == len(set(read_models.__all__))


def test_legacy_bench_action_helpers_stay_absent_via_shim() -> None:
    """The deleted legacy code path must not leak back through the shim."""
    assert not hasattr(read_models, "_bench_actions")
    assert not hasattr(read_models, "_bench_action")


def test_shim_symbols_are_callable_or_class() -> None:
    """Non-vacuous: the re-exported builders/helpers are usable callables and
    the State classes are real classes (guards against re-exporting a stub)."""
    import inspect

    assert inspect.isclass(read_models.FrontPageState)
    assert inspect.isclass(read_models.BenchState)
    assert inspect.isclass(read_models.KanbanState)
    assert inspect.isclass(read_models.LedgerState)
    assert inspect.isclass(read_models._StrategyRow)
    assert callable(read_models.build_front_page_snapshot)
    assert callable(read_models.build_bench_snapshot)
    assert callable(read_models.build_kanban_snapshot)
    assert callable(read_models.build_ledger_snapshot)
    assert callable(read_models._compute_bench_action_menu)
    assert callable(read_models._action_intent_preview)
    assert callable(read_models._open_ro_conn)

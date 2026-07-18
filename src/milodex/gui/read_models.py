"""GUI read models for the Phase 5 observability surfaces.

This module is a thin **re-export shim** (PR12 decompose). The 1669-line
god-module was split along its seams into focused modules; ``read_models``
now re-exports the public contract so every existing
``from milodex.gui.read_models import ...`` keeps working with zero caller
churn. Re-exporting from this single shim also guarantees the four State
classes are the *same* class objects wherever imported (app.py and qml_setup.py
both import them here), which keeps ``test_qml_registry`` in sync.

The classes re-exported here expose *read-only* state to QML. They follow the
same lifecycle contract as ``OperationalState``: periodic refresh on a
per-instance worker pool, main-thread Q_PROPERTY updates, and graceful
degradation that preserves last-known data after a successful refresh.

No code reachable from here runs backtests, promotes, demotes, edits configs,
or resets risk state. The GUI surfaces that bind to these models remain
observability-first.

Private helpers (``_StrategyRow``, ``_compute_bench_action_menu``,
``_action_intent_preview``, ``_open_ro_conn``) are NOT re-exported here —
import them from their home modules below (P3-04 trimmed them from the shim;
only tests ever reached them through it).

New homes for the moved definitions:

- ``strategy_row``       — ``_StrategyRow`` (the shared row contract)
- ``bench_actions``      — action menu / intent preview / evidence packet engine
- ``ledger_builders``    — ``_ledger_entries`` and its six sub-builders
- ``snapshot_builders``  — the four ``build_*_snapshot`` entry points + ``_strategy_rows``
- ``query_helpers``      — read-only event-store SQL projections
- ``row_formatters``     — pure formatter/projector utilities + shared constants
- ``front_page_state`` / ``bench_state`` / ``ledger_state`` — State classes
"""

from __future__ import annotations

from milodex.gui.bench_state import BenchState
from milodex.gui.front_page_state import FrontPageState
from milodex.gui.ledger_state import LedgerState
from milodex.gui.snapshot_builders import (
    build_bench_snapshot,
    build_front_page_snapshot,
    build_ledger_snapshot,
)

__all__ = [
    # State read models (the QML registry imports these from here so they
    # stay identical class objects across app.py / qml_setup.py).
    "FrontPageState",
    "BenchState",
    "LedgerState",
    # Snapshot builder entry points.
    "build_front_page_snapshot",
    "build_bench_snapshot",
    "build_ledger_snapshot",
]

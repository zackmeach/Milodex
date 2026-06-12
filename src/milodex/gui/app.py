"""Application shell for the Milodex GUI.

Bootstrap the Qt Quick application, load bundled fonts, register QML types,
and hand control to the Qt event loop.  Import-time side effects are minimal:
the PySide6 import happens inside :func:`run_app` so that CLI paths that do
not invoke the GUI do not pay the import cost.

Usage::

    from milodex.gui.app import run_app
    raise SystemExit(run_app())
"""

from __future__ import annotations

import importlib.resources
import logging
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AppController — clean-quit slot exposed to QML (Task 37 / PR-7c)
# ---------------------------------------------------------------------------


def _make_app_controller(
    read_models: list[object], *, extra_drainables: list[object] | None = None
) -> object:
    """Construct an ``AppController`` QObject with a ``quitRequested`` slot.

    Deferred import of PySide6 keeps the module importable in CLI paths that
    never start the GUI.

    Parameters
    ----------
    read_models
        All polling read models held by the app.  ``stop()`` is called on each
        during clean shutdown; ``None`` entries are silently skipped.
    extra_drainables
        Non-lifecycle ``stop()``-bearing owners drained OUTSIDE the lifecycle
        filter — chiefly the Bench command bridge, a non-polling QThreadPool
        owner (``lifecycle=False``) that neither the lifecycle stop loop nor the
        global-pool drain reaches.  Each is ``stop()``-ed after the global-pool
        ``waitForDone`` and before ``QGuiApplication.quit()``; ``None`` entries
        are silently skipped.  Optional/keyword so existing callers passing a
        single positional ``read_models`` list keep working unchanged.
    """
    from PySide6.QtCore import QObject, QThreadPool, Slot
    from PySide6.QtGui import QGuiApplication

    class AppController(QObject):
        """Exposes ``quitRequested`` as a QML-callable Slot.

        QML wires the Quit button in the Risk Office drawer to this slot.
        Clean shutdown sequence:
          1. stop() each polling read model
          2. drain QThreadPool (3-second timeout)
          3. stop() each extra non-lifecycle drainable (e.g. the Bench command
             bridge's private async pool)
          4. call QGuiApplication.quit()
        """

        def __init__(self, rms: list[object], extras: list[object]) -> None:
            super().__init__()
            self._read_models = rms
            self._extra_drainables = extras

        @Slot()
        def quitRequested(self) -> None:  # noqa: N802
            """Stop all polling read models, drain thread pool, then quit."""
            for rm in self._read_models:
                if rm is not None:
                    try:
                        rm.stop()
                    except Exception:
                        logger.exception("AppController.quitRequested: stop() failed on %r", rm)
            QThreadPool.globalInstance().waitForDone(3000)
            # Drain non-lifecycle QThreadPool owners (Bench command bridge): the
            # lifecycle stop loop never sees them and the global-pool drain above
            # does not touch their private pools.
            for drainable in self._extra_drainables:
                if drainable is not None:
                    try:
                        drainable.stop()
                    except Exception:
                        logger.exception(
                            "AppController.quitRequested: stop() failed on %r", drainable
                        )
            QGuiApplication.quit()

    return AppController(read_models, list(extra_drainables or []))


# ---------------------------------------------------------------------------
# QML singleton registry (single ordered source of truth)
# ---------------------------------------------------------------------------


def _build_qml_registry(
    *,
    theme_manager: object,
    operational_state: object,
    front_page_state: object,
    bench_state: object,
    ledger_state: object,
    performance_state: object,
    risk_throughput_state: object,
    active_ops_state: object,
    attention_state: object,
    market_tape_state: object,
    activity_feed_state: object,
    bench_command_bridge: object,
    risk_profile_bridge: object,
    orphan_reaper_controller: object,
) -> list[object]:
    """Bind the live instances onto the canonical descriptor spec.

    The ordered source of truth for the GUI wiring is
    ``milodex.gui.qml_setup.REGISTRY_SPEC`` — one static tuple that fixes the
    QML registration order and the per-entry lifecycle flags.  This function
    only zips the construction-site instances onto that spec; it adds no
    ordering or lifecycle knowledge of its own.

    The spec order is the QML registration order; filtered by ``lifecycle`` it
    is also the polling start/stop order, which is a deliberate
    Windows-shutdown teardown contract (stop polling -> drain QThreadPool ->
    quit) and MUST be preserved: ``operational_state`` first through
    ``activity_feed_state``, with ``orphan_reaper_controller`` LAST among
    lifecycle entries.  The three register-only entries (ThemeManager,
    BenchCommandBridge, RiskProfileBridge) carry ``lifecycle=False`` and so are
    skipped by the lifecycle filter.  Of those, BenchCommandBridge is a
    non-polling QThreadPool owner: its private async pool is drained explicitly
    OUTSIDE the lifecycle filter (via ``aboutToQuit`` and
    ``AppController.quitRequested`` in :func:`run_app`), not by the lifecycle
    stop loop.

    To wire a NEW read model: add ONE entry to ``REGISTRY_SPEC`` plus a keyword
    parameter here and its construction in :func:`run_app`.  The registration,
    polling start, AppController membership, load-failure stop, and aboutToQuit
    stop are all derived from this one list.

    Factored out (rather than inlined in :func:`run_app`) so the order-snapshot
    test can assert the order without launching Qt.
    """
    from milodex.gui.qml_setup import REGISTRY_SPEC, QmlSingleton

    provided = {
        "theme_manager": theme_manager,
        "operational_state": operational_state,
        "front_page_state": front_page_state,
        "bench_state": bench_state,
        "ledger_state": ledger_state,
        "performance_state": performance_state,
        "risk_throughput_state": risk_throughput_state,
        "active_ops_state": active_ops_state,
        "attention_state": attention_state,
        "market_tape_state": market_tape_state,
        "activity_feed_state": activity_feed_state,
        "bench_command_bridge": bench_command_bridge,
        "risk_profile_bridge": risk_profile_bridge,
        "orphan_reaper_controller": orphan_reaper_controller,
    }

    return [
        QmlSingleton(qml_name, qml_type, provided[kwarg], lifecycle=lifecycle)
        for kwarg, qml_name, qml_type, lifecycle in REGISTRY_SPEC
    ]


# ---------------------------------------------------------------------------
# Public constant
# ---------------------------------------------------------------------------

#: Absolute path to the QML import root — the directory that contains the
#: ``Milodex/`` folder (which holds ``qmldir``, ``Theme.qml``, etc.).
#: Resolved via ``importlib.resources`` for correctness across editable
#: installs, unpacked wheels, and PyInstaller bundles.
QML_IMPORT_PATH: Path = Path(str(importlib.resources.files("milodex.gui").joinpath("qml")))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_default_broker_factory() -> Callable[[], object]:
    """Return a broker-client factory matching the CLI's wiring.

    The factory raises on construction failure, which the
    OperationalState worker catches and surfaces as ``broker_status =
    "error"``.  Mirroring the CLI is deliberate — divergence between
    GUI and CLI broker construction would be a footgun (different
    credential lookup paths, different defaults).
    """
    from milodex.broker.alpaca_client import AlpacaBrokerClient

    return AlpacaBrokerClient


def _build_default_kill_switch_store():
    """Construct the kill-switch store the same way the CLI does.

    See ``milodex.cli.main.main`` (``get_strategy_runner`` for the
    canonical wiring): the store is event-store-backed with a legacy
    JSON migration path under ``logs/kill_switch_state.json``.
    """
    from milodex.config import get_data_dir, get_logs_dir
    from milodex.core.event_store import EventStore
    from milodex.execution.state import KillSwitchStateStore

    event_store = EventStore(get_data_dir() / "milodex.db")
    return KillSwitchStateStore(
        event_store=event_store,
        legacy_path=get_logs_dir() / "kill_switch_state.json",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_app() -> int:
    """Bootstrap and run the Milodex Qt Quick application.

    Steps:

    1. Construct :class:`QGuiApplication` (not ``QApplication`` -- the UI is
       Qt Quick only; no Widgets are used per ADR 0033).
    2. Call :func:`~milodex.gui.fonts.load_fonts` to register bundled font
       families (Newsreader, Public Sans, JetBrains Mono) with Qt.
    3. Construct all read models, build the ordered registry via
       ``_build_qml_registry``, then call
       :func:`~milodex.gui.qml_setup.register_qml_singletons` to bind them
       as QML singletons under ``Milodex``.
    4. Construct :class:`QQmlApplicationEngine`.
    5. Add :data:`QML_IMPORT_PATH` as a QML import search path so
       ``import Milodex 1.0`` resolves.
    6. Load ``Main.qml`` (the top-level ApplicationWindow).
    7. If no root objects were created (load failure), log an error and
       return exit code 1.
    8. Wire the QML ``quit`` signal to ``app.quit``; stop the
       OperationalState polling on app quit.
    9. Return ``app.exec()`` (the Qt event-loop exit code).

    Returns
    -------
    int
        Process exit code: 0 for clean exit, non-zero for error.
    """
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
    except ImportError:
        logger.error(
            "run_app: PySide6 is not installed -- cannot start the GUI. "
            "Install it with: pip install PySide6"
        )
        return 1

    from milodex.backtesting.engine import BacktestEngine
    from milodex.cli.commands.promote import resolve_strategy_config
    from milodex.commands.bench import BenchCommandFacade
    from milodex.config import (
        get_bundled_resource_dir,
        get_cache_dir,
        get_data_dir,
        get_locks_dir,
        get_logs_dir,
        get_trading_mode,
    )
    from milodex.core.event_store import EventStore
    from milodex.data.alpaca_provider import AlpacaDataProvider
    from milodex.gui.active_ops_state import ActiveOpsState
    from milodex.gui.activity_feed_state import ActivityFeedState
    from milodex.gui.attention_state import AttentionState
    from milodex.gui.bench_command_bridge import BenchCommandBridge
    from milodex.gui.fonts import load_fonts
    from milodex.gui.market_tape_state import MarketTapeState
    from milodex.gui.operational_state import OperationalState
    from milodex.gui.performance_state import PerformanceState
    from milodex.gui.qml_setup import register_qml_singletons
    from milodex.gui.read_models import (
        BenchState,
        FrontPageState,
        LedgerState,
    )
    from milodex.gui.risk_profile_bridge import RiskProfileBridge, record_startup_default
    from milodex.gui.risk_throughput_state import RiskThroughputState
    from milodex.gui.theme_manager import ThemeManager
    from milodex.strategies.loader import StrategyLoader
    from milodex.strategies.paper_runner_control import PaperRunnerControl

    # NOTE: register_qml_singletons is imported above but intentionally stays
    # function-local (not hoisted to module scope).  Two tests in test_app.py
    # patch 'milodex.gui.qml_setup.register_qml_singletons'; that patch works
    # only because the import resolves at call time.  Hoisting it would make the
    # patch arrive too late, silently exercising real Qt registration in those tests.

    # --- 1. QGuiApplication ---------------------------------------------------
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv)

    # Identify the application so QSettings (RUNNER HEALTH reap interval) resolves
    # a stable per-user store. The settings helper also passes org/app explicitly,
    # so this is belt-and-suspenders for any future default-constructed QSettings.
    app.setOrganizationName("Milodex")
    app.setApplicationName("Milodex")

    logger.info("run_app: Milodex GUI starting")

    # --- 2. Fonts -------------------------------------------------------------
    loaded_count, failed = load_fonts()
    if failed:
        logger.warning(
            "run_app: %d font file(s) failed to load -- display may degrade",
            len(failed),
        )

    # --- 3. ThemeManager + OperationalState + QML type registration ----------
    theme_manager = ThemeManager()

    # Trading mode read once at startup; OperationalState exposes it
    # statically.  If the env var is malformed get_trading_mode() raises;
    # we catch it so the GUI still launches and shows the error in the
    # broker-error indicator.
    try:
        trading_mode = get_trading_mode()
    except ValueError as exc:
        logger.warning("run_app: get_trading_mode failed (%s) — defaulting to 'paper'", exc)
        trading_mode = "paper"

    # Kill-switch store may itself fail (DB locked, disk full, etc.).
    # Surface the failure rather than crash; the GUI then runs with a
    # degraded operational-state object that always reports inactive.
    try:
        kill_switch_store = _build_default_kill_switch_store()
    except Exception as exc:  # noqa: BLE001 — durable-state ops can fail at startup
        logger.warning("run_app: kill-switch store construction failed (%s) — using stub", exc)
        from unittest.mock import MagicMock

        kill_switch_store = MagicMock()
        kill_switch_store.get_state.return_value = MagicMock(
            active=False, reason=None, last_triggered_at=None
        )

    operational_state = OperationalState(
        broker_client_factory=_build_default_broker_factory(),
        kill_switch_store=kill_switch_store,
        trading_mode=trading_mode,
    )

    data_dir = get_data_dir()
    db_path = data_dir / "milodex.db"
    configs_dir = get_bundled_resource_dir() / "configs"
    cache_dir = get_cache_dir()
    locks_dir = get_locks_dir()

    # Bootstrap reconciliation: close phantom open strategy_runs left by
    # hard-killed runners *before* any read model renders, so the active-ops
    # view never shows a dead runner as live. Liveness-gated — a genuinely
    # running runner (live advisory lock) is left untouched. Never block the
    # GUI on a reconciliation failure.
    try:
        from datetime import UTC, datetime

        from milodex.strategies.orphan_reconciliation import (
            reconcile_orphaned_runs_on_bootstrap,
        )

        reconciled = reconcile_orphaned_runs_on_bootstrap(
            EventStore(db_path), locks_dir, now=datetime.now(tz=UTC)
        )
        if reconciled:
            logger.warning(
                "Bootstrap reconciled %d orphaned strategy run(s): %s",
                len(reconciled),
                ", ".join(reconciled),
            )
    except Exception:
        logger.exception("Bootstrap orphan reconciliation failed; continuing")

    # PR1: periodic orphan reaper — closes phantom strategy_runs rows on a timer
    # (in addition to the one-shot bootstrap reconcile above), so a runner killed
    # mid-session stops showing as "running" without a GUI relaunch. Main-thread
    # QTimer; liveness-gated reaper with the residual-1 re-check guard.
    from milodex.gui.orphan_reaper_controller import OrphanReaperController
    from milodex.gui.runner_health_settings import read_reap_interval_seconds

    orphan_reaper_controller = OrphanReaperController(
        event_store=EventStore(db_path),
        locks_dir=locks_dir,
        interval_seconds=read_reap_interval_seconds(),
    )

    front_page_state = FrontPageState(db_path=db_path, configs_dir=configs_dir)
    bench_state = BenchState(db_path=db_path, configs_dir=configs_dir)
    ledger_state = LedgerState(db_path=db_path, configs_dir=configs_dir)

    # Trading Desk read-models (spec §3 IA→read-model map). PerformanceState
    # and MarketTapeState read the Parquet market cache; ActiveOpsState needs
    # the strategy configs + advisory-lock/stop-sentinel dirs.
    performance_state = PerformanceState(db_path=db_path, cache_dir=cache_dir)
    risk_throughput_state = RiskThroughputState(db_path=db_path)
    active_ops_state = ActiveOpsState(db_path=db_path, configs_dir=configs_dir, locks_dir=locks_dir)
    attention_state = AttentionState(db_path=db_path)
    market_tape_state = MarketTapeState(cache_dir=cache_dir)
    activity_feed_state = ActivityFeedState(db_path=db_path)

    def get_event_store() -> EventStore:
        return EventStore(db_path)

    def get_backtest_engine(strategy_id: str, **kwargs) -> BacktestEngine:
        config_path = resolve_strategy_config(strategy_id, configs_dir)
        loaded = StrategyLoader().load(config_path)
        return BacktestEngine(
            loaded=loaded,
            data_provider=AlpacaDataProvider(),
            event_store=get_event_store(),
            **kwargs,
        )

    bench_command_facade = BenchCommandFacade(
        config_dir=configs_dir,
        locks_dir=get_locks_dir(),
        get_trading_mode=lambda: trading_mode,
        event_store_factory=get_event_store,
        broker_factory=_build_default_broker_factory(),
        backtest_engine_factory=get_backtest_engine,
        paper_runner_control=PaperRunnerControl(
            locks_dir=get_locks_dir(),
            log_dir=get_logs_dir(),
        ),
    )
    bench_command_bridge = BenchCommandBridge(
        bench_command_facade,
        bench_state=bench_state,
        ledger_state=ledger_state,
    )

    # PR-7c: Risk profile bridge + startup audit row.
    # record_startup_default writes one audit row on first-ever launch (when
    # data/risk_profile.txt is absent). Idempotent within 60 s.
    # Wrapped in try/except so a DB failure (e.g. fresh checkout with no DB)
    # never prevents the GUI from launching.
    risk_profile_bridge = RiskProfileBridge(db_path=db_path)
    try:
        record_startup_default(db_path)
    except Exception:
        logger.exception("PR-7c: record_startup_default failed; continuing")

    # P2-06: informational reconcile of data/risk_profile.txt against the
    # risk_profile_changes audit trail. A hand-edit of the file changes runtime
    # behavior with no audit row; surface that as a WARN at startup. Never
    # blocks the GUI, never changes enforcement.
    try:
        from milodex.risk.profile_activation import reconcile_profile_against_audit

        divergence = reconcile_profile_against_audit(db_path)
        if divergence is not None:
            logger.warning("Risk-profile audit divergence: %s", divergence.message)
    except Exception:
        logger.exception("P2-06: risk-profile audit reconcile failed; continuing")

    # Single ordered registry — the one source of truth for QML registration
    # order AND (filtered by .lifecycle) the polling start/stop order. See
    # _build_qml_registry for the Windows-shutdown teardown contract.
    registry = _build_qml_registry(
        theme_manager=theme_manager,
        operational_state=operational_state,
        front_page_state=front_page_state,
        bench_state=bench_state,
        ledger_state=ledger_state,
        performance_state=performance_state,
        risk_throughput_state=risk_throughput_state,
        active_ops_state=active_ops_state,
        attention_state=attention_state,
        market_tape_state=market_tape_state,
        activity_feed_state=activity_feed_state,
        bench_command_bridge=bench_command_bridge,
        risk_profile_bridge=risk_profile_bridge,
        orphan_reaper_controller=orphan_reaper_controller,
    )
    register_qml_singletons(registry)
    # All lifecycle-bearing instances, in teardown order (orphan_reaper last).
    lifecycle_models = [d.instance for d in registry if d.lifecycle]
    logger.info("run_app: active theme = %r", theme_manager.theme)

    # Begin polling AFTER registration so the GUI sees a populated state
    # on the first frame instead of empty defaults. Iterates the registry's
    # lifecycle order: operational_state first ... orphan_reaper_controller last.
    for model in lifecycle_models:
        model.start()

    # Run AFTER market_tape_state.start() so the GUI begins rendering the
    # already-cached symbols immediately. VIX renders as "—" until the
    # warmup completes and the next refresh tick picks it up — typically
    # sub-second on a warm Yahoo connection, but can stall the call to
    # 10+s on a bad network. Doing it here keeps the GUI responsive.
    from milodex.data.tape_cache_warmup import warmup_vix_cache

    warmup_vix_cache(cache_dir=cache_dir)

    # --- 4. Engine ------------------------------------------------------------
    engine = QQmlApplicationEngine()

    # --- 4b. AppController (Task 37 / PR-7c) ----------------------------------
    # Exposes quitRequested() Slot to QML for the Risk Office drawer Quit button.
    # Clean shutdown: stop all polling read models → drain QThreadPool → quit.
    # Membership and order both come from the single registry's lifecycle slice.
    # The Bench command bridge is a non-polling QThreadPool owner registered
    # with lifecycle=False, so it is NOT in lifecycle_models. Drain it
    # explicitly OUTSIDE the lifecycle filter (here via the controller's
    # quitRequested, and via aboutToQuit below) so an in-flight async backtest
    # submit is not abandoned mid-event-store-write on quit.
    app_controller = _make_app_controller(
        lifecycle_models, extra_drainables=[bench_command_bridge]
    )
    engine.rootContext().setContextProperty("AppController", app_controller)

    # --- 5. QML import path ---------------------------------------------------
    engine.addImportPath(str(QML_IMPORT_PATH))

    # --- 6. Load Main.qml -----------------------------------------------------
    main_qml_path = QML_IMPORT_PATH / "Milodex" / "Main.qml"
    logger.info("run_app: loading %s", main_qml_path)
    engine.load(str(main_qml_path))

    # --- 7. Check for load failure --------------------------------------------
    if not engine.rootObjects():
        logger.error(
            "run_app: QQmlApplicationEngine has no root objects after load -- "
            "Main.qml failed to initialize. Check QML errors above."
        )
        # Same teardown order as the registry's lifecycle slice
        # (operational_state first ... orphan_reaper_controller last).
        for model in lifecycle_models:
            model.stop()
        # Bench command bridge is lifecycle=False; drain its private async pool
        # explicitly on this early-return path too (symmetry with aboutToQuit).
        bench_command_bridge.stop()
        return 1

    logger.info(
        "run_app: engine loaded successfully (%d root object(s))",
        len(engine.rootObjects()),
    )

    # --- 8. Wire quit + polling teardown -------------------------------------
    # aboutToQuit fires each connected slot in connection order, so connecting
    # in the registry's lifecycle order preserves the teardown contract
    # (operational_state first ... orphan_reaper_controller last).
    engine.quit.connect(app.quit)
    for model in lifecycle_models:
        app.aboutToQuit.connect(model.stop)
    # Bench command bridge: non-lifecycle QThreadPool owner, drained explicitly
    # OUTSIDE the lifecycle filter (see _make_app_controller / _build_qml_registry).
    app.aboutToQuit.connect(bench_command_bridge.stop)

    # --- 9. Event loop --------------------------------------------------------
    return app.exec()


# ---------------------------------------------------------------------------
# Module main guard (for `python -m milodex.gui.app`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(run_app())

"""Capture full-page Milodex GUI screenshots for the four primary surfaces.

Renders Main.qml headlessly (QT_QPA_PLATFORM=offscreen) and saves one PNG per
surface (front, bench, ledger, desk) to::

    artifacts/gui-screenshots/<YYYYMMDD-HHMMSS>/<surface>.png

Usage::

    python scripts/capture_gui_screenshots.py
    python scripts/capture_gui_screenshots.py --width 1440 --min-height 900
    python scripts/capture_gui_screenshots.py --output-dir /tmp/shots

Generated artifacts are gitignored; never commit them.
No broker connection or live database data is required — all state objects are
started with mock/empty data via MagicMock stubs.
"""

# ruff: noqa: I001

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from milodex.config import get_bundled_resource_dir, get_data_dir
from milodex.gui.app import QML_IMPORT_PATH
from milodex.gui.fonts import load_fonts
from milodex.gui.operational_state import OperationalState
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.read_models import BenchState, DeskState, FrontPageState, LedgerState
from milodex.gui.strategy_bank_state import StrategyBankState
from milodex.gui.theme_manager import ThemeManager


SURFACES = ("front", "bench", "ledger", "desk")


def _process_events(app: QGuiApplication, ms: int) -> None:
    deadline = datetime.now().timestamp() + ms / 1000
    while datetime.now().timestamp() < deadline:
        app.processEvents()


def _make_states() -> tuple[
    OperationalState,
    StrategyBankState,
    FrontPageState,
    BenchState,
    LedgerState,
    DeskState,
]:
    data_dir = get_data_dir()
    db_path = data_dir / "milodex.db"
    configs_dir = get_bundled_resource_dir() / "configs"

    kill_switch_store = MagicMock()
    kill_switch_store.get_state.return_value = MagicMock(
        active=False, reason=None, last_triggered_at=None
    )

    broker = MagicMock()
    broker.get_account.return_value = MagicMock(equity=0.0, cash=0.0, buying_power=0.0)
    broker.is_market_open.return_value = False
    broker.get_positions.return_value = []

    def _broker_factory() -> object:
        return broker

    operational_state = OperationalState(
        broker_client_factory=_broker_factory,
        kill_switch_store=kill_switch_store,
        trading_mode="paper",
        kill_switch_poll_seconds=9999.0,
        broker_poll_seconds=9999.0,
    )
    strategy_bank_state = StrategyBankState(db_path=db_path)
    front_page_state = FrontPageState(db_path=db_path, configs_dir=configs_dir)
    bench_state = BenchState(db_path=db_path, configs_dir=configs_dir)
    ledger_state = LedgerState(db_path=db_path)
    desk_state = DeskState(db_path=db_path, configs_dir=configs_dir)
    return (
        operational_state,
        strategy_bank_state,
        front_page_state,
        bench_state,
        ledger_state,
        desk_state,
    )


def capture(output_dir: Path, *, width: int, min_height: int, max_height: int) -> list[Path]:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    load_fonts()

    theme_manager = ThemeManager()
    states = _make_states()
    (
        operational_state,
        strategy_bank_state,
        front_page_state,
        bench_state,
        ledger_state,
        desk_state,
    ) = states

    register_qml_types(
        theme_manager=theme_manager,
        operational_state=operational_state,
        strategy_bank_state=strategy_bank_state,
        front_page_state=front_page_state,
        bench_state=bench_state,
        ledger_state=ledger_state,
        desk_state=desk_state,
    )

    for state in states:
        state.start()

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
    engine.addImportPath(str(QML_IMPORT_PATH))
    engine.load(QUrl.fromLocalFile(str(QML_IMPORT_PATH / "Milodex" / "Main.qml")))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml did not load")

    root = engine.rootObjects()[0]
    root.setProperty("width", width)
    root.setProperty("height", min_height)
    _process_events(app, 800)

    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for surface in SURFACES:
        root.setProperty("activeSurface", surface)
        _process_events(app, 900)
        requested_height = int(root.property("screenshotContentHeight") or min_height)
        root.setProperty("height", max(min_height, min(requested_height, max_height)))
        _process_events(app, 500)
        screen = root.screen() or app.primaryScreen()
        image = screen.grabWindow(root.winId())
        if image.isNull():
            raise RuntimeError(f"failed to grab screenshot for {surface}")
        path = output_dir / f"{surface}.png"
        if not image.save(str(path)):
            raise RuntimeError(f"failed to save screenshot: {path}")
        saved.append(path)

    for state in states:
        state.stop()

    severe = [warning for warning in warnings if "Button_QMLTYPE" in warning]
    if severe:
        raise RuntimeError("\n".join(severe))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--min-height", type=int, default=1080)
    parser.add_argument("--max-height", type=int, default=9000)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or Path("artifacts") / "gui-screenshots" / timestamp
    saved = capture(
        output_dir,
        width=args.width,
        min_height=args.min_height,
        max_height=args.max_height,
    )
    for path in saved:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

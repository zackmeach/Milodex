"""Capture Bench+Evidence-rail and Bench+Confirmation-modal screenshots.

Companion to scripts/capture_gui_screenshots.py: captures the two interactive
Bench states (Evidence rail open, Confirmation Preview modal open) that the
base capture script cannot reach.

Drives the public BenchSurface API via QQmlApplicationEngine root property
mutation; no clicks, no computer-use automation.
"""

# ruff: noqa: I001

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from milodex.config import get_bundled_resource_dir, get_data_dir
from milodex.gui.app import QML_IMPORT_PATH
from milodex.gui.fonts import load_fonts
from milodex.gui.operational_state import OperationalState
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.read_models import BenchState, FrontPageState, LedgerState
from milodex.gui.strategy_bank_state import StrategyBankState
from milodex.gui.theme_manager import ThemeManager


def _process_events(app: QGuiApplication, ms: int) -> None:
    deadline = datetime.now().timestamp() + ms / 1000
    while datetime.now().timestamp() < deadline:
        app.processEvents()


def _make_states():
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

    def _broker_factory():
        return broker

    operational_state = OperationalState(
        broker_client_factory=_broker_factory,
        kill_switch_store=kill_switch_store,
        trading_mode="paper",
        kill_switch_poll_seconds=9999.0,
        broker_poll_seconds=9999.0,
    )
    return (
        operational_state,
        StrategyBankState(db_path=db_path),
        FrontPageState(db_path=db_path, configs_dir=configs_dir),
        BenchState(db_path=db_path, configs_dir=configs_dir),
        LedgerState(db_path=db_path),
    )


def _find_promotable_row_and_action(bench_state):
    """Find a Bench row/action pair for opening the confirmation modal."""
    fallback_directional = None
    fallback_invocation = None
    fallback_row = None
    for section in bench_state.sections or []:
        for row in section.get("strategies", []) or []:
            fallback_row = fallback_row or row
            for action in row.get("actions", []) or []:
                if action.get("verbClass") == "directional" and "Promote" in (
                    action.get("label") or ""
                ):
                    return row, action
                if action.get("verbClass") == "directional" and fallback_directional is None:
                    fallback_directional = (row, action)
                if fallback_invocation is None:
                    fallback_invocation = (row, action)
    if fallback_directional is not None:
        return fallback_directional
    if fallback_invocation is not None:
        return fallback_invocation
    if fallback_row is not None:
        return fallback_row, None
    return None, None


def _find_any_row(bench_state):
    for section in bench_state.sections or []:
        for row in section.get("strategies", []) or []:
            return row
    return None


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
    ) = states

    register_qml_types(
        theme_manager=theme_manager,
        operational_state=operational_state,
        strategy_bank_state=strategy_bank_state,
        front_page_state=front_page_state,
        bench_state=bench_state,
        ledger_state=ledger_state,
    )

    for state in states:
        state.start()

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_IMPORT_PATH))
    engine.load(QUrl.fromLocalFile(str(QML_IMPORT_PATH / "Milodex" / "Main.qml")))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml did not load")

    root = engine.rootObjects()[0]
    root.setProperty("width", width)
    root.setProperty("height", min_height)
    _process_events(app, 800)

    # Switch to Bench
    root.setProperty("activeSurface", "bench")
    _process_events(app, 1200)

    # Resize to fit Bench content height
    requested_height = int(root.property("screenshotContentHeight") or min_height)
    root.setProperty("height", max(min_height, min(requested_height, max_height)))
    _process_events(app, 800)

    # Reach the BenchSurface root via QObject tree walk.
    from PySide6.QtCore import QObject

    bench = None
    stack: list[QObject] = list(root.findChildren(QObject))
    for child in stack:
        try:
            if (
                child.property("activeModal") is not None
                and child.property("evidenceModalRow") is not None
            ):
                bench = child
                break
        except Exception:
            continue
    if bench is None:
        raise RuntimeError("BenchSurface root not found in object tree")

    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    screen = root.screen() or app.primaryScreen()

    # --- (A) Evidence rail open --------------------------------------------
    row = _find_any_row(bench_state)
    if row is None:
        raise RuntimeError("no Bench rows available for Evidence rail capture")
    bench.setProperty("evidenceModalRow", row)
    bench.setProperty("activeModal", "evidence")
    _process_events(app, 900)

    img_a = screen.grabWindow(root.winId())
    if img_a.isNull():
        raise RuntimeError("Evidence rail grab failed")
    path_a = output_dir / "bench-evidence-rail.png"
    if not img_a.save(str(path_a)):
        raise RuntimeError(f"failed to save: {path_a}")
    saved.append(path_a)

    bench.setProperty("activeModal", "none")
    _process_events(app, 400)

    # --- (B) Confirmation Preview modal open -------------------------------
    # Resize the window taller so the modal (capped at parent.height - 64)
    # can fit all seven sections without inner scrolling — the post-sequence
    # eval needs to see sections 05/06/07 (esp. the NOT SUBMITTABLE banner
    # under §06) in one frame.
    root.setProperty("height", 1700)
    _process_events(app, 400)

    row_p, action_p = _find_promotable_row_and_action(bench_state)
    if row_p is None:
        raise RuntimeError("no row with a directional action found")
    bench.setProperty("confirmationPreviewRow", row_p)
    bench.setProperty("confirmationPreviewAction", action_p)
    bench.setProperty("activeModal", "confirmation")
    _process_events(app, 900)

    img_b = screen.grabWindow(root.winId())
    if img_b.isNull():
        raise RuntimeError("Confirmation modal grab failed")
    path_b = output_dir / "bench-confirmation-modal.png"
    if not img_b.save(str(path_b)):
        raise RuntimeError(f"failed to save: {path_b}")
    saved.append(path_b)

    for state in states:
        state.stop()

    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--min-height", type=int, default=1080)
    parser.add_argument("--max-height", type=int, default=9000)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    saved = capture(
        args.output_dir,
        width=args.width,
        min_height=args.min_height,
        max_height=args.max_height,
    )
    for path in saved:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

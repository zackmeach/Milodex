"""Tests for the QML-side time-format helper (Task 33 / PR-7c).

``formatTimestamp(isoString, format)`` is defined on the Main.qml Window root.
We test it by loading Main.qml in a subprocess and calling the function via a
one-shot QML component that evaluates the expression and exits with a result
printed to stdout.

Pattern mirrors test_qml_load_smoke.py (subprocess isolation so Qt's
process-global module cache is fresh per test).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"


def _build_format_test_script(iso_string: str, fmt: str) -> str:
    """Return a subprocess script that loads Main.qml and calls formatTimestamp."""
    import_root = str(_QML_IMPORT_ROOT)
    main_qml = str(_MILODEX_QML_DIR / "Main.qml")
    return f"""\
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from unittest.mock import MagicMock
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from milodex.commands.bench import BenchCommandFacade
from milodex.gui.bench_command_bridge import BenchCommandBridge
from milodex.gui.fonts import load_fonts
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.risk_profile_bridge import RiskProfileBridge
from milodex.gui.theme_manager import ThemeManager
from milodex.gui.operational_state import OperationalState
from milodex.gui.strategy_bank_state import StrategyBankState
from milodex.gui.read_models import FrontPageState, BenchState, KanbanState, LedgerState
from milodex.gui.performance_state import PerformanceState
from milodex.gui.risk_throughput_state import RiskThroughputState
from milodex.gui.active_ops_state import ActiveOpsState
from milodex.gui.attention_state import AttentionState
from milodex.gui.market_tape_state import MarketTapeState
from milodex.gui.activity_feed_state import ActivityFeedState

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

tm = ThemeManager()
ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(
    active=False, reason=None, last_triggered_at=None
)
op = OperationalState(
    broker_client_factory=lambda: (_ for _ in ()).throw(RuntimeError("smoke")),
    kill_switch_store=ks_store,
    trading_mode="paper",
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)

_nonexistent = Path("/__nonexistent_smoke_test__")
sb = StrategyBankState(db_path=_nonexistent)
front = FrontPageState(db_path=_nonexistent, configs_dir=Path("configs"))
bench = BenchState(db_path=_nonexistent, configs_dir=Path("configs"))
kanban = KanbanState(db_path=_nonexistent, configs_dir=Path("configs"))
ledger = LedgerState(db_path=_nonexistent)
performance = PerformanceState(db_path=_nonexistent, cache_dir=_nonexistent)
risk_throughput = RiskThroughputState(db_path=_nonexistent)
active_ops = ActiveOpsState(
    db_path=_nonexistent, configs_dir=Path("configs"), locks_dir=_nonexistent
)
attention = AttentionState(db_path=_nonexistent)
market_tape = MarketTapeState(cache_dir=_nonexistent)
activity_feed = ActivityFeedState(db_path=_nonexistent)

_smoke_root = Path(tempfile.mkdtemp(prefix="milodex_ts_test_"))
_smoke_configs = _smoke_root / "configs"
_smoke_locks = _smoke_root / "locks"
_smoke_configs.mkdir()
_smoke_locks.mkdir()
facade = BenchCommandFacade(
    config_dir=_smoke_configs,
    locks_dir=_smoke_locks,
    get_trading_mode=lambda: "paper",
)
bench_command_bridge = BenchCommandBridge(facade, bench_state=bench)
risk_profile_bridge = RiskProfileBridge(db_path=_nonexistent)

register_qml_types(
    theme_manager=tm,
    operational_state=op,
    strategy_bank_state=sb,
    front_page_state=front,
    bench_state=bench,
    kanban_state=kanban,
    ledger_state=ledger,
    performance_state=performance,
    risk_throughput_state=risk_throughput,
    active_ops_state=active_ops,
    attention_state=attention,
    market_tape_state=market_tape,
    activity_feed_state=activity_feed,
    bench_command_bridge=bench_command_bridge,
    risk_profile_bridge=risk_profile_bridge,
)

_warnings = []
engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: _warnings.extend(str(m) for m in msgs))
engine.addImportPath({import_root!r})
engine.load(QUrl.fromLocalFile({main_qml!r}))

if _warnings:
    print("QML_WARNINGS:", _warnings, file=sys.stderr)
    sys.exit(3)

roots = engine.rootObjects()
if not roots:
    print("NO_ROOT_OBJECTS", file=sys.stderr)
    sys.exit(2)

root = roots[0]
result = root.formatTimestamp({iso_string!r}, {fmt!r})
print("RESULT:" + str(result))
sys.exit(0)
"""


def _run_format_test(iso_string: str, fmt: str, label: str) -> str:
    """Run a subprocess that calls formatTimestamp and returns the result string."""
    script = _build_format_test_script(iso_string, fmt)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(
            f"formatTimestamp test failed for {label}\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    for line in result.stdout.splitlines():
        if line.startswith("RESULT:"):
            return line[len("RESULT:"):]
    pytest.fail(f"No RESULT line in output for {label}\nstdout:\n{result.stdout}")


def test_time_format_helper_24h() -> None:
    """formatTimestamp returns HH:MM in 24-hour format for 24h setting."""
    # 2026-01-15T15:42:00+00:00 → 15:42 in UTC; JS Date parses as local time
    # so we use a timezone-naive ISO (which JS treats as local) to avoid
    # platform-local-offset dependence.  Use midnight UTC+0 via Z suffix so
    # the HH portion is predictable.
    iso = "2026-01-15T15:42:00Z"
    result = _run_format_test(iso, "24h", "24h_15:42")
    # The function formats local time; on an offset-zero system this is 15:42.
    # On a system with UTC offset, hours differ — so we assert format shape.
    assert ":" in result, f"Expected HH:MM format, got {result!r}"
    parts = result.split(":")
    assert len(parts) == 2, f"Expected exactly one colon in {result!r}"
    assert parts[0].isdigit() and parts[1].isdigit(), f"Non-digit parts in {result!r}"
    assert len(parts[0]) == 2, f"Expected zero-padded hour in {result!r}"
    assert len(parts[1]) == 2, f"Expected zero-padded minute in {result!r}"
    # No AM/PM in 24h mode
    assert "AM" not in result and "PM" not in result, f"Unexpected AM/PM in 24h: {result!r}"


def test_time_format_helper_12h() -> None:
    """formatTimestamp returns H:MM AM/PM in 12-hour format for 12h setting."""
    iso = "2026-01-15T15:42:00Z"
    result = _run_format_test(iso, "12h", "12h_15:42")
    assert "AM" in result or "PM" in result, f"Expected AM/PM in 12h result: {result!r}"
    assert ":" in result, f"Expected H:MM format in {result!r}"


def test_time_format_helper_empty_returns_empty() -> None:
    """formatTimestamp returns empty string for empty input."""
    result = _run_format_test("", "24h", "empty")
    assert result == "", f"Expected empty string, got {result!r}"


def test_time_format_helper_unparseable_returns_raw() -> None:
    """formatTimestamp returns raw input for unparseable strings."""
    result = _run_format_test("not-a-date", "24h", "unparseable")
    assert result == "not-a-date", f"Expected raw passthrough, got {result!r}"

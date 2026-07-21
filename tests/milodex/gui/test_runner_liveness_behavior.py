"""Behavioral probes for the runner-liveness paper cuts (FRONT copy + DESK panel).

Two founder-observed honesty gaps, tested trigger-and-observe style (the
pattern piloted in test_qml_load_smoke_behavior.py, harness modeled on
test_surface_shells_smoke.py's content-renders net):

FRONT liveness copy
    "N of your M strategies are working right now." rendered N from the
    paper-stage config tally — "11 working right now" while zero runners were
    alive.  The line now binds ActiveOpsState.liveCount (PID-verified running
    sessions, DESK's "N runners" source) and reads "None of your M strategies
    are running right now." when nothing runs.

DESK Active Operations default + ended labeling
    The runner panel defaulted to runners[0] in arbitrary SQL order — an
    ancient stopped session (observed: 6/22, "SESSION AGE 653h") could win
    over a live or newer one, and an ended session wore a ticking SESSION AGE.
    The read model now orders live-first / most-recently-started, and the
    panel labels an ended session "Ended <date time>".

Each test spawns a subprocess that seeds a REAL EventStore DB (and, for live
runners, holds a REAL advisory lock via the subprocess's own PID so
PID-verified liveness resolves to "running"), kicks the read-model refresh
deterministically, renders the real surface in an offscreen QQuickView, and
reads live item properties back.  All tests skip when PySide6 is missing.
"""

from __future__ import annotations

import subprocess  # noqa: S404 - mirrors sibling GUI subprocess-harness tests
import sys
from pathlib import Path

import pytest

try:
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed - skipping runner-liveness behavior tests",
)

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"


def _run(script: str, label: str) -> str:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{label} FAILED\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Subprocess harness — @TOKEN@ substitution (no .format, so literal braces in
# the embedded Python need no escaping).  Three parts:
#   _SETUP: Qt app, tmp EventStore DB, seed_run/hold_lock helpers
#   <seed>: per-test strategy_runs rows + held locks
#   _STATES_AND_VIEW: read models (active_ops/front on the REAL db),
#       deterministic _kick_refresh + drain, QQuickView render of @SURFACE@
#   <assertions>: live property reads; sys.exit(non-zero) on failure
# ---------------------------------------------------------------------------

_SETUP = r'''
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtCore import QObject as _QObjectBase
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from milodex.commands.bench import BenchCommandFacade
from milodex.core.advisory_lock import AdvisoryLock
from milodex.core.event_store import EventStore
from milodex.gui.active_ops_state import ActiveOpsState
from milodex.gui.activity_feed_state import ActivityFeedState
from milodex.gui.attention_state import AttentionState
from milodex.gui.bench_command_bridge import BenchCommandBridge
from milodex.gui.fonts import load_fonts
from milodex.gui.market_tape_state import MarketTapeState
from milodex.gui.operational_state import OperationalState
from milodex.gui.performance_state import PerformanceState
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.read_models import BenchState, FrontPageState, LedgerState
from milodex.gui.risk_throughput_state import RiskThroughputState
from milodex.gui.theme_manager import ThemeManager
from milodex.strategies.paper_runner_control import runner_lock_name

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

_root_dir = Path(tempfile.mkdtemp(prefix="milodex_liveness_probe_"))
db = _root_dir / "milodex.db"
locks_dir = _root_dir / "locks"
configs_dir = _root_dir / "configs"
locks_dir.mkdir()
configs_dir.mkdir()
EventStore(db)  # real, fully-migrated schema


def seed_run(strategy_id, session_id, started_at, ended_at=None, exit_reason=None):
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO strategy_runs "
        "(session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, '{}')",
        (session_id, strategy_id, started_at, ended_at, exit_reason),
    )
    conn.commit()
    conn.close()


_held_locks = []


def hold_lock(strategy_id):
    """Hold the runner advisory lock via THIS process's live PID so
    PID-verified liveness resolves the open session to 'running'."""
    lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
    lock.acquire()
    _held_locks.append(lock)
'''

_STATES_AND_VIEW = r'''
tm = ThemeManager()
ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(active=False, reason=None, last_triggered_at=None)


def _failing_broker():
    raise RuntimeError("liveness-probe: no broker")


op = OperationalState(
    broker_client_factory=_failing_broker,
    kill_switch_store=ks_store,
    trading_mode="paper",
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)

_ne = Path("/__nonexistent_liveness_probe__")
front = FrontPageState(db_path=db, configs_dir=configs_dir, locks_dir=locks_dir)
bench = BenchState(db_path=db, configs_dir=configs_dir)
ledger = LedgerState(db_path=db)
performance = PerformanceState(db_path=_ne, cache_dir=_ne)
risk_throughput = RiskThroughputState(db_path=_ne)
active_ops = ActiveOpsState(db_path=db, configs_dir=configs_dir, locks_dir=locks_dir)
attention = AttentionState(db_path=_ne)
market_tape = MarketTapeState(cache_dir=_ne)
activity_feed = ActivityFeedState(db_path=_ne)

facade = BenchCommandFacade(
    config_dir=configs_dir,
    locks_dir=locks_dir,
    get_trading_mode=lambda: "paper",
)
bridge = BenchCommandBridge(facade, bench_state=bench)

register_qml_types(
    theme_manager=tm,
    operational_state=op,
    front_page_state=front,
    bench_state=bench,
    ledger_state=ledger,
    performance_state=performance,
    risk_throughput_state=risk_throughput,
    active_ops_state=active_ops,
    attention_state=attention,
    market_tape_state=market_tape,
    activity_feed_state=activity_feed,
    bench_command_bridge=bridge,
)


def _drain(state):
    """Condition-based settle (mirrors test_active_ops_state._drain_pool)."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        state._thread_pool.waitForDone(50)
        QCoreApplication.processEvents()
        if state.dataStatus != "loading":
            break
    QCoreApplication.processEvents()


# Deterministic refresh BEFORE the surface loads — no polling in this harness.
active_ops._kick_refresh()
_drain(active_ops)
front._kick_refresh()
_drain(front)

view = QQuickView()
view.engine().addImportPath(@IMPORT_ROOT@)
view.setResizeMode(QQuickView.SizeRootObjectToView)
view.resize(1600, 1100)
view.setSource(QUrl.fromLocalFile(@SURFACE@))

if view.status() == QQuickView.Error:
    for e in view.errors():
        print(str(e.toString()), file=sys.stderr)
    sys.exit(2)

root = view.rootObject()
if root is None:
    print("rootObject() is None", file=sys.stderr)
    sys.exit(3)

view.show()
QTimer.singleShot(900, app.quit)
app.exec()


def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)
'''


def _build(seed: str, surface_relative: str, assertions: str) -> str:
    surface_path = _MILODEX_QML_DIR / surface_relative
    assert surface_path.exists(), f"surface missing: {surface_path}"
    view_block = _STATES_AND_VIEW.replace("@IMPORT_ROOT@", repr(str(_QML_IMPORT_ROOT))).replace(
        "@SURFACE@", repr(str(surface_path))
    )
    return _SETUP + seed + view_block + assertions


# ---------------------------------------------------------------------------
# CUT 1 — FRONT liveness copy
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_front_zero_live_runners_reads_none_running() -> None:
    """0 live runners: the lead renders prose "None", the numeric lead hides,
    and the sentence tail reads "strategies are running right now."

    Trigger: one ENDED session in the store (the pre-fix tally counted its
    paper-stage config as "working right now").
    Observe: ActiveOpsState.liveCount == 0 and the rendered FRONT line.
    """
    seed = (
        "\nseed_run('meanrev.daily.probe_a.v1', 'sess-a',"
        " '2026-06-22T13:30:00+00:00',"
        " ended_at='2026-06-22T20:22:00+00:00', exit_reason='controlled_stop')\n"
    )
    assertions = (
        "\nif int(active_ops.liveCount) != 0:\n"
        "    print('expected liveCount 0, got ' + str(active_ops.liveCount),"
        " file=sys.stderr); sys.exit(5)\n"
        "none_t = root.findChild(_QObjectBase, 'frontRunnersNone')\n"
        "count_t = root.findChild(_QObjectBase, 'frontRunnersCount')\n"
        "if none_t is None or count_t is None:\n"
        "    print('frontRunners* probe Texts not found', file=sys.stderr); sys.exit(6)\n"
        "if not none_t.property('visible'):\n"
        "    print(\"'None' lead not visible with 0 live runners\","
        " file=sys.stderr); sys.exit(7)\n"
        "if count_t.property('visible'):\n"
        "    print('numeric lead visible with 0 live runners', file=sys.stderr); sys.exit(8)\n"
        "if none_t.property('text') != 'None':\n"
        "    print('lead text mismatch: ' + repr(none_t.property('text')),"
        " file=sys.stderr); sys.exit(9)\n"
        "tails = [it for it in _walk(root)\n"
        "         if it.metaObject().className().startswith('QQuickText')\n"
        "         and (it.property('text') or '') == ' strategies are running right now.']\n"
        "if len(tails) != 1:\n"
        "    print('expected exactly one running-tail Text, got ' + str(len(tails)),"
        " file=sys.stderr); sys.exit(10)\n"
        "print('FRONT_ZERO_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(
        _build(seed, "surfaces/FrontSurface.qml", assertions),
        "FRONT zero-live copy",
    )
    assert "FRONT_ZERO_OK" in out


@_skip_no_qt
def test_front_live_runner_count_matches_read_model() -> None:
    """N>0 live runners: the numeric lead renders exactly the read model's
    PID-verified live count, and the prose "None" lead hides.

    Trigger: one open session whose advisory lock is held by this subprocess
    (a live PID), plus one ended session that must NOT count.
    """
    seed = (
        "\nseed_run('meanrev.daily.probe_live.v1', 'sess-live', '2026-07-19T13:30:00+00:00')\n"
        "hold_lock('meanrev.daily.probe_live.v1')\n"
        "seed_run('meanrev.daily.probe_done.v1', 'sess-done',"
        " '2026-07-18T13:30:00+00:00',"
        " ended_at='2026-07-18T20:05:00+00:00', exit_reason='controlled_stop')\n"
    )
    assertions = (
        "\nif int(active_ops.liveCount) != 1:\n"
        "    print('expected liveCount 1, got ' + str(active_ops.liveCount),"
        " file=sys.stderr); sys.exit(5)\n"
        "none_t = root.findChild(_QObjectBase, 'frontRunnersNone')\n"
        "count_t = root.findChild(_QObjectBase, 'frontRunnersCount')\n"
        "if none_t is None or count_t is None:\n"
        "    print('frontRunners* probe Texts not found', file=sys.stderr); sys.exit(6)\n"
        "if not count_t.property('visible'):\n"
        "    print('numeric lead not visible with a live runner', file=sys.stderr); sys.exit(7)\n"
        "if none_t.property('visible'):\n"
        "    print(\"'None' lead visible with a live runner\", file=sys.stderr); sys.exit(8)\n"
        "if str(count_t.property('text')) != str(active_ops.liveCount):\n"
        "    print('rendered count ' + repr(count_t.property('text'))\n"
        "          + ' != liveCount ' + str(active_ops.liveCount),"
        " file=sys.stderr); sys.exit(9)\n"
        "print('FRONT_LIVE_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(
        _build(seed, "surfaces/FrontSurface.qml", assertions),
        "FRONT live-count copy",
    )
    assert "FRONT_LIVE_OK" in out


# ---------------------------------------------------------------------------
# CUT 3 — DESK Active Operations default selection + ended labeling
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_desk_defaults_to_most_recent_and_labels_ended_plainly() -> None:
    """All sessions ended: the panel defaults to the most recently STARTED
    one (not the ancient session), and the age stat reads "Ended <date ...>"
    — a dated label, not a ticking SESSION AGE.
    """
    seed = (
        "\nseed_run('strat.ancient.v1', 'sess-ancient',"
        " '2026-06-22T13:30:00+00:00',"
        " ended_at='2026-06-22T20:22:00+00:00', exit_reason='controlled_stop')\n"
        "seed_run('strat.recent.v1', 'sess-recent',"
        " '2026-07-18T13:30:00+00:00',"
        " ended_at='2026-07-18T20:05:00+00:00', exit_reason='controlled_stop')\n"
    )
    assertions = (
        "\nsel = root.findChild(_QObjectBase, 'deskRunnerSelect')\n"
        "if sel is None:\n"
        "    print('deskRunnerSelect not found', file=sys.stderr); sys.exit(5)\n"
        "if sel.property('current') != 'strat.recent.v1':\n"
        "    print('default selection is ' + repr(sel.property('current'))\n"
        "          + ', expected the most recently started strat.recent.v1',"
        " file=sys.stderr); sys.exit(6)\n"
        "stat = root.findChild(_QObjectBase, 'deskSessionAgeStat')\n"
        "if stat is None:\n"
        "    print('deskSessionAgeStat not found', file=sys.stderr); sys.exit(7)\n"
        "if stat.property('k') != 'Ended':\n"
        "    print('ended session stat label is ' + repr(stat.property('k'))\n"
        "          + ', expected Ended', file=sys.stderr); sys.exit(8)\n"
        "v = str(stat.property('v'))\n"
        "if not re.match(r'^\\d{4}-\\d{2}-\\d{2} ', v):\n"
        "    print('ended stat value ' + repr(v) + ' does not lead with a date',"
        " file=sys.stderr); sys.exit(9)\n"
        "print('DESK_ENDED_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(
        _build(seed, "surfaces/DeskSurface.qml", assertions),
        "DESK ended-session default + label",
    )
    assert "DESK_ENDED_OK" in out


@_skip_no_qt
def test_desk_prefers_live_runner_and_keeps_ticking_age() -> None:
    """A live (PID-verified) runner wins the default selection over a newer
    ended session, and a LIVE session keeps the ticking "Session Age" stat.
    """
    seed = (
        "\nseed_run('strat.live.v1', 'sess-live', '2026-07-19T05:00:00+00:00')\n"
        "hold_lock('strat.live.v1')\n"
        "seed_run('strat.newer_ended.v1', 'sess-newer-ended',"
        " '2026-07-19T13:00:00+00:00',"
        " ended_at='2026-07-19T16:22:00+00:00', exit_reason='controlled_stop')\n"
    )
    assertions = (
        "\nsel = root.findChild(_QObjectBase, 'deskRunnerSelect')\n"
        "if sel is None:\n"
        "    print('deskRunnerSelect not found', file=sys.stderr); sys.exit(5)\n"
        "if sel.property('current') != 'strat.live.v1':\n"
        "    print('default selection is ' + repr(sel.property('current'))\n"
        "          + ', expected the live strat.live.v1',"
        " file=sys.stderr); sys.exit(6)\n"
        "stat = root.findChild(_QObjectBase, 'deskSessionAgeStat')\n"
        "if stat is None:\n"
        "    print('deskSessionAgeStat not found', file=sys.stderr); sys.exit(7)\n"
        "if stat.property('k') != 'Session Age':\n"
        "    print('live session stat label is ' + repr(stat.property('k'))\n"
        "          + ', expected Session Age', file=sys.stderr); sys.exit(8)\n"
        "v = str(stat.property('v'))\n"
        "if not re.match(r'^(\\d+h \\d{2}m|\\d{1,2}m)$', v):\n"
        "    print('live stat value ' + repr(v) + ' is not an age', file=sys.stderr)\n"
        "    sys.exit(9)\n"
        "print('DESK_LIVE_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(
        _build(seed, "surfaces/DeskSurface.qml", assertions),
        "DESK live-session default + age",
    )
    assert "DESK_LIVE_OK" in out

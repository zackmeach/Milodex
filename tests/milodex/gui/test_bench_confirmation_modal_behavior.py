"""Behavioral net for BenchConfirmationModal (PR 13 decompose re-aim).

These tests replace brittle source-substring assertions with real behavior
assertions, driving an actual instantiated modal in a fresh Qt process and
reading PROPERTIES / recorded bridge calls. A QML rename that silently
resolves to ``undefined`` still "loads clean" under the static smoke gate --
these tests are the proof the decompose preserved behavior, because they
exercise the modal's rendered tree and its dispatch path.

Harness shape mirrors ``tests/milodex/gui/test_desk_layout_regression.py``:
a ``QQuickView`` with ``QT_QPA_PLATFORM=offscreen``, ``.show()`` + an
event-loop pump, then a walk of the live ``QQuickItem`` tree / property reads.

The command bridge is a RECORD-ONLY fake registered under the
``Milodex.BenchCommandBridge`` singleton name. It executes nothing -- every
``propose*`` slot appends its argument to a JSON sink and returns a benign,
unblocked proposal; every ``submit*`` slot records and returns a benign
result. This lets dispatch payloads be asserted behaviorally -- e.g. that a
backtest submit proposes exactly ``{"strategy_id": ...}`` (the canonical
walk-forward params are Python-owned per P2-12: CANONICAL_BACKTEST_PARAMS
in bench_command_bridge.py, pinned in test_bench_command_bridge.py).

P2-12: the action fixtures carry the ``actionIntentPreview`` object the
production read model stamps on every menu item
(``bench_actions._compute_bench_action_menu``) -- the modal consumes the
Python-owned action-kind spec via the preview and carries no fallback
classifiers. Preview CONTENT (kind classification, submit-capability,
copy, capital flags) is pinned Python-side in
tests/milodex/gui/test_read_models.py.

Each non-vacuous assertion was confirmed to FAIL when the corresponding
behavior is broken (mutate -> watch fail -> revert); see the test docstrings.

All tests skip when PySide6 is not importable.
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
    reason="PySide6 not installed - skipping BenchConfirmationModal behavior tests",
)

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"


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
# Subprocess harness builder
#
# {body_row}/{body_action} are QML object-literal bodies for the modal's
# rowData/actionData. The harness loads a probe Item hosting one
# BenchConfirmationModal (id `modal`) in a QQuickView (so Layouts resolve and
# the scene-graph polish/render pass runs), pumps the loop, then runs
# {assertions} -- Python that reads `modal`/`root`/`RECORDS_PATH` and exits
# non-zero on failure. The fake bridge writes recorded calls to RECORDS_PATH.
# ---------------------------------------------------------------------------

_HARNESS = r'''
import os, sys, json, tempfile, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl, QTimer, QObject, Signal, Slot, Property, QMetaObject
from PySide6.QtCore import QObject as _QObjectBase
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtQml import qmlRegisterSingletonInstance

from milodex.gui.fonts import load_fonts
from milodex.gui.theme_manager import ThemeManager
from milodex.gui import qml_setup

RECORDS_PATH = pathlib.Path({records_path!r})
_records = []

def _record(method, *args):
    _records.append({{"method": method, "args": list(args)}})
    RECORDS_PATH.write_text(json.dumps(_records), encoding="utf-8")

class FakeBenchCommandBridge(QObject):
    """Record-only stand-in for BenchCommandBridge.

    Every propose* slot records its inputs and returns a benign unblocked
    proposal; every submit* slot records and returns a benign result. NOTHING
    is executed: no facade, no broker, no event store, no runner, no YAML.
    """
    submitCompleted = Signal("QVariantMap")
    submitQueued = Signal("QVariantMap")
    recentCompletionsChanged = Signal()

    _PROPOSAL = {{"proposal_id": "fake-proposal-1", "blockers": []}}
    _SYNC_OK = {{"status": "submitted", "proposal_id": "fake-proposal-1", "blockers": []}}
    _ASYNC_OK = {{"bridge_status": "queued", "proposal_id": "fake-proposal-1", "blockers": []}}

    @Slot("QVariantMap", result="QVariantMap")
    def proposeDemote(self, inputs):
        _record("proposeDemote", dict(inputs)); return dict(self._PROPOSAL)
    @Slot(str, result="QVariantMap")
    def submitDemote(self, pid):
        _record("submitDemote", pid); return dict(self._SYNC_OK)
    @Slot("QVariantMap", result="QVariantMap")
    def proposeFreezeManifest(self, inputs):
        _record("proposeFreezeManifest", dict(inputs)); return dict(self._PROPOSAL)
    @Slot(str, result="QVariantMap")
    def submitFreezeManifest(self, pid):
        _record("submitFreezeManifest", pid); return dict(self._SYNC_OK)
    @Slot("QVariantMap", result="QVariantMap")
    def proposeBacktest(self, inputs):
        _record("proposeBacktest", dict(inputs)); return dict(self._PROPOSAL)
    @Slot(str, result="QVariantMap")
    def submitBacktestAsync(self, pid):
        _record("submitBacktestAsync", pid); return dict(self._ASYNC_OK)
    @Slot("QVariantMap", result="QVariantMap")
    def proposePromoteToPaper(self, inputs):
        _record("proposePromoteToPaper", dict(inputs)); return dict(self._PROPOSAL)
    @Slot(str, result="QVariantMap")
    def submitPromoteToPaper(self, pid):
        _record("submitPromoteToPaper", pid); return dict(self._SYNC_OK)
    @Slot("QVariantMap", result="QVariantMap")
    def proposeStartPaperRunner(self, inputs):
        _record("proposeStartPaperRunner", dict(inputs)); return dict(self._PROPOSAL)
    @Slot(str, result="QVariantMap")
    def submitStartPaperRunnerAsync(self, pid):
        _record("submitStartPaperRunnerAsync", pid); return dict(self._ASYNC_OK)
    @Slot("QVariantMap", result="QVariantMap")
    def proposeStopPaperRunner(self, inputs):
        _record("proposeStopPaperRunner", dict(inputs)); return dict(self._PROPOSAL)
    @Slot(str, result="QVariantMap")
    def submitStopPaperRunnerAsync(self, pid):
        _record("submitStopPaperRunnerAsync", pid); return dict(self._ASYNC_OK)

    def _recent(self):
        return []
    recentCompletions = Property("QVariantList", _recent, notify=recentCompletionsChanged)

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

tm = ThemeManager()
qml_setup.register_qml_types(theme_manager=tm)
_fake_bridge = FakeBenchCommandBridge()
qmlRegisterSingletonInstance(
    QObject, "Milodex", 1, 0, "BenchCommandBridge", _fake_bridge
)

probe = b"""
import QtQuick
import Milodex 1.0

Item {{
    id: probeRoot
    width: 900
    height: 1200

    BenchConfirmationModal {{
        id: modalInner
        objectName: "benchConfirmationModalProbe"
        anchors.fill: parent
        rowData: ({body_row})
        actionData: ({body_action})
        open: true
    }}
}}
"""

_qml_file = pathlib.Path(tempfile.mktemp(suffix=".qml"))
_qml_file.write_bytes(probe)

view = QQuickView()
view.engine().addImportPath({import_root})
view.setResizeMode(QQuickView.SizeRootObjectToView)
view.resize(900, 1200)
view.setSource(QUrl.fromLocalFile(str(_qml_file)))

if view.status() == QQuickView.Error:
    for e in view.errors():
        print(str(e.toString()), file=sys.stderr)
    sys.exit(2)

root = view.rootObject()
if root is None:
    print("rootObject() is None", file=sys.stderr)
    sys.exit(3)

view.show()
QTimer.singleShot(700, app.quit)
app.exec()

modal = root.findChild(_QObjectBase, "benchConfirmationModalProbe")
if modal is None:
    print("modal not found by objectName", file=sys.stderr)
    sys.exit(4)

def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

def _texts():
    # Only text from items that are EFFECTIVELY visible — the inert
    # "Not wired in v1" placeholder lives in the tree as visible:false, so
    # an unfiltered walk would falsely "see" it. isVisible() reflects the
    # effective on-screen visibility (the whole ancestor chain).
    out = []
    for it in _walk(root):
        try:
            if not it.isVisible():
                continue
        except Exception:
            pass
        t = it.property("text")
        if t:
            out.append(str(t))
    return out

def _to_py(v):
    # `var` QML properties come back as QJSValue; normalise to a Python dict.
    if hasattr(v, "toVariant"):
        return v.toVariant()
    return v

def _invoke(name):
    QMetaObject.invokeMethod(modal, name)

{assertions}
'''


def _build(body_row: str, body_action: str, assertions: str, records_path: Path) -> str:
    return _HARNESS.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        body_row=body_row,
        body_action=body_action,
        assertions=assertions,
        records_path=str(records_path),
    )


# Reusable rowData / actionData literals (QML object-literal bodies). Each
# action carries the actionIntentPreview the production read model stamps on
# every menu item (P2-12: the modal consumes the Python-owned spec via the
# preview; there is no QML fallback classifier).
_ROW_PAPER = '{ "strategyId": "regime.daily.x.spy.v1", "name": "Regime", "stage": "backtest" }'
_ACTION_BACKTEST = (
    '{ "label": "Initiate Backtest", "verbClass": "invocation", '
    '"actionIntentPreview": { '
    '"actionKind": "initiate_backtest", "executable": true, "wired": true, '
    '"capitalBearing": false, "futureRecord": "backtest_run", '
    '"intentCopy": "Run canonical walk-forward backtest evidence for this strategy." } }'
)
_ACTION_DEMOTE = (
    '{ "label": "Demote to backtest", "verbClass": "directional", "targetStage": "backtest", '
    '"actionIntentPreview": { '
    '"actionKind": "demote", "executable": true, "wired": true, '
    '"capitalBearing": false, "futureRecord": "demotion_event" } }'
)
_ACTION_PROMOTE_PAPER = (
    '{ "label": "Promote to paper", "verbClass": "directional", "targetStage": "paper", '
    '"actionIntentPreview": { '
    '"actionKind": "promote", "executable": true, "wired": true, '
    '"capitalBearing": false, "futureRecord": "promotion_event" } }'
)
# Non-submit-capable, capital-bearing action (Promote to micro_live).
_ACTION_PROMOTE_MICRO_LIVE = (
    '{ "label": "Promote to micro_live", "verbClass": "directional", '
    '"targetStage": "micro_live", '
    '"actionIntentPreview": { '
    '"actionKind": "promote", "executable": false, "wired": false, '
    '"capitalBearing": true, "futureRecord": "promotion_event" } }'
)
_ROW_BACKTEST_STAGE = (
    '{ "strategyId": "regime.daily.x.spy.v1", "name": "Regime", "stage": "backtest", '
    '"evidenceRunId": "run-1" }'
)


# ===========================================================================
# Canonical backtest params -- Python-owned (P2-12). The modal proposes ONLY
# the strategy id; CANONICAL_BACKTEST_PARAMS in bench_command_bridge.py fills
# the canonical walk-forward shape (pinned in test_bench_command_bridge.py::
# test_canonical_backtest_params_are_python_owned and
# test_propose_backtest_returns_dict_and_caches_proposal, which proposes with
# only a strategy_id and asserts the canonical defaults).
# ===========================================================================


@_skip_no_qt
def test_backtest_submit_proposes_strategy_id_only(tmp_path) -> None:
    """Driving the submit dispatch for a backtest action makes the modal call
    proposeBacktest with EXACTLY {"strategy_id": ...} -- the canonical
    walk-forward evidence shape is Python-owned (P2-12), so QML must not
    spread a local param table into the proposal.

    NON-VACUOUS: if QML regrows a local canonical-param table (or drops the
    strategy id), the recorded proposeBacktest payload stops matching the
    exact single-key dict below and this fails.
    """
    records = tmp_path / "records.json"
    assertions = (
        "_invoke('_dispatchBacktestSubmit')\n"
        "recs = json.loads(RECORDS_PATH.read_text(encoding='utf-8')) "
        "if RECORDS_PATH.exists() else []\n"
        "proposes = [r for r in recs if r['method'] == 'proposeBacktest']\n"
        "if len(proposes) != 1:\n"
        "    print('expected exactly 1 proposeBacktest, got ' + str(len(proposes)) "
        "+ ' records=' + json.dumps(recs), file=sys.stderr); sys.exit(5)\n"
        "payload = proposes[0]['args'][0]\n"
        "expected = {'strategy_id': 'regime.daily.x.spy.v1'}\n"
        "if payload != expected:\n"
        "    print('PAYLOAD MISMATCH expected=' + json.dumps(expected) "
        "+ ' got=' + json.dumps(payload), file=sys.stderr); sys.exit(6)\n"
        "subs = [r for r in recs if r['method'] == 'submitBacktestAsync']\n"
        "if len(subs) != 1:\n"
        "    print('expected exactly 1 submitBacktestAsync, got ' + str(len(subs)), "
        "file=sys.stderr); sys.exit(7)\n"
        "print('STRATEGY_ID_ONLY_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(
        _build(_ROW_PAPER, _ACTION_BACKTEST, assertions, records),
        "backtest proposes strategy id only",
    )
    assert "STRATEGY_ID_ONLY_OK" in out


# ===========================================================================
# Section rendering -- was: grep for the seven ALL-CAPS section labels.
# Now: the live tree renders those labelled sections.
# ===========================================================================


@_skip_no_qt
def test_modal_renders_labelled_sections(tmp_path) -> None:
    """The instantiated modal renders the seven Intent Packet sections as
    on-screen labelled text. Walks the live item tree and asserts each
    section label string is present in a rendered Text item.

    Also asserts the INTENT PACKET prose for the backtest action renders
    verbatim ("Run canonical walk-forward backtest evidence for this
    strategy.") — this is the behavioral home of the former
    test_qml_load_smoke substring pin on that copy (the smoke test now only
    keeps the bridge socket-method names for backtest, not the copy).

    NON-VACUOUS: deleting a SectionLabel instance (or breaking its `label`
    binding so it resolves empty) drops the string from the rendered tree.
    Verified by removing the "FUTURE RECORD" SectionLabel -> test failed.
    Reworking the backtest _intentCopy string also drops it from the tree.
    """
    records = tmp_path / "records.json"
    assertions = (
        "texts = _texts()\n"
        "required = ['ACTION', 'INTENT PACKET', 'CURRENT SNAPSHOT', "
        "'WOULD EVENTUALLY REQUIRE', 'FUTURE RECORD', 'COMMAND DRAFT PREVIEW', "
        "'SAFETY BOUNDARY']\n"
        "missing = [s for s in required if s not in texts]\n"
        "if missing:\n"
        "    print('MISSING SECTION LABELS: ' + ', '.join(missing) "
        "+ ' rendered=' + json.dumps(texts), file=sys.stderr); sys.exit(5)\n"
        "intent = 'Run canonical walk-forward backtest evidence for this strategy.'\n"
        "if intent not in texts:\n"
        "    print('MISSING BACKTEST INTENT COPY rendered=' + json.dumps(texts), "
        "file=sys.stderr); sys.exit(6)\n"
        "print('SECTIONS_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(
        _build(_ROW_PAPER, _ACTION_BACKTEST, assertions, records),
        "labelled sections render",
    )
    assert "SECTIONS_OK" in out


@_skip_no_qt
def test_command_draft_preview_renders_state_and_blockers(tmp_path) -> None:
    """For a NON-submit-capable action (Promote to micro_live), the COMMAND
    DRAFT PREVIEW section renders the not_submittable_v1 / not_validated_v1
    state plus the draft blockers and the inert banner copy -- the
    operator-facing draft-preview boundary.

    NON-VACUOUS: if the draft-preview DetailRows or the blocked-by Repeater
    stop rendering, these strings disappear from the live tree.
    """
    records = tmp_path / "records.json"
    row = '{ "strategyId": "edge.x.y.z.v1", "name": "Edge", "stage": "paper" }'
    action = _ACTION_PROMOTE_MICRO_LIVE
    assertions = (
        "texts = _texts()\n"
        "required = ['COMMAND DRAFT PREVIEW', 'NOT SUBMITTABLE', "
        "'not_submittable_v1', 'not_validated_v1', "
        "'Bench v1 command submission is not wired']\n"
        "missing = [s for s in required if s not in texts]\n"
        "if missing:\n"
        "    print('MISSING DRAFT-PREVIEW STATE: ' + ', '.join(missing) "
        "+ ' rendered=' + json.dumps(texts), file=sys.stderr); sys.exit(5)\n"
        "print('DRAFT_PREVIEW_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(_build(row, action, assertions, records), "draft preview state")
    assert "DRAFT_PREVIEW_OK" in out


# ===========================================================================
# Submit-affordance behavior -- was: grep for helper/property NAME pins.
# Now: submit-capable kinds show the submit label; non-capable show the
# inert "Not wired in v1" placeholder.
# ===========================================================================


@_skip_no_qt
def test_submit_capable_action_shows_submit_affordance(tmp_path) -> None:
    """A submit-capable action (backtest) renders the submit button label
    ("Run backtest") and is flagged _isSubmitCapable; the inert "Not wired
    in v1" placeholder is NOT shown.

    NON-VACUOUS: if the submit-capable predicate breaks, the modal falls
    back to the inert placeholder and "Run backtest" disappears.
    """
    records = tmp_path / "records.json"
    assertions = (
        "if modal.property('_isSubmitCapable') is not True:\n"
        "    print('expected _isSubmitCapable True for backtest', file=sys.stderr); sys.exit(5)\n"
        "texts = _texts()\n"
        "if 'Run backtest' not in texts:\n"
        "    print('submit label missing rendered=' + json.dumps(texts), "
        "file=sys.stderr); sys.exit(6)\n"
        "if 'Not wired in v1' in texts:\n"
        "    print('inert placeholder shown for submit-capable action', "
        "file=sys.stderr); sys.exit(7)\n"
        "print('SUBMIT_AFFORDANCE_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(_build(_ROW_PAPER, _ACTION_BACKTEST, assertions, records), "submit affordance")
    assert "SUBMIT_AFFORDANCE_OK" in out


@_skip_no_qt
def test_non_submit_capable_action_shows_inert_placeholder(tmp_path) -> None:
    """A non-submit-capable action (Promote to micro_live) renders the inert
    "Not wired in v1" placeholder and is NOT flagged _isSubmitCapable.

    NON-VACUOUS: if a non-capable kind were wrongly classed submit-capable,
    the inert placeholder would vanish.
    """
    records = tmp_path / "records.json"
    row = '{ "strategyId": "edge.x.y.z.v1", "name": "Edge", "stage": "paper" }'
    action = _ACTION_PROMOTE_MICRO_LIVE
    assertions = (
        "if modal.property('_isSubmitCapable') is not False:\n"
        "    print('expected _isSubmitCapable False for micro_live promote', "
        "file=sys.stderr); sys.exit(5)\n"
        "texts = _texts()\n"
        "if 'Not wired in v1' not in texts:\n"
        "    print('inert placeholder missing rendered=' + json.dumps(texts), "
        "file=sys.stderr); sys.exit(6)\n"
        "print('INERT_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(_build(row, action, assertions, records), "inert placeholder")
    assert "INERT_OK" in out


# ===========================================================================
# Input-requirement behavior -- was: grep for placeholder copy + helper names.
# Now: stage-walkback requires a reason; promote-to-paper requires
# recommendation + known-risk; blank input refuses (no propose call).
# ===========================================================================


@_skip_no_qt
def test_stage_walkback_blank_reason_refuses_without_proposing(tmp_path) -> None:
    """A demote with a blank reason refuses the submit (sets an error
    message) and never calls proposeDemote -- the audit-record gate.

    NON-VACUOUS: if the reason gate is removed, proposeDemote fires with an
    empty reason and the records list is non-empty.

    Also asserts the reason input's placeholder copy ("Reason required for
    the audit record") renders in the live tree for a blank-reason demote --
    the behavioral home of the former test_qml_load_smoke substring pin on
    that placeholder string.
    """
    records = tmp_path / "records.json"
    assertions = (
        "texts = _texts()\n"
        "if 'Reason required for the audit record' not in texts:\n"
        "    print('reason placeholder copy missing rendered=' + json.dumps(texts), "
        "file=sys.stderr); sys.exit(4)\n"
        "modal.setProperty('_reasonText', '')\n"
        "_invoke('_dispatchDemoteSubmit')\n"
        "recs = json.loads(RECORDS_PATH.read_text(encoding='utf-8')) "
        "if RECORDS_PATH.exists() else []\n"
        "proposes = [r for r in recs if r['method'] == 'proposeDemote']\n"
        "if proposes:\n"
        "    print('proposeDemote fired with blank reason: ' + json.dumps(recs), "
        "file=sys.stderr); sys.exit(5)\n"
        "err = modal.property('_submitErrorMessage')\n"
        "if not err:\n"
        "    print('expected a refusal error message for blank reason', "
        "file=sys.stderr); sys.exit(6)\n"
        "print('REASON_GATE_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(_build(_ROW_PAPER, _ACTION_DEMOTE, assertions, records), "reason gate")
    assert "REASON_GATE_OK" in out


@_skip_no_qt
def test_demote_with_reason_proposes_with_reason(tmp_path) -> None:
    """A demote with a non-blank reason proposes through the bridge carrying
    the strategy id, target stage, and the operator reason.

    NON-VACUOUS: if the demote dispatch stops forwarding the reason/target,
    the recorded payload mismatches.
    """
    records = tmp_path / "records.json"
    assertions = (
        "modal.setProperty('_reasonText', 'walk back per audit')\n"
        "_invoke('_dispatchDemoteSubmit')\n"
        "recs = json.loads(RECORDS_PATH.read_text(encoding='utf-8')) "
        "if RECORDS_PATH.exists() else []\n"
        "proposes = [r for r in recs if r['method'] == 'proposeDemote']\n"
        "if len(proposes) != 1:\n"
        "    print('expected 1 proposeDemote got ' + str(len(proposes)) "
        "+ ' ' + json.dumps(recs), file=sys.stderr); sys.exit(5)\n"
        "payload = proposes[0]['args'][0]\n"
        "expected = {'strategy_id': 'regime.daily.x.spy.v1', 'to_stage': 'backtest', "
        "'reason': 'walk back per audit'}\n"
        "if payload != expected:\n"
        "    print('DEMOTE PAYLOAD MISMATCH expected=' + json.dumps(expected) "
        "+ ' got=' + json.dumps(payload), file=sys.stderr); sys.exit(6)\n"
        "print('DEMOTE_PAYLOAD_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(_build(_ROW_PAPER, _ACTION_DEMOTE, assertions, records), "demote payload")
    assert "DEMOTE_PAYLOAD_OK" in out


@_skip_no_qt
def test_promote_to_paper_blank_evidence_refuses(tmp_path) -> None:
    """Promote-to-paper with blank recommendation/known-risk refuses without
    proposing -- the operator-evidence gate.

    NON-VACUOUS: clearing both fields and firing the dispatch must not call
    proposePromoteToPaper; if the gate is removed, a propose record appears.

    Also asserts the two operator-evidence placeholders ("Recommendation
    required", "Known risk required") render in the live tree -- the
    behavioral home of the former test_qml_load_smoke substring pins on
    those placeholder strings.
    """
    records = tmp_path / "records.json"
    assertions = (
        "texts = _texts()\n"
        "missing = [s for s in ['Recommendation required', 'Known risk required'] "
        "if s not in texts]\n"
        "if missing:\n"
        "    print('evidence placeholder copy missing: ' + ', '.join(missing) "
        "+ ' rendered=' + json.dumps(texts), file=sys.stderr); sys.exit(4)\n"
        "modal.setProperty('_recommendationText', '')\n"
        "modal.setProperty('_knownRiskText', '')\n"
        "_invoke('_dispatchPromoteToPaperSubmit')\n"
        "recs = json.loads(RECORDS_PATH.read_text(encoding='utf-8')) "
        "if RECORDS_PATH.exists() else []\n"
        "proposes = [r for r in recs if r['method'] == 'proposePromoteToPaper']\n"
        "if proposes:\n"
        "    print('proposePromoteToPaper fired with blank evidence: ' + json.dumps(recs), "
        "file=sys.stderr); sys.exit(5)\n"
        "err = modal.property('_submitErrorMessage')\n"
        "if not err:\n"
        "    print('expected refusal error for blank evidence', file=sys.stderr); sys.exit(6)\n"
        "print('EVIDENCE_GATE_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(
        _build(_ROW_BACKTEST_STAGE, _ACTION_PROMOTE_PAPER, assertions, records),
        "promote evidence gate",
    )
    assert "EVIDENCE_GATE_OK" in out


# ===========================================================================
# Blocker surfacing -- was: grep for _blockerSummary + "Blocked -- not
# submitted:". Now: a proposal carrying TWO blockers surfaces BOTH messages.
# ===========================================================================


@_skip_no_qt
def test_blocked_proposal_surfaces_all_blockers(tmp_path) -> None:
    """When the bridge returns a proposal with two blockers, the modal's
    error message contains BOTH messages (the 2026-05-29 regression: only
    the first blocker was shown).

    Uses a fake bridge variant whose proposeBacktest returns two blockers.
    NON-VACUOUS: if _blockerSummary regressed to blockers[0] only, the
    second message would be absent.
    """
    records = tmp_path / "records.json"
    # Override the fake proposeBacktest to return two blockers.
    override = (
        "def _two_blocker_propose(self, inputs):\n"
        "    _record('proposeBacktest', dict(inputs))\n"
        "    return {'proposal_id': 'fake-proposal-1', 'blockers': [\n"
        "        {'reason_code': 'broker_unreachable', 'message': 'Broker is unreachable.'},\n"
        "        {'reason_code': 'reconciliation_drift', "
        "'message': 'Reconciliation drift detected.'},\n"
        "    ]}\n"
        "FakeBenchCommandBridge.proposeBacktest = "
        "Slot('QVariantMap', result='QVariantMap')(_two_blocker_propose)\n"
    )
    assertions = (
        "_invoke('_dispatchBacktestSubmit')\n"
        "err = modal.property('_submitErrorMessage') or ''\n"
        "if 'Broker is unreachable.' not in err or 'Reconciliation drift detected.' not in err:\n"
        "    print('ERROR MESSAGE MISSING A BLOCKER: ' + repr(err), file=sys.stderr); sys.exit(5)\n"
        "if 'Blocked' not in err:\n"
        "    print('error not framed as a refusal: ' + repr(err), file=sys.stderr); sys.exit(6)\n"
        "print('ALL_BLOCKERS_OK')\n"
        "sys.exit(0)\n"
    )
    script = _build(_ROW_PAPER, _ACTION_BACKTEST, assertions, records)
    # Inject the override just before the QGuiApplication line so the class is
    # patched before any instance is registered.
    marker = "app = QGuiApplication.instance() or QGuiApplication(sys.argv)"
    script = script.replace(marker, override + marker, 1)
    out = _run(script, "all blockers surfaced")
    assert "ALL_BLOCKERS_OK" in out

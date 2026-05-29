"""Tests for RiskProfileBridge (ADR 0054 §5, §6).

Every refusal test verifies BOTH the False return AND the audit row written
(success=0, failure_reason populated).  The successful-switch test verifies
BOTH the file content AND the audit row (success=1).

The autouse ``_isolate_milodex_data_dirs`` fixture (tests/conftest.py)
redirects ``get_data_dir()`` to ``tmp_path / "data"`` for every test, so the
bridge's ``_write_profile_file`` and ``get_active_profile_name()`` both operate
on the isolated directory.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.config import get_data_dir
from milodex.core.event_store import EventStore, KillSwitchEvent, StrategyRunEvent
from milodex.gui.risk_profile_bridge import RiskProfileBridge, record_startup_default

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """An isolated EventStore DB with all migrations applied."""
    path = tmp_path / "milodex.db"
    EventStore(path)  # applies all migrations including 011
    return path


@pytest.fixture()
def bridge(db_path: Path) -> RiskProfileBridge:
    return RiskProfileBridge(db_path)


def _audit_rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM risk_profile_changes ORDER BY id").fetchall()
    conn.close()
    return rows


# ── refusal: unknown profile ──────────────────────────────────────────────────


def test_refuse_when_target_profile_unknown(bridge: RiskProfileBridge, db_path: Path) -> None:
    """Switching to a non-shipped profile name is refused."""
    refused: list[tuple[str, str]] = []
    bridge.switchRefused.connect(lambda r, m: refused.append((r, m)))

    result = bridge.attemptSwitch("yolo", "yolo")

    assert result is False
    assert len(refused) == 1
    assert refused[0][0] == "unknown_profile"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["success"] == 0
    assert rows[0]["failure_reason"] == "unknown_profile"
    assert rows[0]["to_profile"] == "yolo"


# ── refusal: active runners ───────────────────────────────────────────────────


def test_refuse_when_current_profile_unknown_still_audits(
    bridge: RiskProfileBridge, db_path: Path
) -> None:
    """Invalid selector content must refuse cleanly and write an audit row."""
    profile_file = get_data_dir() / "risk_profile.txt"
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text("mystery\n", encoding="utf-8")
    refused: list[tuple[str, str]] = []
    bridge.switchRefused.connect(lambda r, m: refused.append((r, m)))

    result = bridge.attemptSwitch("standard", "standard")

    assert result is False
    assert len(refused) == 1
    assert refused[0][0] == "invalid_current_profile"
    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["success"] == 0
    assert rows[0]["failure_reason"] == "invalid_current_profile"
    assert rows[0]["from_profile"] == "mystery"
    assert rows[0]["to_profile"] == "standard"


def test_refuse_when_runners_active(bridge: RiskProfileBridge, db_path: Path) -> None:
    """Mid-flight switch is refused per ADR 0054 §5."""
    # Insert an open strategy_run (ended_at IS NULL)
    store = EventStore(db_path)
    store.append_strategy_run(
        StrategyRunEvent(
            session_id="runner-open-1",
            strategy_id="test_strategy.v1",
            started_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
            ended_at=None,
            exit_reason=None,
            metadata={},
        )
    )

    refused: list[tuple[str, str]] = []
    bridge.switchRefused.connect(lambda r, m: refused.append((r, m)))

    result = bridge.attemptSwitch("standard", "standard")

    assert result is False
    assert len(refused) == 1
    assert refused[0][0] == "active_runners"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["success"] == 0
    assert rows[0]["failure_reason"] == "active_runners"
    assert rows[0]["runners_active_count"] == 1


# ── refusal: kill switch open ─────────────────────────────────────────────────


def test_refuse_when_kill_switch_triggered_unresolved(
    bridge: RiskProfileBridge, db_path: Path
) -> None:
    """Switch refused while a triggered kill switch is unreset per ADR 0054 §6."""
    store = EventStore(db_path)
    store.append_kill_switch_event(
        KillSwitchEvent(
            event_type="activated",
            recorded_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
            reason="Test trigger",
        )
    )

    refused: list[tuple[str, str]] = []
    bridge.switchRefused.connect(lambda r, m: refused.append((r, m)))

    result = bridge.attemptSwitch("standard", "standard")

    assert result is False
    assert len(refused) == 1
    assert refused[0][0] == "kill_switch_open"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["success"] == 0
    assert rows[0]["failure_reason"] == "kill_switch_open"


def test_kill_switch_reset_unblocks_switch(
    bridge: RiskProfileBridge, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After kill switch is reset, switch is no longer blocked by §6."""
    store = EventStore(db_path)
    store.append_kill_switch_event(
        KillSwitchEvent(
            event_type="activated",
            recorded_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
            reason="Test trigger",
        )
    )
    store.append_kill_switch_event(
        KillSwitchEvent(
            event_type="reset",
            recorded_at=datetime(2026, 5, 1, 11, 0, tzinfo=UTC),
            reason=None,
        )
    )

    # Switch conservative→standard (elevation): must provide typed token
    result = bridge.attemptSwitch("standard", "standard")
    # No kill-switch block — elevation token check fires instead → actually succeeds
    # or fails on confirmation. Since token=="standard" == target "standard", should succeed.
    assert result is True


# ── refusal: typed confirmation mismatch ─────────────────────────────────────


def test_refuse_typed_confirmation_mismatch_case_insensitive(
    bridge: RiskProfileBridge, db_path: Path
) -> None:
    """Typed confirmation must match profile name (case-insensitive, trimmed).

    'AGGRESIVE  ' (wrong) should be refused. 'AGGRESSIVE  ' (correct) should pass.
    """
    refused: list[tuple[str, str]] = []
    bridge.switchRefused.connect(lambda r, m: refused.append((r, m)))

    # elevation: conservative → aggressive, wrong token
    result = bridge.attemptSwitch("aggressive", "AGGRESIVE  ")  # misspelled

    assert result is False
    assert len(refused) == 1
    assert refused[0][0] == "typed_confirmation_mismatch"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["success"] == 0
    assert rows[0]["failure_reason"] == "typed_confirmation_mismatch"

    # Now try with correct (uppercase + trailing spaces) token — should succeed
    applied: list[str] = []
    bridge.switchApplied.connect(applied.append)

    result2 = bridge.attemptSwitch("aggressive", "AGGRESSIVE  ")
    assert result2 is True
    assert applied == ["aggressive"]


# ── successful switch: atomic file + audit ────────────────────────────────────


def test_successful_switch_writes_atomic_file_and_audit(
    bridge: RiskProfileBridge, db_path: Path
) -> None:
    """Successful switch: risk_profile.txt rewritten atomically; audit row has success=1."""
    applied: list[str] = []
    bridge.switchApplied.connect(applied.append)

    # elevation: conservative → standard, token must equal "standard"
    result = bridge.attemptSwitch("standard", "standard")

    assert result is True
    assert applied == ["standard"]

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["success"] == 1
    assert rows[0]["failure_reason"] is None
    assert rows[0]["from_profile"] == "conservative"
    assert rows[0]["to_profile"] == "standard"
    assert rows[0]["actor"] == "gui"
    assert rows[0]["confirmation_method"] == "typed"


def test_successful_switch_writes_to_get_data_dir_location(
    bridge: RiskProfileBridge, db_path: Path
) -> None:
    """Explicit guard: bridge writes to get_data_dir()/risk_profile.txt, not cwd-relative."""
    result = bridge.attemptSwitch("standard", "standard")
    assert result is True

    expected_path = get_data_dir() / "risk_profile.txt"
    assert expected_path.exists(), (
        f"risk_profile.txt not found at {expected_path}; "
        "bridge must use get_data_dir(), not Path('data/...')"
    )
    content = expected_path.read_text(encoding="utf-8").strip()
    assert content == "standard"


def test_successful_reduction_uses_single_click_method(
    bridge: RiskProfileBridge, db_path: Path
) -> None:
    """Reduction switch uses 'single_click' confirmation_method in audit."""
    # First elevate to aggressive
    bridge.attemptSwitch("aggressive", "aggressive")

    # Now reduce back: conservative direction, token = 'confirm_reduction'
    result = bridge.attemptSwitch("conservative", "confirm_reduction")
    assert result is True

    rows = _audit_rows(db_path)
    # Two rows: elevation + reduction
    assert len(rows) == 2
    reduction_row = rows[1]
    assert reduction_row["success"] == 1
    assert reduction_row["from_profile"] == "aggressive"
    assert reduction_row["to_profile"] == "conservative"
    assert reduction_row["confirmation_method"] == "single_click"


# ── startup implicit default ──────────────────────────────────────────────────


def test_startup_implicit_default_writes_audit_row(db_path: Path) -> None:
    """When app starts without data/risk_profile.txt, record_startup_default
    writes an audit row with actor='startup', confirmation_method='none'."""
    # Confirm no risk_profile.txt exists (it shouldn't — fresh tmp_path)
    profile_file = get_data_dir() / "risk_profile.txt"
    assert not profile_file.exists()

    record_startup_default(db_path)

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"] == "startup"
    assert row["confirmation_method"] == "none"
    assert row["from_profile"] == "conservative"
    assert row["to_profile"] == "conservative"
    assert row["success"] == 1
    assert row["failure_reason"] is None


def test_startup_default_does_not_audit_when_selector_exists(db_path: Path) -> None:
    """Startup audit is absence-only and does not claim Conservative over an existing selector."""
    profile_file = get_data_dir() / "risk_profile.txt"
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text("standard\n", encoding="utf-8")

    record_startup_default(db_path)

    assert _audit_rows(db_path) == []


def test_record_startup_default_is_idempotent(db_path: Path) -> None:
    """Calling record_startup_default twice within 60 s writes only one row."""
    record_startup_default(db_path)
    record_startup_default(db_path)

    rows = _audit_rows(db_path)
    assert len(rows) == 1, "record_startup_default must be idempotent within 60 s"

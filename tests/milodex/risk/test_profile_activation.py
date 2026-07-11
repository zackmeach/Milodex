"""Tests for ``reconcile_profile_against_audit`` (P2-06).

The active-profile file (``data/risk_profile.txt``) drives runtime behavior;
``risk_profile_changes`` (migration 011) is the audit trail. A hand-edit of the
file diverges the two with no audit row. The reconcile function surfaces that
divergence — informational only, no enforcement change.

The autouse ``_isolate_milodex_data_dirs`` fixture (tests/conftest.py)
redirects ``get_data_dir()`` to ``tmp_path / "data"``, so the profile file
written here is the one ``get_active_profile_name()`` reads.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.config import get_data_dir
from milodex.core.event_store import EventStore, KillSwitchEvent, StrategyRunEvent
from milodex.risk.profile_activation import (
    RiskProfileActivationService,
    reconcile_profile_against_audit,
)

# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """An isolated EventStore DB with all migrations applied (incl. 011)."""
    path = tmp_path / "milodex.db"
    EventStore(path)
    return path


def _write_profile_file(name: str) -> None:
    profile_file = get_data_dir() / "risk_profile.txt"
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text(name + "\n", encoding="utf-8")


def _append_audit_row(db_path: Path, *, to_profile: str, success: int = 1) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO risk_profile_changes
               (recorded_at, from_profile, to_profile, actor,
                confirmation_method, context_mode, runners_active_count,
                success, failure_reason)
               VALUES (?, ?, ?, 'gui', 'typed', 'paper', 0, ?, ?)""",
            (
                datetime.now(UTC).isoformat(),
                "conservative",
                to_profile,
                success,
                None if success else "test_refusal",
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── divergence cases ──────────────────────────────────────────────────────────


def test_file_diverges_from_latest_successful_audit(db_path: Path) -> None:
    """File says aggressive, latest successful audit says conservative → divergence."""
    _append_audit_row(db_path, to_profile="conservative")
    _write_profile_file("aggressive")

    divergence = reconcile_profile_against_audit(db_path)

    assert divergence is not None
    assert divergence.file_profile == "aggressive"
    assert divergence.latest_audit_profile == "conservative"
    assert divergence.file_profile_known is True
    assert "'aggressive'" in divergence.message
    assert "'conservative'" in divergence.message
    assert "Informational only" in divergence.message


def test_file_with_no_audit_history_is_divergent(db_path: Path) -> None:
    """A non-default file with no audit rows ever has no audited provenance."""
    _write_profile_file("standard")

    divergence = reconcile_profile_against_audit(db_path)

    assert divergence is not None
    assert divergence.file_profile == "standard"
    assert divergence.latest_audit_profile is None
    assert "no successful activation" in divergence.message


def test_unknown_profile_name_is_divergent_with_fallback_note(db_path: Path) -> None:
    """Unknown name in the file diverges and notes the conservative runtime fallback."""
    _append_audit_row(db_path, to_profile="conservative")
    _write_profile_file("yolo")

    divergence = reconcile_profile_against_audit(db_path)

    assert divergence is not None
    assert divergence.file_profile == "yolo"
    assert divergence.file_profile_known is False
    assert "falls back to" in divergence.message
    assert "'conservative'" in divergence.message


# ── clean cases ───────────────────────────────────────────────────────────────


def test_file_matching_latest_audit_is_clean(db_path: Path) -> None:
    _append_audit_row(db_path, to_profile="aggressive")
    _write_profile_file("aggressive")

    assert reconcile_profile_against_audit(db_path) is None


def test_no_audit_rows_and_no_file_is_clean_default(db_path: Path) -> None:
    """Implicit Conservative default — file absent, no audit history → not a divergence."""
    assert not (get_data_dir() / "risk_profile.txt").exists()

    assert reconcile_profile_against_audit(db_path) is None


def test_no_audit_rows_and_explicit_conservative_file_is_clean(db_path: Path) -> None:
    _write_profile_file("conservative")

    assert reconcile_profile_against_audit(db_path) is None


def test_refused_audit_rows_are_ignored(db_path: Path) -> None:
    """Only successful rows count: a later refused switch must not flag the file."""
    _append_audit_row(db_path, to_profile="standard")
    _append_audit_row(db_path, to_profile="aggressive", success=0)
    _write_profile_file("standard")

    assert reconcile_profile_against_audit(db_path) is None


# ── RiskProfileActivationService.attempt_switch (direct, no Qt) ────────────────
#
# Mirrors tests/milodex/gui/test_risk_profile_bridge.py and
# test_risk_office_drawer.py:225 one level down: those exercise attempt_switch
# only through the Qt bridge/QML surface. These tests hit the service directly
# so core risk-policy branch coverage does not require PySide6.


def _audit_rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM risk_profile_changes ORDER BY id").fetchall()
    conn.close()
    return rows


def test_attempt_switch_refuses_unknown_target_profile(db_path: Path) -> None:
    """Line 53-59: a non-shipped target profile name is refused, with an audit row."""
    service = RiskProfileActivationService(db_path)

    result = service.attempt_switch("yolo", "yolo")

    assert result.applied is False
    assert result.reason_code == "unknown_profile"
    assert result.from_profile == "conservative"
    assert result.to_profile == "yolo"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["success"] == 0
    assert rows[0]["failure_reason"] == "unknown_profile"
    assert rows[0]["to_profile"] == "yolo"


def test_attempt_switch_refuses_invalid_current_profile(db_path: Path) -> None:
    """Line 61-70: an unrecognized selector on disk refuses the switch cleanly."""
    _write_profile_file("mystery")
    service = RiskProfileActivationService(db_path)

    result = service.attempt_switch("standard", "standard")

    assert result.applied is False
    assert result.reason_code == "invalid_current_profile"
    assert result.from_profile == "mystery"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["failure_reason"] == "invalid_current_profile"
    assert rows[0]["from_profile"] == "mystery"
    assert rows[0]["to_profile"] == "standard"


def test_attempt_switch_refuses_when_runners_active(db_path: Path) -> None:
    """Line 72-80: a mid-flight switch is refused per ADR 0054 Section 5."""
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
    service = RiskProfileActivationService(db_path)

    result = service.attempt_switch("standard", "standard")

    assert result.applied is False
    assert result.reason_code == "active_runners"
    assert result.runners_active_count == 1

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["failure_reason"] == "active_runners"
    assert rows[0]["runners_active_count"] == 1


def test_attempt_switch_refuses_when_kill_switch_open(db_path: Path) -> None:
    """Line 82-91: a triggered, unresolved kill switch blocks the switch per Section 6."""
    store = EventStore(db_path)
    store.append_kill_switch_event(
        KillSwitchEvent(
            event_type="activated",
            recorded_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
            reason="Test trigger",
        )
    )
    service = RiskProfileActivationService(db_path)

    result = service.attempt_switch("standard", "standard")

    assert result.applied is False
    assert result.reason_code == "kill_switch_open"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["failure_reason"] == "kill_switch_open"


def test_attempt_switch_refuses_elevation_confirmation_mismatch(db_path: Path) -> None:
    """Line 93-101: elevation (conservative -> aggressive) requires a typed token
    equal to the target profile name; anything else is refused."""
    service = RiskProfileActivationService(db_path)

    result = service.attempt_switch("aggressive", "wrong_token")

    assert result.applied is False
    assert result.reason_code == "typed_confirmation_mismatch"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["failure_reason"] == "typed_confirmation_mismatch"


def test_attempt_switch_refuses_reduction_confirmation_missing(db_path: Path) -> None:
    """Line 103-110: reduction (aggressive -> conservative) requires the fixed
    'confirm_reduction' token; anything else is refused."""
    service = RiskProfileActivationService(db_path)
    # Elevate to aggressive first so the next switch is a reduction.
    elevated = service.attempt_switch("aggressive", "aggressive")
    assert elevated.applied is True

    result = service.attempt_switch("conservative", "wrong_token")

    assert result.applied is False
    assert result.reason_code == "reduction_confirmation_missing"

    rows = _audit_rows(db_path)
    assert len(rows) == 2
    assert rows[1]["failure_reason"] == "reduction_confirmation_missing"


def test_attempt_switch_success_writes_file_and_audit(db_path: Path) -> None:
    """Line 113-128: a successful switch applies, rewrites risk_profile.txt, and
    writes a success=1 audit row."""
    service = RiskProfileActivationService(db_path)

    result = service.attempt_switch("standard", "standard")

    assert result.applied is True
    assert result.from_profile == "conservative"
    assert result.to_profile == "standard"
    assert result.confirmation_method == "typed"

    profile_file = get_data_dir() / "risk_profile.txt"
    assert profile_file.read_text(encoding="utf-8").strip() == "standard"

    rows = _audit_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["success"] == 1
    assert rows[0]["failure_reason"] is None
    assert rows[0]["from_profile"] == "conservative"
    assert rows[0]["to_profile"] == "standard"
    assert rows[0]["actor"] == "gui"
    assert rows[0]["confirmation_method"] == "typed"

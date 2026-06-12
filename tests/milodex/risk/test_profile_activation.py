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
from milodex.core.event_store import EventStore
from milodex.risk.profile_activation import reconcile_profile_against_audit

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

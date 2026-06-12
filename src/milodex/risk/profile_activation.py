"""Risk-profile activation and audit policy (ADR 0054)."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from milodex.config import get_data_dir
from milodex.core.event_store import EventStore
from milodex.execution.state import KillSwitchStateStore
from milodex.risk.config import get_active_profile_name

logger = logging.getLogger(__name__)

KNOWN_PROFILES: tuple[str, ...] = ("conservative", "standard", "aggressive")
RISK_ORDER: dict[str, int] = {"conservative": 0, "standard": 1, "aggressive": 2}


@dataclass(frozen=True)
class RiskProfileSwitchResult:
    """Outcome of a risk-profile switch attempt."""

    applied: bool
    from_profile: str
    to_profile: str
    confirmation_method: str
    reason_code: str | None = None
    message: str | None = None
    runners_active_count: int = 0


class RiskProfileActivationService:
    """Non-Qt owner for bounded risk-profile activation policy."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def active_profile_name(self) -> str:
        return get_active_profile_name()

    def attempt_switch(
        self,
        target_profile: str,
        confirmation_token: str,
    ) -> RiskProfileSwitchResult:
        current = self.active_profile_name()
        target = target_profile.strip().lower()
        token = confirmation_token.strip().lower()

        if target not in KNOWN_PROFILES:
            return self._refuse(
                "unknown_profile",
                f"Profile {target!r} is not in the shipped set.",
                current,
                target_profile,
            )

        if current not in KNOWN_PROFILES:
            return self._refuse(
                "invalid_current_profile",
                (
                    f"Active risk profile selector {current!r} is invalid. "
                    "Resolve data/risk_profile.txt before switching profiles."
                ),
                current,
                target,
            )

        active_count = self._active_runners_count()
        if active_count > 0:
            return self._refuse(
                "active_runners",
                f"Cannot switch while {active_count} runner(s) active. Stop all runners first.",
                current,
                target_profile,
                runners_active_count=active_count,
            )

        if self._kill_switch_triggered_unresolved():
            return self._refuse(
                "kill_switch_open",
                (
                    "Cannot switch while a triggered kill switch is unresolved. "
                    "Manually reset the kill switch first."
                ),
                current,
                target_profile,
            )

        is_elevation = RISK_ORDER[target] > RISK_ORDER[current]
        if is_elevation:
            if token != target:
                return self._refuse(
                    "typed_confirmation_mismatch",
                    f"Typed confirmation must equal {target!r} (case-insensitive).",
                    current,
                    target_profile,
                )
            method = "typed"
        else:
            if token != "confirm_reduction":
                return self._refuse(
                    "reduction_confirmation_missing",
                    "Reduction confirmation token missing or incorrect.",
                    current,
                    target_profile,
                )
            method = "single_click"

        self._write_profile_file(target)
        self._audit(
            from_profile=current,
            to_profile=target,
            actor="gui",
            confirmation_method=method,
            success=True,
            failure_reason=None,
            runners_active_count=0,
        )
        return RiskProfileSwitchResult(
            applied=True,
            from_profile=current,
            to_profile=target,
            confirmation_method=method,
        )

    def _active_runners_count(self) -> int:
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM strategy_runs WHERE ended_at IS NULL")
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def _kill_switch_triggered_unresolved(self) -> bool:
        return KillSwitchStateStore(event_store=EventStore(self._db_path)).get_state().active

    def _write_profile_file(self, target: str) -> None:
        data_dir = get_data_dir()
        path = data_dir / "risk_profile.txt"
        tmp = data_dir / "risk_profile.txt.tmp"
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(target + "\n", encoding="utf-8")
        tmp.replace(path)

    def _refuse(
        self,
        reason_code: str,
        human_message: str,
        current: str,
        target: str,
        runners_active_count: int = 0,
    ) -> RiskProfileSwitchResult:
        logger.warning("Risk profile switch refused: %s - %s", reason_code, human_message)
        self._audit(
            from_profile=current,
            to_profile=target,
            actor="gui",
            confirmation_method="none",
            success=False,
            failure_reason=reason_code,
            runners_active_count=runners_active_count,
        )
        return RiskProfileSwitchResult(
            applied=False,
            from_profile=current,
            to_profile=target,
            confirmation_method="none",
            reason_code=reason_code,
            message=human_message,
            runners_active_count=runners_active_count,
        )

    def _audit(
        self,
        *,
        from_profile: str,
        to_profile: str,
        actor: str,
        confirmation_method: str,
        success: bool,
        failure_reason: str | None,
        runners_active_count: int,
    ) -> None:
        _append_audit_row(
            self._db_path,
            from_profile=from_profile,
            to_profile=to_profile,
            actor=actor,
            confirmation_method=confirmation_method,
            success=success,
            failure_reason=failure_reason,
            runners_active_count=runners_active_count,
        )


@dataclass(frozen=True)
class ProfileAuditDivergence:
    """Informational divergence between the profile file and the audit trail (P2-06)."""

    file_profile: str
    latest_audit_profile: str | None
    file_profile_known: bool
    message: str


def reconcile_profile_against_audit(db_path: Path) -> ProfileAuditDivergence | None:
    """Compare ``data/risk_profile.txt`` against the latest successful audit row.

    P2-06: a hand-edit of the profile file changes runtime behavior with no
    audit row; this surfaces the divergence so it is no longer invisible.
    Informational ONLY — no incident row, no blocking, no enforcement change.
    The runtime fallback semantics in ``risk/config.py`` (missing/unreadable/
    unknown name → conservative) are untouched.

    Returns ``None`` when the file's profile name matches the most recent
    *successful* ``risk_profile_changes.to_profile``, or when neither a file
    nor any audit history exists (the implicit Conservative default).
    """
    file_profile = get_active_profile_name()
    file_profile_known = file_profile in KNOWN_PROFILES
    latest = _latest_successful_audit_to_profile(db_path)

    if latest is None:
        if file_profile_known and file_profile == "conservative":
            # Implicit default: no selector edits, no audit history required.
            return None
        message = (
            f"Active risk-profile file resolves to {file_profile!r} but no successful "
            "activation has ever been audited in risk_profile_changes."
        )
    elif file_profile_known and file_profile == latest:
        return None
    else:
        message = (
            f"Active risk-profile file resolves to {file_profile!r} but the latest "
            f"successful activation audit row says {latest!r}."
        )

    if not file_profile_known:
        message += (
            f" {file_profile!r} is not a known profile; runtime falls back to "
            "'conservative' (ADR 0054 §2)."
        )
    message += (
        " The file was likely hand-edited outside the audited activation path. Informational only."
    )
    return ProfileAuditDivergence(
        file_profile=file_profile,
        latest_audit_profile=latest,
        file_profile_known=file_profile_known,
        message=message,
    )


def _latest_successful_audit_to_profile(db_path: Path) -> str | None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT to_profile FROM risk_profile_changes WHERE success = 1 ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return None if row is None else str(row[0])
    finally:
        conn.close()


def record_startup_default(db_path: Path) -> None:
    """Audit the implicit Conservative default only when no selector exists."""
    if (get_data_dir() / "risk_profile.txt").exists():
        return

    conn = sqlite3.connect(str(db_path))
    try:
        cutoff = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        cur = conn.execute(
            "SELECT COUNT(*) FROM risk_profile_changes "
            "WHERE actor = 'startup' AND recorded_at >= ?",
            (cutoff,),
        )
        if cur.fetchone()[0] > 0:
            return
    finally:
        conn.close()

    _append_audit_row(
        db_path,
        from_profile="conservative",
        to_profile="conservative",
        actor="startup",
        confirmation_method="none",
        success=True,
        failure_reason=None,
        runners_active_count=0,
    )


def _append_audit_row(
    db_path: Path,
    *,
    from_profile: str,
    to_profile: str,
    actor: str,
    confirmation_method: str,
    success: bool,
    failure_reason: str | None,
    runners_active_count: int,
) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO risk_profile_changes
               (recorded_at, from_profile, to_profile, actor,
                confirmation_method, context_mode, runners_active_count,
                success, failure_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(UTC).isoformat(),
                from_profile,
                to_profile,
                actor,
                confirmation_method,
                "paper",
                runners_active_count,
                1 if success else 0,
                failure_reason,
            ),
        )
        conn.commit()
    finally:
        conn.close()

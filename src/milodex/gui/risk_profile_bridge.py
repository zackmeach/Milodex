"""Risk-profile bridge for GUI/QML (ADR 0054).

``RiskProfileBridge`` is the Slot/Signal surface QML uses to read and change
the active risk profile.  All change attempts (successful, refused, and
implicit startup defaults) are written to the ``risk_profile_changes`` audit
table created by migration 011.

Key invariants enforced per ADR 0054:
- §5: refuses while any strategy runner is active (``strategy_runs.ended_at IS NULL``)
- §6: refuses while a triggered kill switch is unresolved (latest event is
  ``'activated'``, not ``'reset'``)
- Elevation (risk_order ↑) requires typed confirmation equal to the target
  profile name (case-insensitive, trimmed).
- Reduction (risk_order ↓ or same) requires confirmation token
  ``'confirm_reduction'`` (case-insensitive, trimmed).
- Profile file is written to ``get_data_dir() / "risk_profile.txt"`` via an
  atomic tmp-then-replace so the loader never sees a partial write.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from milodex.config import get_data_dir

logger = logging.getLogger(__name__)

_KNOWN_PROFILES: list[str] = ["conservative", "standard", "aggressive"]
_RISK_ORDER: dict[str, int] = {"conservative": 0, "standard": 1, "aggressive": 2}


class RiskProfileBridge(QObject):
    """GUI-facing bridge for risk-profile inspection and switching (ADR 0054)."""

    profileChanged = Signal()  # noqa: N815
    switchRefused = Signal(str, str)  # noqa: N815  (reason_code, human_message)
    switchApplied = Signal(str)  # noqa: N815  new profile name

    def __init__(self, db_path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db_path = db_path

    # ── read ──────────────────────────────────────────────────────────────────

    @Slot(result=str)
    def activeProfileName(self) -> str:  # noqa: N802
        """Return the currently active profile name."""
        from milodex.risk.config import get_active_profile_name

        return get_active_profile_name()

    # ── write ─────────────────────────────────────────────────────────────────

    @Slot(str, str, result=bool)
    def attemptSwitch(self, target_profile: str, confirmation_token: str) -> bool:  # noqa: N802
        """Try to switch the active risk profile.

        Returns True if the switch was applied, False if it was refused.

        For elevation (toward higher risk): ``confirmation_token`` must equal
        the target profile name (case-insensitive, trimmed).
        For reduction (toward lower or same risk): ``confirmation_token`` must
        equal ``'confirm_reduction'`` (case-insensitive, trimmed).

        Every call writes one row to ``risk_profile_changes``.
        """
        current = self.activeProfileName()
        target = target_profile.strip().lower()
        token = confirmation_token.strip().lower()

        # Guard: unknown profile
        if target not in _KNOWN_PROFILES:
            self._refuse(
                "unknown_profile",
                f"Profile {target!r} is not in the shipped set.",
                current,
                target_profile,
            )
            return False

        # ADR 0054 §5: refuse if any runner active
        active_count = self._active_runners_count()
        if active_count > 0:
            self._refuse(
                "active_runners",
                f"Cannot switch while {active_count} runner(s) active. "
                "Stop all runners first.",
                current,
                target_profile,
                runners_active_count=active_count,
            )
            return False

        # ADR 0054 §6: refuse if a triggered kill switch is unresolved
        if self._kill_switch_triggered_unresolved():
            self._refuse(
                "kill_switch_open",
                "Cannot switch while a triggered kill switch is unresolved. "
                "Manually reset the kill switch first.",
                current,
                target_profile,
            )
            return False

        # Risk-direction check
        is_elevation = _RISK_ORDER[target] > _RISK_ORDER[current]

        if is_elevation:
            if token != target:
                self._refuse(
                    "typed_confirmation_mismatch",
                    f"Typed confirmation must equal {target!r} (case-insensitive).",
                    current,
                    target_profile,
                )
                return False
            method = "typed"
        else:
            # Reduction or same-level switch: single-click confirmation token
            if token != "confirm_reduction":
                self._refuse(
                    "reduction_confirmation_missing",
                    "Reduction confirmation token missing or incorrect.",
                    current,
                    target_profile,
                )
                return False
            method = "single_click"

        # Apply atomically
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
        self.switchApplied.emit(target)
        self.profileChanged.emit()
        return True

    # ── internal helpers ──────────────────────────────────────────────────────

    def _active_runners_count(self) -> int:
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM strategy_runs WHERE ended_at IS NULL"
            )
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def _kill_switch_triggered_unresolved(self) -> bool:
        """Return True if the latest kill-switch event is 'activated' (not 'reset').

        The kill-switch state is determined by the most-recent event row:
        - 'activated' → switch is open
        - 'reset'     → switch is cleared
        - no rows     → switch is inactive
        """
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT event_type FROM kill_switch_events ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            return row is not None and row[0] == "activated"
        finally:
            conn.close()

    def _write_profile_file(self, target: str) -> None:
        """Write profile name atomically via tmp-then-replace.

        Uses ``get_data_dir()`` to match the loader path exactly.
        """
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
    ) -> None:
        logger.warning("Risk profile switch refused: %s — %s", reason_code, human_message)
        self._audit(
            from_profile=current,
            to_profile=target,
            actor="gui",
            confirmation_method="none",
            success=False,
            failure_reason=reason_code,
            runners_active_count=runners_active_count,
        )
        self.switchRefused.emit(reason_code, human_message)

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
        conn = sqlite3.connect(str(self._db_path))
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
                    "paper",  # phase-1: always paper
                    runners_active_count,
                    1 if success else 0,
                    failure_reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()


# ── module-level helper ────────────────────────────────────────────────────────


def record_startup_default(db_path: Path) -> None:
    """Write one audit row at app startup if ``data/risk_profile.txt`` is absent.

    Called from ``app.py`` during GUI bootstrap (PR-7c). Idempotent within an
    app session: only writes a row if no ``'startup'`` actor row exists with
    ``recorded_at`` within the last 60 seconds (covers concurrent boot races).

    - Fresh install with no ``data/risk_profile.txt`` → one row written:
      ``from_profile='conservative'``, ``to_profile='conservative'``,
      ``actor='startup'``, ``confirmation_method='none'``, ``success=1``.
    - Subsequent calls within 60 s → no duplicate row.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cutoff = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        cur = conn.execute(
            "SELECT COUNT(*) FROM risk_profile_changes "
            "WHERE actor = 'startup' AND recorded_at >= ?",
            (cutoff,),
        )
        if cur.fetchone()[0] > 0:
            return  # already recorded within race window
        conn.execute(
            """INSERT INTO risk_profile_changes
               (recorded_at, from_profile, to_profile, actor,
                confirmation_method, context_mode, runners_active_count,
                success, failure_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(UTC).isoformat(),
                "conservative",
                "conservative",
                "startup",
                "none",
                "paper",
                0,
                1,
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

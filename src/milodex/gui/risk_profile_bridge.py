"""Qt adapter for risk-profile activation (ADR 0054).

``RiskProfileBridge`` is the QML-facing Slot/Signal surface. Risk activation
policy, audit writes, and selector-file writes live in
``milodex.risk.profile_activation`` so the GUI remains an adapter.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from milodex.risk.profile_activation import (
    RiskProfileActivationService,
)
from milodex.risk.profile_activation import (
    record_startup_default as _record_startup_default,
)


class RiskProfileBridge(QObject):
    """GUI-facing bridge for risk-profile inspection and switching (ADR 0054)."""

    profileChanged = Signal()  # noqa: N815
    switchRefused = Signal(str, str)  # noqa: N815  (reason_code, human_message)
    switchApplied = Signal(str)  # noqa: N815  new profile name

    def __init__(self, db_path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db_path = db_path

    @Slot(result=str)
    def activeProfileName(self) -> str:  # noqa: N802
        """Return the currently active profile name."""
        return RiskProfileActivationService(self._db_path).active_profile_name()

    @Slot(str, str, result=bool)
    def attemptSwitch(self, target_profile: str, confirmation_token: str) -> bool:  # noqa: N802
        """Try to switch the active risk profile."""
        result = RiskProfileActivationService(self._db_path).attempt_switch(
            target_profile,
            confirmation_token,
        )
        if not result.applied:
            self.switchRefused.emit(result.reason_code or "unknown", result.message or "")
            return False
        self.switchApplied.emit(result.to_profile)
        self.profileChanged.emit()
        return True


def record_startup_default(db_path: Path) -> None:
    """Import-compatible startup default audit helper."""
    _record_startup_default(db_path)

"""Local state for execution services."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class KillSwitchState:
    """Local kill-switch state."""

    active: bool
    reason: str | None = None
    last_triggered_at: str | None = None


class KillSwitchStateStore:
    """File-backed kill-switch state store."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def get_state(self) -> KillSwitchState:
        if not self._path.exists():
            return KillSwitchState(active=False)

        with self._path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        return KillSwitchState(
            active=bool(data.get("active", False)),
            reason=data.get("reason"),
            last_triggered_at=data.get("last_triggered_at"),
        )

    def activate(self, reason: str) -> KillSwitchState:
        state = KillSwitchState(
            active=True,
            reason=reason,
            last_triggered_at=datetime.now(tz=UTC).isoformat(),
        )
        self._write_state(state)
        return state

    def reset(self) -> KillSwitchState:
        state = KillSwitchState(active=False)
        self._write_state(state)
        return state

    def _write_state(self, state: KillSwitchState) -> None:
        with self._path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, indent=2)

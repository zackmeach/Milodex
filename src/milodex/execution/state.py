"""Local state for execution services."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from milodex.core.event_store import EventStore, KillSwitchEvent


@dataclass(frozen=True)
class KillSwitchState:
    """Local kill-switch state."""

    active: bool
    reason: str | None = None
    last_triggered_at: str | None = None


class KillSwitchStateStore:
    """Event-store-backed kill-switch state store with JSON migration support."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        event_store: EventStore | None = None,
        legacy_path: Path | None = None,
    ) -> None:
        self._legacy_path = legacy_path or path
        if self._legacy_path is not None:
            self._legacy_path.parent.mkdir(parents=True, exist_ok=True)
        self._event_store = event_store or EventStore(self._default_db_path(self._legacy_path))

    def get_state(self) -> KillSwitchState:
        self._migrate_legacy_state_if_needed()
        latest_event = self._event_store.get_latest_kill_switch_event()
        if latest_event is None or latest_event.event_type == "reset":
            return KillSwitchState(active=False)
        return KillSwitchState(
            active=True,
            reason=latest_event.reason,
            last_triggered_at=latest_event.recorded_at.isoformat(),
        )

    def activate(self, reason: str) -> KillSwitchState:
        self._migrate_legacy_state_if_needed()
        self._event_store.append_kill_switch_event(
            KillSwitchEvent(
                event_type="activated",
                recorded_at=datetime.now(tz=UTC),
                reason=reason,
            )
        )
        return self.get_state()

    def reset(self) -> KillSwitchState:
        self._migrate_legacy_state_if_needed()
        self._event_store.append_kill_switch_event(
            KillSwitchEvent(
                event_type="reset",
                recorded_at=datetime.now(tz=UTC),
                reason=None,
            )
        )
        return self.get_state()

    def _migrate_legacy_state_if_needed(self) -> None:
        if self._legacy_path is None or not self._legacy_path.exists():
            return
        if self._event_store.get_latest_kill_switch_event() is not None:
            return

        with self._legacy_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        if not bool(data.get("active", False)):
            return

        recorded_at = data.get("last_triggered_at")
        self._event_store.append_kill_switch_event(
            KillSwitchEvent(
                event_type="activated",
                recorded_at=(
                    datetime.fromisoformat(recorded_at)
                    if isinstance(recorded_at, str)
                    else datetime.now(tz=UTC)
                ),
                reason=data.get("reason"),
            )
        )

    def _default_db_path(self, legacy_path: Path | None) -> Path:
        if legacy_path is None:
            msg = "KillSwitchStateStore requires either a legacy path or an EventStore."
            raise ValueError(msg)
        return legacy_path.with_name("milodex.db")

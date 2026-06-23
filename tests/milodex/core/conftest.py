"""Shared fixtures/helpers for core event-store tests.

``_append_queued_intent`` is the shared queue-at-open test helper (Phase-1
contract): later phases (3/5/6) import it from here rather than redefining the
``QueuedIntentEvent`` field set, so the exact shared-contract field names live in
exactly one place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from milodex.core.event_store import EventStore, QueuedIntentEvent
from milodex.strategies.loader import compute_config_hash

_DEFAULT_CREATED_AT = datetime(2026, 6, 22, 20, 0, tzinfo=UTC)
_DEFAULT_EXPIRES_AT = datetime(2026, 6, 23, 19, 0, tzinfo=UTC)

_DEFAULT_CONFIG_YAML = "strategy:\n  id: rsi2.mr.swing.v1\n"


def _default_config(store: EventStore) -> tuple[str, str]:
    """Write a real on-disk config next to the store db and return (path, hash).

    The drain authority (``get_active_queued_intents``) re-verifies a queued
    intent's stored ``config_hash`` against the on-disk config (I-7). So the
    shared default must point at a real, hashable file whose stored hash MATCHES
    — otherwise every default-built intent would be dropped by the guard. Tests
    that exercise the guard's drop branches override ``config_hash`` /
    ``strategy_config_path`` explicitly.
    """
    cfg = Path(store._path).parent / "rsi2.yaml"
    if not cfg.exists():
        cfg.write_text(_DEFAULT_CONFIG_YAML, encoding="utf-8")
    return str(cfg), compute_config_hash(cfg)


def _append_queued_intent(store: EventStore, *, idempotency_key: str, **overrides) -> int:
    """Append a queued intent built from sensible defaults and return its id.

    Constructs a ``QueuedIntentEvent`` with the exact shared-contract field names
    (``idempotency_key = f"{strategy_id}|{trading_session}|{side}|{symbol}"``) and
    persists it via :meth:`EventStore.append_queued_intent`. Pass ``**overrides``
    to vary any field; ``idempotency_key`` is required so each row is explicit
    about its dedup key.

    The default ``strategy_config_path`` / ``config_hash`` resolve to a real
    on-disk config with a MATCHING hash so a default-built intent survives the
    I-7 config_hash guard in ``get_active_queued_intents``.
    """
    default_path, default_hash = _default_config(store)
    fields: dict[str, object] = {
        "idempotency_key": idempotency_key,
        "strategy_id": "rsi2.v1",
        "strategy_config_path": default_path,
        "config_hash": default_hash,
        "session_id": "sess-A",
        "trading_session": "2026-06-23",
        "locked_in_bar_timestamp": "2026-06-22T20:00:00+00:00",
        "symbol": "SPY",
        "side": "buy",
        "intent_class": "entry",
        "notional_pct": 0.1,
        "expected_stage": "paper",
        "expected_max_positions": 3,
        "expected_max_position_pct": 0.2,
        "expected_daily_loss_cap_pct": 0.05,
        "intent_payload_json": {"qty": 4, "limit_price": 500.0},
        "reasoning_json": {"signal": "rsi<10"},
        "created_at": _DEFAULT_CREATED_AT,
        "expires_at": _DEFAULT_EXPIRES_AT,
    }
    fields.update(overrides)
    return store.append_queued_intent(QueuedIntentEvent(**fields))  # type: ignore[arg-type]

"""Shared fixtures/helpers for core event-store tests.

``_append_queued_intent`` is the shared queue-at-open test helper (Phase-1
contract): later phases (3/5/6) import it from here rather than redefining the
``QueuedIntentEvent`` field set, so the exact shared-contract field names live in
exactly one place.
"""

from __future__ import annotations

from datetime import UTC, datetime

from milodex.core.event_store import EventStore, QueuedIntentEvent

_DEFAULT_CREATED_AT = datetime(2026, 6, 22, 20, 0, tzinfo=UTC)
_DEFAULT_EXPIRES_AT = datetime(2026, 6, 23, 19, 0, tzinfo=UTC)


def _append_queued_intent(store: EventStore, *, idempotency_key: str, **overrides) -> int:
    """Append a queued intent built from sensible defaults and return its id.

    Constructs a ``QueuedIntentEvent`` with the exact shared-contract field names
    (``idempotency_key = f"{strategy_id}|{trading_session}|{side}|{symbol}"``) and
    persists it via :meth:`EventStore.append_queued_intent`. Pass ``**overrides``
    to vary any field; ``idempotency_key`` is required so each row is explicit
    about its dedup key.
    """
    fields: dict[str, object] = {
        "idempotency_key": idempotency_key,
        "strategy_id": "rsi2.v1",
        "strategy_config_path": "configs/rsi2.yaml",
        "config_hash": "c" * 64,
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

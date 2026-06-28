"""DROPPED-EXIT operator alert (Phase 7, ADR 0057) — the asymmetry guard.

Silently dropping an undrained ENTRY is benign (it just doesn't fire). Silently
dropping an undrained EXIT can strand a live position. So when the queue-at-open
drain declines to drain an EXIT, the runner must emit a DURABLE
``OperatorAlertEvent`` (alert_type ``exit_intent_dropped``) + a ``logger.warning``
so the operator can resolve it (manual flatten). This is observational ONLY — it
NEVER submits, retries, or mutates risk state.

There are exactly two ways an EXIT fails to drain:
  (a) it is excluded by ``get_active_queued_intents`` (the SOLE drain authority) —
      the clean-handoff fence (I-4) failed or its config drifted; the row is still
      ``status='queued'`` but is not in the drainable set; and
  (b) it passes the fence but is dropped in the per-intent loop for halt /
      not-tradable.
Both alert + retire (``status='obsolete'``) so a persistently-stranded/halted exit
does not re-alert every ~60s open poll; the strategy re-emits a fresh exit next
post-close if it still holds the position. Entries keep the prior behavior: drop,
leave queued, NO alert.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from milodex.broker.models import OrderSide
from milodex.core.event_store import QueuedIntentEvent
from tests.milodex.strategies.test_runner_queued_intent_drain import (
    _build_open_runner,
    _force_decision,
    _intent,
    _locked_in_bar_from_provider,
    _reasoning,
    _seed_queued_entry,
)


def _seed_queued_exit_with_session(
    event_store,
    runner,
    *,
    session_id: str,
    symbol: str = "SPY",
) -> int:
    """Seed a queued EXIT (SELL) whose ``session_id`` is ``session_id``.

    Mirrors ``_seed_queued_entry`` but lets a test plant a DIFFERENT originating
    session so the clean-handoff fence in ``get_active_queued_intents`` excludes
    the row (no ``strategy_runs`` row + a non-running session_id => unclean).
    """
    runner_intent = runner._runner_intent(_intent(symbol, OrderSide.SELL))
    normalized = runner_intent.normalized_symbol()
    trading_session = "2026-06-19"
    idempotency_key = (
        f"{runner._strategy_id}|{trading_session}|{OrderSide.SELL.value}|{normalized}"
    )
    # Anchor created_at/expires_at to the runner's clock (not a fixed date) so the
    # 7-day expiry window never rots past the real wall clock and the global sweep
    # can't expire the row before the drain. Mirrors test_runner_expiry_sweep.
    now = runner._now()
    bar_payload = _locked_in_bar_from_provider(runner)
    event = QueuedIntentEvent(
        idempotency_key=idempotency_key,
        strategy_id=runner._strategy_id,
        strategy_config_path=str(runner._loaded.config.path),
        config_hash=runner._risk_config_hash(),
        session_id=session_id,
        trading_session=trading_session,
        locked_in_bar_timestamp=bar_payload["timestamp"],
        symbol=normalized,
        side=OrderSide.SELL.value,
        intent_class="exit",
        expected_stage=runner_intent.expected_stage,
        expected_max_positions=runner_intent.expected_max_positions,
        expected_max_position_pct=runner_intent.expected_max_position_pct,
        expected_daily_loss_cap_pct=runner_intent.expected_daily_loss_cap_pct,
        intent_payload_json={
            "symbol": runner_intent.symbol,
            "side": OrderSide.SELL.value,
            "quantity": runner_intent.quantity,
            "order_type": runner_intent.order_type.value,
            "time_in_force": runner_intent.time_in_force.value,
            "locked_in_bar": bar_payload,
        },
        reasoning_json=_reasoning().asdict(),
        created_at=now,
        expires_at=now + timedelta(days=7),
        status="queued",
    )
    return event_store.append_queued_intent(event)


def _queued_exit_event(runner, *, symbol: str = "SPY") -> QueuedIntentEvent:
    """A standalone exit QueuedIntentEvent for the direct ``_emit`` unit test."""
    return QueuedIntentEvent(
        idempotency_key=f"{runner._strategy_id}|2026-06-19|sell|{symbol}",
        strategy_id=runner._strategy_id,
        session_id=runner.session_id,
        symbol=symbol,
        side="sell",
        intent_class="exit",
    )


# ---------------------------------------------------------------------------
# 1. The emit primitive: durable row + warning, no submit.
# ---------------------------------------------------------------------------


def test_emit_exit_drop_alert_writes_durable_row_and_warns(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    caplog,
):
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    queued = _queued_exit_event(runner, symbol="SPY")

    with caplog.at_level(logging.WARNING):
        runner._emit_exit_drop_alert(queued, reason="no_clean_handoff")

    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.severity == "warning"
    assert alert.symbol == "SPY"
    assert alert.side == "sell"
    assert alert.strategy_id == runner._strategy_id
    assert alert.session_id == runner.session_id
    assert alert.context_json["reason"] == "no_clean_handoff"
    assert alert.context_json["idempotency_key"] == queued.idempotency_key
    # Observational only — no broker submit.
    assert broker.submit_calls == []
    # A WARNING log naming the EXIT and the symbol.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("EXIT intent" in r.getMessage() and "SPY" in r.getMessage() for r in warnings)


# ---------------------------------------------------------------------------
# 2. Fence-failed (unclean-handoff) stranded EXIT -> alert + obsolete.
# ---------------------------------------------------------------------------


def test_drain_fence_failed_exit_alerts_and_obsoletes(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An EXIT queued by a DIFFERENT session with no clean ``strategy_runs`` row
    (SIGKILL) is excluded by ``get_active_queued_intents`` (the fence) but is
    still ``status='queued'``. The drain must alert + retire it."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_exit_with_session(
        event_store, runner, session_id="dead-session-no-strategy-run", symbol="SPY"
    )
    # The fenced row must not be drainable.
    assert (
        event_store.get_active_queued_intents(
            runner._strategy_id, now=runner._now(), running_session_id=runner.session_id
        )
        == []
    )

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SPY"
    assert alerts[0].context_json["reason"] == "no_clean_handoff"
    assert event_store.get_queued_intent(intent_id).status == "obsolete"


# ---------------------------------------------------------------------------
# 3. Halt / not-tradable drainable EXIT -> alert + obsolete.
# ---------------------------------------------------------------------------


def test_drain_halted_exit_alerts_and_obsoletes(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A CLEAN-handoff drainable EXIT whose symbol is halted (not tradable) is
    dropped in the per-intent loop. The drain must alert + retire it."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=1.0)])
    broker._symbol_tradable = False

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SPY"
    assert alerts[0].context_json["reason"] == "not_tradable"
    assert event_store.get_queued_intent(intent_id).status == "obsolete"


# ---------------------------------------------------------------------------
# 4. A normal ENTRY drop must NOT alert (entries are silent).
# ---------------------------------------------------------------------------


def test_drain_entry_drop_does_not_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A halted ENTRY drop emits no ``exit_intent_dropped`` alert (the asymmetry
    guard fires for exits only). Fix #3: a DECIDED entry drop is now terminal
    ('dropped'), but it still raises NO exit-drop operator alert."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    broker._symbol_tradable = False

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []
    # DECIDED entry drop is terminal ('dropped'), and emits no exit-drop alert.
    assert event_store.get_queued_intent(intent_id).status == "dropped"


# ---------------------------------------------------------------------------
# 5. A normal drainable EXIT that submits must NOT alert (alert is for DROPS).
# ---------------------------------------------------------------------------


def test_drainable_exit_does_not_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A clean drainable EXIT that re-evaluates to a matching SELL and an open
    strategy lot submits normally and emits NO alert."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    # A held lot so the 0-share-exit-on-flat-ledger short-circuit does not fire,
    # and the re-eval reproduces the SELL.
    runner._current_positions = lambda: {"SPY": 1.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=1.0)])

    result = runner.run_cycle()

    assert result == []
    assert len(broker.submit_calls) == 1
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []
    assert event_store.get_queued_intent(intent_id).status == "consumed"


# ---------------------------------------------------------------------------
# 6. Held-position EXIT whose at-open re-eval derives NO match -> alert + obsolete.
# ---------------------------------------------------------------------------


def test_drain_held_exit_reeval_no_match_alerts_and_obsoletes(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A clean-handoff drainable EXIT for a STILL-HELD position whose at-open
    re-eval derives no matching SELL must alert (a path-dependent exit may never
    re-emit) and retire the row (reason 'reeval_no_exit_position_open') — not
    fall silently through to ``continue`` leaving a clean 'queued' row the
    stranded-exit alerter skips."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    # Position STILL HELD (the exit's lot exists) but the re-eval derives no SELL.
    runner._current_positions = lambda: {"SPY": 1.0}
    _force_decision(runner, [])

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SPY"
    assert alerts[0].context_json["reason"] == "reeval_no_exit_position_open"
    assert event_store.get_queued_intent(intent_id).status == "obsolete"


# ---------------------------------------------------------------------------
# 7. Broker raise AFTER the consume CAS -> alert 'submit_error' (and no lie).
# ---------------------------------------------------------------------------


def _raise_on_submit(runner, exc: Exception) -> None:
    """Make the execution service's submit_paper raise an infra-style error.

    The consume CAS flips the row to 'consumed' BEFORE the broker call inside
    ``_submit_locked``; a raise here means the row is NOT safely re-queuable, so
    the drain must not claim 'leaving queued'.
    """

    def boom(intent, **kwargs):  # noqa: ARG001
        raise exc

    runner._execution_service.submit_paper = boom


def test_drain_exit_submit_raise_alerts_submit_error(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    caplog,
):
    """A broker infra raise out of ``submit_paper`` (the row may already be
    'consumed' by the CAS) for a clean drainable EXIT must emit an
    ``exit_intent_dropped`` alert (reason 'submit_error') and must NOT log the
    pre-submit 'leaving queued' claim — that claim would be false post-CAS."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 1.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=1.0)])
    _raise_on_submit(runner, ConnectionError("broker timeout"))

    with caplog.at_level(logging.WARNING):
        result = runner.run_cycle()

    assert result == []
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SPY"
    assert alerts[0].context_json["reason"] == "submit_error"
    # The false pre-submit 'leaving queued' claim must NOT be logged for this row.
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("leaving queued" in m for m in messages)
    # The submit-raise log is the one that fired.
    assert any("submit raised" in m for m in messages)


def test_drain_entry_submit_raise_does_not_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An ENTRY whose submit raises emits NO exit-drop alert (entries are silent)."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    _raise_on_submit(runner, ConnectionError("broker timeout"))

    result = runner.run_cycle()

    assert result == []
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []

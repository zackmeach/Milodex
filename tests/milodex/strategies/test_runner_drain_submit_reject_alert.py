"""Silent-broker-reject EXIT strand (ADR 0057, Major 3) — a routine broker
rejection on a drained EXIT consumes the queued row (the idempotency CAS commits
to ``consumed`` BEFORE the broker call) but produces NO order. Because
``OrderRejectedError`` / ``InsufficientFundsError`` are CAUGHT and RETURNED as
``ExecutionResult(status=REJECTED)`` (not raised), the drain's submit-raise alert
path never fires. The drain must inspect the returned result and surface a
rejected EXIT so an undrained exit is always operator-visible.

Asymmetry guard: only EXIT rejections alert. ENTRY rejections stay silent. A
BLOCKED result (idempotency-suppressed race-loss, or a risk block before the CAS)
must NOT alert — only REJECTED (post-CAS, consumed-but-unsubmitted) does.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from milodex.broker.exceptions import InsufficientFundsError
from milodex.broker.models import OrderSide
from milodex.data.models import Bar
from milodex.execution.models import ExecutionResult, ExecutionStatus
from tests.milodex.strategies.test_runner import StubBroker
from tests.milodex.strategies.test_runner_queued_intent_drain import (
    _build_open_runner,
    _force_decision,
    _intent,
    _seed_queued_entry,
)


class _InsufficientFundsBroker(StubBroker):
    """submit_order raises InsufficientFundsError (caught -> REJECTED result)."""

    def submit_order(self, **kwargs) -> object:
        self.submit_calls.append(kwargs)
        raise InsufficientFundsError("insufficient buying power")


def _patch_fresh_latest_bar(runner, *, close: float, symbol: str = "SPY") -> None:
    """Inject a confirmably-fresh bar so the fresh-price gate admits the submit."""
    locked = runner._data_provider._bars_by_symbol[symbol].latest()
    fresh = Bar(
        timestamp=locked.timestamp.to_pydatetime() + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        vwap=close,
    )
    runner._data_provider.get_latest_bar = lambda _sym: fresh


def _swap_broker(runner, broker) -> None:
    """Point both the runner and its execution service at ``broker``."""
    runner._broker = broker
    runner._execution_service._broker = broker


# ---------------------------------------------------------------------------
# Major 3: a drained EXIT the broker REJECTS must emit exit_intent_dropped.
# ---------------------------------------------------------------------------


def test_drain_exit_broker_reject_emits_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A held-lot EXIT that re-evaluates to a SELL but is REJECTED by the broker
    (InsufficientFundsError) leaves the row consumed with no order at the broker.
    The drain must emit an ``exit_intent_dropped`` alert (reason
    ``submit_rejected``) so the consumed-but-unsubmitted exit is operator-visible."""
    runner, _broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    rejecting = _InsufficientFundsBroker(account=_broker.account, market_open=True)
    _swap_broker(runner, rejecting)
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    _patch_fresh_latest_bar(runner, close=10.0)

    result = runner.run_cycle()

    assert result == []
    # The broker was called (and rejected); the row was consumed by the CAS.
    assert len(rejecting.submit_calls) == 1
    assert event_store.get_queued_intent(intent_id).status == "consumed"
    # The rejected EXIT surfaced as an operator alert.
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SPY"
    assert alerts[0].context_json["reason"] == "submit_rejected"


def test_drain_entry_broker_reject_does_not_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An ENTRY rejected by the broker stays silent (asymmetry guard): no
    ``exit_intent_dropped`` alert. The row is consumed (post-CAS)."""
    runner, _broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    rejecting = _InsufficientFundsBroker(account=_broker.account, market_open=True)
    _swap_broker(runner, rejecting)
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=100.0)])
    _patch_fresh_latest_bar(runner, close=10.0)

    result = runner.run_cycle()

    assert result == []
    assert len(rejecting.submit_calls) == 1
    assert event_store.get_queued_intent(intent_id).status == "consumed"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []


def test_drain_exit_idempotency_suppressed_does_not_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An EXIT whose submit returns BLOCKED (idempotency CAS lost the race —
    another drain already consumed the row and will submit) must NOT alert: the
    benign race-loss is not a strand. Only REJECTED (post-CAS) alerts."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    _patch_fresh_latest_bar(runner, close=10.0)

    # Simulate a race-loser: submit_paper returns a BLOCKED (idempotency_suppressed)
    # result without sending an order.
    real_request_builder = runner._execution_service._build_execution_request

    def suppressed_submit(intent, **kwargs):
        request = real_request_builder(
            runner._execution_service._normalize_intent(intent), 10.0, None
        )
        return ExecutionResult(
            status=ExecutionStatus.BLOCKED,
            execution_request=request,
            risk_decision=None,
            account=broker.account,
            market_open=True,
            latest_bar=None,
            message="Submit suppressed: idempotency CAS lost the race (no order sent).",
        )

    runner._execution_service.submit_paper = suppressed_submit

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    # No alert for a benign race-loss.
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []
    # The row is untouched by THIS drain (the winning drain owns it).
    assert event_store.get_queued_intent(intent_id).status == "queued"

"""Phase-7 (queue-at-open, ADR 0057) — the daily runner sweeps expired ``queued``
intents to ``expired`` at the manual run-loop / reconcile cadence.

Bookkeeping only: ``get_active_queued_intents`` already excludes expired rows from
the drain, so the sweep settles durable status for audit and never gates a trade.
It must (a) flip stale rows, (b) leave the lock-in watermark untouched (it is
independent of bar processing), and (c) emit exactly one durable audit explanation
when — and only when — rows were actually swept.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from milodex.core.event_store import QueuedIntentEvent
from tests.milodex.strategies.test_runner import _build_lockin_runner


def _append_expired_queued_intent(event_store, runner, *, symbol: str = "SPY") -> int:
    """Seed an already-expired ``queued`` row owned by the running session.

    The sweep is a blind status flip (no config-hash / handoff fence), so only
    ``status='queued'`` and a past ``expires_at`` matter here.
    """
    now = runner._now()
    key = f"{runner._strategy_id}|2020-01-02|buy|{symbol}"
    event = QueuedIntentEvent(
        idempotency_key=key,
        strategy_id=runner._strategy_id,
        strategy_config_path=str(runner._loaded.config.path),
        config_hash="a" * 64,
        session_id=runner.session_id,
        trading_session="2020-01-02",
        locked_in_bar_timestamp="2020-01-02T20:00:00+00:00",
        symbol=symbol,
        side="buy",
        intent_class="entry",
        expected_stage="paper",
        created_at=now - timedelta(days=2000),
        expires_at=now - timedelta(days=1999),  # long past -> stale
        status="queued",
    )
    return event_store.append_queued_intent(event)


def test_run_cycle_sweeps_expired_queued_intents_at_startup(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, _broker, _provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    _append_expired_queued_intent(event_store, runner)
    watermark_before = runner._last_processed_bar_at

    runner.run_cycle()

    expired = event_store.list_queued_intents_by_status("expired")
    assert [e.symbol for e in expired] == ["SPY"]
    assert event_store.list_queued_intents_by_status("queued") == []

    # The sweep is independent of bar processing — the lock-in watermark is untouched.
    assert runner._last_processed_bar_at == watermark_before

    sweep_explanations = [
        e for e in event_store.list_explanations() if "queued_intent_expiry_sweep" in e.reason_codes
    ]
    assert len(sweep_explanations) == 1
    assert sweep_explanations[0].submitted_by == "strategy_runner"


def test_run_cycle_writes_no_audit_when_nothing_expired(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, _broker, _provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )

    runner.run_cycle()

    sweep_explanations = [
        e for e in event_store.list_explanations() if "queued_intent_expiry_sweep" in e.reason_codes
    ]
    assert sweep_explanations == []

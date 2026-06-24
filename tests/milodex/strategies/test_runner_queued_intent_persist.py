"""Phase-1 (queue-at-open, ADR 0057) — daily runner persists a queued_intent at
the post-close lock-in instead of submitting.

These tests exercise ONLY the persist half of the lifecycle: at the lock-in-
confirmed daily post-close cycle the runner writes an inert, expiring row to the
durable ``queued_intents`` table and submits nothing. The next-open drain is a
sibling task and is not exercised here.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from milodex.broker.models import OrderSide, OrderType, TimeInForce
from milodex.execution.models import TradeIntent
from milodex.strategies.base import DecisionReasoning, StrategyDecision
from tests.milodex.strategies.test_runner import _build_lockin_runner


def _buy_intent(symbol: str = "spy") -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=1.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )


def _sell_intent(symbol: str = "spy") -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=OrderSide.SELL,
        quantity=1.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )


def _reasoning() -> DecisionReasoning:
    return DecisionReasoning(rule="regime.ma_filter_cross", narrative="test buy")


def _decision(intents: list[TradeIntent]) -> StrategyDecision:
    return StrategyDecision(intents=intents, reasoning=_reasoning())


def _stub_evaluate(runner, intents: list[TradeIntent]) -> None:
    """Force the strategy to emit ``intents`` on every evaluation."""
    runner._loaded.strategy.evaluate = lambda bars, ctx: _decision(intents)


def _drive_lockin(runner, provider) -> datetime:
    """Run the 2-cycle daily post-close lockin and return the anchored fake now."""
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner._now = lambda: fake_now[0]
    runner.run_cycle()  # cycle 1: pending stability
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    result = runner.run_cycle()  # cycle 2: lockin confirms → persist
    runner._last_cycle_result = result
    return fake_now[0]


# ---------------------------------------------------------------------------
# Pure helper pins
# ---------------------------------------------------------------------------


def test_idempotency_key_composition(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Plan-Contract §8(a) byte-for-byte pin: lowercase side, uppercase symbol."""
    runner, _, _, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    key = runner._idempotency_key(_buy_intent("spy"), "2026-06-19")
    assert key == f"{runner._strategy_id}|2026-06-19|buy|SPY"


def test_intent_class_entry_vs_exit(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, _, _, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    assert runner._intent_class(_buy_intent()) == "entry"
    assert runner._intent_class(_sell_intent()) == "exit"


# ---------------------------------------------------------------------------
# Persist behaviour at lock-in
# ---------------------------------------------------------------------------


def test_post_close_cycle_persists_and_does_not_submit(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    _stub_evaluate(runner, [_buy_intent("spy")])
    locked_bar = provider._bars_by_symbol["SPY"].latest()

    fake_now = _drive_lockin(runner, provider)

    # Nothing reached the broker; the cycle returned [].
    assert broker.submit_calls == []
    assert runner._last_cycle_result == []
    # The watermark advanced exactly once (lockin confirmed).
    assert runner._last_processed_bar_at is not None

    active = event_store.get_active_queued_intents(
        runner._strategy_id, now=fake_now, running_session_id=runner.session_id
    )
    assert len(active) == 1
    row = active[0]
    session_label = runner._trading_session_label(locked_bar.timestamp)
    assert row.idempotency_key == f"{runner._strategy_id}|{session_label}|buy|SPY"
    assert row.intent_class == "entry"
    assert row.config_hash == runner._risk_config_hash()
    assert row.intent_payload_json["locked_in_bar"]["close"] == locked_bar.close


def test_post_close_no_intents_persists_nothing(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    _stub_evaluate(runner, [])

    fake_now = _drive_lockin(runner, provider)

    assert broker.submit_calls == []
    assert (
        event_store.get_active_queued_intents(
            runner._strategy_id, now=fake_now, running_session_id=runner.session_id
        )
        == []
    )


def test_persist_unique_collision_is_idempotent(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, _, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    runner._now = lambda: datetime(2026, 6, 19, 20, 5, tzinfo=UTC)
    bar = provider._bars_by_symbol["SPY"].latest()
    intent = _buy_intent("spy")
    reasoning = _reasoning()

    runner._persist_queued_intent(intent, bar, reasoning)
    # Re-persist of the same logical intent must not raise.
    runner._persist_queued_intent(intent, bar, reasoning)

    active = event_store.get_active_queued_intents(
        runner._strategy_id,
        now=datetime(2026, 6, 19, 20, 5, tzinfo=UTC),
        running_session_id=runner.session_id,
    )
    assert len(active) == 1


def test_expiry_window_spans_a_weekend(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A Friday→Monday intent must outlast the longest weekend/holiday gap."""
    runner, _, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    runner._now = lambda: datetime(2026, 6, 19, 20, 5, tzinfo=UTC)
    bar = provider._bars_by_symbol["SPY"].latest()
    intent_id = None
    runner._persist_queued_intent(intent=_buy_intent("spy"), latest_bar=bar, reasoning=_reasoning())

    # Read the row back via the diagnostic id path (status/expiry-agnostic).
    active = event_store.get_active_queued_intents(
        runner._strategy_id,
        now=datetime(2026, 6, 19, 20, 5, tzinfo=UTC),
        running_session_id=runner.session_id,
    )
    assert len(active) == 1
    intent_id = active[0].id
    row = event_store.get_queued_intent(intent_id)
    assert row.expires_at - row.created_at >= timedelta(days=3)


# ---------------------------------------------------------------------------
# Fix #1 — persist-before-watermark (no silent intent loss)
# ---------------------------------------------------------------------------


def test_persist_failure_does_not_advance_watermark_and_alerts(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A non-IntegrityError persist failure (locked DB / unreadable config) must
    NOT advance the watermark and MUST emit a durable ``queued_intent_persist_failed``
    operator alert — otherwise the next cycle's already_seen short-circuit would
    silently strand the day's intent."""
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    _stub_evaluate(runner, [_buy_intent("spy")])

    def boom(_event):
        raise sqlite3.OperationalError("database is locked")

    event_store.append_queued_intent = boom

    _drive_lockin(runner, provider)

    # Nothing reached the broker, the watermark did NOT advance (re-eval next cycle),
    # and the failure surfaced as a durable operator alert.
    assert broker.submit_calls == []
    assert runner._last_processed_bar_at is None
    alerts = event_store.list_operator_alerts(alert_type="queued_intent_persist_failed")
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].strategy_id == runner._strategy_id
    assert alerts[0].symbol == "SPY"


def test_persist_failure_then_next_cycle_repersists_and_advances(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """After a persist failure (watermark unadvanced), a later cycle must re-evaluate
    the SAME locked bar, re-persist successfully, and advance the watermark exactly
    once (idempotent recovery)."""
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    _stub_evaluate(runner, [_buy_intent("spy")])
    real_append = event_store.append_queued_intent

    calls = {"n": 0}

    def flaky(event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_append(event)

    event_store.append_queued_intent = flaky

    # First lockin: persist raises -> watermark stays None. Confirming the gate
    # resets the lock-in state machine, so recovery re-arms it (one more stability
    # window) before re-confirming — correct, safe behavior (retry well inside TTL).
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner._now = lambda: fake_now[0]
    runner.run_cycle()  # cycle 1: pending stability
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    runner.run_cycle()  # cycle 2: lockin confirms -> persist raises (no advance)
    assert runner._last_processed_bar_at is None

    # Recovery: re-arm the stability window, then re-confirm + re-persist (the
    # append_queued_intent stub now succeeds on its second call).
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    runner.run_cycle()  # re-arm: pending stability
    assert runner._last_processed_bar_at is None
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    final_now = fake_now[0]
    runner.run_cycle()  # re-confirm -> persist succeeds -> watermark advances

    assert runner._last_processed_bar_at is not None
    active = event_store.get_active_queued_intents(
        runner._strategy_id, now=final_now, running_session_id=runner.session_id
    )
    assert len(active) == 1


def test_persist_integrity_error_is_swallowed_and_advances(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An IntegrityError (UNIQUE idempotency_key collision) is an idempotent
    duplicate: it is swallowed, no operator alert is written, and the watermark
    advances exactly once."""
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    _stub_evaluate(runner, [_buy_intent("spy")])

    def collide(_event):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: queued_intents.idempotency_key")

    event_store.append_queued_intent = collide

    _drive_lockin(runner, provider)

    assert broker.submit_calls == []
    # Idempotent duplicate: watermark advances, no persist-failure alert.
    assert runner._last_processed_bar_at is not None
    assert event_store.list_operator_alerts(alert_type="queued_intent_persist_failed") == []


def test_partial_persist_failure_retries_only_the_failed_intent(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Multi-intent lock-in where intent #1 (spy) persists but intent #2 (qqq)
    raises: the watermark stays unadvanced, the alert names the ACTUAL failed
    intent (qqq, not intents[0]), and the retry re-persists ONLY qqq — spy is
    skipped via the dedup set (which is added to only AFTER a successful persist),
    so spy is never double-persisted. This proves the Fix #1 dedup-ordering
    invariant the single-intent failure tests cannot reach."""
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    _stub_evaluate(runner, [_buy_intent("spy"), _buy_intent("qqq")])
    real_append = event_store.append_queued_intent

    calls = {"n": 0}

    def flaky(event):
        calls["n"] += 1
        # Only the SECOND append of the batch (qqq) raises; spy (#1) and every
        # later retry succeed.
        if calls["n"] == 2:
            raise sqlite3.OperationalError("database is locked")
        return real_append(event)

    event_store.append_queued_intent = flaky

    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner._now = lambda: fake_now[0]
    runner.run_cycle()  # cycle 1: pending stability
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    runner.run_cycle()  # cycle 2: confirm -> spy persists, qqq raises (no advance)

    # spy persisted; qqq did not; watermark unadvanced; alert names the FAILED qqq.
    assert runner._last_processed_bar_at is None
    queued = event_store.list_queued_intents_by_status("queued")
    assert sorted(q.symbol for q in queued) == ["SPY"]
    alerts = event_store.list_operator_alerts(alert_type="queued_intent_persist_failed")
    assert len(alerts) == 1
    assert alerts[0].symbol == "QQQ"

    # Recovery: re-arm + re-confirm on the SAME locked bar. The retry skips spy
    # (its key is in _processed_intent_keys) and re-persists ONLY qqq.
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    runner.run_cycle()  # re-arm: pending stability
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    final_now = fake_now[0]
    runner.run_cycle()  # re-confirm -> qqq re-persists -> watermark advances

    assert runner._last_processed_bar_at is not None
    active = event_store.get_active_queued_intents(
        runner._strategy_id, now=final_now, running_session_id=runner.session_id
    )
    # Exactly one row each — spy was NOT double-persisted by the retry.
    assert sorted(q.symbol for q in active) == ["QQQ", "SPY"]

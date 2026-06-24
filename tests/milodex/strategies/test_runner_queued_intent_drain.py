"""Phase-3 (queue-at-open, ADR 0057) — daily runner drains its active queued
intents at the next session open: re-evaluate against a fresh context, then submit
through the execution chokepoint with the persisted idempotency key and the
reconstructed locked-in bar.

These tests exercise ONLY the drain half of the lifecycle. They seed a queued row
directly (the persist half is covered by the sibling persist suite) and drive a
single market-open daily cycle. The real ExecutionService + StubBroker path is
exercised end-to-end so the row-scoped consume CAS runs against the threaded key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from milodex.broker.models import OrderSide, OrderType, TimeInForce
from milodex.core.event_store import QueuedIntentEvent
from milodex.execution.models import TradeIntent
from milodex.strategies.base import DecisionReasoning, StrategyDecision
from tests.milodex.strategies.test_runner import _build_lockin_runner


def _intent(symbol: str, side: OrderSide, quantity: float = 1.0) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )


def _reasoning() -> DecisionReasoning:
    return DecisionReasoning(rule="regime.ma_filter_cross", narrative="drain test")


def _force_decision(runner, intents: list[TradeIntent]) -> None:
    """Force the strategy to emit ``intents`` on every re-evaluation."""
    runner._loaded.strategy.evaluate = lambda bars, ctx: StrategyDecision(
        intents=intents, reasoning=_reasoning()
    )


def _record_submits(runner) -> list[dict]:
    """Wrap submit_paper to capture kwargs, delegating to the real method.

    Exercises the real ExecutionService + StubBroker (and thus the consume CAS)
    end-to-end while letting a test assert the kwargs the drain threaded through.
    """
    captured: list[dict] = []
    real_submit = runner._execution_service.submit_paper

    def recording_submit(intent, **kwargs):
        captured.append(kwargs)
        return real_submit(intent, **kwargs)

    runner._execution_service.submit_paper = recording_submit
    return captured


def _locked_in_bar_from_provider(runner, symbol: str = "SPY") -> dict:
    bar = runner._data_provider._bars_by_symbol[symbol].latest()
    return {
        "timestamp": bar.timestamp.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "vwap": bar.vwap,
    }


def _seed_queued_entry(
    event_store,
    runner,
    *,
    symbol: str,
    side: OrderSide,
    intent_class: str = "entry",
    config_hash: str | None = None,
    locked_in_bar: dict | None = None,
) -> int:
    """Append a clean-handoff QueuedIntentEvent admissible by get_active.

    ``session_id`` is the running session (clean handoff), ``config_hash`` defaults
    to the runner's on-disk hash (so the config-hash guard admits it), and
    ``locked_in_bar`` defaults to the stub provider's latest bar (so re-eval + the
    1D staleness gate align). Returns the row id.
    """
    runner_intent = runner._runner_intent(_intent(symbol, side))
    normalized = runner_intent.normalized_symbol()
    trading_session = "2026-06-19"
    idempotency_key = f"{runner._strategy_id}|{trading_session}|{side.value}|{normalized}"
    now = datetime(2026, 6, 19, 13, 30, tzinfo=UTC)
    bar_payload = locked_in_bar or _locked_in_bar_from_provider(runner)
    event = QueuedIntentEvent(
        idempotency_key=idempotency_key,
        strategy_id=runner._strategy_id,
        strategy_config_path=str(runner._loaded.config.path),
        config_hash=config_hash if config_hash is not None else runner._risk_config_hash(),
        session_id=runner.session_id,
        trading_session=trading_session,
        locked_in_bar_timestamp=bar_payload["timestamp"],
        symbol=normalized,
        side=side.value,
        intent_class=intent_class,
        expected_stage=runner_intent.expected_stage,
        expected_max_positions=runner_intent.expected_max_positions,
        expected_max_position_pct=runner_intent.expected_max_position_pct,
        expected_daily_loss_cap_pct=runner_intent.expected_daily_loss_cap_pct,
        intent_payload_json={
            "symbol": runner_intent.symbol,
            "side": side.value,
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


def _pin_clock_to_locked_in_bar(runner, symbol: str = "SPY") -> None:
    """Pin the runner clock to the locked-in bar's session so get_active admits it
    (the persisted 7-day expiry window stays open) and the drain re-eval is stable.
    """
    latest_ts = runner._data_provider._bars_by_symbol[symbol].latest().timestamp
    pinned = latest_ts.to_pydatetime()
    runner._now = lambda: pinned


def _build_open_runner(tmp_path, strategy_config_dir, risk_defaults_file):
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=True,
    )
    _pin_clock_to_locked_in_bar(runner)
    return runner, broker, provider, event_store


# ---------------------------------------------------------------------------
# Drain -> submit through the chokepoint
# ---------------------------------------------------------------------------


def test_at_open_drain_reevaluates_and_submits_via_chokepoint(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    seeded_key = f"{runner._strategy_id}|2026-06-19|buy|SPY"
    # The re-eval reproduces the BUY (same symbol+side, positive quantity).
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    captured = _record_submits(runner)

    result = runner.run_cycle()

    assert result == []
    assert len(broker.submit_calls) == 1
    assert len(captured) == 1
    assert captured[0]["idempotency_key"] == seeded_key
    override = captured[0]["latest_bar_override"]
    expected = _locked_in_bar_from_provider(runner)
    assert override.timestamp == datetime.fromisoformat(expected["timestamp"])
    assert override.close == expected["close"]
    # The CAS flipped the row to consumed, tagged with the draining session.
    row = event_store.get_queued_intent(intent_id)
    assert row.status == "consumed"
    assert row.consumed_by == runner.session_id
    # The drain never touches the post-close watermark (I-3).
    assert runner._last_processed_bar_at is None


def test_at_open_drain_does_not_suppress_post_close_eval(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An open-cycle drain must leave the watermark untouched so the later
    post-close lockin still advances it (I-3 — the drain did not poison it)."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])

    runner.run_cycle()  # open-cycle drain
    assert runner._last_processed_bar_at is None

    # Flip to market-closed and drive the 2-cycle post-close lockin.
    broker._market_open = False
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner._now = lambda: fake_now[0]
    runner.run_cycle()  # cycle 1: pending stability
    assert runner._last_processed_bar_at is None
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    runner.run_cycle()  # cycle 2: lockin confirms -> watermark advances

    assert runner._last_processed_bar_at is not None


def test_drain_config_hash_mismatch_drops(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A stale config_hash makes get_active drop the row -> no submit; row stays queued."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.BUY, config_hash="STALE"
    )
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "queued"


def test_drain_zero_share_entry_dropped(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A 0-share / absent re-derived BUY is dropped (no submit) and the row is
    NOT marked obsolete — an entry stays queued to expire on its own."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    # Re-eval yields a 0-share BUY for the queued symbol.
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=0.0)])

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "queued"


def test_drain_zero_share_exit_on_flat_ledger_marked_obsolete(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A 0-share / no-match SELL on a flat strategy ledger is moot -> obsolete."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    # Flat ledger (no open lots) + re-eval yields no matching SELL.
    _force_decision(runner, [])

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "obsolete"


def test_drain_side_flip_dropped(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A re-eval that flips the side (queued BUY -> re-derived SELL for the same
    symbol) drops at ``_match_drain_intent``'s side-equality requirement -> no
    submit; the row stays queued (not consumed, not obsolete). Proves the drain
    never fires the wrong direction on a flipped signal."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    # Re-eval re-derives the OPPOSITE side for the same symbol (a SELL).
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=1.0)])

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "queued"


def test_drain_halted_symbol_dropped(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A not-tradable (halted) symbol drops at the drain gate -> no submit; row stays queued."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    broker._symbol_tradable = False

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "queued"


def test_drain_empty_is_noop(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """No queued rows -> the open daily cycle returns [] with no submit and no error."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []

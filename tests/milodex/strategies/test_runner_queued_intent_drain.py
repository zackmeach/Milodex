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

from datetime import datetime, timedelta
from pathlib import Path

from milodex.broker.models import OrderSide, OrderType, TimeInForce
from milodex.core.event_store import QueuedIntentEvent
from milodex.data.models import Bar
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


def _drop_audit_count(event_store, runner) -> int:
    """Count this session's drain-drop audit explanations (no_trade rows)."""
    return sum(
        1
        for e in event_store.list_explanations()
        if e.session_id == runner.session_id and e.decision_type == "no_trade"
    )


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
    # Anchor created_at/expires_at to the runner's clock (not a fixed date) so the
    # 7-day expiry window never rots past the real wall clock and the global sweep
    # can't expire the row before the drain. Mirrors test_runner_expiry_sweep.
    now = runner._now()
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


def _install_fresh_latest_bar(runner, symbol: str = "SPY") -> None:
    """Make ``get_latest_bar`` return a CONFIRMABLY-FRESH bar for the drain.

    ADR 0057 §2: the drain sizes entries and prices the exposure cap on a fresh
    open price from ``get_latest_bar`` (``_fresh_pricing_bar``), which fails
    closed unless the fresh bar is today-dated AND strictly newer than the locked
    daily session bar. The default ``StubProvider.get_latest_bar`` returns the
    locked daily bar verbatim (same timestamp) — a degenerate case that never
    occurs at a real open, where ``get_latest_bar`` yields a today-dated intraday
    minute bar strictly newer than the locked daily bar. Model that realistic
    case here so the standard drain tests exercise the submit path; the fresh
    close equals the locked close so sizing/notional expectations are unchanged.
    Tests that exercise the fail-closed path override this explicitly.
    """
    locked = runner._data_provider._bars_by_symbol[symbol].latest()
    fresh = Bar(
        timestamp=locked.timestamp.to_pydatetime() + timedelta(minutes=1),
        open=locked.open,
        high=locked.high,
        low=locked.low,
        close=locked.close,
        volume=locked.volume,
        vwap=locked.vwap,
    )
    runner._data_provider.get_latest_bar = lambda _sym: fresh


def _build_open_runner(tmp_path, strategy_config_dir, risk_defaults_file):
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=True,
    )
    _pin_clock_to_locked_in_bar(runner)
    _install_fresh_latest_bar(runner)
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
    """A 0-share / absent re-derived BUY is a DECIDED ENTRY drop (Fix #3): no submit,
    the row is marked terminal 'dropped' (NOT left 'queued' to retry every open),
    and a per-row audit explanation is written. A second run_cycle never re-drains
    it (get_active excludes 'dropped')."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    # Re-eval yields a 0-share BUY for the queued symbol.
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=0.0)])

    before = _drop_audit_count(event_store, runner)
    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "dropped"
    # A per-row audit explanation was recorded for the drop.
    assert _drop_audit_count(event_store, runner) == before + 1
    # A second drain cycle does NOT re-drain a terminal 'dropped' row.
    runner.run_cycle()
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "dropped"


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
    submit. This is a DECIDED ENTRY drop (Fix #3): the row is marked terminal
    'dropped' (not left queued to retry). Proves the drain never fires the wrong
    direction on a flipped signal and never re-drains the terminal row."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    # Re-eval re-derives the OPPOSITE side for the same symbol (a SELL).
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=1.0)])

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "dropped"


def test_drain_halted_symbol_dropped(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A not-tradable (halted) symbol is a DECIDED ENTRY drop at the drain gate
    (Fix #3): no submit, and the row is marked terminal 'dropped' (not left queued
    to retry every open until TTL)."""
    runner, broker, provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    broker._symbol_tradable = False

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "dropped"


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


# ---------------------------------------------------------------------------
# Full single-session lifecycle: persist (day-1 post-close) -> drain (day-2 open)
# ---------------------------------------------------------------------------


def test_full_daily_lifecycle_persist_then_drain_single_submit(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """One runner, one session: a day-1 post-close lock-in PERSISTS (no submit),
    the day-2 open DRAINS (exactly one submit through the chokepoint), and a
    re-drain at the same open is a no-op (the consume CAS already claimed the row).

    Clock/bar alignment (the load-bearing choice for the 1D staleness gate):
    ``build_barset`` dates the latest SPY/SHY bar to "real today" (21:00 UTC), and
    the StubBroker's ``latest_completed_session(now)`` returns ``now.date()``. So
    BOTH phases run on the SAME calendar date as the bars — day-1 post-close at
    20:05 UTC and day-2 "open" at 13:30 UTC are the same date, only the
    ``market_open`` flag flips. That keeps ``locked_in_bar.date ==
    latest_completed_session(now).date`` so the drain's session-aware 1D
    staleness gate admits the reconstructed locked-in bar. We toggle market_open
    False->True between phases to model the day boundary without skewing the date.
    """
    # Build CLOSED so the day-1 post-close lock-in path runs (not the open drain).
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    # The regime universe's first symbol is the evaluation symbol (SPY here).
    eval_symbol = runner._evaluation_symbol()
    # Force a one-BUY decision on every evaluation so BOTH the day-1 persist and
    # the day-2 drain re-eval deterministically reproduce the same entry.
    _force_decision(runner, [_intent(eval_symbol, OrderSide.BUY, quantity=1.0)])

    # --- Phase 1: day-1 post-close (market CLOSED) -> persist, do not submit. ---
    locked_bar = provider._bars_by_symbol[eval_symbol].latest()
    bar_date = locked_bar.timestamp.to_pydatetime()
    fake_now = [bar_date.replace(hour=20, minute=5, second=0, microsecond=0)]
    runner._now = lambda: fake_now[0]
    runner.run_cycle()  # cycle 1: pending stability (first observation)
    assert runner._last_processed_bar_at is None
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    persist_result = runner.run_cycle()  # cycle 2: lockin confirms -> persist

    assert persist_result == []
    assert broker.submit_calls == []  # persisted, NOT submitted
    # The watermark advanced exactly once at the confirmed lock-in.
    assert runner._last_processed_bar_at is not None
    watermark = runner._last_processed_bar_at
    # Exactly one queued row exists for this strategy.
    queued = event_store.get_active_queued_intents(
        runner._strategy_id, now=fake_now[0], running_session_id=runner.session_id
    )
    assert len(queued) == 1
    intent_id = queued[0].id
    assert queued[0].status == "queued"

    # --- Phase 2: day-2 open (market OPEN) -> drain submits exactly once. ---
    captured = _record_submits(runner)
    broker._market_open = True
    # At the day-2 open ``get_latest_bar`` yields a fresh intraday bar (strictly
    # newer than the locked daily session bar, same close) — the drain prices the
    # cap on it (ADR 0057 §2). Without this the default stub echoes the locked bar
    # verbatim (not strictly newer) and the fresh-price gate fails closed.
    _install_fresh_latest_bar(runner, eval_symbol)
    # Same calendar date as the locked-in bar (keeps the staleness gate aligned),
    # at a market-open wall time. The persisted expires_at is 7 days out, so the
    # row is still inside its expiry window.
    fake_now[0] = bar_date.replace(hour=13, minute=30, second=0, microsecond=0)
    drain_result = runner.run_cycle()

    assert drain_result == []
    assert len(broker.submit_calls) == 1  # the drain submitted through the chokepoint
    assert len(captured) == 1
    assert captured[0]["idempotency_key"] == queued[0].idempotency_key
    # The drain submitted via the chokepoint with the reconstructed locked-in bar.
    override = captured[0]["latest_bar_override"]
    assert override.timestamp == locked_bar.timestamp.to_pydatetime()
    assert override.close == locked_bar.close
    # I-3: the open-cycle drain did NOT touch the post-close watermark.
    assert runner._last_processed_bar_at == watermark
    # The CAS consumed the row exactly once, tagged with the draining session.
    row = event_store.get_queued_intent(intent_id)
    assert row.status == "consumed"
    assert row.consumed_by == runner.session_id

    # --- Phase 3: re-drain at the same open -> no second submit (CAS idempotent). ---
    redrain_result = runner.run_cycle()

    assert redrain_result == []
    assert len(broker.submit_calls) == 1  # STILL one: get_active now returns empty
    assert len(captured) == 1
    # Watermark still untouched after the re-drain.
    assert runner._last_processed_bar_at == watermark

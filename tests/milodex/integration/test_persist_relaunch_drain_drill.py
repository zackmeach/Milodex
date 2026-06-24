"""End-to-end operational drill for queue-at-open (ADR 0057), Phase 7.

The capstone integration test for the daily queue-at-open mechanism. It
exercises the full lifecycle across a *session boundary* — the part no unit
test can reach, because the clean-handoff fence (I-4) and the consume CAS only
have meaning when a SECOND process inherits the first's durable state:

    session A (daily, market CLOSED)
        post-close lock-in PERSISTS a QueuedIntentEvent (status='queued')
        instead of submitting (the market is shut; an order would be vetoed)
    A.shutdown(mode="controlled_stop")   -> exit_reason='controlled_stop'
    session B (NEW session, market OPEN)
        at-open DRAIN re-evaluates the inherited intent, the consume CAS flips
        the row to 'consumed', and the order is SUBMITTED + FILLED
    session C (NEW session, market OPEN)
        nothing left to drain — the CAS already consumed the row (idempotency)

Sessions A, B, and C share ONE event_store and ONE broker so B/C see A's
persisted row AND A's controlled_stop exit_reason — the two facts the fence
keys off. A is fully shut down (ended_at written) BEFORE B is constructed, so
B's __init__ orphan-reconcile (which only sweeps runs still OPEN) leaves A's
closed run untouched and the clean handoff survives.

The strategy's ``evaluate`` is stubbed to a fixed, small intent (a 10-share SHY
BUY / SELL) so the drill controls symbol+side+class deterministically and the
order stays well under every risk cap — the mechanism under test is the
persist/handoff/drain/CAS chain, not the regime signal.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from milodex.broker.models import AccountInfo, OrderSide, OrderType, TimeInForce
from milodex.execution import ExecutionService
from milodex.execution.models import TradeIntent
from milodex.strategies.base import DecisionReasoning, StrategyDecision
from milodex.strategies.runner import StrategyRunner
from tests.milodex._helpers.promotion import seed_frozen_manifest
from tests.milodex.strategies.test_runner import (
    StubBroker,
    StubProvider,
    build_barset,
    build_service,
)

# strategy_config_dir / risk_defaults_file fixtures are provided via conftest.py.

STRATEGY_ID = "regime.daily.sma200_rotation.spy_shy.v1"


# ---------------------------------------------------------------------------
# Drill harness: ONE store + ONE broker + ONE provider shared across sessions.
# ---------------------------------------------------------------------------


def _stub_decision(side: OrderSide, *, narrative: str) -> StrategyDecision:
    """A fixed, small SHY intent the drill fully controls.

    10 shares of SHY at ~$10 = $100 ≈ 1% of $10k equity — under every cap, so
    the drain submit is gated only by the mechanism (handoff + CAS + staleness),
    not by an exposure veto. Side selects entry (BUY) vs exit (SELL); the
    runner derives intent_class from the side (BUY->entry, SELL->exit).
    """
    intent = TradeIntent(
        symbol="SHY",
        side=side,
        quantity=10.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    reasoning = DecisionReasoning(
        rule="regime.ma_filter_cross",
        narrative=narrative,
        triggering_values={"close": 10.0},
        threshold={"ma": 10.0},
    )
    return StrategyDecision(intents=[intent], reasoning=reasoning)


def _stub_evaluate(runner: StrategyRunner, side: OrderSide, *, narrative: str) -> None:
    """Force this runner's strategy to emit exactly the drill intent each cycle."""
    runner._loaded.strategy.evaluate = (  # type: ignore[method-assign]
        lambda bars, context: _stub_decision(side, narrative=narrative)
    )


def _new_runner(
    *,
    strategy_config_dir: Path,
    broker: StubBroker,
    provider: StubProvider,
    service: ExecutionService,
    event_store,
) -> StrategyRunner:
    """Construct a fresh StrategyRunner over the SHARED store+broker+provider.

    Mirrors the ``StrategyRunner(...)`` constructor call in
    ``tests.milodex.strategies.test_runner._build_lockin_runner`` but does NOT
    create its own store — the drill threads one store through every session so
    the clean-handoff fence and the consume CAS observe cross-session state.
    """
    return StrategyRunner(
        strategy_id=STRATEGY_ID,
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
        close_lockin_min_interval_seconds=30.0,
        close_lockin_max_wait_seconds=300.0,
    )


def _build_shared_world(
    *,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Build the shared broker/provider/service/store and seed the manifest.

    market_open starts False (session A is post-close). fill_submits=True from
    the outset is inert for A (A never submits) and lets B's drain end in a
    named fill.
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        market_open=False,
        fill_submits=True,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    seed_frozen_manifest(event_store, strategy_config_dir / "regime_runner.yaml")
    return broker, provider, service, event_store


def _persist_with_session_a(
    *,
    strategy_config_dir: Path,
    broker: StubBroker,
    provider: StubProvider,
    service: ExecutionService,
    event_store,
    side: OrderSide,
    narrative: str,
) -> StrategyRunner:
    """Drive session A's 2-cycle post-close lock-in so it persists ONE intent.

    Returns the (not-yet-shutdown) runner A. The clock is pinned to the same
    UTC date as the latest fixture bar (post-close, 20:05) so the
    current-session guard admits lock-in; a +30s second cycle confirms the
    stability window.
    """
    runner_a = _new_runner(
        strategy_config_dir=strategy_config_dir,
        broker=broker,
        provider=provider,
        service=service,
        event_store=event_store,
    )
    _stub_evaluate(runner_a, side, narrative=narrative)
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    a_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner_a._now = lambda: a_now[0]

    runner_a.run_cycle()  # cycle 1: pending stability, no persist
    a_now[0] = a_now[0] + timedelta(seconds=30)
    runner_a.run_cycle()  # cycle 2: stable -> lock in -> persist
    return runner_a


# ---------------------------------------------------------------------------
# Test 1 — the happy chain: persist -> controlled_stop -> relaunch -> drain ->
#          consume CAS -> fill, then idempotent no-op on a third relaunch.
# ---------------------------------------------------------------------------


def test_persist_controlled_stop_relaunch_drain_fill_chain(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    broker, provider, service, event_store = _build_shared_world(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )

    # --- Session A: post-close lock-in PERSISTS a queued BUY -----------------
    runner_a = _persist_with_session_a(
        strategy_config_dir=strategy_config_dir,
        broker=broker,
        provider=provider,
        service=service,
        event_store=event_store,
        side=OrderSide.BUY,
        narrative="A locks in a SHY entry at close",
    )

    queued_after_a = event_store.list_queued_intents_by_status("queued")
    assert len(queued_after_a) == 1, "A's post-close lock-in persists exactly one queued intent."
    queued_row = queued_after_a[0]
    assert queued_row.intent_class == "entry"
    assert queued_row.session_id == runner_a.session_id
    idempotency_key = queued_row.idempotency_key
    assert broker.submit_calls == [], "Persist must NOT touch the broker — the market is closed."

    # --- A shuts down cleanly BEFORE B is constructed -----------------------
    # Ordering is load-bearing: A's run must be CLOSED (ended_at written) before
    # B exists, or B's __init__ orphan-reconcile would mark A's open run
    # 'orphan_recovered' and break the controlled_stop clean handoff.
    runner_a.shutdown(mode="controlled_stop")
    a_run = next(
        run for run in event_store.list_strategy_runs() if run.session_id == runner_a.session_id
    )
    assert a_run.exit_reason == "controlled_stop"
    assert a_run.ended_at is not None

    # --- Session B (NEW session, market OPEN, fills enabled): DRAIN ----------
    broker._market_open = True
    runner_b = _new_runner(
        strategy_config_dir=strategy_config_dir,
        broker=broker,
        provider=provider,
        service=service,
        event_store=event_store,
    )
    assert runner_b.session_id != runner_a.session_id
    _stub_evaluate(runner_b, OrderSide.BUY, narrative="B re-derives the same SHY entry at open")
    # B's drain logic runs under its pinned clock; the SERVICE staleness gate
    # uses the real wall clock + StubBroker.latest_completed_session(now)=now.date(),
    # and the locked-in bar (build_barset) is dated to the real "today", so the
    # bar's session date == the latest completed session by construction.
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    runner_b._now = lambda: latest_ts.to_pydatetime().replace(hour=14, minute=30)

    runner_b.run_cycle()

    # The CAS consumed the row exactly once, attributed to B.
    consumed_row = event_store.get_queued_intent(queued_row.id)
    assert consumed_row is not None
    assert consumed_row.status == "consumed", "the drain's submit CAS must consume the row"
    assert consumed_row.consumed_by == runner_b.session_id
    assert consumed_row.idempotency_key == idempotency_key

    # Exactly one broker submit, and the recorded trade reflects the fill.
    assert len(broker.submit_calls) == 1, "the drain submits the inherited intent exactly once"
    b_trades = [t for t in event_store.list_trades() if t.session_id == runner_b.session_id]
    assert len(b_trades) == 1, "B records exactly one trade for the drained submit"
    assert b_trades[0].broker_status == "filled", (
        "the StubBroker fill must surface as broker_status='filled' on the trade row"
    )
    assert b_trades[0].symbol == "SHY"
    assert b_trades[0].side == "buy"

    # The decision->persist->drain link survived: the consumed row still carries
    # the reasoning captured at lock-in.
    assert consumed_row.reasoning_json not in (None, "", "{}"), (
        "the persisted decision reasoning must survive into the consumed row"
    )

    # --- Session C (NEW session, market OPEN): idempotent no-op -------------
    runner_c = _new_runner(
        strategy_config_dir=strategy_config_dir,
        broker=broker,
        provider=provider,
        service=service,
        event_store=event_store,
    )
    assert runner_c.session_id not in (runner_a.session_id, runner_b.session_id)
    _stub_evaluate(runner_c, OrderSide.BUY, narrative="C finds nothing left to drain")
    runner_c._now = lambda: latest_ts.to_pydatetime().replace(hour=14, minute=35)

    runner_c.run_cycle()

    assert len(broker.submit_calls) == 1, (
        "the row is already consumed; C has nothing to drain -> still exactly one submit"
    )


# ---------------------------------------------------------------------------
# Test 2 — an UNCLEAN relaunch must DROP a queued EXIT (fence fails) and alert.
# ---------------------------------------------------------------------------


def test_relaunch_without_controlled_stop_drops_exit_intent(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    broker, provider, service, event_store = _build_shared_world(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )

    # --- Session A: post-close lock-in PERSISTS a queued EXIT ----------------
    runner_a = _persist_with_session_a(
        strategy_config_dir=strategy_config_dir,
        broker=broker,
        provider=provider,
        service=service,
        event_store=event_store,
        side=OrderSide.SELL,
        narrative="A locks in a SHY exit at close",
    )
    queued_after_a = event_store.list_queued_intents_by_status("queued")
    assert len(queued_after_a) == 1
    exit_row = queued_after_a[0]
    assert exit_row.intent_class == "exit", "a SELL persists as an exit-class intent"

    # --- A shuts down UNCLEANLY (interrupted) -------------------------------
    runner_a.shutdown(mode="interrupted")
    a_run = next(
        run for run in event_store.list_strategy_runs() if run.session_id == runner_a.session_id
    )
    assert a_run.exit_reason == "interrupted"

    # --- Session B (NEW session, market OPEN): drain finds the fence FAILED --
    broker._market_open = True
    runner_b = _new_runner(
        strategy_config_dir=strategy_config_dir,
        broker=broker,
        provider=provider,
        service=service,
        event_store=event_store,
    )
    _stub_evaluate(runner_b, OrderSide.SELL, narrative="B re-derives the exit but cannot drain it")
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    runner_b._now = lambda: latest_ts.to_pydatetime().replace(hour=14, minute=30)

    runner_b.run_cycle()

    # The exit was NOT drained: no broker call, no consumed row.
    assert broker.submit_calls == [], "an unclean-handoff EXIT must not reach the broker"
    assert event_store.list_queued_intents_by_status("consumed") == [], "nothing was consumed"

    # The dropped EXIT raised exactly one operator alert and the row is retired.
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1, "a dropped EXIT must surface exactly one operator alert"
    assert alerts[0].symbol == "SHY"

    stranded_row = event_store.get_queued_intent(exit_row.id)
    assert stranded_row is not None
    assert stranded_row.status == "obsolete", (
        "the stranded EXIT is retired to 'obsolete' so it does not re-alert every open cycle"
    )

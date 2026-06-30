"""Per-intent submit isolation on the LIVE intraday submit loop (PR-4,
runner.py ~line 434).

One intent whose ``submit_paper`` RAISES must not abort its siblings: the loop
catches per intent, logs, continues, and returns the results of the intents that
did submit. The drain's post-submit asymmetry guard is mirrored — an EXIT raise
emits the ``exit_intent_dropped`` operator alert (a raise may have stranded or
half-placed a live position), an ENTRY raise stays silent.

These tests drive the real intraday cycle (regime config switched to 1Min,
market open) with a forced two-intent decision, wrapping ``submit_paper`` so a
chosen symbol raises while the sibling submits through the real service.
"""

from __future__ import annotations

from pathlib import Path

from milodex.broker.models import AccountInfo, OrderSide, OrderType, TimeInForce
from milodex.execution.models import TradeIntent
from milodex.strategies.base import DecisionReasoning, StrategyDecision
from milodex.strategies.runner import StrategyRunner
from tests.milodex.strategies.test_runner import (
    StubBroker,
    StubProvider,
    build_barset,
    build_service,
    make_regime_config_intraday,
    pin_clock_after_latest_bar,
)


def _intent(symbol: str, side: OrderSide, quantity: float = 1.0) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )


def _force_decision(runner, intents: list[TradeIntent]) -> None:
    runner._loaded.strategy.evaluate = lambda bars, ctx: StrategyDecision(
        intents=intents,
        reasoning=DecisionReasoning(rule="regime.ma_filter_cross", narrative="isolation test"),
    )


def _raise_submit_for(runner, *, raise_symbol: str) -> None:
    """Wrap submit_paper so the chosen symbol raises; siblings hit the real service."""
    real_submit = runner._execution_service.submit_paper

    def wrapped(intent, **kwargs):
        if intent.normalized_symbol() == raise_symbol:
            raise RuntimeError("broker connection reset mid-submit")
        return real_submit(intent, **kwargs)

    runner._execution_service.submit_paper = wrapped


def _build_intraday_runner(tmp_path, strategy_config_dir, risk_defaults_file):
    config_path = make_regime_config_intraday(strategy_config_dir)
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
        market_open=True,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, config_path)
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )
    pin_clock_after_latest_bar(runner, provider)
    return runner, broker, provider, event_store


def test_one_poison_intent_does_not_abort_siblings(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """SPY's submit raises; the SHY sibling still submits through the broker and is
    returned in results. The raise does not crash the cycle."""
    runner, broker, _provider, _event_store = _build_intraday_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    _force_decision(
        runner,
        [
            _intent("SPY", OrderSide.BUY, quantity=1.0),
            _intent("SHY", OrderSide.BUY, quantity=1.0),
        ],
    )
    _raise_submit_for(runner, raise_symbol="SPY")

    results = runner.run_cycle()

    # The poison SPY intent raised; SHY still reached the broker.
    assert [call["symbol"] for call in broker.submit_calls] == ["SHY"]
    # Only the sibling's result is returned (the raised one is dropped).
    assert len(results) == 1
    assert results[0].execution_request.symbol == "SHY"


def test_exit_submit_raise_emits_operator_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An EXIT (SELL) submit that raises emits an exit_intent_dropped alert
    (reason submit_error) — a stranded/half-placed live position must be
    operator-visible. The ENTRY sibling submits cleanly."""
    runner, broker, _provider, event_store = _build_intraday_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    _force_decision(
        runner,
        [
            _intent("SPY", OrderSide.SELL, quantity=1.0),
            _intent("SHY", OrderSide.BUY, quantity=1.0),
        ],
    )
    _raise_submit_for(runner, raise_symbol="SPY")

    results = runner.run_cycle()

    assert [call["symbol"] for call in broker.submit_calls] == ["SHY"]
    assert len(results) == 1
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SPY"
    assert alerts[0].side == "sell"
    assert alerts[0].context_json["reason"] == "submit_error"


def test_entry_submit_raise_stays_silent(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An ENTRY (BUY) submit that raises is log-only: no exit_intent_dropped
    alert (asymmetry guard). The sibling still submits."""
    runner, broker, _provider, event_store = _build_intraday_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    _force_decision(
        runner,
        [
            _intent("SPY", OrderSide.BUY, quantity=1.0),
            _intent("SHY", OrderSide.BUY, quantity=1.0),
        ],
    )
    _raise_submit_for(runner, raise_symbol="SPY")

    results = runner.run_cycle()

    assert [call["symbol"] for call in broker.submit_calls] == ["SHY"]
    assert len(results) == 1
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []


def test_raised_intent_key_stays_processed_no_resubmit_on_repoll(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A raised submit leaves the intent key marked processed: submit_paper here
    has no idempotency_key, so a raise may have placed an order at the broker.
    Re-polling the same bar must NOT re-submit it (fail-safe toward never
    re-firing a possibly-placed order)."""
    runner, _broker, _provider, _event_store = _build_intraday_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])

    submit_attempts: list[str] = []

    def wrapped(intent, **kwargs):
        submit_attempts.append(intent.normalized_symbol())
        raise RuntimeError("broker connection reset mid-submit")

    runner._execution_service.submit_paper = wrapped

    first = runner.run_cycle()
    # Same bar re-polled (already_seen short-circuits, but assert the dedup
    # backstop too): no second submit attempt for the already-processed key.
    second = runner.run_cycle()

    assert first == []
    assert second == []
    assert submit_attempts == ["SPY"]

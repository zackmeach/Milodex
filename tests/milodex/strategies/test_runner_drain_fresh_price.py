"""Fresh-price drain sizing + cap pricing (ADR 0057 §2) — the queue-at-open drain
must size ENTRIES and price the risk exposure cap on a FRESH open price obtained
from ``get_latest_bar``, NOT on the stale locked-in close.

The locked-in daily session bar is still legitimately needed by the session-aware
1D staleness GATE (``context.latest_bar``) — a today-dated intraday minute bar
would fail that gate. But it must NOT drive sizing/pricing across an overnight
gap: an open that gaps up doubles the real notional of an order sized on the
stale close, silently defeating the account exposure cap.

These tests exercise the drain half of the queue-at-open lifecycle (a seeded
queued row + one market-open daily cycle) through the real ExecutionService +
StubBroker path, monkeypatching ``get_latest_bar`` to inject a gapped fresh
price distinct from the locked close.
"""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from pathlib import Path

from milodex.broker.models import OrderSide
from milodex.data.models import Bar
from milodex.strategies.runner import _DRAIN_EXIT_NO_FRESH_PRICE_RETRY_WINDOW_SECONDS
from tests.milodex.strategies.test_runner_queued_intent_drain import (
    _build_open_runner,
    _force_decision,
    _intent,
    _seed_queued_entry,
)


def _locked_bar(runner, symbol: str = "SPY") -> Bar:
    """The stub provider's latest daily bar == the locked-in session bar."""
    return runner._data_provider._bars_by_symbol[symbol].latest()


def _patch_fresh_latest_bar(
    runner,
    *,
    close: float,
    symbol: str = "SPY",
    timestamp=None,
) -> list[str]:
    """Override ``get_latest_bar`` to return a FRESH bar (distinct close).

    The fresh bar is timestamped strictly after the locked daily session bar and
    on the current session date, so it satisfies the drain's fail-closed
    freshness predicate. Returns the captured ``get_latest_bar`` call symbols.
    """
    locked = _locked_bar(runner, symbol)
    fresh_ts = (
        timestamp
        if timestamp is not None
        else (locked.timestamp.to_pydatetime() + timedelta(minutes=1))
    )
    fresh_bar = Bar(
        timestamp=fresh_ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        vwap=close,
    )
    calls: list[str] = []

    def fake_latest(sym: str) -> Bar:
        calls.append(sym)
        return fresh_bar

    runner._data_provider.get_latest_bar = fake_latest
    return calls


# ---------------------------------------------------------------------------
# Blocker 1: gap-up ENTRY must size + price on the FRESH open, not the stale close.
# ---------------------------------------------------------------------------


def test_drain_entry_gap_up_resizes_to_fresh_price(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Locked close $10, fresh open $20: an ENTRY sized 800 shares on the stale
    $10 (an $8k = 80% notional, under the 85% cap) would be $16k = 160% of a $10k
    account at the real $20 open. The drain must resize to the fresh price so the
    submitted notional reflects $20 — and stay under the cap."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    # Re-eval reproduces the BUY sized on the stale $10 close: 800 shares.
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=800.0)])
    _patch_fresh_latest_bar(runner, close=20.0)

    result = runner.run_cycle()

    assert result == []
    # Submitted exactly once, NOT blocked.
    assert len(broker.submit_calls) == 1
    submitted_qty = float(broker.submit_calls[0]["quantity"])
    # Resized down: floor(800 * 10 / 20) == 400 shares.
    assert submitted_qty == 400.0
    # The real notional at the fresh $20 is 400*20 = $8000 = 80% of $10k — the
    # over-cap 160% the stale path would have produced is gone.
    assert submitted_qty * 20.0 <= 0.85 * 10_000.0
    assert event_store.get_queued_intent(intent_id).status == "consumed"


def test_drain_entry_resize_matches_floor_formula(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Post-drain ENTRY quantity == floor(quantity * locked_close / fresh_price)."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    locked_close = _locked_bar(runner).close  # 10.0
    fresh_price = 13.0
    queued_qty = 700.0
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=queued_qty)])
    _patch_fresh_latest_bar(runner, close=fresh_price)

    runner.run_cycle()

    assert len(broker.submit_calls) == 1
    expected = math.floor(queued_qty * locked_close / fresh_price)
    assert float(broker.submit_calls[0]["quantity"]) == float(expected)


def test_drain_entry_resize_to_zero_is_dropped(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A fresh price so high the resize floors to 0 shares must NOT submit a
    0-share order. This is a DECIDED ENTRY drop (Fix #3): the row is marked terminal
    'dropped' (a fresh-price determination, not a transient can't-evaluate)."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    # 1 share at locked $10 -> floor(1 * 10 / 10000) == 0 at fresh $10000.
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    _patch_fresh_latest_bar(runner, close=10_000.0)

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "dropped"


# ---------------------------------------------------------------------------
# Blocker 1: EXIT quantity is NOT resized, but its cap prices on the fresh price.
# ---------------------------------------------------------------------------


def test_drain_exit_not_resized_but_priced_on_fresh(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A held-lot EXIT submits the held quantity UNCHANGED (exits sell the lot),
    while the cap/notional prices on the fresh price (get_latest_bar is consulted)."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    calls = _patch_fresh_latest_bar(runner, close=20.0)

    result = runner.run_cycle()

    assert result == []
    assert len(broker.submit_calls) == 1
    # Exit quantity unchanged — the held lot is sold in full, NOT rescaled.
    assert float(broker.submit_calls[0]["quantity"]) == 5.0
    # The fresh price was consulted for cap pricing.
    assert "SPY" in calls
    assert event_store.get_queued_intent(intent_id).status == "consumed"


# ---------------------------------------------------------------------------
# Blocker 1: fail-closed when the fresh price is missing / stale.
# ---------------------------------------------------------------------------


def test_drain_entry_no_fresh_price_drops_and_stays_queued(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A fresh bar that is NOT confirmably current (same timestamp as the locked
    bar, i.e. not strictly newer) fails closed: the ENTRY drops, no submit, and
    the row stays queued (retry/expire). No alert (entries are silent)."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=100.0)])
    # Fresh ts == locked ts -> not strictly newer -> fail closed.
    locked = _locked_bar(runner)
    _patch_fresh_latest_bar(runner, close=20.0, timestamp=locked.timestamp.to_pydatetime())

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []


def test_drain_entry_fresh_price_raises_drops_and_stays_queued(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """If ``get_latest_bar`` raises (provider outage), the ENTRY fails closed:
    no submit, row stays queued (re-queuable for the next open / sweep). No
    alert (entries are silent)."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=100.0)])

    def boom(_symbol: str) -> Bar:
        raise RuntimeError("provider unreachable")

    runner._data_provider.get_latest_bar = boom

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []


def test_drain_exit_no_fresh_price_first_attempt_stays_queued_no_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    caplog,
):
    """An EXIT with no confirmable fresh price on the FIRST drain attempt is NOT
    retired: the row stays ``queued`` (the next ~60s open poll retries it), no
    ``exit_intent_dropped`` alert is emitted, and the miss is logged. IEX-thin
    symbols (observed 2026-07-20: XLF/XLV/JNJ ~17s post-bell) have no
    current-session minute bar in the open's first minute — retiring on the
    first miss stranded live positions with zero risk evaluations."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    # Fresh ts == locked ts -> not strictly newer -> fail closed.
    locked = _locked_bar(runner)
    _patch_fresh_latest_bar(runner, close=20.0, timestamp=locked.timestamp.to_pydatetime())

    with caplog.at_level(logging.WARNING):
        result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert "leaving queued" in caplog.text


def test_drain_exit_fresh_price_raises_first_attempt_stays_queued(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """If ``get_latest_bar`` RAISES for an EXIT, the "can't be obtained" case
    routes through the SAME bounded-retry branch as an unconfirmable bar
    (``_fresh_pricing_bar`` catches the raise and returns ``None``): first
    attempt -> row stays queued, no alert, no submit."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])

    def boom(_symbol: str) -> Bar:
        raise RuntimeError("provider unreachable")

    runner._data_provider.get_latest_bar = boom

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []
    assert event_store.get_queued_intent(intent_id).status == "queued"


def test_drain_exit_no_fresh_price_recovers_within_window_submits(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A fresh price appearing on a LATER drain attempt within the retry window
    proceeds through the normal re-validation path: risk evaluation + submit
    through the chokepoint, row consumed, no alert ever emitted."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    # Attempt 1: fresh ts == locked ts -> not confirmably current -> stays queued.
    locked = _locked_bar(runner)
    _patch_fresh_latest_bar(runner, close=20.0, timestamp=locked.timestamp.to_pydatetime())
    runner.run_cycle()
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "queued"

    # Attempt 2 (~next open poll, well inside the window): fresh price appears.
    _patch_fresh_latest_bar(runner, close=20.0)
    result = runner.run_cycle()

    assert result == []
    assert len(broker.submit_calls) == 1
    # Exit quantity unchanged — the held lot is sold in full, NOT rescaled.
    assert float(broker.submit_calls[0]["quantity"]) == 5.0
    assert event_store.get_queued_intent(intent_id).status == "consumed"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []


def test_drain_exit_no_fresh_price_window_expired_alerts_and_obsoletes(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """When the retry window closes with STILL no fresh price, the EXIT is
    retired exactly as before the retry window existed: exactly ONE
    ``exit_intent_dropped`` alert (reason ``no_fresh_price``) + row
    ``obsolete``. A further drain pass does not re-alert (the retired row is
    no longer served)."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    # Fresh price never becomes confirmable.
    locked = _locked_bar(runner)
    _patch_fresh_latest_bar(runner, close=20.0, timestamp=locked.timestamp.to_pydatetime())
    fake_now = [runner._now()]
    runner._now = lambda: fake_now[0]

    runner.run_cycle()  # first attempt -> stays queued, no alert
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []
    assert event_store.get_queued_intent(intent_id).status == "queued"

    # Advance past the retry window and drain again -> final drop.
    fake_now[0] = fake_now[0] + timedelta(
        seconds=_DRAIN_EXIT_NO_FRESH_PRICE_RETRY_WINDOW_SECONDS + 60.0
    )
    runner.run_cycle()

    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SPY"
    assert alerts[0].context_json["reason"] == "no_fresh_price"
    assert event_store.get_queued_intent(intent_id).status == "obsolete"
    assert broker.submit_calls == []

    # A third drain pass never re-alerts: one alert per intent, on the final drop.
    runner.run_cycle()
    assert len(event_store.list_operator_alerts(alert_type="exit_intent_dropped")) == 1


# ---------------------------------------------------------------------------
# I-2: a stale/wrong LOCKED bar still BLOCKs at the session-aware staleness gate
# even with a valid fresh price for the cap.
# ---------------------------------------------------------------------------


def test_drain_stale_locked_bar_still_blocks_with_fresh_price(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Threading a fresh cap price must NOT weaken the 1D staleness gate: a
    locked-in bar whose session date is NOT the latest completed session is still
    BLOCKED (no order at the broker), even though a valid fresh price exists."""
    runner, broker, _provider, event_store = _build_open_runner(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    # Seed with a locked-in bar dated to a prior (stale) session.
    locked = _locked_bar(runner)
    stale_ts = locked.timestamp.to_pydatetime() - timedelta(days=10)
    stale_payload = {
        "timestamp": stale_ts.isoformat(),
        "open": locked.open,
        "high": locked.high,
        "low": locked.low,
        "close": locked.close,
        "volume": locked.volume,
        "vwap": locked.vwap,
    }
    _seed_queued_entry(
        event_store,
        runner,
        symbol="SPY",
        side=OrderSide.BUY,
        locked_in_bar=stale_payload,
    )
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=100.0)])
    # A perfectly valid fresh price for the cap.
    _patch_fresh_latest_bar(runner, close=11.0)

    result = runner.run_cycle()

    assert result == []
    # The staleness gate blocked it BEFORE any broker submit.
    assert broker.submit_calls == []

"""Cross-symbol ENTRY rescale (ADR 0057 §2, risk-review regression) — the
queue-at-open drain must rescale an ENTRY's at-open quantity against the TRADED
symbol's OWN locked close, NOT universe[0]'s locked close.

The bug: ``_persist_queued_intent`` always stores the evaluation symbol's
(``universe[0]``) bar as ``locked_in_bar``, regardless of which symbol the intent
trades. The drain then rescaled with ``decision_bar.close`` (universe[0]'s close)
as the numerator, while ``match.quantity`` was sized by the strategy on the TRADED
symbol's own close and ``pricing_price`` is the TRADED symbol's fresh price. For a
cross-sectional 1D strategy whose traded symbol != universe[0] this mixed two
different symbols' prices and produced a grossly wrong (often massively oversized)
quantity. Single-symbol strategies are unaffected (traded symbol == universe[0]),
which is why every SPY-only drain test passed and missed this.

The fix sources the rescale numerator from ``eval_bars[queued.symbol].latest().close``
— the traded symbol's own locked close, which is exactly the price the
cross-sectional sizers (``unit_price = candidate["latest_close"]``) sized on — and
fails CLOSED (entry stays queued / exit alerts + retires) when that close is not
obtainable.

These tests use the 2-symbol ``[SPY, SHY]`` regime universe but seed a queued
ENTRY on SHY (the non-evaluation symbol) with SHY priced distinctly from SPY, so
the cross-symbol arithmetic is observable through the real ExecutionService +
StubBroker path.
"""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path

from milodex.broker.models import OrderSide
from milodex.data.models import Bar
from tests.milodex.strategies.test_runner import _build_lockin_runner, build_barset
from tests.milodex.strategies.test_runner_queued_intent_drain import (
    _force_decision,
    _intent,
    _pin_clock_to_locked_in_bar,
    _seed_queued_entry,
)


def _build_open_runner_two_symbol(
    tmp_path,
    strategy_config_dir,
    risk_defaults_file,
    *,
    spy_close: float,
    shy_close: float,
):
    """Build a market-open daily runner over the [SPY, SHY] regime universe with
    SPY (universe[0]) and SHY priced distinctly, so a SHY ENTRY's rescale exposes
    any cross-symbol numerator confusion. Returns the standard 4-tuple."""
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=True,
        initial_bars={
            "SPY": build_barset([spy_close, spy_close, spy_close]),
            "SHY": build_barset([shy_close, shy_close, shy_close]),
        },
    )
    _pin_clock_to_locked_in_bar(runner, "SPY")
    return runner, broker, provider, event_store


def _patch_fresh_for_symbol(runner, *, symbol: str, close: float) -> list[str]:
    """Override ``get_latest_bar`` to return a confirmably-fresh bar (strictly
    newer than the locked SPY session bar, current session) for ``symbol`` with
    the given fresh close. Returns the captured ``get_latest_bar`` call symbols."""
    # The locked bar the drain gates on is universe[0]'s (SPY) — the fresh bar
    # must be strictly newer than THAT timestamp to clear the freshness gate.
    spy_locked = runner._data_provider._bars_by_symbol["SPY"].latest()
    fresh_ts = spy_locked.timestamp.to_pydatetime() + timedelta(minutes=1)
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
# Regression: a SHY ENTRY (traded symbol != universe[0]) must rescale on SHY's
# OWN locked close, not SPY's.
# ---------------------------------------------------------------------------


def test_drain_cross_symbol_entry_rescales_on_traded_symbol_close(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """SPY (universe[0]) locked $10, SHY (traded) locked $40, SHY fresh $40.

    The strategy sized 200 SHY shares on SHY's own $40 close. The correct rescale
    is floor(200 * 40 / 40) = 200 (SHY's own numerator). The pre-fix code used
    SPY's $10 numerator: floor(200 * 10 / 40) = 50 — a ~4x undersize that mixed
    two symbols' prices. (In the symmetric over-size direction the same bug
    produces a ~15x oversize; this fixture isolates the numerator so both submits
    clear the exposure cap and the test discriminates purely on quantity.)
    """
    runner, broker, _provider, event_store = _build_open_runner_two_symbol(
        tmp_path,
        strategy_config_dir,
        risk_defaults_file,
        spy_close=10.0,
        shy_close=40.0,
    )
    # Production persists universe[0]'s (SPY) bar as locked_in_bar for EVERY
    # intent, regardless of which symbol it trades — mirror that here (the seed
    # default uses SPY). The traded symbol is SHY.
    intent_id = _seed_queued_entry(event_store, runner, symbol="SHY", side=OrderSide.BUY)
    # The strategy sized 200 shares on SHY's own $40 close.
    _force_decision(runner, [_intent("SHY", OrderSide.BUY, quantity=200.0)])
    _patch_fresh_for_symbol(runner, symbol="SHY", close=40.0)

    result = runner.run_cycle()

    assert result == []
    assert len(broker.submit_calls) == 1
    submitted_qty = float(broker.submit_calls[0]["quantity"])
    # Correct: floor(200 * SHY_locked(40) / SHY_fresh(40)) == 200.
    # Pre-fix bug: floor(200 * SPY_locked(10) / SHY_fresh(40)) == 50.
    assert submitted_qty == 200.0
    assert event_store.get_queued_intent(intent_id).status == "consumed"


def test_drain_cross_symbol_entry_resize_matches_traded_close_formula(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Post-drain SHY ENTRY quantity == floor(qty * SHY_locked / SHY_fresh),
    using SHY's own locked close — NOT SPY's (universe[0]) locked close."""
    runner, broker, _provider, event_store = _build_open_runner_two_symbol(
        tmp_path,
        strategy_config_dir,
        risk_defaults_file,
        spy_close=10.0,
        shy_close=40.0,
    )
    _seed_queued_entry(event_store, runner, symbol="SHY", side=OrderSide.BUY)
    shy_locked_close = 40.0
    shy_fresh_close = 50.0
    queued_qty = 200.0
    _force_decision(runner, [_intent("SHY", OrderSide.BUY, quantity=queued_qty)])
    _patch_fresh_for_symbol(runner, symbol="SHY", close=shy_fresh_close)

    runner.run_cycle()

    assert len(broker.submit_calls) == 1
    expected = math.floor(queued_qty * shy_locked_close / shy_fresh_close)
    # Sanity: the buggy SPY-numerator result would be floor(200*10/50)=40, distinct
    # from the correct floor(200*40/50)=160.
    assert expected == 160
    assert float(broker.submit_calls[0]["quantity"]) == float(expected)


# ---------------------------------------------------------------------------
# Fail-closed: the traded symbol's locked close is not obtainable.
# ---------------------------------------------------------------------------


def test_drain_cross_symbol_entry_no_sizing_price_stays_queued(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """If the traded symbol is absent from eval_bars (no truncated bars to read a
    locked close from), an ENTRY fails CLOSED: no submit, the row stays queued
    (retry/expire), and no exit alert (entries are silent)."""
    runner, broker, _provider, event_store = _build_open_runner_two_symbol(
        tmp_path,
        strategy_config_dir,
        risk_defaults_file,
        spy_close=10.0,
        shy_close=40.0,
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SHY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SHY", OrderSide.BUY, quantity=200.0)])
    _patch_fresh_for_symbol(runner, symbol="SHY", close=40.0)
    # Drop SHY from the fetched bars so the drain cannot read its locked close.
    real_fetch = runner._fetch_bars_by_symbol

    def fetch_without_shy():
        bars = dict(real_fetch())
        bars.pop("SHY", None)
        return bars

    runner._fetch_bars_by_symbol = fetch_without_shy

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []


def test_drain_cross_symbol_exit_no_sizing_price_alerts_and_obsoletes(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """If the traded symbol's locked close is not obtainable for an EXIT, the
    asymmetry guard fires: alert (reason ``no_sizing_price``) + retire the row.

    An exit quantity is never rescaled, but placing the sizing-price lookup before
    the entry/exit split means an unobtainable traded-symbol close must route an
    exit through the same fail-closed alert path as a missing fresh price — an
    undrained exit can strand a live position."""
    runner, broker, _provider, event_store = _build_open_runner_two_symbol(
        tmp_path,
        strategy_config_dir,
        risk_defaults_file,
        spy_close=10.0,
        shy_close=40.0,
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SHY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SHY": 5.0}
    _force_decision(runner, [_intent("SHY", OrderSide.SELL, quantity=5.0)])
    _patch_fresh_for_symbol(runner, symbol="SHY", close=40.0)
    real_fetch = runner._fetch_bars_by_symbol

    def fetch_without_shy():
        bars = dict(real_fetch())
        bars.pop("SHY", None)
        return bars

    runner._fetch_bars_by_symbol = fetch_without_shy

    result = runner.run_cycle()

    assert result == []
    assert broker.submit_calls == []
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SHY"
    assert alerts[0].context_json["reason"] == "no_sizing_price"
    assert event_store.get_queued_intent(intent_id).status == "obsolete"

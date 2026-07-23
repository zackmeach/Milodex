"""Stale-locked veto retirement (2026-07-23 wedge-loop) — a queued intent whose
drain submit is risk-vetoed with ``stale_market_data`` can NEVER freshen: the
staleness gate evaluates the reconstructed locked-in decision bar, which is
frozen in the row, and staleness is monotone in time for a fixed bar. Live
evidence 2026-07-23: intents locked at the 7/21 close drained after a skipped
session (7/22 no-op), were correctly vetoed
``["disable_condition_active", "stale_market_data"]`` — and then retried every
~90s open poll for hours, writing hundreds of blocked trades/explanations rows.

The drain must retire such an intent after the FIRST staleness veto:

* EXIT: one ``exit_intent_dropped`` alert with the DISTINCT reason
  ``stale_locked_veto`` + row ``obsolete`` (the strategy re-emits a fresh exit
  at the next close-eval — the existing recovery model);
* ENTRY: terminal ``dropped`` via the decided-entry bookkeeping (audit
  ``no_trade`` row, no alert spam).

Non-regression pins: the #374 no-fresh-price EXIT retry window (which CAN heal
intra-session) is untouched, and a NON-staleness risk veto (e.g. a cap veto)
keeps the pre-existing behavior — ENTRY in-memory dedup (row stays queued),
EXIT retries every poll.

Precision split (calendar_unavailable): the staleness gate's THIRD sub-cause —
the exchange calendar could not resolve the latest completed session — is
TRANSIENT (a calendar outage can heal mid-session), unlike the frozen-bar
session-mismatch / 7-day-ceiling sub-causes. It now carries its own reason
code ``calendar_unavailable`` (still fail-closed), and the retire branch —
keyed on ``stale_market_data`` only — must NOT retire it: the intent keeps the
pre-#381 behavior (EXIT retries every poll and proceeds once the calendar
recovers; ENTRY keeps the in-memory veto dedup).
"""

from __future__ import annotations

from pathlib import Path

from milodex.broker.models import OrderSide
from milodex.execution.models import ExecutionResult, ExecutionStatus
from milodex.risk.models import RiskDecision
from tests.milodex.strategies.test_runner import _build_lockin_runner, build_barset
from tests.milodex.strategies.test_runner_queued_intent_drain import (
    _drop_audit_count,
    _force_decision,
    _install_fresh_latest_bar,
    _intent,
    _pin_clock_to_locked_in_bar,
    _record_submits,
    _seed_queued_entry,
)


def _record_submit_results(runner) -> list[ExecutionResult]:
    """Wrap submit_paper to capture RESULTS, delegating to the real method.

    Sibling of ``_record_submits`` (which captures kwargs): the
    calendar-unavailable tests assert on the risk decision's reason codes, so
    they need the returned ``ExecutionResult``, not the threaded kwargs.
    """
    results: list[ExecutionResult] = []
    real_submit = runner._execution_service.submit_paper

    def recording_submit(intent, **kwargs):
        result = real_submit(intent, **kwargs)
        results.append(result)
        return result

    runner._execution_service.submit_paper = recording_submit
    return results


def _build_open_runner_with_history(tmp_path, strategy_config_dir, risk_defaults_file):
    """Open-market runner whose barsets carry enough history that a locked-in
    bar several sessions back still truncates to a NON-empty replay window —
    so a stale-locked intent reaches the risk gate (and its staleness veto)
    instead of failing earlier at the no-sizing-price guard."""
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        initial_bars={
            "SPY": build_barset([10.0] * 15),
            "SHY": build_barset([10.0] * 15),
        },
        market_open=True,
    )
    _pin_clock_to_locked_in_bar(runner)
    _install_fresh_latest_bar(runner)
    return runner, broker, provider, event_store


def _stale_locked_bar_payload(runner, symbol: str, sessions_back: int = 10) -> dict:
    """A locked-in-bar payload for a bar ``sessions_back`` rows before the
    latest — its session date no longer matches the latest completed session,
    so the drain's 1D staleness gate vetoes with ``stale_market_data``."""
    frame = runner._data_provider._bars_by_symbol[symbol].to_dataframe()
    row = frame.iloc[-(sessions_back + 1)]
    return {
        "timestamp": row["timestamp"].isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": int(row["volume"]),
        "vwap": float(row["vwap"]),
    }


# ---------------------------------------------------------------------------
# Stale-locked EXIT: ONE veto, then alert (distinct reason) + obsolete.
# ---------------------------------------------------------------------------


def test_drain_exit_stale_veto_retires_after_first_veto_with_distinct_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A held-lot EXIT whose locked-in bar is a prior (stale) session submits
    ONCE through the chokepoint (real risk gate vetoes: ``stale_market_data``),
    then retires: exactly one ``exit_intent_dropped`` alert with the DISTINCT
    reason ``stale_locked_veto`` + row ``obsolete``. Subsequent drain passes
    never re-submit (no all-day retry loop)."""
    runner, broker, _provider, event_store = _build_open_runner_with_history(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store,
        runner,
        symbol="SPY",
        side=OrderSide.SELL,
        intent_class="exit",
        locked_in_bar=_stale_locked_bar_payload(runner, "SPY"),
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    captured = _record_submits(runner)

    result = runner.run_cycle()

    assert result == []
    # Exactly one chokepoint submit (the risk veto), no broker order.
    assert len(captured) == 1
    assert broker.submit_calls == []
    # Retired with the DISTINCT operator-visible reason.
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SPY"
    assert alerts[0].context_json["reason"] == "stale_locked_veto"
    assert event_store.get_queued_intent(intent_id).status == "obsolete"

    # Second and third drain passes: the retired row is no longer served —
    # no re-submit, no re-alert, no explanation spam.
    runner.run_cycle()
    runner.run_cycle()
    assert len(captured) == 1
    assert len(event_store.list_operator_alerts(alert_type="exit_intent_dropped")) == 1
    assert event_store.get_queued_intent(intent_id).status == "obsolete"


# ---------------------------------------------------------------------------
# Stale-locked ENTRY: ONE veto, then terminal 'dropped' (audit row, no alert).
# ---------------------------------------------------------------------------


def test_drain_entry_stale_veto_dropped_after_first_veto_no_alert(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An ENTRY whose locked-in bar is stale submits ONCE (risk veto), then is
    terminally ``dropped`` via the decided-entry bookkeeping: one audit
    ``no_trade`` explanation, NO operator alert. Subsequent passes never
    re-submit — durable across restarts, unlike the in-memory veto dedup."""
    runner, broker, _provider, event_store = _build_open_runner_with_history(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store,
        runner,
        symbol="SPY",
        side=OrderSide.BUY,
        locked_in_bar=_stale_locked_bar_payload(runner, "SPY"),
    )
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    captured = _record_submits(runner)

    before = _drop_audit_count(event_store, runner)
    result = runner.run_cycle()

    assert result == []
    assert len(captured) == 1
    assert broker.submit_calls == []
    assert event_store.get_queued_intent(intent_id).status == "dropped"
    # One audit no_trade row for the drop; entries stay alert-silent.
    assert _drop_audit_count(event_store, runner) == before + 1
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []

    # Second pass: terminal row not re-served.
    runner.run_cycle()
    assert len(captured) == 1
    assert event_store.get_queued_intent(intent_id).status == "dropped"


# ---------------------------------------------------------------------------
# #374 non-regression: a no-fresh-price EXIT still retries (it CAN heal
# intra-session) while a stale-vetoed EXIT retires immediately.
# ---------------------------------------------------------------------------


def test_drain_no_fresh_price_exit_retries_while_stale_vetoed_exit_retires(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Two EXITs in one drain: SPY has no confirmable fresh price (transient —
    IEX thin open, #374) and must stay ``queued`` retrying inside the bounded
    window with NO alert; SHY has a stale locked bar (permanent) and must
    retire after ONE veto with the ``stale_locked_veto`` alert."""
    runner, broker, _provider, event_store = _build_open_runner_with_history(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    spy_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    shy_id = _seed_queued_entry(
        event_store,
        runner,
        symbol="SHY",
        side=OrderSide.SELL,
        intent_class="exit",
        locked_in_bar=_stale_locked_bar_payload(runner, "SHY"),
    )
    runner._current_positions = lambda: {"SPY": 5.0, "SHY": 5.0}
    _force_decision(
        runner,
        [
            _intent("SPY", OrderSide.SELL, quantity=5.0),
            _intent("SHY", OrderSide.SELL, quantity=5.0),
        ],
    )
    # Per-symbol fresh bars: SPY echoes its own locked bar (NOT strictly newer
    # -> no confirmable fresh price -> #374 retry branch); SHY gets a
    # confirmably-fresh bar so it reaches the risk gate and the staleness veto.
    default_fresh = runner._data_provider.get_latest_bar

    def per_symbol_latest(symbol: str):
        if symbol == "SPY":
            return runner._data_provider._bars_by_symbol["SPY"].latest()
        return default_fresh(symbol)

    runner._data_provider.get_latest_bar = per_symbol_latest
    captured = _record_submits(runner)

    runner.run_cycle()

    # SPY: transient — still queued, retrying, no alert, no submit.
    assert event_store.get_queued_intent(spy_id).status == "queued"
    assert spy_id in runner._drain_exit_no_fresh_price_first_attempt
    # SHY: permanent — one veto submit, retired with the distinct alert.
    assert len(captured) == 1
    assert event_store.get_queued_intent(shy_id).status == "obsolete"
    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    assert alerts[0].symbol == "SHY"
    assert alerts[0].context_json["reason"] == "stale_locked_veto"
    assert broker.submit_calls == []

    # Next poll: SPY retries again (still inside the window), SHY stays retired.
    runner.run_cycle()
    assert event_store.get_queued_intent(spy_id).status == "queued"
    assert len(captured) == 1
    assert len(event_store.list_operator_alerts(alert_type="exit_intent_dropped")) == 1


# ---------------------------------------------------------------------------
# Non-staleness risk veto (e.g. a cap veto): pre-existing behavior UNCHANGED.
# ---------------------------------------------------------------------------


def _blocking_submit_with_codes(runner, reason_codes: list[str]) -> dict:
    """Stub submit_paper to return a BLOCKED result carrying ``reason_codes``
    (models a pre-CAS risk veto: no order, row stays 'queued')."""
    calls = {"n": 0}
    real_request_builder = runner._execution_service._build_execution_request

    def blocked_submit(intent, **kwargs):
        calls["n"] += 1
        request = real_request_builder(
            runner._execution_service._normalize_intent(intent), 10.0, None
        )
        return ExecutionResult(
            status=ExecutionStatus.BLOCKED,
            execution_request=request,
            risk_decision=RiskDecision(
                allowed=False,
                summary="Blocked by risk veto (row stays queued).",
                checks=[],
                reason_codes=list(reason_codes),
            ),
            account=runner._broker.get_account(),
            market_open=True,
            latest_bar=None,
            message="Blocked by risk veto (row stays queued).",
        )

    runner._execution_service.submit_paper = blocked_submit
    return calls


def test_drain_entry_cap_veto_keeps_dedup_row_stays_queued(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A NON-staleness ENTRY veto (cap) keeps the pre-existing behavior: the
    row stays 'queued', the in-memory dedup suppresses re-submits this session,
    and the row is NOT terminally dropped."""
    runner, _broker, _provider, event_store = _build_open_runner_with_history(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    calls = _blocking_submit_with_codes(runner, ["max_total_exposure_exceeded"])

    runner._drain_queued_intents()

    assert calls["n"] == 1
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert intent_id in runner._drain_vetoed_row_ids

    runner._drain_queued_intents()

    assert calls["n"] == 1
    assert event_store.get_queued_intent(intent_id).status == "queued"


def test_drain_exit_calendar_unavailable_stays_queued_then_recovers_and_submits(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Transient sub-cause (c): a calendar-resolution outage vetoes the drained
    EXIT with ``calendar_unavailable`` (real risk gate, fail-closed) but must
    NOT retire it — the row stays ``queued``, no alert, no ``obsolete``. When
    the calendar recovers within the session, the next drain pass proceeds to
    normal evaluation and the submit goes through the chokepoint to the broker."""
    runner, broker, _provider, event_store = _build_open_runner_with_history(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    # Locked-in bar defaults to the provider's LATEST bar (identity-fresh), so
    # once the calendar resolves the staleness gate passes — the ONLY veto in
    # play is the calendar outage.
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    results = _record_submit_results(runner)
    # Calendar outage: the broker cannot resolve the latest completed session.
    real_latest_session = broker.latest_completed_session
    broker.latest_completed_session = lambda now: None

    runner.run_cycle()

    # Vetoed through the REAL risk gate with the transient-specific code — the
    # permanent umbrella is absent, so the retire branch must not fire.
    assert len(results) == 1
    assert results[0].status == ExecutionStatus.BLOCKED
    assert "calendar_unavailable" in results[0].risk_decision.reason_codes
    assert "stale_market_data" not in results[0].risk_decision.reason_codes
    # Pre-#381 behavior preserved: still queued, no alert, no obsolete, and the
    # EXIT is not dedup-suppressed (its veto can clear mid-session).
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []
    assert intent_id not in runner._drain_vetoed_row_ids
    assert broker.submit_calls == []

    # Calendar recovers within the session: the next poll re-attempts and the
    # intent proceeds through normal evaluation to a real broker submit.
    broker.latest_completed_session = real_latest_session

    runner.run_cycle()

    assert len(results) == 2
    assert results[1].status == ExecutionStatus.SUBMITTED
    assert len(broker.submit_calls) == 1
    row = event_store.get_queued_intent(intent_id)
    assert row.status == "consumed"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []


def test_drain_entry_calendar_unavailable_keeps_dedup_row_stays_queued(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Transient sub-cause (c), ENTRY side: a calendar-outage veto (real risk
    gate) keeps the pre-#381 ENTRY behavior — the row stays ``queued`` (NOT
    terminally ``dropped``), no drop-audit row, and the in-memory veto dedup
    suppresses re-submits for the rest of the session (retried next session or
    after a restart)."""
    runner, broker, _provider, event_store = _build_open_runner_with_history(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side=OrderSide.BUY)
    _force_decision(runner, [_intent("SPY", OrderSide.BUY, quantity=1.0)])
    results = _record_submit_results(runner)
    broker.latest_completed_session = lambda now: None

    before = _drop_audit_count(event_store, runner)
    runner.run_cycle()

    assert len(results) == 1
    assert results[0].status == ExecutionStatus.BLOCKED
    assert "calendar_unavailable" in results[0].risk_decision.reason_codes
    assert "stale_market_data" not in results[0].risk_decision.reason_codes
    # NOT retired: no terminal drop, no audit drop row — just the in-memory dedup.
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert _drop_audit_count(event_store, runner) == before
    assert intent_id in runner._drain_vetoed_row_ids
    assert broker.submit_calls == []

    # Second pass this session: dedup suppresses the re-submit; row untouched.
    runner.run_cycle()
    assert len(results) == 1
    assert event_store.get_queued_intent(intent_id).status == "queued"


def test_drain_exit_cap_veto_still_retries_every_poll(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A NON-staleness EXIT veto (cap) keeps the pre-existing behavior: the row
    stays 'queued' and re-submits on every drain pass (its veto can clear
    mid-session), with no alert and no retirement."""
    runner, _broker, _provider, event_store = _build_open_runner_with_history(
        tmp_path, strategy_config_dir, risk_defaults_file
    )
    intent_id = _seed_queued_entry(
        event_store, runner, symbol="SPY", side=OrderSide.SELL, intent_class="exit"
    )
    runner._current_positions = lambda: {"SPY": 5.0}
    _force_decision(runner, [_intent("SPY", OrderSide.SELL, quantity=5.0)])
    calls = _blocking_submit_with_codes(runner, ["max_total_exposure_exceeded"])

    runner._drain_queued_intents()

    assert calls["n"] == 1
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []

    runner._drain_queued_intents()

    assert calls["n"] == 2
    assert event_store.get_queued_intent(intent_id).status == "queued"
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []

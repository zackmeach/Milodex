"""Tests for ``milodex.risk.attribution`` (ADR 0029).

Pin the reconstruction-from-trades semantics:
- only ``status="submitted"`` rows count as fills (Decision 2)
- ``strategy_name`` is the primary attribution key (Decision 3)
- absence of a recoverable opening fill resolves to ``"operator"``
- partial liquidations preserve attribution; full liquidations break it
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.risk.attribution import (
    OPERATOR_ATTRIBUTION,
    attribute_position,
    count_positions_by_strategy,
    strategy_open_lots,
    strategy_position_quantity,
    strategy_positions,
)

_NOW = datetime(2026, 5, 6, 18, 0, tzinfo=UTC)


def _explanation(store: EventStore, *, recorded_at: datetime, status: str = "submitted") -> int:
    """Insert a minimal explanation row and return its id.

    Carries a synthetic ``session_id`` so the dual-ancestor enforcement in
    :meth:`EventStore.append_explanation` accepts the row. These tests
    don't depend on the ancestor's existence in ``strategy_runs`` — they
    exercise position attribution from trades, not session lifecycle — so
    a stable string suffices.
    """
    return store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type="submit",
            status=status,
            strategy_name=None,
            strategy_stage=None,
            strategy_config_path=None,
            config_hash=None,
            symbol="X",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=recorded_at,
            latest_bar_close=100.0,
            account_equity=10_000.0,
            account_cash=10_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=status == "submitted",
            risk_summary="Allowed" if status == "submitted" else "Blocked",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id="test-attribution-session",
        )
    )


_ORDER_IDS = count(1)

# Sentinel: "caller didn't choose" — the helper then mints a unique order id
# per submitted trade, matching production (broker order ids are unique per
# order; the strategy fold's reversal is keyed on that identity).
_AUTO_ORDER_ID = "<auto>"


def _record_trade(
    store: EventStore,
    *,
    symbol: str,
    side: str,
    quantity: float,
    strategy_name: str | None,
    status: str = "submitted",
    submitted_by: str = "strategy_runner",
    recorded_at: datetime | None = None,
    broker_order_id: str | None = _AUTO_ORDER_ID,
    source: str = "paper",
) -> int:
    """Insert a paired explanation+trade row and return the trade id."""
    when = recorded_at if recorded_at is not None else _NOW
    if broker_order_id == _AUTO_ORDER_ID:
        broker_order_id = f"broker-{next(_ORDER_IDS)}" if status == "submitted" else None
    explanation_id = _explanation(store, recorded_at=when, status=status)
    return store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=when,
            status=status,
            source=source,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=quantity * 100.0,
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by=submitted_by,
            broker_order_id=broker_order_id,
            broker_status=None,
            message=None,
        )
    )


def _record_corrective_row(
    store: EventStore,
    *,
    symbol: str,
    side: str,
    quantity: float,
    status: str,
    broker_order_id: str,
    recorded_at: datetime | None = None,
) -> int:
    """Append a corrective terminal row shaped like ``sync_local_only_orders``.

    Mirrors operations/reconciliation.py: ``strategy_name=None``,
    ``submitted_by="reconcile"``, ``estimated_unit_price=0.0``,
    ``source="paper"`` — the reversal must find these by broker_order_id,
    never by strategy_name.
    """
    when = recorded_at if recorded_at is not None else _NOW
    explanation_id = _explanation(store, recorded_at=when, status=status)
    return store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=when,
            status=status,
            source="paper",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=0.0,
            estimated_order_value=0.0,
            strategy_name=None,
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="reconcile",
            broker_order_id=broker_order_id,
            broker_status=status,
            message="order-status sync: test",
        )
    )


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "milodex.db")


# ---------------------------------------------------------------------------
# attribute_position
# ---------------------------------------------------------------------------


def test_attribute_position_returns_strategy_for_single_submitted_buy(store):
    """Single submitted opening BUY attributes the symbol to its strategy."""
    _record_trade(store, symbol="SPY", side="buy", quantity=10, strategy_name="regime")

    assert attribute_position(symbol="SPY", event_store=store) == "regime"


def test_attribute_position_full_liquidation_then_rebuy_creates_new_attribution(store):
    """Held -> liquidated -> bought-by-different-strategy returns the latest owner.

    Pins Decision 1's "full liquidation followed by a fresh opening BUY
    creates a new attribution." Strategy A's chain is broken when the
    running balance hits zero; strategy B's later BUY starts fresh.
    """
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=3),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="sell",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=5,
        strategy_name="strategy_b",
        recorded_at=_NOW - timedelta(days=1),
    )

    assert attribute_position(symbol="SPY", event_store=store) == "strategy_b"


def test_attribute_position_returns_operator_when_opening_strategy_name_is_null(store):
    """A submitted opening row with strategy_name=None resolves to 'operator'.

    Pins Decision 3: ``strategy_name IS NULL`` co-occurs with operator-
    submitted trades (the execution service sets it via
    ``strategy_config.name if strategy_config else None``).
    """
    _record_trade(
        store,
        symbol="GLD",
        side="buy",
        quantity=2,
        strategy_name=None,
        submitted_by="operator",
    )

    assert attribute_position(symbol="GLD", event_store=store) == OPERATOR_ATTRIBUTION


def test_attribute_position_returns_operator_when_no_submitted_rows(store):
    """A symbol with no submitted fills at all resolves to 'operator'."""
    # Empty store -> operator.
    assert attribute_position(symbol="MSFT", event_store=store) == OPERATOR_ATTRIBUTION


def test_attribute_position_returns_operator_when_only_blocked_row_exists(store):
    """A blocked BUY is NOT a fill — its strategy must not be attributed.

    Pins Decision 2's filter: ``status="blocked"`` is excluded from the
    walk even when it is the most recent row for the symbol. A naive
    implementation that read the latest row regardless of status would
    misattribute the symbol to the strategy that proposed the rejected
    trade.
    """
    _record_trade(
        store,
        symbol="AAPL",
        side="buy",
        quantity=1,
        strategy_name="malicious_attribution_attempt",
        status="blocked",
        broker_order_id=None,
    )

    assert attribute_position(symbol="AAPL", event_store=store) == OPERATOR_ATTRIBUTION


def test_attribute_position_ignores_blocked_row_more_recent_than_submitted(store):
    """A more-recent blocked row must not override the older submitted opener.

    Decision 2 must filter blocked even when the row is newer.
    """
    _record_trade(
        store,
        symbol="QQQ",
        side="buy",
        quantity=1,
        strategy_name="real_owner",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="QQQ",
        side="buy",
        quantity=1,
        strategy_name="rejected_proposer",
        status="blocked",
        broker_order_id=None,
        recorded_at=_NOW - timedelta(days=1),
    )

    assert attribute_position(symbol="QQQ", event_store=store) == "real_owner"


def test_attribute_position_ignores_cancelled_rows(store):
    """Decision 2 explicitly names cancelled rows as non-fills."""
    _record_trade(
        store,
        symbol="IWM",
        side="buy",
        quantity=1,
        strategy_name="real_owner",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="IWM",
        side="buy",
        quantity=1,
        strategy_name="cancelled_proposer",
        status="cancelled",
        broker_order_id=None,
        recorded_at=_NOW - timedelta(days=1),
    )

    assert attribute_position(symbol="IWM", event_store=store) == "real_owner"


def test_attribute_position_ignores_preview_rows(store):
    """Decision 2 explicitly names preview rows as non-fills."""
    _record_trade(
        store,
        symbol="DIA",
        side="buy",
        quantity=1,
        strategy_name="real_owner",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="DIA",
        side="buy",
        quantity=1,
        strategy_name="preview_proposer",
        status="preview",
        broker_order_id=None,
        recorded_at=_NOW - timedelta(days=1),
    )

    assert attribute_position(symbol="DIA", event_store=store) == "real_owner"


def test_attribute_position_partial_liquidation_preserves_attribution(store):
    """Partial liquidation must NOT reset attribution.

    Decision 1: "Subsequent increases preserve attribution. Partial
    liquidations leave attribution unchanged on the remaining shares."
    """
    _record_trade(
        store,
        symbol="VTI",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="VTI",
        side="sell",
        quantity=4,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=1),
    )

    assert attribute_position(symbol="VTI", event_store=store) == "strategy_a"


def test_attribute_position_normalizes_symbol_case(store):
    """Symbol comparison must be case-insensitive (matches request normalization)."""
    _record_trade(store, symbol="SPY", side="buy", quantity=1, strategy_name="regime")

    assert attribute_position(symbol="spy", event_store=store) == "regime"


# ---------------------------------------------------------------------------
# count_positions_by_strategy
# ---------------------------------------------------------------------------


def test_count_positions_by_strategy_aggregates_across_multiple_symbols(store):
    """Multiple symbols owned by multiple strategies aggregate correctly."""
    _record_trade(store, symbol="SPY", side="buy", quantity=1, strategy_name="regime")
    _record_trade(store, symbol="QQQ", side="buy", quantity=1, strategy_name="meanrev")
    _record_trade(store, symbol="IWM", side="buy", quantity=1, strategy_name="meanrev")
    _record_trade(store, symbol="DIA", side="buy", quantity=1, strategy_name="meanrev")

    counts = count_positions_by_strategy(
        positions={"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0, "DIA": 1.0},
        event_store=store,
    )

    assert counts == {"regime": 1, "meanrev": 3}


def test_count_positions_by_strategy_includes_operator_key(store):
    """Operator-attributed positions appear under the 'operator' key."""
    _record_trade(store, symbol="SPY", side="buy", quantity=1, strategy_name="regime")
    _record_trade(
        store,
        symbol="GLD",
        side="buy",
        quantity=1,
        strategy_name=None,
        submitted_by="operator",
    )

    counts = count_positions_by_strategy(
        positions={"SPY": 1.0, "GLD": 1.0},
        event_store=store,
    )

    assert counts == {"regime": 1, OPERATOR_ATTRIBUTION: 1}


def test_count_positions_by_strategy_skips_zero_quantity(store):
    """Symbols with zero or negative quantity are not counted."""
    _record_trade(store, symbol="SPY", side="buy", quantity=1, strategy_name="regime")

    counts = count_positions_by_strategy(
        positions={"SPY": 1.0, "QQQ": 0.0},
        event_store=store,
    )

    assert "QQQ" not in counts
    assert counts.get("regime") == 1


def test_attribute_position_over_sell_does_not_drive_running_balance_negative(store):
    """An over-sell (SELL exceeding prior BUYs) must not under-count the owner.

    Regression for the negative-balance reopen bug. A broker position
    cannot be net-short in this system, so a trade-history SELL that
    exceeds prior submitted BUYs (data error / partial-fill mismatch /
    out-of-system manual sell) is a data artifact, not a real short.

    Sequence: strategy_a opens 10, an oversized SELL of 14 lands, then
    strategy_a buys 3 more (the broker still reports the symbol held).
    Without clamping the running balance at zero on the sell side, the
    phantom -4 "debt" must be repaid before a zero->non-zero opening can
    re-fire, so strategy_a's re-buy never re-opens the chain and the
    symbol resolves to OPERATOR — silently dropping the position from
    strategy_a's ADR-0029 per-strategy cap (a fail-open under-count).
    Clamping treats the over-sell as a full liquidation; strategy_a's
    later BUY then legitimately re-opens, attributing the held symbol
    back to strategy_a.
    """
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=3),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="sell",
        quantity=14,
        strategy_name=None,
        submitted_by="operator",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=3,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=1),
    )

    assert attribute_position(symbol="SPY", event_store=store) == "strategy_a"


# ---------------------------------------------------------------------------
# strategy_positions / strategy_open_lots (ADR 0055)
# ---------------------------------------------------------------------------


def test_strategy_positions_folds_buys_minus_sells_and_clamps_at_zero(store):
    """Per-strategy ledger: buys minus sells, clamped at zero."""
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=3),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="sell",
        quantity=4,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=2),
    )

    assert strategy_positions("strategy_a", store) == {"SPY": 6.0}


def test_strategy_positions_excludes_other_strategies_and_non_submitted(store):
    """Only submitted rows for the requested strategy_id count."""
    _record_trade(store, symbol="SPY", side="buy", quantity=10, strategy_name="strategy_a")
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=99,
        strategy_name="strategy_b",
    )
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=50,
        strategy_name="strategy_a",
        status="blocked",
        broker_order_id=None,
    )

    assert strategy_positions("strategy_a", store) == {"SPY": 10.0}
    assert strategy_position_quantity("strategy_a", "SPY", store) == 10.0
    assert strategy_position_quantity("strategy_a", "QQQ", store) == 0.0


def test_strategy_positions_over_sell_clamps_to_zero(store):
    """Oversized sell clamps running balance at zero (same as ADR 0029 walk)."""
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="sell",
        quantity=14,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=1),
    )

    assert strategy_positions("strategy_a", store) == {}


def test_strategy_open_lots_weighted_avg_and_opened_at(store):
    """Open lot tracks weighted-average buy price and zero->nonzero opened_at."""
    t0 = _NOW - timedelta(days=5)
    t1 = _NOW - timedelta(days=3)
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=t0,
    )
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=5,
        strategy_name="strategy_a",
        recorded_at=t1,
    )

    lots = strategy_open_lots("strategy_a", store)
    assert lots["SPY"]["quantity"] == 15.0
    assert lots["SPY"]["opened_at"] == t0
    expected_avg = (10 * 100.0 + 5 * 100.0) / 15.0
    assert lots["SPY"]["avg_entry_price"] == pytest.approx(expected_avg)


def test_strategy_open_lots_resets_after_full_liquidation_and_reentry(store):
    """Full liquidation clears the lot; a later buy starts a fresh opened_at/avg."""
    t_open = _NOW - timedelta(days=10)
    t_close = _NOW - timedelta(days=8)
    t_reopen = _NOW - timedelta(days=2)
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=t_open,
    )
    _record_trade(
        store,
        symbol="SPY",
        side="sell",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=t_close,
    )
    store.append_trade(
        TradeEvent(
            explanation_id=_explanation(store, recorded_at=t_reopen),
            recorded_at=t_reopen,
            status="submitted",
            source="paper",
            symbol="SPY",
            side="buy",
            quantity=5,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=200.0,
            estimated_order_value=1000.0,
            strategy_name="strategy_a",
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="strategy_runner",
            broker_order_id="broker-rebuy",
            broker_status=None,
            message=None,
        )
    )

    lots = strategy_open_lots("strategy_a", store)
    assert lots["SPY"]["quantity"] == 5.0
    assert lots["SPY"]["opened_at"] == t_reopen
    assert lots["SPY"]["avg_entry_price"] == 200.0


# ---------------------------------------------------------------------------
# Paper-source scoping + corrective-row reversal (R-P0-1 / HR-1)
# ---------------------------------------------------------------------------


def test_backtest_fills_do_not_contaminate_strategy_ledger(store):
    """A backtest fill must not appear in any strategy-ledger surface.

    R-P0-1 regression: the backtest engine writes ``trades`` rows with
    ``source='backtest'``, ``status='submitted'``, and the real
    ``strategy_name`` through the same ExecutionService into the same DB.
    Without paper scoping, the runner's position view folds them as live
    holdings and emits phantom exits every close.
    """
    _record_trade(
        store,
        symbol="NVDA",
        side="buy",
        quantity=1341,
        strategy_name="strategy_a",
        source="backtest",
    )

    assert strategy_positions("strategy_a", store) == {}
    assert strategy_open_lots("strategy_a", store) == {}
    assert attribute_position(symbol="NVDA", event_store=store) == OPERATOR_ATTRIBUTION


def test_backtest_fills_do_not_shadow_paper_ledger(store):
    """Interleaved backtest rows leave the paper fold untouched.

    The backtest SELL here is larger than the paper position; if it leaked
    into the fold it would clamp the paper lot to zero (a phantom exit's
    mirror image).
    """
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=500,
        strategy_name="strategy_a",
        source="backtest",
        recorded_at=_NOW - timedelta(days=1),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="sell",
        quantity=505,
        strategy_name="strategy_a",
        source="backtest",
        recorded_at=_NOW,
    )

    assert strategy_positions("strategy_a", store) == {"SPY": 10.0}
    assert attribute_position(symbol="SPY", event_store=store) == "strategy_a"


def test_corrective_cancelled_row_closes_strategy_ledger_lot(store):
    """submitted→cancelled reversal: a sync corrective row closes the lot.

    The corrective row carries ``strategy_name=None`` (see
    ``sync_local_only_orders``), so the reversal must locate it by
    ``broker_order_id`` — a strategy-name-filtered fetch alone never sees it.
    """
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        broker_order_id="order-cancel",
        recorded_at=_NOW - timedelta(days=1),
    )
    _record_corrective_row(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        status="cancelled",
        broker_order_id="order-cancel",
    )

    assert strategy_positions("strategy_a", store) == {}
    assert strategy_open_lots("strategy_a", store) == {}


def test_corrective_cancelled_then_filled_pair_counts_the_order(store):
    """Multiple corrective rows for one order: the LAST (by id) wins.

    The broker-purge-then-backstop path can write cancelled first and a
    later filled correction; latest-status-per-order must follow the
    final word, mirroring ``fold_positions``'s dict-overwrite semantics.
    """
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        broker_order_id="order-flip",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_corrective_row(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        status="cancelled",
        broker_order_id="order-flip",
        recorded_at=_NOW - timedelta(days=1),
    )
    _record_corrective_row(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        status="filled",
        broker_order_id="order-flip",
    )

    assert strategy_positions("strategy_a", store) == {"SPY": 10.0}


def test_corrective_canceled_single_l_spelling_also_reverses(store):
    """The one-'l' 'canceled' spelling (raw broker string; 64 live rows)
    is terminal-non-affecting under the allowlist design and must reverse
    the lot exactly like 'cancelled'."""
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        broker_order_id="order-one-l",
        recorded_at=_NOW - timedelta(days=1),
    )
    _record_corrective_row(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        status="canceled",
        broker_order_id="order-one-l",
    )

    assert strategy_positions("strategy_a", store) == {}
    assert strategy_open_lots("strategy_a", store) == {}


def test_attribute_position_backtest_source_sees_backtest_universe_only(store):
    """The backtest structural evaluator attributes within source='backtest'.

    Regression for the HR-1 consumer break: `BacktestStructuralRiskEvaluator`
    runs `_check_strategy_concurrent_positions` over positions opened by
    source='backtest' fills. Paper-only scoping made those invisible
    (owner -> 'operator'), silently disabling the per-strategy cap in
    ENFORCE-policy backtests. The two universes must be mutually invisible.

    The backtest BUY is recorded FIRST (lower id) so the paper-direction
    assertion discriminates: an unscoped walk would see the backtest row
    open the chain and return 'backtest_owner'.
    """
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=5,
        strategy_name="backtest_owner",
        source="backtest",
        recorded_at=_NOW - timedelta(days=2),
    )
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="paper_owner",
        recorded_at=_NOW - timedelta(days=1),
    )

    assert attribute_position(symbol="SPY", event_store=store) == "paper_owner"
    assert (
        attribute_position(symbol="SPY", event_store=store, source="backtest") == "backtest_owner"
    )


def test_corrective_filled_row_counts_the_order_once(store):
    """submitted→filled must count once, with the original row's lot fields.

    The corrective row carries ``estimated_unit_price=0.0``; the fold must
    keep the original submission's price/timestamp, using the corrective
    status only to decide whether the order counts.
    """
    t_open = _NOW - timedelta(days=1)
    _record_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        strategy_name="strategy_a",
        broker_order_id="order-fill",
        recorded_at=t_open,
    )
    _record_corrective_row(
        store,
        symbol="SPY",
        side="buy",
        quantity=10,
        status="filled",
        broker_order_id="order-fill",
    )

    assert strategy_positions("strategy_a", store) == {"SPY": 10.0}
    lots = strategy_open_lots("strategy_a", store)
    assert lots["SPY"]["quantity"] == 10.0
    assert lots["SPY"]["avg_entry_price"] == 100.0
    assert lots["SPY"]["opened_at"] == t_open

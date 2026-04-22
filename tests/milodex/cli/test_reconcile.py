"""CLI integration tests for ``milodex reconcile``.

Each test injects an ``event_store_factory`` backed by a tmp SQLite DB and a
stub broker so nothing touches real resources. Covers clean runs, every drift
classification, incident recording, idempotency (R-OPS-010), advisory-lock
concurrency (R-OPS-008), degraded broker handling, and the JSON contract
(R-CLI-017).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from milodex.broker.exceptions import BrokerAuthError
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.cli.formatter import JSON_SCHEMA_VERSION
from milodex.cli.main import main as cli_entrypoint
from milodex.core.advisory_lock import AdvisoryLock
from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubBroker:
    def __init__(
        self,
        *,
        account: AccountInfo | None = None,
        market_open: bool = True,
        positions: list[Position] | None = None,
        orders: list[Order] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._account = account or AccountInfo(
            equity=1000.0, cash=500.0, buying_power=500.0, portfolio_value=1000.0, daily_pnl=0.0
        )
        self._market_open = market_open
        self._positions = positions or []
        self._orders = orders or []
        self._error = error

    def get_account(self) -> AccountInfo:
        if self._error:
            raise self._error
        return self._account

    def is_market_open(self) -> bool:
        if self._error:
            raise self._error
        return self._market_open

    def get_positions(self) -> list[Position]:
        if self._error:
            raise self._error
        return list(self._positions)

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        if self._error:
            raise self._error
        return list(self._orders)


def _refuse_data_provider():
    raise AssertionError("data provider should not be needed by reconcile")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _run(
    argv: list[str],
    tmp_path: Path,
    *,
    broker: _StubBroker | None = None,
) -> tuple[int, StringIO, StringIO]:
    out = StringIO()
    err = StringIO()
    broker = broker if broker is not None else _StubBroker()
    exit_code = cli_entrypoint(
        argv,
        event_store_factory=lambda: EventStore(tmp_path / "milodex.db"),
        broker_factory=lambda: broker,
        data_provider_factory=_refuse_data_provider,
        locks_dir=tmp_path / "locks",
        stdout=out,
        stderr=err,
    )
    return exit_code, out, err


def _append_local_trade(
    event_store: EventStore,
    *,
    symbol: str,
    side: str,
    quantity: float,
    status: str = "submitted",
    broker_order_id: str | None = None,
    when: datetime | None = None,
) -> None:
    when = when or datetime.now(tz=UTC)
    exp_id = event_store.append_explanation(
        ExplanationEvent(
            recorded_at=when,
            decision_type="submit",
            status=status,
            strategy_name="test_strategy",
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash="fp-test",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            submitted_by="test",
            market_open=True,
            latest_bar_timestamp=when,
            latest_bar_close=100.0,
            account_equity=1000.0,
            account_cash=500.0,
            account_portfolio_value=1000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="ok",
            reason_codes=[],
            risk_checks=[],
            context={},
        )
    )
    event_store.append_trade(
        TradeEvent(
            explanation_id=exp_id,
            recorded_at=when,
            status=status,
            source="paper",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=100.0 * quantity,
            strategy_name="test_strategy",
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="test",
            broker_order_id=broker_order_id,
            broker_status=None,
            message=None,
        )
    )


def _broker_position(symbol: str, quantity: float) -> Position:
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=100.0,
        current_price=100.0,
        market_value=quantity * 100.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def _broker_order(
    order_id: str,
    *,
    symbol: str = "SPY",
    status: OrderStatus = OrderStatus.PENDING,
    side: OrderSide = OrderSide.BUY,
    quantity: float = 1.0,
) -> Order:
    return Order(
        id=order_id,
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        time_in_force=TimeInForce.DAY,
        status=status,
        submitted_at=datetime.now(tz=UTC),
    )


def _incident_count(tmp_path: Path) -> int:
    store = EventStore(tmp_path / "milodex.db")
    return sum(1 for e in store.list_explanations() if e.decision_type == "reconcile_incident")


# ---------------------------------------------------------------------------
# Clean state
# ---------------------------------------------------------------------------


def test_reconcile_clean_when_broker_and_local_agree(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _append_local_trade(store, symbol="SPY", side="buy", quantity=10.0)
    broker = _StubBroker(positions=[_broker_position("SPY", 10.0)])

    exit_code, out, err = _run(["reconcile"], tmp_path, broker=broker)
    assert exit_code == 0
    assert err.getvalue() == ""
    assert "Result: CLEAN" in out.getvalue()
    assert _incident_count(tmp_path) == 0


def test_reconcile_empty_state_is_clean(tmp_path: Path) -> None:
    exit_code, out, _ = _run(["reconcile"], tmp_path)
    assert exit_code == 0
    assert "Result: CLEAN" in out.getvalue()


# ---------------------------------------------------------------------------
# Position drift
# ---------------------------------------------------------------------------


def test_reconcile_qty_mismatch_logs_incident(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _append_local_trade(store, symbol="SPY", side="buy", quantity=10.0)
    broker = _StubBroker(positions=[_broker_position("SPY", 9.0)])

    exit_code, out, _ = _run(["reconcile", "--json"], tmp_path, broker=broker)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    data = payload["data"]
    assert data["reconciliation_clean"] is False
    assert data["incident_recorded"] is True
    assert "position_qty_mismatch" in data["incident_reason_codes"]
    mismatches = data["positions"]["mismatches"]
    assert len(mismatches) == 1
    assert mismatches[0]["symbol"] == "SPY"
    assert mismatches[0]["local_qty"] == 10.0
    assert mismatches[0]["broker_qty"] == 9.0
    assert _incident_count(tmp_path) == 1


def test_reconcile_broker_only_position_logs_incident(tmp_path: Path) -> None:
    broker = _StubBroker(positions=[_broker_position("QQQ", 3.0)])
    exit_code, out, _ = _run(["reconcile", "--json"], tmp_path, broker=broker)
    assert exit_code == 0
    data = json.loads(out.getvalue())["data"]
    assert "position_broker_only" in data["incident_reason_codes"]
    assert data["incident_recorded"] is True


def test_reconcile_local_only_position_logs_incident(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _append_local_trade(store, symbol="AAPL", side="buy", quantity=5.0)
    exit_code, out, _ = _run(["reconcile", "--json"], tmp_path, broker=_StubBroker())
    assert exit_code == 0
    data = json.loads(out.getvalue())["data"]
    assert "position_local_only" in data["incident_reason_codes"]
    assert data["incident_recorded"] is True


# ---------------------------------------------------------------------------
# Order drift
# ---------------------------------------------------------------------------


def test_reconcile_broker_only_open_order_logs_incident(tmp_path: Path) -> None:
    broker = _StubBroker(orders=[_broker_order("ord-xyz", symbol="QQQ")])
    exit_code, out, _ = _run(["reconcile", "--json"], tmp_path, broker=broker)
    assert exit_code == 0
    data = json.loads(out.getvalue())["data"]
    assert "order_broker_only" in data["incident_reason_codes"]
    assert any(
        m["broker_order_id"] == "ord-xyz" and m["incident"] for m in data["orders"]["mismatches"]
    )


def test_reconcile_stale_local_only_order_is_warning_not_incident(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    long_ago = datetime.now(tz=UTC) - timedelta(days=3)
    # Pair the local trade with a matching broker position so the position
    # dimension is clean — this test isolates the order classification.
    _append_local_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=1.0,
        status="submitted",
        broker_order_id="ord-old",
        when=long_ago,
    )
    broker = _StubBroker(positions=[_broker_position("SPY", 1.0)])
    exit_code, out, _ = _run(["reconcile", "--json"], tmp_path, broker=broker)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    data = payload["data"]
    assert data["reconciliation_clean"] is True
    assert data["incident_recorded"] is False
    assert any("Stale local-only order" in w for w in payload["warnings"])
    stale_rows = [m for m in data["orders"]["mismatches"] if m["broker_order_id"] == "ord-old"]
    assert stale_rows and stale_rows[0]["incident"] is False


def test_reconcile_recent_local_only_order_is_incident(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _append_local_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=1.0,
        status="submitted",
        broker_order_id="ord-fresh",
        when=datetime.now(tz=UTC) - timedelta(minutes=5),
    )
    exit_code, out, _ = _run(["reconcile", "--json"], tmp_path, broker=_StubBroker())
    assert exit_code == 0
    data = json.loads(out.getvalue())["data"]
    assert "order_local_only_recent" in data["incident_reason_codes"]
    assert data["incident_recorded"] is True


def test_reconcile_matched_order_is_ok(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _append_local_trade(
        store,
        symbol="SPY",
        side="buy",
        quantity=1.0,
        status="submitted",
        broker_order_id="ord-match",
    )
    broker = _StubBroker(orders=[_broker_order("ord-match")])
    exit_code, out, _ = _run(["reconcile", "--json"], tmp_path, broker=broker)
    assert exit_code == 0
    data = json.loads(out.getvalue())["data"]
    # Note: this trade also creates a local BUY 1 SPY position; broker has no
    # SPY position, so we expect a position_local_only incident. The *order*
    # alone matches.
    assert any(o["kind"] == "ok" for o in data["orders"]["ok"])


# ---------------------------------------------------------------------------
# Idempotency (R-OPS-010)
# ---------------------------------------------------------------------------


def test_reconcile_is_idempotent_on_repeat(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    _append_local_trade(store, symbol="SPY", side="buy", quantity=10.0)
    broker = _StubBroker(positions=[_broker_position("SPY", 9.0)])

    first, _, _ = _run(["reconcile", "--json"], tmp_path, broker=broker)
    second_code, second_out, _ = _run(["reconcile", "--json"], tmp_path, broker=broker)
    assert first == 0
    assert second_code == 0
    second_payload = json.loads(second_out.getvalue())
    assert second_payload["data"]["incident_recorded"] is False
    assert second_payload["data"]["incident_deduplicated"] is True
    # R-OPS-010: identical durable state on second run — exactly one incident.
    assert _incident_count(tmp_path) == 1


# ---------------------------------------------------------------------------
# Degraded broker
# ---------------------------------------------------------------------------


def test_reconcile_broker_unreachable_degrades_gracefully(tmp_path: Path) -> None:
    broker = _StubBroker(error=BrokerAuthError("no credentials"))
    exit_code, out, err = _run(["reconcile", "--json"], tmp_path, broker=broker)
    assert exit_code == 0
    assert err.getvalue() == ""
    payload = json.loads(out.getvalue())
    assert payload["data"]["broker"]["connected"] is False
    assert "no credentials" in payload["data"]["broker"]["error"]
    assert any("Broker unreachable" in w for w in payload["warnings"])
    # No incident when we cannot prove drift.
    assert _incident_count(tmp_path) == 0


# ---------------------------------------------------------------------------
# Concurrency (R-OPS-008)
# ---------------------------------------------------------------------------


def test_reconcile_refuses_when_advisory_lock_is_held(tmp_path: Path) -> None:
    locks_dir = tmp_path / "locks"
    holder = AdvisoryLock("milodex.runtime", locks_dir=locks_dir, holder_name="other_process")
    holder.acquire()
    try:
        exit_code, _, err = _run(["reconcile", "--json"], tmp_path)
        assert exit_code == 1
        payload = json.loads(err.getvalue())
        assert payload["status"] == "error"
        assert payload["errors"][0]["code"] == "advisory_lock_held"
    finally:
        holder.release()


# ---------------------------------------------------------------------------
# JSON contract & help (R-CLI-015, R-CLI-017)
# ---------------------------------------------------------------------------


def test_reconcile_json_schema_contract(tmp_path: Path) -> None:
    exit_code, out, _ = _run(["reconcile", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())

    assert payload["schema_version"] == JSON_SCHEMA_VERSION
    assert payload["command"] == "reconcile"
    assert payload["status"] == "success"
    assert payload["timestamp"]
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["errors"], list)
    assert isinstance(payload["summary"], list)

    data = payload["data"]
    assert set(data) >= {
        "as_of",
        "broker",
        "positions",
        "orders",
        "deferred_checks",
        "reconciliation_clean",
        "incident_recorded",
        "incident_deduplicated",
        "incident_hash",
        "incident_reason_codes",
    }
    assert set(data["positions"]) == {"ok", "mismatches"}
    assert set(data["orders"]) == {"ok", "mismatches"}


def test_reconcile_appears_in_top_level_help(tmp_path: Path, capsys) -> None:
    import pytest as _pytest

    with _pytest.raises(SystemExit) as excinfo:
        _run(["--help"], tmp_path)
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "reconcile" in (captured.out + captured.err)


def test_reconcile_invalid_as_of_returns_structured_error(tmp_path: Path) -> None:
    exit_code, _, err = _run(["reconcile", "--as-of", "not-a-date", "--json"], tmp_path)
    assert exit_code == 1
    payload = json.loads(err.getvalue())
    assert payload["status"] == "error"
    assert "Invalid --as-of" in payload["errors"][0]["message"]

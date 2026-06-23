"""Tests for the conservative drain-time tradable DROP helper (Phase 4).

Driven through the REAL ``SimulatedBroker`` (no mocks) so the fail-closed catch
is proven against an actual broker read, not a stub. Every uncertain branch
(False / None / raises) MUST DROP, and a broker read that raises MUST NOT
propagate out of ``tradable_drop_decision``.
"""

from datetime import UTC, datetime

from milodex.broker.simulated import SimulatedBroker
from milodex.runner.drain_policy import TradableDecision, tradable_drop_decision


def _broker():
    b = SimulatedBroker(slippage_pct=0.0, commission_per_trade=0.0)
    b.set_simulation_day(datetime(2025, 1, 2, tzinfo=UTC), {"AAPL": 190.0})
    return b


def test_tradable_proceeds():
    d = tradable_drop_decision(_broker(), "AAPL")
    assert isinstance(d, TradableDecision)
    assert d.drop is False
    assert d.reason is None


def test_not_tradable_drops():
    b = _broker()
    b.set_tradable_override("AAPL", False)
    d = tradable_drop_decision(b, "AAPL")
    assert d.drop is True
    assert d.reason == "not_tradable"


def test_unknown_status_drops():
    # MSFT has no close on the sim day -> read returns None -> DROP
    d = tradable_drop_decision(_broker(), "MSFT")
    assert d.drop is True
    assert d.reason == "tradability_unknown"


def test_read_raises_drops_and_does_not_propagate():
    b = _broker()
    b.set_tradable_override("AAPL", RuntimeError("alpaca down"))
    d = tradable_drop_decision(b, "AAPL")  # MUST NOT raise
    assert d.drop is True
    assert d.reason == "tradability_read_error"
    assert "alpaca down" in (d.detail or "")

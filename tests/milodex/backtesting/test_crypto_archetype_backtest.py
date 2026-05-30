"""End-to-end harness proof for the crypto-spot archetype.

Runs each BTC/USD canary through the REAL backtest path — real config via
StrategyLoader, real BacktestEngine + simulation kernel + event store — with a
deterministic in-memory fixture injected via SimulatedDataProvider (no network,
no cache, no mocks of the strategy). Proves: 24/7 intraday bars replay, the
strategy emits intents, fills flow through the kernel, FRACTIONAL position
sizing survives end to end, the "/" symbol survives the event store, and a
round trip (buy + sell) actually executes.

This is an architecture/harness proof, not an alpha claim — the fixture is a
sinusoid chosen to exercise the rules, not real BTC data.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.data.simulated import SimulatedDataProvider
from milodex.strategies.loader import StrategyLoader

REPO = Path(__file__).resolve().parents[3]
CONFIGS = REPO / "configs"
SYMBOL = "BTC/USD"

# Short run window (6 days) with a 7-day warmup lead-in — enough to seed the
# slow EMA (48 bars) and run a couple of sinusoid cycles, while keeping the
# end-to-end engine replay fast (a few seconds, not minutes).
RUN_START = date(2024, 2, 15)
RUN_END = date(2024, 2, 21)
FIXTURE_START = pd.Timestamp("2024-02-08 00:00:00", tz="UTC")
FIXTURE_END = pd.Timestamp("2024-02-22 00:00:00", tz="UTC")


@pytest.mark.parametrize(
    ("config_name", "minutes_per_bar"),
    [
        ("momentum_crypto_ema_cross_btc_usd_1h_v1.yaml", 60),
        ("meanrev_crypto_rsi2_btc_usd_30m_v1.yaml", 30),
    ],
)
def test_crypto_canary_backtests_through_real_engine(
    tmp_path: Path, config_name: str, minutes_per_bar: int
) -> None:
    loaded = StrategyLoader().load(CONFIGS / config_name)
    fixture = _sinusoid_bars(minutes_per_bar)
    provider = SimulatedDataProvider({SYMBOL: fixture})
    store = EventStore(tmp_path / "crypto_backtest.db")

    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=100_000.0,
    )
    result = engine.run(RUN_START, RUN_END)

    # A real round trip executed: at least one buy AND one sell filled.
    assert result.buy_count > 0, "expected at least one filled buy"
    assert result.sell_count > 0, "expected at least one filled sell"
    assert result.trade_count == result.buy_count + result.sell_count
    # Equity moved — the simulation actually marked positions to market.
    assert result.final_equity != pytest.approx(result.initial_equity)

    # Fractional sizing survived end to end, and the "/" symbol round-tripped
    # through the event store unmangled.
    trades = store.list_trades()
    assert trades, "expected trade rows written to the event store"
    assert all(t.symbol == SYMBOL for t in trades)
    buys = [t for t in trades if t.side == "buy"]
    assert buys, "expected at least one buy trade row"
    assert all(0 < t.quantity < 1 for t in buys), (
        "BTC position sizing must be fractional, never floored to whole units"
    )


def test_momentum_and_meanrev_both_persist_completed_runs(tmp_path: Path) -> None:
    """Both canaries complete (status='completed') and write a backtest_runs row."""
    for config_name, mpb in [
        ("momentum_crypto_ema_cross_btc_usd_1h_v1.yaml", 60),
        ("meanrev_crypto_rsi2_btc_usd_30m_v1.yaml", 30),
    ]:
        loaded = StrategyLoader().load(CONFIGS / config_name)
        store = EventStore(tmp_path / f"{config_name}.db")
        engine = BacktestEngine(
            loaded=loaded,
            data_provider=SimulatedDataProvider({SYMBOL: _sinusoid_bars(mpb)}),
            event_store=store,
            initial_equity=100_000.0,
        )
        result = engine.run(RUN_START, RUN_END)
        run = store.get_backtest_run(result.run_id)
        assert run is not None
        assert run.status == "completed"


# ---------------------------------------------------------------------------
# Deterministic fixture
# ---------------------------------------------------------------------------


def _sinusoid_bars(minutes_per_bar: int) -> BarSet:
    """Continuous 24/7 BTC/USD bars on a sinusoid (no randomness).

    A ~10-day sinusoid with 15% amplitude over a base of $50k. The smooth
    up/down swings guarantee fast/slow EMA crossovers (momentum entries/exits)
    and RSI(2) oversold→recovery cycles (mean-reversion entries/exits) within
    the run window, so both canaries produce real round trips.
    """
    base = 50_000.0
    amplitude = 0.15
    period_bars = int(round(3 * 1440 / minutes_per_bar))  # ~3-day cycle regardless of cadence

    step = pd.Timedelta(minutes=minutes_per_bar)
    n_bars = int((FIXTURE_END - FIXTURE_START) / step)

    records: list[dict[str, Any]] = []
    for i in range(n_bars):
        ts = FIXTURE_START + i * step
        close = base * (1.0 + amplitude * math.sin(2 * math.pi * i / period_bars))
        open_ = base * (1.0 + amplitude * math.sin(2 * math.pi * (i - 0.5) / period_bars))
        hi = max(open_, close) * 1.001
        lo = min(open_, close) * 0.999
        records.append(
            {
                "timestamp": ts,
                "open": round(open_, 2),
                "high": round(hi, 2),
                "low": round(lo, 2),
                "close": round(close, 2),
                "volume": 10.0,
                "vwap": round(close, 2),
            }
        )
    return BarSet(pd.DataFrame(records))

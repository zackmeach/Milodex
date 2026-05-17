"""Correctness (equivalence) tests for perf/backtest-hot-path optimisations.

Verifies bit-identical behaviour for:
- Fix #1  _slice_bars_to_day with ts_index cursor vs. legacy parse-each-call path
- Fix #2  BarSet._df_view does not expose internal state; public mutation-safety contract
- Fix #3  _trading_days_in_range vectorised result equals set-comprehension reference
- Full engine equivalence: multi-symbol multi-day run trades, equity curve, and key
  metrics are identical after the optimisations
"""

from __future__ import annotations

from datetime import UTC, date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from milodex.backtesting.engine import (
    BacktestEngine,
    _build_ts_date_index,
    _opens_on_day,
    _slice_bars_to_day,
    _trading_days_in_range,
)
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_REGIME_YAML = """\
strategy:
  id: "regime.daily.sma200_rotation.spy_shy_equiv_test.v1"
  family: "regime"
  template: "daily.sma200_rotation"
  variant: "spy_shy_equiv_test"
  version: 1
  description: "Equivalence-test regime strategy."
  enabled: true
  universe:
    - "SPY"
    - "SHY"
  parameters:
    ma_filter_length: 3
    risk_on_symbol: "SPY"
    risk_off_symbol: "SHY"
    allocation_pct: 1.0
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: null
  risk:
    max_position_pct: 1.0
    max_positions: 1
    daily_loss_cap_pct: 1.0
    stop_loss_pct: null
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: null
    walk_forward_windows: 1
  disable_conditions_additional: []
"""


def _barset(closes: list[float], start: str = "2025-01-01") -> BarSet:
    timestamps = pd.date_range(start, periods=len(closes), freq="D", tz=UTC)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * len(closes),
                "vwap": closes,
            }
        )
    )


class _StubProvider:
    def __init__(self, bars: dict[str, BarSet]) -> None:
        self._bars = bars

    def get_bars(self, symbols, timeframe, start, end):  # noqa: ARG002
        return {sym: self._bars[sym] for sym in symbols if sym in self._bars}

    def get_latest_bar(self, symbol):  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fix #2: BarSet._df_view — no-copy internal accessor
# ---------------------------------------------------------------------------


class TestBarSetDfView:
    def test_view_returns_same_object(self):
        """_df_view must return the internal DataFrame, not a copy."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-13"], utc=True),
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [500000],
                "vwap": [100.2],
            }
        )
        barset = BarSet(df)
        view = barset._df_view()  # noqa: SLF001
        assert view is barset._df_view()  # noqa: SLF001 — same object every time

    def test_to_dataframe_mutation_safety_still_holds(self):
        """Public mutation-safety contract is unchanged: mutating to_dataframe() result
        must not corrupt BarSet internal state."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2025-01-13", "2025-01-14"], utc=True),
                "open": [100.0, 102.0],
                "high": [101.0, 103.0],
                "low": [99.0, 101.0],
                "close": [100.5, 102.5],
                "volume": [500000, 600000],
                "vwap": [100.2, 102.2],
            }
        )
        barset = BarSet(df)
        public_copy = barset.to_dataframe()
        public_copy.iloc[0, public_copy.columns.get_loc("close")] = 999.0
        # Internal state must be unchanged.
        assert barset._df_view().iloc[0]["close"] == pytest.approx(100.5)  # noqa: SLF001
        assert barset.to_dataframe().iloc[0]["close"] == pytest.approx(100.5)

    def test_view_values_match_to_dataframe(self):
        """_df_view must expose the same data as to_dataframe."""
        closes = [10.0, 12.0, 14.0]
        barset = _barset(closes)
        view = barset._df_view()  # noqa: SLF001
        copy = barset.to_dataframe()
        pd.testing.assert_frame_equal(view.reset_index(drop=True), copy.reset_index(drop=True))


# ---------------------------------------------------------------------------
# Fix #1 + #3: _slice_bars_to_day ts_index path vs. legacy path
# ---------------------------------------------------------------------------


class TestSliceBarsEquivalence:
    """The ts_index (cursor) path must produce identical BarSets to the legacy path."""

    @pytest.fixture()
    def all_bars(self) -> dict[str, BarSet]:
        closes_spy = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
        closes_shy = [5.0, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6]
        return {
            "SPY": _barset(closes_spy, start="2025-02-03"),
            "SHY": _barset(closes_shy, start="2025-02-03"),
        }

    def test_slice_with_index_matches_legacy_all_days(self, all_bars):
        ts_index = _build_ts_date_index(all_bars)
        days = [date(2025, 2, 3) + timedelta(days=i) for i in range(7)]
        for day in days:
            legacy = _slice_bars_to_day(all_bars, day)
            indexed = _slice_bars_to_day(all_bars, day, ts_index)
            assert set(legacy.keys()) == set(indexed.keys()), f"key mismatch on {day}"
            for sym in legacy:
                pd.testing.assert_frame_equal(
                    legacy[sym].to_dataframe().reset_index(drop=True),
                    indexed[sym].to_dataframe().reset_index(drop=True),
                    check_like=False,
                )

    def test_opens_on_day_with_index_matches_legacy(self, all_bars):
        ts_index = _build_ts_date_index(all_bars)
        days = [date(2025, 2, 3) + timedelta(days=i) for i in range(7)]
        for day in days:
            sliced = _slice_bars_to_day(all_bars, day, ts_index)
            legacy_opens = _opens_on_day(sliced, day)
            indexed_opens = _opens_on_day(sliced, day, ts_index)
            assert legacy_opens == indexed_opens, f"opens mismatch on {day}"

    def test_slice_before_all_data_returns_empty(self, all_bars):
        ts_index = _build_ts_date_index(all_bars)
        early = date(2025, 1, 1)
        result = _slice_bars_to_day(all_bars, early, ts_index)
        assert result == {}


class TestTradingDaysEquivalence:
    """_trading_days_in_range vectorised path must return the same days."""

    def test_matches_naive_set_comprehension(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0]
        all_bars = {
            "SPY": _barset(closes, start="2025-03-10"),
            "SHY": _barset(closes, start="2025-03-10"),
        }
        start = date(2025, 3, 10)
        end = date(2025, 3, 14)

        optimised = _trading_days_in_range(all_bars, start, end)

        # Reference: naive Python loop over each ts
        naive: set[date] = set()
        for barset in all_bars.values():
            df = barset.to_dataframe()
            ts = pd.to_datetime(df["timestamp"], utc=True)
            for t in ts:
                d = t.date()
                if start <= d <= end:
                    naive.add(d)
        assert optimised == sorted(naive)

    def test_excludes_warmup_days_before_start(self):
        closes = [10.0, 11.0, 12.0, 13.0]
        all_bars = {"SPY": _barset(closes, start="2025-03-01")}
        # Only dates within [03-03, 03-04] should appear
        result = _trading_days_in_range(all_bars, date(2025, 3, 3), date(2025, 3, 4))
        assert result == [date(2025, 3, 3), date(2025, 3, 4)]


# ---------------------------------------------------------------------------
# Full engine equivalence: multi-symbol, multi-day
# ---------------------------------------------------------------------------


class TestEngineEquivalence:
    """Run the full engine; results must be bit-identical to the golden baseline
    established by test_engine_golden_regime.py which has never changed."""

    def test_trades_equity_metrics_match_golden(self, tmp_path: Path):
        """This test exercises the same fixture as the golden test but explicitly
        verifies the optimised code path produces the documented expected values."""
        closes = [10.0, 10.0, 10.0, 12.0, 13.0, 8.0, 7.0]
        bars = {"SPY": _barset(closes), "SHY": _barset(closes)}

        config_path = tmp_path / "regime_equiv.yaml"
        config_path.write_text(_REGIME_YAML, encoding="utf-8")
        loaded = StrategyLoader().load(config_path)
        store = EventStore(tmp_path / "equiv.db")

        engine = BacktestEngine(
            loaded=loaded,
            data_provider=_StubProvider(bars),
            event_store=store,
            initial_equity=10_000.0,
            slippage_pct=0.0,
            commission_per_trade=0.0,
        )
        result = engine.run(start_date=date(2025, 1, 3), end_date=date(2025, 1, 7))

        # These values are locked by test_engine_golden_regime.py and must not shift.
        assert result.trade_count == 3
        assert result.buy_count == 2
        assert result.sell_count == 1
        assert result.skipped_count == 2
        assert result.trading_days == 5
        assert result.final_equity == pytest.approx(9_231.0)

        trades = store.list_trades_for_backtest_run(result.db_id)
        filled = [
            (t.side, t.symbol, t.quantity, t.estimated_unit_price)
            for t in trades
            if t.status == "submitted"
        ]
        assert filled == [
            ("buy", "SPY", 769.0, 8.0),
            ("sell", "SPY", 769.0, 7.0),
            ("buy", "SHY", 1250.0, 7.0),
        ]

        # Equity curve has one entry per trading day.
        assert len(result.equity_curve) == 5
        # Final equity curve entry matches final_equity.
        assert result.equity_curve[-1][1] == pytest.approx(9_231.0)

    def test_multi_symbol_universe_two_year_window(self, tmp_path: Path):
        """Larger window (20 bars × 2 symbols) verifies monotone slice cursor."""
        n = 20
        closes_spy = [100.0 + i for i in range(n)]
        closes_shy = [50.0 + i * 0.5 for i in range(n)]
        bars = {
            "SPY": _barset(closes_spy, start="2025-01-02"),
            "SHY": _barset(closes_shy, start="2025-01-02"),
        }
        ts_index = _build_ts_date_index(bars)
        # Verify every day slice is identical between legacy and indexed paths.
        for offset in range(n):
            day = date(2025, 1, 2) + timedelta(days=offset)
            legacy = _slice_bars_to_day(bars, day)
            indexed = _slice_bars_to_day(bars, day, ts_index)
            for sym in set(legacy) | set(indexed):
                legacy_df = legacy[sym].to_dataframe() if sym in legacy else None
                indexed_df = indexed[sym].to_dataframe() if sym in indexed else None
                if legacy_df is None and indexed_df is None:
                    continue
                assert legacy_df is not None and indexed_df is not None, (
                    f"presence mismatch at {day} for {sym}"
                )
                pd.testing.assert_frame_equal(
                    legacy_df.reset_index(drop=True),
                    indexed_df.reset_index(drop=True),
                )

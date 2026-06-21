"""Dedicated tests for the pure decider-feature kit (``_decider_features``).

Every happy-path expectation here is computed by hand / first principles (not
just "run it and paste"), so a sign flip, a wrong divisor, or a ddof mistake
fails the test. Each documented edge branch is exercised explicitly.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from milodex.strategies._decider_features import (
    cross_sectional_zscore,
    ma_distance,
    realized_vol,
    trailing_return,
    wilder_rsi,
)

# ---------------------------------------------------------------------------
# trailing_return
# ---------------------------------------------------------------------------


def test_trailing_return_happy_path() -> None:
    # 15 / 10 - 1 = 0.5  (reference is 5 bars back).
    closes = pd.Series([10.0, 11.0, 12.0, 13.0, 15.0])
    assert trailing_return(closes, 4) == 0.5


def test_trailing_return_lookback_one() -> None:
    closes = pd.Series([10.0, 11.0, 12.0, 13.0, 15.0])
    assert trailing_return(closes, 1) == pytest.approx(15.0 / 13.0 - 1.0)


def test_trailing_return_insufficient_history_none() -> None:
    # Needs lookback + 1 bars; 5 bars with lookback 5 is one short.
    assert trailing_return(pd.Series([10.0, 11.0, 12.0, 13.0, 15.0]), 5) is None


def test_trailing_return_lookback_below_one_none() -> None:
    assert trailing_return(pd.Series([10.0, 11.0, 12.0]), 0) is None


def test_trailing_return_nonpositive_reference_none() -> None:
    # reference = first value = -1.0 <= 0 -> refuse to divide.
    assert trailing_return(pd.Series([-1.0, 2.0, 3.0]), 2) is None


# ---------------------------------------------------------------------------
# wilder_rsi
# ---------------------------------------------------------------------------


def test_wilder_rsi_hand_computed() -> None:
    # closes -> deltas [+1, +1, -1, +1], lookback 2.
    # seed: avg_gain = mean(1, 1) = 1.0 ; avg_loss = mean(0, 0) = 0.0
    # step delta=-1: avg_gain = (1*1 + 0)/2 = 0.5 ; avg_loss = (0*1 + 1)/2 = 0.5
    # step delta=+1: avg_gain = (0.5 + 1)/2 = 0.75 ; avg_loss = (0.5 + 0)/2 = 0.25
    # rs = 0.75 / 0.25 = 3 ; rsi = 100 - 100/4 = 75.0
    closes = pd.Series([10.0, 11.0, 12.0, 11.0, 12.0])
    assert wilder_rsi(closes, 2) == 75.0


def test_wilder_rsi_all_gains_is_100() -> None:
    # avg_loss == 0 with positive avg_gain -> 100.0.
    assert wilder_rsi(pd.Series([1.0, 2.0, 3.0, 4.0]), 2) == 100.0


def test_wilder_rsi_flat_is_50() -> None:
    # avg_gain == 0 and avg_loss == 0 -> 50.0 (neutral).
    assert wilder_rsi(pd.Series([5.0, 5.0, 5.0, 5.0]), 2) == 50.0


def test_wilder_rsi_insufficient_history_none() -> None:
    # len(closes) <= lookback -> None.
    assert wilder_rsi(pd.Series([1.0, 2.0]), 2) is None


def test_wilder_rsi_lookback_below_one_none() -> None:
    assert wilder_rsi(pd.Series([1.0, 2.0, 3.0]), 0) is None


# ---------------------------------------------------------------------------
# ma_distance
# ---------------------------------------------------------------------------


def test_ma_distance_hand_computed() -> None:
    # SMA(4) of [10, 20, 30, 40] = 25 ; 40 / 25 - 1 = 0.6.
    assert ma_distance(pd.Series([10.0, 20.0, 30.0, 40.0]), 4) == pytest.approx(0.6)


def test_ma_distance_insufficient_history_none() -> None:
    assert ma_distance(pd.Series([10.0, 20.0, 30.0, 40.0]), 5) is None


def test_ma_distance_lookback_below_one_none() -> None:
    assert ma_distance(pd.Series([10.0, 20.0, 30.0]), 0) is None


def test_ma_distance_nonpositive_sma_none() -> None:
    # SMA = (-4 + -4 + 8)/3 = 0 -> refuse to divide.
    assert ma_distance(pd.Series([-4.0, -4.0, 8.0]), 3) is None


# ---------------------------------------------------------------------------
# realized_vol (ddof=0 / population std)
# ---------------------------------------------------------------------------


def test_realized_vol_uses_population_std() -> None:
    # returns over [100, 110, 99, 108.9] are ~[0.1, -0.1, 0.1] (lookback 3).
    # Derive both candidate stds from first principles off the SAME returns so
    # the assertion pins ddof=0 (population) and rejects ddof=1 (sample).
    closes = pd.Series([100.0, 110.0, 99.0, 108.9])
    rets = [110.0 / 100.0 - 1.0, 99.0 / 110.0 - 1.0, 108.9 / 99.0 - 1.0]
    n = len(rets)
    mean = sum(rets) / n
    var_pop = sum((r - mean) ** 2 for r in rets) / n  # ddof=0
    var_sample = sum((r - mean) ** 2 for r in rets) / (n - 1)  # ddof=1
    pop_std = math.sqrt(var_pop)
    sample_std = math.sqrt(var_sample)
    # Sanity: the two divisors give materially different answers.
    assert pop_std == pytest.approx(0.0942809, abs=1e-7)
    assert sample_std == pytest.approx(0.1154700, abs=1e-7)

    got = realized_vol(closes, 3)
    assert got == pytest.approx(pop_std, rel=1e-12, abs=1e-12)
    assert got != pytest.approx(sample_std)


def test_realized_vol_insufficient_history_none() -> None:
    # Needs lookback + 1 bars; 4 bars with lookback 4 is one short.
    assert realized_vol(pd.Series([100.0, 110.0, 99.0, 108.9]), 4) is None


def test_realized_vol_lookback_below_two_none() -> None:
    assert realized_vol(pd.Series([100.0, 110.0, 99.0, 108.9]), 1) is None


# ---------------------------------------------------------------------------
# cross_sectional_zscore (population)
# ---------------------------------------------------------------------------


def test_cross_sectional_zscore_hand_computed() -> None:
    # values [1, 2, 3]: mean 2, population std = sqrt(2/3) = 0.81649658...
    # z = (x - 2) / std -> [-1.2247448, 0.0, +1.2247448].
    zs = cross_sectional_zscore({"A": 1.0, "B": 2.0, "C": 3.0})
    expected = 1.0 / math.sqrt(2.0 / 3.0)
    assert zs["A"] == pytest.approx(-expected, rel=1e-12, abs=1e-12)
    assert zs["B"] == 0.0
    assert zs["C"] == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_cross_sectional_zscore_empty() -> None:
    assert cross_sectional_zscore({}) == {}


def test_cross_sectional_zscore_single_value_is_zero() -> None:
    # std == 0 (one element) -> neutral 0.0, not a divide-by-zero.
    assert cross_sectional_zscore({"A": 7.0}) == {"A": 0.0}


def test_cross_sectional_zscore_zero_variance_is_zero() -> None:
    # All equal -> std 0 -> every z-score 0.0.
    assert cross_sectional_zscore({"A": 5.0, "B": 5.0, "C": 5.0}) == {
        "A": 0.0,
        "B": 0.0,
        "C": 0.0,
    }

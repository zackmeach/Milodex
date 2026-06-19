"""Tests for the single_symbol cardinality guard."""

import pytest

from milodex.strategies.base import single_symbol


def test_single_symbol_returns_sole_symbol():
    assert single_symbol(("spy",)) == "SPY"
    assert single_symbol(["XLF"]) == "XLF"


def test_single_symbol_none_on_empty():
    assert single_symbol(()) is None
    assert single_symbol([]) is None


def test_single_symbol_raises_on_multi():
    with pytest.raises(ValueError, match="single-symbol strategy received"):
        single_symbol(("SPY", "QQQ"))


def test_single_symbol_dedups_then_counts_distinct():
    # case-insensitive dedup of one logical symbol is size 1, not multi
    assert single_symbol(("SPY", "spy")) == "SPY"

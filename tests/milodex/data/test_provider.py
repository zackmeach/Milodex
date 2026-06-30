"""Tests for the DataProvider abstract base class.

R-DAT-001: DataProvider is an ABC declaring exactly get_bars, get_latest_bar, and
get_tradeable_assets. A subclass omitting any one cannot be instantiated.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from milodex.data.models import Bar, BarSet, Timeframe
from milodex.data.provider import DataProvider

_ABSTRACT_METHODS = ("get_bars", "get_latest_bar", "get_tradeable_assets")


class _FullProvider(DataProvider):
    """Concrete subclass implementing every abstract method — instantiates cleanly."""

    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, BarSet]:
        raise NotImplementedError  # pragma: no cover

    def get_latest_bar(self, symbol: str) -> Bar:
        raise NotImplementedError  # pragma: no cover

    def get_tradeable_assets(self) -> list[str]:
        raise NotImplementedError  # pragma: no cover


def test_abstract_method_set() -> None:
    """R-DAT-001: the ABC declares exactly the three required abstract methods."""
    abstract = {
        name
        for name, member in inspect.getmembers(DataProvider)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert abstract == set(_ABSTRACT_METHODS)


def test_full_concrete_subclass_instantiates() -> None:
    """R-DAT-001 (positive): a subclass implementing all three methods instantiates."""
    provider = _FullProvider()
    assert isinstance(provider, DataProvider)


@pytest.mark.parametrize("missing_method", _ABSTRACT_METHODS)
def test_incomplete_subclass_raises_type_error(missing_method: str) -> None:
    """R-DAT-001 (negative): omitting any single abstract method fails instantiation."""
    methods = {
        name: getattr(_FullProvider, name) for name in _ABSTRACT_METHODS if name != missing_method
    }
    incomplete_cls = type(f"_Missing_{missing_method}", (DataProvider,), methods)
    with pytest.raises(TypeError):
        incomplete_cls()  # type: ignore[abstract]

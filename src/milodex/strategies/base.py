"""Strategy contract and shared evaluation context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from milodex.data.models import BarSet

if TYPE_CHECKING:
    from milodex.execution.models import TradeIntent


@dataclass(frozen=True)
class StrategyParameterSpec:
    """Declares one allowed strategy parameter."""

    name: str
    expected_types: tuple[type[Any], ...]
    required: bool = True
    allow_none: bool = False


@dataclass(frozen=True)
class StrategyContext:
    """Immutable runtime context passed into strategy evaluation.

    ``bars_by_symbol`` is populated by the runner for strategy families
    (like ``meanrev``) that evaluate cross-sectionally across a universe.
    Single-asset families (like ``regime``) may ignore it and read only
    the primary ``BarSet`` passed to ``evaluate``.
    """

    strategy_id: str
    family: str
    template: str
    variant: str
    version: int
    config_hash: str
    parameters: Mapping[str, Any]
    universe: tuple[str, ...]
    universe_ref: str | None
    disable_conditions: tuple[str, ...]
    config_path: str
    manifest: Mapping[str, Any]
    positions: Mapping[str, float] = field(default_factory=dict)
    equity: float = 0.0
    bars_by_symbol: Mapping[str, BarSet] = field(default_factory=dict)
    entry_state: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


class Strategy(ABC):
    """Pure signal-generation contract for Milodex strategies."""

    family: str
    template: str
    parameter_specs: Sequence[StrategyParameterSpec] = ()

    @abstractmethod
    def evaluate(self, bars: BarSet, context: StrategyContext) -> list[TradeIntent]:
        """Return zero or more trade intents from the provided inputs."""

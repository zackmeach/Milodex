"""Strategy contract and shared evaluation context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
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


@dataclass(frozen=True)
class DecisionReasoning:
    """Structured record of *why* a strategy evaluation chose what it chose.

    The payload this fills out is the forward-only contract backing
    R-XC-008: the strategy-side "triggering data / rule evaluated /
    alternatives rejected / human-readable explanation" fields required
    by the SRS. The shape is **internal** — it is serialized into the
    free-form ``ExplanationEvent.context["reasoning"]`` JSON blob and
    will be frozen as a stable contract only when a second consumer
    emerges.

    Shape of the serialized dict (``asdict(reasoning)``):

    - ``rule`` — canonical rule identifier. Examples:
      ``"regime.ma_filter_cross"``, ``"regime.hold"``,
      ``"meanrev.rsi_entry"``, ``"meanrev.rsi_exit"``,
      ``"meanrev.stop_loss"``, ``"meanrev.max_hold"``, ``"no_signal"``.
      ``"no_signal"`` is a legal rule — emitted when the cycle produced
      zero intents — so downstream consumers never have to handle a
      ``None`` rule.
    - ``triggering_values`` — inputs the rule actually consumed.
      Per-family shape. Kept as a typed-enough dict rather than a
      nested dataclass.
    - ``threshold`` — threshold-side of the comparison. May overlap
      ``triggering_values`` for crossover rules.
    - ``ranking`` — ordered list of considered candidates and their
      scores (cross-sectional families only; ``None`` elsewhere).
    - ``rejected_alternatives`` — list of ``{"symbol": ..., "reason":
      ...}`` for candidates the rule eliminated. Empty list when none.
    - ``narrative`` — one-sentence human-readable summary. Suggested
      template: ``"<rule>: <trigger> vs <threshold> → <action>"``. The
      first field an operator reads; readability beats greppability.
    - ``extras`` — escape hatch for strategy-specific debugging fields
      that shouldn't bloat the main shape.
    """

    rule: str
    narrative: str
    triggering_values: Mapping[str, float | int | str | None] = field(default_factory=dict)
    threshold: Mapping[str, float | int | str | None] = field(default_factory=dict)
    ranking: list[Mapping[str, Any]] | None = None
    rejected_alternatives: list[Mapping[str, Any]] = field(default_factory=list)
    extras: Mapping[str, Any] = field(default_factory=dict)

    def asdict(self) -> dict[str, Any]:
        """Return the JSON-serializable dict shape used in ``context["reasoning"]``."""
        return asdict(self)


@dataclass(frozen=True)
class StrategyDecision:
    """Return value of :meth:`Strategy.evaluate`.

    Bundles the (possibly empty) list of emitted ``TradeIntent`` objects
    with the cycle-level :class:`DecisionReasoning` explaining the
    outcome. A cycle producing *zero* intents is still a decision with a
    story to tell (e.g. ``rule="no_signal"``) — the wrapper ensures that
    story has a home.
    """

    intents: list[TradeIntent]
    reasoning: DecisionReasoning


class Strategy(ABC):
    """Pure signal-generation contract for Milodex strategies."""

    family: str
    template: str
    parameter_specs: Sequence[StrategyParameterSpec] = ()

    @abstractmethod
    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        """Return the trade intents plus cycle-level reasoning.

        The returned :class:`StrategyDecision` always carries a
        :class:`DecisionReasoning` — even when ``intents`` is empty.
        This is how R-XC-008's "every meaningful decision captures an
        explanation" requirement is honored at the strategy boundary:
        the runner / backtest engine persists the reasoning dict into
        the ``ExplanationEvent.context`` JSON blob on every cycle.
        """

"""Strategy contract and shared evaluation context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import TYPE_CHECKING, Any

from milodex.data.models import BarSet

if TYPE_CHECKING:
    from milodex.execution.models import TradeIntent


@dataclass(frozen=True)
class StrategyParameterSpec:
    """Declares one allowed strategy parameter.

    Beyond name/type/presence, a spec may declare value constraints —
    inclusive numeric bounds (``minimum``/``maximum``), exclusive numeric
    bounds (``exclusive_minimum``/``exclusive_maximum``), and an
    allowed-values enum (``choices``). The loader enforces declared
    constraints at config-load time (P2-04), so an out-of-range or
    unknown-enum value fails when the YAML is loaded — with the file
    named — instead of at first evaluation. Constraints left ``None``
    are not checked.
    """

    name: str
    expected_types: tuple[type[Any], ...]
    required: bool = True
    allow_none: bool = False
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = None
    exclusive_maximum: float | None = None
    choices: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class StrategyParameterRelation:
    """Named cross-parameter constraint enforced at config-load time.

    ``check`` receives the raw ``strategy.parameters`` mapping (after
    every per-parameter presence/type/bound check has passed) and returns
    ``None`` when the relation is satisfied, or a human-readable violation
    detail when it is not. Strategies declare relations on the class-level
    ``parameter_relations`` tuple, mirroring the ``parameter_specs`` idiom.
    """

    name: str
    check: Callable[[Mapping[str, Any]], str | None]


def relation_less_than(lesser: str, greater: str) -> StrategyParameterRelation:
    """Relation requiring ``parameters[lesser] < parameters[greater]``."""

    def check(parameters: Mapping[str, Any]) -> str | None:
        if float(parameters[lesser]) < float(parameters[greater]):
            return None
        return (
            f"require {lesser} < {greater}, got "
            f"{lesser}={parameters[lesser]!r}, {greater}={parameters[greater]!r}"
        )

    return StrategyParameterRelation(name=f"{lesser} < {greater}", check=check)


def relation_at_least(left: str, right: str) -> StrategyParameterRelation:
    """Relation requiring ``parameters[left] >= parameters[right]``."""

    def check(parameters: Mapping[str, Any]) -> str | None:
        if float(parameters[left]) >= float(parameters[right]):
            return None
        return (
            f"require {left} >= {right}, got "
            f"{left}={parameters[left]!r}, {right}={parameters[right]!r}"
        )

    return StrategyParameterRelation(name=f"{left} >= {right}", check=check)


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

    Non-rule decision fields (added when the *second consumer* arrived: the
    substitutable decision layer — axis 3 of the capability map). All default
    to ``None`` and are **omitted from the serialized blob when unset**, so a
    rule strategy that never populates them serializes the identical legacy
    payload. A non-rule decider populates whichever describe its paradigm:

    - ``kind`` — decision-paradigm tag (e.g. ``"scored"``, ``"tree"``).
    - ``score`` — the continuous decision score of the selected candidate
      (linear / ranking deciders). Unlike a boolean threshold comparison this
      is a real value that *orders* candidates.
    - ``decision_path`` — ordered tuple of traversed split steps for a tree /
      bucketed-lookup decider, terminating at a leaf action.
    - ``feature_contributions`` — per-feature weighted contribution to the
      selected candidate's ``score`` (linear deciders).
    """

    rule: str
    narrative: str
    triggering_values: Mapping[str, float | int | str | None] = field(default_factory=dict)
    threshold: Mapping[str, float | int | str | None] = field(default_factory=dict)
    ranking: list[Mapping[str, Any]] | None = None
    rejected_alternatives: list[Mapping[str, Any]] = field(default_factory=list)
    extras: Mapping[str, Any] = field(default_factory=dict)
    # Non-rule decision fields. Flagged ``omit_if_default`` so they vanish from
    # the serialized blob unless a non-rule decider sets them — keeping every
    # rule strategy's payload byte-identical to the pre-generalization shape.
    kind: str | None = field(default=None, metadata={"omit_if_default": True})
    score: float | None = field(default=None, metadata={"omit_if_default": True})
    decision_path: tuple[Mapping[str, Any], ...] | None = field(
        default=None, metadata={"omit_if_default": True}
    )
    feature_contributions: Mapping[str, float] | None = field(
        default=None, metadata={"omit_if_default": True}
    )

    def asdict(self) -> dict[str, Any]:
        """Return the JSON-serializable dict shape used in ``context["reasoning"]``.

        Fields flagged ``omit_if_default`` in their metadata — the non-rule
        decision fields above — are dropped when left at their default, so a
        rule strategy that never sets them serializes the identical legacy
        payload (the existing rule strategies' blobs are byte-unchanged). The
        omission is driven purely by field metadata and a
        field-equals-its-own-default test: there is **no branch on decider
        type, ``kind`` value, template, or class**, so rule strategies and
        non-rule deciders traverse this one code path identically.
        """
        data = asdict(self)
        for spec in fields(self):
            if not spec.metadata.get("omit_if_default"):
                continue
            default = (
                spec.default_factory() if spec.default_factory is not MISSING else spec.default
            )
            if data[spec.name] == default:
                del data[spec.name]
        return data


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
    parameter_relations: Sequence[StrategyParameterRelation] = ()

    def max_lookback_periods(self) -> int:
        """Return the maximum number of trading-day bars this strategy needs to
        compute its indicators without NaN.

        The backtest engine uses this value to size the warmup window it
        requests from the data provider.  A warmup of at least
        ``max_lookback_periods()`` trading days ensures that on the first
        evaluation day the strategy receives a full indicator history.

        **Override this method** in any strategy whose largest lookback cannot
        be reliably inferred from its integer-valued config parameters (e.g.
        when the lookback is stored as a float or is buried inside a nested
        sub-dict).  The default implementation scans the strategy's
        :attr:`parameter_specs` names against the context parameters at class
        level, which is not available here, so it falls back to 0 and lets the
        engine's own heuristic take over.  Concrete strategies that declare a
        float or nested lookback MUST override and return the correct value.

        The default of ``0`` is intentionally conservative: the engine's
        existing integer-parameter heuristic (``max(int_params) * 3``, floor
        365 calendar days) already provides correct warmup for all strategies
        whose lookback params are stored as plain Python ``int`` values.
        Returning ``0`` here means "no override; use the heuristic" — preserving
        current behaviour for all existing strategies.
        """
        return 0

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

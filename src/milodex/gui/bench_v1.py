"""Bench v1 read-model schema and menu-rule pure functions.

Per the Bench v1 freeze (ADRs 0049, 0050, 0047 amend), this module defines
the data types and pure functions that drive the per-row Action menu on
the Bench surface:

- :class:`Stage`, :class:`Freshness`, :class:`GateResult` — string enums
  for the three categorical axes the menu rules consume.
- :class:`EvidenceRecord` — a single per-stage evidence record carrying
  ``freshness`` and ``gate_result`` as orthogonal axes (ADR 0050 Decision 2).
- :class:`BenchStrategyState` — the per-strategy view-model. Holds
  ``current_stage``, ``evidence_by_stage``, ``runs_in_flight``, and
  ``is_session_running``. ``runs_in_flight`` is **operational run state**,
  separate from evidence (ADR 0050 Decision 3).
- :class:`MenuItem` — the Action menu item record returned by the
  composer.
- Pure functions: :func:`can_promote_to_next`, :func:`can_return_to`,
  :func:`re_run_verb`, :func:`can_demote`, and the composer
  :func:`compute_menu_items`.

This module contains **no side effects, no event-store reads, no broker
calls, and no QML wiring**. It is pure data + pure functions, consumed
by the read-model layer and by tests.

v1 scope per ADR 0049: the Bench surface is a visual prototype; no
backend mutation. Real ``Freshness`` / ``GateResult`` derivation from
event history is deferred to v2. v1 callers populate
:class:`EvidenceRecord` values directly (typically from fixture data
per ADR 0049 Decision 5).

The verb grammar this module implements is locked by ADR 0050 Decision 7.
The menu composer additionally applies two policy filters that are not
part of the pure rules:

- ADR 0004 paper-only lock — hides ``Promote to Micro Live`` and
  ``Promote to Live`` in v1.
- ADR 0043 Decision 3 + ADR 0004 — hides capital-affecting demotions
  (from ``MICRO_LIVE`` and ``LIVE``) in v1.

Both filters are isolated to constants at module scope so the v2 PR that
opens those gates can flip them with a single edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# ---------------------------------------------------------------------------
# Categorical axes
# ---------------------------------------------------------------------------


class Stage(StrEnum):
    """Promotion-pipeline stages, in pipeline order.

    String values match the existing convention in ``read_models.py``
    (lowercase; snake_case for multi-word stages). The string form is
    what QML and the SQLite schema use; the enum is a typed handle for
    Python code.
    """

    IDLE = "idle"
    BACKTEST = "backtest"
    PAPER = "paper"
    MICRO_LIVE = "micro_live"
    LIVE = "live"


class Freshness(StrEnum):
    """Evidence freshness — when the evidence was observed and whether it
    is still trustworthy.

    Independent of :class:`GateResult`. See ADR 0050 Decision 3.

    - ``MISSING``: no completed usable evidence exists for this stage.
      Always pairs with ``gate_result=PENDING`` (no verdict). Whether a
      run is currently in flight is carried by
      :attr:`BenchStrategyState.runs_in_flight`, not here.
    - ``FRESH``: recent and accepted; current. Makes no claim about
      gate result.
    - ``AGING``: older than the "fresh" threshold but younger than the
      "stale" threshold. Still usable for ``Promote`` and ``Return``
      paths if paired with ``PASS``; the modal warns about approaching
      staleness. With ``FAIL``, behaves like a Fail evidence record per
      :func:`re_run_verb`.
    - ``STALE``: past the "stale" threshold. Cannot support
      state-changing verbs.
    - ``INVALIDATED``: explicitly killed by an event (manifest drift,
      code change, config change, risk-policy change, methodology
      change, data-source change, fee/slippage change). Cannot be used.

    v1 scope: no real freshness computation. Fixture data assigns these
    values directly per ADR 0049 Decision 5.
    """

    MISSING = "missing"
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    INVALIDATED = "invalidated"


class GateResult(StrEnum):
    """Gate evaluation result — whether the evidence meets criteria.

    Independent of :class:`Freshness`. See ADR 0050 Decision 4.

    - ``PASS``: gate criteria met.
    - ``FAIL``: gate criteria not met.
    - ``PENDING``: evaluation accepted, in flight, or otherwise without
      a verdict.
    - ``NOT_APPLICABLE``: only valid for ``Stage.LIVE`` evidence. LIVE
      has no further promotion gate; the value records that fact rather
      than encoding it as record absence.
    """

    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    NOT_APPLICABLE = "not_applicable"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRecord:
    """A single per-stage evidence record.

    The two axes (:attr:`freshness`, :attr:`gate_result`) are
    orthogonal — see ADR 0050 Decision 2. Each can take any combination
    of values that the invariants permit; in particular,
    ``Freshness.MISSING`` always pairs with ``GateResult.PENDING``
    (no completed evidence implies no verdict).

    ``GateResult.NOT_APPLICABLE`` is restricted to ``Stage.LIVE``
    evidence by convention. The invariant is documented here and
    enforced by tests rather than at construction time so that fixture
    authoring remains low-friction during prototyping.

    Stage-specific metric snapshots and timestamps are intentionally
    not modeled in v1 — the evidence modal will surface those in PR I
    once their shape is settled.
    """

    freshness: Freshness
    gate_result: GateResult


@dataclass(frozen=True)
class BenchStrategyState:
    """Per-strategy view-model surfaced to the Bench Action menu.

    Holds the categorical state needed to compute the per-row Action
    menu via the pure functions in this module. Real wiring to the
    event store and the runner is v2 work; v1 callers populate this
    directly from fixture data.

    - ``current_stage``: the strategy's current promotion stage.
    - ``evidence_by_stage``: per-stage historical evidence. Keys are
      :class:`Stage` values; missing keys imply the strategy never
      reached that stage (and the rules treat the absence as not
      eligible for a ``Return`` verb).
    - ``runs_in_flight``: per-stage **operational run state**, separate
      from evidence. ``True`` at a stage means an
      accepted-but-not-completed backtest run exists at that stage.
      Sourced (eventually, in v2) from the open-runs view in the event
      store. Per ADR 0050 Decision 3, this lives outside
      :class:`EvidenceRecord` by design — it is operational state, not
      historical evidence.
    - ``is_session_running``: whether the strategy currently has an
      active trading session at its current stage. Drives the
      ``Start Trading`` vs ``Stop Trading`` verb visibility per ADR
      0049 Decision 4. Kept as a bool in v1 to match the bool surface
      area of the menu rule; richer session-state modeling is deferred.
    """

    current_stage: Stage
    evidence_by_stage: dict[Stage, EvidenceRecord] = field(default_factory=dict)
    runs_in_flight: dict[Stage, bool] = field(default_factory=dict)
    is_session_running: bool = False


@dataclass(frozen=True)
class MenuItem:
    """A single Action menu item.

    :attr:`verb_class` categorizes the item per ADR 0050 Decision 7:

    - ``"directional"``: ``Promote to X``, ``Demote to X``, ``Return to X``
    - ``"invocation"``: ``Initiate Backtest``, ``Refresh Backtest``,
      ``Start Trading``, ``Stop Trading``
    - ``"informational"``: ``Open Evidence`` (the empty-menu floor per
      ADR 0047 Decision 5)

    :attr:`target_stage` is set for directional verbs (the stage the
    operator is moving to) and ``None`` for invocation and
    informational verbs.
    """

    label: str
    verb_class: str
    target_stage: Stage | None = None


# ---------------------------------------------------------------------------
# Locked verb labels
#
# Constants for the labels that don't take a stage parameter. Stage-templated
# labels (Promote to <stage>, Return to <stage>) are produced by helper
# functions below.
# ---------------------------------------------------------------------------

LABEL_INITIATE_BACKTEST = "Initiate Backtest"
LABEL_REFRESH_BACKTEST = "Refresh Backtest"
LABEL_START_TRADING = "Start Trading"
LABEL_STOP_TRADING = "Stop Trading"
LABEL_DEMOTE_TO_BACKTEST = "Demote to Backtest"
LABEL_RETURN_TO_IDLE = "Return to Idle"
LABEL_OPEN_EVIDENCE = "Open Evidence"


# ---------------------------------------------------------------------------
# Policy filters applied by the menu composer (not by the pure rules)
#
# These constants encode the v1 ADR-0004 / ADR-0043 capital-stage locks.
# When a future ADR opens the live boundary, flipping these constants is
# the single edit that surfaces the previously-hidden verbs.
# ---------------------------------------------------------------------------

# ADR 0004 paper-only lock: forward promotion into capital stages is
# hidden from the menu in v1. The pure rule (`can_promote_to_next`)
# may still return True; the composer applies this filter.
ADR_0004_HIDDEN_PROMOTION_TARGETS: frozenset[Stage] = frozenset(
    {Stage.MICRO_LIVE, Stage.LIVE}
)

# ADR 0043 Decision 3 + ADR 0004: capital-affecting demotions remain
# locked rather than merely confirmed while ADR 0004 is in force. The
# pure rule (`can_demote`) returns True at PAPER+; the composer hides
# the verb when ``current_stage`` is in this set.
ADR_0043_LIVE_LOCKED_DEMOTIONS_FROM: frozenset[Stage] = frozenset(
    {Stage.MICRO_LIVE, Stage.LIVE}
)


# ---------------------------------------------------------------------------
# Pure menu rules (per ADR 0050 Decision 5)
# ---------------------------------------------------------------------------


def can_promote_to_next(state: BenchStrategyState) -> bool:
    """Is ``Promote to <next stage>`` available?

    Per ADR 0050 Decision 5: requires the current stage's evidence to
    be ``Fresh|Aging`` with ``Pass`` gate. There is no
    ``Promote to Backtest`` verb (IDLE → BACKTEST is system-driven on
    backtest job acceptance per ADR 0050 Decision 6); ``LIVE`` has no
    further promotion gate.

    The ADR 0004 paper-only lock is applied by the menu composer, not
    by this rule. The rule reflects the underlying eligibility; the
    composer hides the resulting verb when policy demands.
    """
    if state.current_stage in {Stage.IDLE, Stage.LIVE}:
        return False
    ev = state.evidence_by_stage.get(state.current_stage)
    if ev is None:
        return False
    return (
        ev.freshness in {Freshness.FRESH, Freshness.AGING}
        and ev.gate_result == GateResult.PASS
    )


def can_return_to(state: BenchStrategyState, target_stage: Stage) -> bool:
    """Is ``Return to <target_stage>`` available?

    Two distinct cases per ADR 0050 Decision 5:

    1. **Return to Idle** — the to-shelf affordance, available from any
       active stage. No freshness check; IDLE is the inactive shelf,
       not an evaluated state.

    2. **Return to <active stage>** — the leave-IDLE affordance. Only
       available when ``current_stage == IDLE``; requires the target's
       evidence to be ``Fresh|Aging`` with ``Pass`` (or
       ``NotApplicable`` when the target is ``LIVE`` specifically — the
       only stage where ``NotApplicable`` is a valid gate result).
       Active-to-active ``Return`` is not a Bench verb; that is
       ``Promote`` or ``Demote`` territory.
    """
    if target_stage == state.current_stage:
        return False
    if target_stage == Stage.IDLE:
        return state.current_stage != Stage.IDLE
    if state.current_stage != Stage.IDLE:
        return False
    ev = state.evidence_by_stage.get(target_stage)
    if ev is None:
        return False
    if ev.freshness not in {Freshness.FRESH, Freshness.AGING}:
        return False
    if target_stage == Stage.LIVE:
        return ev.gate_result in {GateResult.PASS, GateResult.NOT_APPLICABLE}
    return ev.gate_result == GateResult.PASS


def re_run_verb(evidence: EvidenceRecord, *, is_run_in_flight: bool) -> str | None:
    """Which re-run verb (if any) should appear for this evidence record?

    Per ADR 0050 Decision 5. Returns one of:

    - :data:`LABEL_REFRESH_BACKTEST` when evidence is
      ``(Aging|Stale) + Pass`` — renew prior usable passing evidence.
    - :data:`LABEL_INITIATE_BACKTEST` when evidence is ``Invalidated``
      (at any age), ``(Aging|Stale) + Fail``, or
      ``Missing`` (with no in-flight run) — produce new evidence from
      a non-usable baseline.
    - ``None`` when no re-run verb should surface. Cases:

      - ``is_run_in_flight=True``: a run is already in flight; the
        Open Evidence floor item carries the monitoring affordance.
      - ``Fresh+Pass`` and ``Fresh+Fail``: workflow discipline — an
        invalidating change must transition the evidence to
        ``Invalidated`` before a re-run is offered. Prevents blind
        re-runs of the same configuration.
      - ``Aging+Pending`` and ``Stale+Pending``: a run is in flight
        on top of prior evidence (typically a refresh-in-flight). The
        ``is_run_in_flight`` guard is the canonical way to express
        "wait for the run to resolve," but the freshness/gate combo
        alone also produces no verb. Open Evidence shows the monitor.
      - Any LIVE-stage evidence (``gate_result == NotApplicable``):
        LIVE has no concept of a backtest re-run.

    ``is_run_in_flight`` is operational run state — the caller resolves
    it from :attr:`BenchStrategyState.runs_in_flight` at the relevant
    stage. It is deliberately separate from :class:`EvidenceRecord` per
    ADR 0050 Decision 3.
    """
    if is_run_in_flight:
        return None
    if (
        evidence.freshness in {Freshness.AGING, Freshness.STALE}
        and evidence.gate_result == GateResult.PASS
    ):
        return LABEL_REFRESH_BACKTEST
    if evidence.freshness == Freshness.MISSING:
        return LABEL_INITIATE_BACKTEST
    if evidence.freshness == Freshness.INVALIDATED:
        return LABEL_INITIATE_BACKTEST
    if evidence.gate_result == GateResult.FAIL and evidence.freshness in {
        Freshness.AGING,
        Freshness.STALE,
    }:
        return LABEL_INITIATE_BACKTEST
    # Fresh+Pass, Fresh+Fail, and LIVE-stage NotApplicable do not
    # surface a re-run verb by default.
    return None


def can_demote(state: BenchStrategyState) -> bool:
    """Is ``Demote to Backtest`` available?

    Per ADR 0043: governance-gated, not evidence-gated. Available at
    ``PAPER``, ``MICRO_LIVE``, and ``LIVE``. The modal the verb opens
    is responsible for typed-confirmation friction for capital-
    affecting demotions (per ADR 0043 Decision 3, capital-stage
    demotions remain locked while ADR 0004 is in force; that
    constraint is applied by the menu composer, not by this rule).
    """
    return state.current_stage in {Stage.PAPER, Stage.MICRO_LIVE, Stage.LIVE}


# ---------------------------------------------------------------------------
# Menu composer
# ---------------------------------------------------------------------------


def compute_menu_items(state: BenchStrategyState) -> list[MenuItem]:
    """Compute the visible Action menu items for a Bench row, in display order.

    Per ADR 0047 Decision 5, the result is **never empty** — the
    informational floor item :data:`LABEL_OPEN_EVIDENCE` is always
    appended last.

    Per ADR 0049, this function is pure: it inspects ``state`` and
    returns a list of :class:`MenuItem` records. It does not mutate
    anything, does not read from the event store, and does not call
    the broker.

    The composer applies two policy filters on top of the pure rules,
    both encoded by the module-level constants
    :data:`ADR_0004_HIDDEN_PROMOTION_TARGETS` and
    :data:`ADR_0043_LIVE_LOCKED_DEMOTIONS_FROM`. When a future ADR
    opens the live boundary, those constants change; the composer
    surfaces the previously-hidden verbs without further edits.

    Display order: **directional verbs first, then invocation verbs,
    then the informational floor.** The operator decides *what changes*
    (Promote / Demote / Return) before *what runs* (Initiate / Refresh /
    Start / Stop). Open Evidence is always last per ADR 0047 Decision 5.

    Within directional verbs, the order is:
    1. Promote to next stage (forward action; if available)
    2. Return to <active stage> verbs (IDLE rows only; deepest target
       first — Live → Micro Live → Paper, matching bench-brief §7.3
       examples that surface the most-consequential restoration first)
    3. Demote to Backtest (backward action from active stages; if not
       policy-locked)
    4. Return to Idle (to-shelf affordance from active stages)

    Within invocation verbs, the order is:
    5. Start Trading / Stop Trading (current-stage runtime control)
    6. Re-run verb (Refresh Backtest or Initiate Backtest)

    7. Open Evidence (informational floor; always last)
    """
    items: list[MenuItem] = []

    # ---- Directional verbs (operator-driven stage transitions) ---------

    # 1. Promote to next stage
    if can_promote_to_next(state):
        next_stage = _next_stage(state.current_stage)
        if next_stage is not None and next_stage not in ADR_0004_HIDDEN_PROMOTION_TARGETS:
            items.append(
                MenuItem(
                    label=label_promote_to(next_stage),
                    verb_class="directional",
                    target_stage=next_stage,
                )
            )

    # 2. Return to <active stage> — leave-IDLE affordance, deepest first.
    # Iterating LIVE → MICRO_LIVE → PAPER surfaces the most-consequential
    # restoration target before shallower ones, matching the bench-brief
    # §7.3 IDLE-with-prior-LIVE example. BACKTEST is excluded: there is
    # no `Return to Backtest` verb per ADR 0050 Decision 7. From IDLE, a
    # backtest is started via Initiate Backtest and the system-driven
    # IDLE → BACKTEST transition (ADR 0050 Decision 6).
    for target in (Stage.LIVE, Stage.MICRO_LIVE, Stage.PAPER):
        if can_return_to(state, target):
            items.append(
                MenuItem(
                    label=label_return_to(target),
                    verb_class="directional",
                    target_stage=target,
                )
            )

    # 3. Demote to Backtest (active stages, not capital-stage-locked)
    if can_demote(state) and state.current_stage not in ADR_0043_LIVE_LOCKED_DEMOTIONS_FROM:
        items.append(
            MenuItem(
                label=LABEL_DEMOTE_TO_BACKTEST,
                verb_class="directional",
                target_stage=Stage.BACKTEST,
            )
        )

    # 4. Return to Idle (to-shelf affordance from any active stage)
    if can_return_to(state, Stage.IDLE):
        items.append(
            MenuItem(
                label=LABEL_RETURN_TO_IDLE,
                verb_class="directional",
                target_stage=Stage.IDLE,
            )
        )

    # ---- Invocation verbs (operator-driven jobs/sessions) --------------

    # 5. Start Trading / Stop Trading at trading-eligible stages
    if state.current_stage in {Stage.PAPER, Stage.MICRO_LIVE, Stage.LIVE}:
        if state.is_session_running:
            items.append(
                MenuItem(
                    label=LABEL_STOP_TRADING,
                    verb_class="invocation",
                )
            )
        else:
            items.append(
                MenuItem(
                    label=LABEL_START_TRADING,
                    verb_class="invocation",
                )
            )

    # 6. Re-run verb (operates on BACKTEST evidence)
    backtest_evidence = state.evidence_by_stage.get(Stage.BACKTEST)
    if backtest_evidence is not None:
        backtest_in_flight = state.runs_in_flight.get(Stage.BACKTEST, False)
        verb = re_run_verb(backtest_evidence, is_run_in_flight=backtest_in_flight)
        if verb is not None:
            items.append(
                MenuItem(
                    label=verb,
                    verb_class="invocation",
                )
            )

    # ---- Informational floor (ADR 0047 Decision 5) ---------------------

    # 7. Open Evidence — always present, always last
    items.append(
        MenuItem(
            label=LABEL_OPEN_EVIDENCE,
            verb_class="informational",
        )
    )

    return items


# ---------------------------------------------------------------------------
# Stage-templated label helpers
# ---------------------------------------------------------------------------


def label_promote_to(stage: Stage) -> str:
    """Display label for ``Promote to <stage>``."""
    return f"Promote to {_stage_display(stage)}"


def label_return_to(stage: Stage) -> str:
    """Display label for ``Return to <stage>``."""
    return f"Return to {_stage_display(stage)}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _next_stage(stage: Stage) -> Stage | None:
    """Next promotion stage in pipeline order, or None at LIVE (terminal)."""
    return _NEXT_STAGE.get(stage)


_NEXT_STAGE: dict[Stage, Stage | None] = {
    # IDLE → BACKTEST is system-driven (ADR 0050 Decision 6); no operator
    # `Promote to Backtest` verb exists. Documented here for pipeline-order
    # completeness; `can_promote_to_next` returns False at IDLE so the
    # composer never reaches this entry.
    Stage.IDLE: Stage.BACKTEST,
    Stage.BACKTEST: Stage.PAPER,
    Stage.PAPER: Stage.MICRO_LIVE,
    Stage.MICRO_LIVE: Stage.LIVE,
    Stage.LIVE: None,
}


def _stage_display(stage: Stage) -> str:
    """Title-cased stage name for use inside operator-facing verb labels."""
    return _STAGE_DISPLAY[stage]


_STAGE_DISPLAY: dict[Stage, str] = {
    Stage.IDLE: "Idle",
    Stage.BACKTEST: "Backtest",
    Stage.PAPER: "Paper",
    Stage.MICRO_LIVE: "Micro Live",
    Stage.LIVE: "Live",
}


__all__ = [
    "ADR_0004_HIDDEN_PROMOTION_TARGETS",
    "ADR_0043_LIVE_LOCKED_DEMOTIONS_FROM",
    "BenchStrategyState",
    "EvidenceRecord",
    "Freshness",
    "GateResult",
    "LABEL_DEMOTE_TO_BACKTEST",
    "LABEL_INITIATE_BACKTEST",
    "LABEL_OPEN_EVIDENCE",
    "LABEL_REFRESH_BACKTEST",
    "LABEL_RETURN_TO_IDLE",
    "LABEL_START_TRADING",
    "LABEL_STOP_TRADING",
    "MenuItem",
    "Stage",
    "can_demote",
    "can_promote_to_next",
    "can_return_to",
    "compute_menu_items",
    "label_promote_to",
    "label_return_to",
    "re_run_verb",
]

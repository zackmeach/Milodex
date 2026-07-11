"""R-PRM-004 lifecycle-criteria enforcement (ADR 0058 M4 addendum).

The SRS R-PRM-004 lifecycle-proof paper gate exempts the regime strategy from
the statistical thresholds but requires three operational criteria instead:

  (a) a successful deterministic backtest run;
  (b) explanation records (R-XC-008) generated for every simulated signal;
  (c) the risk layer having rejected at least one synthetic fault-injection trade.

Until the M4 addendum these were recorded as unenforced (``deferred="M4"``). This
module evaluates all three against the event store, fail-closed. It is called
from the promotion orchestrator's lifecycle-exempt admissibility path (NOT from
``state_machine.check_gate``, which ADR 0058 keeps unchanged).

Fail-closed posture, per criterion:

  (a) No successful backtest run, or the most recent one is older than the
      policy freshness bound → UNMET.
  (b) The run from (a) lacks ``signal_count`` metadata (a pre-enforcement run)
      → UNMET (cannot evaluate; never assume). Otherwise SATISFIED when there
      were zero signals (regime strategy, vacuously satisfied) or the FK-joined
      explanation-row count covers the signals; UNMET when signals happened but
      the explanation rows are missing.
  (c) No synthetic fault-injection veto on record for the strategy, or the most
      recent one is older than the freshness bound → UNMET.

Every outcome carries an actionable ``detail`` message and an ``evidence`` dict
(run id, age in days, counts, synthetic-explanation id) that the orchestrator
records durably in ``gate_check_outcome.lifecycle_criteria``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from milodex.promotion.fault_injection import SYNTHETIC_FAULT_DECISION_TYPE
from milodex.promotion.policy import ACTIVE_PROMOTION_POLICY

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore

# Criterion identifiers, stable for the durable evidence record.
CRITERION_A = "a"
CRITERION_B = "b"
CRITERION_C = "c"


@dataclass(frozen=True)
class CriterionOutcome:
    """Verdict for one of the three R-PRM-004 criteria."""

    criterion: str
    label: str
    satisfied: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "label": self.label,
            "satisfied": self.satisfied,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class LifecycleCriteriaResult:
    """Aggregate verdict across all three criteria."""

    satisfied: bool
    outcomes: list[CriterionOutcome]
    evidence_max_age_days: int

    def failing(self) -> list[CriterionOutcome]:
        return [o for o in self.outcomes if not o.satisfied]

    def failure_messages(self) -> list[str]:
        return [f"Criterion ({o.criterion}) {o.label}: {o.detail}" for o in self.failing()]

    def as_evidence_dict(self) -> dict[str, Any]:
        """Serialize for ``gate_check_outcome.lifecycle_criteria`` (ADR 0058)."""
        return {
            "enforced": True,
            "satisfied": self.satisfied,
            "evidence_max_age_days": self.evidence_max_age_days,
            "criteria": [o.as_dict() for o in self.outcomes],
        }


def _age_days(recorded_at: datetime, now: datetime) -> float:
    return (now - recorded_at).total_seconds() / 86_400.0


def _evaluate_a_and_b(
    strategy_id: str,
    event_store: EventStore,
    *,
    now: datetime,
    max_age_days: int,
) -> tuple[CriterionOutcome, CriterionOutcome]:
    """Evaluate criteria (a) and (b), which share the same backtest run."""
    label_a = "a successful deterministic backtest run"
    label_b = "explanation records generated for every simulated signal"

    run = event_store.get_latest_successful_backtest_run(strategy_id)
    if run is None:
        detail = (
            f"No successful (status='completed') backtest run found for '{strategy_id}'. "
            "Run a deterministic backtest (e.g. `milodex backtest run <strategy-id> "
            "--start <date> --end <date>`) before promotion."
        )
        outcome_a = CriterionOutcome(CRITERION_A, label_a, False, detail)
        outcome_b = CriterionOutcome(
            CRITERION_B,
            label_b,
            False,
            "No successful backtest run to check explanation coverage against "
            "(criterion (a) unmet).",
        )
        return outcome_a, outcome_b

    age = _age_days(run.started_at, now)
    run_evidence = {
        "run_id": run.run_id,
        "backtest_run_db_id": run.id,
        "started_at": run.started_at.isoformat(),
        "age_days": round(age, 3),
    }
    if age > max_age_days:
        detail_a = (
            f"The most recent successful backtest run ({run.run_id}) is {age:.1f} days "
            f"old, exceeding the {max_age_days}-day freshness bound. Re-run the backtest "
            "to refresh the evidence."
        )
        outcome_a = CriterionOutcome(CRITERION_A, label_a, False, detail_a, run_evidence)
        outcome_b = CriterionOutcome(
            CRITERION_B,
            label_b,
            False,
            "The backing backtest run is stale (criterion (a) unmet); re-run to refresh.",
            run_evidence,
        )
        return outcome_a, outcome_b

    outcome_a = CriterionOutcome(
        CRITERION_A,
        label_a,
        True,
        f"Successful backtest run {run.run_id} is {age:.1f} days old "
        f"(within the {max_age_days}-day bound).",
        run_evidence,
    )

    # Criterion (b). The signal-count metadata is additive (ADR 0058 M4); a run
    # written before it existed cannot be evaluated and fails closed — never
    # assume the audit trail is complete.
    signal_count = run.metadata.get("signal_count")
    if signal_count is None:
        detail_b = (
            f"Backtest run {run.run_id} predates signal-count metadata and cannot be "
            "evaluated for explanation coverage. Re-run the backtest to generate "
            "signal-count metadata."
        )
        outcome_b = CriterionOutcome(CRITERION_B, label_b, False, detail_b, run_evidence)
        return outcome_a, outcome_b

    try:
        signal_count = int(signal_count)
    except (TypeError, ValueError):
        # Malformed metadata refuses cleanly (fail-closed), never crashes the
        # promotion attempt with a traceback.
        detail_b = (
            f"Backtest run {run.run_id} has malformed signal-count metadata "
            f"({signal_count!r}) and cannot be evaluated for explanation coverage. "
            "Re-run the backtest to regenerate it."
        )
        outcome_b = CriterionOutcome(CRITERION_B, label_b, False, detail_b, run_evidence)
        return outcome_a, outcome_b

    # INTEGER FK join (explanations.backtest_run_id = backtest_runs.id). The db
    # id, NOT the UUID run_id — the wrong-column join silently returns zero.
    explanation_count = event_store.count_explanations_for_backtest_run(run.id)
    b_evidence = {
        **run_evidence,
        "signal_count": signal_count,
        "explanation_count": explanation_count,
    }

    if signal_count == 0:
        outcome_b = CriterionOutcome(
            CRITERION_B,
            label_b,
            True,
            "Zero simulated signals in the backtest run — R-XC-008 coverage is "
            "vacuously satisfied (legitimate for the regime strategy).",
            b_evidence,
        )
    elif explanation_count >= signal_count:
        outcome_b = CriterionOutcome(
            CRITERION_B,
            label_b,
            True,
            f"{explanation_count} explanation rows cover {signal_count} simulated "
            "signal(s) for the run.",
            b_evidence,
        )
    else:
        outcome_b = CriterionOutcome(
            CRITERION_B,
            label_b,
            False,
            f"{signal_count} simulated signal(s) but only {explanation_count} explanation "
            "row(s) linked to the run — explanation records are missing. Re-run the "
            "backtest and confirm the audit trail before promotion.",
            b_evidence,
        )
    return outcome_a, outcome_b


def _evaluate_c(
    strategy_id: str,
    event_store: EventStore,
    *,
    now: datetime,
    max_age_days: int,
) -> CriterionOutcome:
    """Evaluate criterion (c): a fresh synthetic fault-injection veto on record."""
    label_c = "the risk layer having rejected at least one synthetic fault-injection trade"
    record = event_store.get_latest_synthetic_fault_injection_veto(strategy_id)
    if record is None:
        detail = (
            f"No synthetic fault-injection veto ('{SYNTHETIC_FAULT_DECISION_TYPE}') on "
            f"record for '{strategy_id}'. Run `milodex promotion fault-check {strategy_id}` "
            "to exercise the risk layer against a synthetic guardrail-violating trade."
        )
        return CriterionOutcome(CRITERION_C, label_c, False, detail)

    age = _age_days(record.recorded_at, now)
    evidence = {
        "explanation_id": record.id,
        "recorded_at": record.recorded_at.isoformat(),
        "reason_codes": list(record.reason_codes),
        "age_days": round(age, 3),
    }
    if age > max_age_days:
        detail = (
            f"The most recent synthetic fault-injection veto (explanation {record.id}) is "
            f"{age:.1f} days old, exceeding the {max_age_days}-day freshness bound. Re-run "
            f"`milodex promotion fault-check {strategy_id}`."
        )
        return CriterionOutcome(CRITERION_C, label_c, False, detail, evidence)

    return CriterionOutcome(
        CRITERION_C,
        label_c,
        True,
        f"Synthetic fault-injection veto (explanation {record.id}) is {age:.1f} days old "
        f"(within the {max_age_days}-day bound); reason codes {list(record.reason_codes)}.",
        evidence,
    )


def evaluate_lifecycle_criteria(
    strategy_id: str,
    event_store: EventStore,
    *,
    now: datetime | None = None,
) -> LifecycleCriteriaResult:
    """Evaluate all three R-PRM-004 lifecycle criteria against the event store.

    Fail-closed: any criterion that cannot be positively verified is UNMET. The
    freshness bound comes from ``ACTIVE_PROMOTION_POLICY.lifecycle_gate``.
    """
    now = now or datetime.now(tz=UTC)
    max_age_days = ACTIVE_PROMOTION_POLICY.lifecycle_gate.evidence_max_age_days

    outcome_a, outcome_b = _evaluate_a_and_b(
        strategy_id, event_store, now=now, max_age_days=max_age_days
    )
    outcome_c = _evaluate_c(strategy_id, event_store, now=now, max_age_days=max_age_days)

    outcomes = [outcome_a, outcome_b, outcome_c]
    return LifecycleCriteriaResult(
        satisfied=all(o.satisfied for o in outcomes),
        outcomes=outcomes,
        evidence_max_age_days=max_age_days,
    )

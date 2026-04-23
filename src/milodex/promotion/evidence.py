"""Evidence package assembled at promotion time.

See ``docs/PROMOTION_GOVERNANCE.md`` §"Evidence Package: backtest → paper" for
the governance contract. Machine-derivable fields come from the durable event
store; operator-authored fields (``recommendation``, ``known_risks``) are
passed in by the caller and refused early if blank (R-PRM-008).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore

_EVIDENCE_SCHEMA_VERSION = 1

_PAPER_TRADE_FROM_STAGES = frozenset({"paper"})


@dataclass(frozen=True)
class EvidencePackage:
    """Structured evidence bundled with a promotion row.

    Serialized to the ``promotions.evidence_json`` column. The ``schema_version``
    key on the serialized form lets readers handle future shape evolution
    without a schema migration per field (see slice-2 plan AD-2).
    """

    strategy_id: str
    from_stage: str
    to_stage: str
    manifest_hash: str
    backtest_run_id: str | None
    backtest_run_started_at: str | None
    paper_trade_count: int | None
    paper_rejection_count: int | None
    kill_switch_trip_count: int | None
    metrics_snapshot: dict[str, float | int | None]
    recommendation: str
    known_risks: list[str]
    promotion_type: str
    gate_check_outcome: dict[str, Any]
    assembled_at: str
    schema_version: int = field(default=_EVIDENCE_SCHEMA_VERSION)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-ready representation (pure dict, no dataclass types)."""
        return asdict(self)


def assemble_evidence_package(
    *,
    strategy_id: str,
    from_stage: str,
    to_stage: str,
    manifest_hash: str,
    backtest_run_id: str | None,
    recommendation: str,
    known_risks: list[str],
    promotion_type: str,
    gate_check_outcome: dict[str, Any],
    metrics_snapshot: dict[str, float | int | None],
    event_store: EventStore,
    now: datetime | None = None,
) -> EvidencePackage:
    """Derive machine-assemblable fields, validate operator inputs, return package.

    Raises ``ValueError`` if ``recommendation`` is blank or ``known_risks`` is
    empty / contains blank entries. Derivation queries (``paper_trade_count``,
    ``paper_rejection_count``, ``kill_switch_trip_count``,
    ``backtest_run_started_at``) read the event store directly — the caller
    cannot fudge them.
    """
    _require_nonblank(recommendation, "recommendation")
    _require_risks(known_risks)

    assembled_at = (now or datetime.now(tz=UTC)).isoformat()

    backtest_run_started_at: str | None = None
    if backtest_run_id is not None:
        run = event_store.get_backtest_run(backtest_run_id)
        if run is not None:
            backtest_run_started_at = run.started_at.isoformat()

    paper_trade_count: int | None = None
    paper_rejection_count: int | None = None
    if from_stage in _PAPER_TRADE_FROM_STAGES:
        paper_trade_count, paper_rejection_count = _derive_paper_counts(event_store, strategy_id)

    kill_switch_trip_count = sum(
        1 for event in event_store.list_kill_switch_events() if event.event_type == "activated"
    )

    return EvidencePackage(
        strategy_id=strategy_id,
        from_stage=from_stage,
        to_stage=to_stage,
        manifest_hash=manifest_hash,
        backtest_run_id=backtest_run_id,
        backtest_run_started_at=backtest_run_started_at,
        paper_trade_count=paper_trade_count,
        paper_rejection_count=paper_rejection_count,
        kill_switch_trip_count=kill_switch_trip_count,
        metrics_snapshot=dict(metrics_snapshot),
        recommendation=recommendation.strip(),
        known_risks=[risk.strip() for risk in known_risks],
        promotion_type=promotion_type,
        gate_check_outcome=dict(gate_check_outcome),
        assembled_at=assembled_at,
    )


def _require_nonblank(value: str, field_name: str) -> None:
    if value is None or not value.strip():
        msg = f"Evidence field '{field_name}' is required and must be non-blank."
        raise ValueError(msg)


def _require_risks(risks: list[str]) -> None:
    if not risks:
        msg = (
            "Evidence field 'known_risks' is required — pass at least one risk. "
            "An empty list silently satisfies a governance requirement that "
            "exists specifically to force operators to name their concerns."
        )
        raise ValueError(msg)
    for risk in risks:
        if risk is None or not risk.strip():
            msg = "Evidence field 'known_risks' contains a blank entry."
            raise ValueError(msg)


def _derive_paper_counts(event_store: EventStore, strategy_id: str) -> tuple[int, int]:
    """Count paper-stage trades and rejections for ``strategy_id``."""
    paper_trade_count = sum(
        1
        for trade in event_store.list_trades()
        if trade.source == "paper" and trade.strategy_name == strategy_id
    )
    paper_rejection_count = sum(
        1
        for explanation in event_store.list_explanations()
        if explanation.strategy_name == strategy_id
        and explanation.strategy_stage == "paper"
        and explanation.risk_allowed is False
    )
    return paper_trade_count, paper_rejection_count

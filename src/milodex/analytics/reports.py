"""Trust report assembly.

Composes existing analytics outputs (``compute_metrics``,
``compute_benchmark``, portfolio snapshot history) into a single
:class:`TrustReport` view. No new math lives here — a trust report is
a read/join, not a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from milodex.analytics.benchmark import compute_benchmark
from milodex.analytics.metrics import PerformanceMetrics

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore, PortfolioSnapshotEvent
    from milodex.data.provider import DataProvider


@dataclass(frozen=True)
class SnapshotSummary:
    """Roll-up of the snapshot history used to build a trust report."""

    snapshot_count: int
    first_recorded_at: str | None
    last_recorded_at: str | None
    first_equity: float | None
    last_equity: float | None


@dataclass(frozen=True)
class DecisionSummary:
    """One row in the ``recent_decisions`` trust-report section."""

    recorded_at: str
    rule: str
    narrative: str


@dataclass(frozen=True)
class TrustReport:
    """Composed view of strategy metrics, benchmark delta, and snapshot history."""

    run_id: str
    strategy_id: str
    start_date: date
    end_date: date
    metrics: PerformanceMetrics
    benchmark: PerformanceMetrics | None
    total_return_vs_benchmark_pct: float | None
    max_drawdown_vs_benchmark_pct: float | None
    confidence_label: str
    snapshot_summary: SnapshotSummary
    open_questions: list[str] = field(default_factory=list)
    recent_decisions: list[DecisionSummary] = field(default_factory=list)


def assemble_trust_report(
    *,
    metrics: PerformanceMetrics,
    event_store: EventStore,
    data_provider: DataProvider | None = None,
    include_benchmark: bool = True,
) -> TrustReport:
    """Assemble a :class:`TrustReport` from pre-computed strategy metrics.

    The caller supplies ``metrics`` (typically built via
    ``analytics.commands.metrics_for_run``) — this keeps the report free
    of trade-loading logic and avoids duplicating the existing helper.

    Args:
        metrics: Strategy :class:`PerformanceMetrics` for the run.
        event_store: Event store instance for snapshot lookups.
        data_provider: Optional data provider. Required when
            ``include_benchmark`` is True; otherwise the benchmark slot
            is left empty.
        include_benchmark: When False (or when no provider is supplied),
            the SPY benchmark is skipped and benchmark-delta fields
            return ``None``.
    """
    benchmark: PerformanceMetrics | None = None
    total_return_delta: float | None = None
    max_dd_delta: float | None = None

    if include_benchmark and data_provider is not None:
        try:
            benchmark = compute_benchmark(
                start_date=metrics.start_date,
                end_date=metrics.end_date,
                initial_equity=metrics.initial_equity,
                data_provider=data_provider,
            )
        except ValueError:
            benchmark = None

    if benchmark is not None:
        total_return_delta = metrics.total_return_pct - benchmark.total_return_pct
        max_dd_delta = metrics.max_drawdown_pct - benchmark.max_drawdown_pct

    snapshots = event_store.list_portfolio_snapshots_for_strategy(metrics.strategy_id)
    snapshot_summary = _summarize_snapshots(snapshots)

    open_questions = _derive_open_questions(metrics, benchmark, snapshot_summary)
    recent_decisions = _recent_decisions(event_store, metrics.strategy_id)

    return TrustReport(
        run_id=metrics.run_id,
        strategy_id=metrics.strategy_id,
        start_date=metrics.start_date,
        end_date=metrics.end_date,
        metrics=metrics,
        benchmark=benchmark,
        total_return_vs_benchmark_pct=total_return_delta,
        max_drawdown_vs_benchmark_pct=max_dd_delta,
        confidence_label=metrics.confidence_label,
        snapshot_summary=snapshot_summary,
        open_questions=open_questions,
        recent_decisions=recent_decisions,
    )


def _summarize_snapshots(snapshots: list[PortfolioSnapshotEvent]) -> SnapshotSummary:
    if not snapshots:
        return SnapshotSummary(
            snapshot_count=0,
            first_recorded_at=None,
            last_recorded_at=None,
            first_equity=None,
            last_equity=None,
        )
    return SnapshotSummary(
        snapshot_count=len(snapshots),
        first_recorded_at=snapshots[0].recorded_at.isoformat(),
        last_recorded_at=snapshots[-1].recorded_at.isoformat(),
        first_equity=snapshots[0].equity,
        last_equity=snapshots[-1].equity,
    )


def _recent_decisions(
    event_store: EventStore, strategy_id: str, limit: int = 10
) -> list[DecisionSummary]:
    """Return the most recent ``limit`` decisions for ``strategy_id`` that carry reasoning.

    Rows missing ``context.reasoning`` are skipped — legacy rows from
    before R-XC-008 have no narrative to surface.
    """
    rows = [row for row in event_store.list_explanations() if row.strategy_name == strategy_id]
    summaries: list[DecisionSummary] = []
    for row in reversed(rows):
        reasoning = row.context.get("reasoning") if isinstance(row.context, dict) else None
        if not isinstance(reasoning, dict):
            continue
        summaries.append(
            DecisionSummary(
                recorded_at=row.recorded_at.isoformat(),
                rule=str(reasoning.get("rule", "")),
                narrative=str(reasoning.get("narrative", "")),
            )
        )
        if len(summaries) >= limit:
            break
    return list(reversed(summaries))


def _derive_open_questions(
    metrics: PerformanceMetrics,
    benchmark: PerformanceMetrics | None,
    snapshot_summary: SnapshotSummary,
) -> list[str]:
    questions: list[str] = []
    if metrics.confidence_label == "insufficient_data":
        questions.append(
            f"Trade count {metrics.trade_count} is below the 30-trade floor; "
            "metrics are directional only."
        )
    if benchmark is None:
        questions.append("Benchmark comparison unavailable — SPY bars could not be loaded.")
    if snapshot_summary.snapshot_count == 0:
        questions.append(
            "No portfolio snapshots recorded for this strategy — equity "
            "trajectory is reconstructed from the trade ledger only."
        )
    return questions

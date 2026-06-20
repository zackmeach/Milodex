"""Intraday evidence-report assembler (Tier-3 G-PR1).

A RESEARCH/reporting layer that sits BELOW the promotion/risk seam. It joins a
walk-forward ``BatchResult`` of one intraday candidate-per-symbol against its
matched null baselines into a single :class:`IntradayEvidenceReport`, folds in
an advisory (non-gating) intraday-readiness scan and a reproducibility manifest,
and writes exactly one append-only experiment-registry row.

It is NOT a gate. The ``verdict`` and the decisive-loss predicate are *reported*
— a string and a block in ``evidence_json`` — never an early-return or a raise
that stops a promotion. The only thing this module decides is the registry
row's ``terminal_status`` (research bookkeeping), under the policy fixed in the
G-PR1 design.

Because the underlying bars are IEX (a single-venue, non-consolidated feed), the
report is permanently exploratory: ``iex_exploratory`` is always ``True`` and
``durable`` always ``False`` (ADR 0017). A decisive *win* under IEX is therefore
never durable here — it can only ever read ``inconclusive``; only a decisive
*loss* is trustworthy enough to ``reject``. These markers are load-bearing —
serialized on every report and the writer asserts the full coherent set
(``durable is False`` AND ``iex_exploratory is True`` AND ``feed == "iex"``)
before any ``rejected`` row is written.

This module does NOT reuse :class:`milodex.promotion.evidence.EvidencePackage`:
that structure is promotion-staged, frozen, and demands operator-authored inputs
(``recommendation``, ``known_risks``) that a research screen does not have.
"""

from __future__ import annotations

import dataclasses
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from milodex.backtesting.run_manifest import (
    BacktestRunManifestInput,
    build_backtest_run_manifest,
)
from milodex.data import Timeframe
from milodex.strategies.loader import (
    StrategyLoader,
    compute_config_hash,
    load_strategy_config,
    resolve_config_path,
    resolve_universe_ref,
)

if TYPE_CHECKING:
    from milodex.backtesting.walk_forward_batch import BatchResult, BatchRow

# Baseline kinds, in canonical order. ``no_trade`` is SPY-only in the bank (a
# single flat row), so it is simply absent from a non-SPY symbol's cell dict.
_BASELINE_KINDS: tuple[str, ...] = (
    "unconditional_intraday_long",
    "time_of_day_null",
    "random_matched_exposure.intraday",
    "no_trade",
)

# The three nulls that participate in the decisive-loss predicate (no_trade is
# a degenerate flat baseline, excluded from the "below all nulls" test).
_DECISIVE_NULL_KINDS: tuple[str, ...] = (
    "unconditional_intraday_long",
    "time_of_day_null",
    "random_matched_exposure.intraday",
)

# Decisive-loss predicate thresholds (G-PR1 decided policy).
_DECISIVE_LOSS_MIN_SYMBOLS = 14
_DECISIVE_LOSS_MIN_MARGIN = 2.0

# A verdict reads "insufficient_data" below this many comparable symbols.
_MIN_COMPARABLE_FOR_VERDICT = 5

# Readiness scan timeframe (the intraday evidence lane runs on 5-minute bars).
_READINESS_TIMEFRAME_MINUTES = 5

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BaselineCell:
    """One candidate-vs-one-baseline comparison for a single symbol."""

    baseline_strategy_id: str
    kind: str
    sharpe: float | None
    total_return_pct: float | None
    round_trips: int | None
    delta_sharpe: float | None
    delta_total_return_pct: float | None
    error: str | None


@dataclass(frozen=True)
class SymbolDelta:
    """The candidate's per-symbol metrics plus its baseline comparison cells."""

    symbol: str
    candidate_strategy_id: str
    candidate_sharpe: float | None
    candidate_total_return_pct: float | None
    candidate_round_trips: int | None
    baselines: dict[str, BaselineCell]
    coverage_pct: float | None
    status: str  # "ok" | "candidate_error" | "no_baselines"


@dataclass(frozen=True)
class IntradayEvidenceReport:
    """A joined candidate-vs-baseline report for one intraday experiment.

    Emitted as a sibling of :class:`EvidencePackage` — written into the
    experiment-registry ``evidence_json``, never frozen as a promotion artifact.
    """

    experiment_id: str
    candidate_family: str
    candidate_template: str
    universe_ref: str
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    per_symbol: tuple[SymbolDelta, ...]
    aggregate: dict[str, Any]
    readiness_summary: dict[str, Any]
    run_ids: dict[str, str | None]
    run_manifest: dict[str, Any]
    iex_exploratory: bool = True
    durable: bool = False
    feed: str = "iex"
    survivorship_corrected: bool = False
    schema_version: int = _SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        """Return a fully JSON-serializable representation.

        ``iex_exploratory`` and ``durable`` are ALWAYS present (load-bearing,
        ADR 0017); dates are isoformatted.
        """
        return {
            "experiment_id": self.experiment_id,
            "candidate_family": self.candidate_family,
            "candidate_template": self.candidate_template,
            "universe_ref": self.universe_ref,
            "symbols": list(self.symbols),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "per_symbol": [_symbol_delta_as_dict(d) for d in self.per_symbol],
            "aggregate": self.aggregate,
            "readiness_summary": self.readiness_summary,
            "run_ids": dict(self.run_ids),
            "run_manifest": self.run_manifest,
            "iex_exploratory": self.iex_exploratory,
            "durable": self.durable,
            "feed": self.feed,
            "survivorship_corrected": self.survivorship_corrected,
            "schema_version": self.schema_version,
        }


def _symbol_delta_as_dict(delta: SymbolDelta) -> dict[str, Any]:
    return {
        "symbol": delta.symbol,
        "candidate_strategy_id": delta.candidate_strategy_id,
        "candidate_sharpe": delta.candidate_sharpe,
        "candidate_total_return_pct": delta.candidate_total_return_pct,
        "candidate_round_trips": delta.candidate_round_trips,
        "coverage_pct": delta.coverage_pct,
        "status": delta.status,
        "baselines": {kind: dataclasses.asdict(cell) for kind, cell in delta.baselines.items()},
    }


def assemble_intraday_evidence(
    *,
    candidate_family: str,
    candidate_template: str,
    universe_ref: str,
    start_date: date,
    end_date: date,
    experiment_id: str,
    hypothesis: str,
    ctx: Any,
    batch_result: BatchResult | None = None,
    config_dir: Path | None = None,
    feed_label: str = "iex",
) -> tuple[IntradayEvidenceReport, int]:
    """Join a walk-forward batch into one evidence report and write one registry row.

    Returns ``(report, experiment_registry_row_id)``. The ``batch_result`` is
    consumed verbatim when supplied (the default, pure-join behavior); only when
    it is ``None`` does this build the candidate+baseline roster and call
    ``run_batch`` itself (fallback).
    """
    resolved_config_dir = config_dir or ctx.config_dir

    # Eligibility-guarded, sorted, UPPERCASE symbol set. resolve_universe_ref
    # scans the manifests next to a config path — a dummy path under the config
    # dir gives it the directory to scan (mirrors data._run_readiness).
    dummy_config_path = Path(resolved_config_dir) / "_dummy.yaml"
    symbols = resolve_universe_ref(universe_ref, dummy_config_path)

    candidate_spy_id = f"{candidate_family}.{candidate_template}.spy.v1"
    candidate_spy_config_path = resolve_config_path(candidate_spy_id, Path(resolved_config_dir))
    candidate_spy_config = load_strategy_config(candidate_spy_config_path)
    version = candidate_spy_config.version

    if batch_result is None:
        batch_result = _run_fallback_batch(
            symbols=symbols,
            candidate_family=candidate_family,
            candidate_template=candidate_template,
            version=version,
            start_date=start_date,
            end_date=end_date,
            ctx=ctx,
        )

    rows_by_id = {row.strategy_id: row for row in batch_result.rows}

    # Advisory, NON-GATING readiness scan over the same symbols (5-minute bars).
    readiness_summary, coverage_by_symbol = _scan_readiness(
        ctx=ctx,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        feed_label=feed_label,
    )

    per_symbol: list[SymbolDelta] = []
    run_ids: dict[str, str | None] = {}
    for sym in symbols:
        candidate_id = f"{candidate_family}.{candidate_template}.{sym.lower()}.v{version}"
        candidate_row = rows_by_id.get(candidate_id)
        delta = _build_symbol_delta(
            symbol=sym,
            candidate_id=candidate_id,
            candidate_row=candidate_row,
            rows_by_id=rows_by_id,
            version=version,
            coverage_pct=coverage_by_symbol.get(sym),
        )
        per_symbol.append(delta)
        run_ids[candidate_id] = candidate_row.run_id if candidate_row is not None else None

    aggregate = _build_aggregate(per_symbol)

    run_manifest = _build_run_manifest(
        ctx=ctx,
        loaded_spy=StrategyLoader().load(candidate_spy_config_path),
        start_date=start_date,
        end_date=end_date,
        candidate_spy_config=candidate_spy_config,
    )

    survivorship = _resolve_survivorship(universe_ref, dummy_config_path)

    report = IntradayEvidenceReport(
        experiment_id=experiment_id,
        candidate_family=candidate_family,
        candidate_template=candidate_template,
        universe_ref=universe_ref,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        per_symbol=tuple(per_symbol),
        aggregate=aggregate,
        readiness_summary=readiness_summary,
        run_ids=run_ids,
        run_manifest=run_manifest,
        feed="iex",
        survivorship_corrected=survivorship,
    )

    row_id = _write_registry_row(
        ctx=ctx,
        report=report,
        experiment_id=experiment_id,
        hypothesis=hypothesis,
        candidate_spy_id=candidate_spy_id,
        candidate_spy_config_path=candidate_spy_config_path,
    )
    return report, row_id


# ---------------------------------------------------------------------------
# Per-symbol join
# ---------------------------------------------------------------------------


def _round_trips(trade_count: int | None) -> int | None:
    # trade_count is buy+sell fills ~= 2x round-trips. Never coerce missing to 0.
    return None if trade_count is None else trade_count // 2


def _metric_missing(row: BatchRow | None) -> bool:
    """A row contributes no usable metric if absent, errored, or Sharpe is None."""
    return row is None or row.error is not None or row.oos_sharpe is None


def _build_symbol_delta(
    *,
    symbol: str,
    candidate_id: str,
    candidate_row: BatchRow | None,
    rows_by_id: dict[str, BatchRow],
    version: int,
    coverage_pct: float | None,
) -> SymbolDelta:
    candidate_errored = _metric_missing(candidate_row)

    if candidate_row is None or candidate_errored:
        cand_sharpe = None
        cand_return = None
        cand_round_trips = None
    else:
        cand_sharpe = candidate_row.oos_sharpe
        cand_return = candidate_row.oos_total_return_pct
        cand_round_trips = _round_trips(candidate_row.trade_count)

    baselines: dict[str, BaselineCell] = {}
    for kind in _BASELINE_KINDS:
        baseline_id = f"benchmark.{kind}.{symbol.lower()}.v1"
        baseline_row = rows_by_id.get(baseline_id)
        if baseline_row is None:
            # Absent baseline (e.g. no_trade on a non-SPY symbol) is simply not
            # in the cell dict — never synthesize a null row.
            continue
        baselines[kind] = _build_baseline_cell(
            baseline_id=baseline_id,
            kind=kind,
            baseline_row=baseline_row,
            candidate_errored=candidate_errored,
            cand_sharpe=cand_sharpe,
            cand_return=cand_return,
        )

    if candidate_errored:
        status = "candidate_error"
    elif not baselines:
        status = "no_baselines"
    else:
        status = "ok"

    return SymbolDelta(
        symbol=symbol,
        candidate_strategy_id=candidate_id,
        candidate_sharpe=cand_sharpe,
        candidate_total_return_pct=cand_return,
        candidate_round_trips=cand_round_trips,
        baselines=baselines,
        coverage_pct=coverage_pct,
        status=status,
    )


def _build_baseline_cell(
    *,
    baseline_id: str,
    kind: str,
    baseline_row: BatchRow,
    candidate_errored: bool,
    cand_sharpe: float | None,
    cand_return: float | None,
) -> BaselineCell:
    baseline_missing = _metric_missing(baseline_row)
    if baseline_missing:
        b_sharpe = None
        b_return = None
        b_round_trips = None
    else:
        b_sharpe = baseline_row.oos_sharpe
        b_return = baseline_row.oos_total_return_pct
        b_round_trips = _round_trips(baseline_row.trade_count)

    # delta_X is candidate.X - baseline.X iff BOTH sides non-None. A candidate
    # error nils every delta; a baseline error nils only this cell's delta.
    if candidate_errored or baseline_missing:
        delta_sharpe = None
        delta_return = None
    else:
        delta_sharpe = None if (cand_sharpe is None or b_sharpe is None) else cand_sharpe - b_sharpe
        delta_return = None if (cand_return is None or b_return is None) else cand_return - b_return

    return BaselineCell(
        baseline_strategy_id=baseline_id,
        kind=kind,
        sharpe=b_sharpe,
        total_return_pct=b_return,
        round_trips=b_round_trips,
        delta_sharpe=delta_sharpe,
        delta_total_return_pct=delta_return,
        error=baseline_row.error,
    )


# ---------------------------------------------------------------------------
# Aggregate + advisory verdict
# ---------------------------------------------------------------------------


def _build_aggregate(per_symbol: list[SymbolDelta]) -> dict[str, Any]:
    per_kind: dict[str, dict[str, Any]] = {}
    for kind in _BASELINE_KINDS:
        deltas_sharpe: list[float] = []
        deltas_return: list[float] = []
        n_beats = 0
        for d in per_symbol:
            cell = d.baselines.get(kind)
            if cell is None or cell.delta_sharpe is None:
                continue
            deltas_sharpe.append(cell.delta_sharpe)
            if cell.delta_total_return_pct is not None:
                deltas_return.append(cell.delta_total_return_pct)
            if cell.delta_sharpe > 0:
                n_beats += 1
        per_kind[kind] = {
            "n_symbols_compared": len(deltas_sharpe),
            "mean_delta_sharpe": (statistics.fmean(deltas_sharpe) if deltas_sharpe else None),
            "median_delta_sharpe": (statistics.median(deltas_sharpe) if deltas_sharpe else None),
            "n_candidate_beats": n_beats,
            "mean_delta_total_return_pct": (
                statistics.fmean(deltas_return) if deltas_return else None
            ),
        }

    n_candidate_errors = sum(1 for d in per_symbol if d.status == "candidate_error")
    verdict = _derive_verdict(per_kind)
    return {
        "per_baseline_kind": per_kind,
        "n_symbols_total": len(per_symbol),
        "n_candidate_errors": n_candidate_errors,
        "verdict": verdict,
    }


def _derive_verdict(per_kind: dict[str, dict[str, Any]]) -> str:
    """Advisory verdict vs the unconditional-long floor. A STRING, never a gate.

    The unconditional-intraday-long baseline is the honest floor an intraday
    long-only edge must clear. ``insufficient_data`` below the comparable-symbol
    floor; otherwise read mean ΔSharpe and the beats-fraction.
    """
    floor = per_kind.get("unconditional_intraday_long", {})
    n = floor.get("n_symbols_compared", 0)
    if n < _MIN_COMPARABLE_FOR_VERDICT:
        return "insufficient_data"
    mean_delta = floor.get("mean_delta_sharpe")
    n_beats = floor.get("n_candidate_beats", 0)
    if mean_delta is not None and mean_delta > 0 and n_beats == n:
        return "candidate_beats_all_baselines"
    if mean_delta is not None and mean_delta < 0 and n_beats == 0:
        return "candidate_underperforms"
    return "mixed"


# ---------------------------------------------------------------------------
# Readiness (advisory, non-gating)
# ---------------------------------------------------------------------------


def _scan_readiness(
    *,
    ctx: Any,
    symbols: tuple[str, ...],
    start_date: date,
    end_date: date,
    feed_label: str,
) -> tuple[dict[str, Any], dict[str, float | None]]:
    """Run the intraday-readiness scan; return (summary, per-symbol coverage).

    NON-GATING: a low-coverage or zero-bar symbol still gets a full delta. The
    coverage value is folded into the symbol's cell and the full report is
    summarized; nothing here removes a symbol from the comparison.
    """
    from milodex.data.intraday_readiness import scan_intraday_readiness

    provider = ctx.data_provider_factory()
    bars_by_symbol = provider.get_bars(list(symbols), Timeframe.MINUTE_5, start_date, end_date)
    report = scan_intraday_readiness(
        bars_by_symbol,
        timeframe_minutes=_READINESS_TIMEFRAME_MINUTES,
        requested_start=start_date,
        requested_end=end_date,
        feed_label=feed_label,
    )
    data = report.to_dict()
    # A symbol with zero expected bars reports coverage_pct as None (no signal),
    # not 0.0 — distinct from a symbol that traded but covered 0% of its grid.
    coverage_by_symbol: dict[str, float | None] = {}
    for sr in data["per_symbol"]:
        coverage_by_symbol[sr["symbol"]] = sr["coverage_pct"] if sr["expected_bars"] else None
    summary = {
        "status": data["status"],
        "warning_count": data["warning_count"],
        "issue_codes": data["issue_codes"],
        "per_symbol": data["per_symbol"],
    }
    return summary, coverage_by_symbol


# ---------------------------------------------------------------------------
# run_manifest (one manifest for the candidate SPY base config)
# ---------------------------------------------------------------------------


def _build_run_manifest(
    *,
    ctx: Any,
    loaded_spy: Any,
    start_date: date,
    end_date: date,
    candidate_spy_config: Any,
) -> dict[str, Any]:
    provider = ctx.data_provider_factory()
    slippage_pct = float(candidate_spy_config.backtest.get("slippage_pct", 0.0))
    commission = float(candidate_spy_config.backtest.get("commission_per_trade", 0.0))
    manifest_input = BacktestRunManifestInput(
        loaded=loaded_spy,
        data_provider=provider,
        requested_start=start_date,
        requested_end=end_date,
        warmup_start=start_date,
        risk_policy="research_screen",
        slippage_pct=slippage_pct,
        commission_per_trade=commission,
        initial_equity=100_000.0,
        data_quality=None,
        coverage_threshold=0.0,
    )
    return build_backtest_run_manifest(manifest_input)


def _resolve_survivorship(universe_ref: str, dummy_config_path: Path) -> bool:
    from milodex.strategies.loader import resolve_universe_survivorship_corrected

    return resolve_universe_survivorship_corrected(universe_ref, dummy_config_path)


# ---------------------------------------------------------------------------
# The single registry write + terminal_status policy + writer invariant
# ---------------------------------------------------------------------------


def _decisive_loss_predicate(per_symbol: list[SymbolDelta]) -> dict[str, Any]:
    """Evaluate the decisive-loss predicate over symbols where all four are present.

    Fires when the candidate's Sharpe is below ALL THREE nulls on ≥14 of the 17
    symbols where the candidate + all three nulls are present, AND on those
    symbols the margin below the *strongest* (highest) null is ≥ 2.0 Sharpe.
    """
    symbols_below_all = 0
    n_evaluated = 0
    margins: list[float] = []
    for d in per_symbol:
        if d.candidate_sharpe is None:
            continue
        null_sharpes = []
        for kind in _DECISIVE_NULL_KINDS:
            cell = d.baselines.get(kind)
            if cell is None or cell.sharpe is None:
                null_sharpes = []
                break
            null_sharpes.append(cell.sharpe)
        if not null_sharpes:
            continue  # not all three nulls present for this symbol
        n_evaluated += 1
        strongest = max(null_sharpes)
        if d.candidate_sharpe < min(null_sharpes):
            symbols_below_all += 1
            margins.append(strongest - d.candidate_sharpe)

    min_margin = min(margins) if margins else None
    passed = (
        symbols_below_all >= _DECISIVE_LOSS_MIN_SYMBOLS
        and min_margin is not None
        and min_margin >= _DECISIVE_LOSS_MIN_MARGIN
    )
    return {
        "passed": passed,
        "symbols_below_all_nulls": symbols_below_all,
        "n_symbols_evaluated": n_evaluated,
        "min_margin_sharpe": min_margin,
        "threshold_symbols": _DECISIVE_LOSS_MIN_SYMBOLS,
        "threshold_margin": _DECISIVE_LOSS_MIN_MARGIN,
    }


def _derive_terminal_status(report: IntradayEvidenceReport, predicate: dict[str, Any]) -> str:
    """Research bookkeeping only — NOT a promotion gate.

    - "failed" when nothing comparable (all kinds 0 compared) or every candidate
      cell errored.
    - "rejected" when the decisive-loss predicate fires.
    - "inconclusive" otherwise (this includes a decisive WIN — IEX can overstate
      an edge, so a win is never durable here).
    """
    per_kind = report.aggregate["per_baseline_kind"]
    n_compared_total = sum(k["n_symbols_compared"] for k in per_kind.values())
    n_total = report.aggregate["n_symbols_total"]
    n_errors = report.aggregate["n_candidate_errors"]
    if n_compared_total == 0 or (n_total > 0 and n_errors == n_total):
        return "failed"
    if predicate["passed"]:
        return "rejected"
    return "inconclusive"


def _rationale(report: IntradayEvidenceReport, terminal_status: str) -> str:
    verdict = report.aggregate["verdict"]
    floor = report.aggregate["per_baseline_kind"].get("unconditional_intraday_long", {})
    median = floor.get("median_delta_sharpe")
    n = floor.get("n_symbols_compared", 0)
    median_txt = f"{median:+.2f}" if median is not None else "n/a"
    return (
        f"IEX-exploratory / non-durable (ADR 0017). {verdict}: median ΔSharpe "
        f"{median_txt} vs unconditional over {n} symbols. "
        f"terminal_status={terminal_status}; revisit under SIP."
    )


def _write_registry_row(
    *,
    ctx: Any,
    report: IntradayEvidenceReport,
    experiment_id: str,
    hypothesis: str,
    candidate_spy_id: str,
    candidate_spy_config_path: Path,
) -> int:
    from milodex.core.event_store import ExperimentEvent

    predicate = _decisive_loss_predicate(list(report.per_symbol))
    terminal_status = _derive_terminal_status(report, predicate)

    evidence_json = report.as_dict()
    # Mandatory markers (load-bearing). feed/durable/iex are already serialized
    # by as_dict; the predicate block is folded in here.
    evidence_json["decisive_loss_predicate"] = predicate

    revisitable = True  # IEX is non-durable — always revisitable.

    # Writer invariant: a rejected IEX row MUST carry the full coherent
    # non-durable marker set on the report object itself (not just the
    # hardcoded locals above) — so a poisoned report (e.g. via
    # dataclasses.replace(report, durable=True)) is caught here, not
    # silently written.
    if terminal_status == "rejected" and not (
        report.durable is False and report.iex_exploratory is True and report.feed == "iex"
    ):
        failing = []
        if report.durable is not False:
            failing.append(f"durable={report.durable!r} (want False)")
        if report.iex_exploratory is not True:
            failing.append(f"iex_exploratory={report.iex_exploratory!r} (want True)")
        if report.feed != "iex":
            failing.append(f"feed={report.feed!r} (want 'iex')")
        msg = (
            "Refusing to write a 'rejected' IEX row: report fails non-durable "
            f"marker coherence check — {'; '.join(failing)} (ADR 0017)."
        )
        raise AssertionError(msg)

    event = ExperimentEvent(
        experiment_id=experiment_id,
        hypothesis=hypothesis,
        stage_reached="backtest",
        terminal_status=terminal_status,
        rationale=_rationale(report, terminal_status),
        recorded_at=datetime.now(tz=UTC),
        strategy_id=candidate_spy_id,
        config_hash=compute_config_hash(candidate_spy_config_path),
        evidence_json=evidence_json,
        lessons=None,
        revisitable=revisitable,
    )
    return ctx.get_event_store().append_experiment(event)


# ---------------------------------------------------------------------------
# Fallback batch (only when batch_result is None)
# ---------------------------------------------------------------------------


def _run_fallback_batch(
    *,
    symbols: tuple[str, ...],
    candidate_family: str,
    candidate_template: str,
    version: int,
    start_date: date,
    end_date: date,
    ctx: Any,
) -> BatchResult:
    """Build the candidate+baseline roster and run a walk-forward batch.

    Only reached when no ``batch_result`` is supplied — the default path
    consumes a supplied batch verbatim. Baseline ids follow the same
    family/template convention used by the join.
    """
    from milodex.backtesting.walk_forward_batch import run_batch

    strategy_ids: list[str] = []
    for sym in symbols:
        strategy_ids.append(f"{candidate_family}.{candidate_template}.{sym.lower()}.v{version}")
        for kind in _BASELINE_KINDS:
            if kind == "no_trade" and sym != "SPY":
                continue
            strategy_ids.append(f"benchmark.{kind}.{sym.lower()}.v1")

    return run_batch(
        strategy_ids=strategy_ids,
        start_date=start_date,
        end_date=end_date,
        ctx=ctx,
    )


__all__ = [
    "BaselineCell",
    "IntradayEvidenceReport",
    "SymbolDelta",
    "assemble_intraday_evidence",
]

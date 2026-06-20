"""Tests for the intraday evidence-report assembler (Tier-3 G-PR1).

This is a RESEARCH/reporting layer (below the promotion/risk seam): it joins a
supplied ``BatchResult`` of candidate-vs-baseline walk-forward rows into one
``IntradayEvidenceReport`` and writes a single append-only experiment-registry
row. It is NOT a gate — the verdict and decisive-loss predicate are reported
strings/blocks, never an early-return or raise.

The IEX caveat (ADR 0017) is load-bearing: every serialized report carries
``iex_exploratory=True``, ``durable=False``, ``feed="iex"``, and the
decisive-loss predicate block, and a ``rejected`` row must always be
non-durable + revisitable.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from milodex.backtesting.walk_forward_batch import BatchResult, BatchRow
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.research.evidence_assembler import (
    BaselineCell,
    IntradayEvidenceReport,
    SymbolDelta,
    assemble_intraday_evidence,
)

_CONFIGS_DIR = Path(__file__).parents[3] / "configs"
_BASE_CONFIG = _CONFIGS_DIR / "meanrev_rsi2_intraday_spy_v1.yaml"
_UNIVERSE_MANIFEST = _CONFIGS_DIR / "universe_liquid_etf_core_v1.yaml"

# The candidate base config's family/template/version (read off the real YAML).
_CANDIDATE_FAMILY = "meanrev"
_CANDIDATE_TEMPLATE = "rsi2.intraday"
_CANDIDATE_SPY_ID = "meanrev.rsi2.intraday.spy.v1"

# The 17-ETF liquid-core universe (UPPERCASE, sorted).
_UNIVERSE_REF = "universe.liquid_etf_core.v1"
_UNIVERSE = (
    "DIA", "GLD", "IWM", "QQQ", "SPY", "TLT",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
    "XLP", "XLRE", "XLU", "XLV", "XLY",
)  # fmt: skip

_BASELINE_KINDS = (
    "unconditional_intraday_long",
    "time_of_day_null",
    "random_matched_exposure.intraday",
    "no_trade",
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubProvider:
    """Returns a crafted BarSet per symbol; records the symbols requested."""

    def __init__(self, bars_by_symbol: dict[str, BarSet]) -> None:
        self._bars = bars_by_symbol
        self.requested_symbols: list[str] | None = None

    def get_bars(self, symbols, timeframe, start, end):  # noqa: ANN001
        self.requested_symbols = list(symbols)
        return {s: self._bars[s] for s in symbols if s in self._bars}


class _StubCtx:
    """Minimal CommandContext surface the assembler reads."""

    def __init__(self, config_dir: Path, store: EventStore, provider: _StubProvider) -> None:
        self.config_dir = config_dir
        self._store = store
        self._provider = provider

    def get_event_store(self) -> EventStore:
        return self._store

    def data_provider_factory(self) -> _StubProvider:
        return self._provider


def _empty_barset() -> BarSet:
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": pd.to_datetime([], utc=True),
                "open": pd.Series([], dtype="float64"),
                "high": pd.Series([], dtype="float64"),
                "low": pd.Series([], dtype="float64"),
                "close": pd.Series([], dtype="float64"),
                "volume": pd.Series([], dtype="int64"),
            }
        )
    )


def _full_session_barset(day: str = "2026-06-01") -> BarSet:
    """A complete regular-session 5-minute grid (78 bars, 9:30–15:55 ET)."""
    # 9:30 ET == 13:30 UTC (EDT). 78 five-minute bars to 15:55 ET (19:55 UTC).
    start = pd.Timestamp(f"{day} 13:30", tz="UTC")
    stamps = [start + pd.Timedelta(minutes=5 * i) for i in range(78)]
    n = len(stamps)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": stamps,
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.5] * n,
                "volume": [1000] * n,
            }
        )
    )


def _sparse_session_barset(day: str = "2026-06-01") -> BarSet:
    """A ~60%-coverage session (every other bar present) — low coverage, still scannable."""
    start = pd.Timestamp(f"{day} 13:30", tz="UTC")
    stamps = [start + pd.Timedelta(minutes=5 * i) for i in range(0, 78, 2)]  # ~39 of 78
    n = len(stamps)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": stamps,
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.5] * n,
                "volume": [1000] * n,
            }
        )
    )


def _candidate_id(sym: str) -> str:
    return f"{_CANDIDATE_FAMILY}.{_CANDIDATE_TEMPLATE}.{sym.lower()}.v1"


def _baseline_id(kind: str, sym: str) -> str:
    return f"benchmark.{kind}.{sym.lower()}.v1"


def _row(
    strategy_id: str,
    *,
    family: str = "meanrev",
    oos_sharpe: float | None = 1.0,
    oos_total_return_pct: float = 5.0,
    trade_count: int = 20,
    error: str | None = None,
) -> BatchRow:
    return BatchRow(
        strategy_id=strategy_id,
        family=family,
        trade_count=trade_count,
        oos_sharpe=oos_sharpe,
        oos_max_drawdown_pct=-3.0,
        oos_total_return_pct=oos_total_return_pct,
        single_window_dependency=False,
        gate_allowed=False,
        gate_promotion_type="paper",
        gate_failures=(),
        run_id=f"run-{strategy_id}",
        error=error,
    )


def _setup_configs(tmp_path: Path) -> Path:
    """Copy the real base config + every universe manifest into a tmp config dir.

    The candidate SPY base config references ``universe.spy_only.v1`` (a
    single-name manifest), so StrategyLoader().load() needs that manifest too;
    the liquid-core manifest carries the 17-ETF evidence universe. Copying all
    ``universe_*.yaml`` mirrors the production config dir.
    """
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    shutil.copy(_BASE_CONFIG, config_dir / _BASE_CONFIG.name)
    for manifest in _CONFIGS_DIR.glob("universe_*.yaml"):
        shutil.copy(manifest, config_dir / manifest.name)
    return config_dir


def _make_batch_result(
    *,
    candidate_overrides: dict[str, dict[str, Any]] | None = None,
    baseline_overrides: dict[tuple[str, str], dict[str, Any]] | None = None,
    include_baseline_kinds: tuple[str, ...] = _BASELINE_KINDS,
    candidate_symbols: tuple[str, ...] = _UNIVERSE,
) -> BatchResult:
    """Construct a stub BatchResult with one candidate + baselines per symbol.

    ``no_trade`` is SPY-only (mirrors the real bank: it is the same flat row
    regardless of symbol, generated once). All other baseline kinds appear per
    symbol, unless a symbol/kind is dropped via overrides.
    """
    candidate_overrides = candidate_overrides or {}
    baseline_overrides = baseline_overrides or {}
    rows: list[BatchRow] = []
    for sym in candidate_symbols:
        cov = candidate_overrides.get(sym)
        if cov is not None and cov.get("__omit__"):
            pass  # candidate row absent for this symbol
        else:
            rows.append(_row(_candidate_id(sym), **(cov or {})))
        for kind in include_baseline_kinds:
            if kind == "no_trade" and sym != "SPY":
                continue  # no_trade is SPY-only
            bov = baseline_overrides.get((sym, kind))
            if bov is not None and bov.get("__omit__"):
                continue
            rows.append(
                _row(
                    _baseline_id(kind, sym),
                    family="benchmark",
                    **{k: v for k, v in (bov or {}).items() if not k.startswith("__")},
                )
            )
    return BatchResult(start_date=date(2026, 1, 1), end_date=date(2026, 6, 1), rows=tuple(rows))


def _assemble(
    tmp_path: Path,
    batch_result: BatchResult,
    *,
    bars_by_symbol: dict[str, BarSet] | None = None,
    experiment_id: str = "intraday-etf-meanrev-2026-06",
) -> tuple[IntradayEvidenceReport, int, EventStore]:
    config_dir = _setup_configs(tmp_path)
    store = EventStore(tmp_path / "data" / "milodex.db")
    if bars_by_symbol is None:
        bars_by_symbol = {sym: _full_session_barset() for sym in _UNIVERSE}
    provider = _StubProvider(bars_by_symbol)
    ctx = _StubCtx(config_dir, store, provider)
    report, row_id = assemble_intraday_evidence(
        candidate_family=_CANDIDATE_FAMILY,
        candidate_template=_CANDIDATE_TEMPLATE,
        universe_ref=_UNIVERSE_REF,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 1),
        experiment_id=experiment_id,
        hypothesis="RSI(2) intraday beats every null baseline across the liquid ETF core.",
        ctx=ctx,
        batch_result=batch_result,
        config_dir=config_dir,
    )
    return report, row_id, store


# ---------------------------------------------------------------------------
# Block 1 — dataclasses + as_dict JSON round-trips; IEX markers always present
# ---------------------------------------------------------------------------


def test_baseline_cell_and_symbol_delta_dataclasses_are_frozen():
    cell = BaselineCell(
        baseline_strategy_id="benchmark.unconditional_intraday_long.spy.v1",
        kind="unconditional_intraday_long",
        sharpe=0.5,
        total_return_pct=2.0,
        round_trips=10,
        delta_sharpe=0.5,
        delta_total_return_pct=3.0,
        error=None,
    )
    with pytest.raises(Exception):
        cell.sharpe = 9.0  # type: ignore[misc]
    delta = SymbolDelta(
        symbol="SPY",
        candidate_strategy_id="meanrev.rsi2.intraday.spy.v1",
        candidate_sharpe=1.0,
        candidate_total_return_pct=5.0,
        candidate_round_trips=10,
        baselines={"unconditional_intraday_long": cell},
        coverage_pct=99.0,
        status="ok",
    )
    with pytest.raises(Exception):
        delta.status = "no"  # type: ignore[misc]


def test_report_as_dict_is_json_serializable_with_iex_markers(tmp_path):
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result())
    payload = report.as_dict()
    # Round-trips through json without a custom encoder.
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    # IEX markers ALWAYS present (load-bearing, ADR 0017).
    assert decoded["iex_exploratory"] is True
    assert decoded["durable"] is False
    assert decoded["feed"] == "iex"
    # The liquid-core manifest declares survivorship_corrected: true — read from
    # the manifest, not hardcoded. (Field defaults to False for undeclared sets.)
    assert decoded["survivorship_corrected"] is True
    assert decoded["schema_version"] == 1
    # dates serialized as isoformat strings.
    assert decoded["start_date"] == "2026-01-01"
    assert decoded["end_date"] == "2026-06-01"


def test_report_iex_defaults_independent_of_inputs(tmp_path):
    # Even a decisive candidate WIN must stay iex_exploratory + non-durable.
    overrides = {sym: {"oos_sharpe": 9.0} for sym in _UNIVERSE}
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result(candidate_overrides=overrides))
    assert report.iex_exploratory is True
    assert report.durable is False
    assert report.as_dict()["iex_exploratory"] is True
    assert report.as_dict()["durable"] is False


# ---------------------------------------------------------------------------
# Block 2 — candidate<->baseline grouping
# ---------------------------------------------------------------------------


def test_grouping_maps_symbol_to_candidate_and_present_baselines(tmp_path):
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result())
    by_symbol = {d.symbol: d for d in report.per_symbol}
    assert set(by_symbol) == set(_UNIVERSE)
    # SPY has all four baselines (no_trade is SPY-only).
    spy = by_symbol["SPY"]
    assert set(spy.baselines) == set(_BASELINE_KINDS)
    assert spy.status == "ok"
    # A non-SPY symbol has the three non-no_trade baselines, and no_trade absent.
    xlf = by_symbol["XLF"]
    assert "no_trade" not in xlf.baselines
    assert set(xlf.baselines) == {
        "unconditional_intraday_long",
        "time_of_day_null",
        "random_matched_exposure.intraday",
    }


def test_grouping_uses_lowercase_candidate_ids(tmp_path):
    # resolve_universe_ref returns UPPERCASE; candidate ids are lowercase-variant.
    # A correctly-cased join populates every cell — assert the candidate metric
    # landed (would be None on a case mismatch dropping the row).
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result())
    for d in report.per_symbol:
        assert d.candidate_strategy_id == d.candidate_strategy_id.lower()
        assert d.candidate_sharpe is not None, f"{d.symbol}: candidate row silently dropped"


def test_missing_candidate_row_marks_candidate_error(tmp_path):
    overrides = {"XLF": {"__omit__": True}}
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result(candidate_overrides=overrides))
    by_symbol = {d.symbol: d for d in report.per_symbol}
    assert by_symbol["XLF"].status == "candidate_error"
    assert by_symbol["XLF"].candidate_sharpe is None


def test_symbol_with_no_baselines_marks_status(tmp_path):
    # Drop every baseline for XLF.
    bov = {(("XLF"), kind): {"__omit__": True} for kind in _BASELINE_KINDS}
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result(baseline_overrides=bov))
    by_symbol = {d.symbol: d for d in report.per_symbol}
    assert by_symbol["XLF"].baselines == {}
    assert by_symbol["XLF"].status == "no_baselines"


# ---------------------------------------------------------------------------
# Block 3 — delta computation + error-cell asymmetry; round_trips = trade_count//2
# ---------------------------------------------------------------------------


def test_round_trips_is_half_trade_count(tmp_path):
    overrides = {"SPY": {"trade_count": 20, "oos_sharpe": 1.0}}
    bov = {("SPY", "unconditional_intraday_long"): {"trade_count": 8}}
    report, _row_id, _store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    spy = next(d for d in report.per_symbol if d.symbol == "SPY")
    assert spy.candidate_round_trips == 10  # 20 // 2, NOT 20
    assert spy.candidate_round_trips != 20
    assert spy.baselines["unconditional_intraday_long"].round_trips == 4  # 8 // 2


def test_delta_is_candidate_minus_baseline(tmp_path):
    overrides = {"SPY": {"oos_sharpe": 1.2, "oos_total_return_pct": 6.0}}
    bov = {
        ("SPY", "unconditional_intraday_long"): {
            "oos_sharpe": 0.4,
            "oos_total_return_pct": 2.0,
        }
    }
    report, _row_id, _store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    spy = next(d for d in report.per_symbol if d.symbol == "SPY")
    cell = spy.baselines["unconditional_intraday_long"]
    assert cell.delta_sharpe == pytest.approx(0.8)
    assert cell.delta_total_return_pct == pytest.approx(4.0)


def test_none_metric_never_coerced_to_zero(tmp_path):
    # A baseline with oos_sharpe=None must yield delta_sharpe=None, not candidate-0.
    bov = {("SPY", "time_of_day_null"): {"oos_sharpe": None}}
    overrides = {"SPY": {"oos_sharpe": 1.0}}
    report, _row_id, _store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    spy = next(d for d in report.per_symbol if d.symbol == "SPY")
    cell = spy.baselines["time_of_day_null"]
    assert cell.sharpe is None
    assert cell.delta_sharpe is None  # NOT 1.0 - 0.0


def test_baseline_error_isolated_to_that_cell(tmp_path):
    # A baseline error nils only its own cell; sibling baselines + the candidate
    # are unaffected.
    bov = {("SPY", "time_of_day_null"): {"error": "boom", "oos_sharpe": None}}
    overrides = {"SPY": {"oos_sharpe": 1.0}}
    report, _row_id, _store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    spy = next(d for d in report.per_symbol if d.symbol == "SPY")
    assert spy.status == "ok"
    assert spy.candidate_sharpe == 1.0
    bad = spy.baselines["time_of_day_null"]
    assert bad.error == "boom"
    assert bad.delta_sharpe is None
    # A sibling baseline still has a real delta.
    good = spy.baselines["unconditional_intraday_long"]
    assert good.delta_sharpe is not None


def test_candidate_error_records_baseline_raw_metrics(tmp_path):
    # A candidate error → status candidate_error, all deltas None, but baseline
    # raw metrics are still recorded.
    overrides = {"SPY": {"error": "candidate boom", "oos_sharpe": None}}
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result(candidate_overrides=overrides))
    spy = next(d for d in report.per_symbol if d.symbol == "SPY")
    assert spy.status == "candidate_error"
    cell = spy.baselines["unconditional_intraday_long"]
    assert cell.delta_sharpe is None
    assert cell.sharpe is not None  # raw baseline metric still recorded


# ---------------------------------------------------------------------------
# Block 4 — aggregate + advisory verdict; verdict never gates
# ---------------------------------------------------------------------------


def test_aggregate_per_baseline_kind(tmp_path):
    # Candidate beats unconditional-long on every symbol by +0.5 Sharpe.
    overrides = {sym: {"oos_sharpe": 1.0} for sym in _UNIVERSE}
    bov = {(sym, "unconditional_intraday_long"): {"oos_sharpe": 0.5} for sym in _UNIVERSE}
    report, _row_id, _store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    agg = report.aggregate
    unc = agg["per_baseline_kind"]["unconditional_intraday_long"]
    assert unc["n_symbols_compared"] == 17
    assert unc["mean_delta_sharpe"] == pytest.approx(0.5)
    assert unc["median_delta_sharpe"] == pytest.approx(0.5)
    assert unc["n_candidate_beats"] == 17
    assert agg["n_symbols_total"] == 17
    assert agg["n_candidate_errors"] == 0


def test_verdict_insufficient_data_below_five_comparable(tmp_path):
    # Only 3 symbols have a comparable unconditional-long baseline.
    drop = {
        (sym, "unconditional_intraday_long"): {"__omit__": True}
        for sym in _UNIVERSE
        if sym not in ("SPY", "QQQ", "IWM")
    }
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result(baseline_overrides=drop))
    assert report.aggregate["verdict"] == "insufficient_data"


def test_verdict_is_a_string_and_never_raises(tmp_path):
    # A decisive underperformance still RETURNS a report (verdict is advisory).
    overrides = {sym: {"oos_sharpe": -5.0} for sym in _UNIVERSE}
    bov = {(sym, "unconditional_intraday_long"): {"oos_sharpe": 1.0} for sym in _UNIVERSE}
    report, _row_id, _store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    assert isinstance(report.aggregate["verdict"], str)
    assert report.aggregate["verdict"] in {
        "candidate_beats_all_baselines",
        "mixed",
        "candidate_underperforms",
        "insufficient_data",
    }


# ---------------------------------------------------------------------------
# Block 5 — readiness join advisory + non-gating
# ---------------------------------------------------------------------------


def test_low_coverage_symbol_still_gets_full_delta(tmp_path):
    # XLF gets a ~60% coverage session; it must STILL appear with a full delta.
    bars = {sym: _full_session_barset() for sym in _UNIVERSE}
    bars["XLF"] = _sparse_session_barset()
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result(), bars_by_symbol=bars)
    by_symbol = {d.symbol: d for d in report.per_symbol}
    xlf = by_symbol["XLF"]
    assert xlf.status == "ok"
    assert xlf.coverage_pct is not None
    assert xlf.coverage_pct < 90.0  # genuinely low coverage
    # The delta is fully computed despite low coverage (the load-bearing rule).
    assert xlf.baselines["unconditional_intraday_long"].delta_sharpe is not None


def test_zero_bar_readiness_symbol_still_reported(tmp_path):
    bars = {sym: _full_session_barset() for sym in _UNIVERSE}
    bars["GLD"] = _empty_barset()
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result(), bars_by_symbol=bars)
    by_symbol = {d.symbol: d for d in report.per_symbol}
    gld = by_symbol["GLD"]
    assert gld.coverage_pct is None  # zero expected bars → None coverage
    assert gld.status == "ok"  # still fully reported
    assert gld.candidate_sharpe is not None


def test_readiness_summary_recorded(tmp_path):
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result())
    rs = report.readiness_summary
    assert "status" in rs
    assert "warning_count" in rs
    assert "issue_codes" in rs
    assert "per_symbol" in rs


# ---------------------------------------------------------------------------
# Block 6 — run_manifest join + universe resolution enforcement
# ---------------------------------------------------------------------------


def test_run_manifest_built_for_candidate(tmp_path):
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result())
    manifest = report.run_manifest
    assert manifest["strategy"]["strategy_id"] == _CANDIDATE_SPY_ID
    # One manifest, not 17 — it's a dict, not a list.
    assert isinstance(manifest, dict)


def test_symbols_sourced_from_resolve_universe_ref(tmp_path):
    # The symbols tuple must equal resolve_universe_ref's eligibility-guarded,
    # sorted, uppercased output — NOT an empty universe from bare load_strategy_config.
    report, _row_id, _store = _assemble(tmp_path, _make_batch_result())
    assert report.symbols == _UNIVERSE
    assert all(s.isupper() for s in report.symbols)
    assert len(report.symbols) == 17


# ---------------------------------------------------------------------------
# Block 7 — the single registry write + terminal_status policy + writer invariant
# ---------------------------------------------------------------------------


def test_single_registry_append(tmp_path):
    report, row_id, store = _assemble(tmp_path, _make_batch_result())
    listed = store.list_experiments()
    assert len(listed) == 1
    ev = store.get_experiment("intraday-etf-meanrev-2026-06")
    assert ev is not None
    assert ev.id == row_id
    assert ev.stage_reached == "backtest"
    assert ev.strategy_id == _CANDIDATE_SPY_ID
    assert ev.revisitable is True
    # evidence_json round-trips and carries the IEX markers + predicate block.
    assert ev.evidence_json is not None
    assert ev.evidence_json["durable"] is False
    assert ev.evidence_json["feed"] == "iex"
    assert "decisive_loss_predicate" in ev.evidence_json
    json.dumps(ev.evidence_json)  # serializable


def _decisive_loss_overrides():
    """Candidate ≥2.0 below ALL three nulls on every symbol (17/17 ≥ 14)."""
    overrides = {sym: {"oos_sharpe": -3.0} for sym in _UNIVERSE}
    bov: dict[tuple[str, str], dict[str, Any]] = {}
    for sym in _UNIVERSE:
        # Strongest null is 0.0; candidate -3.0 is 3.0 below it (≥ 2.0 margin).
        bov[(sym, "unconditional_intraday_long")] = {"oos_sharpe": 0.0}
        bov[(sym, "time_of_day_null")] = {"oos_sharpe": -0.5}
        bov[(sym, "random_matched_exposure.intraday")] = {"oos_sharpe": -0.5}
    return overrides, bov


def test_terminal_status_rejected_when_decisive_loss_fires(tmp_path):
    overrides, bov = _decisive_loss_overrides()
    report, _row_id, store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    ev = store.get_experiment("intraday-etf-meanrev-2026-06")
    assert ev is not None
    assert ev.terminal_status == "rejected"
    # A rejected IEX row is ALWAYS non-durable + revisitable.
    assert ev.revisitable is True
    assert ev.evidence_json["durable"] is False
    pred = ev.evidence_json["decisive_loss_predicate"]
    assert pred["passed"] is True
    assert pred["symbols_below_all_nulls"] >= 14
    assert pred["threshold_symbols"] == 14
    assert pred["threshold_margin"] == 2.0
    assert pred["min_margin_sharpe"] is not None


def test_terminal_status_inconclusive_for_mixed(tmp_path):
    # Candidate beats some, loses to others — predicate does not fire.
    overrides = {sym: {"oos_sharpe": 0.3} for sym in _UNIVERSE}
    bov = {(sym, "unconditional_intraday_long"): {"oos_sharpe": 0.2} for sym in _UNIVERSE}
    report, _row_id, store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    ev = store.get_experiment("intraday-etf-meanrev-2026-06")
    assert ev is not None
    assert ev.terminal_status == "inconclusive"
    assert ev.evidence_json["decisive_loss_predicate"]["passed"] is False


def test_terminal_status_inconclusive_for_decisive_win(tmp_path):
    # A decisive WIN is never durable here — IEX can overstate an edge.
    overrides = {sym: {"oos_sharpe": 5.0} for sym in _UNIVERSE}
    bov = {(sym, "unconditional_intraday_long"): {"oos_sharpe": 0.0} for sym in _UNIVERSE}
    report, _row_id, store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    ev = store.get_experiment("intraday-etf-meanrev-2026-06")
    assert ev is not None
    assert ev.terminal_status == "inconclusive"


def test_terminal_status_failed_when_all_candidates_error(tmp_path):
    overrides = {sym: {"__omit__": True} for sym in _UNIVERSE}
    report, _row_id, store = _assemble(tmp_path, _make_batch_result(candidate_overrides=overrides))
    ev = store.get_experiment("intraday-etf-meanrev-2026-06")
    assert ev is not None
    assert ev.terminal_status == "failed"


def test_writer_invariant_refuses_rejected_with_durable_true(tmp_path):
    # A rejected IEX row forced durable=True must be refused by the writer.
    import dataclasses

    from milodex.research.evidence_assembler import _write_registry_row

    # Build a genuine decisive-loss (rejected) report. _assemble creates the
    # tmp config dir internally — reuse it rather than re-mkdir'ing.
    overrides, bov = _decisive_loss_overrides()
    report, _row_id, store = _assemble(
        tmp_path,
        _make_batch_result(candidate_overrides=overrides, baseline_overrides=bov),
    )
    config_dir = tmp_path / "configs"
    # Poison the report: claim it is durable, then drive it back through the
    # writer. The terminal_status derives to "rejected" again, so the writer
    # invariant must fire.
    poisoned = dataclasses.replace(report, durable=True)
    provider = _StubProvider({sym: _full_session_barset() for sym in _UNIVERSE})
    fresh_store = EventStore(tmp_path / "data2" / "milodex.db")
    ctx = _StubCtx(config_dir, fresh_store, provider)
    candidate_spy_path = config_dir / _BASE_CONFIG.name
    with pytest.raises((AssertionError, ValueError)):
        _write_registry_row(
            ctx=ctx,
            report=poisoned,
            experiment_id="poison-test",
            hypothesis="poison",
            candidate_spy_id=_CANDIDATE_SPY_ID,
            candidate_spy_config_path=candidate_spy_path,
        )


# ---------------------------------------------------------------------------
# Block 8 — IEX label mandatory + rationale caveat
# ---------------------------------------------------------------------------


def test_evidence_json_iex_exploratory_true_unconditionally(tmp_path):
    report, _row_id, store = _assemble(tmp_path, _make_batch_result())
    ev = store.get_experiment("intraday-etf-meanrev-2026-06")
    assert ev is not None
    assert ev.evidence_json["iex_exploratory"] is True


def test_rationale_leads_with_iex_caveat(tmp_path):
    report, _row_id, store = _assemble(tmp_path, _make_batch_result())
    ev = store.get_experiment("intraday-etf-meanrev-2026-06")
    assert ev is not None
    assert "IEX-exploratory" in ev.rationale
    assert "non-durable" in ev.rationale

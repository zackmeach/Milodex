"""Tests for ``milodex research evidence`` (G-PR2 CLI facade).

Three blocks:
1. argparse wiring — evidence args accepted; run() dispatches to _evidence.
2. _batch_result_from_screen_json round-trip — rehydrates BatchResult faithfully.
3. End-to-end with stub ctx + hand-built BatchResult — CommandResult shape and
   exactly one experiment-registry row written.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from milodex.backtesting.walk_forward_batch import BatchResult, BatchRow
from milodex.cli.commands import research
from milodex.cli.commands.research import _batch_result_from_screen_json

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_batch_row(strategy_id: str, oos_sharpe: float | None = 0.3, error: str | None = None):
    if error is not None:
        return BatchRow(
            strategy_id=strategy_id,
            family="",
            trade_count=0,
            oos_sharpe=None,
            oos_max_drawdown_pct=0.0,
            oos_total_return_pct=0.0,
            single_window_dependency=False,
            gate_allowed=False,
            gate_promotion_type="error",
            gate_failures=(error,),
            run_id=None,
            oos_equity_curve=(),
            error=error,
            survivorship_corrected=False,
        )
    return BatchRow(
        strategy_id=strategy_id,
        family="meanrev",
        trade_count=40,
        oos_sharpe=oos_sharpe,
        oos_max_drawdown_pct=5.0,
        oos_total_return_pct=8.0,
        single_window_dependency=False,
        gate_allowed=False,
        gate_promotion_type="statistical",
        gate_failures=(),
        run_id=f"run-{strategy_id}",
        oos_equity_curve=((date(2024, 1, 2), 100_000.0), (date(2024, 1, 3), 100_800.0)),
        error=error,
        survivorship_corrected=False,
    )


def _make_batch_result(rows: tuple[BatchRow, ...]) -> BatchResult:
    return BatchResult(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 30),
        rows=rows,
        correlation_matrix={},
    )


def _evidence_args(**overrides) -> argparse.Namespace:
    defaults = {
        "research_command": "evidence",
        "candidate_family": "meanrev",
        "candidate_template": "rsi2.intraday",
        "universe_ref": "universe.liquid_etf_core.v1",
        "start": "2024-01-01",
        "end": "2024-06-30",
        "experiment_id": "test-exp-001",
        "hypothesis": "RSI(2) intraday edge hypothesis",
        "screen_json": None,
        # feed_label removed: lane is IEX-only, label is hardcoded in handler.
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Block 1: argparse wiring
# ---------------------------------------------------------------------------


def test_evidence_argparse_accepts_required_args():
    """register() adds an 'evidence' subparser accepting all required args."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="top")
    sub = subparsers.add_parser("research")
    sub.add_subparsers(dest="research_command")
    # Re-use the module's register to wire the full tree
    top = argparse.ArgumentParser()
    top_sub = top.add_subparsers(dest="command")
    research.register(top_sub)

    argv = [
        "research",
        "evidence",
        "--candidate-family",
        "meanrev",
        "--candidate-template",
        "rsi2.intraday",
        "--universe-ref",
        "universe.liquid_etf_core.v1",
        "--start",
        "2024-01-01",
        "--end",
        "2024-06-30",
        "--experiment-id",
        "exp-xyz",
        "--hypothesis",
        "Test hypothesis",
    ]
    ns = top.parse_args(argv)
    assert ns.research_command == "evidence"
    assert ns.candidate_family == "meanrev"
    assert ns.candidate_template == "rsi2.intraday"
    assert ns.universe_ref == "universe.liquid_etf_core.v1"
    assert ns.experiment_id == "exp-xyz"
    assert not hasattr(ns, "feed_label")  # removed: lane is IEX-only
    assert ns.screen_json is None  # optional, default None


def test_evidence_argparse_accepts_optional_screen_json():
    top = argparse.ArgumentParser()
    top_sub = top.add_subparsers(dest="command")
    research.register(top_sub)
    argv = [
        "research",
        "evidence",
        "--candidate-family",
        "meanrev",
        "--candidate-template",
        "rsi2.intraday",
        "--universe-ref",
        "universe.liquid_etf_core.v1",
        "--start",
        "2024-01-01",
        "--end",
        "2024-06-30",
        "--experiment-id",
        "e",
        "--hypothesis",
        "h",
        "--screen-json",
        "docs/reviews/some.json",
        # --feed-label removed: lane is IEX-only, label is fixed in handler.
    ]
    ns = top.parse_args(argv)
    assert ns.screen_json == "docs/reviews/some.json"
    assert not hasattr(ns, "feed_label")


def test_run_dispatches_evidence_to_handler(monkeypatch):
    """run() calls _evidence when research_command == 'evidence'."""
    sentinel = MagicMock()
    monkeypatch.setattr(research, "_evidence", sentinel)
    ctx = MagicMock()
    args = _evidence_args()
    research.run(args, ctx)
    sentinel.assert_called_once_with(args, ctx)


# ---------------------------------------------------------------------------
# Block 2: _batch_result_from_screen_json round-trip
# ---------------------------------------------------------------------------


def _write_screen_json(path: Path, batch: BatchResult) -> None:
    """Serialise a BatchResult in the same shape _write_report uses."""
    payload = {
        "start_date": batch.start_date.isoformat(),
        "end_date": batch.end_date.isoformat(),
        "generated_at": "2024-06-30T12:00:00+00:00",
        "matched_config_count": len(batch.rows),
        "selected_strategy_ids": [r.strategy_id for r in batch.rows],
        "skipped_configs": [],
        "rows": [r.as_dict() for r in batch.rows],
        "correlation_matrix": batch.correlation_matrix,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_batch_result_round_trip(tmp_path: Path):
    """_batch_result_from_screen_json rehydrates a BatchResult faithfully."""
    row_a = _make_batch_row("meanrev.rsi2.intraday.spy.v1", oos_sharpe=0.45)
    row_b = _make_batch_row("benchmark.unconditional_intraday_long.spy.v1", oos_sharpe=0.61)
    row_err = _make_batch_row("meanrev.rsi2.intraday.qqq.v1", oos_sharpe=None, error="no data")
    original = _make_batch_result((row_a, row_b, row_err))

    json_path = tmp_path / "screen.json"
    _write_screen_json(json_path, original)

    recovered = _batch_result_from_screen_json(json_path, event_store=_provenance_store(original))

    assert recovered.start_date == original.start_date
    assert recovered.end_date == original.end_date
    assert len(recovered.rows) == 3

    ids = {r.strategy_id for r in recovered.rows}
    assert "meanrev.rsi2.intraday.spy.v1" in ids
    assert "benchmark.unconditional_intraday_long.spy.v1" in ids

    by_id = {r.strategy_id: r for r in recovered.rows}
    r = by_id["meanrev.rsi2.intraday.spy.v1"]
    assert r.oos_sharpe == pytest.approx(0.45)
    assert r.trade_count == 40
    assert r.run_id == "run-meanrev.rsi2.intraday.spy.v1"
    assert r.error is None
    assert r.survivorship_corrected is False

    r_err = by_id["meanrev.rsi2.intraday.qqq.v1"]
    assert r_err.oos_sharpe is None
    assert r_err.error == "no data"

    # equity curve round-trips
    r_b = by_id["benchmark.unconditional_intraday_long.spy.v1"]
    assert len(r_b.oos_equity_curve) == 2
    assert r_b.oos_equity_curve[0] == (date(2024, 1, 2), pytest.approx(100_000.0))


def test_batch_result_from_json_tolerates_missing_optional_keys(tmp_path: Path):
    """Rows missing oos_equity_curve / error / survivorship_corrected get defaults."""
    payload = {
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
        "selected_strategy_ids": ["meanrev.rsi2.intraday.spy.v1"],
        "rows": [
            {
                "strategy_id": "meanrev.rsi2.intraday.spy.v1",
                "family": "meanrev",
                "trade_count": 10,
                "oos_sharpe": 0.2,
                "oos_max_drawdown_pct": 3.0,
                "oos_total_return_pct": 2.0,
                "single_window_dependency": False,
                "gate_allowed": False,
                "gate_promotion_type": "statistical",
                "gate_failures": [],
                "run_id": "run-001",
                # oos_equity_curve, error, survivorship_corrected intentionally absent
            }
        ],
        "correlation_matrix": {},
    }
    json_path = tmp_path / "minimal.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    expected = _make_batch_result(
        (
            BatchRow(
                strategy_id="meanrev.rsi2.intraday.spy.v1",
                family="meanrev",
                trade_count=10,
                oos_sharpe=0.2,
                oos_max_drawdown_pct=3.0,
                oos_total_return_pct=2.0,
                single_window_dependency=False,
                gate_allowed=False,
                gate_promotion_type="statistical",
                gate_failures=(),
                run_id="run-001",
            ),
        )
    )
    result = _batch_result_from_screen_json(json_path, event_store=_provenance_store(expected))
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.oos_equity_curve == ()
    assert row.error is None
    assert row.survivorship_corrected is False


# ---------------------------------------------------------------------------
# Block 3: end-to-end with stub ctx + hand-built BatchResult
# ---------------------------------------------------------------------------


def _stub_store():
    """Minimal event-store stub that records append_experiment calls."""
    store = MagicMock()
    store.append_experiment.return_value = 42
    # get_experiment returns a stub with terminal_status
    exp_stub = MagicMock()
    exp_stub.terminal_status = "rejected"
    store.get_experiment.return_value = exp_stub
    return store


def _stub_ctx(store=None, tmp_config_dir: Path | None = None):
    ctx = MagicMock()
    ctx.get_event_store.return_value = store or _stub_store()
    if tmp_config_dir is not None:
        ctx.config_dir = tmp_config_dir
    return ctx


def test_evidence_handler_returns_command_result_with_report_dict(tmp_path: Path, monkeypatch):
    """_evidence returns a CommandResult whose data is the report dict + row_id."""
    store = _stub_store()
    ctx = _stub_ctx(store=store)

    fake_report = MagicMock()
    fake_report.as_dict.return_value = {
        "experiment_id": "test-exp-001",
        "aggregate": {"verdict": "candidate_underperforms", "per_baseline_kind": {}},
        "symbols": ["SPY"],
        "per_symbol": [],
    }
    fake_report.symbols = ("SPY",)
    fake_report.aggregate = {
        "verdict": "candidate_underperforms",
        "per_baseline_kind": {},
        "n_symbols_total": 1,
        "n_candidate_errors": 0,
    }

    with patch("milodex.cli.commands.research._evidence") as mock_ev:
        from milodex.cli.formatter import CommandResult

        mock_ev.return_value = CommandResult(
            command="research.evidence",
            data={"experiment_registry_row_id": 42, "experiment_id": "test-exp-001"},
            human_lines=["Verdict: candidate_underperforms", "Registry row id: 42"],
        )
        args = _evidence_args()
        result = research.run(args, ctx)

    assert result.command == "research.evidence"
    assert result.data["experiment_registry_row_id"] == 42
    assert any("42" in line for line in result.human_lines)


def test_evidence_handler_writes_one_registry_row(tmp_path: Path, monkeypatch):
    """Calling _evidence writes exactly one experiment-registry row via the store."""
    store = _stub_store()
    ctx = _stub_ctx(store=store)

    # Build a minimal BatchResult that assemble_intraday_evidence can consume.
    # We patch assemble_intraday_evidence itself so no real backtest runs.
    fake_report = MagicMock()
    fake_report.symbols = ("SPY",)
    fake_report.aggregate = {
        "verdict": "candidate_underperforms",
        "per_baseline_kind": {
            "unconditional_intraday_long": {"n_symbols_compared": 1},
        },
        "n_symbols_total": 1,
        "n_candidate_errors": 0,
    }
    fake_report.as_dict.return_value = {
        "experiment_id": "test-exp-001",
        "aggregate": fake_report.aggregate,
    }

    with patch(
        "milodex.cli.commands.research.assemble_intraday_evidence"
        if hasattr(research, "assemble_intraday_evidence")
        else "milodex.research.evidence_assembler.assemble_intraday_evidence"
    ):
        # Patch via the import inside _evidence
        with patch(
            "milodex.research.evidence_assembler.assemble_intraday_evidence",
            return_value=(fake_report, 42),
        ) as mock_assemble:
            args = _evidence_args()
            result = research.run(args, ctx)

    # The store's get_experiment is called once (to read terminal_status for human_lines)
    store.get_experiment.assert_called_once_with("test-exp-001")
    # assemble_intraday_evidence was called with the right keyword args
    call_kwargs = mock_assemble.call_args.kwargs
    assert call_kwargs["candidate_family"] == "meanrev"
    assert call_kwargs["candidate_template"] == "rsi2.intraday"
    assert call_kwargs["experiment_id"] == "test-exp-001"
    assert call_kwargs["batch_result"] is None  # no --screen-json supplied

    assert result.command == "research.evidence"
    assert result.data["experiment_registry_row_id"] == 42


def test_evidence_handler_passes_rehydrated_batch_result(tmp_path: Path, monkeypatch):
    """When --screen-json is given, a rehydrated BatchResult is passed to the assembler."""
    store = _stub_store()
    ctx = _stub_ctx(store=store)

    # Write a minimal screen JSON
    row = _make_batch_row("meanrev.rsi2.intraday.spy.v1")
    batch = _make_batch_result((row,))
    json_path = tmp_path / "screen.json"
    _write_screen_json(json_path, batch)
    store.get_backtest_run.return_value = _persisted_run_for(row, batch)

    fake_report = MagicMock()
    fake_report.symbols = ("SPY",)
    fake_report.aggregate = {
        "verdict": "mixed",
        "per_baseline_kind": {},
        "n_symbols_total": 1,
        "n_candidate_errors": 0,
    }
    fake_report.as_dict.return_value = {"experiment_id": "test-exp-001"}

    with patch(
        "milodex.research.evidence_assembler.assemble_intraday_evidence",
        return_value=(fake_report, 7),
    ) as mock_assemble:
        args = _evidence_args(screen_json=str(json_path))
        result = research.run(args, ctx)

    passed_batch = mock_assemble.call_args.kwargs["batch_result"]
    assert passed_batch is not None
    assert isinstance(passed_batch, BatchResult)
    assert len(passed_batch.rows) == 1
    assert passed_batch.rows[0].strategy_id == "meanrev.rsi2.intraday.spy.v1"
    assert result.data["experiment_registry_row_id"] == 7


# ---------------------------------------------------------------------------
# Block 4: provenance validation (Bug 2 fix)
# ---------------------------------------------------------------------------


def _make_batch_row_no_run_id(strategy_id: str) -> BatchRow:
    """A row missing run_id — simulates an uncommitted / fabricated screen JSON row."""
    return BatchRow(
        strategy_id=strategy_id,
        family="meanrev",
        trade_count=40,
        oos_sharpe=0.3,
        oos_max_drawdown_pct=5.0,
        oos_total_return_pct=8.0,
        single_window_dependency=False,
        gate_allowed=False,
        gate_promotion_type="statistical",
        gate_failures=(),
        run_id=None,  # missing
        oos_equity_curve=(),
        error=None,
        survivorship_corrected=False,
    )


def test_screen_json_missing_run_id_raises(tmp_path: Path):
    """_batch_result_from_screen_json raises ValueError when any row has no run_id."""
    row = _make_batch_row_no_run_id("meanrev.rsi2.intraday.spy.v1")
    batch = _make_batch_result((row,))

    # Write JSON with run_id explicitly null
    payload = {
        "start_date": batch.start_date.isoformat(),
        "end_date": batch.end_date.isoformat(),
        "selected_strategy_ids": ["meanrev.rsi2.intraday.spy.v1"],
        "rows": [
            {
                "strategy_id": "meanrev.rsi2.intraday.spy.v1",
                "family": "meanrev",
                "trade_count": 40,
                "oos_sharpe": 0.3,
                "oos_max_drawdown_pct": 5.0,
                "oos_total_return_pct": 8.0,
                "single_window_dependency": False,
                "gate_allowed": False,
                "gate_promotion_type": "statistical",
                "gate_failures": [],
                "run_id": None,  # null in JSON
            }
        ],
        "correlation_matrix": {},
    }
    json_path = tmp_path / "no_run_id.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="run_id"):
        _batch_result_from_screen_json(json_path, event_store=MagicMock())


def test_screen_json_missing_run_id_key_raises(tmp_path: Path):
    """_batch_result_from_screen_json raises ValueError when run_id key is absent."""
    payload = {
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
        "selected_strategy_ids": ["meanrev.rsi2.intraday.spy.v1"],
        "rows": [
            {
                "strategy_id": "meanrev.rsi2.intraday.spy.v1",
                "family": "meanrev",
                "trade_count": 40,
                "oos_sharpe": 0.3,
                "oos_max_drawdown_pct": 5.0,
                "oos_total_return_pct": 8.0,
                "single_window_dependency": False,
                "gate_allowed": False,
                "gate_promotion_type": "statistical",
                "gate_failures": [],
                # run_id key entirely absent
            }
        ],
        "correlation_matrix": {},
    }
    json_path = tmp_path / "absent_run_id.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="run_id"):
        _batch_result_from_screen_json(json_path, event_store=MagicMock())


def test_screen_json_date_mismatch_raises(tmp_path: Path):
    """_evidence raises ValueError when JSON dates don't match CLI start/end args."""
    row = _make_batch_row("meanrev.rsi2.intraday.spy.v1")
    # JSON has 2024-01-01 – 2024-06-30; CLI args will say 2025-01-01 – 2025-06-30.
    batch = _make_batch_result((row,))
    json_path = tmp_path / "screen.json"
    _write_screen_json(json_path, batch)

    store = _stub_store()
    store.get_backtest_run.return_value = _persisted_run_for(row, batch)
    ctx = _stub_ctx(store=store)
    # CLI args with different dates than the JSON
    args = _evidence_args(
        screen_json=str(json_path),
        start="2025-01-01",
        end="2025-06-30",
    )

    from milodex.cli.commands.research import _evidence

    with pytest.raises(ValueError, match="does not match"):
        _evidence(args, ctx)


def test_screen_json_consistent_provenance_succeeds(tmp_path: Path):
    """A well-formed screen JSON with matching dates passes provenance validation."""
    row = _make_batch_row("meanrev.rsi2.intraday.spy.v1")
    batch = _make_batch_result((row,))
    json_path = tmp_path / "screen.json"
    _write_screen_json(json_path, batch)

    # Dates match: JSON has 2024-01-01 – 2024-06-30; args supply the same.
    result = _batch_result_from_screen_json(json_path, event_store=_provenance_store(batch))
    assert result.start_date.isoformat() == "2024-01-01"
    assert result.end_date.isoformat() == "2024-06-30"
    assert result.rows[0].run_id is not None  # provenance intact


def _persisted_run_for(row: BatchRow, batch: BatchResult):
    aggregate = {
        "trade_count": row.trade_count,
        "sharpe": row.oos_sharpe,
        "max_drawdown_pct": row.oos_max_drawdown_pct,
        "total_return_pct": row.oos_total_return_pct,
        "equity_curve": [[d.isoformat(), v] for d, v in row.oos_equity_curve],
    }
    return SimpleNamespace(
        run_id=row.run_id,
        strategy_id=row.strategy_id,
        config_hash="config-hash",
        start_date=datetime.combine(batch.start_date, datetime.min.time(), tzinfo=UTC),
        end_date=datetime.combine(batch.end_date, datetime.min.time(), tzinfo=UTC),
        status="completed",
        metadata={
            "source": "research_screen",
            "oos_aggregate": aggregate,
            "stability": {"single_window_dependency": row.single_window_dependency},
            "run_manifest": {"strategy": {"config_hash": "config-hash"}},
        },
    )


def _provenance_store(batch: BatchResult):
    store = MagicMock()
    by_run_id = {
        row.run_id: _persisted_run_for(row, batch) for row in batch.rows if row.run_id is not None
    }
    store.get_backtest_run.side_effect = by_run_id.get
    return store


def test_screen_json_rejects_fabricated_run_id(tmp_path: Path):
    row = _make_batch_row("meanrev.rsi2.intraday.spy.v1")
    batch = _make_batch_result((row,))
    path = tmp_path / "forged.json"
    _write_screen_json(path, batch)
    store = MagicMock()
    store.get_backtest_run.return_value = None

    with pytest.raises(ValueError, match="does not exist"):
        _batch_result_from_screen_json(path, event_store=store)


def test_screen_json_rejects_metrics_that_do_not_match_persisted_run(tmp_path: Path):
    row = _make_batch_row("meanrev.rsi2.intraday.spy.v1", oos_sharpe=99.0)
    batch = _make_batch_result((row,))
    path = tmp_path / "forged_metrics.json"
    _write_screen_json(path, batch)
    persisted = _persisted_run_for(row, batch)
    persisted.metadata["oos_aggregate"]["sharpe"] = 0.25
    store = MagicMock()
    store.get_backtest_run.return_value = persisted

    with pytest.raises(ValueError, match="oos_sharpe"):
        _batch_result_from_screen_json(path, event_store=store)


def test_screen_json_accepts_correctly_shaped_error_row_without_run_id(tmp_path: Path):
    error_row = BatchRow(
        strategy_id="meanrev.rsi2.intraday.spy.v1",
        family="",
        trade_count=0,
        oos_sharpe=None,
        oos_max_drawdown_pct=0.0,
        oos_total_return_pct=0.0,
        single_window_dependency=False,
        gate_allowed=False,
        gate_promotion_type="error",
        gate_failures=("config load failed",),
        run_id=None,
        error="config load failed",
    )
    batch = _make_batch_result((error_row,))
    path = tmp_path / "error.json"
    _write_screen_json(path, batch)
    store = MagicMock()

    result = _batch_result_from_screen_json(path, event_store=store)

    assert result.rows == (error_row,)
    store.get_backtest_run.assert_not_called()


def test_screen_json_rejects_duplicate_rows_even_when_roster_set_matches(tmp_path: Path):
    row = _make_batch_row("meanrev.rsi2.intraday.spy.v1")
    batch = _make_batch_result((row, row))
    path = tmp_path / "duplicates.json"
    _write_screen_json(path, batch)
    store = MagicMock()

    with pytest.raises(ValueError, match="duplicate"):
        _batch_result_from_screen_json(path, event_store=store)

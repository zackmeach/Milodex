"""Tests for the ``milodex research screen`` command.

Covers the thin CLI layer: glob resolution, mutual-exclusion validation,
output shape, and report writing. The underlying evaluation logic is
covered in ``test_walk_forward_batch.py``.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from milodex.backtesting.walk_forward_batch import BatchResult, BatchRow
from milodex.cli.commands import research


def _row(strategy_id: str = "meanrev.daily.a.v.v1") -> BatchRow:
    return BatchRow(
        strategy_id=strategy_id,
        family="meanrev",
        trade_count=42,
        oos_sharpe=0.91,
        oos_max_drawdown_pct=7.3,
        oos_total_return_pct=12.1,
        single_window_dependency=False,
        gate_allowed=True,
        gate_promotion_type="statistical",
        gate_failures=(),
        run_id="run-xyz",
    )


def _make_args(**overrides) -> argparse.Namespace:
    defaults = {
        "research_command": "screen",
        "configs": None,
        "strategy_ids": [],
        "start": "2024-01-01",
        "end": "2024-03-31",
        "fail_fast": False,
        "initial_equity": 100_000.0,
        "report_out": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _stub_run_batch(monkeypatch, rows: tuple[BatchRow, ...]):
    result = BatchResult(start_date=date(2024, 1, 1), end_date=date(2024, 3, 31), rows=rows)
    stub = MagicMock(return_value=result)
    monkeypatch.setattr(research, "run_batch", stub)
    return stub


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------


def test_screen_rejects_both_configs_and_strategy_id():
    ctx = MagicMock()
    args = _make_args(configs="*.yaml", strategy_ids=["x"])
    with pytest.raises(ValueError, match="mutually exclusive"):
        research.run(args, ctx)


def test_screen_rejects_neither_configs_nor_strategy_id():
    ctx = MagicMock()
    args = _make_args()
    with pytest.raises(ValueError, match="Specify strategies"):
        research.run(args, ctx)


def test_screen_uses_explicit_strategy_ids(monkeypatch):
    stub = _stub_run_batch(monkeypatch, rows=(_row(),))
    ctx = MagicMock()
    args = _make_args(strategy_ids=["meanrev.daily.a.v.v1", "meanrev.daily.b.v.v1"])
    research.run(args, ctx)
    call = stub.call_args
    assert list(call.kwargs["strategy_ids"]) == [
        "meanrev.daily.a.v.v1",
        "meanrev.daily.b.v.v1",
    ]


def test_screen_glob_resolves_configs_to_strategy_ids(monkeypatch, tmp_path: Path):
    (tmp_path / "meanrev_a.yaml").write_text("strategy:\n  id: meanrev.daily.a.v.v1\n")
    (tmp_path / "meanrev_b.yaml").write_text("strategy:\n  id: meanrev.daily.b.v.v1\n")
    (tmp_path / "other.yaml").write_text("strategy:\n  id: momo.daily.c.v.v1\n")

    def fake_load(path: Path):
        config = MagicMock()
        config.strategy_id = path.read_text().split("id:", 1)[1].strip()
        return config

    monkeypatch.setattr(research, "load_strategy_config", fake_load)
    stub = _stub_run_batch(monkeypatch, rows=(_row(),))
    ctx = MagicMock()
    ctx.config_dir = tmp_path
    args = _make_args(configs="meanrev_*.yaml")
    research.run(args, ctx)
    resolved = sorted(stub.call_args.kwargs["strategy_ids"])
    assert resolved == ["meanrev.daily.a.v.v1", "meanrev.daily.b.v.v1"]


def test_screen_glob_no_match_errors(tmp_path: Path):
    ctx = MagicMock()
    ctx.config_dir = tmp_path
    args = _make_args(configs="does_not_exist_*.yaml")
    with pytest.raises(ValueError, match="No configs matched"):
        research.run(args, ctx)


def test_screen_rejects_inverted_dates(monkeypatch):
    _stub_run_batch(monkeypatch, rows=())
    ctx = MagicMock()
    args = _make_args(strategy_ids=["x"], start="2024-06-01", end="2024-01-01")
    with pytest.raises(ValueError, match="on or after"):
        research.run(args, ctx)


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_screen_result_has_rows_in_json_data(monkeypatch):
    _stub_run_batch(monkeypatch, rows=(_row(),))
    ctx = MagicMock()
    result = research.run(_make_args(strategy_ids=["x"]), ctx)
    assert result.command == "research.screen"
    assert result.data["row_count"] == 1
    row = result.data["rows"][0]
    for key in [
        "strategy_id",
        "family",
        "trade_count",
        "oos_sharpe",
        "oos_max_drawdown_pct",
        "gate_allowed",
        "gate_promotion_type",
        "gate_failures",
    ]:
        assert key in row


def test_screen_human_lines_contain_ranking_table(monkeypatch):
    _stub_run_batch(monkeypatch, rows=(_row(),))
    ctx = MagicMock()
    result = research.run(_make_args(strategy_ids=["x"]), ctx)
    header = [ln for ln in result.human_lines if ln.startswith("strategy_id")]
    assert header, "expected a header line starting with 'strategy_id'"
    assert any("meanrev.daily.a.v.v1" in ln for ln in result.human_lines)


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------


def test_screen_report_out_writes_markdown_and_json(monkeypatch, tmp_path: Path):
    _stub_run_batch(monkeypatch, rows=(_row(),))
    ctx = MagicMock()
    md_path = tmp_path / "screen.md"
    args = _make_args(strategy_ids=["x"], report_out=str(md_path))
    result = research.run(args, ctx)
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "meanrev.daily.a.v.v1" in md
    assert "| strategy_id |" in md or "strategy_id" in md
    json_path = md_path.with_suffix(".json")
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["rows"][0]["strategy_id"] == "meanrev.daily.a.v.v1"
    assert result.data["report_path"] == str(md_path)


def test_screen_report_out_default_path_under_docs_reviews(monkeypatch, tmp_path: Path, chdir):
    """No value passed to --report-out → defaults to docs/reviews/screen_<today>.md.

    The test chdirs to a tmp dir so we don't pollute the real repo.
    """
    _stub_run_batch(monkeypatch, rows=(_row(),))
    ctx = MagicMock()
    args = _make_args(strategy_ids=["x"], report_out="__default__")
    result = research.run(args, ctx)
    path = Path(result.data["report_path"])
    assert path.parent.name == "reviews"
    assert path.parent.parent.name == "docs"
    assert path.name.startswith("screen_")
    assert path.suffix == ".md"


@pytest.fixture
def chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

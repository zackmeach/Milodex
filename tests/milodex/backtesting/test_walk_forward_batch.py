"""Tests for the batch walk-forward evaluator.

Focuses on the batch-specific concerns — per-strategy error handling, the
in-memory bar cache across strategies with identical universes, ranking
order, and the low-evidence row being surfaced rather than dropped. The
single-strategy walk-forward math is already covered in
``test_walk_forward_runner.py``.
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.backtesting.walk_forward_batch import BatchRow, _rank_rows, run_batch
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.strategies.base import DecisionReasoning, StrategyDecision


def _decision() -> StrategyDecision:
    return StrategyDecision(
        intents=[], reasoning=DecisionReasoning(rule="no_signal", narrative="stub")
    )


def _make_barset(closes: list[float], start: date) -> BarSet:
    rows = []
    d = start
    for close in closes:
        rows.append(
            {
                "timestamp": pd.Timestamp(d, tz="UTC"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
                "vwap": close,
            }
        )
        d += timedelta(days=1)
    return BarSet(pd.DataFrame(rows))


def _make_loaded(strategy_id: str, family: str, universe: tuple[str, ...]):
    from milodex.strategies.base import StrategyContext

    tmp_dir = Path(tempfile.mkdtemp())
    yaml_path = tmp_dir / f"{strategy_id}.yaml"
    yaml_path.write_text(f"strategy:\n  id: {strategy_id}\n", encoding="utf-8")

    config = MagicMock()
    config.strategy_id = strategy_id
    config.family = family
    config.stage = "backtest"
    config.path = yaml_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.universe = universe

    context = StrategyContext(
        strategy_id=strategy_id,
        family=family,
        template="daily.test",
        variant="v",
        version=1,
        config_hash="hash",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path=str(yaml_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = _decision()

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def _make_engine(
    strategy_id: str,
    family: str,
    universe: tuple[str, ...] = ("SPY",),
    bars_start: date = date(2024, 1, 2),
    bar_count: int = 30,
    shared_provider: MagicMock | None = None,
):
    loaded = _make_loaded(strategy_id, family, universe)
    closes = [100.0 + i for i in range(bar_count)]
    barset = _make_barset(closes, start=bars_start)
    provider = shared_provider or MagicMock()
    provider.get_bars.return_value = {sym: barset for sym in universe}
    store = EventStore(Path(tempfile.mktemp(suffix=".db")))
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    return engine, provider


def _make_ctx(engines: dict[str, BacktestEngine], config_dir: Path | None = None):
    ctx = MagicMock()
    ctx.config_dir = config_dir or Path(tempfile.mkdtemp())
    ctx.get_backtest_engine = lambda sid, **kwargs: engines[sid]
    return ctx


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def test_batch_rejects_inverted_dates():
    ctx = _make_ctx({})
    with pytest.raises(ValueError, match="end_date"):
        run_batch(
            strategy_ids=["x"],
            start_date=date(2024, 2, 1),
            end_date=date(2024, 1, 1),
            ctx=ctx,
        )


def test_batch_runs_two_strategies_and_returns_row_per():
    e1, _ = _make_engine("meanrev.daily.a.v.v1", "meanrev")
    e2, _ = _make_engine("meanrev.daily.b.v.v1", "meanrev")
    ctx = _make_ctx({e1._loaded.config.strategy_id: e1, e2._loaded.config.strategy_id: e2})
    result = run_batch(
        strategy_ids=[e1._loaded.config.strategy_id, e2._loaded.config.strategy_id],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
    )
    assert len(result.rows) == 2
    assert {r.strategy_id for r in result.rows} == {
        e1._loaded.config.strategy_id,
        e2._loaded.config.strategy_id,
    }


def test_batch_fail_fast_reraises_first_error():
    e1, _ = _make_engine("a", "meanrev")
    ctx = _make_ctx({"a": e1})

    def boom(_sid, **_kwargs):
        raise RuntimeError("engine build failed")

    ctx.get_backtest_engine = boom
    with pytest.raises(RuntimeError, match="engine build failed"):
        run_batch(
            strategy_ids=["a"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 31),
            ctx=ctx,
            fail_fast=True,
        )


def test_batch_without_fail_fast_records_error_and_continues():
    e_good, _ = _make_engine("good", "meanrev")
    ctx = MagicMock()
    ctx.config_dir = Path(tempfile.mkdtemp())

    def get_engine(sid, **_kwargs):
        if sid == "bad":
            raise RuntimeError("boom")
        return e_good

    ctx.get_backtest_engine = get_engine
    result = run_batch(
        strategy_ids=["bad", "good"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
        fail_fast=False,
    )
    assert len(result.rows) == 2
    bad_row = next(r for r in result.rows if r.strategy_id == "bad")
    assert bad_row.error == "boom"
    assert bad_row.gate_promotion_type == "error"
    assert bad_row.gate_allowed is False
    good_row = next(r for r in result.rows if r.strategy_id == "good")
    assert good_row.error is None


def test_low_evidence_strategy_flagged_not_dropped():
    """A strategy with <30 trades must appear in the result with a blocked gate.

    The screen's job is to *show* the operator what's insufficient, not to
    silently filter it. A dropped row is indistinguishable from "we forgot
    to run it" in the report.
    """
    engine, _ = _make_engine("meanrev.daily.sparse.v.v1", "meanrev")
    ctx = _make_ctx({engine._loaded.config.strategy_id: engine})
    result = run_batch(
        strategy_ids=[engine._loaded.config.strategy_id],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
    )
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.trade_count == 0  # no-signal strategy
    assert row.gate_allowed is False
    assert any("Trade count" in f for f in row.gate_failures)


def test_regime_family_gets_lifecycle_exempt_gate():
    engine, _ = _make_engine("regime.daily.sma200_rotation.spy_shy.v1", "regime")
    ctx = _make_ctx({engine._loaded.config.strategy_id: engine})
    result = run_batch(
        strategy_ids=[engine._loaded.config.strategy_id],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
    )
    assert result.rows[0].gate_allowed is True
    assert result.rows[0].gate_promotion_type == "lifecycle_exempt"


# ---------------------------------------------------------------------------
# In-memory bar cache
# ---------------------------------------------------------------------------


def test_bar_cache_reuses_prefetch_across_same_universe():
    """Two strategies over the same universe → prefetch called once.

    The disk cache already prevents repeat Alpaca calls; this in-memory
    layer additionally avoids re-reading / re-deserializing the parquet
    files within a single batch invocation.
    """
    shared = MagicMock()
    shared.get_bars.return_value = {
        "SPY": _make_barset([100.0 + i for i in range(40)], date(2024, 1, 2))
    }
    e1, _ = _make_engine("a", "meanrev", shared_provider=shared)
    e2, _ = _make_engine("b", "meanrev", shared_provider=shared)
    ctx = _make_ctx({"a": e1, "b": e2})
    run_batch(
        strategy_ids=["a", "b"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
    )
    # Two strategies, same universe, same warmup → one get_bars call, not two.
    assert shared.get_bars.call_count == 1


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _row(strategy_id: str, *, sharpe: float | None, allowed: bool) -> BatchRow:
    return BatchRow(
        strategy_id=strategy_id,
        family="meanrev",
        trade_count=50,
        oos_sharpe=sharpe,
        oos_max_drawdown_pct=5.0,
        oos_total_return_pct=10.0,
        single_window_dependency=False,
        gate_allowed=allowed,
        gate_promotion_type="statistical",
        gate_failures=(),
        run_id="r",
    )


def test_rank_gate_passing_first_then_sharpe_desc():
    rows = [
        _row("blocked_high", sharpe=2.0, allowed=False),
        _row("passing_mid", sharpe=1.0, allowed=True),
        _row("passing_high", sharpe=1.5, allowed=True),
        _row("blocked_low", sharpe=0.3, allowed=False),
    ]
    ranked = _rank_rows(rows)
    assert [r.strategy_id for r in ranked] == [
        "passing_high",
        "passing_mid",
        "blocked_high",
        "blocked_low",
    ]


def test_rank_puts_none_sharpe_rows_at_bottom_of_tier():
    rows = [
        _row("no_sharpe", sharpe=None, allowed=False),
        _row("with_sharpe", sharpe=0.2, allowed=False),
    ]
    ranked = _rank_rows(rows)
    assert [r.strategy_id for r in ranked] == ["with_sharpe", "no_sharpe"]

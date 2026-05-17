"""Persistence test for ``milodex research screen``.

Verifies that running ``research screen`` causes rows to land in the
``backtest_runs`` table of the event store — the gap identified in the
backtest-rejection audit (recommendation 7 in
docs/reviews/backtest-rejection-analysis.md).

Uses a real temp SQLite event store rather than mocking
``append_backtest_run``, so the test validates actual DB writes, not just
that a call was made.
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from milodex.backtesting.engine import BacktestEngine
from milodex.backtesting.walk_forward_batch import run_batch
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.strategies.base import DecisionReasoning, StrategyDecision

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


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
    from unittest.mock import MagicMock

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
        config_hash="deadbeef",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path=str(yaml_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = _decision()
    strategy.max_lookback_periods.return_value = 0

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def _make_engine_with_store(
    strategy_id: str,
    family: str,
    event_store: EventStore,
    bar_count: int = 40,
    bars_start: date = date(2024, 1, 2),
    universe: tuple[str, ...] = ("SPY",),
):
    from unittest.mock import MagicMock

    loaded = _make_loaded(strategy_id, family, universe)
    closes = [100.0 + i for i in range(bar_count)]
    barset = _make_barset(closes, bars_start)
    provider = MagicMock()
    provider.get_bars.return_value = {sym: barset for sym in universe}

    return BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=event_store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )


# ---------------------------------------------------------------------------
# Persistence assertions
# ---------------------------------------------------------------------------


def test_research_screen_persists_one_row_per_strategy():
    """Each strategy screened must produce a row in backtest_runs.

    This is the regression test for the process-integrity gap: the research
    screen was producing comparison artifacts without writing to the event
    store, making DB↔artifact reconciliation impossible.
    """
    db_path = Path(tempfile.mktemp(suffix=".db"))
    store = EventStore(db_path)

    e1 = _make_engine_with_store("meanrev.daily.a.v.v1", "meanrev", store)
    e2 = _make_engine_with_store("meanrev.daily.b.v.v1", "meanrev", store)

    ctx = _make_ctx(
        {
            e1._loaded.config.strategy_id: e1,
            e2._loaded.config.strategy_id: e2,
        }
    )

    run_batch(
        strategy_ids=[e1._loaded.config.strategy_id, e2._loaded.config.strategy_id],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
    )

    runs = store.list_backtest_runs()
    assert len(runs) == 2
    strategy_ids_in_db = {r.strategy_id for r in runs}
    assert strategy_ids_in_db == {
        e1._loaded.config.strategy_id,
        e2._loaded.config.strategy_id,
    }
    for run in runs:
        assert run.status == "completed"


def test_research_screen_persists_row_with_research_screen_source():
    """Rows written by research screen are tagged with source='research_screen'.

    This allows post-hoc queries to distinguish screen runs from single-
    strategy walk-forward runs invoked via ``milodex backtest``.
    """
    db_path = Path(tempfile.mktemp(suffix=".db"))
    store = EventStore(db_path)

    engine = _make_engine_with_store("meanrev.daily.a.v.v1", "meanrev", store)
    ctx = _make_ctx({engine._loaded.config.strategy_id: engine})

    run_batch(
        strategy_ids=[engine._loaded.config.strategy_id],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
    )

    runs = store.list_backtest_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.metadata.get("source") == "research_screen", (
        f"Expected metadata['source'] == 'research_screen', got: {run.metadata}"
    )


def test_research_screen_persists_row_per_strategy_with_completed_status():
    """All runs complete (not 'running' or 'failed') after a successful screen."""
    db_path = Path(tempfile.mktemp(suffix=".db"))
    store = EventStore(db_path)

    engine = _make_engine_with_store("meanrev.daily.a.v.v1", "meanrev", store)
    ctx = _make_ctx({engine._loaded.config.strategy_id: engine})

    run_batch(
        strategy_ids=[engine._loaded.config.strategy_id],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
    )

    runs = store.list_backtest_runs()
    assert len(runs) >= 1
    for run in runs:
        assert run.status == "completed", f"Expected 'completed', got '{run.status}'"


def test_research_screen_error_strategy_still_records_other_strategies():
    """A strategy error (without fail_fast) must not prevent other rows persisting.

    The screen records an error row in the BatchResult; the DB should still
    have a completed row for the strategy that succeeded.
    """
    db_path = Path(tempfile.mktemp(suffix=".db"))
    store = EventStore(db_path)

    good_engine = _make_engine_with_store("good.strat", "meanrev", store)

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.config_dir = Path(tempfile.mkdtemp())

    def get_engine(sid, **kwargs):
        if sid == "bad.strat":
            raise RuntimeError("config load failed")
        return good_engine

    ctx.get_backtest_engine = get_engine

    result = run_batch(
        strategy_ids=["bad.strat", "good.strat"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        ctx=ctx,
        fail_fast=False,
    )

    # bad strategy surfaces as error row in result
    bad_row = next(r for r in result.rows if r.strategy_id == "bad.strat")
    assert bad_row.error is not None

    # good strategy persisted
    runs = store.list_backtest_runs()
    assert any(r.strategy_id == "good.strat" and r.status == "completed" for r in runs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(engines: dict):
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.config_dir = Path(tempfile.mkdtemp())
    ctx.get_backtest_engine = lambda sid, **kwargs: engines[sid]
    return ctx

"""The paper runner sizes its history window with the engine's warmup rule (C1 PR 7, #396).

The runner kept a private copy of the engine's heuristic: integer params only, no
strategy-declared lookback, no cap. ``_history_window_days`` now calls the shared
``sessions.warmup_calendar_days``; these hold it to the engine's own
``warmup_calendar_days()`` over the same loaded strategy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.core.event_store import EventStore
from milodex.strategies.runner import StrategyRunner

CONFIGS = Path(__file__).resolve().parents[3] / "configs"
REGIME_ID = "regime.daily.sma200_rotation.spy_shy.v1"  # also the strategy_config_dir fixture's id


def _runner(tmp_path: Path, config_dir: Path, strategy_id: str) -> StrategyRunner:
    return StrategyRunner(
        strategy_id=strategy_id,
        config_dir=config_dir,
        broker_client=MagicMock(),
        data_provider=MagicMock(),
        execution_service=MagicMock(),
        event_store=EventStore(tmp_path / "milodex.db"),
    )


def _engine_window(runner: StrategyRunner) -> int:
    """The engine's own warmup, built over the runner's loaded strategy."""
    engine = BacktestEngine(
        loaded=runner._loaded,
        data_provider=MagicMock(),
        event_store=runner._event_store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    return engine.warmup_calendar_days()


@pytest.mark.parametrize(
    ("strategy_id", "declared", "expected"),
    [
        (REGIME_ID, 0, 600),  # daily 200-DMA: 3 x ma_filter_length, same as the old rule
        ("gap.gap_continuation.intraday.spy.v1", 156, 249),  # ceil(156 * 1.4) + 30
        # Old rule: `seed` x 3 = 60,781,857 days, an OverflowError at ``end - timedelta(...)``.
        ("benchmark.random_matched_exposure.intraday.spy.v1", 78, 140),
        # Old rule: entry_window_minutes (300) x 3 = 900 days of 5Min bars on every poll.
        ("meanrev.rsi2.intraday.spy.v1", 78, 140),
    ],
)
def test_real_config_window_matches_the_engine(
    strategy_id: str, declared: int, expected: int, tmp_path: Path
):
    runner = _runner(tmp_path, CONFIGS, strategy_id)

    assert runner._loaded.strategy.max_lookback_periods() == declared
    assert runner._history_window_days() == _engine_window(runner) == expected


def test_whole_float_param_counts_and_bool_param_does_not(
    tmp_path: Path, strategy_config_dir: Path
):
    path = strategy_config_dir / "regime_runner.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "    allocation_pct: 1.0\n",
            "    allocation_pct: 1.0\n    warm_bars: 400.0\n    use_filter: true\n",
        ),
        encoding="utf-8",
    )
    runner = _runner(tmp_path, strategy_config_dir, REGIME_ID)
    parameters = runner._loaded.config.parameters
    assert parameters["warm_bars"] == 400.0
    assert parameters["use_filter"] is True

    # 400.0 x 3; the old rule skipped floats and sized this config at 365.
    assert runner._history_window_days() == _engine_window(runner) == 1200

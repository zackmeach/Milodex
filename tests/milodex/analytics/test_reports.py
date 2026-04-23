"""Tests for trust report assembly."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from milodex.analytics.metrics import compute_metrics
from milodex.analytics.reports import assemble_trust_report
from milodex.analytics.snapshots import record_daily_snapshot
from milodex.broker.models import AccountInfo
from milodex.broker.simulated import SimulatedBroker
from milodex.core.event_store import EventStore


def _flat_metrics(strategy_id: str, initial_equity: float = 100_000.0, trade_count: int = 0):
    start = date(2024, 1, 1)
    curve = [(start + timedelta(days=i), initial_equity) for i in range(252)]
    # Generate `trade_count` synthetic buy/sell pairs so confidence labels can be tested.
    trades = []
    d = start
    for _ in range(trade_count // 2):
        trades.append(
            {
                "symbol": "SPY",
                "side": "buy",
                "quantity": 1.0,
                "estimated_unit_price": 100.0,
                "recorded_at": d.isoformat(),
            }
        )
        trades.append(
            {
                "symbol": "SPY",
                "side": "sell",
                "quantity": 1.0,
                "estimated_unit_price": 100.0,
                "recorded_at": (d + timedelta(days=1)).isoformat(),
            }
        )
        d += timedelta(days=2)
    return compute_metrics(
        run_id="run-1",
        strategy_id=strategy_id,
        start_date=start,
        end_date=start + timedelta(days=251),
        initial_equity=initial_equity,
        equity_curve=curve,
        trades=trades,
    )


def test_assemble_trust_report_without_benchmark(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    metrics = _flat_metrics("regime.v1", trade_count=0)

    report = assemble_trust_report(metrics=metrics, event_store=store, include_benchmark=False)

    assert report.run_id == "run-1"
    assert report.strategy_id == "regime.v1"
    assert report.benchmark is None
    assert report.total_return_vs_benchmark_pct is None
    assert report.max_drawdown_vs_benchmark_pct is None
    assert report.snapshot_summary.snapshot_count == 0
    assert report.confidence_label == "insufficient_data"
    # Expect an open question for snapshots, benchmark, and insufficient data
    assert any("30-trade floor" in q for q in report.open_questions)
    assert any("Benchmark comparison unavailable" in q for q in report.open_questions)
    assert any("No portfolio snapshots" in q for q in report.open_questions)


def test_assemble_trust_report_surfaces_snapshot_summary(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    metrics = _flat_metrics("regime.v1")

    broker = SimulatedBroker(slippage_pct=0.0, commission_per_trade=0.0)
    broker.update_account(
        AccountInfo(
            equity=100_000.0,
            cash=100_000.0,
            buying_power=100_000.0,
            portfolio_value=100_000.0,
            daily_pnl=0.0,
        )
    )
    broker.set_positions([])
    record_daily_snapshot(
        store,
        broker,
        session_id="sess-1",
        strategy_id="regime.v1",
        recorded_at=datetime(2024, 1, 2, 16, 0),
    )
    broker.update_account(
        AccountInfo(
            equity=101_000.0,
            cash=101_000.0,
            buying_power=101_000.0,
            portfolio_value=101_000.0,
            daily_pnl=1_000.0,
        )
    )
    record_daily_snapshot(
        store,
        broker,
        session_id="sess-1",
        strategy_id="regime.v1",
        recorded_at=datetime(2024, 1, 3, 16, 0),
    )

    report = assemble_trust_report(metrics=metrics, event_store=store, include_benchmark=False)

    assert report.snapshot_summary.snapshot_count == 2
    assert report.snapshot_summary.first_equity == 100_000.0
    assert report.snapshot_summary.last_equity == 101_000.0
    assert not any("No portfolio snapshots" in q for q in report.open_questions)


def test_assemble_trust_report_computes_benchmark_delta(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    metrics = _flat_metrics("regime.v1")

    class _FakeBarSet:
        def __init__(self, df):
            self._df = df

        def __len__(self):
            return len(self._df)

        def to_dataframe(self):
            return self._df

    class _StubProvider:
        def get_bars(self, *, symbols, timeframe, start, end):
            import pandas as pd

            df = pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(
                        ["2024-01-01", "2024-01-02", "2024-01-03"], utc=True
                    ),
                    "close": [100.0, 110.0, 120.0],
                }
            )
            return {"SPY": _FakeBarSet(df)}

    report = assemble_trust_report(
        metrics=metrics,
        event_store=store,
        data_provider=_StubProvider(),
        include_benchmark=True,
    )

    assert report.benchmark is not None
    # Strategy flat (0%), benchmark +20% → delta = -20
    assert report.total_return_vs_benchmark_pct is not None
    assert report.total_return_vs_benchmark_pct < 0
    assert report.max_drawdown_vs_benchmark_pct is not None


def test_assemble_trust_report_surfaces_recent_decisions(tmp_path: Path) -> None:
    """R-XC-008 step E: recent reasoning narratives ride the trust report."""
    from datetime import UTC

    from milodex.core.event_store import ExplanationEvent

    store = EventStore(tmp_path / "milodex.db")
    metrics = _flat_metrics("regime.v1")

    def _append(rule: str, narrative: str, recorded_at: datetime) -> None:
        store.append_explanation(
            ExplanationEvent(
                recorded_at=recorded_at,
                decision_type="no_trade",
                status="no_signal",
                strategy_name="regime.v1",
                strategy_stage="paper",
                strategy_config_path="configs/x.yaml",
                config_hash="abc",
                symbol="SPY",
                side="hold",
                quantity=0.0,
                order_type="none",
                time_in_force="day",
                submitted_by="strategy_runner",
                market_open=True,
                latest_bar_timestamp=recorded_at,
                latest_bar_close=100.0,
                account_equity=100_000.0,
                account_cash=100_000.0,
                account_portfolio_value=100_000.0,
                account_daily_pnl=0.0,
                risk_allowed=True,
                risk_summary="",
                reason_codes=[],
                risk_checks=[],
                context={"reasoning": {"rule": rule, "narrative": narrative}},
            )
        )

    base = datetime(2024, 1, 2, 16, 0, tzinfo=UTC)
    _append("regime.hold", "already in SHY", base)
    _append("regime.ma_filter_cross", "latest close above 200-DMA → SPY", base + timedelta(days=1))
    # An unrelated strategy's row should NOT appear.
    store.append_explanation(
        ExplanationEvent(
            recorded_at=base + timedelta(days=2),
            decision_type="no_trade",
            status="no_signal",
            strategy_name="other.strategy.v1",
            strategy_stage="paper",
            strategy_config_path="configs/y.yaml",
            config_hash="def",
            symbol="SPY",
            side="hold",
            quantity=0.0,
            order_type="none",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=base,
            latest_bar_close=100.0,
            account_equity=100_000.0,
            account_cash=100_000.0,
            account_portfolio_value=100_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="",
            reason_codes=[],
            risk_checks=[],
            context={"reasoning": {"rule": "x", "narrative": "y"}},
        )
    )

    report = assemble_trust_report(metrics=metrics, event_store=store, include_benchmark=False)

    assert len(report.recent_decisions) == 2
    rules = [d.rule for d in report.recent_decisions]
    assert rules == ["regime.hold", "regime.ma_filter_cross"]
    assert report.recent_decisions[-1].narrative.startswith("latest close above")

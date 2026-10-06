"""same_session enforcement in the paper runner (V5, C1 PR 2, ADR 0059).

A ``same_session`` runner sells every open lot through ``submit_paper`` at the flatten
deadline (min(table close, broker next_close) - 5 min) or, for a carried lot, at the next
open; it fetches and evaluates only inside ``[session open, deadline)`` while the market is
open, sees only completed regular-hours bars, and never evaluates a previous session's bar.

The runner clock is faked at fixed ET instants; the session table is the real committed XNYS
calendar (a literal one for the coverage path). Fixtures come from ``test_runner``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import AccountInfo, OrderSide, OrderType, Position, TimeInForce
from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.data.models import BarSet
from milodex.data.sessions import ET, SessionCalendar, session_day
from milodex.execution.state import KillSwitchStateStore
from milodex.risk.attribution import strategy_open_lots
from milodex.strategies.base import DecisionReasoning, StrategyDecision
from milodex.strategies.runner import StrategyRunner
from tests.milodex._helpers.promotion import seed_frozen_manifest
from tests.milodex.strategies.test_runner import StubBroker, StubProvider, build_service

STRATEGY_ID = "regime.daily.sma200_rotation.spy_shy.v1"
EDT_DAY = date(2026, 7, 15)  # Wednesday, regular session (UTC-4)
EDT_NEXT = date(2026, 7, 16)
EST_DAY = date(2026, 1, 14)  # Wednesday, regular session (UTC-5)
HALF_DAY = date(2026, 11, 27)  # day after Thanksgiving, 13:00 close
LOT_QTY = 10.0


def et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    """An ET wall-clock instant as aware UTC (the runner's clock convention)."""
    return datetime.combine(day, time(hour, minute, second), tzinfo=ET).astimezone(UTC)


class ClockBroker(StubBroker):
    """StubBroker plus the broker clock's ``next_close`` read (counted)."""

    def __init__(self, *, next_close: datetime | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.next_close_value = next_close
        self.next_close_calls = 0

    def next_close(self) -> datetime | None:
        self.next_close_calls += 1
        return self.next_close_value


@dataclass
class Harness:
    runner: StrategyRunner
    broker: ClockBroker
    provider: StubProvider
    event_store: EventStore
    kill_switch: KillSwitchStateStore
    clock: list[datetime]
    events: list[str] = field(default_factory=list)  # "submit" / "fetch" / "evaluate", in order
    submits: list[tuple] = field(default_factory=list)  # (intent, kwargs) seen by submit_paper
    evaluated: list[dict] = field(default_factory=list)  # per evaluation: bar starts + positions

    def cycle(self, at: datetime, *, market_open: bool = True) -> list:
        self.clock[0] = at
        self.broker._market_open = market_open
        return self.runner.run_cycle()

    def flatten_alerts(self) -> list:
        return self.event_store.list_operator_alerts(alert_type="session_end_flatten_blocked")


def _bars(*days: date) -> BarSet:
    """5Min bars 08:00-16:55 ET (pre-, regular and post-market) on each day."""
    stamps = [
        ts for day in days for ts in pd.date_range(et(day, 8, 0), et(day, 16, 55), freq="5min")
    ]
    closes = [10.0] * len(stamps)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": pd.DatetimeIndex(stamps),
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * len(stamps),
                "vwap": closes,
            }
        )
    )


def _seed_lot(event_store: EventStore, opened_at: datetime) -> None:
    """A submitted paper BUY of LOT_QTY SPY by the strategy: one open ledger lot."""
    explanation = ExplanationEvent(
        recorded_at=opened_at,
        decision_type="submit",
        status="submitted",
        strategy_name=STRATEGY_ID,
        strategy_stage="paper",
        strategy_config_path="configs/regime.yaml",
        config_hash="abc123",
        symbol="SPY",
        side="buy",
        quantity=LOT_QTY,
        order_type="market",
        time_in_force="day",
        submitted_by="strategy_runner",
        market_open=True,
        latest_bar_timestamp=opened_at,
        latest_bar_close=10.0,
        account_equity=10_000.0,
        account_cash=10_000.0,
        account_portfolio_value=10_000.0,
        account_daily_pnl=0.0,
        risk_allowed=True,
        risk_summary="OK",
        reason_codes=[],
        risk_checks=[],
        context={},
        session_id="seed-session",
    )
    trade = TradeEvent(
        explanation_id=0,
        recorded_at=opened_at,
        status="submitted",
        source="paper",
        symbol="SPY",
        side="buy",
        quantity=LOT_QTY,
        order_type="market",
        time_in_force="day",
        estimated_unit_price=10.0,
        estimated_order_value=LOT_QTY * 10.0,
        strategy_name=STRATEGY_ID,
        strategy_stage="paper",
        strategy_config_path="configs/regime.yaml",
        submitted_by="strategy_runner",
        broker_order_id=None,
        broker_status=None,
        message=None,
    )
    event_store.append_explanation_and_trade(explanation=explanation, trade=trade)


@pytest.fixture()
def harness(tmp_path: Path, strategy_config_dir: Path, risk_defaults_file: Path):
    def build(
        *,
        days: tuple[date, ...],
        lifecycle: str | None = "same_session",
        lot_opened_at: datetime | None = None,
        next_close: datetime | None = None,
    ) -> Harness:
        config_path = strategy_config_dir / "regime_runner.yaml"
        if lifecycle is not None:  # None keeps the fixture's daily (1D) config
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    'bar_size: "1D"', f'bar_size: "5Min"\n    position_lifecycle: "{lifecycle}"'
                ),
                encoding="utf-8",
            )
        # The non-1D staleness veto ages the provider's latest bar against the REAL now;
        # these fixed historical days need a wider budget than the fixture's ~11.6 days.
        risk_defaults_file.write_text(
            risk_defaults_file.read_text(encoding="utf-8").replace(
                "max_data_staleness_seconds: 999999", "max_data_staleness_seconds: 999999999"
            ),
            encoding="utf-8",
        )
        bars = _bars(*days)
        provider = StubProvider({"SPY": bars, "SHY": bars})
        held = [] if lot_opened_at is None else [_spy_position()]
        broker = ClockBroker(
            next_close=next_close,
            account=AccountInfo(
                equity=10_000.0,
                cash=9_900.0,
                buying_power=9_900.0,
                portfolio_value=10_000.0,
                daily_pnl=0.0,
            ),
            positions=held,
        )
        service, event_store, kill_switch = build_service(
            tmp_path=tmp_path,
            broker=broker,
            provider=provider,
            risk_defaults_file=risk_defaults_file,
        )
        seed_frozen_manifest(event_store, config_path)
        if lot_opened_at is not None:
            _seed_lot(event_store, lot_opened_at)
        runner = StrategyRunner(
            strategy_id=STRATEGY_ID,
            config_dir=strategy_config_dir,
            broker_client=broker,
            data_provider=provider,
            execution_service=service,
            event_store=event_store,
        )
        h = Harness(runner, broker, provider, event_store, kill_switch, clock=[et(days[0], 9, 0)])
        runner._now = lambda: h.clock[0]

        real_submit = service.submit_paper

        def submit_paper(intent, **kwargs):
            h.events.append("submit")
            h.submits.append((intent, kwargs))
            return real_submit(intent, **kwargs)

        service.submit_paper = submit_paper
        real_get_bars = provider.get_bars

        def get_bars(*args, **kwargs):
            h.events.append("fetch")
            return real_get_bars(*args, **kwargs)

        provider.get_bars = get_bars

        def evaluate(primary_bars, context):
            h.events.append("evaluate")
            h.evaluated.append(
                {
                    "starts": {
                        symbol: list(barset.to_dataframe()["timestamp"])
                        for symbol, barset in context.bars_by_symbol.items()
                    },
                    "positions": dict(context.positions),
                }
            )
            return StrategyDecision(
                intents=[], reasoning=DecisionReasoning(rule="no_signal", narrative="test")
            )

        runner._loaded.strategy.evaluate = evaluate
        return h

    return build


def _spy_position() -> Position:
    return Position(
        symbol="SPY",
        quantity=LOT_QTY,
        avg_entry_price=10.0,
        current_price=10.0,
        market_value=LOT_QTY * 10.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


@pytest.mark.parametrize(
    ("day", "deadline_hm"),
    [
        pytest.param(EDT_DAY, (15, 55), id="edt"),
        pytest.param(EST_DAY, (15, 55), id="est"),
        pytest.param(HALF_DAY, (12, 55), id="half_day_1255"),
    ],
)
def test_flatten_sells_the_whole_lot_through_submit_paper_at_the_deadline(
    harness, day, deadline_hm
):
    h = harness(days=(day,), lot_opened_at=et(day, 10, 0))
    deadline = et(day, *deadline_hm)

    h.cycle(deadline - timedelta(seconds=1))
    assert h.submits == []
    assert h.events == ["fetch", "evaluate"], "evaluation still runs just before the deadline"

    assert h.cycle(deadline) == []

    assert h.events == ["fetch", "evaluate", "submit"], "the deadline cycle never fetches"
    [(intent, kwargs)] = h.submits
    assert (intent.symbol, intent.side, intent.quantity) == ("SPY", OrderSide.SELL, LOT_QTY)
    assert (intent.order_type, intent.time_in_force) == (OrderType.MARKET, TimeInForce.DAY)
    assert intent.submitted_by == "strategy_runner"
    # Ordinary risk evaluation: no idempotency key, bar override or pricing override.
    assert set(kwargs) == {"session_id", "reasoning"}
    reasoning = kwargs["reasoning"]
    assert reasoning.rule == "paper.session_end_flatten"
    assert dict(reasoning.triggering_values) == {
        "basis": "deadline",
        "deadline_utc": deadline.isoformat(),
        "table_close_utc": (deadline + timedelta(minutes=5)).isoformat(),
        "broker_next_close_utc": None,
        "flatten_lead_minutes": 5,
        "opened_on": day.isoformat(),
    }
    assert [call["side"] for call in h.broker.submit_calls] == [OrderSide.SELL]
    assert strategy_open_lots(STRATEGY_ID, h.event_store) == {}
    assert h.flatten_alerts() == []


def test_nothing_is_fetched_or_evaluated_at_or_after_the_deadline(harness):
    h = harness(days=(EDT_DAY,))

    for at in (et(EDT_DAY, 15, 55), et(EDT_DAY, 15, 57, 30), et(EDT_DAY, 15, 59, 59)):
        assert h.cycle(at) == []
    for at in (et(EDT_DAY, 16, 0, 30), et(EDT_DAY, 20, 0)):
        assert h.cycle(at, market_open=False) == []

    assert h.provider.get_bars_calls == []
    assert h.events == []


def test_carried_lot_is_sold_at_the_next_open_before_evaluation(harness):
    h = harness(days=(EDT_DAY, EDT_NEXT), lot_opened_at=et(EDT_DAY, 10, 0))

    h.cycle(et(EDT_NEXT, 9, 0), market_open=False)
    assert h.events == [], "nothing is submitted or fetched before the open"

    h.cycle(et(EDT_NEXT, 9, 35, 5))  # today's 09:30 bar has completed

    assert h.events == ["submit", "fetch", "evaluate"]
    [(intent, kwargs)] = h.submits
    assert (intent.side, intent.quantity) == (OrderSide.SELL, LOT_QTY)
    triggering = kwargs["reasoning"].triggering_values
    assert (triggering["basis"], triggering["opened_on"]) == ("overdue", EDT_DAY.isoformat())
    assert h.evaluated[0]["positions"] == {}, "the strategy evaluates already flat"


def test_vetoed_flatten_retries_every_open_cycle_but_never_after_the_close(harness):
    h = harness(days=(EDT_DAY, EDT_NEXT), lot_opened_at=et(EDT_DAY, 10, 0))
    h.kill_switch.activate("test: the risk layer vetoes every submit")

    for at in (et(EDT_DAY, 15, 55), et(EDT_DAY, 15, 57), et(EDT_DAY, 15, 59, 50)):
        h.cycle(at)
    assert len(h.submits) == 3, "a vetoed flatten is retried on every open cycle"
    assert h.broker.submit_calls == []
    [alert] = h.flatten_alerts()
    assert (alert.severity, alert.symbol, alert.side) == ("warning", "SPY", "sell")
    assert alert.context_json["reason"] == "blocked"
    assert "kill_switch_active" in alert.context_json["reason_codes"]
    assert alert.context_json["session_day"] == EDT_DAY.isoformat()

    for at in (et(EDT_DAY, 16, 0, 10), et(EDT_DAY, 21, 0), et(EDT_NEXT, 9, 25)):
        h.cycle(at, market_open=False)
    assert len(h.submits) == 3, "nothing is submitted while the market is closed"
    assert strategy_open_lots(STRATEGY_ID, h.event_store)["SPY"]["quantity"] == LOT_QTY

    h.cycle(et(EDT_NEXT, 9, 30, 5))
    assert len(h.submits) == 4
    assert h.submits[-1][1]["reasoning"].triggering_values["basis"] == "overdue"
    assert len(h.flatten_alerts()) == 2, "one alert per lot per ET session"

    h.kill_switch.reset()
    h.cycle(et(EDT_NEXT, 9, 30, 20))
    assert [call["side"] for call in h.broker.submit_calls] == [OrderSide.SELL]
    assert strategy_open_lots(STRATEGY_ID, h.event_store) == {}
    assert len(h.flatten_alerts()) == 2


def test_a_raising_flatten_submit_alerts_once_and_is_retried(harness):
    h = harness(days=(EDT_DAY,), lot_opened_at=et(EDT_DAY, 10, 0))
    real_submit = h.runner._execution_service.submit_paper
    failures = [RuntimeError("broker connection reset mid-submit")] * 2

    def flaky(intent, **kwargs):
        if failures:
            h.submits.append((intent, kwargs))
            raise failures.pop()
        return real_submit(intent, **kwargs)

    h.runner._execution_service.submit_paper = flaky

    for at in (et(EDT_DAY, 15, 55), et(EDT_DAY, 15, 55, 10), et(EDT_DAY, 15, 55, 20)):
        h.cycle(at)

    [alert] = h.flatten_alerts()
    assert alert.context_json["reason"].startswith("submit_error")
    assert [call["side"] for call in h.broker.submit_calls] == [OrderSide.SELL]
    assert strategy_open_lots(STRATEGY_ID, h.event_store) == {}


def test_premarket_bars_and_the_previous_session_bar_are_never_evaluated(harness):
    yesterday = date(2026, 7, 14)
    h = harness(days=(yesterday, EDT_DAY))

    h.cycle(et(EDT_DAY, 9, 20))  # even if the broker flag says open, the session has not
    assert h.events == []

    # Fresh start inside [open, open + bar): the newest completed regular-hours bar is
    # yesterday's 15:55 bar, which paper never evaluated (it completes after the deadline).
    fixture = pd.DatetimeIndex(h.provider._bars_by_symbol["SPY"].to_dataframe()["timestamp"])
    newest = fixture[h.runner._session_policy.visible(fixture, et(EDT_DAY, 9, 31))][-1]
    assert (session_day(newest), newest) == (yesterday, et(yesterday, 15, 55))
    h.cycle(et(EDT_DAY, 9, 31))
    assert h.events == ["fetch"], "a previous session's bar is never evaluated"

    h.cycle(et(EDT_DAY, 9, 35))
    assert h.events == ["fetch", "fetch", "evaluate"]
    [seen] = h.evaluated
    for symbol, starts in seen["starts"].items():
        eastern = pd.DatetimeIndex(starts).tz_convert(ET)
        minutes = eastern.hour * 60 + eastern.minute
        assert ((minutes >= 9 * 60 + 30) & (minutes <= 15 * 60 + 55)).all(), symbol
        assert starts[-1] == et(EDT_DAY, 9, 30), symbol
        assert session_day(starts[-1]) == EDT_DAY, symbol


def test_earlier_broker_next_close_moves_the_deadline_earlier(harness):
    h = harness(days=(EDT_DAY,), lot_opened_at=et(EDT_DAY, 10, 0), next_close=et(EDT_DAY, 14, 0))

    h.cycle(et(EDT_DAY, 13, 54, 59))
    assert h.submits == []
    assert h.events == ["fetch", "evaluate"]

    h.cycle(et(EDT_DAY, 13, 55))

    assert h.events == ["fetch", "evaluate", "submit"]
    triggering = h.submits[0][1]["reasoning"].triggering_values
    assert triggering["deadline_utc"] == et(EDT_DAY, 13, 55).isoformat()
    assert triggering["broker_next_close_utc"] == et(EDT_DAY, 14, 0).isoformat()
    assert triggering["table_close_utc"] == et(EDT_DAY, 16, 0).isoformat()
    assert h.broker.next_close_calls == 2


@pytest.mark.parametrize(
    ("day", "cause"),
    [
        # The committed table ends before today: CalendarCoverageError.
        pytest.param(EDT_DAY, "outside_calendar_coverage", id="coverage_error"),
        # Thanksgiving: the table says closed while the broker reports the market open.
        pytest.param(date(2026, 11, 26), "table_closed_while_market_open", id="table_closed"),
    ],
)
def test_no_trustworthy_deadline_fails_closed(harness, day, cause):
    h = harness(days=(day,), lot_opened_at=et(day, 10, 0))
    if cause == "outside_calendar_coverage":
        expired = SessionCalendar.from_rows(
            {
                "timezone": "America/New_York",
                "window_start": "2026-01-02",
                "window_end": "2026-06-30",
                "sessions": [],
            }
        )
        h.runner._session_policy = replace(h.runner._session_policy, calendar=expired)

    for at in (et(day, 11, 0), et(day, 11, 5)):
        assert h.cycle(at) == []

    assert h.events == ["submit"], "no fetch or evaluation; every open lot is flattened"
    triggering = h.submits[0][1]["reasoning"].triggering_values
    assert (triggering["basis"], triggering["deadline_utc"]) == ("calendar_fail_closed", None)
    assert strategy_open_lots(STRATEGY_ID, h.event_store) == {}
    [alert] = h.event_store.list_operator_alerts(alert_type="session_calendar_fail_closed")
    assert alert.severity == "warning"
    assert alert.context_json["cause"].startswith(cause)


@pytest.mark.parametrize("lifecycle", ["multi_session", None], ids=["multi_session", "daily"])
def test_non_same_session_runners_never_read_next_close_or_load_xnys(
    harness, monkeypatch, lifecycle
):
    def xnys(cls):
        raise AssertionError("a non-same_session runner loaded the XNYS table")

    monkeypatch.setattr(SessionCalendar, "xnys", classmethod(xnys))
    h = harness(days=(EDT_DAY,), lifecycle=lifecycle)

    h.cycle(et(EDT_DAY, 15, 57))
    h.cycle(et(EDT_DAY, 16, 30), market_open=False)

    assert h.broker.next_close_calls == 0
    assert h.runner._session_policy.calendar is None
    if lifecycle == "multi_session":
        # Unchanged multi_session behaviour: no deadline gate, extended-hours bars evaluated.
        assert h.events == ["fetch", "evaluate", "fetch", "evaluate"]

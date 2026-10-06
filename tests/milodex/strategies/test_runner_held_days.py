"""Paper ``held_days`` is the backtest kernel's count (C1 PR 3, #396 / V4).

The kernel ticks ``held_days`` once per outer bar day: at an evaluation on day D, a lot
filled on day F has ``|{d in bar days : F < d <= D}|``. The runner used to count UTC calendar
days from the lot's open date to *now*: max-hold exits fired 1-2 sessions early for Tue-Fri
fills, an evening lock-in (after 20:00 ET the UTC date has rolled) added a day, and the next
open's drain re-evaluation added another (three after a weekend). ``_build_entry_state`` now
counts bar days up to the evaluated bar's date and never reads the clock.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.broker.models import OrderSide, OrderType, Position, TimeInForce
from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies.base import DecisionReasoning, StrategyDecision
from tests.milodex.backtesting.test_engine_daily_regression import _make_loaded_strategy
from tests.milodex.strategies.test_runner import _build_lockin_runner, build_session_barset

STRATEGY_ID = "regime.daily.sma200_rotation.spy_shy.v1"  # the strategy_config_dir fixture's id


def _seed_fill(event_store: EventStore, *, symbol: str, side: str, day: date) -> None:
    """Append a submitted paper fill at 13:30Z on ``day`` (the at-open drain's submit time)."""
    at = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
    explanation_id = event_store.append_explanation(
        ExplanationEvent(
            recorded_at=at,
            decision_type="strategy_evaluate",
            status="approved",
            strategy_name=STRATEGY_ID,
            strategy_stage="paper",
            strategy_config_path="configs/regime.yaml",
            config_hash="abc123",
            symbol=symbol,
            side=side,
            quantity=10.0,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=at,
            latest_bar_close=100.0,
            account_equity=10_000.0,
            account_cash=9_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="OK",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id="test-session",
        )
    )
    event_store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=at,
            status="submitted",
            source="paper",
            symbol=symbol,
            side=side,
            quantity=10.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=1_000.0,
            strategy_name=STRATEGY_ID,
            strategy_stage="paper",
            strategy_config_path="configs/regime.yaml",
            submitted_by="strategy_runner",
            broker_order_id=None,
            broker_status=None,
            message=None,
        )
    )


def _through(bars: BarSet, day: date) -> BarSet:
    """The bars visible at an evaluation of ``day``'s session: everything dated <= day."""
    frame = bars.to_dataframe()
    return BarSet(frame[frame["timestamp"].dt.date <= day])


def _held(runner, bars: dict[str, BarSet], day: date, symbol: str = "SPY") -> int:
    visible = {sym: _through(b, day) for sym, b in bars.items()}
    return runner._build_entry_state(visible, day)[symbol]["held_days"]


class _EvalProbe:
    """Stands in for ``strategy.evaluate``: records ``entry_state``, emits fixed intents."""

    def __init__(self, intents: list[TradeIntent] | None = None) -> None:
        self.intents = intents or []
        self.seen: list[dict] = []

    def __call__(self, bars, context) -> StrategyDecision:
        self.seen.append(copy.deepcopy(context.entry_state))
        return StrategyDecision(
            intents=list(self.intents),
            reasoning=DecisionReasoning(rule="held_days_probe", narrative="held_days probe"),
        )


def _sell_spy() -> TradeIntent:
    return TradeIntent(
        symbol="SPY",
        side=OrderSide.SELL,
        quantity=10.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )


def _broker_holds_spy(broker) -> None:
    """Match the broker to the seeded ledger so the cycle's reconciliation stays quiet."""
    broker.positions = [
        Position(
            symbol="SPY",
            quantity=10.0,
            avg_entry_price=100.0,
            current_price=100.0,
            market_value=1_000.0,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
        )
    ]


# ---------------------------------------------------------------------------
# _build_entry_state: trading sessions, not calendar days
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fill", "exit_session"),
    [
        (date(2026, 7, 13), date(2026, 7, 20)),  # Mon fill -> next Mon
        (date(2026, 7, 14), date(2026, 7, 21)),  # Tue fill -> next Tue (calendar days: Mon, early)
        (date(2026, 7, 15), date(2026, 7, 22)),  # Wed fill -> next Wed (calendar days: Mon)
        (date(2026, 7, 16), date(2026, 7, 23)),  # Thu fill -> next Thu (calendar days: Tue)
        (date(2026, 7, 17), date(2026, 7, 24)),  # Fri fill -> next Fri (calendar days: Wed)
    ],
)
def test_max_hold_5_first_reached_five_sessions_after_the_fill(
    fill: date,
    exit_session: date,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """The kernel's ``held_days >= 5`` first holds at the 5th session after the fill."""
    runner, _, _, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )
    _seed_fill(event_store, symbol="SPY", side="buy", day=fill)
    bars = {"SPY": build_session_barset(date(2026, 7, 6), date(2026, 7, 31))}

    sessions_after_fill = [d.date() for d in pd.bdate_range(fill, date(2026, 7, 31))]
    first_exit_eval = next(d for d in sessions_after_fill if _held(runner, bars, d) >= 5)

    assert first_exit_eval == exit_session


def test_holiday_monday_is_not_counted(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, _, _, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )
    _seed_fill(event_store, symbol="SPY", side="buy", day=date(2026, 9, 4))  # Fri
    labor_day = date(2026, 9, 7)
    bars = {"SPY": build_session_barset(date(2026, 8, 31), date(2026, 9, 11), closed=(labor_day,))}

    # Tue 9/8 is the first session after the fill (4 calendar days); Wed 9/9 the second.
    assert _held(runner, bars, date(2026, 9, 8)) == 1
    assert _held(runner, bars, date(2026, 9, 9)) == 2


def test_bar_days_are_the_union_across_symbols_and_tolerate_empty_barsets(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """The kernel's outer days are every symbol's bar days; a no-data symbol adds none."""
    runner, _, _, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )
    _seed_fill(event_store, symbol="SPY", side="buy", day=date(2026, 7, 6))  # Mon
    spy = build_session_barset(date(2026, 7, 6), date(2026, 7, 10), closed=(date(2026, 7, 8),))
    shy = build_session_barset(date(2026, 7, 6), date(2026, 7, 10))
    # What a provider returns for a symbol with no data: untyped (object-dtype) columns.
    no_data = BarSet(
        pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])
    )

    state = runner._build_entry_state({"SPY": spy, "SHY": shy, "GONE": no_data}, date(2026, 7, 10))

    # Tue, Wed (SHY only), Thu, Fri.
    assert state["SPY"]["held_days"] == 4


def test_non_datetime_opened_at_keeps_the_legacy_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, _, _, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )
    lots = {"SPY": {"quantity": 1.0, "avg_entry_price": 100.0, "opened_at": None}}
    monkeypatch.setattr("milodex.strategies.runner.strategy_open_lots", lambda *_: lots)

    state = runner._build_entry_state({}, date(2026, 7, 14))

    assert state == {"SPY": {"entry_price": 100.0, "held_days": 0}}


# ---------------------------------------------------------------------------
# Call sites: run_cycle (lock-in) and the at-open drain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now",
    [datetime(2026, 7, 13, 21, 0, tzinfo=UTC), datetime(2026, 7, 14, 0, 30, tzinfo=UTC)],
    ids=["2100Z", "0030Z-next-utc-day"],
)
def test_post_close_lockin_held_days_does_not_depend_on_the_clock(
    now: datetime,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A lock-in after 20:00 ET (UTC date already rolled) counts the same as one at 17:00 ET."""
    runner, broker, _, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        initial_bars={
            "SPY": build_session_barset(date(2026, 7, 6), date(2026, 7, 13)),
            "SHY": build_session_barset(date(2026, 7, 6), date(2026, 7, 13)),
        },
    )
    _broker_holds_spy(broker)
    _seed_fill(event_store, symbol="SPY", side="buy", day=date(2026, 7, 7))  # Tue
    probe = _EvalProbe()
    runner._loaded.strategy.evaluate = probe
    runner._now = lambda: now

    runner.run_cycle()

    # Wed, Thu, Fri, Mon -- not 6 (calendar days at 21:00Z) or 7 (at 00:30Z).
    assert probe.seen == [{"SPY": {"entry_price": 100.0, "held_days": 4}}]


def test_friday_lockin_and_monday_drain_count_the_same(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    runner, broker, provider, event_store = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        initial_bars={
            "SPY": build_session_barset(date(2026, 7, 6), date(2026, 7, 10)),
            "SHY": build_session_barset(date(2026, 7, 6), date(2026, 7, 10)),
        },
    )
    _broker_holds_spy(broker)
    _seed_fill(event_store, symbol="SPY", side="buy", day=date(2026, 7, 7))  # Tue
    probe = _EvalProbe([_sell_spy()])
    runner._loaded.strategy.evaluate = probe

    # Friday post-close lock-in: two stable fetches 30s apart queue the exit.
    friday_close = datetime(2026, 7, 10, 21, 0, tzinfo=UTC)
    runner._now = lambda: friday_close
    runner.run_cycle()
    runner._now = lambda: friday_close + timedelta(seconds=30)
    runner.run_cycle()
    queued = event_store.get_active_queued_intents(
        runner._strategy_id, now=runner._now(), running_session_id=runner.session_id
    )
    assert len(queued) == 1
    lockin_seen = probe.seen
    probe.seen, probe.intents = [], []  # the drain's re-evaluation derives no exit: no submit

    # Monday open: the provider now also returns Monday's in-progress bar. The drain re-evaluates
    # through the locked Friday bar, so its count is Friday's, not Monday's.
    provider._bars_by_symbol = {
        "SPY": build_session_barset(date(2026, 7, 6), date(2026, 7, 13)),
        "SHY": build_session_barset(date(2026, 7, 6), date(2026, 7, 13)),
    }
    broker._market_open = True
    runner._now = lambda: datetime(2026, 7, 13, 13, 35, tzinfo=UTC)
    runner.run_cycle()

    friday = {"SPY": {"entry_price": 100.0, "held_days": 3}}  # Wed, Thu, Fri
    assert lockin_seen == [friday, friday]
    assert probe.seen == [friday]


# ---------------------------------------------------------------------------
# Parity: the runner's count equals the backtest kernel's at every evaluation
# ---------------------------------------------------------------------------

LABOR_DAY = date(2026, 9, 7)
SPY_GAP = date(2026, 9, 16)  # SHY has a bar, SPY does not: still an outer day for the kernel
WINDOW = (date(2026, 8, 17), date(2026, 9, 25))
BUY, SELL = OrderSide.BUY, OrderSide.SELL
# Decision session -> (symbol, side). Fills land the next session, so the BUYs fill on a
# Fri, Tue, Wed, Thu and Mon, lots span weekends and Labor Day, and two lots overlap.
SCRIPT = {
    date(2026, 8, 20): ("SPY", BUY),
    date(2026, 8, 24): ("SHY", BUY),
    date(2026, 8, 28): ("SPY", SELL),
    date(2026, 9, 1): ("SPY", BUY),
    date(2026, 9, 3): ("SHY", SELL),
    date(2026, 9, 9): ("SHY", BUY),
    date(2026, 9, 10): ("SPY", SELL),
    date(2026, 9, 11): ("SPY", BUY),
    date(2026, 9, 17): ("SHY", SELL),
}


def test_runner_held_days_match_the_backtest_kernel_at_every_evaluation(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Run the real engine on a fixture with weekends, a holiday and a one-symbol gap day, then
    replay its fills into the runner's ledger and compare ``held_days`` at each evaluation."""
    bars = {
        "SPY": build_session_barset(*WINDOW, closed=(LABOR_DAY, SPY_GAP)),
        "SHY": build_session_barset(*WINDOW, closed=(LABOR_DAY,)),
    }

    # --- the kernel side: a probe strategy records entry_state at every evaluation ---------
    evals: list[tuple[date, dict[str, int]]] = []

    def probe(_primary_bars, context) -> StrategyDecision:
        frames = [b.to_dataframe() for b in context.bars_by_symbol.values() if len(b)]
        day = max(f["timestamp"].max() for f in frames).date()
        evals.append((day, {s: int(v["held_days"]) for s, v in context.entry_state.items()}))
        intents = []
        if day in SCRIPT:
            symbol, side = SCRIPT[day]
            intents = [
                TradeIntent(symbol=symbol, side=side, quantity=10.0, order_type=OrderType.MARKET)
            ]
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(rule="held_days_probe", narrative="held_days probe"),
        )

    loaded = _make_loaded_strategy("regression.daily.held_days_probe.v1", ("SPY", "SHY"))
    loaded.strategy.evaluate.side_effect = probe
    provider = MagicMock()
    provider.get_bars.return_value = bars
    BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=EventStore(tmp_path / "engine" / "milodex.db"),
        initial_equity=100_000.0,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    ).run(*WINDOW)

    # The engine's fills, read off its own state: a symbol that appears in entry_state at an
    # evaluation was bought at that session's open; one that disappears was sold at it.
    fills: list[tuple[date, str, str]] = []
    held: set[str] = set()
    for day, state in evals:
        fills += [(day, "buy", s) for s in sorted(state.keys() - held)]
        fills += [(day, "sell", s) for s in sorted(held - state.keys())]
        held = set(state)
    assert len(fills) == len(SCRIPT), fills
    assert {d.weekday() for d, side, _ in fills if side == "buy"} == {0, 1, 2, 3, 4}

    # --- the runner side: same fills in the event store, same bars visible, same as_of ------
    runner, _, _, event_store = _build_lockin_runner(
        tmp_path=tmp_path / "runner",
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )
    opened: dict[str, date] = {}
    calendar_would_differ = 0
    compared = 0
    for day, kernel_state in evals:
        for fill_day, side, symbol in [f for f in fills if f[0] == day]:
            _seed_fill(event_store, symbol=symbol, side=side, day=fill_day)
            if side == "buy":
                opened[symbol] = fill_day
            else:
                del opened[symbol]
        if day == SPY_GAP:
            continue  # no SPY bar, so the runner (which evaluates on the primary's bars) is idle
        visible = {sym: _through(b, day) for sym, b in bars.items()}
        runner_state = {
            sym: s["held_days"] for sym, s in runner._build_entry_state(visible, day).items()
        }
        assert runner_state == kernel_state, f"held_days diverge at {day}"
        calendar_would_differ += kernel_state != {s: (day - d).days for s, d in opened.items()}
        compared += 1

    assert compared == len(evals) - 1
    assert calendar_would_differ > 0, "fixture no longer separates trading days from calendar days"

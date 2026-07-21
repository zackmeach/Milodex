"""Direct behavioral tests for each RiskEvaluator rule.

The execution-service tests already exercise risk via the submit path,
but several rules (daily-loss, order-value, single-position,
total-exposure, concurrent-positions) had no direct assertions. The
"risk layer is sacred" rule in `CLAUDE.md` / `AGENTS.md` is only
credible if every rule has a passing-case and a failing-case with an
expected reason code. That is what this file provides.

Rules exercised elsewhere (paper_mode, strategy_stage, market_hours,
data_staleness, duplicate_order) keep their coverage in
`tests/milodex/execution/test_service.py`. kill_switch is covered both
there (activation path) and here (DC-1 absolute-halt semantics).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.data.models import Bar
from milodex.execution.config import StrategyExecutionConfig
from milodex.execution.models import ExecutionRequest, TradeIntent
from milodex.execution.state import KillSwitchState
from milodex.risk import (
    EvaluationContext,
    ReconciliationReadiness,
    RiskCheckResult,
    RiskDecision,
    RiskDefaults,
    RiskEvaluator,
)

DEFAULT_RISK_DEFAULTS = RiskDefaults(
    kill_switch_enabled=True,
    kill_switch_max_drawdown_pct=0.10,
    require_manual_reset=True,
    max_single_position_pct=0.20,
    max_concurrent_positions=3,
    max_total_exposure_pct=0.80,
    max_daily_loss_pct=0.03,
    max_trades_per_day=20,
    max_order_value_pct=0.15,
    duplicate_order_window_seconds=60,
    max_data_staleness_seconds=300,
)
_DEFAULT_RECONCILIATION_READINESS = object()


def _fresh_bar() -> Bar:
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=10),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )


def _position(symbol: str, quantity: float, unit_price: float = 100.0) -> Position:
    market_value = quantity * unit_price
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=unit_price,
        current_price=unit_price,
        market_value=market_value,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def make_context(
    *,
    side: OrderSide = OrderSide.BUY,
    symbol: str = "SPY",
    quantity: float = 10.0,
    estimated_unit_price: float = 100.0,
    estimated_order_value: float | None = None,
    positions: Iterable[Position] = (),
    recent_orders: Iterable[Order] = (),
    account_portfolio_value: float = 10_000.0,
    account_daily_pnl: float = 0.0,
    risk_defaults: RiskDefaults = DEFAULT_RISK_DEFAULTS,
    strategy_config: StrategyExecutionConfig | None = None,
    preview_only: bool = False,
    market_open: bool = True,
    trading_mode: str = "paper",
    kill_switch_active: bool = False,
    latest_bar: Bar | None = None,
    latest_completed_session: date | None = None,
    runtime_config_hash: str | None = None,
    frozen_manifest_hash: str | None = None,
    expected_stage: str | None = None,
    expected_max_positions: int | None = None,
    expected_max_position_pct: float | None = None,
    expected_daily_loss_cap_pct: float | None = None,
    request_strategy_name: str | None = None,
    event_store=None,
    is_backtest: bool = False,
    reconciliation_readiness=_DEFAULT_RECONCILIATION_READINESS,
) -> EvaluationContext:
    """Build an ``EvaluationContext`` pre-configured to pass every rule
    except the one under test."""
    order_value = (
        estimated_order_value
        if estimated_order_value is not None
        else quantity * estimated_unit_price
    )
    intent = TradeIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )
    request = ExecutionRequest(
        symbol=symbol.upper(),
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        estimated_unit_price=estimated_unit_price,
        estimated_order_value=order_value,
        strategy_name=request_strategy_name,
    )
    account = AccountInfo(
        equity=account_portfolio_value,
        cash=account_portfolio_value,
        buying_power=account_portfolio_value,
        portfolio_value=account_portfolio_value,
        daily_pnl=account_daily_pnl,
    )
    # Promoted stages now require both manifest hashes (RuntimeError otherwise).
    # Default to matching dummy hashes when the caller hasn't specified — this
    # mirrors the production wiring in ExecutionService._evaluate. Tests that
    # exercise drift behavior pass explicit hash values and override these.
    if (
        strategy_config is not None
        and strategy_config.stage in {"paper", "micro_live", "live"}
        and runtime_config_hash is None
        and frozen_manifest_hash is None
    ):
        runtime_config_hash = "0" * 64
        frozen_manifest_hash = "0" * 64
    return EvaluationContext(
        intent=intent,
        request=request,
        account=account,
        positions=list(positions),
        recent_orders=list(recent_orders),
        reconciliation_readiness=(
            reconciliation_readiness
            if reconciliation_readiness is not _DEFAULT_RECONCILIATION_READINESS
            else ReconciliationReadiness(
                ready=True,
                reason_code=None,
                message="test clean reconciliation",
                broker_connected=True,
            )
        ),
        latest_bar=latest_bar or _fresh_bar(),
        market_open=market_open,
        latest_completed_session=latest_completed_session,
        trading_mode=trading_mode,
        preview_only=preview_only,
        kill_switch_state=KillSwitchState(active=kill_switch_active),
        risk_defaults=risk_defaults,
        strategy_config=strategy_config,
        runtime_config_hash=runtime_config_hash,
        frozen_manifest_hash=frozen_manifest_hash,
        expected_stage=expected_stage,
        expected_max_positions=expected_max_positions,
        expected_max_position_pct=expected_max_position_pct,
        expected_daily_loss_cap_pct=expected_daily_loss_cap_pct,
        event_store=event_store,
        is_backtest=is_backtest,
    )


def check_result(decision: RiskDecision, name: str) -> RiskCheckResult:
    for check in decision.checks:
        if check.name == name:
            return check
    msg = f"No check named '{name}' in decision: {[c.name for c in decision.checks]}"
    raise AssertionError(msg)


# --- _check_daily_loss -----------------------------------------------------


def test_daily_loss_passes_when_pnl_positive():
    decision = RiskEvaluator().evaluate(make_context(account_daily_pnl=150.0))

    assert check_result(decision, "daily_loss").passed is True


def test_daily_loss_passes_when_loss_within_cap():
    # Loss of $100 on a ~$10,100 pre-loss base = ~0.99% < 3% cap.
    decision = RiskEvaluator().evaluate(make_context(account_daily_pnl=-100.0))

    assert check_result(decision, "daily_loss").passed is True


def test_daily_loss_fails_when_loss_between_cap_and_kill_switch():
    # Loss of $400 on ~$10,400 pre-loss base ≈ 3.85%. Exceeds 3% cap,
    # below 10% kill switch threshold.
    decision = RiskEvaluator().evaluate(make_context(account_daily_pnl=-400.0))

    result = check_result(decision, "daily_loss")
    assert result.passed is False
    assert result.reason_code == "daily_loss_cap_exceeded"


def test_daily_loss_triggers_kill_switch_reason_when_over_kill_threshold():
    # Loss of $2,000 on ~$12,000 pre-loss base ≈ 16.7%. Well over 10%
    # kill-switch threshold; kill-switch reason takes precedence over
    # ordinary cap-exceeded reason.
    decision = RiskEvaluator().evaluate(make_context(account_daily_pnl=-2_000.0))

    result = check_result(decision, "daily_loss")
    assert result.passed is False
    assert result.reason_code == "kill_switch_threshold_breached"


def test_daily_loss_cap_tightened_by_strategy_config():
    strategy_config = StrategyExecutionConfig(
        name="tight_strategy",
        enabled=True,
        stage="paper",
        max_position_pct=0.20,
        max_positions=3,
        daily_loss_cap_pct=0.005,  # 0.5% — much tighter than 3%
        path=None,  # type: ignore[arg-type]
    )
    # Loss of $100 ≈ 0.99% of base — under global 3% cap but over the
    # strategy's 0.5% cap. Should fail via the effective-min rule.
    decision = RiskEvaluator().evaluate(
        make_context(account_daily_pnl=-100.0, strategy_config=strategy_config)
    )

    result = check_result(decision, "daily_loss")
    assert result.passed is False
    assert result.reason_code == "daily_loss_cap_exceeded"


# --- _check_order_value ----------------------------------------------------


def test_order_value_passes_below_cap():
    # cap = 10,000 * 0.15 = 1,500. Order value 1,000 is under.
    decision = RiskEvaluator().evaluate(make_context(estimated_order_value=1_000.0))

    assert check_result(decision, "order_value").passed is True


def test_order_value_passes_at_cap_exactly():
    # Rule uses strict `>`, so equality passes.
    decision = RiskEvaluator().evaluate(make_context(estimated_order_value=1_500.0))

    assert check_result(decision, "order_value").passed is True


def test_order_value_fails_above_cap():
    # Default side is BUY (exposure-increasing): the cap binds.
    decision = RiskEvaluator().evaluate(make_context(estimated_order_value=1_500.01))

    result = check_result(decision, "order_value")
    assert result.passed is False
    assert result.reason_code == "max_order_value_exceeded"


def test_order_value_reducing_sell_over_cap_is_exempt():
    # DC-1 (2026-06-10): the fat-finger cap targets oversized ENTRIES. A sell
    # covered by a broker-held long is exposure-reducing and bypasses the cap —
    # a held position must always be exitable, even past the cap.
    # cap = 10,000 * 0.15 = 1,500; full exit of 30 shares @ $100 = 3,000.
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=30.0,
            positions=[_position("SPY", quantity=30.0)],
            estimated_order_value=3_000.0,
        )
    )

    result = check_result(decision, "order_value")
    assert result.passed is True
    assert "exempt" in result.message


def test_order_value_sell_without_position_is_still_capped():
    # A sell with NO covering broker position is classified exposure-increasing
    # (short-opening) by is_exposure_increasing — no exemption, cap binds.
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=30.0,
            positions=[],
            estimated_order_value=3_000.0,
        )
    )

    result = check_result(decision, "order_value")
    assert result.passed is False
    assert result.reason_code == "max_order_value_exceeded"


def test_order_value_oversized_sell_beyond_held_is_still_capped():
    # A sell EXCEEDING the held quantity (sell-side fat-finger: 10,000 vs 100
    # held) is classified exposure-increasing (exposure.py: quantity > held) —
    # the exemption is unreachable and the cap binds. Pins the doctrine claim
    # "beyond the held quantity counts as increasing" to the code.
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=10_000.0,
            positions=[_position("SPY", quantity=100.0)],
            estimated_order_value=1_000_000.0,
        )
    )

    result = check_result(decision, "order_value")
    assert result.passed is False
    assert result.reason_code == "max_order_value_exceeded"


# --- _check_kill_switch ------------------------------------------------------


def test_kill_switch_blocks_reducing_sell():
    """R-EXE-016: an active kill switch blocks even an exposure-reducing sell."""
    # DC-1 (2026-06-10): the kill switch is an ABSOLUTE halt. The reducing-order
    # permissiveness applies to the order-value cap and the reconciliation gate,
    # NOT the kill switch — an active switch blocks even a covered reducing sell.
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=10.0,
            positions=[_position("SPY", quantity=30.0)],
            kill_switch_active=True,
        )
    )

    result = check_result(decision, "kill_switch")
    assert result.passed is False
    assert result.reason_code == "kill_switch_active"
    assert decision.allowed is False


# --- _check_single_position_limit ------------------------------------------


def test_single_position_passes_fresh_buy_below_cap():
    # Single-position cap = 10,000 * 0.20 = 2,000. Buy value = 1,500.
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=1_500.0,
            # Raise the order-value cap high enough to isolate this check.
            risk_defaults=_with_overrides(max_order_value_pct=0.50),
        )
    )

    assert check_result(decision, "single_position").passed is True


def test_single_position_fails_when_buy_adds_to_existing_over_cap():
    # Existing SPY position worth $1,500 + BUY adds $700 → projected
    # $2,200 > $2,000 cap.
    existing = _position("SPY", 15.0, 100.0)
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=700.0,
            positions=[existing],
            risk_defaults=_with_overrides(max_order_value_pct=0.50),
        )
    )

    result = check_result(decision, "single_position")
    assert result.passed is False
    assert result.reason_code == "max_single_position_exceeded"


def test_single_position_cap_tightened_by_strategy_config():
    strategy_config = StrategyExecutionConfig(
        name="narrow",
        enabled=True,
        stage="paper",
        max_position_pct=0.05,  # 5% → $500 cap
        max_positions=3,
        daily_loss_cap_pct=0.05,
        path=None,  # type: ignore[arg-type]
    )
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=600.0,
            strategy_config=strategy_config,
            risk_defaults=_with_overrides(max_order_value_pct=0.50),
        )
    )

    result = check_result(decision, "single_position")
    assert result.passed is False
    assert result.reason_code == "max_single_position_exceeded"


def test_single_position_allows_sell_that_reduces_position():
    existing = _position("SPY", 30.0, 100.0)  # current value $3,000 (over cap)
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            estimated_order_value=2_500.0,
            positions=[existing],
            risk_defaults=_with_overrides(max_order_value_pct=0.50),
            recent_orders=[],
        )
    )

    # Projected = max(0, 3000 - 2500) = 500, under $2,000 cap → pass.
    assert check_result(decision, "single_position").passed is True


def test_single_position_allows_exit_when_remainder_still_over_cap():
    # DC-1 (2026-07-14 XLF deadlock): a covered exit must PASS even when the
    # remaining position after the sell is STILL over the cap — otherwise an
    # over-cap position is unexitable. Reproduces the production case: 364sh XLF
    # @ ~$55.64 = ~$20.6k held, 10% cap on a ~$100k portfolio = ~$10.08k, SELL
    # 182sh leaves ~$10.13k > cap. Pre-fix this was vetoed
    # max_single_position_exceeded and retried every 60s for 26 minutes.
    held = _position("XLF", 364.0, 55.64)  # current value ~$20,253 (over cap)
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="XLF",
            side=OrderSide.SELL,
            quantity=182.0,
            estimated_unit_price=55.64,
            positions=[held],
            account_portfolio_value=100_820.0,  # 10% cap = $10,082
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )

    # Remainder after exit = 182 * 55.64 = $10,126.48 > $10,082 cap, yet the
    # covered exit is exposure-reducing and exempt.
    result = check_result(decision, "single_position")
    assert result.passed is True
    assert "exempt" in result.message


def test_single_position_oversized_sell_beyond_held_is_still_capped():
    # A sell EXCEEDING the held quantity opens a fresh short leg and must remain
    # capped (is_exposure_increasing: quantity > held). Held 10sh; SELL 500sh at
    # a price whose 490sh short leg blows the cap. The projected<=current
    # predicate would wrongly exempt this (the residual short leg is smaller than
    # the netted-off long); keying the exemption on is_exposure_increasing keeps
    # it capped.
    held = _position("SPY", 10.0, 100.0)  # current value $1,000
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=500.0,
            estimated_unit_price=100.0,
            estimated_order_value=50_000.0,
            positions=[held],
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )
    # excess 490sh * $100 = $49,000 short leg; projected $49,000 > $2,000 cap.
    result = check_result(decision, "single_position")
    assert result.passed is False
    assert result.reason_code == "max_single_position_exceeded"


def test_single_position_naked_short_exceeding_cap_is_still_capped():
    # A sell with NO covering position is short-opening (is_exposure_increasing:
    # held 0, qty > 0) — the exemption is unreachable and the cap binds.
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=30.0,
            estimated_unit_price=100.0,
            estimated_order_value=3_000.0,  # > $2,000 single-position cap
            positions=[],
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )
    result = check_result(decision, "single_position")
    assert result.passed is False
    assert result.reason_code == "max_single_position_exceeded"


def test_single_position_buy_growing_at_cap_position_is_still_capped():
    # A BUY is always exposure-increasing — a buy that grows an at/over-cap
    # position stays capped (the exemption never applies to entries).
    existing = _position("SPY", 20.0, 100.0)  # current value $2,000 (at cap)
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            estimated_order_value=500.0,  # projected $2,500 > $2,000 cap
            positions=[existing],
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )
    result = check_result(decision, "single_position")
    assert result.passed is False
    assert result.reason_code == "max_single_position_exceeded"


# --- _check_total_exposure -------------------------------------------------


def test_total_exposure_passes_fresh_buy_below_cap():
    # Cap = 10,000 * 0.80 = 8,000. Buy $1,000 → exposure 1,000.
    decision = RiskEvaluator().evaluate(make_context(estimated_order_value=1_000.0))

    assert check_result(decision, "total_exposure").passed is True


def test_total_exposure_fails_when_buy_pushes_over_cap():
    # Existing $7,500 exposure + $1,000 BUY = $8,500 > $8,000 cap.
    existing = _position("QQQ", 75.0, 100.0)  # $7,500
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=1_000.0,
            positions=[existing],
        )
    )

    result = check_result(decision, "total_exposure")
    assert result.passed is False
    assert result.reason_code == "max_total_exposure_exceeded"


def test_total_exposure_passes_exactly_at_cap():
    # Existing $7,500 + BUY $500 = $8,000 — equality passes.
    existing = _position("QQQ", 75.0, 100.0)
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=500.0,
            positions=[existing],
        )
    )

    assert check_result(decision, "total_exposure").passed is True


def test_total_exposure_allows_sell_that_reduces_exposure():
    # Existing $9,000 exposure (over cap already); SELL reduces to $6,500.
    existing = _position("QQQ", 90.0, 100.0)
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="QQQ",
            side=OrderSide.SELL,
            quantity=25.0,
            estimated_unit_price=100.0,
            positions=[existing],
        )
    )

    assert check_result(decision, "total_exposure").passed is True


def test_total_exposure_allows_covered_exit_when_remainder_still_over_cap():
    # DC-1 (mirrors _check_single_position_limit / _check_order_value): a covered
    # exit that REDUCES account exposure must never be blocked by the total-
    # exposure cap, even when the account sits so far over the cap that the
    # post-exit remainder is STILL over it. The A-6 increasing/reducing split
    # nets the covered notional to zero increasing, but projected = current -
    # reducing can still exceed the cap purely from the pre-existing over-cap
    # baseline — the exit is not the cause and must stay admissible.
    #
    # Reproduces the production deadlock (2026-07-21): the whole daily fleet's
    # open-drain exits were vetoed max_total_exposure_exceeded ~190x each because
    # Monday's entries pushed account exposure ~$19.6k over the ~$75.9k cap; each
    # covered exit reduced exposure but the ~$85k remainder still exceeded it, so
    # every held position was UNEXITABLE. Cap = 10,000 * 0.80 = 8,000.
    existing = _position("QQQ", 90.0, 100.0)  # $9,000 exposure (over the $8,000 cap)
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="QQQ",
            side=OrderSide.SELL,
            quantity=5.0,  # covered by the 90 held; reduces to $8,500 — STILL over cap
            estimated_unit_price=100.0,
            positions=[existing],
        )
    )

    result = check_result(decision, "total_exposure")
    assert result.passed is True
    assert "exempt" in result.message


def test_total_exposure_partial_covered_exit_over_cap_is_exempt():
    # Selling PART of a held long (5 of 90) is exposure-reducing and exempt even
    # though the account (two positions, $18,000) remains far over the $8,000 cap
    # after the exit. Pins that the exemption keys on the covered/held relation,
    # not on the post-exit total dipping under the cap.
    held_qqq = _position("QQQ", 90.0, 100.0)  # $9,000
    held_spy = _position("SPY", 90.0, 100.0)  # $9,000
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="QQQ",
            side=OrderSide.SELL,
            quantity=5.0,
            estimated_unit_price=100.0,
            positions=[held_qqq, held_spy],
        )
    )

    assert check_result(decision, "total_exposure").passed is True


# --- A-6: naked / over-held SELL exposure netting (FIX4) --------------------
# The single-position and total-exposure caps must not read a short or a
# sell-beyond-held as benign exposure-REDUCING notional. exposure_increasing_
# notional() nets the short leg the same way is_exposure_increasing() classifies
# it, so all three caps (order_value, single_position, total_exposure) agree on
# exposure direction. Covered exits still net DOWN (the regression guards above).


def _intent_and_request(side, symbol, quantity, unit_price=100.0):
    intent = TradeIntent(symbol=symbol, side=side, quantity=quantity, order_type=OrderType.MARKET)
    request = ExecutionRequest(
        symbol=symbol.upper(),
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        estimated_unit_price=unit_price,
        estimated_order_value=quantity * unit_price,
    )
    return intent, request


def test_exposure_increasing_notional_units():
    from milodex.risk.exposure import exposure_increasing_notional

    buy_i, buy_r = _intent_and_request(OrderSide.BUY, "SPY", 10.0)
    assert exposure_increasing_notional(buy_i, buy_r, []) == 1_000.0  # full notional

    naked_i, naked_r = _intent_and_request(OrderSide.SELL, "SPY", 10.0)
    assert exposure_increasing_notional(naked_i, naked_r, []) == 1_000.0  # naked short

    covered_i, covered_r = _intent_and_request(OrderSide.SELL, "SPY", 10.0)
    assert exposure_increasing_notional(covered_i, covered_r, [_position("SPY", 10.0)]) == 0.0

    over_i, over_r = _intent_and_request(OrderSide.SELL, "SPY", 30.0)
    # excess 20 shares * $100 = $2,000
    assert exposure_increasing_notional(over_i, over_r, [_position("SPY", 10.0)]) == 2_000.0


def test_exposure_increasing_notional_agrees_with_is_exposure_increasing():
    """The notional helper is positive iff is_exposure_increasing is True — the two
    cannot drift (they share _held_long_qty)."""
    from milodex.risk.exposure import exposure_increasing_notional, is_exposure_increasing

    cases = [
        (OrderSide.BUY, "SPY", 10.0, []),
        (OrderSide.SELL, "SPY", 10.0, []),  # naked
        (OrderSide.SELL, "SPY", 10.0, [_position("SPY", 10.0)]),  # covered exact
        (OrderSide.SELL, "SPY", 30.0, [_position("SPY", 10.0)]),  # over-held
        (OrderSide.SELL, "SPY", 5.0, [_position("SPY", 10.0)]),  # partial reduce
    ]
    for side, sym, qty, pos in cases:
        intent, request = _intent_and_request(side, sym, qty)
        assert (exposure_increasing_notional(intent, request, pos) > 0) == is_exposure_increasing(
            intent, pos
        )


def test_total_exposure_naked_sell_adds_short_leg_exposure():
    # Pre-fix the SELL subtracted notional -> projected 0 -> falsely passed.
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=90.0,
            estimated_unit_price=100.0,
            estimated_order_value=9_000.0,  # > $8,000 total cap
            positions=[],
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )
    result = check_result(decision, "total_exposure")
    assert result.passed is False
    assert result.reason_code == "max_total_exposure_exceeded"


def test_total_exposure_oversized_sell_beyond_held_counts_excess():
    held = _position("SPY", 50.0, 100.0)  # current exposure $5,000
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=200.0,
            estimated_unit_price=100.0,
            estimated_order_value=20_000.0,
            positions=[held],
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )
    # excess 150sh * $100 = $15,000 short leg; projected $15,000 > $8,000 cap.
    result = check_result(decision, "total_exposure")
    assert result.passed is False
    assert result.reason_code == "max_total_exposure_exceeded"


def test_total_exposure_naked_sell_with_pending_buy_sums_both():
    """The naked short leg ADDS on top of in-flight BUY exposure — additively
    independent in the projection (A-6 nets the short; ADR 0024 folds open BUYs)."""
    pending = _open_buy(
        "QQQ",
        status=OrderStatus.PARTIALLY_FILLED,
        quantity=40.0,
        filled_avg_price=100.0,
        filled_quantity=10.0,
    )  # remaining_notional = (40 - 10) * 100 = $3,000
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            symbol="SPY",
            quantity=60.0,
            estimated_unit_price=100.0,
            estimated_order_value=6_000.0,
            positions=[],
            recent_orders=[pending],
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )
    # $3,000 pending BUY + $6,000 naked short leg = $9,000 > $8,000 cap.
    result = check_result(decision, "total_exposure")
    assert result.passed is False
    assert result.reason_code == "max_total_exposure_exceeded"


def test_single_position_naked_sell_projects_short_leg():
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=30.0,
            estimated_unit_price=100.0,
            estimated_order_value=3_000.0,  # > $2,000 single-position cap
            positions=[],
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )
    result = check_result(decision, "single_position")
    assert result.passed is False
    assert result.reason_code == "max_single_position_exceeded"


# --- _check_concurrent_positions ------------------------------------------


def test_concurrent_positions_passes_when_buy_fits_within_limit():
    # Two held + 1 new = 3 under default limit of 3 (strict `>`, equality
    # passes).
    held = [_position("QQQ", 10.0), _position("IWM", 5.0)]
    decision = RiskEvaluator().evaluate(make_context(positions=held, estimated_order_value=500.0))

    assert check_result(decision, "concurrent_positions").passed is True


def test_concurrent_positions_fails_when_new_symbol_exceeds_limit():
    # Three held + new BUY for a 4th symbol → projected 4 > 3 limit.
    held = [
        _position("QQQ", 10.0),
        _position("IWM", 5.0),
        _position("DIA", 2.0),
    ]
    decision = RiskEvaluator().evaluate(make_context(positions=held, estimated_order_value=500.0))

    result = check_result(decision, "concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_concurrent_positions_exceeded"


def test_concurrent_positions_passes_when_buying_more_of_existing():
    # Three held (at limit); buying MORE of an existing symbol does not
    # add a slot.
    held = [
        _position("SPY", 1.0),
        _position("IWM", 5.0),
        _position("DIA", 2.0),
    ]
    decision = RiskEvaluator().evaluate(make_context(positions=held, estimated_order_value=500.0))

    assert check_result(decision, "concurrent_positions").passed is True


def test_concurrent_positions_allows_sell_that_closes_slot():
    # Four held (already over limit), full SELL of one should drop count to 3.
    held = [
        _position("SPY", 10.0),
        _position("IWM", 5.0),
        _position("DIA", 2.0),
        _position("QQQ", 1.0),
    ]
    # Risk defaults allow 3. A full-close SELL of SPY: projected 3.
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.SELL,
            quantity=10.0,
            positions=held,
            risk_defaults=_with_overrides(
                max_single_position_pct=1.0,
                max_total_exposure_pct=1.0,
                max_order_value_pct=1.0,
            ),
        )
    )

    assert check_result(decision, "concurrent_positions").passed is True


def test_concurrent_positions_not_tightened_by_strategy_config():
    """ADR 0024: the account-scoped cap is the GLOBAL value alone — a
    strategy's own ``risk.max_positions`` never clamps it down (that per-
    strategy bound is enforced separately in
    ``_check_strategy_concurrent_positions``). One held + one new BUY = 2,
    under the global cap of 3, so the account-scoped check passes even
    though the strategy declares ``max_positions=1``. (Pre-fix the account
    check clamped to ``min(3, 1)=1`` and wrongly refused — spec≠code.)"""
    strategy_config = StrategyExecutionConfig(
        name="single_slot",
        enabled=True,
        stage="paper",
        max_position_pct=1.0,
        max_positions=1,
        daily_loss_cap_pct=0.05,
        path=None,  # type: ignore[arg-type]
    )
    held = [_position("QQQ", 5.0)]
    decision = RiskEvaluator().evaluate(
        make_context(
            positions=held,
            estimated_order_value=500.0,
            strategy_config=strategy_config,
        )
    )

    assert check_result(decision, "concurrent_positions").passed is True


def test_concurrent_positions_regression_fleet_deadlock_2026_07_13(tmp_path):
    """Regression for the 2026-07-13/14 paper-fleet deadlock (spec≠code vs
    ADR 0024/0029).

    The broker held 3 positions opened by an unrelated strategy ('meanrev');
    the regime strategy (``max_positions=1``) proposed a BUY of a 4th symbol.
    Pre-fix, the account-scoped check clamped its limit to
    ``min(global, regime.max_positions) = min(10, 1) = 1`` and vetoed the BUY
    with "Projected open positions 4 exceeds limit 1" — every fleet strategy
    was deadlocked from buying while any positions were open.

    Post-fix the account-scoped check uses the GLOBAL cap (10) alone: 3 held +
    1 new = 4 <= 10 → PASS. The per-strategy check is unaffected — none of the
    3 broker positions are attributed to regime, so regime owns 0 and its
    projected count is 1 == its cap of 1 → PASS. The trade is allowed.

    ``strategy_config`` is set (non-None) deliberately: the pre-fix clamp only
    engaged when a full ``StrategyExecutionConfig`` was present, which is the
    production runner wiring that actually deadlocked.
    """
    store = _attrib_store(
        tmp_path,
        attributions={"AVGO": "meanrev", "GLD": "meanrev", "SLV": "meanrev"},
    )
    held = [_position("AVGO", 5.0), _position("GLD", 5.0), _position("SLV", 5.0)]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            strategy_config=_strategy_config(stage="paper", max_positions=1),
            expected_max_positions=1,
            estimated_order_value=500.0,
            # Operator sized the global cap for multi-strategy operation.
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )

    assert check_result(decision, "concurrent_positions").passed is True
    assert check_result(decision, "strategy_concurrent_positions").passed is True
    assert decision.allowed is True
    assert "max_concurrent_positions_exceeded" not in decision.reason_codes
    assert "max_strategy_positions_exceeded" not in decision.reason_codes


def test_concurrent_positions_blocks_at_global_cap():
    """The account-scoped check still blocks at the GLOBAL cap. Ten occupied
    slots at ``max_concurrent_positions=10``; a BUY of an 11th symbol projects
    11 > 10 → refused with ``max_concurrent_positions_exceeded`` (ADR 0024)."""
    held = [_position(sym, 5.0) for sym in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=held,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )

    result = check_result(decision, "concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_concurrent_positions_exceeded"


# --- _check_reconciliation_readiness ---------------------------------------


@pytest.mark.parametrize(
    "readiness, expected_reason",
    [
        (None, "reconciliation_required"),
        (
            ReconciliationReadiness(
                ready=False,
                reason_code="reconciliation_drift",
                message="dirty",
                broker_connected=True,
            ),
            "reconciliation_drift",
        ),
        (
            ReconciliationReadiness(
                ready=False,
                reason_code="reconciliation_stale",
                message="stale",
                broker_connected=True,
            ),
            "reconciliation_stale",
        ),
        (
            ReconciliationReadiness(
                ready=False,
                reason_code="reconciliation_incomplete",
                message="incomplete",
                broker_connected=False,
            ),
            "reconciliation_incomplete",
        ),
    ],
)
def test_reconciliation_blocks_exposure_increasing_intents(readiness, expected_reason):
    # R-OPS-005 (partial: reconciliation-gated resume clause)
    decision = RiskEvaluator().evaluate(
        make_context(
            reconciliation_readiness=readiness,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )

    result = check_result(decision, "reconciliation")
    assert result.passed is False
    assert result.reason_code == expected_reason
    assert expected_reason in decision.reason_codes


def test_reconciliation_allows_current_clean_readiness():
    # R-OPS-005 (partial: reconciliation-gated resume clause)
    decision = RiskEvaluator().evaluate(
        make_context(
            reconciliation_readiness=ReconciliationReadiness(
                ready=True,
                reason_code=None,
                message="clean today",
                broker_connected=True,
            ),
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )

    assert check_result(decision, "reconciliation").passed is True


def test_reconciliation_does_not_block_reducing_sell_against_broker_long():
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=5.0,
            positions=[_position("SPY", 10.0)],
            reconciliation_readiness=ReconciliationReadiness(
                ready=False,
                reason_code="reconciliation_drift",
                message="dirty",
                broker_connected=True,
            ),
            risk_defaults=_with_overrides(
                max_single_position_pct=1.0,
                max_total_exposure_pct=1.0,
                max_order_value_pct=1.0,
            ),
        )
    )

    assert check_result(decision, "reconciliation").passed is True
    assert "reconciliation_drift" not in decision.reason_codes


def test_reconciliation_blocks_sell_beyond_broker_held_quantity():
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            quantity=15.0,
            positions=[_position("SPY", 10.0)],
            reconciliation_readiness=ReconciliationReadiness(
                ready=False,
                reason_code="reconciliation_drift",
                message="dirty",
                broker_connected=True,
            ),
            risk_defaults=_with_overrides(
                max_single_position_pct=1.0,
                max_total_exposure_pct=1.0,
                max_order_value_pct=1.0,
            ),
        )
    )

    result = check_result(decision, "reconciliation")
    assert result.passed is False
    assert result.reason_code == "reconciliation_drift"


def test_reconciliation_exempt_for_backtests():
    decision = RiskEvaluator().evaluate(
        make_context(
            is_backtest=True,
            reconciliation_readiness=None,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_order_value_pct=1.0),
        )
    )

    assert check_result(decision, "reconciliation").passed is True


# --- helpers ---------------------------------------------------------------


def _with_overrides(**overrides) -> RiskDefaults:
    """Return a copy of DEFAULT_RISK_DEFAULTS with selected fields replaced."""
    base = DEFAULT_RISK_DEFAULTS
    return RiskDefaults(
        kill_switch_enabled=overrides.get("kill_switch_enabled", base.kill_switch_enabled),
        kill_switch_max_drawdown_pct=overrides.get(
            "kill_switch_max_drawdown_pct", base.kill_switch_max_drawdown_pct
        ),
        require_manual_reset=overrides.get("require_manual_reset", base.require_manual_reset),
        max_single_position_pct=overrides.get(
            "max_single_position_pct", base.max_single_position_pct
        ),
        max_concurrent_positions=overrides.get(
            "max_concurrent_positions", base.max_concurrent_positions
        ),
        max_total_exposure_pct=overrides.get("max_total_exposure_pct", base.max_total_exposure_pct),
        max_daily_loss_pct=overrides.get("max_daily_loss_pct", base.max_daily_loss_pct),
        max_trades_per_day=overrides.get("max_trades_per_day", base.max_trades_per_day),
        max_order_value_pct=overrides.get("max_order_value_pct", base.max_order_value_pct),
        duplicate_order_window_seconds=overrides.get(
            "duplicate_order_window_seconds", base.duplicate_order_window_seconds
        ),
        max_data_staleness_seconds=overrides.get(
            "max_data_staleness_seconds", base.max_data_staleness_seconds
        ),
    )


# --- _check_manifest_drift -------------------------------------------------


def _strategy_config(
    stage: str = "paper",
    enabled: bool = True,
    max_position_pct: float = 0.10,
    max_positions: int = 1,
    daily_loss_cap_pct: float = 0.05,
) -> StrategyExecutionConfig:
    from pathlib import Path as _Path

    return StrategyExecutionConfig(
        name="demo_strategy",
        enabled=enabled,
        stage=stage,
        max_position_pct=max_position_pct,
        max_positions=max_positions,
        daily_loss_cap_pct=daily_loss_cap_pct,
        path=_Path("configs/demo.yaml"),
    )


def test_manifest_drift_passes_for_manual_trade():
    """Manual trades (no strategy_config) are exempt."""
    decision = RiskEvaluator().evaluate(make_context(strategy_config=None))

    assert check_result(decision, "manifest_drift").passed is True


def test_manifest_drift_passes_for_backtest_stage():
    """Backtest stage has no promoted state to freeze."""
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="backtest"),
            runtime_config_hash=None,
            frozen_manifest_hash=None,
        )
    )

    assert check_result(decision, "manifest_drift").passed is True


def test_manifest_drift_blocks_paper_stage_without_frozen_manifest():
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            runtime_config_hash="abc" * 10,
            frozen_manifest_hash=None,
        )
    )

    result = check_result(decision, "manifest_drift")
    assert result.passed is False
    assert result.reason_code == "no_frozen_manifest"


def test_manifest_drift_blocks_when_hashes_diverge():
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="b" * 64,
        )
    )

    result = check_result(decision, "manifest_drift")
    assert result.passed is False
    assert result.reason_code == "manifest_drift"


def test_manifest_drift_passes_when_hashes_match():
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    assert check_result(decision, "manifest_drift").passed is True


def test_manifest_drift_raises_when_promoted_stage_missing_runtime_hash():
    """Promoted stages must supply runtime_config_hash; None is a programmer error."""
    for stage in ("paper", "micro_live", "live"):
        with pytest.raises(RuntimeError, match="requires runtime_config_hash"):
            RiskEvaluator().evaluate(
                make_context(
                    strategy_config=_strategy_config(stage=stage),
                    runtime_config_hash=None,
                    frozen_manifest_hash="a" * 64,
                )
            )


def test_manifest_drift_uses_runner_bound_stage_when_yaml_transiently_flips_to_backtest():
    """TOCTOU race fix: a paper-bound runner must NOT exempt drift just because
    the YAML on disk transiently reads ``stage: backtest`` (parallel agent edit,
    git checkout, in-place YAML rewrite, etc.). The runner's bound
    ``expected_stage`` wins — drift gates on the bound stage, not on whatever
    the file happens to say at config-read time.

    Surfaced 2026-05-06 (one cycle out of 459 fired SELL NVDA x50 because Opus
    4.7 in parallel had the YAML in a ``stage: backtest`` state for one read).
    See docs/reviews/2026-05-06-manifest-drift-toctou-race.md.
    """
    decision = RiskEvaluator().evaluate(
        make_context(
            # Runner was started at paper. YAML on disk is currently backtest
            # (mid-edit by a parallel writer). Drift hashes diverge.
            strategy_config=_strategy_config(stage="backtest"),
            expected_stage="paper",
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="b" * 64,
        )
    )

    result = check_result(decision, "manifest_drift")
    assert result.passed is False, (
        "paper-bound runner must block drift even when YAML reads backtest"
    )
    assert result.reason_code == "manifest_drift"


def test_manifest_drift_remains_exempt_when_runner_bound_to_backtest():
    """Regression guard for the legitimate exemption: a runner started against
    a backtest-stage YAML must stay drift-exempt across YAML edits (research
    iteration). The runner's bound ``expected_stage`` keeps it exempt regardless
    of whether the YAML transiently reads paper/micro_live/live mid-session."""
    for transient_yaml_stage in ("paper", "micro_live", "live"):
        decision = RiskEvaluator().evaluate(
            make_context(
                strategy_config=_strategy_config(stage=transient_yaml_stage),
                expected_stage="backtest",
                # Promoted-stage hashes provided to satisfy the
                # raise-on-missing-hash invariant; they should be ignored
                # because the runner-bound stage is exempt.
                runtime_config_hash="a" * 64,
                frozen_manifest_hash="b" * 64,
            )
        )

        result = check_result(decision, "manifest_drift")
        assert result.passed is True, (
            f"backtest-bound runner must stay exempt even when YAML transiently "
            f"reads {transient_yaml_stage}"
        )


def test_manifest_drift_falls_back_to_yaml_stage_when_no_expected_stage():
    """Backward-compat: callers that haven't been routed through a runner (e.g.
    operator manual trades, legacy entry points) supply ``expected_stage=None``
    and the existing YAML-stage logic applies unchanged."""
    # backtest-stage YAML, no expected_stage → exempt (existing behavior)
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="backtest"),
            expected_stage=None,
            runtime_config_hash=None,
            frozen_manifest_hash=None,
        )
    )
    assert check_result(decision, "manifest_drift").passed is True

    # paper-stage YAML with diverging hashes, no expected_stage → blocks
    # (existing behavior)
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            expected_stage=None,
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="b" * 64,
        )
    )
    result = check_result(decision, "manifest_drift")
    assert result.passed is False
    assert result.reason_code == "manifest_drift"


def test_manifest_drift_applies_at_micro_live_and_live_stages():
    for stage in ("micro_live", "live"):
        decision = RiskEvaluator().evaluate(
            make_context(
                strategy_config=_strategy_config(stage=stage),
                runtime_config_hash="a" * 64,
                frozen_manifest_hash="b" * 64,
            )
        )
        result = check_result(decision, "manifest_drift")
        assert result.passed is False, f"stage={stage} should be blocked on drift"
        assert result.reason_code == "manifest_drift"


def test_manifest_drift_skipped_when_is_backtest_true():
    """Pins ADR 0030 Decision 3: ``is_backtest=True`` bypasses manifest drift.

    Constructs a worst-case "should-be-blocked" context — paper-stage frozen
    manifest with mismatched runtime hash — that would normally trip
    ``_check_manifest_drift`` with ``reason_code='manifest_drift'``. With
    ``is_backtest=True`` the check must short-circuit to a passing result
    BEFORE inspecting the effective stage, runtime hash, or frozen hash.

    Calls ``_check_manifest_drift`` directly (not via ``evaluate``) to pin
    the fast-path's behavior in isolation: the message contains the literal
    "backtest mode" string from ADR 0030 Decision 3, and no other inspection
    happens. This is the architectural seam for future research-mode paths
    that consume the full evaluator without paying the manifest-drift refusal
    cost on backtest-stage queries.
    """
    context = make_context(
        # Paper-stage strategy with frozen manifest. Without is_backtest,
        # the diverging hashes would refuse with reason_code='manifest_drift'.
        strategy_config=_strategy_config(stage="paper"),
        runtime_config_hash="a" * 64,
        frozen_manifest_hash="b" * 64,
        is_backtest=True,
    )

    result = RiskEvaluator()._check_manifest_drift(context)  # noqa: SLF001

    assert result.name == "manifest_drift"
    assert result.passed is True
    assert result.reason_code is None
    assert "backtest mode" in result.message.lower()


def test_manifest_drift_is_backtest_default_false_preserves_drift_block():
    """Companion to the fast-path test: pins the default behavior unchanged.

    With ``is_backtest=False`` (the dataclass default), a paper-stage strategy
    with diverging hashes still refuses — confirming the fast-path is opt-in,
    not a backwards-incompatible change to the default refusal path.
    """
    context = make_context(
        strategy_config=_strategy_config(stage="paper"),
        runtime_config_hash="a" * 64,
        frozen_manifest_hash="b" * 64,
        # is_backtest defaults to False
    )

    result = RiskEvaluator()._check_manifest_drift(context)  # noqa: SLF001
    assert result.passed is False
    assert result.reason_code == "manifest_drift"


# --- TOCTOU follow-ups: runner-bound risk envelope wins over per-cycle YAML --
#
# These tests close the audit findings from PR #31 / Action Item #4 in
# docs/reviews/2026-05-06-manifest-drift-toctou-race.md. Same shape as the
# manifest_drift fix: a parallel writer raising a per-strategy cap mid-session
# must not let a cycle in flight take a position the runner's bound envelope
# would refuse. The ``min(global_default, per_strategy)`` defense partially
# mitigates the bypass but does not guarantee cycle-to-cycle consistency
# across a long-running runner; the bound values do.


def test_strategy_stage_uses_runner_bound_stage_when_yaml_flips_to_ineligible_stage():
    """A runner started against an ineligible stage (e.g. ``micro_live`` or
    ``live``) must not be permitted to submit just because the YAML
    transiently reads ``paper`` mid-cycle. The runner's bound stage wins."""
    decision = RiskEvaluator().evaluate(
        make_context(
            # YAML transiently reads paper (parallel writer mid-edit). Runner
            # is bound to micro_live (which is NOT eligible for paper-mode
            # submission per ADR 0004).
            strategy_config=_strategy_config(stage="paper"),
            expected_stage="micro_live",
            # Provide hashes so manifest_drift doesn't fire first and short-
            # circuit the test.
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    result = check_result(decision, "strategy_stage")
    assert result.passed is False, (
        "runner bound to micro_live must be refused even when YAML reads paper"
    )
    assert result.reason_code == "strategy_stage_ineligible"


def test_strategy_stage_refuses_backtest_stage_for_paper_submission():
    """SRS R-PRM-002 / review P1-01: stage ``backtest`` is not in
    ``ALLOWED_STAGES_BY_MODE['paper']``. The risk layer previously accepted
    both ``backtest`` and ``paper`` here — looser than the canonical
    promotion table the CLI and bench preflight enforce. The risk layer is
    the final arbiter and must not be the loosest gate."""
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="backtest"),
            preview_only=False,
        )
    )

    result = check_result(decision, "strategy_stage")
    assert result.passed is False
    assert result.reason_code == "strategy_stage_ineligible"


def test_strategy_stage_refuses_backtest_stage_even_in_preview():
    """Preview is intentionally NOT exempt from the stage check: stage
    eligibility does not depend on whether the order is actually sent, and
    a preview verdict must predict the submit verdict."""
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="backtest"),
            preview_only=True,
        )
    )

    result = check_result(decision, "strategy_stage")
    assert result.passed is False
    assert result.reason_code == "strategy_stage_ineligible"


def test_strategy_stage_fails_closed_for_unrecognized_trading_mode():
    """A trading mode absent from ``ALLOWED_STAGES_BY_MODE`` resolves to an
    empty allow-set — no stage is eligible. (Defense in depth: the
    paper-mode check refuses non-paper modes first, but the stage check
    must not silently pass if that fence ever moves.)"""
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            trading_mode="micro_live",
        )
    )

    result = check_result(decision, "strategy_stage")
    assert result.passed is False
    assert result.reason_code == "strategy_stage_ineligible"


def test_strategy_stage_allows_paper_stage_for_paper_submission():
    """Positive case: ``paper`` is the only stage in
    ``ALLOWED_STAGES_BY_MODE['paper']`` and must keep passing."""
    decision = RiskEvaluator().evaluate(
        make_context(strategy_config=_strategy_config(stage="paper"))
    )

    result = check_result(decision, "strategy_stage")
    assert result.passed is True


def test_runner_bound_max_positions_enforced_by_per_strategy_cap(tmp_path):
    """A parallel writer raising ``max_positions`` from 1 to 10 mid-session
    must not let the runner take a second position. The runner's bound cap
    (``expected_max_positions``) wins over the per-cycle YAML value.

    Post ADR 0024/0029 this binding is enforced by the per-strategy check
    (``_check_strategy_concurrent_positions``), which reads
    ``expected_max_positions`` raw — NOT by the account-scoped check, which
    now uses the global cap alone. The strategy already owns one attributed
    lot (AAPL); BUY MSFT projects 2 > the bound cap of 1, so the per-strategy
    check refuses with ``max_strategy_positions_exceeded`` even though YAML
    reads 10. The account-scoped check is comfortable (2 <= global 3)."""
    store = _attrib_store(tmp_path, attributions={"AAPL": "regime"})
    held = [_position("AAPL", 10.0)]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="MSFT",
            side=OrderSide.BUY,
            quantity=10,
            estimated_unit_price=100.0,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            # YAML on disk currently reads max_positions=10 (parallel writer
            # raised it mid-session). Runner is bound to max_positions=1.
            strategy_config=_strategy_config(stage="paper", max_positions=10),
            expected_max_positions=1,
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    result = check_result(decision, "strategy_concurrent_positions")
    assert result.passed is False, (
        "runner bound to max_positions=1 must refuse even when YAML reads 10"
    )
    assert result.reason_code == "max_strategy_positions_exceeded"
    # The account-scoped check does NOT tighten to the strategy bound.
    assert check_result(decision, "concurrent_positions").passed is True


def test_single_position_uses_runner_bound_max_position_pct():
    """A parallel writer raising ``max_position_pct`` from 5% to 50%
    mid-session must not let the runner take a position larger than the
    bound 5% envelope."""
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            # 10 shares × $100 = $1,000 = 10% of $10,000 portfolio.
            quantity=10,
            estimated_unit_price=100.0,
            account_portfolio_value=10_000.0,
            # YAML on disk currently reads 50% (parallel writer raised it).
            # Runner is bound to 5%.
            strategy_config=_strategy_config(
                stage="paper",
                max_position_pct=0.50,
            ),
            expected_max_position_pct=0.05,
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    result = check_result(decision, "single_position")
    assert result.passed is False, (
        "runner bound to max_position_pct=0.05 must refuse a 10% order even when YAML reads 0.50"
    )
    assert result.reason_code == "max_single_position_exceeded"


def test_daily_loss_uses_runner_bound_daily_loss_cap_pct():
    """A parallel writer raising ``daily_loss_cap_pct`` from 0.5% to 5%
    mid-session must not let the runner act when daily loss is past the
    bound 0.5% envelope."""
    decision = RiskEvaluator().evaluate(
        make_context(
            # Loss of $100 ≈ 1% of $10,000 portfolio.
            account_daily_pnl=-100.0,
            account_portfolio_value=10_000.0,
            # YAML on disk currently reads 5% cap (parallel writer raised it).
            # Runner is bound to 0.5%.
            strategy_config=_strategy_config(
                stage="paper",
                daily_loss_cap_pct=0.05,
            ),
            expected_daily_loss_cap_pct=0.005,
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    result = check_result(decision, "daily_loss")
    assert result.passed is False, (
        "runner bound to daily_loss_cap_pct=0.005 must refuse at 1% loss even when YAML reads 0.05"
    )
    assert result.reason_code == "daily_loss_cap_exceeded"


def test_account_cap_ignores_yaml_max_positions_when_no_binding():
    """The account-scoped check uses the GLOBAL cap alone (ADR 0024),
    regardless of the strategy YAML's ``max_positions`` and regardless of
    whether a runner binding is present. Here an operator/legacy caller has
    no per-strategy binding (``expected_max_positions=None``, no event store)
    so the per-strategy check is skipped; the account check applies the
    global cap of 3 directly."""
    from milodex.broker.models import Position

    # One existing position, one new BUY -> projected 2, under global cap 3.
    # The YAML ``max_positions=10`` is irrelevant to the account-scoped check.
    existing_position = Position(
        symbol="AAPL",
        quantity=10,
        avg_entry_price=100.0,
        current_price=100.0,
        market_value=1000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="MSFT",
            side=OrderSide.BUY,
            quantity=10,
            estimated_unit_price=100.0,
            positions=[existing_position],
            strategy_config=_strategy_config(stage="paper", max_positions=10),
            expected_max_positions=None,  # explicit None — no binding
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    # Global default=3, projected=2, 2 < 3 -> OK.
    assert check_result(decision, "concurrent_positions").passed is True


# --- decision.allowed aggregator (mutation audit Critical #1) -------------
#
# The risk evaluator's load-bearing AND across all checks
# (``allowed = all(check.passed for check in checks)`` at evaluator.py:91)
# was never asserted directly. The tests above pin individual
# ``check.passed`` values but a regression that returned ``None`` or a
# fixed ``True``/``False`` would slip through. The two tests below pin
# the global aggregation in both directions.


def test_decision_allowed_is_true_when_every_check_passes():
    """Kills mutation: evaluator.py:91 ``allowed = all(...)`` -> ``allowed = None``.

    Also kills the constant-flip mutations on the aggregator
    (``True`` -> ``False`` and vice versa). The default ``make_context``
    is engineered to satisfy every rule, so the global ``allowed`` must
    be the literal ``True``.
    """
    decision = RiskEvaluator().evaluate(make_context())

    # Every individual check passes — sanity-check the precondition before
    # the load-bearing global assertion.
    assert all(check.passed for check in decision.checks), (
        f"precondition failed: at least one check failed: "
        f"{[(c.name, c.passed, c.reason_code) for c in decision.checks if not c.passed]}"
    )
    assert decision.allowed is True
    assert decision.reason_codes == []
    assert decision.summary == "Allowed"


def test_decision_allowed_is_false_when_any_check_fails():
    """Kills mutation: evaluator.py:91 ``allowed = all(...)`` -> ``allowed = None``.

    Also kills the negation-flip on the aggregator
    (``all(...)`` -> ``not all(...)`` would invert this). Forces a single
    check to fail (kill switch active) and asserts the global decision
    flips to ``False`` with the failure surfaced in ``reason_codes``.
    """
    decision = RiskEvaluator().evaluate(make_context(kill_switch_active=True))

    assert decision.allowed is False
    assert "kill_switch_active" in decision.reason_codes
    assert decision.summary == "Blocked by risk checks"


# --- boundary tests (mutation audit Important #4) -------------------------


def test_daily_loss_passes_exactly_at_cap():
    """Kills mutation: evaluator.py:281 ``current_loss_pct > max_daily_loss``
    -> ``current_loss_pct >= max_daily_loss``.

    The rule uses strict ``>`` so equality must pass. Construct a loss
    that lands exactly on the 3% global cap: equity_base = portfolio - pnl
    = 10_000 - (-pnl) = 10_000 + |pnl|; current_loss_pct = |pnl| /
    (10_000 + |pnl|). Solving |pnl| / (10_000 + |pnl|) = 0.03 ->
    |pnl| = 300/0.97 ≈ 309.27835...
    """
    # |pnl| computed so current_loss_pct equals exactly max_daily_loss_pct (0.03).
    target_pct = DEFAULT_RISK_DEFAULTS.max_daily_loss_pct
    portfolio = 10_000.0
    # current_loss_pct = |pnl| / (portfolio + |pnl|) = target_pct
    # solve: |pnl| = target_pct * portfolio / (1 - target_pct)
    daily_pnl = -(target_pct * portfolio / (1.0 - target_pct))

    decision = RiskEvaluator().evaluate(
        make_context(
            account_portfolio_value=portfolio,
            account_daily_pnl=daily_pnl,
        )
    )

    result = check_result(decision, "daily_loss")
    assert result.passed is True, (
        "strict-> boundary: a loss exactly at the cap must pass; only > cap fails"
    )


def test_data_staleness_passes_exactly_at_max_age(monkeypatch):
    """R-OPS-007. Kills mutation: evaluator.py:254 ``age > max_age``
    -> ``age >= max_age``.

    The rule reads ``datetime.now(tz=UTC)`` internally; pin it via
    ``monkeypatch`` against ``milodex.risk.evaluator.datetime`` so we
    can engineer ``age == max_age`` exactly. Under strict ``>`` this
    must pass; the mutation to ``>=`` would flip it to fail.
    """
    from milodex.risk import evaluator as evaluator_module

    fixed_now = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)
    max_age_seconds = DEFAULT_RISK_DEFAULTS.max_data_staleness_seconds
    bar_timestamp = fixed_now - timedelta(seconds=max_age_seconds)

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(evaluator_module, "datetime", _FrozenDateTime)

    bar = Bar(
        timestamp=bar_timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )

    decision = RiskEvaluator().evaluate(make_context(latest_bar=bar))

    result = check_result(decision, "data_staleness")
    assert result.passed is True, (
        "strict-> boundary: a bar exactly at the staleness limit must pass; "
        "the > -> >= mutation would flip this to fail"
    )


def test_data_staleness_fails_just_over_max_age(monkeypatch):
    """Companion to ``test_data_staleness_passes_exactly_at_max_age``.

    Pin ``datetime.now`` and place the bar one microsecond beyond
    ``max_data_staleness_seconds``. Must fail under both the original
    and mutated comparison — but combined with the equality-passes
    test above, the pair pins the strict-``>`` semantic against the
    ``>=`` mutation.

    R-DAT-006: cache freshness is detectable; the bar timestamp is compared against now().
    R-OPS-007: stale data blocks the exposure-increasing decision (fail-closed).
    """
    from milodex.risk import evaluator as evaluator_module

    fixed_now = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)
    max_age_seconds = DEFAULT_RISK_DEFAULTS.max_data_staleness_seconds
    bar_timestamp = fixed_now - timedelta(seconds=max_age_seconds) - timedelta(microseconds=1)

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(evaluator_module, "datetime", _FrozenDateTime)

    bar = Bar(
        timestamp=bar_timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )

    decision = RiskEvaluator().evaluate(make_context(latest_bar=bar))

    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"


# --- session-aware 1D data-staleness (D-1 queue-at-open) -----------------
#
# Policy (founder): for 1D the bar's session date must equal the exchange
# calendar's latest completed session; the wall clock is only a generous
# seven-day defense-in-depth ceiling; resolution failure fails closed. The
# 300s global budget is unchanged for non-1D / operator-manual paths. Both
# the ``data_staleness`` veto and the ``data_quality_issue`` disable
# condition are driven by the single ``staleness_verdict`` helper.


def _exec_config_1d() -> StrategyExecutionConfig:
    """A paper-stage execution config tagged 1D (daily tempo)."""
    return StrategyExecutionConfig(
        name="daily_demo",
        enabled=True,
        stage="paper",
        max_position_pct=0.20,
        max_positions=3,
        daily_loss_cap_pct=0.02,
        path=Path("daily_demo.yaml"),
        family="momentum",
        bar_size="1D",
    )


def _daily_bar(session_date: date, *, now: datetime) -> Bar:
    """A daily bar stamped at the session date (00:00 UTC of that date),
    matching how Alpaca daily bars carry their session identity. ``now`` is
    unused for the timestamp but kept to document the session/now relation at
    call sites."""
    return Bar(
        timestamp=datetime(session_date.year, session_date.month, session_date.day, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )


def _freeze_now(monkeypatch, fixed_now: datetime) -> None:
    """Freeze ``datetime.now`` in BOTH risk-layer staleness gates so the veto
    and the disable condition read the same clock."""
    from milodex.risk import disable_conditions as dc_module
    from milodex.risk import evaluator as evaluator_module

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(evaluator_module, "datetime", _FrozenDateTime)
    monkeypatch.setattr(dc_module, "datetime", _FrozenDateTime)


def test_data_staleness_1d_matching_session_fresh_18h_old(monkeypatch):
    """R-OPS-007. 1D bar whose session date == latest completed session, ~18h old
    (Mon submit against Fri's close style), must PASS even though 18h is far
    past the 300s global budget."""
    fixed_now = datetime(2026, 5, 11, 14, 0, 0, tzinfo=UTC)  # Mon 10:00 ET-ish
    _freeze_now(monkeypatch, fixed_now)
    session = date(2026, 5, 8)  # Friday's session
    # A date-anchored daily bar carries the Friday session date; ~18h before
    # ``fixed_now`` it is far past the 300s global budget but identity-fresh.
    bar = _daily_bar(session, now=fixed_now)
    assert fixed_now - bar.timestamp > timedelta(hours=18)
    decision = RiskEvaluator().evaluate(
        make_context(
            latest_bar=bar,
            latest_completed_session=session,
            strategy_config=_exec_config_1d(),
        )
    )
    assert check_result(decision, "data_staleness").passed is True


def test_data_staleness_1d_none_session_fails_closed(monkeypatch):
    """R-OPS-007. Same fresh 1D bar, but the exchange calendar could not resolve the
    latest session (None) -> BLOCKED (fail-closed)."""
    fixed_now = datetime(2026, 5, 11, 14, 0, 0, tzinfo=UTC)
    _freeze_now(monkeypatch, fixed_now)
    session = date(2026, 5, 8)
    bar = _daily_bar(session, now=fixed_now)
    decision = RiskEvaluator().evaluate(
        make_context(
            latest_bar=bar,
            latest_completed_session=None,  # calendar unavailable / ambiguous
            strategy_config=_exec_config_1d(),
        )
    )
    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"


def test_data_staleness_1d_session_mismatch_dead_feed_blocked(monkeypatch):
    """R-OPS-007. Dead-feed case: a 3-day-old 1D bar whose session date != the latest
    completed session is BLOCKED, even though it is well inside the 7-day
    ceiling. This is the bug the prior attempt missed."""
    fixed_now = datetime(2026, 5, 11, 14, 0, 0, tzinfo=UTC)
    _freeze_now(monkeypatch, fixed_now)
    latest_session = date(2026, 5, 8)  # Friday: the real latest session
    stale_session = date(2026, 5, 5)  # Monday: a 3-session-old stale feed
    bar = _daily_bar(stale_session, now=fixed_now)
    decision = RiskEvaluator().evaluate(
        make_context(
            latest_bar=bar,
            latest_completed_session=latest_session,
            strategy_config=_exec_config_1d(),
        )
    )
    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"


def test_data_staleness_1d_beyond_7day_ceiling_blocked(monkeypatch):
    """R-OPS-007. Defensive ceiling: a 1D bar whose session date matches the latest
    completed session but whose age exceeds 7 calendar days is BLOCKED. This
    can't arise under a correct calendar; it bounds the blast radius if the
    calendar were wrong and the feed were stale at a matching date."""
    fixed_now = datetime(2026, 5, 20, 14, 0, 0, tzinfo=UTC)
    _freeze_now(monkeypatch, fixed_now)
    session = date(2026, 5, 8)  # bar is 12 days old, > 7-day ceiling
    bar = _daily_bar(session, now=fixed_now)
    assert (fixed_now - bar.timestamp) > timedelta(days=7)
    decision = RiskEvaluator().evaluate(
        make_context(
            latest_bar=bar,
            latest_completed_session=session,  # date matches, but age > ceiling
            strategy_config=_exec_config_1d(),
        )
    )
    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"


def test_data_staleness_none_strategy_config_uses_300s_not_crash(monkeypatch):
    """R-OPS-007. strategy_config is None (operator manual / legacy) -> 300s wall clock,
    never the 1D path. A bar just past 300s fails; it does not crash on the
    missing bar_size / latest_completed_session."""
    fixed_now = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)
    _freeze_now(monkeypatch, fixed_now)
    bar = Bar(
        timestamp=fixed_now - timedelta(seconds=300) - timedelta(microseconds=1),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )
    decision = RiskEvaluator().evaluate(make_context(latest_bar=bar))  # strategy_config=None
    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"


def test_data_staleness_1d_both_gates_agree_fresh(monkeypatch):
    """R-OPS-007. Pin the non-divergence invariant the prior attempt broke: a 1D/paper
    config with a latest-session bar must pass BOTH the ``data_staleness``
    veto AND keep the ``data_quality_issue`` disable condition inactive, so
    the overall decision is allowed. (The prior bug widened only the veto, so
    the disable condition still vetoed via ``disable_conditions``.)"""
    fixed_now = datetime(2026, 5, 11, 14, 0, 0, tzinfo=UTC)
    _freeze_now(monkeypatch, fixed_now)
    session = date(2026, 5, 8)
    bar = _daily_bar(session, now=fixed_now)
    decision = RiskEvaluator().evaluate(
        make_context(
            latest_bar=bar,
            latest_completed_session=session,
            strategy_config=_exec_config_1d(),
        )
    )
    assert check_result(decision, "data_staleness").passed is True
    assert check_result(decision, "disable_conditions").passed is True
    assert decision.allowed is True


def test_data_staleness_1d_both_gates_agree_session_mismatch_blocked(monkeypatch):
    """R-OPS-007. Companion to the agree-fresh test on the BLOCK side: a 1D session
    mismatch must fail the veto AND activate the disable condition, so a
    widening that touched only one gate cannot pass here."""
    fixed_now = datetime(2026, 5, 11, 14, 0, 0, tzinfo=UTC)
    _freeze_now(monkeypatch, fixed_now)
    bar = _daily_bar(date(2026, 5, 5), now=fixed_now)
    decision = RiskEvaluator().evaluate(
        make_context(
            latest_bar=bar,
            latest_completed_session=date(2026, 5, 8),
            strategy_config=_exec_config_1d(),
        )
    )
    assert check_result(decision, "data_staleness").passed is False
    dc = check_result(decision, "disable_conditions")
    assert dc.passed is False
    assert "data_quality_issue" in dc.message
    assert decision.allowed is False


# --- _check_strategy_concurrent_positions (ADR 0029) ---------------------
#
# These tests pin the per-strategy concurrent-positions cap. The new
# check sits ALONGSIDE _check_concurrent_positions (account-scoped, ADR
# 0024) — both must pass. The new check uses
# ``milodex.risk.attribution.attribute_position`` to reconstruct
# attribution from the durable trades history, and reads the per-strategy
# cap from ``EvaluationContext.expected_max_positions`` directly — an
# independent ceiling that neither clamps nor is clamped by the global
# account cap.


def _attrib_store(tmp_path, *, attributions: dict[str, str]):
    """Create an EventStore seeded with submitted opening BUY rows.

    Each ``(symbol, strategy_name)`` pair becomes a single submitted BUY
    that takes the symbol from zero to non-zero shares. The strategy_name
    column carries the attribution per ADR 0029 Decision 3.
    """
    from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent

    store = EventStore(tmp_path / "milodex.db")
    for idx, (symbol, strategy_name) in enumerate(attributions.items()):
        explanation_id = store.append_explanation(
            ExplanationEvent(
                recorded_at=datetime(2026, 5, 1, tzinfo=UTC) + timedelta(seconds=idx),
                decision_type="submit",
                status="submitted",
                strategy_name=strategy_name,
                strategy_stage="paper",
                strategy_config_path=None,
                config_hash=None,
                symbol=symbol,
                side="buy",
                quantity=1.0,
                order_type="market",
                time_in_force="day",
                submitted_by=("operator" if strategy_name is None else "strategy_runner"),
                market_open=True,
                latest_bar_timestamp=None,
                latest_bar_close=None,
                account_equity=10_000.0,
                account_cash=10_000.0,
                account_portfolio_value=10_000.0,
                account_daily_pnl=0.0,
                risk_allowed=True,
                risk_summary="Allowed",
                reason_codes=[],
                risk_checks=[],
                context={},
                # operator rows have no run ancestor; strategy_runner rows
                # carry a synthetic session for the dual-ancestor enforcement
                # in EventStore.append_explanation (migration 008).
                session_id=(None if strategy_name is None else "test-attrib-session"),
            )
        )
        store.append_trade(
            TradeEvent(
                explanation_id=explanation_id,
                recorded_at=datetime(2026, 5, 1, tzinfo=UTC) + timedelta(seconds=idx),
                status="submitted",
                source="paper",
                symbol=symbol,
                side="buy",
                quantity=1.0,
                order_type="market",
                time_in_force="day",
                estimated_unit_price=100.0,
                estimated_order_value=100.0,
                strategy_name=strategy_name,
                strategy_stage="paper",
                strategy_config_path=None,
                submitted_by=("operator" if strategy_name is None else "strategy_runner"),
                # Unique per row: the strategy-scoped ledger (strategy_positions)
                # dedupes by broker_order_id, so a shared id would collapse a
                # multi-symbol strategy to a single lot. attribute_position keys
                # off symbol and is unaffected either way.
                broker_order_id=f"broker-{idx}",
                broker_status=None,
                message=None,
            )
        )
    return store


def test_strategy_concurrent_positions_passes_when_strategy_below_cap(tmp_path):
    """Strategy 'regime' owns 0 positions; cap=1; BUY SPY -> projected 1 == cap, passes."""
    store = _attrib_store(tmp_path, attributions={})
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            request_strategy_name="regime",
            event_store=store,
            expected_max_positions=1,
            estimated_order_value=500.0,
        )
    )
    result = check_result(decision, "strategy_concurrent_positions")
    assert result.passed is True


def test_strategy_concurrent_positions_fails_when_strategy_at_cap(tmp_path):
    """Strategy 'regime' owns 1 position (SHY); cap=1; BUY SPY -> projected 2 > 1.

    Pins the failure path with reason code ``max_strategy_positions_exceeded``.
    """
    store = _attrib_store(tmp_path, attributions={"SHY": "regime"})
    held_shy = _position("SHY", 5.0, 100.0)
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=[held_shy],
            request_strategy_name="regime",
            event_store=store,
            expected_max_positions=1,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )
    result = check_result(decision, "strategy_concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_strategy_positions_exceeded"


def test_strategy_concurrent_positions_skipped_when_no_cap_declared(tmp_path):
    """No per-strategy cap declared -> the check is a no-op pass.

    Pins ADR 0029 Decision 6: a strategy YAML with no ``risk.max_positions``
    set leaves the per-strategy check skipped. The account-scoped floor
    (``_check_concurrent_positions``) still applies.
    """
    store = _attrib_store(tmp_path, attributions={"AAPL": "regime", "MSFT": "regime"})
    held = [_position("AAPL", 5.0), _position("MSFT", 5.0)]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            expected_max_positions=None,  # no cap declared
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )
    result = check_result(decision, "strategy_concurrent_positions")
    assert result.passed is True
    assert "no per-strategy cap" in result.message.lower()


def test_strategy_concurrent_positions_does_not_count_operator_positions(tmp_path):
    """Operator-attributed positions don't consume strategy slots.

    Pins ADR 0029 Decision 3: a strategy with cap=1 can still BUY when its
    only attributed position is 1 — even if the account also holds operator-
    attributed positions. The per-strategy check counts only strategy-owned
    positions; operator positions are visible to the account-scoped check.
    """
    # 'regime' owns AAPL; 'operator' owns 2 unrelated symbols.
    store = _attrib_store(
        tmp_path,
        attributions={"AAPL": "regime", "GLD": None, "SLV": None},
    )
    held = [
        _position("AAPL", 5.0),
        _position("GLD", 1.0),
        _position("SLV", 1.0),
    ]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="MSFT",
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            # regime cap is 2: AAPL is 1, BUY MSFT projects 2, equality OK.
            expected_max_positions=2,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )
    result = check_result(decision, "strategy_concurrent_positions")
    assert result.passed is True


def test_strategy_concurrent_positions_does_not_count_other_strategies_positions(tmp_path):
    """Strategy A's positions don't block strategy B.

    Pins ADR 0029 Decision 1: attribution is per-strategy. Strategy A
    holding 5 positions doesn't reduce the headroom for strategy B's
    cap.
    """
    store = _attrib_store(
        tmp_path,
        attributions={
            "AAPL": "strategy_a",
            "MSFT": "strategy_a",
            "GOOG": "strategy_a",
        },
    )
    held = [
        _position("AAPL", 5.0),
        _position("MSFT", 5.0),
        _position("GOOG", 5.0),
    ]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="strategy_b",
            event_store=store,
            expected_max_positions=1,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )
    result = check_result(decision, "strategy_concurrent_positions")
    assert result.passed is True


def test_both_caps_fail_simultaneously(tmp_path):
    """Both per-strategy AND account-scoped caps exceeded -> both reason codes appear.

    Pins ADR 0029 Decision 5: when both checks fail, both reason codes
    appear in ``decision.reason_codes`` and both checks appear in
    ``decision.checks``.
    """
    store = _attrib_store(
        tmp_path,
        attributions={"AAPL": "regime", "MSFT": "regime"},
    )
    held = [_position("AAPL", 5.0), _position("MSFT", 5.0)]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            expected_max_positions=1,  # regime owns 2, projected 3 > 1
            estimated_order_value=500.0,
            # Account-scoped cap=2: 2 held + 1 new = 3 > 2 (also fails).
            risk_defaults=_with_overrides(max_concurrent_positions=2),
        )
    )
    assert decision.allowed is False
    assert "max_concurrent_positions_exceeded" in decision.reason_codes
    assert "max_strategy_positions_exceeded" in decision.reason_codes
    # Both checks present in the explanation.
    check_names = {check.name for check in decision.checks}
    assert "concurrent_positions" in check_names
    assert "strategy_concurrent_positions" in check_names


def test_only_account_scoped_fails_when_per_strategy_below(tmp_path):
    """Account-scoped fails alone, per-strategy passes.

    Cap=2 account-wide; 2 operator-held + 1 new BUY = 3 > 2, account-
    scoped blocks. Per-strategy: regime owns 0, BUY -> 1 within its
    cap of 2.
    """
    # Two operator-held positions.
    store = _attrib_store(tmp_path, attributions={"GLD": None, "SLV": None})
    held = [_position("GLD", 1.0), _position("SLV", 1.0)]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            expected_max_positions=2,  # plenty of strategy-side room
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=2),
        )
    )
    assert decision.allowed is False
    assert "max_concurrent_positions_exceeded" in decision.reason_codes
    assert "max_strategy_positions_exceeded" not in decision.reason_codes


def test_only_per_strategy_fails_when_account_scoped_below(tmp_path):
    """Per-strategy fails alone, account-scoped passes.

    Account cap is high (10); strategy cap is tight (1). Strategy holds
    1, BUY would project 2 > 1 -> per-strategy blocks; the account-
    scoped check is comfortable.
    """
    store = _attrib_store(tmp_path, attributions={"AAPL": "regime"})
    held = [_position("AAPL", 5.0)]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            expected_max_positions=1,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )
    assert decision.allowed is False
    assert "max_strategy_positions_exceeded" in decision.reason_codes
    assert "max_concurrent_positions_exceeded" not in decision.reason_codes


def test_strategy_concurrent_positions_uses_expected_max_positions_not_clamped_to_global(
    tmp_path,
):
    """Per-strategy check reads ``expected_max_positions`` raw; no clamp.

    Pins ADR 0029 Decision 6: the per-strategy cap is an independent
    ceiling. Construct a scenario where ``expected_max_positions`` is
    LOOSER than the global account-scoped cap — the per-strategy check
    must use the raw per-strategy bound, never a value clamped down to
    the global ``max_concurrent_positions``.

    Scenario:
      - ``risk_defaults.max_concurrent_positions = 2`` (global tight)
      - ``expected_max_positions = 10`` (per-strategy loose)
      - Strategy 'regime' currently holds 2 attributed positions
        (AAPL, MSFT). It proposes BUY GOOG (a new symbol).
      - Projected per-strategy count = 3.

    Two behaviors, two outcomes:
      - Correct (raw read): cap = 10 → 3 <= 10 → PASS.
      - Buggy (clamp to global): cap = min(10, 2) = 2 → 3 > 2 → FAIL.

    The account-scoped check
    (:meth:`RiskEvaluator._check_concurrent_positions`) WILL fail in
    this scenario (3 > 2), and that's by design — this test is
    isolating the per-strategy check's behavior. The assertion is
    pinned only to ``strategy_concurrent_positions``.
    """
    # Strategy 'regime' already owns 2 attributed positions (AAPL, MSFT).
    store = _attrib_store(tmp_path, attributions={"AAPL": "regime", "MSFT": "regime"})
    held = [_position("AAPL", 1.0), _position("MSFT", 1.0)]

    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="GOOG",  # New symbol -> projected per-strategy count = 3.
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            # Per-strategy raw cap is 10 (loose). A buggy clamp to the
            # global cap would see 2 and refuse.
            expected_max_positions=10,
            estimated_order_value=500.0,
            # Global default is intentionally tight at 2. This is what
            # makes the two behaviors diverge: raw read sees 10, a clamp
            # to the global cap sees 2.
            risk_defaults=_with_overrides(max_concurrent_positions=2),
        )
    )
    strategy_check = check_result(decision, "strategy_concurrent_positions")
    assert strategy_check.passed is True, (
        "Per-strategy check must use raw expected_max_positions=10, not a "
        "value clamped down to risk_defaults.max_concurrent_positions=2 "
        "(which would refuse at projected=3 > cap=2)."
    )
    # The account-scoped check legitimately fails here (3 > 2). That is
    # the global cap doing its job and is unrelated to the per-strategy
    # behavior under test — pinned explicitly so a future refactor that
    # accidentally couples the two checks can't silently pass this test.
    account_check = check_result(decision, "concurrent_positions")
    assert account_check.passed is False
    assert "max_concurrent_positions_exceeded" in decision.reason_codes
    assert "max_strategy_positions_exceeded" not in decision.reason_codes


def test_regime_can_enter_when_meanrev_holds_unrelated_positions(tmp_path):
    """The 2026-05-04 regime/meanrev incident scenario (PHASE2_PLANNING.md §3.2 CS-1).

    Account holds 3 meanrev positions (AVGO, GLD, SLV) at session start.
    Regime starts with ``risk.max_positions=1`` and proposes BUY SPY.
    Under ADR 0024 alone, this was blocked with
    ``max_concurrent_positions_exceeded`` (account count 3+1 > regime's
    cap 1). Under ADR 0029, regime's per-strategy cap applies only to
    regime-attributed positions (currently 0), so projected = 1 == 1 ok.

    The account-scoped global cap stays the floor — ``_with_overrides``
    raises it to 10 to model an operator who has correctly sized
    ``max_concurrent_positions`` for multi-strategy operation per the
    RISK_POLICY.md guidance.
    """
    store = _attrib_store(
        tmp_path,
        attributions={
            "AVGO": "meanrev",
            "GLD": "meanrev",
            "SLV": "meanrev",
        },
    )
    held = [
        _position("AVGO", 5.0),
        _position("GLD", 5.0),
        _position("SLV", 5.0),
    ]
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=held,
            request_strategy_name="regime",
            event_store=store,
            expected_max_positions=1,
            estimated_order_value=500.0,
            # Account-scoped cap raised to 10 — operator sized correctly
            # per RISK_POLICY.md multi-strategy guidance.
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )

    assert decision.allowed is True, (
        "regime BUY SPY must NOT be blocked by meanrev's positions under ADR 0029"
    )
    assert "max_strategy_positions_exceeded" not in decision.reason_codes
    assert "max_concurrent_positions_exceeded" not in decision.reason_codes


# --- per-strategy cap counts the strategy's OWN lots (concurrent-intraday PR3) ---
#
# The pre-PR3 check enumerated broker-net ``context.positions``, then attributed
# each symbol. When a sibling's offsetting position nets the broker flat for a
# symbol this strategy holds (ADR 0055: rsi2 +13 / vwap_trend -13 -> account
# flat), the symbol never enters the enumeration -> the strategy's own lot is
# invisible -> the cap undercounts and FAILS OPEN. PR3 derives the owned set
# from the strategy-scoped ledger (``strategy_positions``), the same source the
# runner already trusts (runner.py ``_current_positions``).


def test_strategy_concurrent_positions_counts_own_lot_when_broker_net_flat(tmp_path):
    """Strategy owns SPY in its own ledger, but a sibling has netted the broker
    flat (``positions=[]``). The per-strategy cap must still see the SPY lot and
    block a BUY on a new symbol — not fail open.

    Non-vacuous: the pre-PR3 broker-net enumeration sees no SPY (net flat) ->
    owned 0 -> BUY AAPL projects 1 <= cap 1 -> ALLOWS. PR3 sees the ledger SPY
    lot -> owned 1 -> projects 2 > 1 -> BLOCKS.
    """
    store = _attrib_store(tmp_path, attributions={"SPY": "rsi2"})
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="AAPL",
            side=OrderSide.BUY,
            positions=[],  # broker net flat — sibling offset rsi2's lot at the account
            request_strategy_name="rsi2",
            event_store=store,
            expected_max_positions=1,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )
    result = check_result(decision, "strategy_concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_strategy_positions_exceeded"


def test_strategy_concurrent_positions_blocks_at_ledger_cap_when_net_flat(tmp_path):
    """Two ledger lots (SPY, AAPL), broker net flat, cap 2, BUY a third symbol ->
    projected 3 > 2 -> BLOCK. Exercises owned=2 read purely from the strategy
    ledger (requires the fixture's per-row broker_order_id to be unique)."""
    store = _attrib_store(tmp_path, attributions={"SPY": "rsi2", "AAPL": "rsi2"})
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="MSFT",
            side=OrderSide.BUY,
            positions=[],  # broker net flat
            request_strategy_name="rsi2",
            event_store=store,
            expected_max_positions=2,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )
    result = check_result(decision, "strategy_concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_strategy_positions_exceeded"


def test_strategy_concurrent_positions_adding_to_held_ledger_lot_adds_no_slot(tmp_path):
    """Buying MORE of a symbol the strategy already holds (per its ledger) adds
    no slot, even when broker net is flat. Guards against the PR3 implementation
    double-counting a held symbol: owned 1 + BUY SPY (already held) -> projected
    1 <= cap 1 -> PASS (a missing 'already-owned' guard would project 2 > 1)."""
    store = _attrib_store(tmp_path, attributions={"SPY": "rsi2"})
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            positions=[],  # broker net flat
            request_strategy_name="rsi2",
            event_store=store,
            expected_max_positions=1,
            estimated_order_value=500.0,
            risk_defaults=_with_overrides(max_concurrent_positions=10),
        )
    )
    assert check_result(decision, "strategy_concurrent_positions").passed is True


# --- _check_data_staleness fail-closed -------------------------------------


def test_data_staleness_naive_bar_timestamp_blocks_not_raises():
    """R-OPS-007. A naive (tz-unaware) bar timestamp must fail closed, not raise.

    Regression: ``datetime.now(tz=UTC) - naive_ts`` raises ``TypeError``
    (can't subtract offset-naive from offset-aware). An uncaught
    exception inside a ``_check_*`` aborts the whole ``evaluate()`` so
    the trade is neither explicitly allowed nor blocked — a fail-open
    hole. The bar timestamp must be normalized to UTC-aware before the
    subtraction, and ``evaluate()`` must still return a decision.
    """
    naive_old = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=1)
    bar = Bar(
        timestamp=naive_old,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )

    decision = RiskEvaluator().evaluate(make_context(latest_bar=bar))

    assert decision.allowed is False
    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"


def test_unexpected_exception_in_check_fails_closed(monkeypatch):
    """An unexpected exception in any individual check blocks, not raises.

    Defense-in-depth: even if a future check raises an unanticipated
    error, ``evaluate()`` must return a blocking ``RiskDecision`` rather
    than propagating the exception (which would leave the trade in an
    undefined allow/block state at the call site).
    """

    def _boom(self, context):
        raise RuntimeError("synthetic check failure")

    monkeypatch.setattr(RiskEvaluator, "_check_order_value", _boom)

    decision = RiskEvaluator().evaluate(make_context())

    assert decision.allowed is False
    result = check_result(decision, "order_value")
    assert result.passed is False
    assert result.reason_code == "risk_check_error"


# --------------------------------------------------------------------------- #
# Pending / in-flight orders consume risk slots (hardening-3)
#
# Caps must bound real economic exposure, including in-flight (unfilled) BUY
# orders — not only already-filled broker positions. A single-process burst of
# distinct-symbol BUYs before any fill must not over-submit. Data comes from
# context.recent_orders (already fetched); no new broker call.
# --------------------------------------------------------------------------- #


def _open_buy(
    symbol: str,
    *,
    status: OrderStatus = OrderStatus.PENDING,
    quantity: float = 10.0,
    filled_avg_price: float | None = None,
    filled_quantity: float | None = None,
) -> Order:
    """Build an in-flight (open) BUY order. PENDING with no price models a
    market order the broker has not yet filled; PARTIALLY_FILLED with a
    filled_avg_price models a mid-fill order whose notional is knowable."""
    return Order(
        id=f"ord-{symbol}",
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        time_in_force=TimeInForce.DAY,
        status=status,
        submitted_at=datetime.now(tz=UTC),
        filled_quantity=filled_quantity,
        filled_avg_price=filled_avg_price,
    )


def test_pending_buy_order_counts_toward_concurrent_positions():
    """An in-flight (unfilled) BUY occupies a concurrent-position slot: held
    MSFT + in-flight GOOG + intent AAPL = 3 > cap 2."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            symbol="AAPL",
            positions=[_position("MSFT", 10.0)],
            recent_orders=[_open_buy("GOOG", status=OrderStatus.PENDING)],
            risk_defaults=_with_overrides(max_concurrent_positions=2),
        )
    )

    result = check_result(decision, "concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_concurrent_positions_exceeded"


def test_concurrent_positions_pass_without_pending_order():
    """Contrast: with no in-flight order, held + intent == cap is allowed —
    proving the pending order is what tips the burst over the cap."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            symbol="AAPL",
            positions=[_position("MSFT", 10.0)],
            recent_orders=[],
            risk_defaults=_with_overrides(max_concurrent_positions=2),
        )
    )

    assert check_result(decision, "concurrent_positions").passed is True


def test_in_flight_buy_order_counts_toward_total_exposure():
    """A priced in-flight BUY (partially filled, has an avg price) adds to
    projected total exposure: 9_000 in-flight alone tops the 8_000 cap."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            symbol="AAPL",
            positions=[],
            recent_orders=[
                _open_buy(
                    "GOOG",
                    status=OrderStatus.PARTIALLY_FILLED,
                    quantity=90.0,
                    filled_quantity=10.0,
                    filled_avg_price=100.0,
                )
            ],  # remaining_notional = (90 - 10) * 100 = 8_000; + 1_000 intent = 9_000 > 8_000 cap
        )
    )

    result = check_result(decision, "total_exposure")
    assert result.passed is False
    assert result.reason_code == "max_total_exposure_exceeded"


def test_held_symbol_partial_fill_counts_unfilled_remainder():
    """A partially-filled BUY on a HELD symbol must contribute its *unfilled
    remainder* to the exposure cap — the filled portion is already in the
    position's market_value, but the in-flight remainder is real committed
    exposure that must not be dropped.

    MSFT position = the 10 filled shares (market_value 1_000); open MSFT order
    qty 60 / filled 10 @ 100 -> remaining (60-10)*100 = 5_000. Intent AAPL BUY
    2_500. Correct projected = 1_000 + 5_000 + 2_500 = 8_500 > 8_000 cap -> BLOCK.
    The old all-or-nothing held-symbol skip dropped the 5_000 remainder, giving
    1_000 + 2_500 = 3_500 < 8_000 -> wrongly allowed. So this BLOCK fails against
    the old code (non-vacuous)."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            symbol="AAPL",
            quantity=25.0,  # estimated_order_value = 25 * 100 = 2_500
            positions=[_position("MSFT", 10.0, 100.0)],  # filled portion, market_value 1_000
            recent_orders=[
                _open_buy(
                    "MSFT",
                    status=OrderStatus.PARTIALLY_FILLED,
                    quantity=60.0,
                    filled_quantity=10.0,
                    filled_avg_price=100.0,
                )
            ],  # remaining_notional = (60 - 10) * 100 = 5_000
        )
    )

    result = check_result(decision, "total_exposure")
    assert result.passed is False
    assert result.reason_code == "max_total_exposure_exceeded"


def test_held_symbol_partial_fill_remainder_not_double_counted():
    """The filled portion of a held-symbol partial fill must not be counted
    twice: once in the position's market_value and again via the order. Only the
    unfilled remainder is added on top.

    MSFT position = 10 filled shares (market_value 1_000); open MSFT order qty 60
    / filled 10 @ 100 -> remainder 5_000. Intent AAPL BUY 1_500. Correct
    projected = 1_000 + 5_000 + 1_500 = 7_500 <= 8_000 cap -> PASS. A naive fix
    that counted the order's *full* 6_000 notional on top of the position would
    give 1_000 + 6_000 + 1_500 = 8_500 > 8_000 -> wrongly blocked. Guards against
    that double-count regression."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            symbol="AAPL",
            quantity=15.0,  # estimated_order_value = 15 * 100 = 1_500
            positions=[_position("MSFT", 10.0, 100.0)],  # filled portion, market_value 1_000
            recent_orders=[
                _open_buy(
                    "MSFT",
                    status=OrderStatus.PARTIALLY_FILLED,
                    quantity=60.0,
                    filled_quantity=10.0,
                    filled_avg_price=100.0,
                )
            ],  # remaining_notional = 5_000 (NOT the full 6_000)
        )
    )

    assert check_result(decision, "total_exposure").passed is True


def test_full_sell_keeps_slot_when_pending_buy_for_same_symbol():
    """A full SELL does not free its concurrent-position slot while an in-flight
    BUY for the same symbol could re-open it (conservative). Held MSFT + GOOG (2
    slots) with a pending MSFT BUY, cap 1: the SELL is held at 2 occupied slots
    (not decremented to 1) -> BLOCK. Regression guard for the pending-aware SELL
    branch; non-vacuous only against master (which freed the slot)."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            symbol="MSFT",
            quantity=10.0,  # full sell (>= held quantity)
            positions=[_position("MSFT", 10.0), _position("GOOG", 10.0)],
            recent_orders=[_open_buy("MSFT", status=OrderStatus.PENDING)],
            risk_defaults=_with_overrides(max_concurrent_positions=1),
        )
    )

    result = check_result(decision, "concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_concurrent_positions_exceeded"


def test_full_sell_frees_slot_when_no_pending_buy():
    """Contrast: with no in-flight BUY for the symbol, a full SELL frees its
    slot. Held MSFT + GOOG (2) with no pending order, cap 1: SELL MSFT
    decrements to 1 <= cap -> PASS. Proves the pending guard is what keeps the
    slot in the test above."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            symbol="MSFT",
            quantity=10.0,
            positions=[_position("MSFT", 10.0), _position("GOOG", 10.0)],
            recent_orders=[],
            risk_defaults=_with_overrides(max_concurrent_positions=1),
        )
    )

    assert check_result(decision, "concurrent_positions").passed is True


# --------------------------------------------------------------------------- #
# Order.remaining_notional — the still-unfilled economic value used by the
# exposure cap (the filled portion lives in positions.market_value).
# --------------------------------------------------------------------------- #


def _bare_order(**overrides) -> Order:
    """Minimal Order for property unit tests; override individual fields."""
    base = {
        "id": "o",
        "symbol": "X",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": 10.0,
        "time_in_force": TimeInForce.DAY,
        "status": OrderStatus.PENDING,
        "submitted_at": datetime.now(tz=UTC),
    }
    base.update(overrides)
    return Order(**base)


def test_remaining_notional_unfilled_equals_full():
    # Fully-unfilled (filled_quantity None) -> remainder is the full quantity.
    order = _bare_order(quantity=10.0, limit_price=100.0, filled_quantity=None)
    assert order.remaining_notional == 1_000.0


def test_remaining_notional_partial_fill_counts_only_remainder():
    order = _bare_order(
        status=OrderStatus.PARTIALLY_FILLED,
        quantity=60.0,
        filled_quantity=10.0,
        filled_avg_price=100.0,
    )
    assert order.remaining_notional == 5_000.0  # (60 - 10) * 100


def test_remaining_notional_unpriced_market_is_none():
    # Pending market order: no filled_avg_price and no limit_price -> unknowable.
    order = _bare_order(quantity=10.0, limit_price=None, filled_avg_price=None)
    assert order.remaining_notional is None


def test_remaining_notional_overfill_clamps_to_zero():
    # Defensive: filled_quantity > quantity must not yield negative exposure.
    order = _bare_order(quantity=10.0, filled_quantity=15.0, limit_price=100.0)
    assert order.remaining_notional == 0.0


def test_checks_registry_is_account_complete():
    """R-EXE-004. Guard against doc/code drift: the enforced-check registry stays at 17
    (HR-7 added _check_max_trades_per_day; P2-07 added
    _check_disable_conditions per R-STR-014; concurrent-intraday PR2 added
    _check_opposite_side_order) and never silently lists a sector/correlation
    cap that the code does not implement (RISK_POLICY.md / SRS.md advertise
    those as planned only)."""
    # Pin the exact enforced set (member-for-member), not just its cardinality —
    # a length-only assert passes when a check is swapped out for another.
    assert RiskEvaluator._CHECKS == (
        "_check_kill_switch",
        "_check_trading_mode",
        "_check_reconciliation_readiness",
        "_check_strategy_stage",
        "_check_manifest_drift",
        "_check_disable_conditions",
        "_check_market_open",
        "_check_data_staleness",
        "_check_daily_loss",
        "_check_max_trades_per_day",
        "_check_order_value",
        "_check_single_position_limit",
        "_check_total_exposure",
        "_check_concurrent_positions",
        "_check_strategy_concurrent_positions",
        "_check_duplicate_order",
        "_check_opposite_side_order",
    )
    # Planned-only caps must never appear in the enforced set (SRS R-EXE-004).
    assert not any("sector" in name or "correlat" in name for name in RiskEvaluator._CHECKS)


# --- _check_max_trades_per_day (HR-7) ----------------------------------------
#
# Enforce that the account-wide paper-submitted trade count since UTC midnight
# is checked before every submission. Semantics: >= limit blocks (N+1th trade);
# < limit passes (Nth trade is the last allowed one).


def _store_with_today_trades(tmp_path, *, count: int, source: str = "paper") -> object:
    """Return an EventStore with ``count`` submitted trades recorded at 'now'."""
    from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent

    store = EventStore(tmp_path / "milodex_trades_today.db")
    now = datetime.now(tz=UTC)
    for idx in range(count):
        explanation_id = store.append_explanation(
            ExplanationEvent(
                recorded_at=now + timedelta(seconds=idx),
                decision_type="submit",
                status="submitted",
                strategy_name="test_strategy",
                strategy_stage="paper",
                strategy_config_path=None,
                config_hash=None,
                symbol="SPY",
                side="buy",
                quantity=1.0,
                order_type="market",
                time_in_force="day",
                submitted_by="strategy_runner",
                market_open=True,
                latest_bar_timestamp=None,
                latest_bar_close=None,
                account_equity=10_000.0,
                account_cash=10_000.0,
                account_portfolio_value=10_000.0,
                account_daily_pnl=0.0,
                risk_allowed=True,
                risk_summary="Allowed",
                reason_codes=[],
                risk_checks=[],
                context={},
                session_id="test-session-mtpd",
            )
        )
        store.append_trade(
            TradeEvent(
                explanation_id=explanation_id,
                recorded_at=now + timedelta(seconds=idx),
                status="submitted",
                source=source,
                symbol="SPY",
                side="buy",
                quantity=1.0,
                order_type="market",
                time_in_force="day",
                estimated_unit_price=100.0,
                estimated_order_value=100.0,
                strategy_name="test_strategy",
                strategy_stage="paper",
                strategy_config_path=None,
                submitted_by="strategy_runner",
                broker_order_id=f"broker-{idx}",
                broker_status=None,
                message=None,
                session_id="test-session-mtpd",
            )
        )
    return store


def test_max_trades_per_day_passes_when_below_limit(tmp_path):
    """Under the limit (19 of 20) — the check passes."""
    store = _store_with_today_trades(tmp_path, count=19)
    decision = RiskEvaluator().evaluate(
        make_context(
            event_store=store,
            risk_defaults=_with_overrides(max_trades_per_day=20),
        )
    )
    result = check_result(decision, "max_trades_per_day")
    assert result.passed is True


def test_max_trades_per_day_blocks_at_limit(tmp_path):
    """At the limit (20 of 20) — the (N+1)th trade is blocked.

    Strict semantics: ``today_count >= limit`` blocks; so a count of exactly
    ``max_trades_per_day`` must refuse the next submission.
    """
    store = _store_with_today_trades(tmp_path, count=20)
    decision = RiskEvaluator().evaluate(
        make_context(
            event_store=store,
            risk_defaults=_with_overrides(max_trades_per_day=20),
        )
    )
    result = check_result(decision, "max_trades_per_day")
    assert result.passed is False
    assert result.reason_code == "max_trades_per_day_exceeded"
    assert "20" in result.message


def test_max_trades_per_day_excludes_backtest_source_rows(tmp_path):
    """Backtest-source rows must not count toward the daily paper limit.

    Seeds 20 source='backtest' rows (which would hit limit=20 if counted)
    plus 1 source='paper' row. The check must pass because only the paper
    row is counted (today_count=1 < limit=20).

    Uses _store_with_today_trades with source='backtest' to create the
    20 rows, then manually appends one source='paper' row.
    """
    from milodex.core.event_store import ExplanationEvent, TradeEvent

    # Seed 20 source='backtest' rows into the store returned by the helper.
    # _store_with_today_trades uses session_id="test-session-mtpd" (no FK issue).
    store = _store_with_today_trades(tmp_path, count=20, source="backtest")

    now = datetime.now(tz=UTC)
    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=now + timedelta(seconds=500),
            decision_type="submit",
            status="submitted",
            strategy_name="paper_strategy",
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash=None,
            symbol="QQQ",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=None,
            latest_bar_close=None,
            account_equity=10_000.0,
            account_cash=10_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="Allowed",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id="paper-session-bt-excl",
        )
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=now + timedelta(seconds=500),
            status="submitted",
            source="paper",
            symbol="QQQ",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=100.0,
            strategy_name="paper_strategy",
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="strategy_runner",
            broker_order_id="paper-excl-1",
            broker_status=None,
            message=None,
            session_id="paper-session-bt-excl",
        )
    )

    # 20 backtest rows present + 1 paper row; limit=20. Paper count is 1 → pass.
    decision = RiskEvaluator().evaluate(
        make_context(
            event_store=store,
            risk_defaults=_with_overrides(max_trades_per_day=20),
        )
    )
    result = check_result(decision, "max_trades_per_day")
    assert result.passed is True, (
        "20 source='backtest' rows must not count toward the paper daily limit; "
        "only 1 source='paper' row → today_count=1 < limit=20 must pass"
    )


def test_max_trades_per_day_fails_closed_on_query_error(tmp_path, monkeypatch):
    """Query error → fail closed (blocks the trade, cannot verify)."""
    from milodex.core.event_store import EventStore

    store = EventStore(tmp_path / "milodex_err.db")

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(store, "count_submitted_trades_today", _raise)

    decision = RiskEvaluator().evaluate(
        make_context(
            event_store=store,
            risk_defaults=_with_overrides(max_trades_per_day=20),
        )
    )
    result = check_result(decision, "max_trades_per_day")
    assert result.passed is False
    assert result.reason_code == "max_trades_per_day_exceeded"


def test_max_trades_per_day_skipped_when_no_event_store():
    """``event_store=None`` → check is a no-op pass (legacy/manual-trade callers)."""
    decision = RiskEvaluator().evaluate(
        make_context(
            event_store=None,
            risk_defaults=_with_overrides(max_trades_per_day=0),  # limit=0 → would block
        )
    )
    result = check_result(decision, "max_trades_per_day")
    assert result.passed is True


# --------------------------------------------------------------------------- #
# Opposite-side resting order veto (concurrent-intraday plan PR2, invariant 2)
#
# When many strategies share one account+symbol, one can submit a BUY while a
# sibling's SELL still rests (or vice versa). Alpaca rejects that as a wash
# trade (40310000). The risk layer declines it first — audit hygiene, decided in
# risk/ (risk disposes), keyed off the account-scoped order book (recent_orders).
# Only OPEN (PENDING / PARTIALLY_FILLED) orders rest; a FILLED order is a
# position, a CANCELLED/REJECTED one is gone.
# --------------------------------------------------------------------------- #


def _open_sell(symbol: str, *, status: OrderStatus = OrderStatus.PENDING) -> Order:
    """Build an in-flight (open) SELL order — the opposite-side mirror of
    ``_open_buy``."""
    return Order(
        id=f"ord-sell-{symbol}",
        symbol=symbol,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=10.0,
        time_in_force=TimeInForce.DAY,
        status=status,
        submitted_at=datetime.now(tz=UTC),
    )


def test_opposite_side_resting_buy_declines_incoming_sell():
    """A resting BUY on SPY + an incoming SELL on SPY is declined: submitting it
    would trip Alpaca's wash-trade reject (40310000)."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            symbol="SPY",
            recent_orders=[_open_buy("SPY", status=OrderStatus.PENDING)],
        )
    )

    result = check_result(decision, "opposite_side_order")
    assert result.passed is False
    assert result.reason_code == "opposite_side_order_open"


def test_opposite_side_resting_sell_declines_incoming_buy():
    """Symmetric: a resting SELL + an incoming BUY on the same symbol is
    declined."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            symbol="SPY",
            recent_orders=[_open_sell("SPY", status=OrderStatus.PARTIALLY_FILLED)],
        )
    )

    result = check_result(decision, "opposite_side_order")
    assert result.passed is False
    assert result.reason_code == "opposite_side_order_open"


def test_same_side_resting_order_passes_opposite_side_check():
    """A resting BUY + an incoming BUY is NOT an opposite-side collision (the
    duplicate-order check handles same-side; this check must not fire)."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            symbol="SPY",
            recent_orders=[_open_buy("SPY", status=OrderStatus.PENDING)],
        )
    )

    assert check_result(decision, "opposite_side_order").passed is True


def test_opposite_side_order_on_different_symbol_passes():
    """A resting BUY on GOOG does not block a SELL on SPY — wash trades are
    per-symbol."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            symbol="SPY",
            recent_orders=[_open_buy("GOOG", status=OrderStatus.PENDING)],
        )
    )

    assert check_result(decision, "opposite_side_order").passed is True


@pytest.mark.parametrize(
    "terminal_status",
    [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED],
)
def test_terminal_opposite_side_order_does_not_block(terminal_status):
    """Only OPEN orders rest. A terminal opposite-side order (filled = a
    position; cancelled/rejected = gone) does not trip the veto."""
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.BUY,
            symbol="SPY",
            recent_orders=[_open_sell("SPY", status=terminal_status)],
        )
    )

    assert check_result(decision, "opposite_side_order").passed is True


def test_no_opposite_side_order_passes():
    """No resting orders at all → the veto passes."""
    decision = RiskEvaluator().evaluate(
        make_context(side=OrderSide.SELL, symbol="SPY", recent_orders=[])
    )

    assert check_result(decision, "opposite_side_order").passed is True


# --------------------------------------------------------------------------- #
# Per-strategy duplicate-order scoping (concurrent-intraday PR5)
#
# RISK_POLICY's Duplicate-Order Policy and the risk_defaults.yaml comment both
# say detection keys off the *strategy instance*. The check was account-wide
# (symbol+side+window, no strategy_name) — masked by the launch guard's
# one-strategy-per-symbol rule. With same-symbol co-run (PR4), two different
# strategies' legitimate same-side entries within the window would false-veto
# the second. The veto now scopes to the proposing strategy via the durable
# event-store path (broker recent_orders carries no strategy tag).
# --------------------------------------------------------------------------- #


def _store_with_recent_submit(
    tmp_path,
    *,
    strategy_name: str | None,
    symbol: str = "SPY",
    side: str = "buy",
    seconds_ago: int = 30,
):
    """EventStore seeded with one recent submitted paper trade attributed to
    ``strategy_name`` (None → operator)."""
    from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent

    store = EventStore(tmp_path / "milodex_dedup.db")
    recorded_at = datetime.now(tz=UTC) - timedelta(seconds=seconds_ago)
    is_operator = strategy_name is None
    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type="submit",
            status="submitted",
            strategy_name=strategy_name,
            strategy_stage=None if is_operator else "paper",
            strategy_config_path=None,
            config_hash=None,
            symbol=symbol,
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="operator" if is_operator else "strategy_runner",
            market_open=True,
            latest_bar_timestamp=recorded_at,
            latest_bar_close=100.0,
            account_equity=10_000.0,
            account_cash=10_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="Allowed",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id=None if is_operator else f"dedup-session-{strategy_name}",
        )
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=recorded_at,
            status="submitted",
            source="paper",
            symbol=symbol,
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=100.0,
            strategy_name=strategy_name,
            strategy_stage=None if is_operator else "paper",
            strategy_config_path=None,
            submitted_by="operator" if is_operator else "strategy_runner",
            broker_order_id=f"dedup-broker-{strategy_name}",
            broker_status=None,
            message=None,
        )
    )
    return store


def test_duplicate_order_not_blocked_across_strategies(tmp_path):
    """A recent SPY BUY by strategy 'alpha' must NOT veto strategy 'beta's SPY
    BUY: different strategies are not duplicates of each other (the co-run case
    the launch guard used to make impossible).

    Non-vacuous: the pre-PR5 account-wide durable query counts alpha's order
    regardless of the proposing strategy → BLOCKS beta. Per-strategy scoping
    sees zero beta orders → PASS.
    """
    store = _store_with_recent_submit(tmp_path, strategy_name="alpha")
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            request_strategy_name="beta",
            event_store=store,
        )
    )
    assert check_result(decision, "duplicate_order").passed is True


def test_duplicate_order_blocked_for_same_strategy(tmp_path):
    """A strategy's own recent same-side order on the symbol still vetoes a
    duplicate — the protective intent is preserved, just scoped per-strategy."""
    store = _store_with_recent_submit(tmp_path, strategy_name="alpha")
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            request_strategy_name="alpha",
            event_store=store,
        )
    )
    result = check_result(decision, "duplicate_order")
    assert result.passed is False
    assert result.reason_code == "duplicate_order_window"


def test_duplicate_order_operator_scope_ignores_strategy_orders(tmp_path):
    """An operator manual order (no strategy) scopes to operator-attributed
    history only: a *strategy's* recent order does not veto the operator's."""
    store = _store_with_recent_submit(tmp_path, strategy_name="alpha")
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            request_strategy_name=None,  # operator
            event_store=store,
        )
    )
    assert check_result(decision, "duplicate_order").passed is True


def test_duplicate_order_blocked_for_same_operator_order(tmp_path):
    """Operator scope still dedups against the operator's own recent order."""
    store = _store_with_recent_submit(tmp_path, strategy_name=None)  # operator-seeded
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            request_strategy_name=None,  # operator
            event_store=store,
        )
    )
    result = check_result(decision, "duplicate_order")
    assert result.passed is False
    assert result.reason_code == "duplicate_order_window"

"""Unit tests for the shared staleness policy (D-1 queue-at-open).

These pin the pure :func:`milodex.risk.staleness.staleness_verdict` contract
directly, independent of the two gates that consume it. The integration that
both gates agree lives in ``test_invariant_parity.py`` and ``test_risk_rules.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from milodex.data.models import Bar
from milodex.risk.staleness import DAILY_STALENESS_CEILING, staleness_verdict

from .test_risk_rules import make_context

_SESSION = date(2026, 5, 8)
_NOW = datetime(2026, 5, 11, 14, 0, tzinfo=UTC)


def _daily_bar(session_date: date) -> Bar:
    return Bar(
        timestamp=datetime(session_date.year, session_date.month, session_date.day, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )


def _bar_aged(seconds: float) -> Bar:
    return Bar(
        timestamp=_NOW - timedelta(seconds=seconds),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )


def _ctx(*, latest_bar, bar_size: str, latest_completed_session):
    from milodex.execution.config import StrategyExecutionConfig

    cfg = None
    if bar_size is not None:
        from pathlib import Path

        cfg = StrategyExecutionConfig(
            name="helper_demo",
            enabled=True,
            stage="paper",
            max_position_pct=0.2,
            max_positions=3,
            daily_loss_cap_pct=0.02,
            path=Path("helper_demo.yaml"),
            family="momentum",
            bar_size=bar_size,
        )
    return make_context(
        latest_bar=latest_bar,
        strategy_config=cfg,
        latest_completed_session=latest_completed_session,
    )


def test_no_bar_is_stale_with_none_age():
    ctx = _ctx(latest_bar=None, bar_size="1D", latest_completed_session=_SESSION)
    # make_context substitutes a fresh bar when latest_bar is None, so build a
    # context with an explicitly None bar via the dataclass replace path.
    from dataclasses import replace

    ctx = replace(ctx, latest_bar=None)
    verdict = staleness_verdict(ctx, _NOW)
    assert verdict.is_stale is True
    assert verdict.age_seconds is None
    # The evaluator pre-empts this case with its own ``no_latest_bar`` code
    # before consulting the verdict; the verdict carries the legacy umbrella.
    assert verdict.reason_code == "stale_market_data"


def test_1d_matching_session_within_ceiling_is_fresh():
    ctx = _ctx(latest_bar=_daily_bar(_SESSION), bar_size="1D", latest_completed_session=_SESSION)
    verdict = staleness_verdict(ctx, _NOW)
    assert verdict.is_stale is False
    assert verdict.reason_code is None


def test_1d_none_session_fails_closed_with_calendar_unavailable_code():
    """Sub-cause (c): a calendar-resolution failure still FAILS (fail-closed
    unchanged) but carries the transient-specific ``calendar_unavailable``
    reason code, NOT the permanent ``stale_market_data`` umbrella — the drain
    retire branch (#381) keys on the latter and must not retire on the former."""
    ctx = _ctx(latest_bar=_daily_bar(_SESSION), bar_size="1D", latest_completed_session=None)
    verdict = staleness_verdict(ctx, _NOW)
    assert verdict.is_stale is True
    assert "fail-closed" in verdict.detail
    assert verdict.reason_code == "calendar_unavailable"


def test_1d_session_mismatch_is_stale():
    ctx = _ctx(
        latest_bar=_daily_bar(date(2026, 5, 5)),
        bar_size="1D",
        latest_completed_session=_SESSION,
    )
    verdict = staleness_verdict(ctx, _NOW)
    assert verdict.is_stale is True
    assert verdict.reason_code == "stale_market_data"


def test_1d_beyond_ceiling_is_stale_even_if_date_matches():
    far_now = datetime(_SESSION.year, _SESSION.month, _SESSION.day, tzinfo=UTC) + (
        DAILY_STALENESS_CEILING + timedelta(days=1)
    )
    ctx = _ctx(latest_bar=_daily_bar(_SESSION), bar_size="1D", latest_completed_session=_SESSION)
    verdict = staleness_verdict(ctx, far_now)
    assert verdict.is_stale is True
    assert verdict.reason_code == "stale_market_data"


def test_non_1d_uses_300s_budget_fresh_then_stale():
    fresh = _ctx(latest_bar=_bar_aged(299), bar_size="5Min", latest_completed_session=None)
    fresh_verdict = staleness_verdict(fresh, _NOW)
    assert fresh_verdict.is_stale is False
    assert fresh_verdict.reason_code is None
    stale = _ctx(latest_bar=_bar_aged(301), bar_size="5Min", latest_completed_session=None)
    stale_verdict = staleness_verdict(stale, _NOW)
    assert stale_verdict.is_stale is True
    assert stale_verdict.reason_code == "stale_market_data"


def test_none_config_uses_300s_budget_not_1d_path():
    ctx = _ctx(latest_bar=_bar_aged(301), bar_size=None, latest_completed_session=None)
    verdict = staleness_verdict(ctx, _NOW)
    assert verdict.is_stale is True
    assert "stale by" in verdict.detail  # 300s message, not the fail-closed one


def test_empty_bar_size_uses_300s_budget():
    """Task A defaults bar_size to '' (not '1D') for configs that omit tempo —
    that must take the 300s path, not the 1D session path."""
    fresh = _ctx(latest_bar=_bar_aged(120), bar_size="", latest_completed_session=None)
    assert staleness_verdict(fresh, _NOW).is_stale is False

"""Unit tests for milodex.promotion.run_evidence.

Covers ``metrics_from_run`` and ``compute_post_update_hash`` at their new public
home in the promotion layer (refactor/bench-facade-layering). These helpers were
previously private to ``milodex.cli.commands.promotion``; moving them here
eliminates the layering inversion where the bench command facade was reaching
into CLI internals.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from milodex.promotion.run_evidence import compute_post_update_hash, metrics_from_run
from milodex.strategies.loader import canonicalize_config_data

# ---------------------------------------------------------------------------
# metrics_from_run
# ---------------------------------------------------------------------------


def _fake_event_store(run=None):
    """Return a minimal event-store stub."""
    store = MagicMock()
    store.get_backtest_run.return_value = run
    return store


def _make_single_period_run(run_id: str) -> SimpleNamespace:
    """Backtest run with no walk_forward metadata — falls through to metrics_for_run."""
    return SimpleNamespace(
        run_id=run_id,
        strategy_id="meanrev.daily.rsi2pullback.v1",
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
        metadata={},
    )


def _make_walk_forward_run(run_id: str, *, sharpe, max_dd, trade_count) -> SimpleNamespace:
    """Backtest run with OOS-aggregate metadata (walk-forward shape)."""
    return SimpleNamespace(
        run_id=run_id,
        strategy_id="meanrev.daily.rsi2pullback.v1",
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
        metadata={
            "walk_forward": True,
            "oos_aggregate": {
                "sharpe": sharpe,
                "max_drawdown_pct": max_dd,
                "trade_count": trade_count,
            },
        },
    )


def test_metrics_from_run_returns_none_triple_when_run_id_is_none():
    """Lifecycle-exempt callers pass run_id=None; function short-circuits."""
    result = metrics_from_run(None, _fake_event_store())
    assert result == (None, None, None)


def test_metrics_from_run_raises_when_run_not_found():
    """Unknown run_id raises ValueError with a descriptive message."""
    store = _fake_event_store(run=None)
    with pytest.raises(ValueError, match="Backtest run not found"):
        metrics_from_run("nonexistent-run-id", store)


def test_metrics_from_run_walk_forward_reads_oos_aggregate():
    """Walk-forward runs return OOS-aggregate values from metadata, not trades."""
    run = _make_walk_forward_run("wf-run-1", sharpe=1.23, max_dd=8.5, trade_count=45)
    store = _fake_event_store(run=run)

    sharpe, max_dd, trades = metrics_from_run("wf-run-1", store)

    assert sharpe == pytest.approx(1.23)
    assert max_dd == pytest.approx(8.5)
    assert trades == 45


def test_metrics_from_run_walk_forward_handles_partial_oos_aggregate():
    """Missing keys in oos_aggregate return None for those fields."""
    run = SimpleNamespace(
        run_id="wf-partial",
        metadata={"walk_forward": True, "oos_aggregate": {"sharpe": 0.8}},
    )
    store = _fake_event_store(run=run)

    sharpe, max_dd, trades = metrics_from_run("wf-partial", store)

    assert sharpe == pytest.approx(0.8)
    assert max_dd is None
    assert trades is None


def test_metrics_from_run_single_period_delegates_to_metrics_for_run(monkeypatch):
    """Single-period runs fall through to metrics_for_run (not walk-forward path)."""
    run = _make_single_period_run("sp-run-1")
    store = _fake_event_store(run=run)

    fake_metrics = SimpleNamespace(sharpe_ratio=0.75, max_drawdown_pct=12.3, trade_count=38)
    calls = []

    def fake_metrics_for_run(run_arg, store_arg):
        calls.append((run_arg, store_arg))
        return fake_metrics

    monkeypatch.setattr(
        "milodex.cli.commands.analytics.metrics_for_run",
        fake_metrics_for_run,
    )

    sharpe, max_dd, trades = metrics_from_run("sp-run-1", store)

    assert len(calls) == 1, "metrics_for_run should be called exactly once"
    assert sharpe == pytest.approx(0.75)
    assert max_dd == pytest.approx(12.3)
    assert trades == 38


# ---------------------------------------------------------------------------
# compute_post_update_hash
# ---------------------------------------------------------------------------


def _make_raw_data(stage: str) -> dict:
    """Minimal raw_data dict matching the StrategyConfig shape."""
    return {
        "strategy": {
            "id": "meanrev.daily.rsi2pullback.curated.v1",
            "family": "meanrev",
            "template": "daily.rsi2pullback",
            "variant": "curated",
            "version": 1,
            "stage": stage,
            "enabled": True,
            "universe": ["AAPL"],
            "parameters": {},
            "tempo": {"bar_size": "1D", "min_hold_days": 1, "max_hold_days": 5},
            "risk": {"max_position_pct": 0.1},
            "backtest": {},
            "disable_conditions_additional": [],
        }
    }


def test_compute_post_update_hash_changes_with_stage():
    """The hash differs when to_stage changes — it reflects the post-update YAML."""
    raw_data = _make_raw_data("backtest")

    hash_paper = compute_post_update_hash(raw_data, "paper")
    hash_micro_live = compute_post_update_hash(raw_data, "micro_live")

    assert hash_paper != hash_micro_live


def test_compute_post_update_hash_is_deterministic():
    """Same inputs produce the same hash every time."""
    raw_data = _make_raw_data("backtest")

    h1 = compute_post_update_hash(raw_data, "paper")
    h2 = compute_post_update_hash(raw_data, "paper")

    assert h1 == h2


def test_compute_post_update_hash_does_not_mutate_raw_data():
    """The function must not modify the caller's raw_data dict."""
    raw_data = _make_raw_data("backtest")
    original_stage = raw_data["strategy"]["stage"]

    compute_post_update_hash(raw_data, "paper")

    assert raw_data["strategy"]["stage"] == original_stage


def test_compute_post_update_hash_matches_manual_derivation():
    """Result matches a manual SHA-256 of the canonical post-update JSON.

    This test pins the exact derivation so a future change to
    ``canonicalize_config_data`` or the hash algorithm fails loudly.
    """
    raw_data = _make_raw_data("backtest")
    to_stage = "paper"

    actual = compute_post_update_hash(raw_data, to_stage)

    # Replicate the derivation manually
    strategy = dict(raw_data["strategy"])
    strategy["stage"] = to_stage
    canonical = canonicalize_config_data({**raw_data, "strategy": strategy})
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    assert actual == expected


def test_compute_post_update_hash_returns_hex_string():
    """Result is a lowercase hex string of the expected length (SHA-256 = 64 chars)."""
    raw_data = _make_raw_data("backtest")
    result = compute_post_update_hash(raw_data, "paper")

    assert isinstance(result, str)
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)

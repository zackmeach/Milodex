"""Tests for the shared paper-promotion orchestrator (RM-010).

The orchestrator owns the full ``backtest -> paper`` choreography that CLI
and Bench previously open-coded: stage validation, metrics resolution, gate
evaluation, manifest hash, evidence assembly, atomic transition. These tests
verify the orchestrator's contract directly — the existing CLI and Bench
behavior suites cover the call-site integrations.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from milodex.core.event_store import BacktestRunEvent, EventStore
from milodex.promotion.orchestrator import (
    REASON_GATE_FAILED,
    REASON_INVALID_STAGE_TRANSITION,
    REASON_LIFECYCLE_EXEMPT_NOT_SCOPED,
    REASON_MISSING_BACKTEST_RUN,
    REASON_OVERRIDE_FLAGS_CONFLICT,
    REASON_OVERRIDE_STAGE_NOT_ALLOWED,
    PromoteBlocked,
    PromoteError,
    PromoteRequest,
    PromoteSuccess,
    _check_bypass_admissibility,
    prepare_and_record_promotion,
)

_NOW = datetime(2026, 5, 21, 18, 0, tzinfo=UTC)
# The policy-listed lifecycle-proof strategy id (ADR 0058). Using the real
# applies_to id keeps the lifecycle-exempt path admissible; the statistical
# path is identity-agnostic, so the statistical tests are unaffected.
_STRATEGY_ID = "regime.daily.sma200_rotation.spy_shy.v1"
# A non-lifecycle-proof id for scoping-refusal tests.
_NON_REGIME_ID = "meanrev.daily.rsi2.spy.v1"


def _write_config(
    tmp_path: Path,
    *,
    stage: str = "backtest",
    min_trades_required: int = 30,
    strategy_id: str = _STRATEGY_ID,
) -> Path:
    """Write a strategy YAML. ``min_trades_required`` is always an int — the
    null path is a known pre-existing CLI/Bench bug (``int(None)`` raises);
    RM-010 preserves bug-for-bug parity and does not exercise that path.

    ``strategy_id`` defaults to the lifecycle-proof id; scoping tests pass a
    non-regime id. The id is decomposed into family/template/variant/version
    to satisfy the loader's ``{family}.{template}.{variant}.v{version}`` rule.
    """
    mt = str(min_trades_required)
    family, template_head, template_tail, variant, version_token = strategy_id.split(".")
    template = f"{template_head}.{template_tail}"
    version = version_token.removeprefix("v")
    path = tmp_path / "demo_strategy.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            strategy:
              id: "{strategy_id}"
              family: "{family}"
              template: "{template}"
              variant: "{variant}"
              version: {version}
              description: "test"
              enabled: true
              universe:
                - "SPY"
                - "SHY"
              parameters:
                ma_filter_length: 200
                risk_on_symbol: "SPY"
                risk_off_symbol: "SHY"
                allocation_pct: 0.09
              tempo:
                bar_size: "1D"
                min_hold_days: 1
                max_hold_days: null
              risk:
                max_position_pct: 0.10
                max_positions: 1
                daily_loss_cap_pct: 0.05
                stop_loss_pct: null
              stage: "{stage}"
              backtest:
                slippage_pct: 0.001
                commission_per_trade: 0.00
                min_trades_required: {mt}
                walk_forward_windows: 1
              disable_conditions_additional: []
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _seed_walk_forward_run(
    store: EventStore,
    *,
    run_id: str = "wf-run-1",
    sharpe: float | None,
    max_drawdown_pct: float | None,
    trade_count: int | None,
) -> None:
    """Seed a backtest run whose OOS-aggregate metadata feeds the gate."""
    store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=_STRATEGY_ID,
            config_path="configs/test.yaml",
            config_hash="a" * 64,
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 12, 31, tzinfo=UTC),
            started_at=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
            status="completed",
            slippage_pct=0.002,
            commission_per_trade=0.0,
            metadata={
                "walk_forward": True,
                "oos_aggregate": {
                    "sharpe": sharpe,
                    "max_drawdown_pct": max_drawdown_pct,
                    "trade_count": trade_count,
                },
            },
        )
    )


def _statistical_request(cfg_path: Path, *, run_id: str = "wf-run-1") -> PromoteRequest:
    return PromoteRequest(
        strategy_id=_STRATEGY_ID,
        config_path=cfg_path,
        to_stage="paper",
        recommendation="strategy ready for paper-stage capital exposure",
        known_risks=["regime filter may lag on rapid drawdowns"],
        approved_by="operator",
        run_id=run_id,
        lifecycle_exempt=False,
        notes="rm-010 test",
        now=_NOW,
    )


def _lifecycle_exempt_request(cfg_path: Path) -> PromoteRequest:
    return PromoteRequest(
        strategy_id=_STRATEGY_ID,
        config_path=cfg_path,
        to_stage="paper",
        recommendation="regime strategy — lifecycle-exempt paper promotion",
        known_risks=["drift-check relies on manifest freezing correctly"],
        approved_by="operator",
        run_id=None,
        lifecycle_exempt=True,
        notes=None,
        now=_NOW,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_statistical_paper_promotion_writes_manifest_and_promotion(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    _seed_walk_forward_run(store, sharpe=0.4, max_drawdown_pct=10.0, trade_count=40)

    result = prepare_and_record_promotion(_statistical_request(cfg_path), store)

    assert isinstance(result, PromoteSuccess)
    assert result.strategy_id == _STRATEGY_ID
    assert result.from_stage == "backtest"
    assert result.to_stage == "paper"
    assert result.promotion_type == "statistical"
    assert result.manifest_id is not None
    assert result.promotion_id is not None
    assert result.manifest_hash and len(result.manifest_hash) == 64
    assert result.evidence.manifest_hash == result.manifest_hash
    assert result.evidence.recommendation.startswith("strategy ready")
    assert result.evidence.known_risks == ["regime filter may lag on rapid drawdowns"]
    assert result.metrics_snapshot == {
        "sharpe_ratio": 0.4,
        "max_drawdown_pct": 10.0,
        "trade_count": 40,
    }

    # YAML stage line rewritten in-place — confirms the durable side effect.
    assert 'stage: "paper"' in cfg_path.read_text(encoding="utf-8")

    # Exactly one promotion event + one manifest event were appended.
    promotions = store.list_promotions_for_strategy(_STRATEGY_ID)
    assert len(promotions) == 1
    assert promotions[0].id == result.promotion_id
    assert promotions[0].manifest_id == result.manifest_id


# ---------------------------------------------------------------------------
# RM-001 paper-tier parity guardrail
# ---------------------------------------------------------------------------


def test_paper_tier_pass_below_capital_sharpe_is_allowed(tmp_path):
    """Sharpe=0.2 passes paper gate (>0.0) but fails capital gate (>0.5).

    RM-001's whole point: paper promotion uses paper-tier thresholds. The
    orchestrator must not regress that.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    _seed_walk_forward_run(store, sharpe=0.2, max_drawdown_pct=12.0, trade_count=35)

    result = prepare_and_record_promotion(_statistical_request(cfg_path), store)

    assert isinstance(result, PromoteSuccess), (
        "paper-tier pass (Sharpe>0.0) should NOT be blocked by capital-tier threshold (Sharpe>0.5)."
    )
    assert result.promotion_type == "statistical"


def test_strategy_specific_min_trades_required_is_honored(tmp_path):
    """A YAML override below the default-30 floor must be respected."""
    store = EventStore(tmp_path / "milodex.db")
    # min_trades_required=10 — a 12-trade run should pass.
    cfg_path = _write_config(tmp_path, min_trades_required=10)
    _seed_walk_forward_run(store, sharpe=0.4, max_drawdown_pct=10.0, trade_count=12)

    result = prepare_and_record_promotion(_statistical_request(cfg_path), store)

    assert isinstance(result, PromoteSuccess)


# ---------------------------------------------------------------------------
# Gate failure — structured blocked result, no durable writes
# ---------------------------------------------------------------------------


def test_gate_failure_returns_blocked_and_writes_nothing(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    # Sharpe at threshold (paper requires >0.0; 0.0 fails).
    _seed_walk_forward_run(store, sharpe=0.0, max_drawdown_pct=10.0, trade_count=40)
    original_yaml = cfg_path.read_text(encoding="utf-8")

    result = prepare_and_record_promotion(_statistical_request(cfg_path), store)

    assert isinstance(result, PromoteBlocked)
    assert result.reason_code == REASON_GATE_FAILED
    assert result.gate_failures, "gate failures must be surfaced for caller rendering"
    assert result.metrics_snapshot == {
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 10.0,
        "trade_count": 40,
    }
    assert result.promotion_type == "statistical"
    # PromoteBlocked carries the stages so callers don't need to re-load the
    # config just to render an operator-facing "X -> Y" label.
    assert result.from_stage == "backtest"
    assert result.to_stage == "paper"
    # No durable side effects.
    assert cfg_path.read_text(encoding="utf-8") == original_yaml
    assert store.list_promotions_for_strategy(_STRATEGY_ID) == []


# ---------------------------------------------------------------------------
# Missing backtest run — structured blocked result, no durable writes
# ---------------------------------------------------------------------------


def test_missing_backtest_run_returns_blocked(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    original_yaml = cfg_path.read_text(encoding="utf-8")

    request = _statistical_request(cfg_path, run_id="nonexistent-run")
    result = prepare_and_record_promotion(request, store)

    assert isinstance(result, PromoteBlocked)
    assert result.reason_code == REASON_MISSING_BACKTEST_RUN
    assert "nonexistent-run" in result.message
    assert cfg_path.read_text(encoding="utf-8") == original_yaml
    assert store.list_promotions_for_strategy(_STRATEGY_ID) == []


# ---------------------------------------------------------------------------
# Invalid stage transition — structured blocked result, no durable writes
# ---------------------------------------------------------------------------


def test_invalid_stage_transition_returns_blocked(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    # Strategy already at paper → backtest->paper is invalid (same-stage).
    cfg_path = _write_config(tmp_path, stage="paper")
    _seed_walk_forward_run(store, sharpe=0.4, max_drawdown_pct=10.0, trade_count=40)
    original_yaml = cfg_path.read_text(encoding="utf-8")

    result = prepare_and_record_promotion(_statistical_request(cfg_path), store)

    assert isinstance(result, PromoteBlocked)
    assert result.reason_code == REASON_INVALID_STAGE_TRANSITION
    assert "already at stage" in result.message
    assert cfg_path.read_text(encoding="utf-8") == original_yaml
    assert store.list_promotions_for_strategy(_STRATEGY_ID) == []


# ---------------------------------------------------------------------------
# Lifecycle-exempt regime strategy bypasses statistical gate (ADR 0052)
# ---------------------------------------------------------------------------


def test_lifecycle_exempt_promotion_bypasses_statistical_gate(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    # No backtest run seeded; lifecycle-exempt passes run_id=None.

    result = prepare_and_record_promotion(_lifecycle_exempt_request(cfg_path), store)

    assert isinstance(result, PromoteSuccess)
    assert result.promotion_type == "lifecycle_exempt"
    assert result.metrics_snapshot == {
        "sharpe_ratio": None,
        "max_drawdown_pct": None,
        "trade_count": None,
    }
    assert result.backtest_run_id is None
    assert 'stage: "paper"' in cfg_path.read_text(encoding="utf-8")

    # D-4 (ADR 0058): the lifecycle path durably records the three R-PRM-004
    # criteria as unenforced + deferred to M4 — an honest ledger, not a silent
    # bypass.
    criteria = result.evidence.gate_check_outcome["lifecycle_criteria"]
    assert criteria["enforced"] is False
    assert criteria["deferred"] == "M4"
    assert len(criteria["criteria"]) == 3


# ---------------------------------------------------------------------------
# D-4 (ADR 0058): lifecycle exemption is scoped; operator override is split.
# ---------------------------------------------------------------------------


def test_lifecycle_exempt_refused_for_non_lifecycle_proof_strategy(tmp_path):
    """A non-regime strategy cannot claim the lifecycle exemption. Fail closed;
    point the operator at --operator-override. No durable writes."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, strategy_id=_NON_REGIME_ID)
    original_yaml = cfg_path.read_text(encoding="utf-8")

    request = PromoteRequest(
        strategy_id=_NON_REGIME_ID,
        config_path=cfg_path,
        to_stage="paper",
        recommendation="trying to sneak a non-regime strategy past the gate",
        known_risks=["this should be refused"],
        approved_by="operator",
        run_id=None,
        lifecycle_exempt=True,
        now=_NOW,
    )
    result = prepare_and_record_promotion(request, store)

    assert isinstance(result, PromoteBlocked)
    assert result.reason_code == REASON_LIFECYCLE_EXEMPT_NOT_SCOPED
    assert "operator-override" in result.message
    assert cfg_path.read_text(encoding="utf-8") == original_yaml
    assert store.list_promotions_for_strategy(_NON_REGIME_ID) == []


def test_both_bypass_flags_refused(tmp_path):
    """lifecycle_exempt and operator_override are mutually exclusive."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)

    request = PromoteRequest(
        strategy_id=_STRATEGY_ID,
        config_path=cfg_path,
        to_stage="paper",
        recommendation="cannot pick both",
        known_risks=["ambiguous intent"],
        approved_by="operator",
        run_id=None,
        lifecycle_exempt=True,
        operator_override=True,
        now=_NOW,
    )
    result = prepare_and_record_promotion(request, store)

    assert isinstance(result, PromoteBlocked)
    assert result.reason_code == REASON_OVERRIDE_FLAGS_CONFLICT
    assert store.list_promotions_for_strategy(_STRATEGY_ID) == []


def test_operator_override_to_paper_succeeds_and_records_reason(tmp_path):
    """A general operator override lands at paper with
    promotion_type='operator_override' and the reason recorded in durable
    evidence — for a non-regime strategy, with no backtest run."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, strategy_id=_NON_REGIME_ID)

    request = PromoteRequest(
        strategy_id=_NON_REGIME_ID,
        config_path=cfg_path,
        to_stage="paper",
        recommendation="deliberate operator bypass: platform smoke of a new edge",
        known_risks=["statistical gate deliberately skipped"],
        approved_by="operator",
        run_id=None,
        operator_override=True,
        now=_NOW,
    )
    result = prepare_and_record_promotion(request, store)

    assert isinstance(result, PromoteSuccess)
    assert result.promotion_type == "operator_override"
    assert 'stage: "paper"' in cfg_path.read_text(encoding="utf-8")

    override = result.evidence.gate_check_outcome["operator_override"]
    assert override["reason"].startswith("deliberate operator bypass")

    promotions = store.list_promotions_for_strategy(_NON_REGIME_ID)
    assert len(promotions) == 1
    assert promotions[0].promotion_type == "operator_override"


def test_operator_override_to_micro_live_refused(tmp_path):
    """operator_override may never reach a capital stage. End-to-end, a
    micro_live override is refused with no durable write. (In Phase 1 the
    stage-transition Phase-1 lock fires first — R-PRM-006 — so the refusal
    reason is invalid_stage_transition; the paper-only override guard is
    unit-tested separately below to prove it independently.)"""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper", strategy_id=_NON_REGIME_ID)
    original_yaml = cfg_path.read_text(encoding="utf-8")

    request = PromoteRequest(
        strategy_id=_NON_REGIME_ID,
        config_path=cfg_path,
        to_stage="micro_live",
        recommendation="trying to override into a capital stage",
        known_risks=["should be refused by the autonomy boundary"],
        approved_by="operator",
        run_id=None,
        operator_override=True,
        now=_NOW,
    )
    result = prepare_and_record_promotion(request, store)

    assert isinstance(result, PromoteBlocked)
    # Refused, no durable write, config untouched — the essential guarantee.
    assert cfg_path.read_text(encoding="utf-8") == original_yaml
    assert store.list_promotions_for_strategy(_NON_REGIME_ID) == []


def test_operator_override_stage_guard_refuses_capital_stages():
    """Unit-test the paper-only override guard in isolation (independent of the
    Phase-1 stage lock): a capital-stage operator_override is refused with
    REASON_OVERRIDE_STAGE_NOT_ALLOWED."""
    request = PromoteRequest(
        strategy_id=_NON_REGIME_ID,
        config_path=Path("unused.yaml"),
        to_stage="micro_live",
        recommendation="capital-stage override attempt",
        known_risks=["should be refused"],
        approved_by="operator",
        operator_override=True,
        now=_NOW,
    )
    refusal = _check_bypass_admissibility(request, from_stage="paper", to_stage="micro_live")
    assert isinstance(refusal, PromoteBlocked)
    assert refusal.reason_code == REASON_OVERRIDE_STAGE_NOT_ALLOWED


# ---------------------------------------------------------------------------
# Atomicity — a transition-time failure surfaces as PromoteError, not silent partial-write
# ---------------------------------------------------------------------------


def test_transition_failure_returns_promote_error_without_silent_partial_write(tmp_path):
    """If ``transition()`` raises after the gate passes, the orchestrator must
    surface it as ``PromoteError`` — never claim PromoteSuccess. The
    durable-log-first / YAML-after sequence inside ``transition()`` already
    guarantees coherent state; the orchestrator's contract is to not lie
    about success.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    _seed_walk_forward_run(store, sharpe=0.4, max_drawdown_pct=10.0, trade_count=40)

    # Force the underlying transition() to raise, simulating a ValueError
    # that the governance layer raises (e.g. YAML stage-line not found).
    with patch(
        "milodex.promotion.orchestrator.transition",
        side_effect=ValueError("could not find stage line"),
    ):
        result = prepare_and_record_promotion(_statistical_request(cfg_path), store)

    assert isinstance(result, PromoteError)
    assert "could not find stage line" in result.message
    assert result.context.get("callee", "").endswith("state_machine.transition")

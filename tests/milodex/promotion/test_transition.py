"""Tests for the transactional ``transition`` helper in state_machine.py."""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.core.event_store import EventStore
from milodex.promotion import (
    EvidencePackage,
    PromotionCheckResult,
    assemble_evidence_package,
)
from milodex.promotion.state_machine import transition
from milodex.strategies.loader import compute_config_hash

_NOW = datetime(2026, 4, 23, 18, 0, tzinfo=UTC)
_STRATEGY_ID = "regime.daily.sma200_rotation.demo.v1"


def _write_config(tmp_path: Path, *, stage: str = "backtest") -> Path:
    path = tmp_path / "demo_strategy.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            strategy:
              id: "{_STRATEGY_ID}"
              family: "regime"
              template: "daily.sma200_rotation"
              variant: "demo"
              version: 1
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
                min_trades_required: null
                walk_forward_windows: 1
              disable_conditions_additional: []
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _build_evidence(
    store: EventStore, *, manifest_hash: str, to_stage: str = "paper"
) -> EvidencePackage:
    return assemble_evidence_package(
        strategy_id=_STRATEGY_ID,
        from_stage="backtest",
        to_stage=to_stage,
        manifest_hash=manifest_hash,
        backtest_run_id=None,
        recommendation="lifecycle-exempt regime strategy ready for paper",
        known_risks=["drift-check relies on manifest freezing correctly"],
        promotion_type="lifecycle_exempt",
        gate_check_outcome={"lifecycle_exempt": True},
        metrics_snapshot={"sharpe_ratio": None, "max_drawdown_pct": None, "trade_count": None},
        event_store=store,
        now=_NOW,
    )


def _hash_at_stage(cfg_path: Path, stage: str) -> str:
    """Compute the hash the YAML WILL have after transition() updates it."""
    original = cfg_path.read_text(encoding="utf-8")
    try:
        cfg_path.write_text(
            original.replace('stage: "backtest"', f'stage: "{stage}"'),
            encoding="utf-8",
        )
        return compute_config_hash(cfg_path)
    finally:
        cfg_path.write_text(original, encoding="utf-8")


def test_transition_writes_manifest_and_promotion_atomically(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    to_stage_hash = _hash_at_stage(cfg_path, "paper")
    evidence = _build_evidence(store, manifest_hash=to_stage_hash)
    gate = PromotionCheckResult(allowed=True, promotion_type="lifecycle_exempt")

    promotion = transition(
        config_path=cfg_path,
        to_stage="paper",
        gate_result=gate,
        evidence=evidence,
        approved_by="operator",
        event_store=store,
        now=_NOW,
    )

    assert promotion.id is not None
    assert promotion.manifest_id is not None
    assert promotion.from_stage == "backtest"
    assert promotion.to_stage == "paper"
    assert promotion.evidence_json is not None
    assert promotion.evidence_json["recommendation"].startswith("lifecycle-exempt")

    manifest = store.get_active_manifest_for_strategy(_STRATEGY_ID, "paper")
    assert manifest is not None
    assert manifest.stage == "paper"
    assert manifest.config_hash == to_stage_hash

    # Both rows share a transaction; promotion's manifest_id points at it.
    assert promotion.manifest_id == manifest.id


def test_transition_updates_yaml_stage_line_after_commit(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    to_stage_hash = _hash_at_stage(cfg_path, "paper")
    evidence = _build_evidence(store, manifest_hash=to_stage_hash)
    gate = PromotionCheckResult(allowed=True, promotion_type="lifecycle_exempt")

    transition(
        config_path=cfg_path,
        to_stage="paper",
        gate_result=gate,
        evidence=evidence,
        approved_by="operator",
        event_store=store,
        now=_NOW,
    )

    assert 'stage: "paper"' in cfg_path.read_text(encoding="utf-8")
    # Runtime hash of the updated YAML matches the frozen manifest hash.
    assert compute_config_hash(cfg_path) == to_stage_hash


def test_transition_refuses_when_gate_failed(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    to_stage_hash = _hash_at_stage(cfg_path, "paper")
    evidence = _build_evidence(store, manifest_hash=to_stage_hash)
    gate = PromotionCheckResult(
        allowed=False, promotion_type="statistical", failures=["Sharpe too low"]
    )

    with pytest.raises(ValueError, match="gate"):
        transition(
            config_path=cfg_path,
            to_stage="paper",
            gate_result=gate,
            evidence=evidence,
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    # Nothing written; YAML untouched.
    assert store.list_promotions() == []
    assert store.list_strategy_manifests() == []
    assert 'stage: "backtest"' in cfg_path.read_text(encoding="utf-8")


def test_transition_refuses_when_evidence_hash_mismatches(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    evidence = _build_evidence(store, manifest_hash="0" * 64)
    gate = PromotionCheckResult(allowed=True, promotion_type="lifecycle_exempt")

    with pytest.raises(ValueError, match="manifest_hash"):
        transition(
            config_path=cfg_path,
            to_stage="paper",
            gate_result=gate,
            evidence=evidence,
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    assert store.list_promotions() == []
    assert store.list_strategy_manifests() == []

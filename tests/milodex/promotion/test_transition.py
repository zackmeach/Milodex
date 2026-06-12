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
from milodex.promotion.state_machine import _update_stage_in_yaml, transition
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


@pytest.mark.parametrize(
    "stage_line",
    [
        '  stage: "backtest"',  # canonical double-quoted (control)
        "  stage: 'backtest'",  # single-quoted
        "  stage: backtest",  # unquoted
        "  stage:    backtest",  # extra whitespace, unquoted
        '  stage:  "backtest"  ',  # trailing whitespace, double-quoted
    ],
)
def test_update_stage_in_yaml_tolerates_quoting_and_whitespace(tmp_path, stage_line):
    """A real promotion must not leave the YAML stale just because the
    ``stage:`` line is single-quoted, unquoted, or differently spaced —
    that would block all trading for the strategy on the next runner cycle.
    """
    path = tmp_path / "demo.yaml"
    path.write_text(
        f'strategy:\n  id: "demo.v1"\n{stage_line}\n  enabled: true  # keep this comment\n',
        encoding="utf-8",
    )

    _update_stage_in_yaml(path, "backtest", "paper")

    content = path.read_text(encoding="utf-8")
    # Stage line now reflects the target stage.
    assert "backtest" not in content
    assert "stage:" in content
    # Surrounding lines and comments are preserved.
    assert '  id: "demo.v1"' in content
    assert "  enabled: true  # keep this comment" in content
    # Reloading shows the new stage value regardless of original quoting.
    import yaml as _yaml

    assert _yaml.safe_load(content)["strategy"]["stage"] == "paper"


def test_update_stage_in_yaml_raises_when_from_stage_absent(tmp_path):
    """A genuine from-stage mismatch must still fail loudly — never a
    silent no-op that masks a real divergence."""
    path = tmp_path / "demo.yaml"
    path.write_text(
        'strategy:\n  id: "demo.v1"\n  stage: paper\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="backtest"):
        _update_stage_in_yaml(path, "backtest", "paper")


def test_transition_with_inline_comment_on_stage_line_fails_before_any_db_write(tmp_path):
    """A ``stage: "backtest"  # comment`` line does not match the rewrite
    regex (trailing comments are deliberately unmatched). The rewrite is
    precomputed BEFORE the event-store commit, so the failure must leave
    no durable rows — not strand a manifest+promotion ahead of a failed
    YAML update."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    original = cfg_path.read_text(encoding="utf-8")
    cfg_path.write_text(
        original.replace('stage: "backtest"', 'stage: "backtest"  # promoted by hand'),
        encoding="utf-8",
    )
    # Comments don't affect the canonical hash, so the evidence hash check passes.
    to_stage_hash = _hash_at_stage(cfg_path, "paper")
    evidence = _build_evidence(store, manifest_hash=to_stage_hash)
    gate = PromotionCheckResult(allowed=True, promotion_type="lifecycle_exempt")

    with pytest.raises(ValueError, match="before any durable state is written"):
        transition(
            config_path=cfg_path,
            to_stage="paper",
            gate_result=gate,
            evidence=evidence,
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    # Nothing durable was appended; YAML untouched.
    assert store.list_promotions() == []
    assert store.list_strategy_manifests() == []
    assert 'stage: "backtest"  # promoted by hand' in cfg_path.read_text(encoding="utf-8")


def test_transition_yaml_write_failure_after_commit_keeps_durable_rows(tmp_path, monkeypatch):
    """Durable-log-first ordering: if the YAML write itself fails AFTER the
    event-store commit, the manifest and promotion rows must survive and the
    error must say so — naming the drift-check backstop (message contract)."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    to_stage_hash = _hash_at_stage(cfg_path, "paper")
    evidence = _build_evidence(store, manifest_hash=to_stage_hash)
    gate = PromotionCheckResult(allowed=True, promotion_type="lifecycle_exempt")

    def _boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)

    with pytest.raises(
        ValueError,
        match=(
            "Durable state is written, but the YAML could not be updated; "
            "the next cycle's drift check will flag this discrepancy"
        ),
    ):
        transition(
            config_path=cfg_path,
            to_stage="paper",
            gate_result=gate,
            evidence=evidence,
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    monkeypatch.undo()

    # Durable rows exist; the YAML is stale (still at backtest).
    promotions = store.list_promotions()
    manifests = store.list_strategy_manifests()
    assert len(promotions) == 1
    assert len(manifests) == 1
    assert promotions[0].to_stage == "paper"
    assert manifests[0].config_hash == to_stage_hash
    assert 'stage: "backtest"' in cfg_path.read_text(encoding="utf-8")


def test_transition_statistical_happy_path_writes_atomically_and_advances(tmp_path):
    """The statistical (non-exempt) promotion happy path through
    ``transition()`` — gate passes on real metrics, atomic manifest+promotion
    write, stage advance — is otherwise never exercised end-to-end."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)
    to_stage_hash = _hash_at_stage(cfg_path, "paper")
    evidence = assemble_evidence_package(
        strategy_id=_STRATEGY_ID,
        from_stage="backtest",
        to_stage="paper",
        manifest_hash=to_stage_hash,
        backtest_run_id=None,
        recommendation="statistical edge cleared the paper-readiness gate",
        known_risks=["sample size near the floor"],
        promotion_type="statistical",
        gate_check_outcome={"allowed": True},
        metrics_snapshot={
            "sharpe_ratio": 1.2,
            "max_drawdown_pct": 8.0,
            "trade_count": 42,
        },
        event_store=store,
        now=_NOW,
    )
    gate = PromotionCheckResult(
        allowed=True,
        promotion_type="statistical",
        failures=[],
        sharpe_ratio=1.2,
        max_drawdown_pct=8.0,
        trade_count=42,
    )

    promotion = transition(
        config_path=cfg_path,
        to_stage="paper",
        gate_result=gate,
        evidence=evidence,
        approved_by="operator",
        event_store=store,
        now=_NOW,
    )

    assert promotion.promotion_type == "statistical"
    assert promotion.from_stage == "backtest"
    assert promotion.to_stage == "paper"
    assert promotion.sharpe_ratio == 1.2
    assert promotion.max_drawdown_pct == 8.0
    assert promotion.trade_count == 42

    manifest = store.get_active_manifest_for_strategy(_STRATEGY_ID, "paper")
    assert manifest is not None
    assert manifest.stage == "paper"
    assert manifest.config_hash == to_stage_hash
    # Atomic write: both rows share the same transaction.
    assert promotion.manifest_id == manifest.id
    # Stage advanced on disk.
    assert 'stage: "paper"' in cfg_path.read_text(encoding="utf-8")

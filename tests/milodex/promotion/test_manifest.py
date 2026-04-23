"""Tests for ``milodex.promotion.manifest`` (freeze + active-hash read)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from milodex.core.event_store import EventStore
from milodex.promotion import (
    freeze_manifest,
    get_active_manifest_hash,
    resolve_strategy_config_path,
)
from milodex.strategies.loader import compute_config_hash


def _write_config(
    tmp_path: Path,
    *,
    filename: str = "demo_strategy.yaml",
    strategy_id: str = "regime.daily.sma200_rotation.demo.v1",
    stage: str = "paper",
    ma_length: int = 200,
    family: str = "regime",
    template: str = "daily.sma200_rotation",
    variant: str = "demo",
) -> Path:
    path = tmp_path / filename
    path.write_text(
        textwrap.dedent(
            f"""
            strategy:
              id: "{strategy_id}"
              family: "{family}"
              template: "{template}"
              variant: "{variant}"
              version: 1
              description: "test"
              enabled: true
              universe:
                - "SPY"
                - "SHY"
              parameters:
                ma_filter_length: {ma_length}
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


def test_freeze_manifest_hash_matches_compute_config_hash(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)

    event = freeze_manifest(cfg_path, event_store=store)

    assert event.id is not None
    assert event.strategy_id == "regime.daily.sma200_rotation.demo.v1"
    assert event.stage == "paper"
    assert event.config_hash == compute_config_hash(cfg_path)
    assert event.config_path == str(cfg_path)


def test_freeze_manifest_reads_stage_from_config(tmp_path):
    """AD-8: freeze takes no --stage; stage comes from the YAML."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="micro_live")

    event = freeze_manifest(cfg_path, event_store=store)
    assert event.stage == "micro_live"

    active = store.get_active_manifest_for_strategy(
        "regime.daily.sma200_rotation.demo.v1", "micro_live"
    )
    assert active is not None
    assert active.config_hash == event.config_hash


def test_freeze_manifest_refuses_backtest_stage(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="backtest")

    with pytest.raises(ValueError, match="backtest"):
        freeze_manifest(cfg_path, event_store=store)


def test_freeze_manifest_stores_canonical_config_json(tmp_path):
    """Stored ``config_json`` equals the canonicalized form that fed the hash."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)

    event = freeze_manifest(cfg_path, event_store=store)
    # Canonicalization sorts keys — the top-level key is "strategy".
    assert "strategy" in event.config_json
    assert event.config_json["strategy"]["stage"] == "paper"
    # Dict keys survive in canonical (sorted) order.
    keys = list(event.config_json["strategy"])
    assert keys == sorted(keys)


def test_get_active_manifest_hash_returns_none_when_unfrozen(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert get_active_manifest_hash("nobody.v1", "paper", store) is None


def test_get_active_manifest_hash_returns_latest_freeze(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, ma_length=200)
    first = freeze_manifest(cfg_path, event_store=store)

    # Drift the config, freeze again — active hash should advance.
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace(
            "ma_filter_length: 200", "ma_filter_length: 201"
        ),
        encoding="utf-8",
    )
    second = freeze_manifest(cfg_path, event_store=store)
    assert second.config_hash != first.config_hash

    active = get_active_manifest_hash("regime.daily.sma200_rotation.demo.v1", "paper", store)
    assert active == second.config_hash


def test_resolve_strategy_config_path_finds_file(tmp_path):
    cfg_path = _write_config(
        tmp_path,
        filename="a.yaml",
        strategy_id="regime.daily.sma200_rotation.alpha.v1",
        variant="alpha",
    )
    _write_config(
        tmp_path,
        filename="b.yaml",
        strategy_id="regime.daily.sma200_rotation.beta.v1",
        variant="beta",
    )

    resolved = resolve_strategy_config_path("regime.daily.sma200_rotation.alpha.v1", tmp_path)
    assert resolved == cfg_path


def test_resolve_strategy_config_path_raises_when_missing(tmp_path):
    _write_config(tmp_path, filename="a.yaml")
    with pytest.raises(ValueError, match="Strategy config not found"):
        resolve_strategy_config_path("ghost.v1", tmp_path)


def test_freeze_manifest_always_appends_even_on_identical_hash(tmp_path):
    """AD-5: a re-freeze that produces the same hash still writes a new row."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)

    freeze_manifest(cfg_path, event_store=store)
    freeze_manifest(cfg_path, event_store=store)

    rows = store.list_strategy_manifests()
    assert len(rows) == 2
    assert rows[0].config_hash == rows[1].config_hash

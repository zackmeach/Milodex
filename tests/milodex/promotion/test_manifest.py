"""Tests for ``milodex.promotion.manifest`` (freeze + active-hash read)."""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

import pytest

from milodex.core.event_store import EventStore
from milodex.promotion import (
    FROZEN_STAGES,
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
    display_name: str | None = None,
) -> Path:
    path = tmp_path / filename
    display_name_line = (
        f'              display_name: "{display_name}"\n' if display_name is not None else ""
    )
    path.write_text(
        textwrap.dedent(
            f"""
            strategy:
              id: "{strategy_id}"
{display_name_line.rstrip()}
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


def _hash_canonical(canonical) -> str:
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_freeze_manifest_hash_is_derived_from_stored_config_json(tmp_path):
    """'What you hashed is what you stored' must hold by construction.

    ``transition()`` hashes the exact canonical dict it stores. ``freeze_manifest``
    must do the same — computing the hash from the same canonical dict that is
    serialized into ``config_json``, not from an independent file re-read whose
    canonicalization path can silently diverge from the stored JSON.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, display_name="Demo Regime")

    event = freeze_manifest(cfg_path, event_store=store)

    # The stored hash equals a hash recomputed over the stored config_json.
    assert event.config_hash == _hash_canonical(event.config_json)

    # And the persisted row carries the same invariant.
    active = store.get_active_manifest_for_strategy("regime.daily.sma200_rotation.demo.v1", "paper")
    assert active is not None
    assert active.config_hash == _hash_canonical(active.config_json)


def test_freeze_manifest_excludes_display_name_from_hashed_config_json(tmp_path):
    """Presentation labels must not drift from the exact config material hashed."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, display_name="Demo Regime")

    event = freeze_manifest(cfg_path, event_store=store)

    assert event.config_hash == compute_config_hash(cfg_path)
    assert "display_name" not in event.config_json["strategy"]


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


def test_frozen_stages_is_exactly_paper_micro_live_live():
    """``FROZEN_STAGES`` must be *exactly* the three promoted stages.

    Freezing binds an immutable manifest — the production-evidence
    guarantee of ADR 0015/0030. Which stages freeze is a governance
    invariant: ``backtest`` is exploratory (no frozen manifest), while
    ``paper``/``micro_live``/``live`` each carry one. A silent add or
    remove here would change what evidence the system promises. Pinning
    the whole set (not just membership) closes the TEST_EFFICACY_AUDIT
    surviving mutant on this constant.
    """
    assert FROZEN_STAGES == frozenset({"paper", "micro_live", "live"})


def test_freeze_manifest_always_appends_even_on_identical_hash(tmp_path):
    """AD-5: a re-freeze that produces the same hash still writes a new row."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path)

    freeze_manifest(cfg_path, event_store=store)
    freeze_manifest(cfg_path, event_store=store)

    rows = store.list_strategy_manifests()
    assert len(rows) == 2
    assert rows[0].config_hash == rows[1].config_hash

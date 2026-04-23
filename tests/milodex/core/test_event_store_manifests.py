"""Tests for the ``strategy_manifests`` event-store surface (ADR 0015)."""

from __future__ import annotations

from datetime import UTC, datetime

from milodex.core.event_store import EventStore, StrategyManifestEvent


def _event(
    *,
    strategy_id: str = "regime.daily.sma200_rotation.spy_shy.v1",
    stage: str = "paper",
    config_hash: str = "a" * 64,
    config_json: dict | None = None,
    config_path: str = "configs/regime_spy_shy_200dma.yaml",
    frozen_at: datetime | None = None,
    frozen_by: str = "operator",
) -> StrategyManifestEvent:
    return StrategyManifestEvent(
        strategy_id=strategy_id,
        stage=stage,
        config_hash=config_hash,
        config_json=config_json or {"strategy": {"id": strategy_id}},
        config_path=config_path,
        frozen_at=frozen_at or datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
        frozen_by=frozen_by,
    )


def test_append_and_read_manifest_round_trip(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    event_id = store.append_strategy_manifest(_event())

    manifests = store.list_strategy_manifests()
    assert len(manifests) == 1
    row = manifests[0]
    assert row.id == event_id
    assert row.strategy_id == "regime.daily.sma200_rotation.spy_shy.v1"
    assert row.stage == "paper"
    assert row.config_hash == "a" * 64
    assert row.config_json == {"strategy": {"id": "regime.daily.sma200_rotation.spy_shy.v1"}}
    assert row.config_path == "configs/regime_spy_shy_200dma.yaml"
    assert row.frozen_by == "operator"
    assert row.frozen_at == datetime(2026, 4, 23, 12, 0, tzinfo=UTC)


def test_get_active_manifest_returns_latest_for_stage(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    older = _event(
        config_hash="1" * 64,
        frozen_at=datetime(2026, 4, 23, 10, 0, tzinfo=UTC),
    )
    newer = _event(
        config_hash="2" * 64,
        frozen_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
    )
    store.append_strategy_manifest(older)
    store.append_strategy_manifest(newer)

    active = store.get_active_manifest_for_strategy(
        "regime.daily.sma200_rotation.spy_shy.v1", "paper"
    )
    assert active is not None
    assert active.config_hash == "2" * 64


def test_get_active_manifest_returns_none_when_unfrozen(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.get_active_manifest_for_strategy("not.a.real.strategy", "paper") is None


def test_manifests_segregate_by_stage(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_strategy_manifest(_event(stage="paper", config_hash="p" * 64))
    store.append_strategy_manifest(_event(stage="micro_live", config_hash="m" * 64))

    paper = store.get_active_manifest_for_strategy(
        "regime.daily.sma200_rotation.spy_shy.v1", "paper"
    )
    micro = store.get_active_manifest_for_strategy(
        "regime.daily.sma200_rotation.spy_shy.v1", "micro_live"
    )
    assert paper is not None and paper.config_hash == "p" * 64
    assert micro is not None and micro.config_hash == "m" * 64


def test_manifests_segregate_by_strategy_id(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_strategy_manifest(_event(strategy_id="alpha.v1", config_hash="a" * 64))
    store.append_strategy_manifest(_event(strategy_id="beta.v1", config_hash="b" * 64))

    assert store.get_active_manifest_for_strategy("alpha.v1", "paper").config_hash == "a" * 64
    assert store.get_active_manifest_for_strategy("beta.v1", "paper").config_hash == "b" * 64


def test_append_is_always_append_even_when_hash_unchanged(tmp_path):
    """AD-5: re-freeze writes a fresh row even if the hash matches."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_strategy_manifest(_event())
    store.append_strategy_manifest(_event())

    assert len(store.list_strategy_manifests()) == 2

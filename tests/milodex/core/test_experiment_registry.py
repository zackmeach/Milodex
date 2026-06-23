"""Tests for the append-only experiment registry store (R-PRM-011, migration 015)."""

from __future__ import annotations

from pathlib import Path

import pytest

from milodex.core.event_store import EventStore, ExperimentEvent


def _event(experiment_id: str, **overrides: object) -> ExperimentEvent:
    """Build an ExperimentEvent with sensible defaults; recorded_at is server-stamped
    for ``update_experiment`` but explicit on ``append_experiment``."""
    from datetime import UTC, datetime

    fields: dict[str, object] = {
        "experiment_id": experiment_id,
        "hypothesis": "RSI(2) mean-reversion on intraday ETFs beats buy-and-hold",
        "stage_reached": "backtest",
        "terminal_status": "active",
        "rationale": "Under evaluation.",
        "recorded_at": datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return ExperimentEvent(**fields)  # type: ignore[arg-type]


def test_append_then_get_roundtrips_all_fields(tmp_path):
    store = EventStore(tmp_path / "data" / "milodex.db")

    new_id = store.append_experiment(
        _event(
            "intraday-etf-rsi2-2026-06",
            strategy_id="meanrev.rsi2.intraday_spy.v1",
            config_hash="c" * 64,
            terminal_status="rejected",
            rationale="OOS Sharpe below capital-readiness threshold.",
            evidence_json={
                "run_ids": ["bt-1", "bt-2"],
                "per_symbol": {"SPY": {"candidate": 0.4, "baseline": 0.7}},
                "readiness_ref": "readiness-2026-06-19",
            },
            lessons="RSI(2) intraday edge does not survive IEX fidelity gate.",
            revisitable=True,
        )
    )

    fetched = store.get_experiment("intraday-etf-rsi2-2026-06")
    assert fetched is not None
    assert fetched.id == new_id
    assert fetched.experiment_id == "intraday-etf-rsi2-2026-06"
    assert fetched.strategy_id == "meanrev.rsi2.intraday_spy.v1"
    assert fetched.config_hash == "c" * 64
    assert fetched.hypothesis.startswith("RSI(2)")
    assert fetched.stage_reached == "backtest"
    assert fetched.terminal_status == "rejected"
    assert fetched.rationale == "OOS Sharpe below capital-readiness threshold."
    assert fetched.evidence_json == {
        "run_ids": ["bt-1", "bt-2"],
        "per_symbol": {"SPY": {"candidate": 0.4, "baseline": 0.7}},
        "readiness_ref": "readiness-2026-06-19",
    }
    assert fetched.lessons == "RSI(2) intraday edge does not survive IEX fidelity gate."
    assert fetched.revisitable is True


def test_nullable_fields_roundtrip_as_none(tmp_path):
    store = EventStore(tmp_path / "milodex.db")

    store.append_experiment(_event("idea-only-2026-06"))

    fetched = store.get_experiment("idea-only-2026-06")
    assert fetched is not None
    assert fetched.strategy_id is None
    assert fetched.config_hash is None
    assert fetched.evidence_json is None
    assert fetched.lessons is None
    assert fetched.revisitable is False


def test_get_experiment_unknown_returns_none(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.get_experiment("never-registered") is None


def test_update_appends_new_row_and_preserves_prior(tmp_path):
    store = EventStore(tmp_path / "milodex.db")

    first_id = store.append_experiment(
        _event("intraday-etf-rsi2-2026-06", terminal_status="active")
    )

    def _row_count() -> int:
        import sqlite3

        with sqlite3.connect(tmp_path / "milodex.db") as con:
            return con.execute("SELECT COUNT(*) FROM experiment_registry").fetchone()[0]

    assert _row_count() == 1

    new_id = store.update_experiment(
        "intraday-etf-rsi2-2026-06",
        terminal_status="rejected",
        rationale="Failed the capital-readiness gate.",
        lessons="Edge vanished after slippage.",
    )

    # A NEW row was inserted, not an in-place update.
    assert _row_count() == 2
    assert new_id != first_id

    # get_experiment returns the updated (latest) values.
    latest = store.get_experiment("intraday-etf-rsi2-2026-06")
    assert latest is not None
    assert latest.id == new_id
    assert latest.terminal_status == "rejected"
    assert latest.rationale == "Failed the capital-readiness gate."
    assert latest.lessons == "Edge vanished after slippage."
    # Carried-forward field unchanged.
    assert latest.hypothesis.startswith("RSI(2)")

    # The prior row still exists, unchanged — proves no in-place mutation.
    import sqlite3

    with sqlite3.connect(tmp_path / "milodex.db") as con:
        con.row_factory = sqlite3.Row
        prior = con.execute(
            "SELECT * FROM experiment_registry WHERE id = ?", (first_id,)
        ).fetchone()
    assert prior is not None
    assert prior["terminal_status"] == "active"
    assert prior["rationale"] == "Under evaluation."
    assert prior["lessons"] is None


def test_update_missing_experiment_raises(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    with pytest.raises(KeyError):
        store.update_experiment("never-registered", terminal_status="abandoned")


def test_update_rejects_unknown_field(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_experiment(_event("exp-1"))
    with pytest.raises(TypeError):
        store.update_experiment("exp-1", not_a_field="x")


def test_list_returns_latest_per_experiment(tmp_path):
    store = EventStore(tmp_path / "milodex.db")

    store.append_experiment(_event("exp-a", terminal_status="active"))
    store.append_experiment(_event("exp-b", terminal_status="active"))
    # Second version of exp-a — only the latest should appear.
    store.update_experiment("exp-a", terminal_status="promoted")

    listed = store.list_experiments()
    by_id = {e.experiment_id: e for e in listed}
    assert set(by_id) == {"exp-a", "exp-b"}
    assert by_id["exp-a"].terminal_status == "promoted"
    assert by_id["exp-b"].terminal_status == "active"
    # Deterministic order: latest row id DESC. exp-a's promoted row (id 3) is the
    # newest, so it sorts first.
    assert [e.experiment_id for e in listed] == ["exp-a", "exp-b"]


def test_list_filters_by_terminal_status(tmp_path):
    store = EventStore(tmp_path / "milodex.db")

    store.append_experiment(_event("exp-a", terminal_status="rejected"))
    store.append_experiment(_event("exp-b", terminal_status="active"))
    store.append_experiment(_event("exp-c", terminal_status="rejected"))

    rejected = store.list_experiments(terminal_status="rejected")
    assert {e.experiment_id for e in rejected} == {"exp-a", "exp-c"}
    assert all(e.terminal_status == "rejected" for e in rejected)

    assert store.list_experiments(terminal_status="abandoned") == []


def test_list_filter_reflects_latest_status_only(tmp_path):
    """An experiment whose latest row moved off 'active' must not match the old status."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_experiment(_event("exp-a", terminal_status="active"))
    store.update_experiment("exp-a", terminal_status="abandoned")

    assert store.list_experiments(terminal_status="active") == []
    abandoned = store.list_experiments(terminal_status="abandoned")
    assert [e.experiment_id for e in abandoned] == ["exp-a"]


def test_schema_version_is_16_after_construction(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.schema_version == 16


def test_no_delete_or_in_place_mutate_path_in_source():
    """R-PRM-011: no delete path, and the registry is never mutated in place.

    Asserts the event-store source contains no DELETE/UPDATE statement against
    experiment_registry and exposes no delete/remove method — the version
    history is the append-only row sequence, nothing else.
    """
    source = (
        Path(__file__).resolve().parents[3] / "src" / "milodex" / "core" / "event_store.py"
    ).read_text(encoding="utf-8")
    lowered = source.lower()
    assert "delete from experiment_registry" not in lowered
    assert "update experiment_registry" not in lowered

    assert not hasattr(EventStore, "delete_experiment")
    assert not hasattr(EventStore, "remove_experiment")

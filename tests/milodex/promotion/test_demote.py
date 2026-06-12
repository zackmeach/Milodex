"""Direct unit tests for the ``demote`` helper in state_machine.py.

The mutation audit (``docs/TEST_EFFICACY_AUDIT.md`` §B.2) flagged that
``demote()`` had no test coverage in ``tests/milodex/promotion/`` —
its only tests live under ``tests/milodex/cli/test_promotion_demote.py``,
which the per-file mutation scoping excludes. About 30 of the 74
state-machine survivors land inside this function. These tests close
the gap with direct unit coverage matching the style of
``test_transition.py``.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.core.event_store import EventStore, PromotionEvent
from milodex.promotion.state_machine import demote

_NOW = datetime(2026, 5, 6, 18, 0, tzinfo=UTC)
_STRATEGY_ID = "regime.daily.demote_unit.demo.v1"


def _write_config(tmp_path: Path, *, stage: str = "paper") -> Path:
    path = tmp_path / "demote_strategy.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            strategy:
              id: "{_STRATEGY_ID}"
              family: "regime"
              template: "daily.demote_unit"
              variant: "demo"
              version: 1
              description: "demote unit test fixture"
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


def _seed_paper_promotion(store: EventStore, *, recorded_at: datetime = _NOW) -> int:
    """Insert a non-demotion promotion row that ``demote`` should reverse."""
    promotion = PromotionEvent(
        strategy_id=_STRATEGY_ID,
        from_stage="backtest",
        to_stage="paper",
        promotion_type="lifecycle_exempt",
        approved_by="operator",
        recorded_at=recorded_at,
    )
    return store.append_promotion(promotion)


# ---------------------------------------------------------------------------
# Happy path (mutation audit Critical #3 coverage)
# ---------------------------------------------------------------------------


def test_demote_to_backtest_writes_promotion_event_and_updates_yaml(tmp_path):
    """Kills mutations clustered in demote() lines 257-336 (audit B.2 #94-128).

    Direct unit coverage of the happy path: a paper-stage strategy with
    a prior promotion is demoted to backtest. Asserts on the durable
    PromotionEvent shape (promotion_type='demotion', reverses_event_id
    set, notes carries the reason) and on the YAML side-effect.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")
    prior_id = _seed_paper_promotion(store)

    result = demote(
        config_path=cfg_path,
        to_stage="backtest",
        reason="restaging for verification",
        approved_by="operator",
        event_store=store,
        now=_NOW,
    )

    assert result.promotion_type == "demotion"
    assert result.from_stage == "paper"
    assert result.to_stage == "backtest"
    assert result.reverses_event_id == prior_id
    assert result.approved_by == "operator"
    assert result.recorded_at == _NOW
    assert result.notes == "restaging for verification"
    assert result.id is not None

    # YAML stage line was rewritten in place.
    assert 'stage: "backtest"' in cfg_path.read_text(encoding="utf-8")


def test_demote_to_disabled_does_not_touch_yaml(tmp_path):
    """Kills mutations on the ``to_stage == 'backtest'`` YAML-side-effect branch.

    A demote to ``disabled`` writes the governance-ledger event but
    leaves the YAML untouched (per docstring contract). A mutation
    flipping the equality check would either rewrite the YAML on
    ``disabled`` or skip rewriting it on ``backtest``.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")
    _seed_paper_promotion(store)

    result = demote(
        config_path=cfg_path,
        to_stage="disabled",
        reason="retiring pending rewrite",
        approved_by="operator",
        event_store=store,
        now=_NOW,
    )

    assert result.to_stage == "disabled"
    assert result.promotion_type == "demotion"
    # YAML is untouched — stage line still reads the pre-demote value.
    assert 'stage: "paper"' in cfg_path.read_text(encoding="utf-8")


def test_demote_carries_evidence_ref_in_notes(tmp_path):
    """Pin the evidence_ref formatting to keep the governance-ledger contract.

    A mutation to the ``" | "`` separator or the ``evidence_ref=`` prefix
    would break the parseability of the notes field.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")
    _seed_paper_promotion(store)

    result = demote(
        config_path=cfg_path,
        to_stage="backtest",
        reason="post-incident demotion",
        approved_by="operator",
        event_store=store,
        evidence_ref="INC-1234",
        now=_NOW,
    )

    assert "post-incident demotion" in result.notes
    assert "evidence_ref=INC-1234" in result.notes
    # Separator pinned.
    assert " | " in result.notes


def test_demote_without_prior_promotion_records_null_reversal(tmp_path):
    """Pre-slice-2 strategies may be at ``paper`` without a promotion row.

    The ``reverses_event_id`` must be ``None`` in that case — pin the
    branch where ``prior is None`` short-circuits the reversal lookup.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")

    result = demote(
        config_path=cfg_path,
        to_stage="backtest",
        reason="legacy-state cleanup",
        approved_by="operator",
        event_store=store,
        now=_NOW,
    )

    assert result.reverses_event_id is None
    assert result.promotion_type == "demotion"


def test_demote_does_not_chain_a_prior_demotion(tmp_path):
    """Kills mutation: state_machine.py:302 ``if prior.promotion_type != 'demotion'``.

    A prior row that is itself a demotion must NOT be chained as the
    reversal target — otherwise a second demote would point at the
    first demote's id, not the original promotion. The mutation
    flipping ``!=`` to ``==`` would invert this invariant.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")
    _seed_paper_promotion(store)

    # First demote: rewrites YAML to backtest, writes a 'demotion' row.
    demote(
        config_path=cfg_path,
        to_stage="backtest",
        reason="first demote",
        approved_by="operator",
        event_store=store,
        now=_NOW,
    )

    # Re-promote-by-hand to set up a second demote scenario where the
    # latest promotion row IS a demotion. Fastest path: directly insert
    # a paper row, then make the YAML reflect 'paper' so demote can
    # rewrite it again.
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace('stage: "backtest"', 'stage: "paper"'),
        encoding="utf-8",
    )
    second_promote_id = _seed_paper_promotion(store)

    # The latest promotion is now the fresh paper row; demote should
    # reverse THAT, not the earlier demotion row.
    result = demote(
        config_path=cfg_path,
        to_stage="backtest",
        reason="second demote",
        approved_by="operator",
        event_store=store,
        now=_NOW,
    )

    assert result.reverses_event_id == second_promote_id


# ---------------------------------------------------------------------------
# Refusal paths (mutation audit Critical #3, mutants #98 and #100)
# ---------------------------------------------------------------------------


def test_demote_with_reason_none_raises(tmp_path):
    """Kills mutation: state_machine.py:290
    ``if reason is None or not reason.strip():``
    -> ``if reason is not None or not reason.strip():`` (mutant #98).

    The original guard rejects ``reason=None``; the mutation would
    accept it and silently write a demotion row with an empty notes
    field.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")

    with pytest.raises(ValueError, match="non-blank"):
        demote(
            config_path=cfg_path,
            to_stage="backtest",
            reason=None,  # type: ignore[arg-type]
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    # No durable side effect, no YAML rewrite.
    assert store.list_promotions() == []
    assert 'stage: "paper"' in cfg_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("blank_reason", ["", "   ", "\t", "\n"])
def test_demote_with_blank_reason_raises(tmp_path, blank_reason):
    """Kills mutation: state_machine.py:290
    ``if reason is None or not reason.strip():``
    -> ``if reason is None and not reason.strip():`` (mutant #100).

    The original ``or`` guard rejects any blank reason (empty,
    whitespace, tabs, newlines). The mutation to ``and`` would only
    reject when reason was simultaneously ``None`` AND blank — making
    a literal ``""`` slip through.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")

    with pytest.raises(ValueError, match="non-blank"):
        demote(
            config_path=cfg_path,
            to_stage="backtest",
            reason=blank_reason,
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    assert store.list_promotions() == []


def test_demote_to_unsupported_stage_raises(tmp_path):
    """Pin the ``_DEMOTE_TARGETS`` membership check.

    Slice 2 only supports ``backtest`` and ``disabled``; any other
    target must refuse before any DB write.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")

    with pytest.raises(ValueError, match="not supported"):
        demote(
            config_path=cfg_path,
            to_stage="micro_live",
            reason="invalid demote target",
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    assert store.list_promotions() == []


def test_demote_with_inline_comment_on_stage_line_fails_before_any_db_write(tmp_path):
    """``demote`` shares the precompute-then-write YAML path with
    ``transition``: a ``stage: "paper"  # comment`` line (unmatched by the
    rewrite regex) must refuse BEFORE the promotion row is appended, not
    strand a durable demotion ahead of a failed YAML update."""
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="paper")
    original = cfg_path.read_text(encoding="utf-8")
    cfg_path.write_text(
        original.replace('stage: "paper"', 'stage: "paper"  # pinned by hand'),
        encoding="utf-8",
    )
    _seed_paper_promotion(store)

    with pytest.raises(ValueError, match="before any durable state is written"):
        demote(
            config_path=cfg_path,
            to_stage="backtest",
            reason="should refuse on unmatched stage line",
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    # No demotion row was appended; YAML untouched.
    assert [p.promotion_type for p in store.list_promotions()] == ["lifecycle_exempt"]
    assert 'stage: "paper"  # pinned by hand' in cfg_path.read_text(encoding="utf-8")


def test_demote_when_already_at_target_stage_raises(tmp_path):
    """Kills mutation: state_machine.py:296 ``if from_stage == to_stage``
    -> any operator flip (``!=``, ``is``, ``is not``).

    A backtest-stage strategy demoted to backtest must refuse with
    'already at stage' before any DB write. This pins the no-op
    refusal path.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg_path = _write_config(tmp_path, stage="backtest")

    with pytest.raises(ValueError, match="already at stage"):
        demote(
            config_path=cfg_path,
            to_stage="backtest",
            reason="nothing to demote",
            approved_by="operator",
            event_store=store,
            now=_NOW,
        )

    assert store.list_promotions() == []

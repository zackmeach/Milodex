"""Model tests for the Bench template-group rollup layer (read side).

Unit tests drive ``bench_grouping.build_group_rollups`` with synthetic
``_StrategyRow`` sets; integration tests drive ``build_bench_snapshot`` with
real configs + a migrated event store, mirroring the idiom in
``test_read_models.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from milodex.gui.bench_grouping import build_group_rollups, group_key
from milodex.gui.strategy_row import _StrategyRow

# ---------------------------------------------------------------------------
# Unit-level helpers
# ---------------------------------------------------------------------------


def _row(
    strategy_id: str,
    *,
    stage: str = "backtest",
    name: str | None = None,
    sharpe: float | None = None,
    max_dd: float | None = None,
    trades: int | None = None,
    archetype: str = "research",
) -> _StrategyRow:
    parts = strategy_id.split(".")
    return _StrategyRow(
        strategy_id=strategy_id,
        name=name or parts[-2].replace("_", " ").title(),
        display_name_source="derived",
        stage=stage,
        description="",
        config_path="",
        family=parts[0],
        template=".".join(parts[1:-2]),
        enabled=True,
        sharpe=sharpe,
        max_drawdown_pct=max_dd,
        trade_count=trades,
        archetype=archetype,
    )


def _rollups(rows: list[_StrategyRow]) -> list[dict]:
    """Build rollups with stub per-row QML payloads (id-tagged dicts)."""
    qml_by_id = {
        row.strategy_id: {"strategyId": row.strategy_id, "stage": row.stage} for row in rows
    }
    return build_group_rollups(rows, qml_by_id)


def _group(groups: list[dict], key: str) -> dict:
    return next(group for group in groups if group["groupKey"] == key)


# ---------------------------------------------------------------------------
# Grouping and counts
# ---------------------------------------------------------------------------


def test_group_key_is_family_dot_template() -> None:
    row = _row("meanrev.rsi2.intraday.spy.v1")
    assert row.family == "meanrev"
    assert row.template == "rsi2.intraday"
    assert group_key(row) == "meanrev.rsi2.intraday"


def test_rows_group_by_family_template_with_instance_counts() -> None:
    rows = [
        _row("meanrev.rsi2.intraday.spy.v1"),
        _row("meanrev.rsi2.intraday.qqq.v1"),
        _row("momentum.daily.tsmom.curated_largecap.v1"),
    ]
    groups = _rollups(rows)
    assert {g["groupKey"] for g in groups} == {"meanrev.rsi2.intraday", "momentum.daily.tsmom"}
    assert _group(groups, "meanrev.rsi2.intraday")["instanceCount"] == 2
    assert _group(groups, "momentum.daily.tsmom")["instanceCount"] == 1


def test_roster_carries_qml_rows_highest_stage_first() -> None:
    rows = [
        _row("meanrev.rsi2.intraday.aaa.v1", stage="backtest"),
        _row("meanrev.rsi2.intraday.bbb.v1", stage="paper"),
        _row("meanrev.rsi2.intraday.ccc.v1", stage="paper"),
    ]
    groups = _rollups(rows)
    roster = _group(groups, "meanrev.rsi2.intraday")["instances"]
    assert [entry["strategyId"] for entry in roster] == [
        "meanrev.rsi2.intraday.bbb.v1",  # paper instances first (Bbb < Ccc)
        "meanrev.rsi2.intraday.ccc.v1",
        "meanrev.rsi2.intraday.aaa.v1",  # waiting-below backtest instance last
    ]


# ---------------------------------------------------------------------------
# Group stage rollup
# ---------------------------------------------------------------------------


def test_group_stage_is_highest_instance_stage() -> None:
    rows = [
        _row("meanrev.rsi2.intraday.spy.v1", stage="paper"),
        _row("meanrev.rsi2.intraday.qqq.v1", stage="backtest"),
        _row("meanrev.rsi2.intraday.iwm.v1", stage="idle"),
    ]
    groups = _rollups(rows)
    assert _group(groups, "meanrev.rsi2.intraday")["stage"] == "paper"


def test_unpromoted_group_stage_falls_back_to_highest_unpromoted_stage() -> None:
    # Unit-level only: these _StrategyRow objects are built POST-clamp (the
    # rollup layer never sees YAML stages), so this proves the max-stage math,
    # not the promotion clamp. The clamp composition — YAML paper claims
    # without a promotion row arriving here already demoted to backtest — is
    # exercised end-to-end in
    # test_snapshot_clamps_unpromoted_paper_siblings_before_rollup.
    rows = [
        _row("gap.gap_continuation.intraday.spy.v1", stage="backtest"),
        _row("gap.gap_continuation.intraday.qqq.v1", stage="idle"),
    ]
    groups = _rollups(rows)
    assert _group(groups, "gap.gap_continuation.intraday")["stage"] == "backtest"


# ---------------------------------------------------------------------------
# Headline stats — best instance AT the group stage
# ---------------------------------------------------------------------------


def test_headline_stats_come_from_best_instance_at_group_stage() -> None:
    rows = [
        # Higher Sharpe but sitting BELOW the group stage — must not headline.
        _row("meanrev.rsi2.intraday.qqq.v1", stage="backtest", sharpe=2.0, max_dd=1.0, trades=99),
        _row("meanrev.rsi2.intraday.spy.v1", stage="paper", sharpe=0.5, max_dd=8.5, trades=40),
        _row("meanrev.rsi2.intraday.iwm.v1", stage="paper", sharpe=0.9, max_dd=6.0, trades=55),
    ]
    group = _group(_rollups(rows), "meanrev.rsi2.intraday")
    assert group["headlineStrategyId"] == "meanrev.rsi2.intraday.iwm.v1"
    assert group["sharpe"] == 0.9
    assert group["maxDrawdownPct"] == 6.0
    assert group["tradeCount"] == 55


def test_headline_prefers_numbered_sharpe_over_none() -> None:
    rows = [
        _row("meanrev.rsi2.intraday.spy.v1", stage="paper", sharpe=None),
        _row("meanrev.rsi2.intraday.qqq.v1", stage="paper", sharpe=-0.2, trades=12),
    ]
    group = _group(_rollups(rows), "meanrev.rsi2.intraday")
    assert group["headlineStrategyId"] == "meanrev.rsi2.intraday.qqq.v1"
    assert group["sharpe"] == -0.2


def test_headline_with_no_metrics_serialises_dash_friendly_values() -> None:
    rows = [_row("momentum.daily.tsmom.curated_largecap.v1")]
    group = _group(_rollups(rows), "momentum.daily.tsmom")
    assert group["sharpe"] is None
    assert group["maxDrawdownPct"] is None
    assert group["tradeCount"] == 0  # matches as_qml()'s `trade_count or 0`


# ---------------------------------------------------------------------------
# Stage-mix summary
# ---------------------------------------------------------------------------


def test_stage_mix_counts_highest_stage_first_with_label() -> None:
    rows = [
        _row("meanrev.rsi2.intraday.spy.v1", stage="paper"),
        _row("meanrev.rsi2.intraday.qqq.v1", stage="paper"),
        _row("meanrev.rsi2.intraday.iwm.v1", stage="backtest"),
    ]
    group = _group(_rollups(rows), "meanrev.rsi2.intraday")
    assert group["stageMix"] == [
        {"stage": "paper", "count": 2},
        {"stage": "backtest", "count": 1},
    ]
    assert group["stageMixLabel"] == "2 paper · 1 backtest"


# ---------------------------------------------------------------------------
# Filter tags — benchmark.* is harness instrumentation
# ---------------------------------------------------------------------------


def test_benchmark_family_groups_visible_only_under_baseline() -> None:
    rows = [
        _row("benchmark.time_of_day_null.spy.v1", archetype="baseline"),
        _row("benchmark.time_of_day_null.qqq.v1", archetype="baseline"),
    ]
    group = _group(_rollups(rows), "benchmark.time_of_day_null")
    assert group["filterTags"] == ["baseline"]


def test_benchmark_family_canary_instance_still_baseline_only() -> None:
    # One benchmark-family instance was promoted lifecycle-exempt and
    # classifies as "canary" — the FAMILY still rules: harness instrumentation
    # stays out of ALL and out of the CANARY filter.
    rows = [
        _row("benchmark.unconditional_intraday_long.spy.v1", stage="paper", archetype="canary"),
        _row("benchmark.unconditional_intraday_long.qqq.v1", stage="paper", archetype="baseline"),
    ]
    group = _group(_rollups(rows), "benchmark.unconditional_intraday_long")
    assert group["filterTags"] == ["baseline"]


def test_non_benchmark_group_tags_include_all_plus_instance_archetypes() -> None:
    rows = [
        _row("meanrev.rsi2.intraday.spy.v1", stage="paper", archetype="canary"),
        _row("meanrev.rsi2.intraday.qqq.v1", stage="paper", archetype="paper"),
        _row("meanrev.rsi2.intraday.iwm.v1", stage="backtest", archetype="blocked"),
    ]
    group = _group(_rollups(rows), "meanrev.rsi2.intraday")
    assert group["filterTags"] == ["all", "blocked", "canary", "paper"]


# ---------------------------------------------------------------------------
# Display naming
# ---------------------------------------------------------------------------


def test_single_instance_group_keeps_instance_display_name() -> None:
    rows = [_row("momentum.daily.tsmom.curated_largecap.v1", name="Tsmom")]
    assert _group(_rollups(rows), "momentum.daily.tsmom")["displayName"] == "Tsmom"


def test_multi_instance_group_uses_humanized_template() -> None:
    rows = [
        _row("meanrev.rsi2.intraday.spy.v1"),
        _row("meanrev.rsi2.intraday.qqq.v1"),
    ]
    group = _group(_rollups(rows), "meanrev.rsi2.intraday")
    assert group["displayName"] == "Rsi2 Intraday"
    assert group["family"] == "meanrev"
    assert group["template"] == "rsi2.intraday"


# ---------------------------------------------------------------------------
# Integration — build_bench_snapshot nests groups inside stage sections
# ---------------------------------------------------------------------------


def _write_strategy_config(
    configs_dir: Path,
    *,
    family: str,
    template: str,
    variant: str,
    stage: str = "backtest",
) -> str:
    strategy_id = f"{family}.{template}.{variant}.v1"
    path = configs_dir / f"{strategy_id.replace('.', '_')}.yaml"
    path.write_text(
        f"""
strategy:
  id: {strategy_id}
  family: {family}
  template: {template}
  variant: {variant}
  version: 1
  description: rollup test strategy
  enabled: true
  universe: [SPY]
  parameters:
    rsi_period: 2
  tempo:
    bar_size: 1D
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.1
    max_positions: 1
    daily_loss_cap_pct: 0.03
    stop_loss_pct: 0.05
  stage: {stage}
  backtest:
    commission_per_trade: 0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )
    return strategy_id


def _create_db(path: Path) -> None:
    from milodex.core.event_store import EventStore

    EventStore(path)


def _seed_promotion(db: Path, strategy_id: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO promotions
            (recorded_at, strategy_id, from_stage, to_stage, promotion_type, approved_by,
             backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count, notes)
        VALUES ('2026-05-08T12:00:00+00:00', ?, 'backtest', 'paper', 'statistical',
                'test', 'run-1', 0.72, 8.5, 120, 'gate pass')
        """,
        (strategy_id,),
    )
    conn.commit()
    conn.close()


def test_snapshot_places_group_in_its_group_stage_section(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    db = tmp_path / "milodex.db"
    _create_db(db)
    promoted = _write_strategy_config(
        configs, family="meanrev", template="rsi2.intraday", variant="spy", stage="paper"
    )
    waiting = _write_strategy_config(
        configs, family="meanrev", template="rsi2.intraday", variant="qqq", stage="backtest"
    )
    _seed_promotion(db, promoted)

    snapshot = build_bench_snapshot(db, configs)
    paper = next(s for s in snapshot["sections"] if s["stage"] == "paper")
    backtest = next(s for s in snapshot["sections"] if s["stage"] == "backtest")

    # The group lands in the section of its GROUP stage only.
    assert [g["groupKey"] for g in paper["groups"]] == ["meanrev.rsi2.intraday"]
    assert backtest["groups"] == []

    group = paper["groups"][0]
    assert group["stage"] == "paper"
    assert group["instanceCount"] == 2
    assert group["stageMix"] == [
        {"stage": "paper", "count": 1},
        {"stage": "backtest", "count": 1},
    ]
    # Roster: promoted instance first, waiting instance below, with the
    # full as_qml() payload (per-instance actions attach to instances).
    roster_ids = [entry["strategyId"] for entry in group["instances"]]
    assert roster_ids == [promoted, waiting]
    assert all("actions" in entry for entry in group["instances"])

    # Flat per-instance lists keep their existing per-stage shape.
    assert [r["strategyId"] for r in paper["strategies"]] == [promoted]
    assert [r["strategyId"] for r in backtest["strategies"]] == [waiting]


def test_snapshot_clamps_unpromoted_paper_siblings_before_rollup(tmp_path: Path) -> None:
    """The rollup's honesty claim rests on the snapshot_builders clamp.

    Production shape: sibling configs in the SAME template group all declare
    ``stage: paper`` in YAML, but only one has a promotion row. The clamp in
    ``_strategy_rows`` (promotion records — not YAML stage — bind a promoted
    stage) must demote the unpromoted siblings to backtest BEFORE the rollup
    runs, so the group reads 1 paper + N backtest, not N+1 paper. A refactor
    that fed the rollup the raw YAML stage (e.g. ``meta_stage``) would pass
    every unit test in this file and silently break exactly this.
    """
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    db = tmp_path / "milodex.db"
    _create_db(db)
    promoted = _write_strategy_config(
        configs, family="meanrev", template="rsi2.intraday", variant="spy", stage="paper"
    )
    unpromoted = [
        _write_strategy_config(
            configs, family="meanrev", template="rsi2.intraday", variant=variant, stage="paper"
        )
        for variant in ("qqq", "iwm")
    ]
    _seed_promotion(db, promoted)

    snapshot = build_bench_snapshot(db, configs)
    by_stage = {section["stage"]: section for section in snapshot["sections"]}

    # (a) The group appears in the paper section and NOWHERE else.
    assert [g["groupKey"] for g in by_stage["paper"]["groups"]] == ["meanrev.rsi2.intraday"]
    for stage, section in by_stage.items():
        if stage != "paper":
            assert section["groups"] == [], f"group leaked into the {stage} section"

    # (b) Stage mix reads exactly 1 paper + 2 backtest — not 3 paper.
    group = by_stage["paper"]["groups"][0]
    assert group["stageMix"] == [
        {"stage": "paper", "count": 1},
        {"stage": "backtest", "count": 2},
    ]

    # (c) The unpromoted siblings' roster entries carry the CLAMPED stage.
    roster_stage_by_id = {entry["strategyId"]: entry["stage"] for entry in group["instances"]}
    assert roster_stage_by_id[promoted] == "paper"
    for strategy_id in unpromoted:
        assert roster_stage_by_id[strategy_id] == "backtest"


def test_snapshot_headline_uses_promotion_metrics_of_best_paper_instance(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    db = tmp_path / "milodex.db"
    _create_db(db)
    promoted = _write_strategy_config(
        configs, family="meanrev", template="rsi2.intraday", variant="spy", stage="paper"
    )
    _write_strategy_config(
        configs, family="meanrev", template="rsi2.intraday", variant="qqq", stage="backtest"
    )
    _seed_promotion(db, promoted)

    snapshot = build_bench_snapshot(db, configs)
    paper = next(s for s in snapshot["sections"] if s["stage"] == "paper")
    group = paper["groups"][0]
    assert group["headlineStrategyId"] == promoted
    assert group["sharpe"] == 0.72
    assert group["maxDrawdownPct"] == 8.5
    assert group["tradeCount"] == 120


def test_snapshot_benchmark_group_is_baseline_only(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    db = tmp_path / "milodex.db"
    _create_db(db)
    _write_strategy_config(
        configs, family="benchmark", template="time_of_day_null", variant="spy", stage="backtest"
    )
    _write_strategy_config(
        configs, family="meanrev", template="rsi2.intraday", variant="spy", stage="backtest"
    )

    snapshot = build_bench_snapshot(db, configs)
    backtest = next(s for s in snapshot["sections"] if s["stage"] == "backtest")
    by_key = {g["groupKey"]: g for g in backtest["groups"]}

    # benchmark.* harness group: BASELINE filter only — excluded from ALL.
    assert by_key["benchmark.time_of_day_null"]["filterTags"] == ["baseline"]
    # Ordinary group remains visible under ALL (plus its archetypes).
    assert "all" in by_key["meanrev.rsi2.intraday"]["filterTags"]


def test_snapshot_without_db_still_emits_groups(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    _write_strategy_config(
        configs, family="meanrev", template="rsi2.intraday", variant="spy", stage="backtest"
    )
    snapshot = build_bench_snapshot(tmp_path / "missing.db", configs)
    backtest = next(s for s in snapshot["sections"] if s["stage"] == "backtest")
    assert [g["groupKey"] for g in backtest["groups"]] == ["meanrev.rsi2.intraday"]
    assert backtest["groups"][0]["instanceCount"] == 1

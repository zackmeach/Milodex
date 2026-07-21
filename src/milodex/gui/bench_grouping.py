"""Template-group rollup layer for the Bench read model (read side only).

A bench row is a TEMPLATE GROUP: every strategy instance sharing
``{family}.{template}`` (strategy ids are ``{family}.{template}.{variant}.v{n}``)
rolls up into one group row, e.g. ``meanrev.rsi2.intraday`` groups its per-ETF
variants while ``momentum.daily.tsmom`` is a 1-instance group.

This module is presentation + read model only. It adds NO promotion verbs and
touches NO per-strategy write path: it consumes the already-built
``_StrategyRow`` list (and the per-row ``as_qml()`` payloads) from
``snapshot_builders._strategy_rows`` and projects group dicts for QML.

Group semantics (founder decision record):

- **Group stage** = highest stage holding >= 1 promoted instance. The rollup
  does not re-read the promotion ledger: ``_strategy_rows`` already clamps any
  paper/micro_live/live claim WITHOUT a promotion record back to backtest, so
  an instance stage above backtest always means "promoted" — taking the max
  instance stage is exactly the promoted-stage rollup.
- **Headline stats** = the best (highest-Sharpe) instance AT the group stage,
  same read-model-snapshot provenance as the per-row ladder metrics.
- **``benchmark.*`` family groups are harness instrumentation, not
  strategies**: visible only under the BASELINE filter
  (``filterTags == ["baseline"]``) and excluded from the ALL filter.
- Every other group carries ``["all"]`` plus the archetypes present among its
  instances, so the existing archetype filters keep working at group
  granularity. QML only indexes ``filterTags`` — no visibility logic is
  re-derived client-side.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from milodex.gui.row_formatters import _VISIBLE_STAGES, _stage_label
from milodex.gui.strategy_row import _StrategyRow

_STAGE_RANK = {stage: index for index, stage in enumerate(_VISIBLE_STAGES)}


def group_key(row: _StrategyRow) -> str:
    """The template-group key for a strategy row: ``{family}.{template}``."""
    return f"{row.family}.{row.template}"


def _humanized_template(template: str) -> str:
    return template.replace(".", " ").replace("_", " ").title()


def _group_display_name(roster: list[_StrategyRow]) -> str:
    """Display name for a group row.

    1-instance groups keep the instance's own display name (what the operator
    already recognizes on the flat ledger, including any config-provided
    ``display_name``). Multi-instance groups get the humanized template — the
    per-variant names are all symbol suffixes and would misname the group.
    """
    if len(roster) == 1:
        return roster[0].name
    return _humanized_template(roster[0].template)


def _headline_instance(instances: list[_StrategyRow], stage: str) -> _StrategyRow:
    """Best instance at the group's stage: highest Sharpe, ties by id.

    Instances with no Sharpe rank below any numbered Sharpe. The at-stage pool
    is never empty by construction (the group stage IS an instance stage); the
    fallback to the full group is a pure safety net.
    """
    pool = [row for row in instances if row.stage == stage] or instances

    def rank(row: _StrategyRow) -> tuple[bool, float, str]:
        return (
            row.sharpe is not None,
            row.sharpe if row.sharpe is not None else float("-inf"),
            row.strategy_id,
        )

    return max(pool, key=rank)


def _stage_mix(instances: list[_StrategyRow]) -> tuple[list[dict[str, Any]], str]:
    """Per-stage instance counts, highest stage first, plus a display label."""
    counts = Counter(row.stage for row in instances)
    mix = [
        {"stage": stage, "count": counts[stage]}
        for stage in reversed(_VISIBLE_STAGES)
        if counts.get(stage)
    ]
    label = " · ".join(
        f"{entry['count']} {_stage_label(str(entry['stage'])).lower()}" for entry in mix
    )
    return mix, label


def _filter_tags(family: str, instances: list[_StrategyRow]) -> list[str]:
    """Filter values under which this group is visible (Python-owned).

    ``benchmark.*`` family rows are harness instrumentation: visible ONLY under
    the BASELINE filter, excluded from ALL (and from every archetype filter —
    a benchmark-family canary still lives under BASELINE only). All other
    groups match ALL plus each archetype present among their instances.
    """
    if family == "benchmark":
        return ["baseline"]
    tags = ["all"]
    tags.extend(sorted({row.archetype for row in instances if row.archetype}))
    return tags


def build_group_rollups(
    rows: list[_StrategyRow], qml_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Project ``_StrategyRow`` instances into template-group dicts for QML.

    ``qml_by_id`` maps ``strategy_id`` to the row's ``as_qml()`` payload so the
    roster reuses the exact per-instance dicts (stage, gate status, stats, and
    the per-instance action menu) the flat ledger carries — actions attach to
    instances, never to the group.
    """
    grouped: dict[str, list[_StrategyRow]] = {}
    for row in rows:
        grouped.setdefault(group_key(row), []).append(row)

    groups: list[dict[str, Any]] = []
    for key, instances in grouped.items():
        stage = max(instances, key=lambda row: _STAGE_RANK.get(row.stage, 0)).stage
        # Roster order: highest stage first (instances executable at the group
        # stage on top, the ones waiting below after), then name.
        roster = sorted(
            instances,
            key=lambda row: (-_STAGE_RANK.get(row.stage, 0), row.name.lower()),
        )
        headline = _headline_instance(instances, stage)
        mix, mix_label = _stage_mix(instances)
        groups.append(
            {
                "groupKey": key,
                "displayName": _group_display_name(roster),
                "family": instances[0].family,
                "template": instances[0].template,
                "stage": stage,
                "instanceCount": len(instances),
                "stageMix": mix,
                "stageMixLabel": mix_label,
                # Headline metrics keep the flat-row key names so the shared
                # QML formatters (formattedSharpe/formattedMaxDD/formattedTrades)
                # apply unchanged. Same read-model-snapshot provenance.
                "sharpe": headline.sharpe,
                "maxDrawdownPct": headline.max_drawdown_pct,
                "tradeCount": headline.trade_count or 0,
                "headlineStrategyId": headline.strategy_id,
                "filterTags": _filter_tags(instances[0].family, instances),
                "instances": [qml_by_id[row.strategy_id] for row in roster],
            }
        )
    groups.sort(
        key=lambda group: (_STAGE_RANK.get(group["stage"], 0), group["displayName"].lower())
    )
    return groups

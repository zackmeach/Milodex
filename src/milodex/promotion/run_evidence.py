"""Promotion evidence helpers: resolving backtest metrics and manifest hashes.

These functions are used by both the CLI promotion command and the Bench command
facade when assembling the inputs for :func:`milodex.promotion.check_gate` and
:func:`milodex.promotion.state_machine.transition`.

Metrics are resolved through ``milodex.analytics`` rather than CLI command
modules, so promotion evidence can remain a domain/governance boundary instead
of depending on an interface adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from milodex.analytics.metrics import metrics_for_run
from milodex.promotion.manifest import hash_canonical
from milodex.strategies.loader import canonicalize_config_data

if TYPE_CHECKING:
    pass


def metrics_from_run(
    run_id: str | None, event_store: Any
) -> tuple[float | None, float | None, int | None]:
    """Return (sharpe_ratio, max_drawdown_pct, trade_count) for a backtest run.

    For walk-forward runs, returns the OOS-aggregate metrics stored in run
    metadata by the orchestrator, not the whole-period numbers. This is the
    promotion-gate hookup for ADR 0021: the gate evaluates evidence from
    out-of-sample data, not from data the strategy implicitly had access to.
    For single whole-period runs, falls through to
    :func:`milodex.analytics.metrics.metrics_for_run` which derives metrics
    from recorded trades.

    Args:
        run_id: The backtest run ID to look up, or ``None`` for lifecycle-exempt
            promotions (returns ``(None, None, None)`` in that case).
        event_store: A readable event store with a ``get_backtest_run`` method.

    Returns:
        A 3-tuple of ``(sharpe_ratio, max_drawdown_pct, trade_count)``.

    Raises:
        ValueError: If *run_id* is not ``None`` but the run cannot be found.
    """
    if run_id is None:
        return None, None, None

    run_ = event_store.get_backtest_run(run_id)
    if run_ is None:
        raise ValueError(f"Backtest run not found: {run_id}")
    metadata = run_.metadata or {}
    if metadata.get("walk_forward") and isinstance(metadata.get("oos_aggregate"), dict):
        oos = metadata["oos_aggregate"]
        return (
            oos.get("sharpe"),
            oos.get("max_drawdown_pct"),
            oos.get("trade_count"),
        )
    metrics = metrics_for_run(run_, event_store)
    return metrics.sharpe_ratio, metrics.max_drawdown_pct, metrics.trade_count


def compute_post_update_hash(raw_data: dict, to_stage: str) -> str:
    """Compute the canonical YAML hash *after* the stage line is updated to *to_stage*.

    :func:`milodex.promotion.state_machine.transition` re-derives this hash
    internally and raises :class:`ValueError` on a mismatch, so callers that
    pass the manifest hash to ``transition()`` must use the same derivation.
    Using this function from both the CLI promotion command and the Bench facade
    ensures the two paths cannot drift apart.

    Args:
        raw_data: The strategy config's ``raw_data`` dict (the parsed YAML).
        to_stage: The target stage string (e.g. ``"paper"``).

    Returns:
        A hex-encoded SHA-256 digest of the canonical JSON of the post-update config.
    """
    strategy = dict(raw_data["strategy"])
    strategy["stage"] = to_stage
    canonical = canonicalize_config_data({**raw_data, "strategy": strategy})
    return hash_canonical(canonical)

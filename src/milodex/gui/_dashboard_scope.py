"""Canonical paper-scope predicate shared by dashboard read-models (spec §8)."""

PAPER_STAGES: tuple[str, ...] = ("paper", "micro_live", "live")
# backtest_run_id IS NULL is the authoritative live/backtest discriminator (matches
# TRADE_PAPER_SQL). Without it, a strategy promoted to 'paper' that still has backtest
# history leaks its backtest evaluation rows into live dashboards — the 2026-05-29
# benchmark leak (69,251 counted vs 358 live). The decision_type guard is kept as a
# belt-and-suspenders; backtest rows already carry a backtest_run_id.
EXPLANATION_PAPER_SQL = (
    "strategy_stage IN ('paper','micro_live','live') "
    "AND backtest_run_id IS NULL "
    "AND decision_type != 'backtest_fill'"
)
TRADE_PAPER_SQL = "strategy_stage IN ('paper','micro_live','live') AND backtest_run_id IS NULL"

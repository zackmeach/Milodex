"""Canonical paper-scope predicate shared by dashboard read-models (spec §8)."""

PAPER_STAGES: tuple[str, ...] = ("paper", "micro_live", "live")
EXPLANATION_PAPER_SQL = (
    "strategy_stage IN ('paper','micro_live','live') AND decision_type != 'backtest_fill'"
)
TRADE_PAPER_SQL = "strategy_stage IN ('paper','micro_live','live') AND backtest_run_id IS NULL"

"""Strategy promotion gate checker.

Rules
-----
Stage progression order: backtest → paper → micro_live → live.
No stage may be skipped; no downgrade is allowed.

Statistical strategies (promotion_type='statistical')
  Sharpe ratio     > 0.5          (SRS R-PRM-001)
  Max drawdown     < 15.0%        (SRS R-PRM-002)
  Trade count      >= 30          (SRS R-PRM-003, R-BKT-003)

Lifecycle-exempt strategies (promotion_type='lifecycle_exempt')
  Statistical thresholds do not apply (SRS R-PRM-004).
  The caller is responsible for passing --lifecycle-exempt only for
  strategies that qualify (currently the regime SPY/SHY strategy).
"""

from __future__ import annotations

from dataclasses import dataclass, field

STAGE_ORDER: list[str] = ["backtest", "paper", "micro_live", "live"]

MIN_SHARPE: float = 0.5
MAX_DRAWDOWN_PCT: float = 15.0
MIN_TRADES: int = 30


@dataclass(frozen=True)
class PromotionCheckResult:
    """Gate check outcome for a single promotion request."""

    allowed: bool
    promotion_type: str
    failures: list[str] = field(default_factory=list)
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None


def validate_stage_transition(from_stage: str, to_stage: str) -> None:
    """Raise ``ValueError`` if the transition is invalid.

    Invalid cases: unknown stage, same stage, downgrade, or stage-skip.
    """
    if from_stage not in STAGE_ORDER:
        msg = f"Unknown from_stage '{from_stage}'. Valid stages: {STAGE_ORDER}."
        raise ValueError(msg)
    if to_stage not in STAGE_ORDER:
        msg = f"Unknown to_stage '{to_stage}'. Valid stages: {STAGE_ORDER}."
        raise ValueError(msg)

    from_idx = STAGE_ORDER.index(from_stage)
    to_idx = STAGE_ORDER.index(to_stage)

    if to_idx == from_idx:
        msg = f"Strategy is already at stage '{from_stage}'."
        raise ValueError(msg)
    if to_idx < from_idx:
        msg = f"Cannot downgrade from '{from_stage}' to '{to_stage}'."
        raise ValueError(msg)
    if to_idx != from_idx + 1:
        msg = (
            f"Skipping stages is not allowed: '{from_stage}' → '{to_stage}'. "
            f"Next valid stage is '{STAGE_ORDER[from_idx + 1]}'."
        )
        raise ValueError(msg)


def check_gate(
    *,
    lifecycle_exempt: bool,
    sharpe_ratio: float | None,
    max_drawdown_pct: float | None,
    trade_count: int | None,
) -> PromotionCheckResult:
    """Evaluate statistical promotion thresholds.

    When ``lifecycle_exempt=True`` the thresholds are bypassed and the check
    always passes (promotion_type='lifecycle_exempt').
    """
    if lifecycle_exempt:
        return PromotionCheckResult(
            allowed=True,
            promotion_type="lifecycle_exempt",
            failures=[],
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
        )

    failures: list[str] = []

    if sharpe_ratio is None or sharpe_ratio <= MIN_SHARPE:
        failures.append(
            f"Sharpe {_fmt_or_none(sharpe_ratio)} must be > {MIN_SHARPE} "
            f"(got {_fmt_or_none(sharpe_ratio)})"
        )

    if max_drawdown_pct is None or max_drawdown_pct >= MAX_DRAWDOWN_PCT:
        failures.append(
            f"Max drawdown {_fmt_or_none(max_drawdown_pct)}% must be < {MAX_DRAWDOWN_PCT}% "
            f"(got {_fmt_or_none(max_drawdown_pct)})"
        )

    if trade_count is None or trade_count < MIN_TRADES:
        failures.append(
            f"Trade count must be >= {MIN_TRADES} (got {_fmt_or_none(trade_count)})"
        )

    return PromotionCheckResult(
        allowed=len(failures) == 0,
        promotion_type="statistical",
        failures=failures,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
    )


def _fmt_or_none(value: float | int | None) -> str:
    return "None" if value is None else str(value)

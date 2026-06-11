"""Bench v1 demo fixture rows — curated state-space coverage for test assertions.

ADR-0049-era visual-prototype demo data only. Not a production path.

Per ADR 0049 Decision 5, the v1 prototype must exercise the full Action
menu state space. Real ``Freshness`` / ``GateResult`` derivation from
event history is v2 work; v1 fixture data populates these directly.

This module exposes a curated list of :class:`BenchFixtureRow` records,
each pairing a :class:`BenchStrategyState` (drives menu computation per
ADR 0050 Decision 5) with the display metadata QML needs for row
rendering. Each row is deliberate: it exercises a distinct menu rule
branch and models a believable strategy/state combination.

Consumers:

- ``tests/milodex/gui/test_bench_v1_fixtures.py`` asserts the curated set
  covers every menu rule branch defined in :mod:`milodex.gui.bench_v1`.
  No production surface or Bench GUI surface reads this module; the Bench
  GUI renders live data from the event store exclusively.

This module contains no side effects, no event-store reads, no broker
calls, and no QML wiring — it is pure data, declared inline.

**Strategy IDs are synthetic.** Demo rows that would otherwise collide
with real strategy configs in ``configs/`` carry a ``.demo`` segment
(e.g. ``breakout.daily.atr_channel.sector_etfs.demo.v1``) to prevent
any read of these rows as real-strategy state. The single intentional
near-collision — ``breakout.daily.atr_channel.sector_etfs.paper_runner.v1``
— is disambiguated by the ``.paper_runner`` segment and labelled
"Active Paper" in its display name to signal it is a demo of the same
strategy in a different runtime state, not the real strategy's record.
"""

from __future__ import annotations

from dataclasses import dataclass

from milodex.gui.bench_v1 import (
    BenchStrategyState,
    EvidenceRecord,
    Freshness,
    GateResult,
    Stage,
)

# ---------------------------------------------------------------------------
# Fixture row record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchFixtureRow:
    """A single demo row for the v1 prototype Bench.

    Pairs the categorical state that drives menu computation with the
    display metadata QML needs for row rendering. v2 will derive these
    pieces from real data; v1 declares them inline.

    - ``strategy_id``: dotted strategy identifier per ADR 0015.
    - ``display_name``: human-readable name per ADR 0041 (presentation
      metadata; not load-bearing for state).
    - ``family`` / ``template``: category metadata for filtering and
      grouping in future surfaces.
    - ``description``: one-sentence intent.
    - ``state``: the categorical state per ADR 0050 (current_stage +
      evidence_by_stage + runs_in_flight + is_session_running).
    - ``sharpe`` / ``max_drawdown_pct`` / ``trade_count``: backtest
      metric snapshots. Present when there is completed BACKTEST
      evidence; ``None`` otherwise. These are display values; the menu
      rules do not consume them.
    """

    strategy_id: str
    display_name: str
    family: str
    template: str
    description: str
    state: BenchStrategyState
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None


# ---------------------------------------------------------------------------
# Curated fixture rows
#
# Each row is annotated with the menu rule branch it exercises. The full
# set must collectively cover every branch in compute_menu_items per
# ADR 0049 Decision 5 (verified by tests/milodex/gui/test_bench_v1_fixtures.py).
# ---------------------------------------------------------------------------


def bench_v1_demo_rows() -> list[BenchFixtureRow]:
    """Return the curated v1 demo row set.

    Returned freshly each call so callers can sort, filter, or augment
    without mutating the canonical list. Order is the natural pipeline
    order (IDLE → BACKTEST → PAPER → MICRO LIVE → LIVE) within each
    section the bench-brief §7.3 walks.
    """
    return [
        # -------- IDLE rows ---------------------------------------------------
        BenchFixtureRow(
            # §7.3: IDLE — no prior history. Demonstrates: Initiate Backtest
            # path from a brand-new strategy; Open Evidence floor.
            strategy_id="breakout.daily.atr_channel.sector_etfs.demo.v1",
            display_name="ATR Channel Breakout",
            family="breakout",
            template="atr_channel",
            description="ATR-bounded channel breakout across the nine sector SPDR ETFs.",
            state=BenchStrategyState(
                current_stage=Stage.IDLE,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                },
            ),
        ),
        BenchFixtureRow(
            # §7.3: IDLE — no prior history, backtest run in flight.
            # Demonstrates: in-flight suppression of the re-run verb;
            # Open Evidence is the only menu item.
            strategy_id="meanrev.daily.bollinger_squeeze.qqq_holdings.v1",
            display_name="Bollinger Squeeze Mean Reversion",
            family="meanrev",
            template="bollinger_squeeze",
            description="Mean-reversion entries on Bollinger band squeezes in QQQ holdings.",
            state=BenchStrategyState(
                current_stage=Stage.IDLE,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                },
                runs_in_flight={Stage.BACKTEST: True},
            ),
        ),
        BenchFixtureRow(
            # §7.3: IDLE — with prior PAPER evidence Fresh+Pass.
            # Demonstrates: Return to Paper (leave-IDLE affordance);
            # Initiate Backtest because BACKTEST evidence is Missing.
            strategy_id="momentum.daily.cross_sectional_rsi.spy_holdings.v1",
            display_name="Cross-Sectional RSI Momentum",
            family="momentum",
            template="cross_sectional_rsi",
            description="Cross-sectional RSI ranking across SPY's 30 largest holdings.",
            state=BenchStrategyState(
                current_stage=Stage.IDLE,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                    Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                },
            ),
            sharpe=0.71,
            max_drawdown_pct=11.2,
            trade_count=147,
        ),
        BenchFixtureRow(
            # §7.3: IDLE — with prior MICRO LIVE evidence Fresh+Pass
            # (deeper shelf candidate). Demonstrates: Return to Micro Live,
            # Return to Paper both available; Initiate Backtest still
            # surfaces because BACKTEST is Missing.
            strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
            display_name="SMA-200 Regime Rotation (SPY/SHY)",
            family="regime",
            template="sma200_rotation",
            description="Regime-driven rotation between SPY and SHY using a 200-day SMA filter.",
            state=BenchStrategyState(
                current_stage=Stage.IDLE,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                    Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                    Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                },
            ),
            sharpe=0.84,
            max_drawdown_pct=8.4,
            trade_count=63,
        ),
        BenchFixtureRow(
            # §7.3: IDLE — with prior LIVE evidence Fresh+NotApplicable
            # (deepest shelf candidate). Demonstrates: Return to Live with
            # the NotApplicable wildcard (LIVE has no further promotion gate);
            # chained Return targets at Paper and Micro Live also available.
            strategy_id="breakout.daily.donchian_20_10.sector_etfs.demo.v1",
            display_name="Donchian 20/10 Breakout",
            family="breakout",
            template="donchian_20_10",
            description="Donchian 20-day high entries with 10-day low exits across sector ETFs.",
            state=BenchStrategyState(
                current_stage=Stage.IDLE,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                    Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                    Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                    Stage.LIVE: EvidenceRecord(Freshness.FRESH, GateResult.NOT_APPLICABLE),
                },
            ),
            sharpe=0.92,
            max_drawdown_pct=9.7,
            trade_count=212,
        ),
        BenchFixtureRow(
            # §7.3: IDLE — MICRO LIVE Stale+Pass + BACKTEST Stale+Pass.
            # Demonstrates: Refresh Backtest path. Stale evidence cannot
            # support a Return verb, but the prior pass entitles the
            # operator to renew rather than restart.
            strategy_id="meanrev.weekly.rsi_2_oversold.semi_etfs.v1",
            display_name="RSI-2 Oversold Mean Reversion (Semis)",
            family="meanrev",
            template="rsi_2_oversold",
            description="Pullbacks on weekly RSI(2) oversold readings in semiconductor ETFs.",
            state=BenchStrategyState(
                current_stage=Stage.IDLE,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.STALE, GateResult.PASS),
                    Stage.MICRO_LIVE: EvidenceRecord(Freshness.STALE, GateResult.PASS),
                },
            ),
            sharpe=0.62,
            max_drawdown_pct=12.8,
            trade_count=88,
        ),
        BenchFixtureRow(
            # §7.3: IDLE — MICRO LIVE Stale+Fail + BACKTEST Stale+Fail.
            # Demonstrates: Initiate Backtest from stale failing evidence
            # (no usable baseline; start fresh).
            strategy_id="meanrev.daily.bollinger_squeeze.tech_etfs.v1",
            display_name="Bollinger Squeeze Mean Reversion (Tech)",
            family="meanrev",
            template="bollinger_squeeze",
            description="Mean-reversion entries on Bollinger squeezes across tech-sector ETFs.",
            state=BenchStrategyState(
                current_stage=Stage.IDLE,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.STALE, GateResult.FAIL),
                    Stage.PAPER: EvidenceRecord(Freshness.STALE, GateResult.FAIL),
                },
            ),
            sharpe=0.31,
            max_drawdown_pct=18.4,
            trade_count=42,
        ),
        BenchFixtureRow(
            # §7.3: IDLE — Invalidated evidence (e.g. manifest drift after
            # a config change). Demonstrates: Invalidated → Initiate Backtest
            # regardless of the prior gate result.
            strategy_id="momentum.daily.relative_strength_macd.bond_etfs.v1",
            display_name="MACD Relative Strength (Bonds)",
            family="momentum",
            template="relative_strength_macd",
            description="MACD-driven relative-strength rotation across bond duration buckets.",
            state=BenchStrategyState(
                current_stage=Stage.IDLE,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.INVALIDATED, GateResult.PASS),
                },
            ),
        ),
        # -------- BACKTEST rows -----------------------------------------------
        BenchFixtureRow(
            # §7.3: BACKTEST — Fresh+Pass, no run in flight.
            # Demonstrates: Promote to Paper + Return to Idle.
            strategy_id="breakout.daily.range_expansion_atr.energy_etfs.v1",
            display_name="ATR Range Expansion Breakout (Energy)",
            family="breakout",
            template="range_expansion_atr",
            description="Range-expansion when ATR exceeds a multi-week percentile (XLE/XOP).",
            state=BenchStrategyState(
                current_stage=Stage.BACKTEST,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                },
            ),
            sharpe=0.78,
            max_drawdown_pct=10.1,
            trade_count=119,
        ),
        BenchFixtureRow(
            # §7.3: BACKTEST — Fresh+Fail. Demonstrates: workflow discipline —
            # no Promote (gate fail), no re-run verb (Fresh+Fail must be
            # invalidated first via a config change). Only Return to Idle
            # + Open Evidence.
            strategy_id="meanrev.intraday.opening_range_fade.qqq.v1",
            display_name="Opening Range Fade (QQQ)",
            family="meanrev",
            template="opening_range_fade",
            description="Intraday fade of QQQ's first 15-minute range extension.",
            state=BenchStrategyState(
                current_stage=Stage.BACKTEST,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.FAIL),
                },
            ),
            sharpe=0.18,
            max_drawdown_pct=22.7,
            trade_count=204,
        ),
        BenchFixtureRow(
            # §7.3: BACKTEST — Aging+Pass. Demonstrates: Promote to Paper
            # AND Refresh Backtest both available.
            strategy_id="regime.weekly.dual_momentum.global_etf_set.v1",
            display_name="Dual Momentum (Global ETFs)",
            family="regime",
            template="dual_momentum",
            description="Gary Antonacci-style dual momentum across a global ETF basket.",
            state=BenchStrategyState(
                current_stage=Stage.BACKTEST,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.AGING, GateResult.PASS),
                },
            ),
            sharpe=0.69,
            max_drawdown_pct=11.5,
            trade_count=78,
        ),
        BenchFixtureRow(
            # §7.3: BACKTEST — run in flight. Demonstrates: in-flight at
            # BACKTEST → no re-run verb; Return to Idle still available
            # because IDLE return is unconditional from active stages.
            strategy_id="breakout.daily.bollinger_breakout.gold_etfs.v1",
            display_name="Bollinger Breakout (Gold)",
            family="breakout",
            template="bollinger_breakout",
            description="Bollinger upper-band breakout entries across gold and gold-miner ETFs.",
            state=BenchStrategyState(
                current_stage=Stage.BACKTEST,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                },
                runs_in_flight={Stage.BACKTEST: True},
            ),
        ),
        # -------- PAPER rows --------------------------------------------------
        BenchFixtureRow(
            # §7.3: PAPER — Fresh+Pass, session idle. Demonstrates:
            # Start Trading + Demote to Backtest + Return to Idle.
            # Promote to Micro Live hidden by ADR 0004 forward lock;
            # no re-run verb because BACKTEST evidence is absent here.
            strategy_id="momentum.daily.cross_sectional_rsi.bonds.v1",
            display_name="Cross-Sectional RSI Momentum (Bonds)",
            family="momentum",
            template="cross_sectional_rsi",
            description="Cross-sectional RSI ranking across the duration spectrum of bond ETFs.",
            state=BenchStrategyState(
                current_stage=Stage.PAPER,
                evidence_by_stage={
                    Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                },
            ),
            sharpe=0.66,
            max_drawdown_pct=9.3,
            trade_count=51,
        ),
        BenchFixtureRow(
            # §7.3: PAPER — session running. Demonstrates: Stop Trading
            # surfaces; Start Trading hidden.
            strategy_id="breakout.daily.atr_channel.sector_etfs.paper_runner.v1",
            display_name="ATR Channel Breakout (Active Paper)",
            family="breakout",
            template="atr_channel",
            description="ATR Channel Breakout currently running its paper-stage session.",
            state=BenchStrategyState(
                current_stage=Stage.PAPER,
                evidence_by_stage={
                    Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                },
                is_session_running=True,
            ),
            sharpe=0.81,
            max_drawdown_pct=7.6,
            trade_count=98,
        ),
        BenchFixtureRow(
            # §7.3: PAPER — Aging+Pass with BACKTEST also Aging+Pass.
            # Demonstrates: Start Trading + Refresh Backtest + Demote to
            # Backtest + Return to Idle. Promote still hidden by ADR 0004.
            strategy_id="meanrev.daily.zscore_reversion.bonds.v1",
            display_name="Z-Score Reversion (Bonds)",
            family="meanrev",
            template="zscore_reversion",
            description="Cross-sectional z-score mean reversion across bond ETFs.",
            state=BenchStrategyState(
                current_stage=Stage.PAPER,
                evidence_by_stage={
                    Stage.BACKTEST: EvidenceRecord(Freshness.AGING, GateResult.PASS),
                    Stage.PAPER: EvidenceRecord(Freshness.AGING, GateResult.PASS),
                },
            ),
            sharpe=0.58,
            max_drawdown_pct=10.4,
            trade_count=72,
        ),
        # -------- MICRO LIVE rows ---------------------------------------------
        BenchFixtureRow(
            # §7.3: MICRO LIVE — session idle. Demonstrates: Start Trading
            # + Return to Idle. Promote to Live hidden by ADR 0004; Demote
            # to Backtest hidden by ADR 0043 Decision 3 + ADR 0004
            # (capital-affecting demotions remain locked while ADR 0004
            # is in force).
            strategy_id="regime.daily.adaptive_volatility.spy_iwm.v1",
            display_name="Adaptive Volatility (SPY/IWM)",
            family="regime",
            template="adaptive_volatility",
            description="SPY/IWM rotation gated by realized volatility regime detection.",
            state=BenchStrategyState(
                current_stage=Stage.MICRO_LIVE,
                evidence_by_stage={
                    Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                },
            ),
            sharpe=0.74,
            max_drawdown_pct=8.9,
            trade_count=44,
        ),
        # -------- LIVE rows ---------------------------------------------------
        BenchFixtureRow(
            # §7.3: LIVE — session running. Demonstrates: terminal stage
            # with Stop Trading; Return to Idle survives the ADR 0043
            # capital-stage lock (Idle is the inactive shelf, not an
            # evaluated state). Demote to Backtest and Return to Micro
            # Live hidden by ADR 0043 Decision 3.
            strategy_id="regime.daily.sma200_rotation.spy_shy.live.v1",
            display_name="SMA-200 Regime Rotation (Live)",
            family="regime",
            template="sma200_rotation",
            description="The lifecycle-proof SPY/SHY rotation strategy currently running live.",
            state=BenchStrategyState(
                current_stage=Stage.LIVE,
                evidence_by_stage={
                    Stage.LIVE: EvidenceRecord(Freshness.FRESH, GateResult.NOT_APPLICABLE),
                },
                is_session_running=True,
            ),
            sharpe=0.83,
            max_drawdown_pct=7.1,
            trade_count=29,
        ),
    ]


__all__ = [
    "BenchFixtureRow",
    "bench_v1_demo_rows",
]

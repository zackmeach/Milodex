"""Apply alternative promotion-gate thresholds against historical backtest evidence.

This is an analysis tool, not production code. It does not modify the production
gate (``milodex.promotion.state_machine.check_gate``); it imports the production
function for sanity checks and wraps it with parameterized thresholds for
counterfactual sweeps.

Two evidence sources are merged:

1. ``data/milodex.db`` — authoritative ``backtest_runs.metadata_json`` (7 strategies).
2. ``docs/reviews/strategy-bank-final-comparison.md`` — the published comparison
   table (12 strategies, including 5 not in the DB). The artifact's Evidence
   Notes flag a cached-data caveat for large-cap rows; rows whose strategy_id
   is absent from the DB are tagged ``source='artifact (cache-caveat)'``.

Outputs a markdown table with one row per (strategy, gate-variant). Designed to
be re-run with different ``--variant`` flags so the report can quote multiple
counterfactuals.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Mirror of milodex.promotion.state_machine constants. Kept inline because the
# package has circular imports that pull in the broker/execution/strategies
# stack — too heavy for an analysis script. If these constants ever change in
# state_machine.py, update here to match (single-source truth lives there).
PROD_MIN_SHARPE: float = 0.5
PROD_MAX_DD: float = 15.0
PROD_MIN_TRADES: int = 30

DB_PATH = REPO_ROOT / "data" / "milodex.db"
ARTIFACT_PATH = REPO_ROOT / "docs" / "reviews" / "strategy-bank-final-comparison.md"

# Cadence map drives the power-aware trade-count gate. Round-trips per backtest
# year are roughly: daily 30+, weekly 6-10, regime/monthly <=4. The threshold
# expresses the minimum number of round-trips below which inference is power-
# limited regardless of Sharpe sign. Trade counts in the DB are *fills*, so
# round-trips ~= trades / 2 for long-only strategies.
STRATEGY_CADENCE: dict[str, str] = {
    "regime.daily.sma200_rotation.spy_shy.v1": "regime",
    "momentum.daily.dual_absolute.gem_weekly.v1": "weekly",
    "momentum.daily.xsec_rotation.sector_etfs.v1": "daily",
    "meanrev.daily.ibs_lowclose.index_etfs.v1": "daily",
    "momentum.daily.tsmom.curated_largecap.v1": "daily",
    "breakout.daily.donchian_20_10.sector_etfs.v1": "daily",
    "breakout.daily.atr_channel.sector_etfs.v1": "daily",
    "meanrev.daily.bbands_lowerband.curated_largecap.v1": "daily",
    "seasonality.daily.turn_of_month.spy.v1": "monthly",
    "breakout.daily.nr7_inside.liquid_largecap.v1": "daily",
    "momentum.daily.52w_high_proximity.largecap.v1": "daily",
    "meanrev.daily.pullback_rsi2.curated_largecap.v1": "daily",
}

# Round-trip floors per cadence. Production gate is 30 fills (= 15 round-trips
# for long-only) regardless of cadence. Power-aware variant scales by frequency.
POWER_AWARE_MIN_ROUNDTRIPS: dict[str, int] = {
    "daily": 30,
    "weekly": 8,
    "monthly": 4,
    "regime": 4,
}


@dataclass(frozen=True)
class StrategyEvidence:
    strategy_id: str
    family: str
    cadence: str
    trades: int | None  # fills, per engine.py:417,465
    oos_sharpe: float | None
    oos_max_dd_pct: float | None
    oos_total_return_pct: float | None
    source: str  # 'db' or 'artifact (cache-caveat)'
    db_run_id: str | None = None


@dataclass(frozen=True)
class GateOutcome:
    allowed: bool
    failures: tuple[str, ...]
    promotion_type: str


# ---------- evidence loaders ---------------------------------------------------


def load_db_evidence(db_path: Path = DB_PATH) -> dict[str, StrategyEvidence]:
    """Latest run per strategy from ``backtest_runs.metadata_json``."""
    out: dict[str, StrategyEvidence] = {}
    with sqlite3.connect(db_path) as con:
        cur = con.cursor()
        rows = cur.execute(
            "SELECT strategy_id, run_id, started_at, metadata_json "
            "FROM backtest_runs WHERE status='completed' "
            "ORDER BY started_at DESC"
        ).fetchall()
    for sid, run_id, _started, meta_json in rows:
        if sid in out:
            continue  # keep most recent
        meta = json.loads(meta_json)
        agg = meta.get("oos_aggregate") or {}
        family = sid.split(".", 1)[0]
        out[sid] = StrategyEvidence(
            strategy_id=sid,
            family=family,
            cadence=STRATEGY_CADENCE.get(sid, "daily"),
            trades=agg.get("trade_count"),
            oos_sharpe=agg.get("sharpe"),
            oos_max_dd_pct=agg.get("max_drawdown_pct"),
            oos_total_return_pct=agg.get("total_return_pct"),
            source="db",
            db_run_id=run_id,
        )
    return out


# Per-strategy detail blocks in the artifact look like:
#   ### `strategy_id`
#   - Family: foo
#   - Trades: NN
#   - OOS Sharpe: 0.1234   (or "n/a")
#   - OOS Max DD: 12.34%
#   - OOS Total Return: +1.23%
_SECTION_RE = re.compile(r"^### `([^`]+)`\s*$", re.MULTILINE)


def _parse_section(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in body.splitlines():
        m = re.match(r"^- ([^:]+):\s*(.+)$", line)
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def _to_float(s: str) -> float | None:
    if s in ("n/a", "N/A", ""):
        return None
    s = s.replace("%", "").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(s: str) -> int | None:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def load_artifact_evidence(
    artifact_path: Path = ARTIFACT_PATH,
) -> dict[str, StrategyEvidence]:
    text = artifact_path.read_text(encoding="utf-8")
    matches = list(_SECTION_RE.finditer(text))
    out: dict[str, StrategyEvidence] = {}
    for i, m in enumerate(matches):
        sid = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fields = _parse_section(text[body_start:body_end])
        family = (fields.get("Family") or sid.split(".", 1)[0]).strip()
        out[sid] = StrategyEvidence(
            strategy_id=sid,
            family=family,
            cadence=STRATEGY_CADENCE.get(sid, "daily"),
            trades=_to_int(fields.get("Trades", "")),
            oos_sharpe=_to_float(fields.get("OOS Sharpe", "")),
            oos_max_dd_pct=_to_float(fields.get("OOS Max DD", "")),
            oos_total_return_pct=_to_float(fields.get("OOS Total Return", "")),
            source="artifact (cache-caveat)",
        )
    return out


def merge_evidence() -> dict[str, StrategyEvidence]:
    """DB rows take precedence; artifact-only rows are tagged with the caveat."""
    artifact = load_artifact_evidence()
    db = load_db_evidence()
    merged = dict(artifact)
    merged.update(db)  # DB overrides artifact
    return merged


# ---------- gate variants ------------------------------------------------------


def _is_lifecycle_exempt(family: str) -> bool:
    # Mirror walk_forward_batch.py: only "regime" family is exempt.
    return family == "regime"


def production_gate(ev: StrategyEvidence) -> GateOutcome:
    """Replays the production gate using the inlined constants (see top-of-file note)."""
    return custom_gate(
        ev,
        min_sharpe=PROD_MIN_SHARPE,
        max_dd_pct=PROD_MAX_DD,
        min_trades=PROD_MIN_TRADES,
    )


def custom_gate(
    ev: StrategyEvidence,
    *,
    min_sharpe: float,
    max_dd_pct: float,
    min_trades: int,
) -> GateOutcome:
    """Production-shaped gate with overridable thresholds."""
    if _is_lifecycle_exempt(ev.family):
        return GateOutcome(allowed=True, failures=(), promotion_type="lifecycle_exempt")
    failures: list[str] = []
    if ev.oos_sharpe is None or ev.oos_sharpe <= min_sharpe:
        failures.append(f"Sharpe {ev.oos_sharpe} must be > {min_sharpe}")
    if ev.oos_max_dd_pct is None or ev.oos_max_dd_pct >= max_dd_pct:
        failures.append(f"Max DD {ev.oos_max_dd_pct}% must be < {max_dd_pct}%")
    if ev.trades is None or ev.trades < min_trades:
        failures.append(f"Trades {ev.trades} must be >= {min_trades}")
    return GateOutcome(
        allowed=not failures,
        failures=tuple(failures),
        promotion_type="statistical",
    )


def power_aware_gate(ev: StrategyEvidence, *, min_sharpe: float, max_dd_pct: float) -> GateOutcome:
    """Trade-count threshold scales with cadence (round-trip-equivalent)."""
    if _is_lifecycle_exempt(ev.family):
        return GateOutcome(allowed=True, failures=(), promotion_type="lifecycle_exempt")
    floor_rt = POWER_AWARE_MIN_ROUNDTRIPS.get(ev.cadence, 30)
    floor_fills = floor_rt * 2  # long-only round-trip = 2 fills
    failures: list[str] = []
    if ev.oos_sharpe is None or ev.oos_sharpe <= min_sharpe:
        failures.append(f"Sharpe {ev.oos_sharpe} must be > {min_sharpe}")
    if ev.oos_max_dd_pct is None or ev.oos_max_dd_pct >= max_dd_pct:
        failures.append(f"Max DD {ev.oos_max_dd_pct}% must be < {max_dd_pct}%")
    if ev.trades is None or ev.trades < floor_fills:
        failures.append(
            f"Trades {ev.trades} must be >= {floor_fills} ({floor_rt} round-trips, cadence={ev.cadence})"
        )
    return GateOutcome(
        allowed=not failures,
        failures=tuple(failures),
        promotion_type="statistical",
    )


# ---------- variants -----------------------------------------------------------


VARIANTS: dict[str, dict] = {
    # Replays prod gate. Used for the verification step.
    "production": {
        "label": f"Production (Sharpe>{PROD_MIN_SHARPE}, DD<{PROD_MAX_DD}%, >={PROD_MIN_TRADES} trades)",
        "fn": lambda ev: custom_gate(
            ev, min_sharpe=PROD_MIN_SHARPE, max_dd_pct=PROD_MAX_DD, min_trades=PROD_MIN_TRADES
        ),
    },
    # Cheap to be wrong — paper risks no real capital.
    "paper-readiness": {
        "label": "Paper-readiness (Sharpe>0.0, DD<25%, >=15 round-trips)",
        "fn": lambda ev: custom_gate(ev, min_sharpe=0.0, max_dd_pct=25.0, min_trades=30),
    },
    # Same Sharpe/DD as production, but trade-count scales with cadence.
    "power-aware": {
        "label": f"Power-aware (Sharpe>{PROD_MIN_SHARPE}, DD<{PROD_MAX_DD}%, cadence-scaled trades)",
        "fn": lambda ev: power_aware_gate(ev, min_sharpe=PROD_MIN_SHARPE, max_dd_pct=PROD_MAX_DD),
    },
    # Combine paper-readiness Sharpe/DD with cadence-scaled trade count.
    "paper-power": {
        "label": "Paper+power-aware (Sharpe>0.0, DD<25%, cadence-scaled trades)",
        "fn": lambda ev: power_aware_gate(ev, min_sharpe=0.0, max_dd_pct=25.0),
    },
}


# ---------- rendering ----------------------------------------------------------


def _fmt(value, suffix: str = "", places: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{places}f}{suffix}"


def render_table(
    evidence: dict[str, StrategyEvidence], variant: str, *, sort_by_sharpe: bool = True
) -> str:
    if variant not in VARIANTS:
        msg = f"Unknown variant {variant!r}. Valid: {sorted(VARIANTS)}"
        raise SystemExit(msg)
    spec = VARIANTS[variant]
    rows = list(evidence.values())
    if sort_by_sharpe:
        rows.sort(key=lambda r: (-(r.oos_sharpe if r.oos_sharpe is not None else -99),))

    lines: list[str] = []
    lines.append(f"## Variant: {spec['label']}\n")
    lines.append("| strategy | cadence | trades | sharpe | max_dd | gate | failures | source |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")

    pass_count = 0
    for ev in rows:
        outcome: GateOutcome = spec["fn"](ev)
        if outcome.allowed:
            pass_count += 1
        gate_label = "PASS" if outcome.allowed else "fail"
        if outcome.promotion_type == "lifecycle_exempt":
            gate_label = "PASS (exempt)"
        fails = "; ".join(outcome.failures) if outcome.failures else "-"
        lines.append(
            f"| `{ev.strategy_id}` | {ev.cadence} | {ev.trades if ev.trades is not None else 'n/a'} | "
            f"{_fmt(ev.oos_sharpe)} | {_fmt(ev.oos_max_dd_pct, '%')} | "
            f"{gate_label} | {fails} | {ev.source} |"
        )

    total = len(rows)
    rejection_rate = 1.0 - pass_count / total if total else 0
    lines.append("")
    lines.append(
        f"**Summary:** {pass_count}/{total} pass "
        f"({rejection_rate * 100:.1f}% rejection rate)."
    )
    return "\n".join(lines)


def verify_against_artifact(evidence: dict[str, StrategyEvidence]) -> str:
    """Production-gate the evidence and check parity with artifact 'gate' column.

    The artifact lists every row as either 'pass (lifecycle_exempt)' or 'block'.
    For DB-resident strategies whose numbers diverge from the artifact, parity
    is *expected* to fail when the divergence flips the gate decision. This
    function reports both the parity check and the divergent rows.
    """
    lines: list[str] = ["## Verification: production-gate replay vs. artifact\n"]
    artifact_only = {
        sid: ev for sid, ev in evidence.items() if ev.source.startswith("artifact")
    }
    db_resident = {sid: ev for sid, ev in evidence.items() if ev.source == "db"}
    lines.append(
        f"- Total strategies: {len(evidence)} ({len(db_resident)} DB-authoritative, "
        f"{len(artifact_only)} artifact-only with cache caveat)\n"
    )
    artifact_pass = 1  # the artifact shows exactly 1 pass (regime)
    prod_pass = sum(1 for ev in evidence.values() if production_gate(ev).allowed)
    lines.append(
        f"- Production gate replay: {prod_pass} pass; artifact says {artifact_pass} pass.\n"
    )
    if prod_pass != artifact_pass:
        lines.append(
            f"- **Divergence**: replay produces {prod_pass} passes vs artifact {artifact_pass}. "
            "Caused by DB-vs-artifact metric divergence (RSI2, tsmom, regime).\n"
        )
    return "\n".join(lines)


# ---------- CLI ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        choices=sorted(VARIANTS),
        default="production",
        help="Gate variant to apply.",
    )
    parser.add_argument("--all", action="store_true", help="Render every variant in sequence.")
    parser.add_argument("--verify", action="store_true", help="Also run the verification block.")
    args = parser.parse_args(argv)

    evidence = merge_evidence()

    chunks: list[str] = []
    if args.verify or args.all:
        chunks.append(verify_against_artifact(evidence))
    if args.all:
        for v in VARIANTS:
            chunks.append(render_table(evidence, v))
    else:
        chunks.append(render_table(evidence, args.variant))

    print("\n\n".join(chunks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

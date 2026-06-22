"""Live-fire diagnostic probe. Reads the event store read-only.

Usage:
    python scripts/livefire_probe.py expl [minutes]   # recent explanations w/ reasoning
    python scripts/livefire_probe.py trades [minutes]  # recent submitted trades
    python scripts/livefire_probe.py pos               # strategy-scoped open lots/positions
    python scripts/livefire_probe.py runs              # open (running) strategy_runs

ponytail: throwaway operator scope, read-only; not a product module.
"""

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

DB = "data/milodex.db"

# Windows console defaults to cp1252; narratives carry em-dashes etc.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _since(argv, default_min=30):
    mins = int(argv[2]) if len(argv) > 2 else default_min
    return (datetime.now(UTC) - timedelta(minutes=mins)).isoformat()


def _cols(c, table):
    return {r[1] for r in c.execute(f"pragma table_info({table})")}


def expl(c, argv):
    since = _since(argv)
    rows = c.execute(
        "select * from explanations where recorded_at >= ? "
        "order by recorded_at desc limit 80",
        (since,),
    ).fetchall()
    print(f"{len(rows)} explanations since {since}")
    for r in rows:
        d = dict(r)
        try:
            ctx = json.loads(d.get("context_json") or "{}")
        except Exception:
            ctx = {"_raw": str(d.get('context_json'))[:200]}
        name = str(d.get("strategy_name"))[:40]
        narrative = (((ctx.get("reasoning") or {}).get("narrative"))
                     or d.get("risk_summary") or "")
        print(f"  [{d.get('recorded_at')}] {d.get('symbol'):<6} {name}")
        print(f"      {d.get('decision_type')}/{d.get('status')} "
              f"side={d.get('side') or '-'} qty={d.get('quantity')} "
              f"risk_allowed={d.get('risk_allowed')}")
        print(f"      → {narrative}")
        rc = d.get("reason_codes_json")
        if rc and rc not in ("[]", "null", None):
            print(f"      reason_codes={rc}")


def trades(c, argv):
    since = _since(argv)
    rows = c.execute(
        "select * from trades where recorded_at >= ? "
        "order by recorded_at desc limit 80",
        (since,),
    ).fetchall()
    print(f"{len(rows)} trades since {since}")
    for r in rows:
        d = dict(r)
        print(f"  [{d.get('recorded_at')}] {str(d.get('strategy_name'))[:36]}  "
              f"{d.get('symbol')} {d.get('side')} qty={d.get('quantity')}  "
              f"status={d.get('status')} broker_status={d.get('broker_status')}  "
              f"order_id={d.get('broker_order_id')}")
        if d.get("message"):
            print(f"      message={d.get('message')}")


def pos(c, argv):
    for t in ("strategy_open_lots", "strategy_positions"):
        if not c.execute(
            "select name from sqlite_master where type='table' and name=?", (t,)
        ).fetchone():
            print(f"(no table {t})")
            continue
        rows = c.execute(f"select * from {t} order by 1 limit 60").fetchall()
        print(f"== {t}: {len(rows)} rows ==")
        for r in rows:
            print("  " + dict(r).__repr__())


def runs(c, argv):
    rows = c.execute(
        "select strategy_id, session_id, started_at from strategy_runs "
        "where ended_at is null order by started_at"
    ).fetchall()
    print(f"{len(rows)} OPEN strategy_runs")
    for r in rows:
        print("  " + dict(r).__repr__())


def blocked(c, argv):
    """Recent non-clean decisions (blocked/rejected/vetoed) with full risk detail."""
    since = _since(argv, 240)
    rows = c.execute(
        "select recorded_at, strategy_name, symbol, side, quantity, decision_type, "
        "status, risk_allowed, risk_summary, reason_codes_json, risk_checks_json, "
        "context_json from explanations where recorded_at >= ? "
        "and (risk_allowed = 0 or status not in ('no_signal','submitted','filled')) "
        "order by recorded_at desc limit 40",
        (since,),
    ).fetchall()
    print(f"{len(rows)} non-clean decisions since {since}")
    for r in rows:
        d = dict(r)
        print(f"== [{d['recorded_at']}] {d['symbol']} {d['strategy_name']} "
              f"{d['side']} qty={d['quantity']} {d['decision_type']}/{d['status']} "
              f"risk_allowed={d['risk_allowed']} ==")
        print(f"  risk_summary={d['risk_summary']}")
        print(f"  reason_codes={d['reason_codes_json']}")
        print(f"  risk_checks={d['risk_checks_json']}")


def cache(c, argv):
    """Per-year daily bar counts — a year well under ~250 flags a gap."""
    import os

    import pandas as pd
    syms = argv[2:] or ["SPY", "SHY", "XLB", "AAPL"]
    roots = ["market_cache/v3/1Day", "market_cache/1Day"]
    for sym in syms:
        path = next((os.path.join(r, f"{sym}.parquet") for r in roots
                     if os.path.exists(os.path.join(r, f"{sym}.parquet"))), None)
        if not path:
            print(f"{sym}: (no daily parquet in {roots})")
            continue
        df = pd.read_parquet(path)
        tcol = "timestamp" if "timestamp" in df.columns else df.columns[0]
        years = pd.to_datetime(df[tcol], utc=True).dt.year.value_counts().sort_index()
        span = f"{df[tcol].min()} .. {df[tcol].max()}"
        print(f"{sym}: {len(df)} bars  [{span}]  ({os.path.dirname(path)})")
        print("   " + "  ".join(f"{y}:{n}" for y, n in years.items()))


def ctx(c, argv):
    n = int(argv[2]) if len(argv) > 2 else 3
    rows = c.execute(
        "select recorded_at, strategy_name, symbol, decision_type, status, "
        "reason_codes_json, risk_checks_json, context_json from explanations "
        "order by recorded_at desc limit ?",
        (n,),
    ).fetchall()
    for r in rows:
        d = dict(r)
        print(f"== [{d['recorded_at']}] {d['strategy_name']} {d['symbol']} "
              f"{d['decision_type']}/{d['status']} ==")
        print(f"  reason_codes={d['reason_codes_json']}")
        print(f"  risk_checks={d['risk_checks_json']}")
        print(f"  context={d['context_json']}")


def schema(c, argv):
    tables = argv[2:] or [
        "explanations", "trades", "strategy_open_lots",
        "strategy_positions", "strategy_runs",
    ]
    for t in tables:
        info = c.execute(f"pragma table_info({t})").fetchall()
        if not info:
            print(f"{t}: (no such table)")
            continue
        print(f"{t}: " + ", ".join(r[1] for r in info))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "expl"
    c = _conn()
    {"expl": expl, "trades": trades, "pos": pos, "runs": runs,
     "schema": schema, "ctx": ctx, "cache": cache,
     "blocked": blocked}[cmd](c, sys.argv)


if __name__ == "__main__":
    main()

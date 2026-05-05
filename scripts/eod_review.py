"""End-of-day review of concurrent strategy runs.

Reads the Milodex event store (`data/milodex.db`) and produces a structured
cross-session review covering the three angles an operator cares about:

    1. INTERACTION  — what each strategy did, cross-strategy events
    2. MONEY        — equity, cash, P&L delta over the window
    3. BROKER       — trades that reached the broker, plus pointers to
                      the live broker views via existing CLI commands

The script is event-store-only. For the broker side it tells you which CLI
commands to run rather than re-implementing them — keeps the surface narrow
and avoids duplicating the broker auth/connection path.

Default window: last 480 minutes (covers a full US trading session). Override
with --minutes or --since-iso.

Usage:
    python scripts/eod_review.py
    python scripts/eod_review.py --minutes 60
    python scripts/eod_review.py --since-iso 2026-05-05T13:30:00+00:00

Designed as ad-hoc operator tooling, not a long-lived CLI surface. If the
output shape stabilizes and a real "milodex analytics live" command lands,
this script can be retired in favor of that.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB_DEFAULT = Path("data/milodex.db")
SHORT_SID = 8  # how many leading chars of session_id to print

# The event store is shared by every component that records a decision —
# strategy_runner (live runners), backtest_engine (walk-forward folds),
# operator (manual `milodex trade submit`), and reconcile (drift events).
# This script reports on *operational* activity, so backtest rows must be
# excluded — otherwise walk-forward folds at 60+ decisions/sec contaminate
# the cross-strategy, money, and anomaly sections.
#
# We exclude `backtest_engine` rather than include `strategy_runner` so that
# any future operational writer (e.g. a reconcile-repair tool) shows up in
# the report by default instead of being silently filtered out.
OPERATIONAL_WRITER_FILTER = "submitted_by != 'backtest_engine'"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--minutes",
        type=int,
        default=480,
        help="Lookback window in minutes (default: 480 = ~1 trading session).",
    )
    p.add_argument(
        "--since-iso",
        type=str,
        default=None,
        help="Override --minutes with an explicit ISO 8601 UTC timestamp "
        "(e.g. 2026-05-05T13:30:00+00:00).",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DB_DEFAULT,
        help=f"Event store path (default: {DB_DEFAULT}).",
    )
    return p.parse_args()


def resolve_window(args: argparse.Namespace) -> tuple[str, str]:
    """Return (start_iso, end_iso) UTC strings the SQLite TEXT comparison wants."""
    end = datetime.now(tz=UTC)
    if args.since_iso:
        start = datetime.fromisoformat(args.since_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
    else:
        start = end - timedelta(minutes=args.minutes)
    return start.isoformat(), end.isoformat()


def open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"Event store not found at {path}. Pass --db <path> if elsewhere.")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def fmt_count(n: int) -> str:
    return f"{n:,}"


def fmt_money(v: float | None) -> str:
    if v is None:
        return "-"
    sign = "-" if v < 0 else " "
    return f"{sign}${abs(v):,.2f}"


def hr(title: str = "", char: str = "=") -> str:
    if not title:
        return char * 72
    pad = 72 - len(title) - 4
    return f"{char * 2} {title} {char * max(0, pad)}"


# --------------------------------------------------------------------------
# Section 1: INTERACTION
# --------------------------------------------------------------------------


def section_interaction(conn: sqlite3.Connection, start: str, end: str) -> None:
    print(hr("1. INTERACTION"))

    # Sessions active in the window: any strategy_runs row with started_at < end
    # and (ended_at IS NULL or ended_at > start). Live runners and runners that
    # closed during the window both qualify.
    sessions = conn.execute(
        """
        SELECT session_id, strategy_id, started_at, ended_at, exit_reason
        FROM strategy_runs
        WHERE started_at <= ?
          AND (ended_at IS NULL OR ended_at >= ?)
        ORDER BY started_at ASC
        """,
        (end, start),
    ).fetchall()

    if not sessions:
        print("  No strategy runs active in window.")
        print()
        return

    for s in sessions:
        sid_short = s["session_id"][:SHORT_SID]
        live = "live" if s["ended_at"] is None else s["ended_at"]
        exit_reason = s["exit_reason"] or "-"
        print(
            f"\n  session: {sid_short}  strategy: {s['strategy_id']}\n"
            f"  started: {s['started_at']}\n"
            f"  ended:   {live}  exit_reason: {exit_reason}"
        )

        # Per-session decision breakdown
        decisions = conn.execute(
            """
            SELECT decision_type, status, COUNT(*) AS n
            FROM explanations
            WHERE session_id = ?
              AND recorded_at >= ?
              AND recorded_at <= ?
            GROUP BY decision_type, status
            ORDER BY n DESC
            """,
            (s["session_id"], start, end),
        ).fetchall()
        total = sum(r["n"] for r in decisions)
        print(f"  cycles in window: {fmt_count(total)}")
        for r in decisions:
            print(f"    {r['decision_type']:<10} {r['status']:<14} {fmt_count(r['n']):>8}")

        # Top reason codes for this session
        reason_rows = conn.execute(
            """
            SELECT reason_codes_json
            FROM explanations
            WHERE session_id = ?
              AND recorded_at >= ?
              AND recorded_at <= ?
              AND reason_codes_json != '[]'
            """,
            (s["session_id"], start, end),
        ).fetchall()
        codes: Counter[str] = Counter()
        for r in reason_rows:
            for code in json.loads(r["reason_codes_json"] or "[]"):
                codes[code] += 1
        if codes:
            print("  top reason codes:")
            for code, n in codes.most_common(5):
                print(f"    {code:<40} {fmt_count(n):>8}")

    # Cross-strategy events: any submitted trade in the window
    print(hr("Cross-strategy events", char="-"))
    submitted = conn.execute(
        f"""
        SELECT recorded_at, substr(session_id, 1, ?) AS sid,
               strategy_name, symbol, side, quantity, status
        FROM trades
        WHERE recorded_at >= ?
          AND recorded_at <= ?
          AND status IN ('submitted', 'accepted', 'filled')
          AND {OPERATIONAL_WRITER_FILTER}
        ORDER BY recorded_at ASC
        """,
        (SHORT_SID, start, end),
    ).fetchall()
    if not submitted:
        print("  No trade reached the broker in window (no submitted/accepted/filled).")
        print("  Both strategies stayed in evaluate-only mode this session.")
    else:
        last_sym_side: tuple[str, str, str] | None = None
        for r in submitted:
            print(
                f"  {r['recorded_at']}  {r['sid']}  "
                f"{(r['strategy_name'] or '-')[:30]:<30}  "
                f"{r['side']:<4} {r['symbol']:<6} x{r['quantity']:g}  "
                f"-> {r['status']}"
            )
            # Heuristic handoff detection
            if last_sym_side and last_sym_side[0] != r["sid"]:
                a_side, a_strat = last_sym_side[1], last_sym_side[2]
                b_side, b_strat = r["side"], r["strategy_name"]
                if a_side.lower() == "sell" and b_side.lower() == "buy":
                    print(
                        f"    >>> possible cross-strategy handoff: {a_strat} SELL -> {b_strat} BUY"
                    )
            last_sym_side = (r["sid"], r["side"], r["strategy_name"])
    print()


# --------------------------------------------------------------------------
# Section 2: MONEY
# --------------------------------------------------------------------------


def section_money(conn: sqlite3.Connection, start: str, end: str) -> None:
    print(hr("2. MONEY"))

    # First and last account snapshot from explanations in the window.
    # explanations carry account_equity/cash/portfolio_value/daily_pnl on every
    # decision, so this gives us a window-bounded equity delta even without
    # daily portfolio_snapshots.
    first = conn.execute(
        f"""
        SELECT recorded_at, account_equity, account_cash,
               account_portfolio_value, account_daily_pnl
        FROM explanations
        WHERE recorded_at >= ? AND recorded_at <= ?
          AND {OPERATIONAL_WRITER_FILTER}
        ORDER BY recorded_at ASC LIMIT 1
        """,
        (start, end),
    ).fetchone()
    last = conn.execute(
        f"""
        SELECT recorded_at, account_equity, account_cash,
               account_portfolio_value, account_daily_pnl
        FROM explanations
        WHERE recorded_at >= ? AND recorded_at <= ?
          AND {OPERATIONAL_WRITER_FILTER}
        ORDER BY recorded_at DESC LIMIT 1
        """,
        (start, end),
    ).fetchone()

    if not first or not last:
        print("  No account snapshots in window.")
        print()
        return

    eq_delta = last["account_equity"] - first["account_equity"]
    cash_delta = last["account_cash"] - first["account_cash"]
    pnl_delta = last["account_daily_pnl"] - first["account_daily_pnl"]
    eq_pct = (eq_delta / first["account_equity"]) * 100 if first["account_equity"] else 0

    print(f"  window first snapshot: {first['recorded_at']}")
    print(f"  window last snapshot:  {last['recorded_at']}\n")
    print(f"  {'metric':<22} {'start':>14} {'end':>14} {'delta':>14}")
    print(f"  {'-' * 22} {'-' * 14} {'-' * 14} {'-' * 14}")
    print(
        f"  {'equity':<22} "
        f"{fmt_money(first['account_equity']):>14} "
        f"{fmt_money(last['account_equity']):>14} "
        f"{fmt_money(eq_delta):>14}  ({eq_pct:+.2f}%)"
    )
    print(
        f"  {'cash':<22} "
        f"{fmt_money(first['account_cash']):>14} "
        f"{fmt_money(last['account_cash']):>14} "
        f"{fmt_money(cash_delta):>14}"
    )
    print(
        f"  {'portfolio_value':<22} "
        f"{fmt_money(first['account_portfolio_value']):>14} "
        f"{fmt_money(last['account_portfolio_value']):>14} "
        f"{fmt_money(last['account_portfolio_value'] - first['account_portfolio_value']):>14}"
    )
    print(
        f"  {'daily_pnl':<22} "
        f"{fmt_money(first['account_daily_pnl']):>14} "
        f"{fmt_money(last['account_daily_pnl']):>14} "
        f"{fmt_money(pnl_delta):>14}"
    )

    # Min/max equity inside the window — useful for intraday excursion view
    extremes = conn.execute(
        f"""
        SELECT MIN(account_equity) AS lo, MAX(account_equity) AS hi
        FROM explanations
        WHERE recorded_at >= ? AND recorded_at <= ?
          AND {OPERATIONAL_WRITER_FILTER}
        """,
        (start, end),
    ).fetchone()
    if extremes and extremes["lo"] is not None:
        excursion = extremes["hi"] - extremes["lo"]
        print(
            f"\n  intraday equity range: "
            f"{fmt_money(extremes['lo'])} ... {fmt_money(extremes['hi'])} "
            f"(excursion: {fmt_money(excursion)})"
        )
    print()


# --------------------------------------------------------------------------
# Section 3: BROKER
# --------------------------------------------------------------------------


def section_broker(conn: sqlite3.Connection, start: str, end: str) -> None:
    print(hr("3. BROKER"))

    # Local view of trades that reached the broker (status != blocked / rejected).
    # Operator should cross-check against the live broker with `milodex orders`.
    submitted = conn.execute(
        f"""
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN status = 'submitted' THEN 1 ELSE 0 END) AS submitted,
               SUM(CASE WHEN status = 'accepted'  THEN 1 ELSE 0 END) AS accepted,
               SUM(CASE WHEN status = 'filled'    THEN 1 ELSE 0 END) AS filled,
               SUM(CASE WHEN status = 'blocked'   THEN 1 ELSE 0 END) AS blocked,
               SUM(CASE WHEN status = 'rejected'  THEN 1 ELSE 0 END) AS rejected
        FROM trades
        WHERE recorded_at >= ? AND recorded_at <= ?
          AND {OPERATIONAL_WRITER_FILTER}
        """,
        (start, end),
    ).fetchone()
    print("  Local trade-row counts in window (event-store side):")
    print(f"    total:     {fmt_count(submitted['n'] or 0):>8}")
    for label in ("submitted", "accepted", "filled", "blocked", "rejected"):
        print(f"    {label + ':':<10} {fmt_count(submitted[label] or 0):>8}")

    print(
        "\n  For the live broker view, run these in another terminal:\n"
        "    milodex positions\n"
        "    milodex orders --limit 50\n"
        "    milodex reconcile           # cross-check broker vs local\n"
        "    milodex status              # account equity, market state, daily P&L"
    )
    print()


# --------------------------------------------------------------------------
# Section 4: ANOMALIES
# --------------------------------------------------------------------------


def section_anomalies(conn: sqlite3.Connection, start: str, end: str) -> None:
    print(hr("4. ANOMALIES & WATCHPOINTS"))

    findings: list[str] = []

    # Block-loop detector: any session with > 100 blocked decisions all on the
    # same reason code is in a wedge loop (regime stuck on
    # max_concurrent_positions_exceeded was today's case).
    block_rows = conn.execute(
        f"""
        SELECT substr(session_id, 1, ?) AS sid, strategy_name,
               reason_codes_json, COUNT(*) AS n
        FROM explanations
        WHERE recorded_at >= ? AND recorded_at <= ?
          AND status = 'blocked'
          AND {OPERATIONAL_WRITER_FILTER}
        GROUP BY session_id, reason_codes_json
        HAVING n > 100
        ORDER BY n DESC
        """,
        (SHORT_SID, start, end),
    ).fetchall()
    for r in block_rows:
        findings.append(
            f"WEDGE-LOOP: session {r['sid']} "
            f"({(r['strategy_name'] or '-')[:30]}) blocked "
            f"{fmt_count(r['n'])}x on {r['reason_codes_json']}"
        )

    # Cycle-rate detector: > 1 decision per second from one session is poll-tight
    rate_rows = conn.execute(
        f"""
        SELECT substr(session_id, 1, ?) AS sid,
               strategy_name,
               COUNT(*) AS n,
               (julianday(MAX(recorded_at)) - julianday(MIN(recorded_at))) * 86400.0
                   AS span_sec
        FROM explanations
        WHERE recorded_at >= ? AND recorded_at <= ?
          AND session_id IS NOT NULL
          AND {OPERATIONAL_WRITER_FILTER}
        GROUP BY session_id
        HAVING span_sec > 0
        """,
        (SHORT_SID, start, end),
    ).fetchall()
    for r in rate_rows:
        rate = r["n"] / r["span_sec"] if r["span_sec"] else 0
        if rate > 1.0:
            findings.append(
                f"TIGHT-POLL: session {r['sid']} ({(r['strategy_name'] or '-')[:30]}) "
                f"averaged {rate:.2f} decisions/sec ({fmt_count(r['n'])} in "
                f"{r['span_sec']:.0f}s)"
            )

    # Kill-switch state — if any kill switch is currently active, surface it
    try:
        ks = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM kill_switch_state
            WHERE active = 1
            """
        ).fetchone()
        if ks and ks["n"] > 0:
            findings.append(
                f"KILL-SWITCH: {ks['n']} active kill-switch row(s) — manual reset required"
            )
    except sqlite3.OperationalError:
        # Table name may differ; non-fatal.
        pass

    # Drift incidents in window
    try:
        drift = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM explanations
            WHERE recorded_at >= ? AND recorded_at <= ?
              AND decision_type = 'reconcile'
            """,
            (start, end),
        ).fetchone()
        if drift and drift["n"] > 0:
            findings.append(
                f"RECONCILE: {drift['n']} reconcile event(s) in window — run "
                "`milodex reconcile` to inspect"
            )
    except sqlite3.OperationalError:
        pass

    if not findings:
        print("  No anomalies detected.")
    else:
        for f in findings:
            print(f"  - {f}")
    print()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> int:
    args = parse_args()
    start, end = resolve_window(args)
    conn = open_db(args.db)

    print(hr("Milodex EOD Review"))
    print(f"  window: {start}")
    print(f"          {end}")
    print(f"  db:     {args.db}")
    print()

    section_interaction(conn, start, end)
    section_money(conn, start, end)
    section_broker(conn, start, end)
    section_anomalies(conn, start, end)

    print(hr())
    print(
        "  For the live broker side of the picture, also run:\n"
        "    milodex status\n"
        "    milodex positions\n"
        "    milodex orders --limit 50\n"
        "    milodex reconcile"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

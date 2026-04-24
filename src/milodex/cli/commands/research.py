"""research command — screening and comparison tooling.

Currently exposes ``research screen``: a batch walk-forward evaluator that
runs every strategy in a given set through the same OOS harness and prints
a ranked comparison table. Intended for working through the research
strategy bank — rather than running ``milodex backtest --walk-forward``
twelve times by hand and eyeballing scrollback, point this at a glob of
configs and read off which candidates clear the gate.

Stays below the governance line: this command only evaluates. It never
freezes, promotes, or advances a strategy. The ``gate`` column is advisory
— the operator still drives any actual promotion via ``milodex promotion
promote`` for each candidate they choose.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from milodex.backtesting.walk_forward_batch import BatchResult, BatchRow, run_batch
from milodex.cli._shared import (
    CommandContext,
    add_global_flags,
    parse_iso_date,
)
from milodex.cli.formatter import CommandResult
from milodex.strategies.loader import load_strategy_config


def register(subparsers: argparse._SubParsersAction) -> None:
    research = subparsers.add_parser(
        "research",
        help="Research tooling — screen and compare strategy candidates.",
    )
    add_global_flags(research)
    research_sub = research.add_subparsers(dest="research_command", required=True)

    screen = research_sub.add_parser(
        "screen",
        help="Batch walk-forward evaluate a set of strategies and rank results.",
    )
    add_global_flags(screen)
    screen.add_argument(
        "--configs",
        default=None,
        help=(
            "Glob over the configs directory (e.g. 'meanrev_*.yaml'). "
            "Mutually exclusive with --strategy-id."
        ),
    )
    screen.add_argument(
        "--strategy-id",
        action="append",
        dest="strategy_ids",
        default=[],
        help="Strategy id. Repeatable. Mutually exclusive with --configs.",
    )
    screen.add_argument("--start", required=True, help="Screening start date YYYY-MM-DD.")
    screen.add_argument("--end", required=True, help="Screening end date YYYY-MM-DD.")
    screen.add_argument(
        "--fail-fast",
        action="store_true",
        help="Abort on the first strategy error rather than recording it and continuing.",
    )
    screen.add_argument(
        "--initial-equity",
        type=float,
        default=100_000.0,
        help="Per-strategy starting equity.",
    )
    screen.add_argument(
        "--report-out",
        nargs="?",
        const="__default__",
        default=None,
        help=(
            "Write a markdown report (plus JSON sibling). Pass a path, or pass "
            "the flag with no value to use docs/reviews/screen_<today>.md."
        ),
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.research_command == "screen":
        return _screen(args, ctx)
    msg = f"Unsupported research command: {args.research_command}"
    raise ValueError(msg)


def _screen(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start.")

    strategy_ids = _resolve_strategy_ids(args, ctx)

    result = run_batch(
        strategy_ids=strategy_ids,
        start_date=start,
        end_date=end,
        ctx=ctx,
        fail_fast=args.fail_fast,
        initial_equity=args.initial_equity,
    )

    report_path: Path | None = None
    if args.report_out is not None:
        report_path = _write_report(args.report_out, result)

    data = _build_data(result, report_path=report_path)
    lines = _build_human(result, report_path=report_path)
    return CommandResult(command="research.screen", data=data, human_lines=lines)


def _resolve_strategy_ids(args: argparse.Namespace, ctx: CommandContext) -> list[str]:
    configs = args.configs
    explicit = list(args.strategy_ids or [])
    if configs and explicit:
        raise ValueError("--configs and --strategy-id are mutually exclusive.")
    if not configs and not explicit:
        raise ValueError("Specify strategies via --configs <glob> or --strategy-id <id>.")
    if explicit:
        return explicit
    matched = sorted(ctx.config_dir.glob(configs))
    if not matched:
        raise ValueError(f"No configs matched {configs!r} under {ctx.config_dir}.")
    ids: list[str] = []
    for path in matched:
        try:
            config = load_strategy_config(path)
        except ValueError:
            # Skip non-strategy YAMLs (e.g. risk_defaults, universe manifests)
            # that don't match the strategy schema.
            continue
        ids.append(config.strategy_id)
    if not ids:
        raise ValueError(
            f"Glob {configs!r} matched {len(matched)} file(s) but none were strategy configs."
        )
    return ids


def _build_data(result: BatchResult, *, report_path: Path | None) -> dict[str, Any]:
    return {
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "row_count": len(result.rows),
        "rows": [row.as_dict() for row in result.rows],
        "report_path": str(report_path) if report_path else None,
    }


def _build_human(result: BatchResult, *, report_path: Path | None) -> list[str]:
    lines = [
        "Research Screen",
        f"Period:    {result.start_date} to {result.end_date}",
        f"Strategies: {len(result.rows)}",
        "",
    ]
    lines.extend(_format_table(result.rows))
    if report_path:
        lines.append("")
        lines.append(f"Report written: {report_path}")
    return lines


_TABLE_HEADER = (
    "strategy_id",
    "family",
    "trades",
    "oos_sharpe",
    "oos_max_dd",
    "fragile",
    "gate",
)


def _format_table(rows: tuple[BatchRow, ...]) -> list[str]:
    data_rows: list[tuple[str, ...]] = [
        (
            row.strategy_id,
            row.family,
            str(row.trade_count),
            _fmt(row.oos_sharpe, ".2f"),
            f"{row.oos_max_drawdown_pct:.2f}%",
            "yes" if row.single_window_dependency else "no",
            _gate_cell(row),
        )
        for row in rows
    ]
    widths = [len(h) for h in _TABLE_HEADER]
    for cells in data_rows:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
    header = "  ".join(h.ljust(widths[i]) for i, h in enumerate(_TABLE_HEADER))
    separator = "  ".join("-" * w for w in widths)
    body = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) for cells in data_rows]
    return [header, separator, *body]


def _gate_cell(row: BatchRow) -> str:
    if row.error:
        return "error"
    if row.gate_allowed:
        return f"pass ({row.gate_promotion_type})"
    return "block"


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _write_report(target: str, result: BatchResult) -> Path:
    stem_date = date.today().isoformat()
    if target == "__default__":
        path = Path("docs/reviews") / f"screen_{stem_date}.md"
    else:
        path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_markdown(result), encoding="utf-8")
    json_path = path.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "start_date": result.start_date.isoformat(),
                "end_date": result.end_date.isoformat(),
                "generated_at": datetime.now().astimezone().isoformat(),
                "rows": [row.as_dict() for row in result.rows],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _render_markdown(result: BatchResult) -> str:
    header_cells = list(_TABLE_HEADER)
    separator = ["---"] * len(header_cells)
    rows_md: list[str] = []
    for row in result.rows:
        rows_md.append(
            "| "
            + " | ".join(
                [
                    f"`{row.strategy_id}`",
                    row.family or "—",
                    str(row.trade_count),
                    _fmt(row.oos_sharpe, ".2f"),
                    f"{row.oos_max_drawdown_pct:.2f}%",
                    "yes" if row.single_window_dependency else "no",
                    _gate_cell(row),
                ]
            )
            + " |"
        )
    body = [
        f"# Research Screen — {result.start_date} → {result.end_date}",
        "",
        f"Generated: {datetime.now().astimezone().isoformat()}",
        f"Strategies: {len(result.rows)}",
        "",
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join(separator) + " |",
        *rows_md,
        "",
        "## Per-strategy detail",
        "",
    ]
    for row in result.rows:
        body.append(f"### `{row.strategy_id}`")
        body.append("")
        if row.error:
            body.append(f"- **ERROR:** {row.error}")
            body.append("")
            continue
        body.append(f"- Family: {row.family}")
        body.append(f"- Trades: {row.trade_count}")
        body.append(f"- OOS Sharpe: {_fmt(row.oos_sharpe, '.4f')}")
        body.append(f"- OOS Max DD: {row.oos_max_drawdown_pct:.2f}%")
        body.append(f"- OOS Total Return: {row.oos_total_return_pct:+.2f}%")
        body.append(f"- Single-window dependency: {row.single_window_dependency}")
        body.append(f"- Gate: {row.gate_promotion_type} — allowed={row.gate_allowed}")
        if row.gate_failures:
            body.append("- Gate failures:")
            for failure in row.gate_failures:
                body.append(f"  - {failure}")
        body.append(f"- Run ID: {row.run_id}")
        body.append("")
    return "\n".join(body)
